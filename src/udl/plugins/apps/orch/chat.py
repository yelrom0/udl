"""
------------
- chat.py -
------------

A person's chat with their own harness (27.11): the shapes its operations take and answer, and
the turn it runs.

A chat is not an agent's session: it is the person's own harness, in their folder, with the
options they chose — and only they (or the token udl issues for that one chat) read it. What its
harness does is its harness's own work, so udl never asks it to call udl the way an agent does;
it asks udl one thing, before it acts: may I use this tool (27.11's permission prompt).
"""

# System Imports
import asyncio
import contextlib
import logging
import re
import secrets
from collections.abc import Mapping, Sequence
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal
from uuid import UUID, uuid4

# Package Imports
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
)

# Local Imports
from udl.clock import Clock
from udl.db.models import JsonObject
from udl.db.records import Name
from udl.errors import DoesNotExist, NotAllowed, PluginError, UDLError
from udl.interfaces.security import CHAT_PREFIX
from udl.plugins.apps.base import AppEvents, AppRecords
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch.agents import AnAgentEvent, Said, SessionState, an_event
from udl.plugins.apps.orch.codex_chat import Ask, CodexChat, CodexQuestion
from udl.plugins.apps.orch.models import ChatPermissionState, HostName, Text, Title
from udl.plugins.runners.base import (
    McpEndpoint,
    RunnerFailed,
    SessionRef,
    SessionSpec,
    TextDelta,
    TurnEnded,
    TurnReason,
    Usage,
)
from udl.plugins.runners.manage import Harnesses
from udl.plugins.secrets.base import SecretRef

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)
"""Where a live event that cannot be published says so (it never breaks a turn)."""

LOCAL: Final = "local"
"""This machine, as the hosts name it."""

_POLL: Final = 0.25
"""How often a waiting permission looks for its answer (a person answers in a moment, or not)."""


class _Model(BaseModel):
    """The chat shapes' base: frozen, no unknown fields, and its docstrings are the fields'."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


__all__ = [
    "AskPermissionRequest",
    "ChatDecision",
    "ChatInfo",
    "ChatList",
    "ChatPermissionInfo",
    "ChatPermissionState",
    "DeleteChatRequest",
    "GetChatRequest",
    "ListChatsRequest",
    "PermissionAnswer",
    "PermissionRequest",
    "SendChatRequest",
    "StartChatRequest",
    "StopChatRequest",
]


class ChatPermissionInfo(_Model):
    """A permission a chat's harness asked for, as an operation answers it."""

    id: UUID
    """Its id (what `orch_chat_permission` takes)."""
    tool: str
    """The tool it wants to use (`Bash`, `Edit`, …)."""
    input: JsonObject = Field(default_factory=dict)
    """What it wants to use it on, as the harness asked."""
    state: ChatPermissionState = ChatPermissionState.PENDING
    """Where it is: waiting for the person, allowed, or denied."""
    message: str = ""
    """Why it was denied, when the person said why."""
    allowed_for_chat: bool = False
    """Whether the person allowed this tool for the rest of the chat."""
    asked_at: AwareDatetime
    """When it was asked."""


class ChatInfo(_Model):
    """A chat as an operation answers it: whose it is, where it got to, what it has cost, what it
    last said, and what its harness is waiting on."""

    id: UUID
    """Its id (what every other chat operation takes)."""
    owner: str
    """Whose it is (`human:<name>`)."""
    runner: Name
    """The harness it is in."""
    model: str = ""
    """The model it was asked for (empty: the harness's own default)."""
    permission_mode: str = "manual"
    """The harness's permission mode."""
    host: HostName = "local"
    """The host its harness runs on."""
    directory: str = ""
    """The folder it works in."""
    state: SessionState
    """Where it is: starting, running, idle, failed or ended."""
    title: Title = "A chat"
    """What it is about, in a line."""
    turns: NonNegativeInt = 0
    """How many turns it has had."""
    tokens_in: NonNegativeInt = 0
    """The tokens it was given, over its turns."""
    tokens_out: NonNegativeInt = 0
    """The tokens it wrote, over its turns."""
    cost: Decimal | None = None
    """What its turns cost, when its harness says."""
    last_error: str = ""
    """Why its last turn failed, as its harness said."""
    last_text: str = ""
    """What its harness last said."""
    pending: NonNegativeInt = 0
    """How many things the person said are queued for the next turns."""
    created_at: AwareDatetime
    """When it was started."""
    updated_at: AwareDatetime
    """When it was last spoken in."""
    permission: ChatPermissionInfo | None = None
    """What its harness is waiting on, if it is waiting on one."""
    events: list[AnAgentEvent] = Field(default_factory=list[AnAgentEvent])
    """Its transcript (paged: the newest page of it, oldest first)."""
    total: NonNegativeInt = 0
    """How many events its transcript has."""


class ChatList(_Model):
    """Every chat of a person, each as `ChatInfo` answers one (without its transcript)."""

    items: list[ChatInfo] = Field(default_factory=list[ChatInfo])
    """The chats, the most recently spoken in first."""


class StartChatRequest(_Model):
    """Starting a chat with the caller's own harness (27.11): their runner, their folder, their
    model — and `manual` permissions, so the harness may ask before it acts."""

    runner: Name
    """The harness (`claude-code`)."""
    directory: Text = ""
    """The folder it works in (empty: the folder udl runs in)."""
    model: str = ""
    """The model (empty: the harness's own default)."""
    host: HostName = "local"
    """The host it runs on."""
    text: Text = ""
    """What to say first (empty: the chat waits for its first message)."""
    title: Title = "A chat"
    """What it is about, in a line (the default: what the person says first)."""


class SendChatRequest(_Model):
    """Saying something to a chat, which is one turn of its harness."""

    chat: UUID
    """The chat."""
    text: Text
    """What to say (the turn's prompt)."""
    wait: bool = True
    """Whether to wait for the turn to end (a client that streams sets this False and reads the
    chat's events)."""


class GetChatRequest(_Model):
    """One chat, with its transcript (a page of it) and what it is waiting on."""

    chat: UUID
    """The chat."""
    limit: PositiveInt = 200
    """How many of its latest events to answer (its newest page)."""


class ListChatsRequest(_Model):
    """The caller's chats."""

    all: bool = False
    """Whether to answer every chat (only udl's owner may: a chat's token sees its own)."""


class StopChatRequest(_Model):
    """Ending a chat: its harness's session ends, and nothing it was doing goes on."""

    chat: UUID
    """The chat."""


class DeleteChatRequest(_Model):
    """Forgetting a chat, with its transcript, what its harness asked and the token it was
    given."""

    chat: UUID
    """The chat."""


class AskPermissionRequest(_Model):
    """What a harness asks udl, through udl's MCP server, before it acts in a chat (27.11): the
    tool it wants and what it wants to use it on.

    This is Claude Code's own permission-prompt protocol, not udl's: it calls the tool with
    `tool_name`, `input` and `tool_use_id`, and reads back `behavior` with `updatedInput` or
    `message`. The chat is never in the request — it is the one the calling token is for."""

    tool_name: str
    """The tool it wants to use (`Bash`, `Edit`, …)."""
    input: JsonObject = Field(default_factory=dict)
    """What it wants to use it on, as the harness asked."""
    tool_use_id: str = ""
    """Which of its tool uses this is (the harness's own id: udl keeps it only as it came)."""


class ChatDecision(StrEnum):
    """What a person decided about what a chat's harness asked."""

    ALLOW_ONCE = "allow_once"
    """It may act this once."""
    ALLOW_CHAT = "allow_chat"
    """It may act, and the same request (that tool, that exact input) is allowed for the rest of
    the chat without asking again — unless it is a shell command that chains or nests others, or
    runs a shell, which is allowed this once only."""
    DENY = "deny"
    """It may not act, and is told why."""


class PermissionRequest(_Model):
    """The person's answer to what a chat's harness asked (27.11's `orch_chat_permission`)."""

    chat: UUID
    """The chat."""
    request: UUID
    """What was asked (its id)."""
    decision: ChatDecision = ChatDecision.DENY
    """What the person decided."""
    message: str = ""
    """Why, when it is denied (the harness is told)."""


class PermissionAnswer(_Model):
    """What the harness is told, in its own protocol: whether it may act, on what, and why not.

    `behavior` is the word Claude Code reads (`allow` or `deny`), and `updatedInput` is what it
    then runs: the input it asked with, unchanged."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        use_attribute_docstrings=True,
        serialize_by_alias=True,
        populate_by_name=True,
    )

    behavior: Literal["allow", "deny"]
    """Whether it may act."""
    updated_input: JsonObject | None = Field(default=None, alias="updatedInput")
    """What it may run (the input it asked with: udl never changes it)."""
    message: str = ""
    """Why not, when it may not."""


# -- the chat's turn ---------------------------------------------------------------------------
#
# A chat's turn is its harness's own: udl starts it once, sends it what the person says, streams
# what it says into the chat's transcript (the same `SessionEvent`s an agent's session keeps) and
# publishes it, so a client that is watching reads the answer as it comes. The one thing udl
# answers while a turn runs is the harness's permission prompt: udl's MCP server's
# `orch_chat_ask_permission` writes what it asked, the person answers, and the harness is told.

CHAT_SECRET: Final = "apps/orch/chats/{}/token"  # noqa: S105
"""Where a chat's own token is kept (its MCP server's, and the one a client of that chat uses)."""

PERMISSION_TOOL: Final = "mcp__udl__orch_chat_ask_permission"
"""What a chat's harness is told to ask before it acts (27.11: `--permission-prompt-tool`)."""


def chat_secret_ref(chat: UUID) -> SecretRef:
    """Where `chat`'s token is kept."""
    return SecretRef(CHAT_SECRET.format(chat))


class Chats:
    """A person's chats with their own harnesses (27.11): starting one, sending it a turn,
    stopping it, and answering what its harness asks.

    The chat is the person's: their harness, their folder, their model, and `manual` permissions
    so it asks before it acts. Its token reaches that one chat and nothing else."""

    def __init__(
        self,
        *,
        service: UDLService,
        records: AppRecords,
        events: AppEvents,
        harnesses: Harnesses,
        clock: Clock,
        permission_minutes: int = 10,
        memory: int | None = None,
        idle: timedelta = timedelta(hours=24),
    ) -> None:
        self._service = service
        self._records = records
        self._events = events
        self._harnesses = harnesses
        self._clock = clock
        self._permission_minutes = permission_minutes
        self._memory = memory
        """What a chat's turn may use (27.12): a chat is a harness running a person's work, and a
        runaway in it is that turn's failure, not the host's."""
        self._idle = idle
        """How long a chat's Codex server may live untouched (`[apps.orch] chat_idle`): the same
        setting the chat's token keeps working by, so the two cannot drift apart."""
        self._codex: dict[UUID, CodexChat] = {}
        """The `app-server` driver of each Codex chat (27.11's Codex half): its harness is a
        process of the chat's own, not a runner's turn."""
        self._turns: dict[UUID, asyncio.Task[None]] = {}
        """The turn each chat is in, when one is (so a stop can end it, and a second turn is
        refused)."""

    # -- starting ------------------------------------------------------------------------------

    async def start(self, request: StartChatRequest, *, owner: str) -> ChatInfo:
        """Start a chat: the person's harness, in their folder, with a token of its own (and
        `manual` permissions, so it asks udl before it acts)."""
        runner = await self._harnesses.runner(request.host, request.runner)
        directory = request.directory or await self._folder(request.host)
        chat = await self._records.save(
            stored.Chat(
                owner=owner,
                runner=request.runner,
                model=request.model,
                permission_mode="manual",  # (a label: udl passes no mode to the harness)
                host=request.host,
                directory=directory,
                state=SessionState.STARTING,
                title=request.title or "A chat",
            )
        )
        token = await self._issue(chat)
        if request.runner == "codex":
            # (A Codex that cannot relay its approvals is refused before anything is started:
            # a chat that cannot ask is a chat that acts unwatched, and a refusal is the
            # caller's to see, not a chat that failed.)
            try:
                ref = await self._codex_start(chat, directory, token)
            except NotAllowed:
                raise
            except Exception as error:  # noqa: BLE001 - (a harness that cannot start is reported)
                chat = await self._records.save(
                    chat.model_copy(update={"state": SessionState.FAILED, "last_error": str(error)})
                )
                return await self.info(chat)
            return await self._started(chat, ref, request.text)
        try:
            ref = await runner.start(
                SessionSpec(
                    cwd=directory,
                    model=request.model or None,
                    # (27.11: a chat runs as the person would — their own settings
                    # decide — and the one thing udl adds is its MCP server, whose
                    # permission prompt is udl's.)
                    own_settings=True,
                    permission_tool=PERMISSION_TOOL,
                    memory=self._memory,
                    unit="udl-session-chat",  # (its turn is named for the chat, not an agent)
                    mcp=McpEndpoint(url=self._mcp_url(), token=token),
                )
            )
        except Exception as error:  # noqa: BLE001 - (a harness that cannot start is reported)
            # (A chat that dies before its harness starts says so, and is over: nothing else is
            # going to happen in it.)
            chat = await self._records.save(
                chat.model_copy(update={"state": SessionState.FAILED, "last_error": str(error)})
            )
            return await self.info(chat)
        chat = await self._records.save(
            chat.model_copy(update={"ref": ref, "state": SessionState.IDLE})
        )
        await self._live(chat, "orch.chat.started", {"chat": str(chat.id), "runner": chat.runner})
        if request.text.strip():
            return await self.send(chat, request.text)
        return await self.info(chat)

    async def _codex_driver(self, chat: stored.Chat, token: SecretStr) -> CodexChat:
        """The `app-server` driver of `chat`: `codex app-server`, which relays its approvals
        (27.11). A Codex that cannot is refused rather than run without approvals — a chat that
        cannot ask is a chat that acts on the person's machine unwatched (O-C15)."""
        runner = await self._harnesses.runner(chat.host, "codex")
        driver = CodexChat(
            cli=runner.options.cli,  # (a Codex's own options: its `cli` is the program)
            shell=runner.shell,
            clock=self._clock,
            ask=self._codex_ask(chat),
            within=self._idle,
        )
        if not await driver.available():
            raise NotAllowed(
                "this Codex can't relay its approvals; update it",
                hint="A chat's harness asks before it acts: `codex app-server` is what asks.",
            )
        return driver

    async def _started(self, chat: stored.Chat, ref: SessionRef, text: str) -> ChatInfo:
        """What a chat is once its harness has started: saved, said, and its first turn sent."""
        chat = await self._records.save(
            chat.model_copy(update={"ref": ref, "state": SessionState.IDLE})
        )
        await self._live(chat, "orch.chat.started", {"chat": str(chat.id), "runner": chat.runner})
        if text.strip():
            return await self.send(chat, text)
        return await self.info(chat)

    async def _codex_start(self, chat: stored.Chat, directory: str, token: SecretStr) -> SessionRef:
        """Start `chat`'s Codex harness, and keep its driver for its turns."""
        driver = await self._codex_driver(chat, token)
        ref = await driver.start(
            SessionSpec(
                cwd=directory,
                model=chat.model or None,
                own_settings=True,
                memory=self._memory,
                unit="udl-session-chat",
                mcp=McpEndpoint(url=self._mcp_url(), token=token),
                secret_env={"UDL_CHAT_TOKEN": token},
            )
        )
        self._codex[chat.id] = driver
        return ref

    def _codex_ask(self, chat: stored.Chat) -> Ask:
        """What the driver asks when its harness does: the person's answer, and whether it was
        given for the rest of the chat."""

        async def asked(tool: str, given: JsonObject, call: str) -> CodexQuestion:
            answer, for_chat = await self.ask_and_remember(
                chat, AskPermissionRequest(tool_name=tool, input=given, tool_use_id=call)
            )
            return CodexQuestion(
                allowed=answer.behavior == "allow", for_chat=for_chat, message=answer.message
            )

        return asked

    async def _folder(self, host: str) -> str:
        """Where a chat with no folder of its own works: the folder udl runs in, on this machine
        (another host must name its own: udl does not guess where a person works)."""
        await self._service.hosts.get(host)  # (DoesNotExist for a host there is not)
        if host == LOCAL:
            return str(Path.cwd())
        raise UDLError(
            f"A chat on {host} needs its folder",
            hint="Say where it works: the folder is the person's own.",
        )

    async def _issue(self, chat: stored.Chat) -> SecretStr:
        """Give `chat` a token of its own (in udl's secret store, and its hash in the records),
        so its harness calls udl as that chat — and nothing else."""
        token = f"{CHAT_PREFIX}{secrets.token_urlsafe(32)}"
        await self._service.secrets.set(chat_secret_ref(chat.id), SecretStr(token))
        await stored.ChatTokenRecords(self._records).issue(chat, token)
        return SecretStr(token)

    def _mcp_url(self) -> str:
        """Where a chat's harness finds udl's MCP server (the loopback, for a local host)."""
        api = self._service.settings.api
        host = "127.0.0.1" if api.host in ("0.0.0.0", "::") else api.host  # noqa: S104
        address = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"http://{address}:{api.port}/mcp"

    # -- a turn --------------------------------------------------------------------------------

    async def send(self, chat: stored.Chat, text: str) -> ChatInfo:
        """Say something to `chat`: one turn of its harness, streamed into its transcript.

        A chat has one turn at a time: a second is refused while the first runs (`wait=False`
        answers as soon as the turn has started, so a client can read the chat's events)."""
        current = await self._saved(chat)
        if current is None:
            raise DoesNotExist(f"There is no chat {chat.id}", hint="It may have been deleted.")
        if current.state in (SessionState.ENDED, SessionState.FAILED):
            raise UDLError(
                "That chat is over",
                hint="Start a new one: a chat that ended does not take turns.",
            )
        if current.id in self._turns:
            # (27.11: a chat takes one turn at a time, so what the person says during one is
            # queued, and is the next turn.)
            queued = await self._records.save(
                current.model_copy(update={"pending": [*current.pending, text]})
            )
            return await self.info(queued)
        return await self._begin(current, text)

    async def _begin(self, chat: stored.Chat, text: str) -> ChatInfo:
        """Start a turn of `chat` with `text` (the caller has checked one is not running)."""
        # (29.12: the person's own words are the turn's first line, so a page that draws the
        # chat draws both sides of it — and a reload shows the question as well as the answer.)
        await self._records.save(
            stored.SessionEvent(
                agent=chat.owner, session=chat.id, turn=chat.turns + 1, event=Said(text=text)
            )
        )
        running = await self._records.save(
            chat.model_copy(
                update={
                    "state": SessionState.RUNNING,
                    "title": (_title(text) if chat.title == "A chat" else chat.title),
                }
            )
        )
        task = asyncio.create_task(self._turn(running, text))
        self._turns[running.id] = task
        task.add_done_callback(
            # (Only the turn that is there is taken out: the next turn may already have begun.)
            lambda done: (
                self._turns.pop(running.id, None) if self._turns.get(running.id) is done else None
            )
        )
        return await self.info(running)

    async def _turn(self, chat: stored.Chat, text: str) -> None:
        """One turn of `chat`'s harness, streamed: its events into the transcript and the live
        events, and what its end says into the chat."""
        if chat.ref is None:  # (a chat with a turn is a chat whose harness started)
            return
        runner = await self._harnesses.runner(chat.host, chat.runner)
        driver = self._codex.get(chat.id)
        if chat.runner == "codex" and driver is None:
            # (A Codex chat whose server is gone is not handed to the `codex exec` runner: that
            # one has no approvals at all, which is what the refusal in start() prevents.)
            await self._ended(
                chat,
                "",
                ended=TurnEnded(
                    reason=TurnReason.ERROR, text="Its harness is gone: start the chat again."
                ),
                failed=None,
            )
            return
        said: list[str] = []
        ended: TurnEnded | None = None
        failed: RunnerFailed | None = None
        try:
            stream = (
                driver.send(chat.ref, text) if driver is not None else runner.send(chat.ref, text)
            )
            async for event in stream:
                spoken = an_event(event)
                await self._said(chat, spoken)
                if isinstance(spoken, TextDelta):
                    said.append(spoken.text)
                if isinstance(spoken, TurnEnded):
                    ended = spoken
                    break
                if isinstance(spoken, RunnerFailed):
                    failed = spoken
        except asyncio.CancelledError:
            # (A person stopped it: the chat is ended, and nothing it was about to write counts.)
            raise
        except Exception as error:  # noqa: BLE001 - (a harness that fails is reported, not raised)
            failed = RunnerFailed(message=str(error))
            ended = TurnEnded(reason=TurnReason.ERROR, text=str(error))
        finally:
            await self._ended(chat, "".join(said), ended=ended, failed=failed)

    async def _said(self, chat: stored.Chat, event: AnAgentEvent) -> None:
        """One thing the harness said: its transcript record and its live event, and what it
        cost."""
        record = await self._records.save(
            stored.SessionEvent(agent=chat.owner, session=chat.id, turn=chat.turns + 1, event=event)
        )
        await self._live(
            chat,
            "orch.chat.event",
            {
                "chat": str(chat.id),
                "owner": chat.owner,
                "turn": chat.turns + 1,
                "kind": event.kind,
            },
            dedupe=str(record.id),
        )
        if isinstance(event, Usage):
            current = await self._saved(chat)
            if current is None:
                return
            await self._records.save(
                current.model_copy(
                    update={
                        "tokens_in": current.tokens_in + event.input_tokens,
                        "tokens_out": current.tokens_out + event.output_tokens,
                        "cost": event.cost if event.cost is not None else current.cost,
                    }
                )
            )

    async def _ended(
        self,
        chat: stored.Chat,
        answer: str,
        *,
        ended: TurnEnded | None,
        failed: RunnerFailed | None,
    ) -> None:
        """What a turn's end means for the chat: it answered (idle, with what it said), or it
        failed (and says why) — and a chat a person ended meanwhile stays ended."""
        current = await self._saved(chat)
        if current is None or current.state is SessionState.ENDED:
            return
        if ended is not None and ended.reason is TurnReason.STOPPED:
            # (The person did not stop the chat, so its harness's session is gone: the chat is
            # over, and says why rather than pretending it answered.)
            await self._records.save(
                current.model_copy(
                    update={
                        "state": SessionState.FAILED,
                        "last_error": ended.text or "its session ended",
                    }
                )
            )
            await self._live(
                chat,
                "orch.chat.failed",
                {"chat": str(chat.id), "why": ended.text or "its session ended"},
            )
            return
        if failed is not None or (ended is not None and ended.reason is TurnReason.ERROR):
            why = (
                failed.message if failed is not None else (ended.text if ended is not None else "")
            )
            await self._records.save(
                current.model_copy(update={"state": SessionState.FAILED, "last_error": why})
            )
            await self._live(chat, "orch.chat.failed", {"chat": str(chat.id), "why": why})
            return
        text = answer or (ended.text if ended is not None else "")
        if current.pending:
            nxt, *rest = current.pending
            queued = await self._records.save(
                current.model_copy(update={"pending": rest, "turns": current.turns + 1})
            )
            self._turns.pop(current.id, None)  # (this turn is over: the next one takes its place)
            await self._begin(queued, nxt)
            return
        await self._records.save(
            current.model_copy(
                update={
                    "state": SessionState.IDLE,
                    "turns": current.turns + 1,
                    "last_text": text or current.last_text,
                }
            )
        )
        await self._live(chat, "orch.chat.answered", {"chat": str(chat.id), "turn": chat.turns + 1})

    # -- stopping ------------------------------------------------------------------------------

    async def stop(self, chat: stored.Chat) -> ChatInfo:
        """End `chat`: its harness's session ends, and a turn it was in is ended with it."""
        current = await self._saved(chat)
        if current is None:
            raise DoesNotExist(f"There is no chat {chat.id}", hint="It may have been deleted.")
        current = await self._records.save(current.model_copy(update={"state": SessionState.ENDED}))
        # (27.11: a stopped chat's token is deleted with it: nothing it left running reaches udl.)
        await self._service.secrets.delete(chat_secret_ref(current.id))
        await stored.ChatTokenRecords(self._records).delete_for(current.id)
        task = self._turns.pop(current.id, None)
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if current.ref is not None:
            driver = self._codex.pop(current.id, None)
            if driver is not None:
                with contextlib.suppress(PluginError, UDLError):
                    await driver.stop(current.ref)
            else:
                runner = await self._harnesses.runner(current.host, current.runner)
                with contextlib.suppress(PluginError, UDLError):
                    await runner.stop(current.ref)
        await self._forget_files(current)
        await self._live(current, "orch.chat.ended", {"chat": str(current.id)})
        return await self.info(current)

    async def delete(self, chat: stored.Chat) -> None:
        """Forget `chat`: its harness is stopped, its transcript, what it asked and its token
        go."""
        current = await self._saved(chat)
        if current is None:
            return
        if current.state is not SessionState.ENDED:
            await self.stop(current)
        await self._service.secrets.delete(chat_secret_ref(current.id))
        await self._forget_files(current)
        await stored.ChatRecords(self._records).delete(current)

    async def _forget_files(self, chat: stored.Chat) -> None:
        """Take udl's own files out of the person's folder: the harness reads its token from
        `.udl/mcp.json`, and a chat that is over leaves nothing of udl's behind (27.11). A folder
        on another host is the person's own shell, and udl does not reach into it."""
        if chat.host != LOCAL or not chat.directory:
            return
        with contextlib.suppress(UDLError, PluginError):
            shell = await self._service.hosts.shell(LOCAL)
            for name in ("mcp.json", "settings.json"):
                await shell.run(["rm", "-f", f"{chat.directory}/.udl/{name}"])

    # -- permissions ---------------------------------------------------------------------------

    async def ask_and_remember(
        self, chat: stored.Chat, request: AskPermissionRequest
    ) -> tuple[PermissionAnswer, bool]:
        """`ask`, and whether the answer was for the rest of the chat (27.11's Codex half: its
        protocol has a word for that, `acceptForSession`)."""
        answer, asked = await self._ask(chat, request)
        return answer, asked.allowed_for_chat if asked is not None else False

    async def ask(self, chat: stored.Chat, request: AskPermissionRequest) -> PermissionAnswer:
        """What `chat`'s harness asks before it acts: kept as what it asked, and answered when
        the person says (a denial is told, so the harness knows why)."""
        answer, _ = await self._ask(chat, request)
        return answer

    async def _ask(
        self, chat: stored.Chat, request: AskPermissionRequest
    ) -> tuple[PermissionAnswer, stored.ChatPermission | None]:
        """What the harness asked, kept and waited on: the answer, and the record it was filed
        as (None when the chat is over, which is a denial)."""
        current = await self._saved(chat)
        if current is None:
            raise DoesNotExist(f"There is no chat {chat.id}", hint="It may have been deleted.")
        if current.state in (SessionState.ENDED, SessionState.FAILED):
            return PermissionAnswer(behavior="deny", message="That chat has ended."), None
        asked = await self._records.save(
            stored.ChatPermission(chat=current.id, tool=request.tool_name, input=request.input)
        )
        await self._live(
            current,
            "orch.chat.permission",
            {"chat": str(current.id), "permission": str(asked.id), "tool": asked.tool},
        )
        answered = await self._wait_for(asked)
        if answered is None:
            return (
                PermissionAnswer(
                    behavior="deny",
                    message=(
                        f"Nobody answered within {self._permission_minutes} minutes, so udl did "
                        "not allow it."
                    ),
                ),
                asked,
            )
        if answered.state is ChatPermissionState.ALLOWED:
            return PermissionAnswer(behavior="allow", updatedInput=request.input), answered
        return PermissionAnswer(behavior="deny", message=answered.message or "Denied."), answered

    async def _wait_for(self, asked: stored.ChatPermission) -> stored.ChatPermission | None:
        """`asked` once it is answered, or None when nobody answers within the chat's time (a
        harness is never left waiting on a person forever)."""
        deadline = self._clock.now() + timedelta(minutes=self._permission_minutes)
        while self._clock.now() < deadline:
            current = await self._records.get(stored.ChatPermission, asked.id)
            if current is None:
                return None
            if current.state is not ChatPermissionState.PENDING:
                return current
            chat = await self._records.get(stored.Chat, asked.chat)
            if chat is None or chat.state in (SessionState.ENDED, SessionState.FAILED):
                return None  # (the chat is over: nobody is going to answer for it)
            await asyncio.sleep(_POLL)
        return None

    async def answer(self, chat: stored.Chat, request: PermissionRequest) -> ChatInfo:
        """The person's answer to what `chat`'s harness asked: allow it once, allow the same
        request for the rest of the chat, or deny it and say why."""
        asked = await self._records.get(stored.ChatPermission, request.request)
        if asked is None or asked.chat != chat.id:
            raise DoesNotExist(
                f"There is no request {request.request} in that chat",
                hint="It may have been answered already.",
            )
        if asked.state is not ChatPermissionState.PENDING:
            raise UDLError(
                "That request is already answered",
                hint="Ask the harness what it is doing now.",
            )
        allow = request.decision is not ChatDecision.DENY
        remember = request.decision is ChatDecision.ALLOW_CHAT and rememberable(
            asked.tool, asked.input
        )
        await self._records.save(
            asked.model_copy(
                update={
                    "state": (ChatPermissionState.ALLOWED if allow else ChatPermissionState.DENIED),
                    "message": request.message,
                    "allowed_for_chat": remember,
                }
            )
        )
        return await self.info(chat)

    async def allowed(self, chat: stored.Chat, tool: str, given: JsonObject) -> bool:
        """Whether the person allowed this exact request (that tool, that exact input) for the
        rest of `chat` (27.11: nothing is matched by prefix, and a shell command that chains or
        nests others is never remembered)."""
        if not rememberable(tool, given):
            return False
        for asked in await stored.ChatRecords(self._records).permissions(chat.id):
            if (
                asked.tool == tool
                and asked.input == given
                and asked.allowed_for_chat
                and asked.state is ChatPermissionState.ALLOWED
            ):
                return True
        return False

    # -- reading -------------------------------------------------------------------------------

    async def info(self, chat: stored.Chat, *, limit: int = 200) -> ChatInfo:
        """`chat` as an operation answers it, with the newest page of its transcript."""
        events = await stored.SessionRecords(self._records).transcript(chat.id)
        pending = await stored.ChatRecords(self._records).pending(chat.id)
        return _info(
            chat,
            events[-limit:] if limit else [],
            total=len(events),
            permission=pending,
        )

    async def _saved(self, chat: stored.Chat) -> stored.Chat | None:
        """The chat as it is stored now (None when it is gone)."""
        return await stored.ChatRecords(self._records).by_id(chat.id)

    async def _live(
        self, chat: stored.Chat, event_type: str, data: Mapping[str, object], *, dedupe: str = ""
    ) -> None:
        """Publish one of the chat's live events (one that cannot be is logged, never fatal)."""
        try:
            await self._events.emit(
                event_type,
                {str(name): _json(value) for name, value in data.items()},
                dedupe_key=f"{event_type}:{dedupe or uuid4()}",
            )
        except (PluginError, UDLError) as error:
            log.warning("publishing %s: %s", event_type, error)


def _info(
    chat: stored.Chat,
    events: Sequence[stored.SessionEvent],
    *,
    total: int,
    permission: stored.ChatPermission | None,
) -> ChatInfo:
    """`chat` as an operation answers it."""
    return ChatInfo(
        id=chat.id,
        owner=chat.owner,
        runner=chat.runner,
        model=chat.model,
        permission_mode=chat.permission_mode,
        host=chat.host,
        directory=chat.directory,
        state=chat.state,
        title=chat.title,
        turns=chat.turns,
        tokens_in=chat.tokens_in,
        tokens_out=chat.tokens_out,
        cost=chat.cost,
        last_error=chat.last_error,
        last_text=chat.last_text,
        pending=len(chat.pending),
        created_at=chat.created_at,
        updated_at=chat.updated_at,
        permission=(
            ChatPermissionInfo(
                id=permission.id,
                tool=permission.tool,
                input=permission.input,
                state=permission.state,
                message=permission.message,
                allowed_for_chat=permission.allowed_for_chat,
                asked_at=permission.created_at,
            )
            if permission is not None
            else None
        ),
        events=[event.event for event in events],
        total=total,
    )


_SHELL_TOOLS: Final = frozenset({"Bash", "bash", "run_command", "shell"})
"""The tools whose input is a command line (the ones never remembered when it chains or nests)."""

_NEVER_REMEMBERED: Final = (
    "&&",
    "||",
    ";",
    "|",
    "&",
    "$(",
    "`",
    "<(",
    ">(",
    ">",
    "<",
    "\n",
    "\r",
)
"""What a command that chains or nests others has (27.11: Allow for this chat on one is Allow
once)."""

_SHELLS: Final = frozenset(
    {
        "sh",
        "bash",
        "zsh",
        "dash",
        "fish",
        "env",
        "xargs",
        "sudo",
        "command",
        "eval",
        "exec",
        "nohup",
        "timeout",
        "ssh",
        "nice",
        "setsid",
        "stdbuf",
        "script",
    }
)
"""What a command runs when its first word is one of these (a shell, or something that runs what
it is given): never remembered either."""


_ASSIGNED: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
"""What a shell command's leading assignments look like (`VAR=x bash …`)."""


def rememberable(tool: str, given: JsonObject) -> bool:
    """Whether `tool` with `given` may be allowed for the rest of a chat: the same tool and the
    same exact input, never a prefix — and never a shell command that chains or nests others, or
    runs a shell (27.11)."""
    if tool not in _SHELL_TOOLS:
        return True
    command = given.get("command")
    if not isinstance(command, str) or not command.strip():
        return False
    if any(part in command for part in _NEVER_REMEMBERED):
        return False
    words = command.strip().split()
    # (`VAR=x bash -c …` runs a shell: what comes before the first word may be assignments.)
    first = next(
        (word for word in words if not _ASSIGNED.match(word)),
        "",
    ).rsplit("/", 1)[-1]
    return bool(first) and first not in _SHELLS


def _json(value: object) -> JsonValue:
    """`value` as an event's data holds it (what the bus refuses is never a chat's event)."""
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def _title(text: str) -> str:
    """What a chat is about, from what the person said first: its first line, trimmed to fit."""
    first = text.strip().splitlines()[0] if text.strip() else "A chat"
    return first[:120]
