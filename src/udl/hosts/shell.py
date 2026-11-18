"""
------------
- hosts.py -
------------

Hosts: the machines agents run on, and the HostShell every action on one goes through.

The shell is the seam between udl and a machine. Runners (25.7) and orch are handed one and
never learn whether it is this machine or an SSH target (O-D11, O-C4), so a harness on thevault
is driven exactly as the one here. LocalShell runs programs here (udl.process); SshShell runs
them there as `ssh <host> -- <one sh -c line>`, the remote command built by remote_command()
with shlex so no value a user gave (a prompt, a path, a model name) can break out of it.

A secret a shell is given (secret_env) is never in an argv: the command's stdin holds the
secrets, one line each, before whatever the program itself reads, and the remote command's
`read`s consume exactly those lines. The program's environment still gets them, so a harness
finds them where it expects (os.environ), and they are in no `ps` output, in no log and in no
error message.
"""

# System Imports
import asyncio
import contextlib
import os
import re
import shlex
import socket
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import ClassVar, Final, Protocol, Self

# Package Imports
from pydantic import Field, model_validator

# Local Imports
from udl.db.records import Name, Record
from udl.errors import PluginFailed, ToolUnavailable
from udl.plugins.requirements import Requirement
from udl.process import CancelToken, ProcessResult, run_process

type HostName = Name
"""What a host is called (local, thevault): letters, digits, `_`, `.`, `:` and `-`."""

LOCAL: Final = "local"
"""The host that is this machine: always there, and it cannot be deleted."""

DEFAULT_TIMEOUT: Final = timedelta(seconds=30)
"""How long a command gets when the caller names no `within`."""

# What a command that was stopped says (udl's own words: it is not a download).
STOPPED: Final = "The command was stopped"
# How much of a program's output run() keeps: the start of its stdout and the end of its stderr.
KEPT: Final = 64 * 1024

# What a Command's env and secret_env default to (an immutable one, so a Command is hashable).
_EMPTY: Final[Mapping[str, str]] = MappingProxyType({})

# How long a program started by stream() or interact() gets to exit after being asked to.
GRACE: Final = timedelta(seconds=2)
# How long a program being talked to gets to end by itself (after its input is closed) before it
# is asked to stop.
END: Final = timedelta(seconds=5)
# How long an SSH tunnel gets to be ready before it is called a failure.
FORWARD_WAIT: Final = timedelta(seconds=15)

# The SSH command's own options: the user gives a ~/.ssh/config host name, never a command line.
_SSH_OPTIONS: Final = (
    "-o",
    "BatchMode=yes",  # (never a question nobody can answer)
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPersist=60",
    "-o",
    "StrictHostKeyChecking=accept-new",
)
# An SSH config file to pass ssh (`-F`), when the default ~/.ssh/config is not wanted (the tests,
# whose HOME is not the user's, and a udl that was told another). ssh reads ~/.ssh/config from
# the passwd entry, not from $HOME, so a test cannot point it there: it gives its own file.
SSH_CONFIG: Final = "UDL_SSH_CONFIG"
# A name that can be an environment variable in a shell.
_VAR: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class HostTransport(StrEnum):
    """How udl reaches a host. A closed set: `client` (a udl on that machine, O-D11) joins it
    later, as a new shell class, with nothing above this changing."""

    LOCAL = "local"
    SSH = "ssh"


class Host(Record):
    """A machine agents run on: this one (local), or another reached over SSH."""

    kind = "host"
    indexed = ("name",)
    unique = (("name",),)

    name: HostName
    transport: HostTransport
    ssh: str | None = None
    """The ~/.ssh/config host name to reach it by (SSH only): never a command line the user
    typed, and never anything ssh would take as an option (it must not start with `-`)."""
    work_dir: str
    """Where worktrees go on it."""
    env: dict[str, str] = Field(default_factory=dict)
    """The environment programs get on it (not secrets: those are given to each shell)."""
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        if (self.name == LOCAL) != (self.transport is HostTransport.LOCAL):
            raise ValueError(
                f"{self.name!r} is {'this machine' if self.name == LOCAL else 'not this machine'},"
                f" so its transport cannot be {self.transport.value!r}"
            )
        if self.ssh is not None and self.ssh.startswith("-"):
            raise ValueError(
                f"{self.ssh!r} looks like an ssh option, not a ~/.ssh/config host name"
            )
        if (self.transport is HostTransport.SSH) != (self.ssh is not None):
            raise ValueError(
                f"the host {self.name!r} "
                f"{'is' if self.transport is HostTransport.SSH else 'is not'} reached over SSH, so"
                f" it {'needs' if self.transport is HostTransport.SSH else 'has no'} ssh (a"
                " ~/.ssh/config host name)"
            )
        return self

    @model_validator(mode="after")
    def _check_env(self) -> Self:
        for name in self.env:
            if not _VAR.fullmatch(name):
                raise ValueError(f"{name!r} cannot name an environment variable")
        return self

    @classmethod
    def example(cls) -> Self:
        """A host for the record contract (not this machine's: `local` is the only local one)."""
        return cls(
            name="test-host",
            transport=HostTransport.SSH,
            ssh="test-host",
            work_dir="/srv/udl/work",
            env={"UDL_TEST": "1"},
        )

    @classmethod
    def local(cls, work_dir: str) -> Self:
        """The host that is this machine."""
        return cls(name=LOCAL, transport=HostTransport.LOCAL, work_dir=work_dir)


# ---------------------------------------------------------------------------------------------
# The shell: the one seam
# ---------------------------------------------------------------------------------------------


class Interaction(Protocol):
    """A program that is running while someone talks to it (a harness's sign-in, 25.11): read
    what it prints, write what they type."""

    @property
    def running(self) -> bool:
        """Whether it is still running."""
        ...

    def read(self) -> Awaitable[str]:
        """What it has printed since the last read (the first of it, when it has printed some).

        Waits a little for output (a harness prints a link a moment after it starts); it gives ""
        when there is nothing new, so a reader is never stuck.
        """
        ...

    def write(self, text: str) -> Awaitable[None]:
        """Give it a line to read (a newline is added)."""
        ...

    def close(self) -> Awaitable[None]:
        """End it, and let go of it (a program that has ended is no longer waited for)."""
        raise NotImplementedError

    def wait(self) -> Awaitable[int]:
        """Wait for it to end, and give its exit code."""
        raise NotImplementedError


class HostShell(Protocol):
    """What a runner or orch is given to act on a host: run a program, read what it prints, end
    it, and reach a port it serves. Nothing above this knows which transport it is."""

    host: Host
    """The host it acts on."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        cancel: CancelToken | None = None,
    ) -> Awaitable[ProcessResult]:
        """Run a program to completion: its exit code and what it printed (the start of its
        stdout and the end of its stderr, when it printed more than udl keeps).

        `cwd` is the folder it runs in, `env` its environment (over the host's), `secret_env`
        secrets for its environment (never in an argv), `stdin` what it reads, `within` how long
        it gets, and `cancel` a CancelToken that stops it. Raises ToolUnavailable when it is not
        installed, PluginFailed when it exits with an error, Cancelled when it is stopped, and
        TimeoutError when it runs past the time it gets."""
        ...

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
    ) -> AsyncIterator[bytes]:
        """Run a program and give its output as it comes (what a harness prints while it works):
        the arguments run() takes, but for cancel."""
        ...

    def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Awaitable[Interaction]:
        """Start a program whose output is read and whose input is written while it runs (a
        harness's sign-in, relayed): the arguments run() takes, but for stdin. The caller ends it
        with the handle's close(), or by waiting for it (wait())."""
        ...

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        """A context manager giving a base URL on this machine for the loopback port `port` on
        the host: this machine's own for local, an SSH tunnel for another host. The URL is ready
        in the block, and the tunnel is closed at its end."""
        ...


# ---------------------------------------------------------------------------------------------
# What a shell is asked to run
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Command:
    """A program to run on a host: its argv, the folder it runs in, the environment it gets, the
    secrets in its environment, and what it reads on stdin. A shell turns it into a run (here:
    exec it; there: one `sh -c` line over SSH)."""

    argv: tuple[str, ...]
    cwd: str | None = None
    """The folder it runs in (None: the shell's own)."""
    env: Mapping[str, str] = _EMPTY
    """Its environment, over the host's own."""
    secret_env: Mapping[str, str] = _EMPTY
    """Secrets for its environment: never in an argv, but read from the command's stdin."""
    stdin: bytes | None = None
    """What it reads on stdin (after the secrets, which come first, a line each)."""

    def __post_init__(self) -> None:
        for name in self.secret_env:
            if not _VAR.fullmatch(name):
                raise ValueError(f"{name!r} cannot name an environment variable")
            if name in self.env:
                raise ValueError(f"{name!r} is given as both env and secret_env")
            # (A secret is framed on one line, so a newline in one would spill the rest of it
            # into whatever the program reads next.)
            if "\n" in self.secret_env[name] or "\r" in self.secret_env[name]:
                raise ValueError(f"The secret {name!r} has a newline: it cannot be framed")
        for name in self.env:
            if not _VAR.fullmatch(name):
                raise ValueError(f"{name!r} cannot name an environment variable")


def remote_command(command: Command) -> str:
    """`command` as one POSIX sh command line: into its folder, its secrets read from stdin, its
    environment exported, then its program (exec'd, so a signal reaches it and no shell is left).

    Every value goes through shlex.quote, so a program name, an argument, a path or a value with
    a quote, a space, a newline or `$(...)` in it reaches the program unchanged.

    The secrets come first on stdin, a line each, and the shell reads exactly those lines with
    one `read` per secret before it exec's the program; the program then reads the rest of what
    it was given. Nothing secret is in the command line (the names are, which is fine), so it is
    in no `ps` output and in no log.
    """
    parts: list[str] = []
    for name in command.secret_env:
        parts.append(f"IFS= read -r {name}")  # (from the command's stdin, a line each)
        parts.append(f"export {name}")
    # (Exported, not just assigned: a plain `NAME=value command` would reach the program, but a
    # NAME=value line of its own only sets a shell variable, so export them all.)
    parts += [f"export {name}={shlex.quote(value)}" for name, value in sorted(command.env.items())]
    parts.append("exec " + " ".join(shlex.quote(part) for part in command.argv))
    body = " && ".join(parts)
    if command.cwd is None:
        return body
    # (Joined with `&&`: a folder that is not there, or a secret line that never comes, stops the
    # whole command, so the program never runs with an empty secret or somewhere else. Local
    # run() refuses the same cases, so the shells agree.)
    return f"cd -- {shlex.quote(command.cwd)} && {{ {body}; }}"


def _secrets(command: Command) -> bytes | None:
    """The secret lines alone (a line each), which a remote shell reads first."""
    if not command.secret_env:
        return None
    return "".join(f"{value}\n" for value in command.secret_env.values()).encode()


def remote_stdin(command: Command) -> bytes | None:
    """What the remote shell's stdin is: the secrets (a line each), then what the program reads.

    Only the remote shell reads its secrets this way (its environment cannot be set from here):
    a local program gets them in its environment, so its stdin is untouched."""
    secrets = _secrets(command)
    if secrets is None:
        return command.stdin
    return secrets + (command.stdin or b"")


def _check_cwd(command: Command) -> None:
    """Refuse a folder that is not there, before running anything.

    A remote `cd` that fails stops the command (remote_command's `&&`), and local would fail in
    the child with a FileNotFoundError that looks like a missing program, so both say the same
    thing here."""
    if command.cwd is not None and not Path(command.cwd).is_dir():
        raise PluginFailed(f"There is no folder {command.cwd}")


def _environment(command: Command, host: Host) -> dict[str, str]:
    """The whole environment a local program runs with: udl's, the host's, the command's and its
    secrets (which are in no argv, here or there)."""
    return {**os.environ, **host.env, **command.env, **command.secret_env}


def remote_line(host: Host, command: Command) -> str:
    """The one command line ssh runs on `host` for `command`.

    It is a *login* shell (`sh -lc`), so the program gets the environment its user gets on that
    host: a harness in ~/.local/bin (which sshd's default PATH lacks, and only the login
    profile adds) is found, and so is a version manager's shims. The host's own environment is
    exported in it too (it is not in ssh's, which is the login's, not the host record's)."""
    line = remote_command(replace(command, env={**host.env, **command.env}))
    return f"sh -lc {shlex.quote(line)}"


def ssh_argv(host: Host, command: Command) -> tuple[str, ...]:
    """The argv that runs `command` on `host`: its config host name, then the one remote command
    line (remote_line())."""
    return (
        "ssh",
        *_config_argv(),
        *_SSH_OPTIONS,
        host.ssh or host.name,
        "--",
        remote_line(host, command),
    )


def ssh_forward_argv(host: Host, port: int, *, local: int) -> tuple[str, ...]:
    """The argv that tunnels a loopback port on `host` to this machine: `ssh -N -L
    127.0.0.1:<local>:127.0.0.1:<port> <host>`, where `local` is this machine's own port."""
    return (
        "ssh",
        *_config_argv(),
        *_SSH_OPTIONS,
        # (Exit rather than sit there silently when the tunnel cannot be set up.)
        "-o",
        "ExitOnForwardFailure=yes",
        "-N",
        "-L",
        f"127.0.0.1:{local}:127.0.0.1:{port}",
        host.ssh or host.name,
    )


def _config_argv() -> tuple[str, ...]:
    """The `-F <file>` that points ssh at another config, when UDL_SSH_CONFIG names one (the
    tests', and a udl that was told another): nothing for the user's own."""
    config = os.environ.get(SSH_CONFIG)
    return ("-F", config) if config else ()


def _command(
    argv: Sequence[str],
    cwd: str | None,
    env: Mapping[str, str] | None,
    secret_env: Mapping[str, str] | None,
    stdin: bytes | None,
) -> Command:
    """A Command of what a shell was asked to run."""
    return Command(
        argv=tuple(argv), cwd=cwd, env=env or {}, secret_env=secret_env or {}, stdin=stdin
    )


# ---------------------------------------------------------------------------------------------
# Running one command: the parts both shells share
# ---------------------------------------------------------------------------------------------


async def _timed[T](work: Awaitable[T], within: timedelta | None) -> T:
    return await asyncio.wait_for(work, (within or DEFAULT_TIMEOUT).total_seconds())


async def _started(
    *argv: str, stdin: bytes | None, env: Mapping[str, str] | None, cwd: str | None
) -> asyncio.subprocess.Process:
    """A program started to be read as it prints (its stderr with its stdout, in order)."""
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=None if env is None else dict(env),
        cwd=cwd,
    )


async def _write(process: asyncio.subprocess.Process, data: bytes | None) -> None:
    """Give a program `data` on its stdin and leave it open (a program that is talked to)."""
    if process.stdin is None or not data:
        return
    try:
        process.stdin.write(data)
        await process.stdin.drain()
    except BrokenPipeError, ConnectionResetError:  # (it stopped reading)
        pass


async def _feed(process: asyncio.subprocess.Process, data: bytes | None) -> None:
    """Give a program `data` on its stdin, then close it (so `input()` and `cat` see the end)."""
    if process.stdin is None:
        return
    try:
        if data:
            process.stdin.write(data)
            await process.stdin.drain()
    except BrokenPipeError, ConnectionResetError:  # (it stopped reading)
        pass
    finally:
        process.stdin.close()


async def _output(
    process: asyncio.subprocess.Process,
    within: timedelta | None,
) -> AsyncGenerator[bytes]:
    """What a running program prints, as it prints it, until it ends or `within` passes."""
    if process.stdout is None:  # pragma: no cover - (always piped: _started())
        raise PluginFailed("The program has no output to read")
    try:
        async with asyncio.timeout((within or DEFAULT_TIMEOUT).total_seconds()):
            while chunk := await process.stdout.read(4096):
                yield chunk
    finally:
        await _stopped(process)


async def _stopped(process: asyncio.subprocess.Process) -> None:
    """Ask a program that is still running to end, then kill it if it does not within GRACE."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=GRACE.total_seconds())
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()


def _checked(result: ProcessResult, *, program: str, via_ssh: bool = False) -> ProcessResult:
    """A program's result, or what its failure means (its stderr's last line is the message, and
    its last lines are the hint).

    ssh's own codes are not the program's: 255 is ssh failing to reach the host (a connection
    failure), and 127 is the remote command not being found (the program is not installed)."""
    if result.returncode == 0:
        return result
    lines = [line for line in (result.stderr or result.stdout).strip().splitlines() if line]
    detail = f": {lines[-1]}" if lines else ""
    hint = "\n".join(lines[-5:]) or None
    if via_ssh and result.returncode == 255:
        raise PluginFailed(f"Could not reach the host over SSH{detail}", hint=hint)
    if via_ssh and result.returncode == 127:
        raise ToolUnavailable(f"{program} is not installed there{detail}", hint=hint)
    raise PluginFailed(f"{program} failed with exit code {result.returncode}{detail}", hint=hint)


@asynccontextmanager
async def _forwarded(url: str) -> AsyncGenerator[str]:
    """A base URL on this machine (for `local`, the host's own port: nothing to do)."""
    yield url


@asynccontextmanager
async def _forwarded_ssh(host: Host, port: int) -> AsyncGenerator[str]:
    """An SSH tunnel to a loopback port on `host`: a local port on this machine (the OS picks a
    free one) forwarded to 127.0.0.1:<port> there, for as long as the block runs."""
    with socket.socket() as spare:
        spare.bind(("127.0.0.1", 0))
        local = spare.getsockname()[1]
    argv = ssh_forward_argv(host, port, local=local)
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await _tunnel_up(process, local)
        yield f"http://127.0.0.1:{local}"
    finally:
        await _stopped(process)


async def _tunnel_up(process: asyncio.subprocess.Process, local: int) -> None:
    """Wait until the tunnel's local port accepts a connection (or ssh gives up).

    ssh would otherwise sit there silently when the tunnel cannot be set up, and a caller would
    hand out a URL that leads nowhere."""
    deadline = asyncio.get_event_loop().time() + FORWARD_WAIT.total_seconds()
    while True:
        if process.returncode is not None:
            detail = (
                (await process.stderr.read()).decode(errors="replace").strip()
                if process.stderr
                else ""
            )
            raise PluginFailed(
                f"The SSH tunnel closed before it was ready{detail and ': ' + detail}",
                hint="Check the host's ssh entry (a ~/.ssh/config host name) and its sshd.",
            )
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", local)
        except OSError:
            if asyncio.get_event_loop().time() >= deadline:
                raise PluginFailed(
                    f"The SSH tunnel to port {local} was not ready in time",
                    hint="Check the host is reachable (udl hosts check <host>).",
                ) from None
            await asyncio.sleep(0.05)
            continue
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        return


class _Interaction:
    """A program started by a shell, read and written while it runs (an Interaction).

    `within` is how long the whole call gets: when it passes, the program is asked to end, so a
    sign-in that is never finished cannot outlive it. read() waits for output only until then."""

    def __init__(self, process: asyncio.subprocess.Process, within: timedelta) -> None:
        self._process = process
        self._deadline = asyncio.get_event_loop().time() + within.total_seconds()
        self._so_far: str = ""
        self._woken = asyncio.Event()
        self._task: asyncio.Task[None] = asyncio.create_task(self._pump())
        self._timer: asyncio.Task[None] = asyncio.create_task(self._expire())

    async def _pump(self) -> None:
        """Read the program's output as it comes, so read() can give everything since the last
        read and wake when more arrives."""
        stdout = self._process.stdout
        if stdout is None:  # pragma: no cover - (always piped by its shell)
            self._woken.set()
            return
        while chunk := await stdout.read(4096):
            self._so_far += chunk.decode(errors="replace")
            self._woken.set()
        self._woken.set()  # (it ended: read() stops waiting)

    async def _expire(self) -> None:
        """End the program when the call's own time is up."""
        await asyncio.sleep(max(self._deadline - asyncio.get_event_loop().time(), 0))
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.terminate()

    @property
    def running(self) -> bool:
        return self._process.returncode is None

    async def read(self) -> str:
        """Everything it has printed since the last read (waiting, until the call's time is up,
        for the first of it)."""
        if not self._so_far:
            left = max(self._deadline - asyncio.get_event_loop().time(), 0)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._woken.wait(), left)
        # (Also when it drained text it already had, or the next read would not wait. But once
        # the reader has ended, its wake-up is the end-of-output: clearing it would make the next
        # read wait out the call's time for nothing more that will never come.)
        if not self._task.done():
            self._woken.clear()
        said, self._so_far = self._so_far, ""
        return said

    async def write(self, text: str) -> None:
        """Give it a line to read."""
        if self._process.stdin is None:  # pragma: no cover - (a shell always pipes it)
            raise PluginFailed("This program reads no input")
        self._process.stdin.write(f"{text}\n".encode())
        await self._process.stdin.drain()

    async def close(self) -> None:
        """End it: no more input (so a program that has read its last line can end, and ssh
        closes the channel), then ask it to stop if it is still running."""
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            if self._process.stdin is not None:
                self._process.stdin.close()
        # (Give it a moment to end on its own — ssh needs a round trip to close the channel — and
        # only then ask it to stop.)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._process.wait(), timeout=END.total_seconds())
        await _stopped(self._process)
        for task in (self._task, self._timer):
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(self._task, self._timer, return_exceptions=True)

    async def wait(self) -> int:
        """Wait for it to end, and give its exit code."""
        return await self._process.wait()


# ---------------------------------------------------------------------------------------------
# The shells
# ---------------------------------------------------------------------------------------------


class LocalShell:
    """A shell on this machine: udl.process."""

    def __init__(self, host: Host) -> None:
        if host.transport is not HostTransport.LOCAL:
            raise ValueError(f"{host.name!r} is not this machine")
        self.host = host

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        cancel: CancelToken | None = None,
    ) -> Awaitable[ProcessResult]:
        return self._run(_command(argv, cwd, env, secret_env, stdin), within, cancel)

    async def _run(
        self,
        command: Command,
        within: timedelta | None,
        cancel: CancelToken | None,
    ) -> ProcessResult:
        _check_cwd(command)
        result = await _timed(
            run_process(
                command.argv,
                cancel=cancel or CancelToken(),
                stdin=command.stdin,
                env=_environment(command, self.host),
                cwd=None if command.cwd is None else Path(command.cwd),
                max_output=KEPT,
                cancelled=STOPPED,
            ),
            within,
        )
        return _checked(result, program=command.argv[0])

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
    ) -> AsyncGenerator[bytes]:
        return self._stream(_command(argv, cwd, env, secret_env, stdin), within)

    async def _stream(
        self,
        command: Command,
        within: timedelta | None,
    ) -> AsyncGenerator[bytes]:
        _check_cwd(command)
        process = await _started(
            *command.argv,
            stdin=command.stdin,
            env=_environment(command, self.host),
            cwd=command.cwd,
        )
        try:
            await _feed(process, command.stdin)
            async for chunk in _output(process, within):
                yield chunk
        finally:
            await _stopped(process)

    async def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Interaction:
        """Start a program on this machine to be talked to."""
        command = _command(argv, cwd, env, secret_env, None)
        _check_cwd(command)
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_environment(command, self.host),
            cwd=command.cwd,
        )
        # (A local program reads its secrets from its environment, so nothing is written first.)
        return _Interaction(process, within or DEFAULT_TIMEOUT)

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        """The port is already on this machine: the URL is itself."""
        return _forwarded(f"http://127.0.0.1:{port}")


class SshShell:
    """A shell on another host: `ssh <host> -- <one sh -c line>`, a connection kept between
    commands (ControlMaster) so a session does not pay for one each time."""

    requires: ClassVar[tuple[Requirement, ...]] = (Requirement.binary("ssh"),)
    """ssh has to be on the machine udl runs on (not on the host: it is reached with ssh)."""

    def __init__(self, host: Host) -> None:
        if host.transport is not HostTransport.SSH:
            raise ValueError(f"{host.name!r} is not reached over SSH")
        self.host = host

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        cancel: CancelToken | None = None,
    ) -> Awaitable[ProcessResult]:
        return self._run(_command(argv, cwd, env, secret_env, stdin), within, cancel)

    async def _run(
        self,
        command: Command,
        within: timedelta | None,
        cancel: CancelToken | None,
    ) -> ProcessResult:
        result = await _timed(
            run_process(
                ssh_argv(self.host, command),
                cancel=cancel or CancelToken(),
                stdin=remote_stdin(command),
                max_output=KEPT,
                cancelled=STOPPED,
            ),
            within,
        )
        return _checked(result, program=command.argv[0], via_ssh=True)

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
    ) -> AsyncGenerator[bytes]:
        return self._stream(_command(argv, cwd, env, secret_env, stdin), within)

    async def _stream(
        self,
        command: Command,
        within: timedelta | None,
    ) -> AsyncGenerator[bytes]:
        process = await _started(
            *ssh_argv(self.host, command), stdin=remote_stdin(command), env=None, cwd=None
        )
        try:
            await _feed(process, remote_stdin(command))
            async for chunk in _output(process, within):
                yield chunk
        finally:
            await _stopped(process)

    async def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Interaction:
        """Start a program on the host to be talked to (over SSH)."""
        command = _command(argv, cwd, env, secret_env, None)
        process = await asyncio.create_subprocess_exec(
            *ssh_argv(self.host, command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        await _write(process, _secrets(command))  # (the remote shell's secret lines)
        return _Interaction(process, within or DEFAULT_TIMEOUT)

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return _forwarded_ssh(self.host, port)


def shell_for(host: Host) -> HostShell:
    """The shell that acts on `host`: LocalShell for this machine, SshShell for another."""
    if host.transport is HostTransport.LOCAL:
        return LocalShell(host)
    return SshShell(host)
