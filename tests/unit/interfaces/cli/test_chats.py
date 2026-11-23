"""
------------------
- test_chats.py -
------------------

`udl orch chat` (27.11): the one chat command that is not an operation. It is a conversation in a
terminal, so what is tested here is what it says and what it makes of a person's answer — the
operations it calls are the other chat commands', tested with them.
"""

# System Imports
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

# Package Imports
import pytest
import typer
from typer.testing import CliRunner

# Local Imports
from support.engine import settings
from support.orch import orch_registry
from udl.core.service import UDLService
from udl.errors import UDLError
from udl.interfaces.cli.commands import chats
from udl.plugins.apps.orch.agents import SessionState
from udl.plugins.apps.orch.chat import (
    ChatDecision,
    ChatInfo,
    ChatPermissionInfo,
    ChatPermissionState,
    PermissionRequest,
    SendChatRequest,
)


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service to talk to (its operations are replaced in the loop's own tests)."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        yield running


def plain(text: str) -> str:
    """What the terminal shows, without its colours and with its wrapping undone (a narrow one
    breaks a message across lines, as CI's does)."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m", "", text).split())


def a_permission() -> ChatPermissionInfo:
    """What a chat's harness is waiting on."""
    return ChatPermissionInfo(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        tool="Bash",
        input={"command": "ls"},
        state=ChatPermissionState.PENDING,
        asked_at=datetime(2026, 11, 24, 9, 0, tzinfo=UTC),
    )


def a_chat(*, total: int = 0, permission: ChatPermissionInfo | None = None) -> ChatInfo:
    """A chat as `orch_chat_get` answers one, with `total` events in its transcript."""
    return ChatInfo(
        id=UUID("0b9a1f8e-6a5f-4d1f-9a52-2d0e7f1a3c48"),
        owner="human:owner",
        runner="claude-code",
        state=SessionState.IDLE,
        created_at=datetime(2026, 11, 24, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 11, 24, 9, 1, tzinfo=UTC),
        total=total,
        events=[],  # (what it said is not what these tests are about)
        permission=permission,
    )


def test_it_needs_a_chat_to_join_or_one_to_start() -> None:
    app = typer.Typer()
    app.command()(chats.chat)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 2
    assert "Give --chat <id>, or --new to start one" in plain(result.output)


def test_it_will_not_take_both() -> None:
    app = typer.Typer()
    app.command()(chats.chat)

    result = CliRunner().invoke(app, ["--chat", "one", "--new"])

    assert result.exit_code == 2
    assert "not both" in plain(result.output)


def test_only_what_is_new_is_shown() -> None:
    """(The transcript is the newest page of it, so what is new is counted from its total.)"""
    chat = a_chat(total=2)

    assert chats.shown(chat, seen=2) == 2


def test_the_first_look_shows_nothing_that_was_already_there() -> None:
    """(Joining a chat that has a history shows the history as the page it is: `seen` starts at
    what the chat had when the loop joined it.)"""
    chat = a_chat(total=5)

    assert chats.shown(chat, seen=5) == 5


@pytest.mark.parametrize(
    ("typed", "decision"),
    [
        ("o", ChatDecision.ALLOW_ONCE),
        ("allow", ChatDecision.ALLOW_ONCE),
        ("c", ChatDecision.ALLOW_CHAT),
        ("chat", ChatDecision.ALLOW_CHAT),
        ("d", ChatDecision.DENY),
        ("no, that is my repo", ChatDecision.DENY),
    ],
)
def test_a_persons_answer_is_a_decision(typed: str, decision: ChatDecision) -> None:
    found, _ = chats.decided(typed)

    assert found is decision


def test_a_denial_says_why_it_was_one() -> None:
    _, message = chats.decided("no, that is my repo")

    assert message == "no, that is my repo"


def test_a_denial_with_no_reason_says_it_was_denied() -> None:
    _, message = chats.decided("")

    assert message == "Denied in the terminal."


class _Talked:
    """A service whose chat operations are scripted: what the loop reads, and what it did."""

    def __init__(self, reads: list[ChatInfo], *, refuse: bool = False) -> None:
        self._reads = reads
        self.refuse = refuse
        self.said: list[str] = []
        self.answered: list[UUID] = []

    async def call(self, name: str, request: object, **_rest: object) -> object:
        if name == "orch_chat_get":
            assert isinstance(request, object)
            return self._reads.pop(0) if len(self._reads) > 1 else self._reads[0]
        if name == "orch_chat_send":
            assert isinstance(request, SendChatRequest)
            self.said.append(request.text)
            return self._reads[0]
        if name == "orch_chat_permission":
            assert isinstance(request, PermissionRequest)
            if self.refuse:
                raise UDLError("That request is already answered")
            self.answered.append(request.request)
            return self._reads[0]
        raise AssertionError(f"the loop called {name}")


async def talk(
    service: UDLService,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reads: list[ChatInfo],
    lines: list[str],
    refuse: bool = False,
) -> _Talked:
    """Run the loop over `reads`, with `lines` as what the person types."""
    scripted = _Talked(reads, refuse=refuse)
    monkeypatch.setattr(service, "call", scripted.call)
    typed = list(lines)

    async def says(prompt: str = "you> ") -> str | None:
        return typed.pop(0) if typed else "/quit"

    monkeypatch.setattr(chats, "_typed", says)
    await chats.talk_loop(
        service,
        chat_id=str(reads[0].id),
        new=False,
        runner="claude-code",
        host="local",
        directory="",
        model="",
        text="",
    )
    return scripted


async def test_the_loop_sends_what_is_typed_and_leaves_on_quit(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripted = await talk(service, monkeypatch, reads=[a_chat(total=0)], lines=["hello", "/quit"])

    assert scripted.said == ["hello"]
    assert scripted.answered == []


async def test_a_prompt_that_is_gone_is_a_message_and_not_a_second_answer(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(A prompt answered in the web UI while the person was typing must not be answered again:
    the next line is a message, and the loop never crashes on udl's refusal.)"""
    asked = a_chat(total=0, permission=a_permission())
    after = a_chat(total=0)

    scripted = await talk(
        service,
        monkeypatch,
        reads=[asked, asked, after, after, after],
        lines=["o", "and another thing", "/quit"],
    )

    assert scripted.answered == [a_permission().id]
    assert scripted.said == ["and another thing"]


async def test_a_prompt_is_shown_once(service: UDLService, monkeypatch: pytest.MonkeyPatch) -> None:
    """(The loop reads the chat every half second: a prompt already shown is not shown again.)"""
    asked = a_chat(total=0, permission=a_permission())

    scripted = await talk(service, monkeypatch, reads=[asked, asked, asked], lines=["d", "/quit"])

    assert scripted.answered == [a_permission().id]


async def test_a_refused_answer_goes_as_a_message_and_the_loop_goes_on(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(The window the poll leaves: the chat still shows the prompt the person has just answered
    in the web UI. udl refuses the second answer, it is said, and the line is a message.)"""
    asked = a_chat(total=0, permission=a_permission())

    scripted = await talk(
        service,
        monkeypatch,
        reads=[asked, asked, asked],
        lines=["hello", "/quit"],
        refuse=True,
    )

    assert scripted.answered == []
    assert scripted.said == ["hello"]
