"""
--------------
- command.py -
--------------

CommandRunner: any CLI as a harness (the `command` runner). Its argv is a template — `{model}`,
`{session}` and `{cwd}` are filled in — with the prompt on stdin, and `resume_argv` (when it has
one) is what a later turn runs. Its output is one line at a time: plain text, or one JSON object a
line (`output: jsonl`), whose `text` is what the model said (a line udl cannot read is said as it
is, never fatal). Exit 0 ends the turn as done; anything else ends it as an error with what the
program said on stderr.

A program may only be started when its name is in `[orch] allowed_commands` (set in the config
file only, as an AI provider's command is): the list is given to the runner when it is made, and a
program that is not in it is refused (NotAllowed) rather than run.
"""

# System Imports
import logging
import shlex
from collections.abc import AsyncIterator, Sequence
from enum import StrEnum
from typing import ClassVar
from uuid import uuid4

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Local Imports
from udl.clock import Clock
from udl.errors import Cancelled, NotAllowed, PluginFailed, ToolUnavailable
from udl.hosts.shell import HostShell
from udl.plugins.requirements import Requirement
from udl.plugins.runners.base import (
    AgentEvent,
    AgentRunner,
    RunnerHealth,
    RunnerModel,
    RunnerOptions,
    SessionRef,
    SessionSpec,
    TextDelta,
    TurnEnded,
    TurnReason,
)
from udl.process import CancelToken

log = logging.getLogger(__name__)

__all__ = ["CommandOutput", "CommandRunner", "CommandRunnerOptions"]


class CommandOutput(StrEnum):
    """What the program prints."""

    TEXT = "text"  # one line at a time, said as it is
    JSONL = "jsonl"  # one JSON object a line, whose `text` is what it said


class _Said(BaseModel):
    """One line of a jsonl program's output, as far as udl reads it: its `text`."""

    model_config = ConfigDict(extra="ignore")

    text: str | None = None


class CommandRunnerOptions(RunnerOptions):
    """The command runner's options: what it runs, and how to read what it says."""

    argv: list[str] = Field(default_factory=list)
    """The program and its arguments, as a template: `{model}`, `{session}` and `{cwd}` are
    filled in, and the prompt goes on stdin."""
    resume_argv: list[str] | None = None
    """What a later turn runs, when it differs (a harness that resumes with an id)."""
    output: CommandOutput = CommandOutput.TEXT
    """How to read its output."""
    models: list[str] = Field(default_factory=lambda: ["default"])
    """What it can run (the `{model}` values it takes)."""


class CommandRunner(AgentRunner[CommandRunnerOptions]):
    """Any CLI as a harness."""

    name: ClassVar[str] = "command"
    options_model: ClassVar[type[RunnerOptions]] = CommandRunnerOptions
    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(
        self,
        options: CommandRunnerOptions,
        *,
        shell: HostShell,
        clock: Clock,
        allowed: Sequence[str] = (),
    ) -> None:
        super().__init__(options, shell=shell, clock=clock)
        self._allowed = frozenset(allowed)
        self._sessions: dict[str, SessionSpec] = {}
        self._sent: set[str] = set()
        self._stopped: set[str] = set()
        self._cancels: dict[str, CancelToken] = {}

    @property
    def program(self) -> str:
        """The program it runs (the first of its argv, before any template is filled in)."""
        return self.options.argv[0] if self.options.argv else ""

    async def health(self) -> RunnerHealth:
        if not self.program:
            return RunnerHealth(installed=False, detail="No argv is set: nothing to run.")
        if self.program not in self._allowed:
            return RunnerHealth(
                installed=False,
                detail=f"{self.program!r} is not in [orch] allowed_commands (the config file).",
            )
        try:
            await self.shell.run(["sh", "-c", f"command -v {shlex.quote(self.program)}"])
        except PluginFailed, ToolUnavailable:
            return RunnerHealth(installed=False, detail=f"{self.program!r} is not on this host.")
        return RunnerHealth(installed=True, signed_in=True, detail="Started as a command.")

    async def models(self) -> list[RunnerModel]:
        return [RunnerModel(id=model, name=model) for model in self.options.models]

    async def start(self, spec: SessionSpec) -> SessionRef:
        if not self.program:
            raise NotAllowed("The command runner has no argv: set one in [runners.command].")
        if self.program not in self._allowed:
            raise NotAllowed(
                f"{self.program!r} is not in [orch] allowed_commands: add it to the config file."
            )
        session = SessionRef(
            runner=self.name, id=uuid4().hex, cwd=spec.cwd, host=self.shell.host.name
        )
        self._sessions[session.id] = spec
        return session

    async def stop(self, session: SessionRef) -> None:
        self._stopped.add(session.id)
        if (cancel := self._cancels.get(session.id)) is not None:
            cancel.cancel()

    async def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        spec = self._sessions.get(session.id)
        if spec is None:
            yield TurnEnded(reason=TurnReason.ERROR, text=f"No session {session.id!r}.")
            return
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        cancel = CancelToken()
        self._cancels[session.id] = cancel
        first = session.id not in self._sent
        try:
            result = await self.shell.run(
                self._argv(session, spec, first=first),
                cwd=session.cwd,
                env=spec.env,
                secret_env={
                    name: value.get_secret_value() for name, value in spec.secret_env.items()
                },
                stdin=prompt.encode(),
                cancel=cancel,
            )
        except Cancelled:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        except (PluginFailed, ToolUnavailable) as failure:
            # (A program that exits with an error, or is not there: what it said is the turn's
            # answer to why it could not go on.)
            yield TurnEnded(reason=TurnReason.ERROR, text=str(failure))
            return
        finally:
            self._cancels.pop(session.id, None)
        self._sent.add(session.id)
        for line in result.stdout.splitlines():
            if (event := self._event(line)) is not None:
                yield event
        lines = result.stdout.strip().splitlines()
        yield TurnEnded(reason=TurnReason.DONE, text=lines[-1] if lines else "")

    def _argv(self, session: SessionRef, spec: SessionSpec, *, first: bool) -> list[str]:
        """The program and its arguments, with the template filled in.

        (A plain `{name}` replacement, not str.format: a program's own arguments often hold
        braces — a JSON config, a Python one-liner — and they must be passed as they are.)"""
        resume = self.options.resume_argv
        template = self.options.argv if first or resume is None else resume
        values = {"{model}": spec.model or "", "{session}": session.id, "{cwd}": spec.cwd}
        filled: list[str] = []
        for part in template:
            for name, value in values.items():
                part = part.replace(name, value)
            filled.append(part)
        return filled

    def _event(self, line: str) -> AgentEvent | None:
        """What a line of the program's output is, or None when it is empty."""
        if not line.strip():
            return None
        if self.options.output is CommandOutput.TEXT:
            return TextDelta(text=line)
        try:
            said = _Said.model_validate_json(line)
        except ValidationError:  # (a line that is not a JSON object: said as it is)
            return TextDelta(text=line)
        return TextDelta(text=said.text if said.text is not None else line)
