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

The methods are the installed Codex's own: `codex app-server generate-json-schema --out <dir>`
writes them, and `tests/fixtures/codex-app-server/` is that output, so a change in the protocol
is a change there. The ones this uses:

- `initialize`, then `thread/start` (with `cwd`, `model` and `approvalPolicy: on-request`);
- `turn/start` for each turn, with the person's words as its input;
- `item/agentMessage/delta`, `item/started`, `item/completed` and `turn/completed` as the turn's
  events, and `thread/tokenUsage/updated` for what it cost;
- `item/commandExecution/requestApproval` and `item/fileChange/requestApproval` (and the older
  `execCommandApproval`) as the approvals, answered `accept`, `acceptForSession` or `decline`.

udl never passes Codex's bypass flag (`--dangerously-bypass-approvals-and-sandbox`): a chat that
cannot ask is not started at all (`Chats.start` refuses it).
"""

# System Imports
import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Final
from uuid import uuid4

# Package Imports
from pydantic import TypeAdapter, ValidationError

# Local Imports
from udl.clock import Clock
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

__all__ = ["CodexChat", "CodexQuestion"]

ANSWER: Final = "accept"
"""Let it act, this once."""
ANSWER_FOR_CHAT: Final = "acceptForSession"
"""Let it act, and the same request for the rest of the chat."""
DECLINE: Final = "decline"
"""Do not let it act."""

_APPROVALS: Final = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "execCommandApproval",
    "applyPatchApproval",
}
"""The requests that are a harness asking before it acts (the v2 names, and the older two)."""

_MESSAGE: Final = TypeAdapter[dict[str, Any]](dict[str, Any])
"""One line the server said, as a JSON object (anything else is not the protocol)."""
_OBJECT: Final = TypeAdapter[JsonObject](JsonObject)
_WAIT: Final = 0.05
"""How long a reader waits for output before looking again (a person's turn is not a busy loop)."""


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
    waiting: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=lambda: {})
    reader: asyncio.Task[None] | None = None
    buffer: str = ""


class CodexChat:
    """A chat's Codex harness: one `codex app-server` per chat, spoken to in JSON-RPC."""

    def __init__(
        self,
        *,
        cli: Sequence[str],
        shell: HostShell,
        clock: Clock,
        ask: Ask,
        within: timedelta,
    ) -> None:
        self._cli = list(cli)
        self._shell = shell
        self._clock = clock
        self._ask = ask
        self._within = within
        """How long the server may live without a turn (a chat's own idle time: the process is
        the chat's, and must outlast a turn — 25.8's 30-second bug is not repeated here)."""
        self._sessions: dict[str, _Session] = {}

    # -- starting ------------------------------------------------------------------------------

    async def start(self, spec: SessionSpec) -> SessionRef:
        """Start `app-server` in `spec.cwd` and open a thread in it."""
        interaction = await self._shell.interact(
            [*self._cli, "app-server"],
            cwd=spec.cwd,
            env=spec.env,
            secret_env={name: value.get_secret_value() for name, value in spec.secret_env.items()},
            within=self._within,
        )
        session = _Session(interaction=interaction)
        session.reader = asyncio.create_task(self._read(session))
        self._sessions[str(id(session))] = session
        try:
            await self._call(session, "initialize", {"clientInfo": {"name": "udl", "version": "1"}})
            started = await self._call(
                session,
                "thread/start",
                {
                    "cwd": spec.cwd,
                    "model": spec.model,
                    "approvalPolicy": "on-request",
                },
            )
        except Exception:
            await self._close(session)
            raise
        thread = started.get("thread")
        session.thread = str(thread.get("id", "")) if isinstance(thread, dict) else ""
        return SessionRef(
            runner="codex",
            id=session.thread or str(uuid4()),
            cwd=spec.cwd,
            host=self._shell.host.name,
        )

    async def stop(self, session: SessionRef) -> None:
        """End the chat's server (and nothing it was about to do goes on)."""
        found = self._by_ref(session)
        if found is None:
            return
        await self._close(found)

    def _by_ref(self, session: SessionRef) -> _Session | None:
        for one in self._sessions.values():
            if one.thread == session.id:
                return one
        return None

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
        found = self._by_ref(session)
        if found is None:
            yield TurnEnded(reason=TurnReason.ERROR, text="That chat's harness is gone.")
            return
        try:
            await self._call(
                found,
                "turn/start",
                {
                    "threadId": found.thread,
                    "input": [{"type": "text", "text": prompt}],
                },
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

    async def _call(self, session: _Session, method: str, params: JsonObject) -> JsonObject:
        """One request, and what the server answered (a request's answer is its `result`)."""
        asked = str(uuid4())
        waiting: asyncio.Future[JsonObject] = asyncio.get_event_loop().create_future()
        session.waiting[asked] = waiting
        await session.interaction.write(
            json.dumps({"jsonrpc": "2.0", "id": asked, "method": method, "params": params})
        )
        answered = await waiting
        if "error" in answered:
            raise PluginFailed(f"Codex refused {method}: {answered['error']}")
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
                        for waiting in session.waiting.values():
                            if not waiting.done():
                                waiting.set_result({})
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
                        message = _MESSAGE.validate_json(line)
                    except ValidationError:
                        log.warning("codex app-server said something that is not JSON: %s", line)
                        continue
                    await self._message(session, message)
        except asyncio.CancelledError:
            raise
        except Exception as failure:  # noqa: BLE001 - (a server that broke is the turn's failure)
            log.warning("codex app-server broke: %s", failure)
            await session.said.put(RunnerFailed(message=str(failure)))
            await session.said.put(None)

    async def _message(self, session: _Session, message: dict[str, Any]) -> None:
        """One message: an answer, a request, or a notification."""
        asked = message.get("id")
        method = message.get("method")
        if method is None:
            waiting = session.waiting.pop(str(asked), None) if asked is not None else None
            if waiting is not None and not waiting.done():
                answered: JsonObject | None = message.get("result")
                waiting.set_result(answered if answered is not None else {})
            return
        params = message.get("params")
        given: JsonObject = _OBJECT.validate_python(params) if params is not None else {}
        if method in _APPROVALS:
            await self._approve(session, message, given)
            return
        for event in self._event(method, given):
            await session.said.put(event)

    async def _approve(self, session: _Session, message: dict[str, Any], given: JsonObject) -> None:
        """The harness asks before it acts: udl asks the person, and answers in Codex's own
        words (`accept`, `acceptForSession` for Allow for this chat, or `decline`)."""
        tool, wanted = _asked(method=message["method"], given=given)
        answer = await self._ask(tool, wanted, str(message.get("id", "")))
        decision = (
            ANSWER if not answer.allowed else (ANSWER_FOR_CHAT if answer.for_chat else ANSWER)
        )
        if not answer.allowed:
            decision = DECLINE
        body = {"decision": decision}
        if not answer.allowed and answer.message:
            body["message"] = answer.message
        await session.interaction.write(
            json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": body})
        )

    def _event(self, method: str, given: JsonObject) -> list[AgentEvent]:
        """What one notification is, as the events a chat's transcript keeps (27.1's shapes)."""
        if method == "item/agentMessage/delta":
            said = str(given.get("delta", ""))
            return [TextDelta(text=said)] if said else []
        if method == "thread/tokenUsage/updated":
            return [_usage(given)]
        if method == "item/started":
            item = given.get("item")
            return [_started(item)] if isinstance(item, dict) else []
        if method == "item/completed":
            item = given.get("item")
            return [_finished(item)] if isinstance(item, dict) else []
        if method == "turn/completed":
            turn = given.get("turn")
            said = turn if isinstance(turn, dict) else {}
            return [TurnEnded(reason=_reason(said), text=_why(said))]
        if method in ("error", "warning"):
            why = str(given.get("message") or given.get("error") or "its harness failed")
            return [RunnerFailed(message=why), TurnEnded(reason=TurnReason.ERROR, text=why)]
        return []

    async def available(self) -> bool:
        """Whether this Codex can relay its approvals (`app-server` is there)."""
        try:
            done = await self._shell.run([*self._cli, "app-server", "--help"])
        except PluginFailed, ToolUnavailable, TimeoutError:
            return False
        return done.returncode == 0


def _asked(*, method: str, given: JsonObject) -> tuple[str, JsonObject]:
    """The tool a request is about, and what it wants to use it on, in udl's words (`Bash`,
    `Edit`, …) — the same names a Claude Code chat's prompts carry."""
    command = given.get("command")
    if method == "item/fileChange/requestApproval":
        return "Edit", {"file_path": str(given.get("grantRoot") or "")}
    if isinstance(command, list):
        return "Bash", {"command": " ".join(str(part) for part in command)}
    return "Bash", {}


def _started(item: JsonObject) -> AgentEvent:
    """A command or a change beginning: the tool udl's transcript shows."""
    kind = str(item.get("type", ""))
    if kind == "commandExecution":
        command = item.get("command")
        wanted = " ".join(str(part) for part in command) if isinstance(command, list) else ""
        return ToolStarted(name="Bash", summary=wanted)
    if kind == "fileChange":
        return ToolStarted(name="Edit", summary=str(item.get("path", "")))
    if kind == "mcpToolCall":
        return ToolStarted(name=str(item.get("tool", "")), summary="")
    return ToolStarted(name=kind or "tool", summary="")


def _finished(item: JsonObject) -> AgentEvent:
    """A tool's end: what it said (a command's output is its own, and is not repeated here)."""
    kind = str(item.get("type", ""))
    name = {"commandExecution": "Bash", "fileChange": "Edit"}.get(kind, kind or "tool")
    return ToolFinished(name=name, ok=True)


def _usage(given: JsonObject) -> AgentEvent:
    """What the turn cost, in the tokens udl counts (Codex's own names)."""
    used = given.get("tokenUsage")
    counts: JsonObject = used if isinstance(used, dict) else given
    return Usage(
        input_tokens=_number(counts, "inputTokens", "promptTokens"),
        output_tokens=_number(counts, "outputTokens", "completionTokens"),
    )


def _number(given: JsonObject, *names: str) -> int:
    for name in names:
        value = given.get(name)
        if isinstance(value, int):
            return value
    return 0


def _reason(turn: JsonObject) -> TurnReason:
    """Why a turn ended, as udl's own words: a turn that failed is an error, one stopped is
    stopped, and everything else is a turn that ended."""
    if turn.get("error") is not None:
        return TurnReason.ERROR
    return TurnReason.DONE


def _why(turn: JsonObject) -> str:
    error = turn.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error) if error else ""
