"""
------------------
- codex_chat.py -
------------------

A person's Codex chat (27.11's Codex half): `codex app-server`, the JSON-RPC protocol over stdio
that OpenAI's own IDE extension uses, driven as a chat's harness.

It is not `codex exec` (25.12's runner): that is one process per turn, with no way to ask. A
chat's harness has to ask before it acts (O-C15), so it is the long-lived `app-server`, whose
requests come to udl over the same connection: an approval is a request from the server, and udl
answers it with the person's decision.

The methods and their shapes are the installed Codex's own: `codex app-server generate-json-schema
--out <dir>` writes them, and `tests/fixtures/codex-app-server/` is that output (0.171.0), so a
change in the protocol is a change there — and a test validates every message against them. The
ones this uses:

- `initialize`, then `thread/start` (with `cwd`, `model`, `approvalPolicy: on-request` and udl's
  MCP server in `config`);
- `turn/start` for each turn, with the person's words as its input;
- `item/agentMessage/delta`, `item/started`, `item/completed` and `turn/completed` as the turn's
  events, and `thread/tokenUsage/updated` for what it cost;
- `item/commandExecution/requestApproval` and `item/fileChange/requestApproval` (v2), and the
  older `execCommandApproval` and `applyPatchApproval` (v1), as the approvals.

The two vocabularies are not the same: a v2 approval is answered `accept`, `acceptForSession` or
`decline`, and a legacy one `approved`, `approved_for_session` or `denied` (the schemas'
`ReviewDecision`). Neither carries a message, so a person's reason is udl's to keep (the note on
the record), not something the harness is told.

udl never passes Codex's bypass flag: a chat that cannot ask is not started at all.
"""

# System Imports
import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final
from uuid import uuid4

# Package Imports
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

# Local Imports
from udl.db.models import JsonObject
from udl.errors import PluginFailed, ToolUnavailable
from udl.hosts.shell import HostShell, Interaction
from udl.plugins.runners.base import (
    AgentEvent,
    RunnerFailed,
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

__all__ = ["Ask", "CodexChat", "CodexQuestion"]

_ANSWER: Final = "accept"
"""Let it act, this once (a v2 approval)."""
_ANSWER_FOR_CHAT: Final = "acceptForSession"
"""Let it act, and the same request for the rest of the chat (a v2 approval)."""
_DECLINE: Final = "decline"
"""Do not let it act (a v2 approval)."""
_OLD_ANSWER: Final = "approved"
"""The same, in the older vocabulary (`ReviewDecision`, the v1 approvals)."""
_OLD_ANSWER_FOR_CHAT: Final = "approved_for_session"
_OLD_DECLINE: Final = "denied"

_APPROVALS: Final = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "file",
    "execCommandApproval": "legacy",
    "applyPatchApproval": "legacy",
}
"""Each approval request, and which vocabulary answers it: `command`/`file` for the v2 shapes,
`legacy` for the older `ReviewDecision` ones."""

_WAIT: Final = 0.05
"""How long a reader waits for output before looking again (a person's turn is not a busy loop)."""


class _Envelope(BaseModel):
    """One JSON-RPC message: a request (an `id` and a `method`), an answer (an `id` and a
    `result`, or an `error`), or a notification (a `method` alone)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    jsonrpc: str = "2.0"
    """The protocol's version (always 2.0)."""
    id: str | int | None = None
    """What it is answering, when it is a request or an answer (the app-server numbers its own
    requests, so this is an int as often as a string)."""
    method: str | None = None
    """What it is, when it is a request or a notification."""
    params: JsonObject | None = None
    """What it is about, when it is a request or a notification."""
    result: JsonObject | None = None
    """What was asked for, when it is an answer."""
    error: JsonObject | None = None
    """Why it could not, when it is an answer that failed."""


_ENVELOPE: Final = TypeAdapter[_Envelope](_Envelope)
"""One line the server said, as a message (anything else is not the protocol)."""


@dataclass
class CodexQuestion:
    """What the harness asked, and what the person said: may it act, may it always, and why not."""

    allowed: bool
    for_chat: bool = False
    message: str = ""


Ask = Callable[[str, JsonObject, str], Awaitable[CodexQuestion]]
"""What `CodexChat` asks when its harness does: the tool, what it wants to use it on, and the
request's own id (so a person's answer is matched to the request it answers)."""


@dataclass
class _Session:
    """One running `app-server`: its process, its thread, and what is waiting on it."""

    interaction: Interaction
    thread: str = ""
    said: asyncio.Queue[AgentEvent | None] = field(default_factory=lambda: asyncio.Queue())
    waiting: dict[str | int, asyncio.Future[_Envelope]] = field(default_factory=lambda: {})
    paths: dict[str, str] = field(default_factory=lambda: {})
    """What each `fileChange` item is changing, by its `itemId`: a v2 file approval names the item
    and not the paths, which the `item/started` before it carried."""
    reader: asyncio.Task[None] | None = None
    buffer: str = ""


class CodexChat:
    """A chat's Codex harness: one `codex app-server` per chat, spoken to in JSON-RPC."""

    def __init__(
        self,
        *,
        cli: Sequence[str],
        shell: HostShell,
        ask: Ask,
        within: timedelta,
    ) -> None:
        self._cli = list(cli)
        self._shell = shell
        self._ask = ask
        self._within = within
        """How long the server may live without a turn (the chat's own idle time: the process is
        the chat's, and must outlast a turn — 25.8's 30-second bug is not repeated here)."""
        self._sessions: dict[str, _Session] = {}
        """The running servers, by their thread's id (a chat's ref is its thread)."""

    # -- starting ------------------------------------------------------------------------------

    async def start(self, spec: SessionSpec) -> SessionRef:
        """Start `app-server` in `spec.cwd` and open a thread in it (with udl's MCP server, whose
        token is in the environment, never in an argv)."""
        interaction = await self._shell.interact(
            [*self._cli, "app-server"],
            cwd=spec.cwd,
            env=spec.env,
            secret_env={name: value.get_secret_value() for name, value in spec.secret_env.items()},
            within=self._within,
        )
        session = _Session(interaction=interaction)
        session.reader = asyncio.create_task(self._read(session))
        try:
            await self._call(session, "initialize", {"clientInfo": {"name": "udl", "version": "1"}})
            started = await self._call(
                session,
                "thread/start",
                {
                    "cwd": spec.cwd,
                    "model": spec.model,
                    "approvalPolicy": "on-request",
                    "config": _mcp(spec),
                },
            )
        except Exception:
            await self._close(session)
            raise
        thread = started.result.get("thread") if started.result is not None else None
        session.thread = str(thread.get("id", "")) if isinstance(thread, dict) else ""
        if not session.thread:
            await self._close(session)
            raise PluginFailed("Codex started a thread udl cannot name")
        self._sessions[session.thread] = session
        return SessionRef(
            runner="codex", id=session.thread, cwd=spec.cwd, host=self._shell.host.name
        )

    async def stop(self, session: SessionRef) -> None:
        """End the chat's server (and nothing it was about to do goes on)."""
        found = self._sessions.get(session.id)
        if found is not None:
            await self._close(found)

    async def _close(self, session: _Session) -> None:
        """Close the server's input, and let its reader end."""
        self._sessions = {key: one for key, one in self._sessions.items() if one is not session}
        try:
            await session.interaction.close()
        except Exception as failure:  # noqa: BLE001 - (a server that will not close is logged)
            log.warning("codex app-server would not close: %s", failure)
        if session.reader is not None:
            session.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.reader

    # -- a turn --------------------------------------------------------------------------------

    async def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        """One turn: the person's words, and what the harness says as it says it."""
        found = self._sessions.get(session.id)
        if found is None:
            yield TurnEnded(reason=TurnReason.ERROR, text="That chat's harness is gone.")
            return
        try:
            await self._call(
                found,
                "turn/start",
                {"threadId": found.thread, "input": [{"type": "text", "text": prompt}]},
            )
        except (PluginFailed, ToolUnavailable) as failure:
            yield TurnEnded(reason=TurnReason.ERROR, text=str(failure))
            return
        while True:
            event = await found.said.get()
            if event is None:  # (the server ended: the turn is over, and says so)
                yield TurnEnded(reason=TurnReason.STOPPED, text="Its harness ended.")
                return
            yield event
            if isinstance(event, TurnEnded):
                return

    # -- the protocol --------------------------------------------------------------------------

    async def _call(self, session: _Session, method: str, params: JsonObject) -> _Envelope:
        """One request, and the message it was answered with. An `error` is raised, never
        swallowed: a caller must not read a failure as an answer."""
        asked = str(uuid4())
        waiting: asyncio.Future[_Envelope] = asyncio.get_running_loop().create_future()
        session.waiting[asked] = waiting
        await session.interaction.write(
            json.dumps({"jsonrpc": "2.0", "id": asked, "method": method, "params": params})
        )
        answered = await waiting
        if answered.error is not None:
            raise PluginFailed(f"Codex refused {method}: {answered.error}")
        return answered

    async def _read(self, session: _Session) -> None:
        """Read the server's lines as they come: answers to what udl asked, notifications, and
        the requests it makes (an approval) — each answered as the person decides."""
        try:
            while True:
                said = await session.interaction.read()
                if not said:
                    if not session.interaction.running:
                        # (The server ended: what udl is waiting on will never be answered, and
                        # a caller must not wait for it.)
                        self._fail_waiters(session, "its harness ended")
                        await session.said.put(None)
                        return
                    await asyncio.sleep(_WAIT)
                    continue
                session.buffer += said
                *lines, session.buffer = session.buffer.split("\n")
                for line in lines:
                    if not line.strip():
                        continue
                    try:
                        message = _ENVELOPE.validate_json(line)
                    except ValidationError:
                        log.warning("codex app-server said something that is not JSON: %s", line)
                        continue
                    await self._message(session, message)
        except asyncio.CancelledError:
            raise
        except Exception as failure:  # noqa: BLE001 - (a server that broke is the turn's failure)
            log.warning("codex app-server broke: %s", failure)
            self._fail_waiters(session, str(failure))
            await session.said.put(RunnerFailed(message=str(failure)))
            await session.said.put(None)

    def _fail_waiters(self, session: _Session, why: str) -> None:
        """Answer what udl is waiting on with a failure, so no caller waits for a message that
        will never come."""
        for asked, waiting in list(session.waiting.items()):
            session.waiting.pop(asked, None)
            if not waiting.done():
                waiting.set_result(_Envelope(error={"message": why}))

    async def _message(self, session: _Session, message: _Envelope) -> None:
        """One message: an answer, a request, or a notification."""
        if message.method is None:
            waiting = session.waiting.pop(message.id, None) if message.id is not None else None
            if waiting is not None and not waiting.done():
                waiting.set_result(message)
            return
        if message.method in _APPROVALS:
            await self._approve(session, message)
            return
        for event in self._event(session, message):
            await session.said.put(event)

    async def _approve(self, session: _Session, message: _Envelope) -> None:
        """The harness asks before it acts: udl asks the person, and answers in the words the
        request's own protocol reads (v2's `accept`…, the older `approved`…)."""
        kind = _APPROVALS.get(message.method or "", "command")
        tool, wanted = _asked(kind, message.params or {}, session.paths)
        answer = await self._ask(tool, wanted, str(message.id if message.id is not None else ""))
        if not answer.allowed:
            decision = _OLD_DECLINE if kind == "legacy" else _DECLINE
        elif answer.for_chat:
            decision = _OLD_ANSWER_FOR_CHAT if kind == "legacy" else _ANSWER_FOR_CHAT
        else:
            decision = _OLD_ANSWER if kind == "legacy" else _ANSWER
        await session.interaction.write(
            json.dumps({"jsonrpc": "2.0", "id": message.id, "result": {"decision": decision}})
        )

    def _event(self, session: _Session, message: _Envelope) -> list[AgentEvent]:
        """What one notification is, as the events a chat's transcript keeps (27.1's shapes)."""
        given = message.params or {}
        method = message.method or ""
        if method == "item/agentMessage/delta":
            said = given.get("delta")
            return [TextDelta(text=str(said))] if isinstance(said, str) and said else []
        if method == "thread/tokenUsage/updated":
            return [_usage(given)]
        if method == "item/started":
            item = given.get("item")
            if isinstance(item, dict):
                _keep(session, item)
                return [_started(item)]
            return []
        if method == "item/completed":
            item = given.get("item")
            return [_finished(item)] if isinstance(item, dict) else []
        if method == "turn/completed":
            turn = given.get("turn")
            said = turn if isinstance(turn, dict) else {}
            return [TurnEnded(reason=_reason(said), text=_why(_error(said)))]
        if method == "error":
            # (A recoverable error is Codex's to carry on from, so it is said and the turn goes
            # on; one it will not retry ends the turn.)
            error = given.get("error")
            why = _why(error if isinstance(error, dict) else {})
            if given.get("willRetry") is True:
                # (Codex is carrying on: nothing is said, or the chat would read a failure it
                # recovered from — and `_ended` gives a failure priority over the turn's end.)
                log.info("codex will retry: %s", why)
                return []
            return [RunnerFailed(message=why), TurnEnded(reason=TurnReason.ERROR, text=why)]
        return []

    async def available(self) -> bool:
        """Whether this Codex can relay its approvals (`app-server` is there)."""
        try:
            done = await self._shell.run([*self._cli, "app-server", "--help"])
        except PluginFailed, ToolUnavailable, TimeoutError:
            return False
        return done.returncode == 0


def _mcp(spec: SessionSpec) -> JsonObject:
    """udl's MCP server, as Codex's config has it: its token in the environment, never in the
    argv (27.11's rule, and 25.12's)."""
    if spec.mcp is None:
        return {}
    return {
        "mcp_servers": {
            "udl": {
                "url": spec.mcp.url,
                "bearer_token_env_var": "UDL_CHAT_TOKEN",
            }
        }
    }


def _asked(kind: str, given: JsonObject, paths: dict[str, str]) -> tuple[str, JsonObject]:
    """The tool a request is about, and what it wants to use it on, in udl's words (`Bash`,
    `Edit`, …) — the same names a Claude Code chat's prompts carry.

    The shapes differ by version: v2's command approval carries the command as a string (and its
    `itemId` names the item), the older one as an array; a file change carries `changes` (v2) or
    `fileChanges` (the patch approval), and `grantRoot` is `[UNSTABLE]` and usually null."""
    if kind == "file" or (kind == "legacy" and given.get("fileChanges") is not None):
        changes = given.get("changes") or given.get("fileChanges")
        named = (
            [str(one.get("path", "")) for one in changes if isinstance(one, dict)]
            if isinstance(changes, list)
            else list(changes)
            if isinstance(changes, dict)
            else []
        )
        # (A v2 file approval has no paths of its own: the item it names does.)
        item = str(given.get("itemId", ""))
        kept = paths.get(item, "")
        said = ", ".join([*named, kept]) if kept else ", ".join(named)
        if not said:
            said = str(given.get("reason") or "")
        return "Edit", {"file_path": said}
    command = given.get("command")
    if isinstance(command, str):
        return "Bash", {"command": command}
    if isinstance(command, list):
        return "Bash", {"command": " ".join(str(part) for part in command)}
    return "Bash", {}


def _keep(session: _Session, item: JsonObject) -> None:
    """What a `fileChange` item is changing, kept by its id: a v2 file approval names the item,
    and its paths are here."""
    if item.get("type") != "fileChange":
        return
    changes = item.get("changes")
    paths = (
        [str(one.get("path", "")) for one in changes if isinstance(one, dict)]
        if isinstance(changes, list)
        else []
    )
    if paths:
        session.paths[str(item.get("id", ""))] = ", ".join(paths)


def _started(item: JsonObject) -> AgentEvent:
    """A command or a change beginning: the tool udl's transcript shows (the schema's shapes:
    `command` is a string, a file change has `changes`)."""
    kind = str(item.get("type", ""))
    if kind == "commandExecution":
        return ToolStarted(name="Bash", summary=str(item.get("command", "")))
    if kind == "fileChange":
        changes = item.get("changes")
        first = changes[0] if isinstance(changes, list) and changes else None
        return ToolStarted(
            name="Edit", summary=str(first.get("path", "")) if isinstance(first, dict) else ""
        )
    if kind == "mcpToolCall":
        return ToolStarted(name=str(item.get("tool", "")), summary="")
    return ToolStarted(name=kind or "tool", summary="")


def _finished(item: JsonObject) -> AgentEvent:
    """A tool's end: whether it worked, as the item says (`status`, and a command's
    `exitCode`)."""
    kind = str(item.get("type", ""))
    name = {"commandExecution": "Bash", "fileChange": "Edit"}.get(kind, kind or "tool")
    status = str(item.get("status", ""))
    exit_code = item.get("exitCode")
    ok = status in ("completed", "success", "") and (exit_code in (None, 0))
    return ToolFinished(name=name, ok=ok)


def _usage(given: JsonObject) -> AgentEvent:
    """What the turn cost, in the tokens udl counts: `tokenUsage` is `{last, total}`, and the
    turn's own is `last` (the chat's total is udl's to add up)."""
    used = given.get("tokenUsage")
    counts = used.get("last") if isinstance(used, dict) else None
    if not isinstance(counts, dict):
        counts = given
    return Usage(
        input_tokens=_number(counts, "inputTokens"),
        output_tokens=_number(counts, "outputTokens"),
    )


def _number(given: JsonObject, name: str) -> int:
    value = given.get(name)
    return value if isinstance(value, int) else 0


def _reason(turn: JsonObject) -> TurnReason:
    """Why a turn ended, as udl's own words: `Turn.status` says `completed`, `interrupted`,
    `failed` or `inProgress`."""
    status = str(turn.get("status", ""))
    if status == "failed":
        return TurnReason.ERROR
    if status == "interrupted":
        return TurnReason.STOPPED
    return TurnReason.DONE


def _error(turn: JsonObject) -> JsonObject:
    """A turn's `error` (`TurnError | null`), as the object it is."""
    error = turn.get("error")
    return error if isinstance(error, dict) else {}


def _why(error: JsonObject) -> str:
    """What a `TurnError` says (`message`, not the object's own `str()`)."""
    said = error.get("message")
    return str(said) if said else ""
