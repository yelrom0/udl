"""
------------
- chats.py -
------------

udl orch chat: a person's own harness, in this terminal (27.11).

The other chat commands (`udl orch chats start|send|show|stop|rm|allow`) are made from their
operations, as every plugin's are. This one is a loop over the same operations, because a
conversation is a terminal's own reading and writing: it says what the harness says as it comes,
asks you what it asks (its permission prompts), and sends what you type.
"""

# System Imports
import asyncio
import contextlib
from typing import Annotated
from uuid import UUID

# Package Imports
import typer

# Local Imports
from udl.core.service import UDLService
from udl.errors import UDLError
from udl.interfaces.cli.common import run, stdout
from udl.plugins.apps.orch.agents import AnAgentEvent
from udl.plugins.apps.orch.chat import (
    ChatDecision,
    ChatInfo,
    GetChatRequest,
    PermissionRequest,
    SendChatRequest,
    StartChatRequest,
)
from udl.plugins.runners.base import TextDelta

POLL: float = 0.5
"""How often the loop looks at the chat again, in seconds (its events, and what it asks)."""

QUIT: tuple[str, ...] = ("/quit", "/exit")
"""What ends the loop (the chat itself goes on: `udl orch chats stop` ends it)."""

HELP: str = (
    "What you type is a turn of your harness. /quit leaves the chat running; "
    "`udl orch chats stop <id>` ends it."
)
"""What the loop says when it starts."""


def chat(
    ctx: typer.Context,
    chat_id: Annotated[
        str | None, typer.Option("--chat", help="The chat to join (its id, from `chats`).")
    ] = None,
    new: Annotated[bool, typer.Option("--new", help="Start a chat, and join it.")] = False,
    runner: Annotated[str, typer.Option("--runner", help="The harness (claude-code).")] = (
        "claude-code"
    ),
    host: Annotated[str, typer.Option("--host", help="The machine it runs on.")] = "local",
    directory: Annotated[
        str, typer.Option("--directory", help="The folder it works in (udl's own by default).")
    ] = "",
    model: Annotated[
        str, typer.Option("--model", help="The model (the harness's own default).")
    ] = "",
    text: Annotated[str, typer.Option("--text", help="What to say first (with --new).")] = "",
) -> None:
    """Talk to your own harness, in this terminal: its turns, and the prompts it asks you."""
    if new == (chat_id is not None):
        raise typer.BadParameter("Give --chat <id>, or --new to start one, and not both.")

    async def talk(service: UDLService) -> None:
        await _talk(
            service,
            chat_id=chat_id,
            new=new,
            runner=runner,
            host=host,
            directory=directory,
            model=model,
            text=text,
        )

    run(ctx, talk)


async def _talk(
    service: UDLService,
    *,
    chat_id: str | None,
    new: bool,
    runner: str,
    host: str,
    directory: str,
    model: str,
    text: str,
) -> None:
    """Join the chat (or start it), and say what each side says until the person leaves."""
    if new:
        started = await service.call(
            "orch_chat_start",
            StartChatRequest(runner=runner, host=host, directory=directory, model=model, text=text),
        )
        if not isinstance(started, ChatInfo):
            raise UDLError("orch_chat_start did not answer a chat")
        found = started
        stdout().print(f"chat {found.id} ({found.runner} on {found.host})")
    elif chat_id is not None:
        found = await _read(service, chat_id, limit=1)
        stdout().print(f"chat {found.id}: {found.title}")
    else:
        raise typer.BadParameter("Give --chat <id>, or --new to start one.")
    stdout().print(HELP)

    # (The loop looks at the chat while the person types, so what the harness says — and what it
    # asks — shows as it comes, not when the next line is typed.)
    seen = found.total
    asked: UUID | None = None
    typing = asyncio.create_task(_typed())
    try:
        while True:
            found = await _read(service, str(found.id), limit=200)
            seen = shown(found, seen=seen)
            if found.permission is not None and found.permission.id != asked:
                asked = found.permission.id
                _prompted(found)
            if found.state.value in ("failed", "ended"):
                stdout().print(
                    f"\n({found.state.value}: {found.last_error or 'the harness is gone'})"
                )
                return
            done, _ = await asyncio.wait({typing}, timeout=POLL)
            if not done:
                continue
            line = typing.result()
            typing = asyncio.create_task(_typed())
            if line is None or line.strip().lower() in QUIT:
                stdout().print("Leaving the chat. It keeps running.")
                return
            if not line.strip():
                continue
            if asked is not None:
                await _answered(service, found, asked, line)
                asked = None
                continue
            await service.call(
                "orch_chat_send", SendChatRequest(chat=found.id, text=line, wait=False)
            )
    finally:
        typing.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing


def shown(found: ChatInfo, *, seen: int) -> int:
    """What the chat said after the first `seen` events, printed, and how many it has now.

    (`events` is the newest page of the transcript, so what is new is counted from its total,
    not from a place in the page: the page slides as the chat says more.)"""
    fresh = found.total - seen
    if fresh > 0:
        for event in found.events[-fresh:]:
            _said(event)
    return found.total


def _said(event: AnAgentEvent) -> None:
    """One of the chat's events, as a terminal shows it."""
    if isinstance(event, TextDelta):
        stdout().print(event.text, end="")


def _prompted(found: ChatInfo) -> None:
    """What the harness wants to do, shown as it asks (the person's next line answers it)."""
    asked = found.permission
    if asked is None:
        return
    stdout().print(f"\n[{asked.tool}] {asked.input}")
    stdout().print("allow [o]nce, for this [c]hat, or [d]eny?")


async def _answered(service: UDLService, found: ChatInfo, request: UUID, line: str) -> None:
    """The person's line, as the answer to what the harness asked."""
    decision, message = decided(line)
    await service.call(
        "orch_chat_permission",
        PermissionRequest(chat=found.id, request=request, decision=decision, message=message),
    )


def decided(typed: str) -> tuple[ChatDecision, str]:
    """What the person's line means: the decision, and why when it is denied (any line that is
    not one of the two allows denies it, and is passed on as the reason)."""
    choice = typed.strip().lower()
    if choice.startswith(("o", "a")):
        return ChatDecision.ALLOW_ONCE, ""
    if choice.startswith("c"):
        return ChatDecision.ALLOW_CHAT, ""
    return ChatDecision.DENY, typed.strip() or "Denied in the terminal."


async def _read(service: UDLService, chat_id: str, *, limit: int) -> ChatInfo:
    """The chat, as `orch_chat_get` answers it (a `UDLError` says what is wrong with the id)."""
    try:
        wanted = UUID(chat_id)
    except ValueError:
        raise UDLError(f"{chat_id!r} is not a chat's id") from None
    found = await service.call("orch_chat_get", GetChatRequest(chat=wanted, limit=limit))
    if not isinstance(found, ChatInfo):
        raise UDLError("orch_chat_get did not answer a chat")
    return found


async def _typed(prompt: str = "you> ") -> str | None:
    """A line the person typed (None: the terminal ended, as Ctrl-D does)."""
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return None
    except KeyboardInterrupt:
        return None


__all__ = ["chat"]
