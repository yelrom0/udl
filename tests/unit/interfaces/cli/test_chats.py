"""
------------------
- test_chats.py -
------------------

`udl orch chat` (27.11): the one chat command that is not an operation. It is a conversation in a
terminal, so what is tested here is what it says and what it makes of a person's answer — the
operations it calls are the other chat commands', tested with them.
"""

# System Imports
from datetime import UTC, datetime
from uuid import UUID

# Package Imports
import pytest
import typer
from typer.testing import CliRunner

# Local Imports
from udl.interfaces.cli.commands import chats
from udl.plugins.apps.orch.agents import SessionState
from udl.plugins.apps.orch.chat import ChatDecision, ChatInfo


def a_chat(*, total: int = 0) -> ChatInfo:
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
    )


def test_it_needs_a_chat_to_join_or_one_to_start() -> None:
    app = typer.Typer()
    app.command()(chats.chat)

    result = CliRunner().invoke(app, [])

    assert result.exit_code == 2
    assert "Give --chat <id>, or --new to start one" in result.output


def test_it_will_not_take_both() -> None:
    app = typer.Typer()
    app.command()(chats.chat)

    result = CliRunner().invoke(app, ["--chat", "one", "--new"])

    assert result.exit_code == 2
    assert "not both" in result.output


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
