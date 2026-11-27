"""
-------------------
- test_chat_codex.py -
-------------------

A person's Codex chat (27.11's Codex half), through the operations as every interface calls
them: `orch_chat_start` starts `codex app-server` (a fake of the real protocol,
`tests/support/codex_app_server.py`), its approval reaches `orch_chat_permission`, and the answer
goes back to the harness. A Codex without `app-server` is refused.
"""

# System Imports
import asyncio
import sys
from collections.abc import AsyncIterator
from contextvars import Token
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.engine import registry, settings
from udl.config import APISettings
from udl.core.service import UDLService
from udl.errors import NotAllowed
from udl.interfaces.security import Principal, PrincipalKind, current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import SessionState
from udl.plugins.apps.orch.chat import ChatInfo, ChatList
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import ChatRecords
from udl.plugins.registry import APPS, RUNNERS
from udl.plugins.runners.codex import Codex

ROOT = Path(__file__).resolve().parents[5]
FAKE = ROOT / "tests" / "support" / "codex_app_server.py"


def orch_registry(*, cli: list[str]) -> object:
    """The memory database, the secret store, the codex runner (pointed at the fake), and orch."""
    plugins = registry()
    plugins.register(RUNNERS, Codex)
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
def codex_settings(tmp_path: Path, cli: list[str]) -> object:
    """Settings whose `[integrations.codex]` runs the fake app-server."""
    return settings(
        tmp_path,
        api=APISettings(api_key=SecretStr("test-key")),
        integrations={"codex": {"cli": cli}},
    )


@pytest.fixture
def cli() -> list[str]:
    """The fake, for a turn that answers."""
    return [sys.executable, str(FAKE), "turn"]


@pytest.fixture
async def service(
    tmp_path: Path, codex_settings: object, cli: list[str]
) -> AsyncIterator[UDLService]:
    """A service with the codex runner pointed at the fake, and a folder to chat in."""
    async with UDLService(codex_settings, registry=orch_registry(cli=cli)) as running:  # type: ignore[arg-type]
        yield running
        await end_chats(running)


async def end_chats(service: UDLService) -> None:
    """End every chat a test left running, so no turn outlives the service it writes to."""
    found = await service.call("orch_chat_list", {"all": True})
    assert isinstance(found, ChatList)
    for chat in found.items:
        if chat.state not in (SessionState.ENDED, SessionState.FAILED):
            await service.call("orch_chat_stop", {"chat": chat.id})
    # (A turn's task is ended with its chat; give it a moment to let go of its process.)
    await asyncio.sleep(0.05)


def as_owner() -> Token[Principal | None]:
    """The caller is udl's owner for a while."""
    return current_principal.set(Principal(kind=PrincipalKind.OWNER, name="owner"))


async def started(service: UDLService, **given: object) -> ChatInfo:
    """Start a Codex chat in the test's folder, as the owner."""
    token = as_owner()
    try:
        found: object = await service.call(
            "orch_chat_start",
            {"runner": "codex", "directory": str(ROOT), **given},
        )
    finally:
        current_principal.reset(token)
    assert isinstance(found, ChatInfo)
    return found


async def settled(service: UDLService, chat: ChatInfo, *, turns: int = 1) -> ChatInfo:
    """Wait for the chat's turn to end, and answer it."""
    found = chat
    for _ in range(400):
        read: object = await service.call("orch_chat_get", {"chat": chat.id})
        assert isinstance(read, ChatInfo)
        found = read
        if found.turns >= turns or found.state in (SessionState.FAILED, SessionState.ENDED):
            return found
        await asyncio.sleep(0.01)
    raise AssertionError(f"the chat never settled: {found.state} ({found.last_error})")


async def test_a_codex_chat_answers_a_turn(service: UDLService) -> None:
    chat = await started(service, text="hello")

    found = await settled(service, chat)

    assert found.state is SessionState.IDLE
    assert "hello from codex" in (found.last_text or "")
    await service.call("orch_chat_stop", {"chat": chat.id})


async def test_a_codex_without_app_server_is_refused(tmp_path: Path, cli: list[str]) -> None:
    """(A Codex that cannot relay its approvals is not started: it would act unwatched.)"""
    config = settings(
        tmp_path,
        api=APISettings(api_key=SecretStr("test-key")),
        integrations={"codex": {"cli": [sys.executable, str(FAKE), "no-server"]}},
    )
    async with UDLService(config, registry=orch_registry(cli=cli)) as running:  # type: ignore[arg-type]
        token = as_owner()
        try:
            with pytest.raises(NotAllowed) as refused:
                await running.call(
                    "orch_chat_start",
                    {"runner": "codex", "directory": str(ROOT)},
                )
        finally:
            current_principal.reset(token)
    assert "approvals" in str(refused.value)


async def test_an_approval_reaches_the_person_and_the_harness_goes_on(
    tmp_path: Path, cli: list[str]
) -> None:
    """(The card's done-when: an approval is asked, answered through `orch_chat_permission`, and
    the turn goes on — on the fake, which is the protocol the real one speaks.)"""
    config = settings(
        tmp_path,
        api=APISettings(api_key=SecretStr("test-key")),
        integrations={"codex": {"cli": [sys.executable, str(FAKE), "approval"]}},
    )
    async with UDLService(config, registry=orch_registry(cli=cli)) as running:  # type: ignore[arg-type]
        chat = await started(running, text="run it")
        asked = await wait_for_a_prompt(running, chat)
        assert asked is not None

        token = as_owner()
        try:
            await running.call(
                "orch_chat_permission",
                {"chat": chat.id, "request": asked, "decision": "allow_once"},
            )
        finally:
            current_principal.reset(token)

        found = await settled(running, chat)
        assert found.state is SessionState.IDLE
        assert "accept" in (found.last_text or "")
        await running.call("orch_chat_stop", {"chat": chat.id})


async def wait_for_a_prompt(service: UDLService, chat: ChatInfo) -> object:
    """The id of the permission the chat is waiting on, or None when none came."""
    records = ChatRecords(AppRecords(service.db))
    for _ in range(400):
        pending = await records.pending(chat.id)
        if pending is not None:
            return pending.id
        await asyncio.sleep(0.01)
    return None
