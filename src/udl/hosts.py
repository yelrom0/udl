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
    Awaitable,
    Callable,
    Coroutine,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final, Protocol, Self

# Package Imports
from pydantic import Field, model_validator

# Local Imports
from udl.clock import Clock, SystemClock
from udl.config import Duration
from udl.db.records import Name, Record
from udl.errors import PluginFailed
from udl.process import CancelToken, ProcessResult, run_process

type HostName = Name
"""What a host is called (local, thevault): letters, digits, `_`, `.`, `:` and `-`."""

LOCAL: Final = "local"
"""The host that is this machine: always there, and it cannot be deleted."""

DEFAULT_TIMEOUT: Final = timedelta(seconds=30)
"""How long a command gets when the caller names no `within`."""

# How much of a program's output run() keeps: the start of its stdout and the end of its stderr.
KEPT: Final = 64 * 1024

# What a Command's env and secret_env default to (an immutable one, so a Command is hashable).
_EMPTY: Final[Mapping[str, str]] = MappingProxyType({})

# How long a program started by stream() or interact() gets to exit after being asked to.
GRACE: Final = timedelta(seconds=2)
# How long a program being talked to gets to end by itself (after its input is closed) before it
# is asked to stop.
END: Final = timedelta(seconds=5)

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
    typed."""
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
        if (self.transport is HostTransport.SSH) != (self.ssh is not None):
            raise ValueError(
                f"the host {self.name!r} "
                f"{'is' if self.transport is HostTransport.SSH else 'is not'} reached over SSH, so"
                f" it {'needs' if self.transport is HostTransport.SSH else 'has no'} ssh (a"
                " ~/.ssh/config host name)"
            )
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

    run: Callable[..., Awaitable[ProcessResult]]
    """Run a program to completion: its exit code and what it printed (the start of its stdout
    and the end of its stderr, when it printed more than udl keeps). Named arguments: cwd (the
    folder it runs in), env (its environment, over the host's), secret_env (secrets for its
    environment, never in an argv), stdin (what it reads), within (how long it gets) and cancel
    (a CancelToken that stops it). Raises ToolUnavailable when it is not installed, PluginFailed
    when it exits with an error, Cancelled when it is stopped, and TimeoutError when it runs past
    the time it gets."""

    stream: Callable[..., AsyncGenerator[bytes]]
    """Run a program and give its output as it comes (what a harness prints while it works): the
    same arguments as run(), but for cancel."""

    def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Coroutine[Any, Any, Interaction]:
        """Start a program whose output is read and whose input is written while it runs (a
        harness's sign-in, relayed). The same arguments as run(), but for stdin. The caller ends
        it with the handle's close(), or by waiting for it (wait())."""
        ...

    forward: Callable[[int], AbstractAsyncContextManager[str]]
    """A context manager giving a base URL on this machine for the loopback port `port` on the
    host: this machine's own for local, an SSH tunnel for another host. The URL is ready in the
    block, and the tunnel is closed at its end."""


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
    if command.cwd is not None:
        parts.append(f"cd -- {shlex.quote(command.cwd)}")
    for name in command.secret_env:
        parts.append(f"IFS= read -r {name}")  # (from the command's stdin, a line each)
        parts.append(f"export {name}")
    # (Exported, not just assigned: a plain `NAME=value command` would reach the program, but a
    # NAME=value line of its own only sets a shell variable, so export them all.)
    parts += [f"export {name}={shlex.quote(value)}" for name, value in sorted(command.env.items())]
    parts.append("exec " + " ".join(shlex.quote(part) for part in command.argv))
    return "; ".join(parts)


def command_stdin(command: Command) -> bytes | None:
    """What the shell's stdin is: the secrets (a line each), then what the program reads."""
    if not command.secret_env:
        return command.stdin
    secrets = "".join(f"{value}\n" for value in command.secret_env.values()).encode()
    return secrets + (command.stdin or b"")


def _environment(command: Command, host: Host) -> dict[str, str]:
    """The whole environment a local program runs with: udl's, the host's, the command's and its
    secrets (which are in no argv, here or there)."""
    return {**os.environ, **host.env, **command.env, **command.secret_env}


def ssh_argv(host: Host, command: Command) -> tuple[str, ...]:
    """The argv that runs `command` on `host`: its config host name, then the remote `sh -c`
    line remote_command() built. The host's own environment is exported there too (it is not in
    ssh's, which is the login's, not the host record's)."""
    return (
        "ssh",
        *_config_argv(),
        *_SSH_OPTIONS,
        host.ssh or host.name,
        "--",
        remote_command(replace(command, env={**host.env, **command.env})),
    )


def ssh_forward_argv(host: Host, port: int, *, local: int) -> tuple[str, ...]:
    """The argv that tunnels a loopback port on `host` to this machine: `ssh -N -L
    127.0.0.1:<local>:127.0.0.1:<port> <host>`, where `local` is this machine's own port."""
    return (
        "ssh",
        *_config_argv(),
        *_SSH_OPTIONS,
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


def _checked(argv: Sequence[str], result: ProcessResult) -> ProcessResult:
    """A program's result, or PluginFailed when it says it failed (its stderr's last line is the
    message, and its last lines are the hint)."""
    if result.returncode == 0:
        return result
    lines = [line for line in (result.stderr or result.stdout).strip().splitlines() if line]
    raise PluginFailed(
        f"{argv[0]} failed with exit code {result.returncode}"
        + (f": {lines[-1]}" if lines else ""),
        hint="\n".join(lines[-5:]) or None,
    )


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
        yield f"http://127.0.0.1:{local}"
    finally:
        await _stopped(process)


class _Interaction:
    """A program started by a shell, read and written while it runs (an Interaction)."""

    def __init__(self, process: asyncio.subprocess.Process, idle: Duration) -> None:
        self._process = process
        self._idle = idle.total_seconds()
        self._lines: asyncio.Queue[str] = asyncio.Queue()
        self._task: asyncio.Task[None] = asyncio.create_task(self._pump())

    async def _pump(self) -> None:
        """Read the program's output into a queue (a line at a time), so read() waits for some
        when it has printed nothing yet."""
        stdout = self._process.stdout
        if stdout is None:  # pragma: no cover - (always piped by its shell)
            self._lines.put_nowait("")
            return
        buffer = b""
        while chunk := await stdout.read(4096):
            buffer += chunk
            while (end := buffer.find(b"\n")) >= 0:
                line, buffer = buffer[: end + 1], buffer[end + 1 :]
                self._lines.put_nowait(line.decode(errors="replace"))
        if buffer:
            self._lines.put_nowait(buffer.decode(errors="replace"))
        self._lines.put_nowait("")  # (it ended: read() stops waiting)

    @property
    def running(self) -> bool:
        return self._process.returncode is None

    async def read(self) -> str:
        """What it has printed since the last read (waiting up to its idle time for some)."""
        try:
            return await asyncio.wait_for(self._lines.get(), self._idle)
        except TimeoutError:
            return ""

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
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def wait(self) -> int:
        """Wait for it to end, and give its exit code."""
        return await self._process.wait()


# ---------------------------------------------------------------------------------------------
# The shells
# ---------------------------------------------------------------------------------------------


class LocalShell:
    """A shell on this machine: udl.process."""

    def __init__(self, host: Host, *, clock: Clock | None = None) -> None:
        if host.transport is not HostTransport.LOCAL:
            raise ValueError(f"{host.name!r} is not this machine")
        self.host = host
        self.clock: Clock = clock or SystemClock()

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
                command.argv,
                cancel=cancel or CancelToken(),
                stdin=command_stdin(command),
                env=_environment(command, self.host),
                cwd=None if command.cwd is None else Path(command.cwd),
                max_output=KEPT,
            ),
            within,
        )
        return _checked(command.argv, result)

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
            *command.argv,
            stdin=command_stdin(command),
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
        process = await asyncio.create_subprocess_exec(
            *command.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_environment(command, self.host),
            cwd=command.cwd,
        )
        return _Interaction(process, within or DEFAULT_TIMEOUT)

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        """The port is already on this machine: the URL is itself."""
        return _forwarded(f"http://127.0.0.1:{port}")


class SshShell:
    """A shell on another host: `ssh <host> -- <one sh -c line>`, a connection kept between
    commands (ControlMaster) so a session does not pay for one each time."""

    def __init__(self, host: Host, *, clock: Clock | None = None) -> None:
        if host.transport is not HostTransport.SSH:
            raise ValueError(f"{host.name!r} is not reached over SSH")
        self.host = host
        self.clock: Clock = clock or SystemClock()

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
                stdin=command_stdin(command),
                max_output=KEPT,
            ),
            within,
        )
        return _checked(command.argv, result)

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
            *ssh_argv(self.host, command), stdin=command_stdin(command), env=None, cwd=None
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
        """Start a program on the host to be talked to (over SSH)."""
        command = _command(argv, cwd, env, secret_env, None)
        process = await asyncio.create_subprocess_exec(
            *ssh_argv(self.host, command),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        return _Interaction(process, within or DEFAULT_TIMEOUT)

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return _forwarded_ssh(self.host, port)


def shell_for(host: Host, *, clock: Clock | None = None) -> HostShell:
    """The shell that acts on `host`: LocalShell for this machine, SshShell for another."""
    if host.transport is HostTransport.LOCAL:
        return LocalShell(host, clock=clock)
    return SshShell(host, clock=clock)
