"""
-------------------
- test_codex_chat.py -
-------------------

A person's Codex chat (27.11's Codex half): `codex app-server` driven over stdio, its approvals
relayed to the person, and a Codex that cannot relay them refused.

The harness is a fake of the real protocol (`tests/support/codex_app_server.py`), a program of its
own, so what is tested is a process, its lines and the decisions it is given — not a monkeypatch.
"""

# System Imports
import sys
from datetime import timedelta
from pathlib import Path

# Package Imports
# Local Imports
from support.clock import FakeClock
from udl.db.models import JsonObject
from udl.hosts.shell import Host, LocalShell
from udl.plugins.apps.orch.codex_chat import Ask, CodexChat, CodexQuestion
from udl.plugins.runners.base import (
    AgentEvent,
    SessionRef,
    SessionSpec,
    TextDelta,
    TurnEnded,
    TurnReason,
)

# (The repo root: this file is tests/unit/plugins/apps/orch/test_codex_chat.py.)
ROOT = Path(__file__).resolve().parents[5]
FAKE = ROOT / "tests" / "support" / "codex_app_server.py"


def tmp() -> Path:
    """A folder of the test's own (a shell needs a folder that is there)."""
    import tempfile

    return Path(tempfile.mkdtemp(prefix="udl-codex-chat-"))


"""The fake app-server, run as a program (the way a test points the runner's `cli` at it)."""


def driver(ask: Ask | None = None, *, mode: str = "turn") -> CodexChat:
    """A driver whose harness is the fake, on this machine, in a folder of its own."""
    return CodexChat(
        cli=[sys.executable, str(FAKE), mode],
        shell=LocalShell(Host.local(str(tmp()))),
        clock=FakeClock(),
        ask=ask or (lambda tool, given, call: _allowed()),
        within=timedelta(minutes=5),
    )


async def _allowed() -> CodexQuestion:
    return CodexQuestion(allowed=True)


async def said(driver_: CodexChat, text: str = "go") -> list[AgentEvent]:
    """One turn, and everything it said (and the harness is ended, so no process is left)."""
    ref = await driver_.start(SessionSpec(cwd=str(ROOT)))
    try:
        return [event async for event in driver_.send(ref, text)]
    finally:
        await driver_.stop(ref)


async def test_a_turn_says_what_the_harness_said() -> None:
    events = await said(driver())

    spoken = [one for one in events if isinstance(one, TextDelta)]
    assert [one.text for one in spoken] == ["hello from codex"]
    ended = events[-1]
    assert isinstance(ended, TurnEnded)
    assert ended.reason is TurnReason.DONE


async def test_an_approval_is_asked_and_the_answer_reaches_the_harness() -> None:
    asked: list[tuple[str, JsonObject, str]] = []

    async def ask(tool: str, given: JsonObject, call: str) -> CodexQuestion:
        asked.append((tool, given, call))
        return CodexQuestion(allowed=True)

    events = await said(driver(ask, mode="approval"))

    assert asked
    assert asked[0][0] == "Bash"
    assert asked[0][1].get("command") == "git status"
    assert any(isinstance(one, TextDelta) and "accept" in one.text for one in events)


async def test_a_denied_approval_says_so_to_the_harness() -> None:
    async def ask(tool: str, given: JsonObject, call: str) -> CodexQuestion:
        return CodexQuestion(allowed=False, message="not this one")

    events = await said(driver(ask, mode="approval"))

    assert any(isinstance(one, TextDelta) and "decline" in one.text for one in events)


async def test_allow_for_the_chat_is_the_harness_s_own_word_for_it() -> None:
    async def ask(tool: str, given: JsonObject, call: str) -> CodexQuestion:
        return CodexQuestion(allowed=True, for_chat=True)

    events = await said(driver(ask, mode="approval"))

    assert any(isinstance(one, TextDelta) and "acceptForSession" in one.text for one in events)


async def test_a_codex_without_app_server_cannot_be_a_chat() -> None:
    plain = CodexChat(
        cli=[sys.executable, str(FAKE), "no-server"],
        shell=LocalShell(Host.local(str(tmp()))),
        clock=FakeClock(),
        ask=lambda tool, given, call: _allowed(),
        within=timedelta(minutes=5),
    )

    assert await plain.available() is False


async def test_a_ref_that_is_gone_says_so_rather_than_hanging() -> None:
    driver_ = driver()
    gone = SessionRef(runner="codex", id="nothing", cwd=str(ROOT), host="local")

    events = [event async for event in driver_.send(gone, "go")]

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
