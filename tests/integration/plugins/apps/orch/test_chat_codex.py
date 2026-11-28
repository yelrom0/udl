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
from udl.config import APISettings, Settings
from udl.core.service import UDLService
from udl.errors import NotAllowed
from udl.interfaces.security import Principal, PrincipalKind, current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import SessionState
from udl.plugins.apps.orch.chat import ChatInfo, ChatList
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import ChatRecords
from udl.plugins.registry import APPS, RUNNERS, PluginRegistry
from udl.plugins.runners.codex import Codex

ROOT = Path(__file__).resolve().parents[5]
FAKE = ROOT / "tests" / "support" / "codex_app_server.py"


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the codex runner (pointed at the fake), and orch."""
    plugins = registry()
    plugins.register(RUNNERS, Codex)
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
def codex_settings(tmp_path: Path, cli: list[str]) -> Settings:
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
async def service(tmp_path: Path, codex_settings: Settings) -> AsyncIterator[UDLService]:
    """A service with the codex runner pointed at the fake, and a folder to chat in."""
    async with UDLService(codex_settings, registry=orch_registry()) as running:
        yield running
        await end_chats(running)


async def end_chats(service: UDLService) -> None:
    """End every chat a test left running, so no turn outlives the service it writes to."""
    found = await service.call("orch_chat_list", {"all": True})
    assert isinstance(found, ChatList)
    for chat in found.items:
        if chat.state not in (SessionState.ENDED, SessionState.FAILED):
            await service.call("orch_chat_stop", {"chat": chat.id})
    await asyncio.sleep(0.1)
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
    await asyncio.sleep(0.1)


async def test_a_codex_chat_whose_server_is_gone_is_over_not_run_without_approvals(
    tmp_path: Path, cli: list[str]
) -> None:
    """(O-C15: the `codex exec` runner has no approvals, so a Codex chat whose server is gone is
    ended rather than handed to it.)"""
    config = settings(
        tmp_path,
        api=APISettings(api_key=SecretStr("test-key")),
        integrations={"codex": {"cli": cli}},
    )
    async with UDLService(config, registry=orch_registry()) as running:
        chat = await started(running, text="hello")
        await settled(running, chat)

        app = await running.apps.get("orch")
        assert isinstance(app, Orch)
        drivers = vars(app.chats)["_codex"]
        driver = drivers.pop(chat.id)
        saved = await ChatRecords(AppRecords(running.db)).by_id(chat.id)
        assert saved is not None
        assert saved.ref is not None
        await driver.stop(saved.ref)

        again = await running.call("orch_chat_send", {"chat": chat.id, "text": "again"})
        assert isinstance(again, ChatInfo)
        found = await settled(running, again, turns=2)
        assert found.state is SessionState.FAILED
        assert "gone" in (found.last_error or "")


async def test_usage_and_a_failed_tool_reach_the_chat(tmp_path: Path, cli: list[str]) -> None:
    """(The chat's counts come from `tokenUsage.last`, and a tool's `status` says `ok=False`.)"""
    config = settings(
        tmp_path,
        api=APISettings(api_key=SecretStr("test-key")),
        integrations={"codex": {"cli": [sys.executable, str(FAKE), "tool"]}},
    )
    async with UDLService(config, registry=orch_registry()) as running:
        chat = await started(running, text="run it")

        found = await settled(running, chat)

        assert found.tokens_in == 4
        assert found.tokens_out == 2
        kinds = [event.kind for event in found.events]
        assert "tool_started" in kinds
        assert "tool_finished" in kinds
        finished = [one for one in found.events if one.kind == "tool_finished"]
        assert finished
        assert finished[-1].ok is False
        await running.call("orch_chat_stop", {"chat": chat.id})
        await asyncio.sleep(0.1)


async def test_a_codex_without_app_server_is_refused(tmp_path: Path, cli: list[str]) -> None:
    """(A Codex that cannot relay its approvals is not started: it would act unwatched.)"""
    config = settings(
        tmp_path,
        api=APISettings(api_key=SecretStr("test-key")),
        integrations={"codex": {"cli": [sys.executable, str(FAKE), "no-server"]}},
    )
    async with UDLService(config, registry=orch_registry()) as running:
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
