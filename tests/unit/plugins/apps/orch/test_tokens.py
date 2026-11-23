"""
------------------
- test_tokens.py -
------------------

An agent's token (27.2): only its hash is kept (orch.records.AgentToken), issuing one rotates
the one an agent had, and the role table is the one place a role is compared — an implementer
its work, a manager the same (27.8 adds its powers), a reviewer every orch_* that is not admin,
and nothing else, whatever an operation's hints say.
"""

# System Imports
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.engine import databases, registry, settings
from support.interfaces import Harness
from udl.config import APISettings
from udl.errors import DoesNotExist, NotAllowed
from udl.interfaces.security import AGENT_PREFIX, Principal, PrincipalKind
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers
from udl.plugins.apps.orch.records import (
    Agent as AgentRecord,
)
from udl.plugins.apps.orch.records import (
    AgentRecords,
    AgentToken,
    AgentTokenRecords,
    Chat,
    ChatRecords,
    ChatTokenRecords,
    hash_token,
)
from udl.plugins.apps.orch.tokens import (
    ROLE_OPERATIONS,
    OrchTokens,
    allowed,
    issue_token,
    refused,
    secret_ref,
)
from udl.plugins.secrets.base import SecretRef

NOW: Final = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
"""When these tests' clock starts."""

TOKEN = "udl_agent_abcdefghijklmnop"  # noqa: S105 - (a fake's own)
"""A token as udl issues one (a fake's own)."""

CHAT_TOKEN = "udl_chat_abcdefghijklmnop"  # noqa: S105 - (a fake's own)
"""A chat's token, as 27.11 issues one (a fake's own)."""


@pytest.fixture
async def records(tmp_path: Path) -> AppRecords:
    """Orch's records over a fresh in-memory database (as tests/support/engine.py makes one)."""
    db = databases(settings(tmp_path), registry())
    await db.connect()
    return AppRecords(db)


@pytest.fixture
def agents(records: AppRecords) -> AgentRecords:
    """Orch's agents, over the same records."""
    return AgentRecords(records)


@pytest.fixture
def tokens(records: AppRecords) -> AgentTokenRecords:
    """Orch's agent tokens, over the same records."""
    return AgentTokenRecords(records)


async def an_agent(agents: AgentRecords, name: str, role: AgentRole) -> AgentRecord:
    """The agent `name`, with `role`, stored (as a record, not a shape)."""
    return await agents.save_agent(
        AgentRecord.model_validate({"name": name, "title": name, "runner": "fake", "role": role})
    )


# ---------------------------------------------------------------------------------------------
# What is kept
# ---------------------------------------------------------------------------------------------


def test_a_token_is_kept_as_its_hash() -> None:
    kept = AgentToken.example()

    assert kept.agent == "deepseek-a"
    assert kept.hash == hash_token("udl_agent_example")
    assert kept.hash != "udl_agent_example"
    assert kept.last_used_at is None
    assert AgentToken.model_validate(kept.model_dump()) == kept


async def test_issuing_a_token_keeps_its_hash_and_not_the_token(
    agents: AgentRecords, tokens: AgentTokenRecords
) -> None:
    await an_agent(agents, "fake-a", AgentRole.IMPLEMENTER)

    kept = await tokens.issue("fake-a", TOKEN)

    assert kept.agent == "fake-a"
    assert kept.hash == hash_token(TOKEN)
    assert TOKEN not in kept.model_dump_json()
    found = await tokens.by_hash(hash_token(TOKEN))
    assert found is not None
    assert found.id == kept.id


async def test_issuing_another_token_replaces_the_one_it_had(
    agents: AgentRecords, tokens: AgentTokenRecords
) -> None:
    await an_agent(agents, "fake-a", AgentRole.IMPLEMENTER)
    first = await tokens.issue("fake-a", TOKEN)

    second = await tokens.issue("fake-a", f"{TOKEN}2")

    assert [one.id for one in await tokens.for_agent("fake-a")] == [second.id]
    assert await tokens.by_hash(hash_token(TOKEN)) is None
    assert second.id != first.id


async def test_using_a_token_notes_when(tokens: AgentTokenRecords) -> None:
    kept = await tokens.issue("fake-a", TOKEN)

    touched = await tokens.touch(kept)

    assert touched.last_used_at is not None
    found = await tokens.by_hash(hash_token(TOKEN))
    assert found is not None
    assert found.last_used_at == touched.last_used_at


async def test_a_token_is_forgotten_with_its_agent(
    agents: AgentRecords, tokens: AgentTokenRecords
) -> None:
    await an_agent(agents, "fake-a", AgentRole.IMPLEMENTER)
    await tokens.issue("fake-a", TOKEN)

    assert await tokens.delete_for("fake-a") is True
    assert await tokens.for_agent("fake-a") == []
    assert await tokens.delete_for("fake-a") is False


# ---------------------------------------------------------------------------------------------
# Who a token belongs to
# ---------------------------------------------------------------------------------------------


async def test_a_token_says_which_agent_it_is(agents: AgentRecords, records: AppRecords) -> None:
    await an_agent(agents, "fake-a", AgentRole.IMPLEMENTER)
    book = OrchTokens(records)
    await book.issue("fake-a", TOKEN)

    assert await book.principal_for(TOKEN) == Principal(
        kind=PrincipalKind.AGENT, name="fake-a", role="implementer"
    )


async def test_a_token_udl_does_not_know_is_nobody(records: AppRecords) -> None:
    assert await OrchTokens(records).principal_for(TOKEN) is None


async def test_a_token_whose_agent_is_gone_is_nobody(
    agents: AgentRecords, records: AppRecords
) -> None:
    stored = await an_agent(agents, "fake-a", AgentRole.IMPLEMENTER)
    await agents.tokens.issue("fake-a", TOKEN)
    await agents.records.delete(AgentRecord, stored.id)

    assert await OrchTokens(records).principal_for(TOKEN) is None


async def test_issuing_one_through_a_service_is_kept_in_the_secret_store(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path, api=APISettings(api_key=SecretStr("s3cret")))
    async with harness.service() as service:
        agents = AgentRecords(AppRecords(service.db))
        await agents.save_agent(
            AgentRecord.model_validate({"name": "fake-a", "title": "Fake A", "runner": "fake"})
        )

        token = await issue_token(service, "fake-a")

        assert token.startswith(AGENT_PREFIX)
        kept = await service.secrets.get(_ref("fake-a"))
        assert kept is not None
        assert kept.get_secret_value() == token
        assert await agents.tokens.by_hash(hash_token(token)) is not None

        with pytest.raises(DoesNotExist, match="fake-b"):
            await issue_token(service, "fake-b")


async def test_a_token_is_not_issued_on_a_server_without_an_api_key(tmp_path: Path) -> None:
    """A token only means something on a server that needs a key: without one every request is
    the owner's, so a token would limit nothing (an agent could drop it and do everything)."""
    harness = Harness(tmp_path)  # (no api.api_key)
    async with harness.service() as service:
        agents = AgentRecords(AppRecords(service.db))
        await agents.save_agent(
            AgentRecord.model_validate({"name": "fake-a", "title": "Fake A", "runner": "fake"})
        )

        with pytest.raises(NotAllowed, match=r"api\.api_key") as asked:
            await issue_token(service, "fake-a")

        assert "every request is the owner" in (asked.value.hint or "")
        assert await agents.tokens.by_hash(hash_token("anything")) is None  # (nothing was issued)


def _ref(agent: str) -> SecretRef:
    """Where an agent's token is kept (the one orch uses)."""
    return secret_ref(agent)


# ---------------------------------------------------------------------------------------------
# What a role may call
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(ROLE_OPERATIONS[AgentRole.IMPLEMENTER]))
def test_every_operation_an_implementer_may_call_is_named_as_one(name: str) -> None:
    assert name.startswith("orch_") or name == "github_get_pull"


@pytest.mark.parametrize(
    "name",
    [
        "orch_my_task",
        "orch_get_task",
        "orch_add_note",
        "orch_push",
        "orch_ready",
        "github_get_pull",
    ],
)
def test_an_implementer_may_call_its_own_work(name: str) -> None:
    assert allowed(AgentRole.IMPLEMENTER, name, admin=False)


@pytest.mark.parametrize(
    "name",
    ["orch_set_task", "orch_move_task", "orch_delete_project", "download", "set_plugin_settings"],
)
def test_an_implementer_may_not_call_anything_else(name: str) -> None:
    assert not allowed(AgentRole.IMPLEMENTER, name, admin=False)
    assert not allowed(AgentRole.IMPLEMENTER, name, admin=True)


def test_a_manager_may_do_an_implementers_work() -> None:
    for name in ROLE_OPERATIONS[AgentRole.MANAGER]:
        assert allowed(AgentRole.MANAGER, name, admin=False)


@pytest.mark.parametrize(
    "name",
    [
        "orch_assign",
        "orch_reorder_queue",
        "orch_suggest",
        "orch_answer",
        "orch_review_task",
        "orch_set_task",
        "orch_split_task",
    ],
)
def test_a_manager_with_no_powers_may_not_call_what_a_power_switches_on(name: str) -> None:
    """(27.8: each power is off until it is given.)"""
    assert not allowed(AgentRole.MANAGER, name, admin=False)
    assert not allowed(AgentRole.MANAGER, name, admin=False, powers=ManagerPowers())


@pytest.mark.parametrize(
    ("power", "names"),
    [
        ("assign", ("orch_assign", "orch_reorder_queue", "orch_suggest")),
        ("answer", ("orch_answer",)),
        ("review", ("orch_review_task",)),
        ("plan", ("orch_set_task", "orch_split_task")),
    ],
)
def test_a_power_switches_on_its_operations_and_no_others(
    power: str, names: tuple[str, ...]
) -> None:
    given = ManagerPowers(**{power: True})
    for name in names:
        assert allowed(AgentRole.MANAGER, name, admin=False, powers=given)
    for other in {
        "orch_assign",
        "orch_reorder_queue",
        "orch_suggest",
        "orch_answer",
        "orch_review_task",
        "orch_set_task",
        "orch_split_task",
    } - set(names):
        assert not allowed(AgentRole.MANAGER, other, admin=False, powers=given)


def test_a_manager_keeps_an_implementers_work_whatever_its_powers() -> None:
    for name in ROLE_OPERATIONS[AgentRole.IMPLEMENTER]:
        assert allowed(AgentRole.MANAGER, name, admin=False, powers=ManagerPowers())


def test_a_manager_refused_a_power_operation_is_told_about_its_powers() -> None:
    who = Principal(kind=PrincipalKind.AGENT, name="fake-a", role="manager")

    with pytest.raises(NotAllowed, match="not one of the operations") as refused_it:
        refused(who, "orch_review_task", admin=False, powers=ManagerPowers())

    assert "powers" in (refused_it.value.hint or "")
    assert "27.8" in (refused_it.value.hint or "")


def test_a_power_does_not_open_an_admin_operation() -> None:
    every = ManagerPowers(assign=True, answer=True, review=True, plan=True)
    assert not allowed(AgentRole.MANAGER, "orch_issue_token", admin=True, powers=every)


@pytest.mark.parametrize("name", ["orch_list_tasks", "orch_get_task", "orch_move_task"])
def test_a_reviewer_may_call_any_orch_operation_that_is_not_admin(name: str) -> None:
    assert allowed(AgentRole.REVIEWER, name, admin=False)


@pytest.mark.parametrize("name", ["orch_set_task", "orch_delete_project", "orch_issue_token"])
def test_a_reviewer_may_not_call_an_admin_one(name: str) -> None:
    assert not allowed(AgentRole.REVIEWER, name, admin=True)


@pytest.mark.parametrize("name", ["download", "get_status", "set_plugin_settings"])
def test_no_role_may_call_an_operation_that_is_not_orchs(name: str) -> None:
    for role in AgentRole:
        assert not allowed(role, name, admin=False)


def test_a_role_that_may_call_it_is_not_refused() -> None:
    who = Principal(kind=PrincipalKind.AGENT, name="fake-a", role="implementer")

    refused(who, "orch_ready", admin=False)  # (it does not raise)


def test_an_agent_calling_what_it_may_not_is_refused_with_its_role() -> None:
    who = Principal(kind=PrincipalKind.AGENT, name="fake-a", role="implementer")

    with pytest.raises(NotAllowed, match="implementer"):
        refused(who, "orch_set_task", admin=True)


async def test_deleting_an_agent_revokes_its_token(
    agents: AgentRecords, records: AppRecords
) -> None:
    """A token outlives no agent: delete the agent and its token is gone, so a new agent of the
    same name does not inherit the old one's key."""
    await an_agent(agents, "fake-a", AgentRole.IMPLEMENTER)
    await agents.tokens.issue("fake-a", TOKEN)

    assert await agents.delete_agent("fake-a") is True

    assert await OrchTokens(records).principal_for(TOKEN) is None


async def test_deleting_an_agent_that_had_no_token_is_fine(agents: AgentRecords) -> None:
    await an_agent(agents, "fake-a", AgentRole.IMPLEMENTER)

    assert await agents.delete_agent("fake-a") is True


# ---------------------------------------------------------------------------------------------
# A chat's token, and how long it outlives its chat being untouched (27.11)
# ---------------------------------------------------------------------------------------------


async def a_chat(records: AppRecords) -> Chat:
    """A chat of the owner's, with a token issued for it."""
    chat = await records.save(Chat(owner="human:owner", runner="claude-code"))
    await ChatTokenRecords(records).issue(chat, CHAT_TOKEN)
    return chat


async def test_a_chat_s_token_is_its_chat_while_it_is_used(tmp_path: Path) -> None:
    clock = FakeClock(NOW)
    db = databases(settings(tmp_path), registry(), clock=clock)
    await db.connect()
    records = AppRecords(db)
    chat = await a_chat(records)
    await clock.advance(timedelta(hours=23))

    who = await OrchTokens(records, clock=clock).principal_for(CHAT_TOKEN)

    assert who is not None
    assert (who.kind, who.name, who.chat) == (PrincipalKind.CHAT, "human:owner", chat.id)


async def test_a_chat_s_token_untouched_for_a_day_is_refused_and_taken_away(
    tmp_path: Path,
) -> None:
    """(A chat left open for a week is not a key left open for a week.)"""
    clock = FakeClock(NOW)
    db = databases(settings(tmp_path), registry(), clock=clock)
    await db.connect()
    records = AppRecords(db)
    await a_chat(records)
    await clock.advance(timedelta(hours=25))

    assert await OrchTokens(records, clock=clock).principal_for(CHAT_TOKEN) is None
    assert await ChatTokenRecords(records).by_hash(hash_token(CHAT_TOKEN)) is None


async def test_a_chat_s_token_goes_when_its_chat_does(tmp_path: Path) -> None:
    clock = FakeClock(NOW)
    db = databases(settings(tmp_path), registry(), clock=clock)
    await db.connect()
    records = AppRecords(db)
    chat = await a_chat(records)
    await ChatRecords(records).delete(chat)

    assert await OrchTokens(records, clock=clock).principal_for(CHAT_TOKEN) is None
