"""
------------------
- harnesses.py -
------------------

A host whose harnesses are scripts: what the manage tests (25.11) drive. `ScriptedShell` answers
a command by its argv (`answers`, or a fallback), records every one it was asked to run, keeps the
files written through it, and hands back a `ScriptedInteraction` for a sign-in.

Nothing here runs a real program: a test says what `claude --version` or `opencode auth list`
prints, and what a sign-in asks for.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import timedelta
from typing import ClassVar

# Local Imports
from udl.hosts.shell import Host, HostShell, Interaction
from udl.plugins.requirements import Requirement
from udl.process import CancelToken, ProcessResult

FAILED: int = 127
"""What a shell answers for a program it does not have."""


class ScriptedInteraction:
    """A sign-in that is running: what it says, what it is told, and how it ends."""

    def __init__(self, says: Sequence[str] = (), exit_code: int = 0) -> None:
        self._says = list(says)
        self._ended = asyncio.Event()
        self.exit_code = exit_code
        """What it exits with (0: signed in)."""
        self.written: list[str] = []
        """Every line it was given, in order."""
        self.closed = False
        """Whether it was ended."""
        self.running = True
        """Whether it is still running."""

    async def read(self) -> str:
        """What it has printed since the last read."""
        return self._says.pop(0) if self._says else ""

    async def write(self, text: str) -> None:
        """Give it a line (a sign-in ends once it has what it asked for)."""
        self.written.append(text)
        self._ended.set()

    async def close(self) -> None:
        """End it."""
        self.closed = True
        self.running = False
        self._ended.set()

    async def wait(self) -> int:
        """Wait for it to end: it does once it is given a line, or ended (a sign-in waits)."""
        await self._ended.wait()
        self.running = False
        return self.exit_code


class ScriptedShell(HostShell):
    """A host's shell, answering what a test scripts and recording every argv it is given."""

    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(self, host: Host | None = None) -> None:
        self.host = host if host is not None else Host.local("/w")
        self.answers: dict[str, ProcessResult] = {}
        """What each argv answers, by `" ".join(argv)` (a missing one is a program udl has not:
        exit 127)."""
        self.runs: list[list[str]] = []
        """Every argv it was asked to run, in order."""
        self.secret_envs: list[dict[str, str]] = []
        """Every secret environment it was given, in order."""
        self.files: dict[str, str] = {}
        """Every file written through it (its path, and what was written)."""
        self.interactions: list[ScriptedInteraction] = []
        """Every sign-in it started, in order."""
        self.starts: list[list[str]] = []
        """Every argv it started as a sign-in."""
        self.says: Sequence[str] = ("",)
        """What the next sign-in prints, a line per read."""
        self.hanging: set[str] = set()
        """The argv that never answer: what a shell whose `wait_for` runs out raises."""

    def never(self, argv: Sequence[str]) -> None:
        """Say that a command never answers (it runs past the time it is given)."""
        self.hanging.add(" ".join(argv))

    def answer(self, argv: Sequence[str], stdout: str = "", code: int = 0) -> None:
        """Say what a command answers."""
        self.answers[" ".join(argv)] = ProcessResult(
            returncode=code, stdout=stdout, stderr="" if code == 0 else stdout
        )

    async def run(
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
    ) -> ProcessResult:
        self.runs.append(list(argv))
        self.secret_envs.append(dict(secret_env or {}))
        command = " ".join(argv)
        if command in self.hanging:
            raise TimeoutError
        if argv[0] == "sh" and "cat > " in command:
            path = command.split("cat > ", 1)[1]
            self.files[path] = (stdin or b"").decode()
            return ProcessResult(returncode=0, stdout="", stderr="")
        if argv[0] == "sh" and "cat " in command:
            # (A file read back through the shell, as `cat` does it: a file that is not there is
            # a failure, which is what a setup on a fresh host meets.)
            path = command.split("cat ", 1)[1].split(" 2>", 1)[0].split(" ||", 1)[0].strip()
            if path not in self.files:
                return ProcessResult(
                    returncode=FAILED, stdout="", stderr=f"cat: {path}: no such file"
                )
            return ProcessResult(returncode=0, stdout=self.files[path], stderr="")
        found = self.answers.get(command)
        if found is None:
            return ProcessResult(returncode=FAILED, stdout="", stderr=f"{argv[0]}: not found")
        return found

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
        self.runs.append(list(argv))
        said = self.answers.get(" ".join(argv))
        return self._chunks(said.stdout if said is not None else "")

    async def _chunks(self, text: str) -> AsyncIterator[bytes]:
        """A command's output, a line at a time."""
        for line in text.splitlines(keepends=True):
            yield line.encode()

    async def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Interaction:
        self.starts.append(list(argv))
        started = ScriptedInteraction(self.says)
        self.interactions.append(started)
        return started

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return nullcontext(f"http://127.0.0.1:{port}")
