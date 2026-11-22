"""
------------------
- test_managers.py -
------------------

What a manager may do for its reports (27.8), as pure functions: the four powers, the operations
each switches on, and who is inside a manager's subtree.

The operations themselves are tested through the service in
`tests/integration/plugins/apps/orch/test_managers.py`; this file is the reach they are held to.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import databases, registry, settings
from udl.errors import ConfigError
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers
from udl.plugins.apps.orch.managers import (
    POWER_OPERATIONS,
    POWERS,
    in_subtree,
    managers_of,
    reports,
    subtree,
    switched_on,
)
from udl.plugins.apps.orch.records import Agent, AgentRecords

BOSS: str = "boss"
"""The manager at the top."""
MID: str = "mid"
"""A manager that reports to `BOSS`."""
LOW: str = "low"
"""An implementer that reports to `MID`."""
STRANGER: str = "stranger"
"""Another manager's report."""


@pytest.fixture
async def agents(tmp_path: Path) -> AgentRecords:
    """A hierarchy: `BOSS` → `MID` → `LOW`, and a `STRANGER` under nobody."""
    plugins = registry()
    db = databases(settings(tmp_path), plugins)
    await db.connect()
    found = AgentRecords(AppRecords(db))
    await found.save_agent(Agent(name=BOSS, title=BOSS, runner="fake", role=AgentRole.MANAGER))
    await found.save_agent(
        Agent(name=MID, title=MID, runner="fake", role=AgentRole.MANAGER, manager=BOSS)
    )
    await found.save_agent(Agent(name=LOW, title=LOW, runner="fake", manager=MID))
    await found.save_agent(Agent(name=STRANGER, title=STRANGER, runner="fake"))
    return found


def test_the_four_powers_are_the_card_s() -> None:
    assert POWERS == ("assign", "answer", "review", "plan")
    assert set(POWER_OPERATIONS) == set(POWERS)


def test_no_power_switches_on_nothing() -> None:
    assert switched_on(None) == frozenset()
    assert switched_on(ManagerPowers()) == frozenset()


def test_each_power_switches_on_its_operations() -> None:
    assert switched_on(ManagerPowers(assign=True)) == POWER_OPERATIONS["assign"]
    assert switched_on(ManagerPowers(answer=True)) == frozenset({"orch_answer"})
    assert switched_on(ManagerPowers(review=True)) == frozenset({"orch_review_task"})
    assert switched_on(ManagerPowers(plan=True)) == frozenset({"orch_set_task", "orch_split_task"})


def test_all_four_switch_on_all_of_them() -> None:
    every = ManagerPowers(assign=True, answer=True, review=True, plan=True)
    assert switched_on(every) == frozenset().union(*POWER_OPERATIONS.values())


async def test_reports_are_the_agents_that_name_it(agents: AgentRecords) -> None:
    assert [one.name for one in await reports(agents, BOSS)] == [MID]
    assert [one.name for one in await reports(agents, MID)] == [LOW]
    assert await reports(agents, LOW) == []


async def test_the_subtree_is_itself_and_everything_below(agents: AgentRecords) -> None:
    assert await subtree(agents, BOSS) == {BOSS, MID, LOW}
    assert await subtree(agents, MID) == {MID, LOW}
    assert await subtree(agents, LOW) == {LOW}
    assert await subtree(agents, STRANGER) == {STRANGER}


async def test_a_manager_reaches_its_own_subtree_only(agents: AgentRecords) -> None:
    assert await in_subtree(agents, BOSS, LOW), "however far down"
    assert await in_subtree(agents, MID, LOW)
    assert not await in_subtree(agents, MID, BOSS), "never up"
    assert not await in_subtree(agents, BOSS, STRANGER), "never across"
    assert await in_subtree(agents, LOW, LOW), "always itself"


async def test_a_hierarchy_with_a_cycle_is_refused(agents: AgentRecords) -> None:
    """(udl refuses to make one: an implementer cannot take reports, so `BOSS` cannot report to
    `LOW` — and a cycle has nowhere to go.)"""
    found = await agents.agent(BOSS)
    assert found is not None
    with pytest.raises(ConfigError, match="not a manager"):
        await agents.save_agent(found.model_copy(update={"manager": LOW}))


async def test_the_managers_above_an_agent_nearest_first(agents: AgentRecords) -> None:
    every = await agents.agents()
    assert [one.name for one in managers_of(every, LOW)] == [MID, BOSS]
    assert [one.name for one in managers_of(every, MID)] == [BOSS]
    assert managers_of(every, BOSS) == []
