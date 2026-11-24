"""
------------
- shell.py -
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
import tempfile
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Awaitable,
    Mapping,
    MutableMapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import IO, ClassVar, Final, Literal, Protocol, Self
from uuid import uuid4

# Package Imports
from pydantic import Field, model_validator

# Local Imports
from udl.db.records import Name, Record
from udl.errors import OverCap, PluginFailed, ToolUnavailable
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
# How a capped run is started (27.12): a transient user scope, quiet, then the program.
SYSTEMD: Final = ("systemd-run", "--user", "--scope", "-q")
# How long the probe for user systemd gets: a host without it answers at once.
PROBE: Final = timedelta(seconds=10)
# How long stopping a unit gets (a scope that is already gone says so, quickly).
STOPPING: Final = timedelta(seconds=30)

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
# What git exports to point a program at one repo (`git rev-parse --local-env-vars`): a hook gets
# them for the repo it runs in. udl does not pass them on from its own environment, so a program
# it runs finds its repo by the folder it runs in: on 21 Nov a `make check` under the pre-commit
# hook passed them on, and a test's `git init --bare` turned the repo it was run from bare. A
# host's or a command's own env may still set them.
REPO_VARIABLES: Final = frozenset(
    {
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_CONFIG",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
        "GIT_DIR",
        "GIT_GRAFT_FILE",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_OBJECT_DIRECTORY",
        "GIT_PREFIX",
        "GIT_REPLACE_REF_BASE",
        "GIT_SHALLOW_FILE",
        "GIT_WORK_TREE",
    }
)
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
        check: bool = True,
        tail: bool = False,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> Awaitable[ProcessResult]:
        """Run a program to completion: its exit code and what it printed (the start of its
        stdout and the end of its stderr, when it printed more than udl keeps; with `tail`, the
        end of its stdout too).

        `limits` is the memory cap the run is under (27.12): where the host has user systemd it
        runs in a transient scope of that cap and no swap, named `unit`, and the result says
        `limits="enforced"`; elsewhere it runs as it is and the result says `unenforced`, never
        silently. A checked run that the kernel stopped at its cap raises OverCap.

        `cwd` is the folder it runs in, `env` its environment (over the host's), `secret_env`
        secrets for its environment (never in an argv), `stdin` what it reads, `within` how long
        it gets, `cancel` a CancelToken that stops it, `check` whether a non-zero exit is an
        error (False: its code and output come back, for a caller that reads a failure as an
        answer — a repo's gate, 27.5), and `tail` whether to keep the end of its stdout rather
        than the start (what such a caller reads: a failure says it at the end). Raises
        ToolUnavailable when it is not installed, PluginFailed when it exits with an error and
        `check` is set, Cancelled when it is stopped, and TimeoutError when it runs past the
        time it gets."""
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
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Run a program and give its output as it comes (what a harness prints while it works):
        the arguments run() takes, but for cancel and check. A capped run the kernel stopped
        raises OverCap once its output ends (27.12)."""
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
# A run under a memory cap (27.12)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResourceLimits:
    """What a program may use: its memory, and no swap at all (a cap that spills to swap is not
    a cap: the point is that a runaway ends, not that the machine starts thrashing)."""

    memory: int
    """Its cap, in bytes."""

    def __post_init__(self) -> None:
        if self.memory <= 0:
            raise ValueError(f"{self.memory!r} is not more than nothing")

    @property
    def words(self) -> str:
        """The cap as a person reads it (`4 GiB`)."""
        return size_words(self.memory)


def size_words(size: int) -> str:
    """A number of bytes as a person reads it: `4 GiB`, `1536 MiB`, `500 B`."""
    for unit in ("GiB", "MiB", "KiB"):
        size_of = 1024 ** {"GiB": 3, "MiB": 2, "KiB": 1}[unit]
        if size and size % size_of == 0:
            return f"{size // size_of} {unit}"
    return f"{size} B"


def escaped(part: str) -> str:
    """One part of a unit's name, escaped the way `systemd-escape` escapes it: an ASCII letter, a
    digit or `:`, `_`, `.`, `-` stays (a `-` is how udl joins the parts, so `deepseek-b` stays
    readable), a `/` becomes a `-`, and anything else becomes an `x` escape, byte by byte (a
    unit name is ASCII only, so `é` is `\\xc3\\xa9` and not a letter). A `-` at the start is
    escaped, since a unit name cannot begin with one."""
    kept: list[str] = []
    for index, char in enumerate(part):
        if (char.isascii() and char.isalnum()) or char in ":_." or (char == "-" and index > 0):
            kept.append(char)
        elif char == "/":
            kept.append("-")
        else:
            kept.append("".join(f"\\x{byte:02x}" for byte in char.encode()))
    return "".join(kept)


def unit_name(prefix: str, *parts: str) -> str:
    """A unit's name: udl's own `prefix`, then each part escaped as `systemd-escape` escapes it,
    so a project with a slash in it and a key like `27.3a` both make a valid name
    (`udl-gate-yelrom0-udl-27.3a-0a1b2c3d`)."""
    return "-".join([prefix, *(escaped(part) for part in parts)])


def scope_of(unit: str) -> str:
    """The unit as `systemctl` names it: a scope is always named with its suffix (a bare name
    means `.service`)."""
    return f"{unit}.scope"


def capped(
    argv: Sequence[str], limits: ResourceLimits, *, unit: str | None = None
) -> tuple[str, ...]:
    """`argv` as a capped run: a transient user scope with the memory cap and no swap, named
    `unit` (or `udl-<8 hex>` when the caller has no name of its own)."""
    return (
        *SYSTEMD,
        f"--unit={unit or unit_name('udl', uuid4().hex[:8])}",
        "-p",
        f"MemoryMax={limits.memory}",
        "-p",
        "MemorySwapMax=0",
        "--",
        *argv,
    )


class Probe:
    """Whether a host runs a capped program under user systemd: asked once, and kept until the
    host changes (27.12).

    A shell is made on every call (`Hosts.shell()`), so the answer is kept in the map a service's
    hosts share (`Hosts`' own), by host name: a host is probed once, not before every gate and
    every turn."""

    def __init__(self, shell: HostShell, seen: MutableMapping[str, bool] | None = None) -> None:
        self._shell = shell
        self._seen: MutableMapping[str, bool] = seen if seen is not None else {}
        """The answers hosts have already given, by name (shared by one service's shells)."""

    async def there(self) -> bool:
        """Whether `systemd-run --user --scope` works here."""
        name = self._shell.host.name
        if name not in self._seen:
            try:
                result = await self._shell.run([*SYSTEMD, "true"], within=PROBE, check=False)
                self._seen[name] = result.returncode == 0
            except PluginFailed, ToolUnavailable, TimeoutError:
                self._seen[name] = False
        return self._seen[name]


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
    reports_status: bool = False
    """Whether the remote shell waits for the program and exits with its code, instead of
    `exec`ing it (27.12). A capped run needs it: a shell that execs a program the kernel kills at
    its cap dies with it, so sshd reports a signal and the ssh client exits 255 — "the host was
    not reached" — instead of 137, which is what a cap looks like."""

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
    program = " ".join(shlex.quote(part) for part in command.argv)
    parts.append(program if command.reports_status else "exec " + program)
    body = " && ".join(parts)
    if command.cwd is not None:
        # (Joined with `&&`: a folder that is not there, or a secret line that never comes, stops
        # the whole command, so the program never runs with an empty secret or somewhere else.
        # Local run() refuses the same cases, so the shells agree.)
        body = f"cd -- {shlex.quote(command.cwd)} && {{ {body}; }}"
    if command.reports_status:
        # (Waiting for it is what turns a kill into an exit code: the shell is still there to
        # say 128+9, where an exec'd program's signal would be sshd's "exit-signal".)
        body += "; exit $?"
    return body


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


def inherited() -> dict[str, str]:
    """udl's own environment, as a program it runs is given it: without git's REPO_VARIABLES."""
    return {name: value for name, value in os.environ.items() if name not in REPO_VARIABLES}


def _environment(command: Command, host: Host) -> dict[str, str]:
    """The whole environment a local program runs with: udl's (inherited()), the host's, the
    command's and its secrets (which are in no argv, here or there)."""
    return {**inherited(), **host.env, **command.env, **command.secret_env}


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
    # (Its stderr goes to a file, not a pipe: a pipe nobody drains is a transport asyncio leaves
    # open — and a ProxyCommand or ProxyJump helper from ~/.ssh/config can hold one open long
    # after ssh itself has ended, which is a warning at the next collection, not a tunnel.)
    with tempfile.TemporaryFile() as errors:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=errors,
        )
        try:
            await _tunnel_up(process, errors, local)
            yield f"http://127.0.0.1:{local}"
        finally:
            await _stopped(process)


async def _tunnel_up(process: asyncio.subprocess.Process, errors: IO[bytes], local: int) -> None:
    """Wait until the tunnel's local port accepts a connection (or ssh gives up).

    ssh would otherwise sit there silently when the tunnel cannot be set up, and a caller would
    hand out a URL that leads nowhere."""
    deadline = asyncio.get_event_loop().time() + FORWARD_WAIT.total_seconds()
    while True:
        if process.returncode is not None:
            errors.seek(0)
            detail = errors.read().decode(errors="replace").strip()
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
        # (Let the reader reach the end of the output rather than cancelling it: a cancelled read
        # leaves the program's pipe open, and asyncio warns about the transport at the next
        # collection. It ends as soon as the program does.)
        self._timer.cancel()
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._task), END.total_seconds())
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def wait(self) -> int:
        """Wait for it to end, and give its exit code."""
        return await self._process.wait()


# ---------------------------------------------------------------------------------------------
# The shells
# ---------------------------------------------------------------------------------------------


async def _limited(
    probe: Probe, command: Command, limits: ResourceLimits | None, unit: str | None
) -> tuple[Command, Literal["enforced", "unenforced"] | None]:
    """`command` as the run it will be: wrapped in a capped scope when it is capped and the host
    has user systemd, as it is otherwise (and the state its result says)."""
    if limits is None:
        return command, None
    if await probe.there():
        return (
            replace(command, argv=capped(command.argv, limits, unit=unit), reports_status=True),
            "enforced",
        )
    return command, "unenforced"


def _stateful(
    result: ProcessResult, state: Literal["enforced", "unenforced"] | None
) -> ProcessResult:
    """`result` saying whether the cap it was given was enforced."""
    return result if state is None else result.model_copy(update={"limits": state})


def _over_cap(
    result: ProcessResult,
    limits: ResourceLimits | None,
    *,
    state: Literal["enforced", "unenforced"] | None = None,
    what: str = "The program",
) -> None:
    """Raise OverCap when a capped run was stopped at its cap by the kernel (SIGKILL: exit 137,
    or -9 from a shell), which is the one thing a run this size ends on that udl did not do."""
    enforced = (state or result.limits) == "enforced"
    if limits is None or not enforced or result.returncode not in (137, -9):
        return
    raise OverCap(f"{what} passed its memory cap of {limits.words}")


class LocalShell:
    """A shell on this machine: udl.process."""

    def __init__(self, host: Host, seen: MutableMapping[str, bool] | None = None) -> None:
        if host.transport is not HostTransport.LOCAL:
            raise ValueError(f"{host.name!r} is not this machine")
        self.host = host
        self.probe = Probe(self, seen)

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
        check: bool = True,
        tail: bool = False,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> Awaitable[ProcessResult]:
        return self._run(
            _command(argv, cwd, env, secret_env, stdin),
            within,
            cancel,
            check=check,
            tail=tail,
            limits=limits,
            unit=unit,
        )

    async def _run(
        self,
        command: Command,
        within: timedelta | None,
        cancel: CancelToken | None,
        *,
        check: bool = True,
        tail: bool = False,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> ProcessResult:
        _check_cwd(command)
        program = command.argv[0]
        command, state = await _limited(self.probe, command, limits, unit)
        result = await _timed(
            run_process(
                command.argv,
                cancel=cancel or CancelToken(),
                stdin=command.stdin,
                env=_environment(command, self.host),
                cwd=None if command.cwd is None else Path(command.cwd),
                max_output=KEPT,
                cancelled=STOPPED,
                tail=tail,
            ),
            within,
        )
        result = _stateful(result, state)
        if check:
            _over_cap(result, limits)
        return _checked(result, program=program) if check else result

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> AsyncGenerator[bytes]:
        return self._stream(
            _command(argv, cwd, env, secret_env, stdin), within, limits=limits, unit=unit
        )

    async def _stream(
        self,
        command: Command,
        within: timedelta | None,
        *,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> AsyncGenerator[bytes]:
        _check_cwd(command)
        command, state = await _limited(self.probe, command, limits, unit)
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
            _over_cap(
                ProcessResult(returncode=process.returncode or 0, stdout="", stderr=""),
                limits,
                state=state,
            )
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

    def __init__(self, host: Host, seen: MutableMapping[str, bool] | None = None) -> None:
        if host.transport is not HostTransport.SSH:
            raise ValueError(f"{host.name!r} is not reached over SSH")
        self.host = host
        self.probe = Probe(self, seen)

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
        check: bool = True,
        tail: bool = False,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> Awaitable[ProcessResult]:
        return self._run(
            _command(argv, cwd, env, secret_env, stdin),
            within,
            cancel,
            check=check,
            tail=tail,
            limits=limits,
            unit=unit,
        )

    async def _run(
        self,
        command: Command,
        within: timedelta | None,
        cancel: CancelToken | None,
        *,
        check: bool = True,
        tail: bool = False,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> ProcessResult:
        program = command.argv[0]
        command, state = await _limited(self.probe, command, limits, unit)
        result = await _timed(
            run_process(
                ssh_argv(self.host, command),
                cancel=cancel or CancelToken(),
                stdin=remote_stdin(command),
                max_output=KEPT,
                cancelled=STOPPED,
                tail=tail,
            ),
            within,
        )
        result = _stateful(result, state)
        if check:
            _over_cap(result, limits)
        return _checked(result, program=program, via_ssh=True) if check else result

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> AsyncGenerator[bytes]:
        return self._stream(
            _command(argv, cwd, env, secret_env, stdin), within, limits=limits, unit=unit
        )

    async def _stream(
        self,
        command: Command,
        within: timedelta | None,
        *,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> AsyncGenerator[bytes]:
        command, state = await _limited(self.probe, command, limits, unit)
        process = await _started(
            *ssh_argv(self.host, command), stdin=remote_stdin(command), env=None, cwd=None
        )
        try:
            await _feed(process, remote_stdin(command))
            async for chunk in _output(process, within):
                yield chunk
            _over_cap(
                ProcessResult(returncode=process.returncode or 0, stdout="", stderr=""),
                limits,
                state=state,
            )
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


async def enforces(shell: HostShell) -> bool:
    """Whether a capped run on this host really is a unit, which is what a gate that ran past its
    time has to stop (27.12).

    A shell of ours is asked its own `probe`, the answer kept per host, so a gate costs no second
    probe; one that is not (a test's fake) is asked the way a shell asks — with a probe run."""
    if isinstance(shell, (LocalShell, SshShell)):
        return await shell.probe.there()
    try:
        said = await shell.run([*SYSTEMD, "true"], within=PROBE, check=False)
    except PluginFailed, ToolUnavailable, TimeoutError:
        return False
    return said.returncode == 0


def shell_for(host: Host, seen: MutableMapping[str, bool] | None = None) -> HostShell:
    """The shell that acts on `host`: LocalShell for this machine, SshShell for another.

    `seen` is where the caps probe keeps its answer per host (27.12), shared by one service's
    shells so a host is asked once."""
    if host.transport is HostTransport.LOCAL:
        return LocalShell(host, seen)
    return SshShell(host, seen)
