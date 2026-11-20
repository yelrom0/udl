"""
--------------
- command.py -
--------------

CommandRunner: any CLI as a harness (the `command` runner). Its argv is a template — `{model}`,
`{session}` and `{cwd}` are filled in — with the prompt on stdin, and `resume_argv` (when it has
one) is what a later turn runs. Its output is one line at a time: plain text, or one JSON object a
line (`output: jsonl`), whose `text` is what the model said (a line udl cannot read is said as it
is, never fatal). Exit 0 ends the turn as done; anything else ends it as an error with what the
program said on stderr. A jsonl line saying the account is limited (`limited`, with `resets_at`)
ends the turn as limited, with the RunnerLimited that says when it lifts.

A program may only be started when its name is in `[orch] allowed_commands` (set in the config
file only, as an AI provider's command is): the list is given to the runner when it is made, and a
program that is not in it is refused (NotAllowed) rather than run — both templates' programs, the
first turn's and `resume_argv`'s, since either may be the one that runs.

Its sessions may be kept outside the runner (`CommandSessions`): a session then outlives the runner
object, as a harness's does, and another runner given the same one resumes it by id — running
`resume_argv`, since the store says the session has had its first turn.
"""

# System Imports
import logging
import shlex
from collections.abc import AsyncIterator, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
    RunnerLimited,
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

__all__ = ["CommandOutput", "CommandRunner", "CommandRunnerOptions", "CommandSessions"]


@dataclass
class CommandSessions:
    """A command runner's sessions, outside any runner object (a service may hold one)."""

    specs: MutableMapping[str, SessionSpec] = field(default_factory=dict[str, SessionSpec])
    """What each session was started with, by id."""
    sent: set[str] = field(default_factory=set[str])
    """The sessions that have had a turn (their next one runs `resume_argv`)."""


class CommandOutput(StrEnum):
    """What the program prints."""

    TEXT = "text"  # one line at a time, said as it is
    JSONL = "jsonl"  # one JSON object a line, whose `text` is what it said


class _Said(BaseModel):
    """One line of a jsonl program's output, as far as udl reads it: its `text`, and whether it
    says the account is limited (and until when)."""

    model_config = ConfigDict(extra="ignore")

    text: str | None = None
    result: str | None = None
    limited: bool = False
    resets_at: int | None = None


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
        sessions: CommandSessions | MutableMapping[str, SessionSpec] | None = None,
    ) -> None:
        super().__init__(options, shell=shell, clock=clock)
        self._allowed = frozenset(allowed)
        # (A bare mapping is the specs alone: it says nothing of first turns, so each runner
        # object keeps its own — give it a `CommandSessions` for a session to outlive the object.)
        self._sessions = (
            sessions
            if isinstance(sessions, CommandSessions)
            else CommandSessions(specs=sessions if sessions is not None else {})
        )
        self._stopped: set[str] = set()
        self._cancels: dict[str, CancelToken] = {}

    @property
    def program(self) -> str:
        """The program it runs (the first of its argv, before any template is filled in)."""
        return self.options.argv[0] if self.options.argv else ""

    @property
    def programs(self) -> tuple[str, ...]:
        """Every program a turn may run: the first turn's, and `resume_argv`'s when it has one."""
        resume = self.options.resume_argv
        return tuple(dict.fromkeys([self.program, *(resume[:1] if resume else [])]))

    @property
    def sign_in_command(self) -> tuple[str, ...]:
        """The program itself, run in a shell on the host: a CLI udl does not know signs itself
        in its own way, and 25.11 answers with that command."""
        return (self.program,) if self.program else ()

    async def health(self) -> RunnerHealth:
        if not self.program:
            return RunnerHealth(installed=False, detail="No argv is set: nothing to run.")
        for program in self.programs:
            if program not in self._allowed:
                return RunnerHealth(
                    installed=False,
                    detail=f"{program!r} is not in [orch] allowed_commands (the config file).",
                )
        for program in self.programs:
            try:
                await self.shell.run(["sh", "-c", f"command -v {shlex.quote(program)}"])
            except PluginFailed, ToolUnavailable:
                return RunnerHealth(installed=False, detail=f"{program!r} is not on this host.")
        return RunnerHealth(installed=True, signed_in=True, detail="Started as a command.")

    async def models(self) -> list[RunnerModel]:
        return [RunnerModel(id=model, name=model) for model in self.options.models]

    async def start(self, spec: SessionSpec) -> SessionRef:
        if not self.program:
            raise NotAllowed("The command runner has no argv: set one in [runners.command].")
        for program in self.programs:
            if program not in self._allowed:
                raise NotAllowed(
                    f"{program!r} is not in [orch] allowed_commands: add it to the config file."
                )
        session = SessionRef(
            runner=self.name, id=uuid4().hex, cwd=spec.cwd, host=self.shell.host.name
        )
        self._sessions.specs[session.id] = spec
        return session

    async def stop(self, session: SessionRef) -> None:
        self._stopped.add(session.id)
        if (cancel := self._cancels.get(session.id)) is not None:
            cancel.cancel()

    async def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        spec = self._sessions.specs.get(session.id)
        if spec is None:
            yield TurnEnded(reason=TurnReason.ERROR, text=f"No session {session.id!r}.")
            return
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        cancel = CancelToken()
        self._cancels[session.id] = cancel
        first = session.id not in self._sessions.sent
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
        self._sessions.sent.add(session.id)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        for line in lines:
            if (limited := self._limited(line)) is not None:
                yield limited
                yield TurnEnded(reason=TurnReason.LIMITED, text=limited.message)
                return
            if (event := self._event(line)) is not None:
                yield event
        # (Its answer is what the last line said, read as its output is: a jsonl line's text.)
        answer = self._said(lines[-1]) if lines else ""
        yield TurnEnded(reason=TurnReason.DONE, text=answer)

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

    def _limited(self, line: str) -> RunnerLimited | None:
        """What a line says about the account's limit, when it says the turn cannot go on."""
        if self.options.output is not CommandOutput.JSONL or not line.strip():
            return None
        try:
            said = _Said.model_validate_json(line)
        except ValidationError:  # (a line that is not a JSON object says nothing about a limit)
            return None
        if not said.limited:
            return None
        until = (
            datetime.fromtimestamp(said.resets_at, tz=UTC) if said.resets_at is not None else None
        )
        return RunnerLimited(
            until=until, message=said.result or said.text or "The account is limited."
        )

    def _said(self, line: str) -> str:
        """What a line says, read as its output is (a jsonl object's text, or the line itself)."""
        if self.options.output is CommandOutput.TEXT:
            return line
        try:
            said = _Said.model_validate_json(line)
        except ValidationError:
            return line
        return said.result or said.text or line

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
