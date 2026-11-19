"""
------------------
- claude_code.py -
------------------

ClaudeCode: the `claude-code` runner, driving Claude Code's CLI on a host through that host's
HostShell (so a `claude` on thevault is driven exactly as the one here).

A turn is one process: `claude -p --output-format stream-json --verbose`, with `--session-id` on
the first turn and `--resume` after it, the model and the permission mode from the options, the
brief in a file (`--append-system-prompt-file`), the deny list in a settings file (`--settings`),
and udl's MCP server in a config file (`--mcp-config` + `--strict-mcp-config`). The two files are
written through the shell, `0600`, in the worktree's `.udl/` — which is git-ignored through the
worktree's own `info/exclude`, never the hub's disk. The token is in the MCP config file and
nowhere else: not in an argv, not in a log.

Its stream's JSON lines become AgentEvents (what it said, the tools it ran, what the turn cost,
and how it ended); a line udl does not know is skipped, never fatal. A usage-limit result becomes
RunnerLimited with the reset time when the CLI gives one.

Its sessions may be kept outside the runner (`ClaudeSessions`): a session then outlives the runner
object, and a runner made later resumes it by id (`--resume`) rather than starting it again — the
store says it has had a turn, so the id is the CLI's already.

`health()` reads `claude --version` and `claude auth status`. udl never opens Claude Code's own
credential files (O-C3): the account is what the CLI says, and it is never logged.
"""

# System Imports
import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import uuid4

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Local Imports
from udl.clock import Clock
from udl.errors import PluginFailed, ToolUnavailable
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
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnReason,
    Usage,
)

log = logging.getLogger(__name__)

__all__ = ["ClaudeCode", "ClaudeCodeOptions", "ClaudeSessions"]

_FOLDER: str = ".udl"
"""Where a session's files go, in its worktree (git-ignored through info/exclude)."""

_RESET: re.Pattern[str] = re.compile(
    r"(?:resets?|back)\s+(?:at|on)?\s*"
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.IGNORECASE,
)
"""How a limit's result says when the quota is back (an ISO time, when it says one)."""

_EPOCH: re.Pattern[str] = re.compile(r"\|\s*(\d{10})\s*$")
"""The other way it says it: the text, then `|<epoch second>` (what the CLI prints)."""


@dataclass
class ClaudeSessions:
    """A claude-code runner's sessions, outside any runner object (a service may hold one)."""

    specs: MutableMapping[str, SessionSpec] = field(default_factory=dict[str, SessionSpec])
    """What each session was started with, by id."""
    sent: set[str] = field(default_factory=set[str])
    """The sessions that have had a turn (the CLI's session exists: the next turn resumes it)."""


class ClaudeCodeOptions(RunnerOptions):
    """The claude-code runner's options."""

    cli: list[str] = Field(default_factory=lambda: ["claude"])
    """The program and any arguments before the turn's own (a test points it at a fake)."""
    models: list[str] = Field(default_factory=lambda: ["opus", "sonnet", "haiku"])
    """What it can run: the aliases the CLI takes (it has no listing call)."""
    permission_mode: str = "acceptEdits"
    """`--permission-mode` (acceptEdits, auto, bypassPermissions, manual, …)."""


class _Status(BaseModel):
    """What `claude auth status` says (the account is shown, never logged)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    logged_in: bool = Field(default=False, alias="loggedIn")
    email: str | None = None
    subscription_type: str | None = Field(default=None, alias="subscriptionType")


class _Item(BaseModel):
    """One content item of a message: what the model said, or a tool it started or finished."""

    model_config = ConfigDict(extra="ignore")

    type: str | None = None
    text: str | None = None
    name: str | None = None
    id: str | None = None
    tool_use_id: str | None = None
    is_error: bool = False
    input: dict[str, object] | str | None = None


class _Message(BaseModel):
    """A message in the stream: its content (a line of text, or items)."""

    model_config = ConfigDict(extra="ignore")

    content: str | list[_Item] | None = None


class _Usage(BaseModel):
    """What a turn was given and wrote (the CLI says more, and udl reads what it needs)."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = 0
    output_tokens: int = 0


class _Line(BaseModel):
    """One line of `claude`'s stream-json, as far as udl reads it."""

    model_config = ConfigDict(extra="ignore")

    type: str
    subtype: str | None = None
    is_error: bool = False
    result: str | None = None
    message: _Message | None = None
    usage: _Usage | None = None
    total_cost_usd: float | None = None


class ClaudeCode(AgentRunner[ClaudeCodeOptions]):
    """Claude Code as a runner."""

    name: ClassVar[str] = "claude-code"
    options_model: ClassVar[type[RunnerOptions]] = ClaudeCodeOptions
    requires: ClassVar[tuple[Requirement, ...]] = (Requirement.binary("claude"),)

    def __init__(
        self,
        options: ClaudeCodeOptions,
        *,
        shell: HostShell,
        clock: Clock,
        sessions: ClaudeSessions | MutableMapping[str, SessionSpec] | None = None,
    ) -> None:
        super().__init__(options, shell=shell, clock=clock)
        # (A bare mapping is the specs alone: it says nothing of first turns, so each runner
        # object keeps its own — give it a `ClaudeSessions` for a session to outlive the object.)
        self._sessions = (
            sessions
            if isinstance(sessions, ClaudeSessions)
            else ClaudeSessions(specs=sessions if sessions is not None else {})
        )
        self._stopped: set[str] = set()
        self._pulls: dict[str, asyncio.Task[None]] = {}
        self._tools: dict[str, str] = {}

    async def health(self) -> RunnerHealth:
        try:
            version = await self.shell.run([*self.options.cli, "--version"])
            status = await self.shell.run([*self.options.cli, "auth", "status"])
        except (ToolUnavailable, PluginFailed) as failure:
            return RunnerHealth(installed=False, detail=str(failure))
        return _health(version.stdout.strip(), status.stdout)

    async def models(self) -> list[RunnerModel]:
        return [RunnerModel(id=model, name=model) for model in self.options.models]

    async def start(self, spec: SessionSpec) -> SessionRef:
        session = SessionRef(
            runner=self.name, id=str(uuid4()), cwd=spec.cwd, host=self.shell.host.name
        )
        self._sessions.specs[session.id] = spec
        folder = f"{spec.cwd}/{_FOLDER}"
        if spec.instructions is not None:
            await self._write(f"{folder}/system-prompt.md", spec.instructions)
        await self._write(
            f"{folder}/settings.json",
            json.dumps({"permissions": {"deny": list(spec.deny)}}, indent=2),
        )
        if spec.mcp is not None:
            await self._write(f"{folder}/mcp.json", _mcp_config(spec))
        await self._exclude(spec.cwd)
        return session

    async def stop(self, session: SessionRef) -> None:
        self._stopped.add(session.id)
        if (pull := self._pulls.pop(session.id, None)) is not None:
            pull.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pull

    async def send(self, session: SessionRef, prompt: str) -> AsyncGenerator[AgentEvent]:
        spec = self._sessions.specs.get(session.id)
        if spec is None:
            yield TurnEnded(reason=TurnReason.ERROR, text=f"No session {session.id!r}.")
            return
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        # (Marked before the turn runs: a turn closed early has still started the CLI's session,
        # so the next one must resume it, not give `--session-id` an id already in use.)
        first = session.id not in self._sessions.sent
        self._sessions.sent.add(session.id)
        ended: TurnEnded | None = None
        try:
            async for chunk in self._chunks(session, prompt, spec, first=first):
                for line in chunk.decode(errors="replace").splitlines():
                    if not line.strip():
                        continue
                    for event in self._events(line):
                        if isinstance(event, TurnEnded):
                            ended = event
                        else:
                            yield event
        except (ToolUnavailable, PluginFailed) as failure:
            ended = TurnEnded(reason=TurnReason.ERROR, text=str(failure))

        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        yield ended or TurnEnded(reason=TurnReason.ERROR, text="The turn ended without saying how.")

    async def _chunks(
        self, session: SessionRef, prompt: str, spec: SessionSpec, *, first: bool
    ) -> AsyncIterator[bytes]:
        """What the turn's process prints, as it prints it.

        (Pulled by a task of its own, so stop() can cancel it: closing a generator that is being
        read cannot, and the shell closes the process when its generator ends.)"""
        turn = self.shell.stream(
            self._argv(session, spec, first=first),
            cwd=session.cwd,
            env=spec.env,
            secret_env={name: value.get_secret_value() for name, value in spec.secret_env.items()},
            stdin=prompt.encode(),
        )
        queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()

        async def pull() -> None:
            try:
                async for chunk in turn:
                    await queue.put(chunk)
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 - (a process that could not start, or broke)
                await queue.put(failure)
            finally:
                await queue.put(None)

        task = asyncio.create_task(pull())
        self._pulls[session.id] = task
        try:
            while (item := await queue.get()) is not None:
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self._pulls.pop(session.id, None)
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _argv(self, session: SessionRef, spec: SessionSpec, *, first: bool) -> list[str]:
        """The one process a turn is."""
        folder = f"{spec.cwd}/{_FOLDER}"
        argv = [*self.options.cli, "-p", "--output-format", "stream-json", "--verbose"]
        argv += ["--model", spec.model] if spec.model else []
        argv += ["--permission-mode", self.options.permission_mode]
        argv += ["--session-id", session.id] if first else ["--resume", session.id]
        argv += ["--settings", f"{folder}/settings.json"]
        if spec.instructions is not None:
            argv += ["--append-system-prompt-file", f"{folder}/system-prompt.md"]
        if spec.mcp is not None:
            argv += ["--mcp-config", f"{folder}/mcp.json", "--strict-mcp-config"]
        return argv

    def _events(self, line: str) -> list[AgentEvent]:
        """What one line of the stream is (nothing, when udl does not know it)."""
        try:
            said = _Line.model_validate_json(line)
        except ValueError:
            log.debug("claude said a line udl cannot read: %r", line[:200])
            return []
        if said.type == "assistant" and said.message is not None:
            return self._content(said.message)
        if said.type == "user" and said.message is not None:
            return self._results(said.message)
        if said.type == "result":
            return self._ended(said)
        return []

    def _content(self, message: _Message) -> list[AgentEvent]:
        """What the model said and the tools it started, in one assistant message."""
        events: list[AgentEvent] = []
        for item in _items(message):
            if item.type == "text" and item.text is not None:
                events.append(TextDelta(text=item.text))
            elif item.type == "tool_use" and item.name is not None:
                if item.id is not None:
                    self._tools[item.id] = item.name
                events.append(ToolStarted(name=item.name, summary=_summary(item.input)))
        return events

    def _results(self, message: _Message) -> list[AgentEvent]:
        """The tools that finished, in one user message (its tool results)."""
        events: list[AgentEvent] = []
        for item in _items(message):
            if item.type != "tool_result":
                continue
            identifier = item.tool_use_id
            name = self._tools.pop(identifier, "tool") if identifier is not None else "tool"
            events.append(ToolFinished(name=name, ok=not item.is_error))
        return events

    def _ended(self, said: _Line) -> list[AgentEvent]:
        """The result: what the turn cost, and how it ended."""
        events: list[AgentEvent] = []
        if said.usage is not None:
            events.append(
                Usage(
                    input_tokens=said.usage.input_tokens,
                    output_tokens=said.usage.output_tokens,
                    cost=_cost(said.total_cost_usd),
                )
            )
        text = said.result or ""
        subtype = said.subtype or ""
        if _is_limited(subtype, text):
            events.append(RunnerLimited(until=_reset(text), message=text or subtype))
            events.append(TurnEnded(reason=TurnReason.LIMITED, text=text or subtype))
        elif said.is_error or not subtype.startswith("success"):
            events.append(TurnEnded(reason=TurnReason.ERROR, text=text or subtype))
        else:
            events.append(TurnEnded(reason=TurnReason.DONE, text=text))
        return events

    async def _write(self, path: str, content: str) -> None:
        """Write a file on the host, `0600`, through the shell (never from the hub's disk)."""
        await self.shell.run(
            ["sh", "-c", 'umask 077; mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", path],
            stdin=content.encode(),
        )

    async def _exclude(self, cwd: str) -> None:
        """List the session's folder in the worktree's `info/exclude`, once."""
        await self.shell.run(
            [
                "sh",
                "-c",
                'd=$(git -C "$1" rev-parse --git-path info/exclude 2>/dev/null) || exit 0; '
                'mkdir -p "$(dirname "$d")"; grep -qxF "$2" "$d" 2>/dev/null || '
                'printf \'%s\\n\' "$2" >> "$d"',
                "sh",
                cwd,
                f"/{_FOLDER}/",
            ]
        )


def _items(message: _Message) -> list[_Item]:
    """A message's content items (a string message is one text item)."""
    content = message.content
    if isinstance(content, str):
        return [_Item(type="text", text=content)]
    return content or []


def _summary(tool_input: dict[str, object] | str | None) -> str:
    """What a tool is doing, in a line (never a secret: a command, a path, a query)."""
    if isinstance(tool_input, dict):
        for key in ("command", "file_path", "path", "query", "pattern", "url"):
            if isinstance(value := tool_input.get(key), str):
                return value[:200]
        if tool_input:
            return ", ".join(sorted(tool_input))[:200]
    return ""


def _cost(total: float | None) -> Decimal | None:
    """What a turn cost, as the CLI says (a subscription's turns are not paid for)."""
    return None if total is None else Decimal(str(total))


def _is_limited(subtype: str, text: str) -> bool:
    """Whether a result says the account is out of quota."""
    return "limit" in subtype or "usage limit" in text.lower()


def _reset(text: str) -> datetime | None:
    """When a limit's result says the quota is back, if it says."""
    if (epoch := _EPOCH.search(text)) is not None:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    if (found := _RESET.search(text)) is None:
        return None
    said = found.group(1).replace(" ", "T").replace("Z", "+00:00")
    try:
        reset = datetime.fromisoformat(said)
    except ValueError:
        return None
    return reset if reset.tzinfo is not None else reset.replace(tzinfo=UTC)


def _mcp_config(spec: SessionSpec) -> str:
    """udl's MCP server, as Claude Code is pointed at it: the token in this file, nowhere else."""
    server: dict[str, object] = {"type": "http", "url": spec.mcp.url if spec.mcp else ""}
    if spec.mcp is not None and spec.mcp.token is not None:
        server["headers"] = {"Authorization": f"Bearer {spec.mcp.token.get_secret_value()}"}
    return json.dumps({"mcpServers": {"udl": server}}, indent=2)


def _health(version: str, status: str) -> RunnerHealth:
    """What `--version` and `auth status` say about the harness."""
    if not version:
        return RunnerHealth(installed=False, detail="claude says no version.")
    try:
        said = _Status.model_validate_json(status)
    except ValidationError:
        return RunnerHealth(installed=True, version=version, detail=status.strip() or None)
    return RunnerHealth(
        installed=True,
        signed_in=said.logged_in,
        version=version,
        account=said.email,
        detail=said.subscription_type,
    )
