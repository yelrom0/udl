"""
----------------------------
- test_agents.py -
----------------------------

Agents and their sessions (udl.plugins.apps.orch.agents): what an agent is (its runner, its
model, where it runs, its role, its manager, its powers, its projects, its brief and its
nudges), what it refuses (a role that is not one, a name that cannot name one), and the status
it has — derived from its session, never stored.

Then the stored side (orch.records): an agent is unique by its name, the hierarchy is a forest
(a manager that is not a manager, a manager chain that reaches itself, a manager that is not
there), and a session's events are its transcript.
"""

# System Imports
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from support.clock import FakeClock
from support.engine import databases, registry, settings
from udl.config import Settings
from udl.db.records import RecordScope, SyncRule
from udl.errors import ConfigError, DoesNotExist, DuplicateKey, InUse
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import (
    Agent,
    AgentRole,
    AgentSession,
    AgentStatus,
    ManagerPowers,
    SessionState,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import Agent as AgentRecord
from udl.plugins.apps.orch.records import (
    AgentRecords,
    SessionEvent,
    SessionRecords,
)
from udl.plugins.apps.orch.records import (
    AgentSession as AgentSessionRecord,
)
from udl.plugins.apps.orch.records import (
    Project as ProjectRecord,
)
from udl.plugins.apps.orch.records import (
    Repo as RepoRecord,
)
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.runners.base import SessionRef, TextDelta

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
"""When the status tests ask."""


def agent(**fields: object) -> Agent:
    """An agent of the fake runner, as a test changes it."""
    return Agent.model_validate({"name": "fake-a", "title": "Fake A", "runner": "fake", **fields})


def session(**fields: object) -> AgentSession:
    """A session of fake-a, as a test changes it."""
    return AgentSession.model_validate(
        {
            "agent": "fake-a",
            "task": "27.1",
            "ref": SessionRef(runner="fake", id="s-1", cwd="/work", host="local"),
            **fields,
        }
    )


# ---------------------------------------------------------------------------------------------
# What an agent is
# ---------------------------------------------------------------------------------------------


def test_an_agent_holds_what_it_runs_and_where() -> None:
    found = agent(model="deepseek-v4", host="jeff-wiggle", runner_options={"args": ["-x"]})

    assert (found.name, found.title, found.runner) == ("fake-a", "Fake A", "fake")
    assert (found.model, found.host) == ("deepseek-v4", "jeff-wiggle")
    assert found.runner_options == {"args": ["-x"]}


def test_an_agent_is_an_implementer_on_this_host_unless_it_says_otherwise() -> None:
    found = agent()

    assert found.role is AgentRole.IMPLEMENTER
    assert found.manager is None  # (no manager: it reports to the humans)
    assert found.manages == ManagerPowers()
    assert found.projects == []  # (empty: every project)
    assert (found.brief, found.max_nudges) == ("", 3)
    assert (found.enabled, found.paused) == (True, False)


@pytest.mark.parametrize("role", list(AgentRole))
def test_a_role_is_one_of_three(role: AgentRole) -> None:
    assert agent(role=role).role is role


def test_a_role_that_is_not_one_is_refused() -> None:
    with pytest.raises(ValidationError):
        agent(role="robot")


def test_an_agent_needs_a_name_a_title_and_a_runner() -> None:
    with pytest.raises(ValidationError):
        Agent(title="Fake A", runner="fake")  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        Agent(name="fake-a", runner="fake")  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        Agent(name="fake-a", title="Fake A")  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize("name", ["", "has space", "a/b", "-nope", "x" * 101])
def test_a_name_that_cannot_name_one_is_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        agent(name=name)


def test_its_runner_options_are_json() -> None:
    with pytest.raises(ValidationError):
        agent(runner_options={"when": object()})


def test_its_nudges_are_at_least_one() -> None:
    assert agent(max_nudges=1).max_nudges == 1
    with pytest.raises(ValidationError):
        agent(max_nudges=0)


def test_its_projects_are_names() -> None:
    assert agent(projects=["udl", "liively"]).projects == ["udl", "liively"]
    with pytest.raises(ValidationError):
        agent(projects=["has space"])


def test_a_managers_powers_are_off_until_they_are_switched_on() -> None:
    # (O-D10: a power is off unless it is given, and a manager has none by default.)
    assert ManagerPowers() == ManagerPowers(assign=False, answer=False, review=False, plan=False)

    manager = agent(role=AgentRole.MANAGER, manages=ManagerPowers(assign=True, review=True))

    assert manager.manages.assign
    assert manager.manages.review
    assert not manager.manages.answer
    assert not manager.manages.plan


# ---------------------------------------------------------------------------------------------
# What it is doing (derived, never stored)
# ---------------------------------------------------------------------------------------------


def test_an_agent_with_no_session_is_idle() -> None:
    assert agent().status(None, now=NOW) is AgentStatus.IDLE


@pytest.mark.parametrize("state", [SessionState.STARTING, SessionState.RUNNING])
def test_an_agent_whose_session_runs_is_working(state: SessionState) -> None:
    assert agent().status(session(state=state), now=NOW) is AgentStatus.WORKING


def test_an_agent_whose_session_answered_is_waiting_for_its_next_prompt() -> None:
    assert agent().status(session(state=SessionState.IDLE), now=NOW) is AgentStatus.WAITING


def test_an_ended_session_leaves_an_agent_idle() -> None:
    assert agent().status(session(state=SessionState.ENDED), now=NOW) is AgentStatus.IDLE


def test_an_agent_whose_session_failed_waits_on_a_person() -> None:
    # (27.7 raises a question when a session fails: the agent is waiting on its answer.)
    assert agent().status(session(state=SessionState.FAILED), now=NOW) is AgentStatus.WAITING


def test_an_agent_whose_session_is_limited_is_limited_until_its_quota_is_back() -> None:
    limited = session(state=SessionState.LIMITED, limited_until=NOW + timedelta(hours=1))

    assert agent().status(limited, now=NOW) is AgentStatus.LIMITED
    # (Its quota is back: nothing is running, so it is idle until the dispatcher starts it.)
    assert agent().status(limited, now=NOW + timedelta(hours=2)) is AgentStatus.IDLE


def test_a_limited_session_that_does_not_say_when_it_is_back_stays_limited() -> None:
    # (A limited session that did not say until when stays limited: it has nothing to run.)
    assert agent().status(session(state=SessionState.LIMITED), now=NOW) is AgentStatus.LIMITED


def test_a_paused_agent_is_paused_whatever_its_session_does() -> None:
    assert (
        agent(paused=True).status(session(state=SessionState.RUNNING), now=NOW)
        is AgentStatus.PAUSED
    )


def test_an_agent_whose_host_is_off_is_offline() -> None:
    # (27.7's host check says so: a running session on a host that went away is offline.)
    assert (
        agent().status(session(state=SessionState.RUNNING), offline=True, now=NOW)
        is AgentStatus.OFFLINE
    )
    assert agent(paused=True).status(None, offline=True, now=NOW) is AgentStatus.PAUSED


# ---------------------------------------------------------------------------------------------
# Storing them
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def records(tmp_path: Path) -> AppRecords:
    """Orch's records over a fresh in-memory database (as tests/support/engine.py makes one)."""
    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    return AppRecords(db)


@pytest.fixture
async def agents(records: AppRecords) -> AgentRecords:
    """Orch's agents, over the same records."""
    return AgentRecords(records)


@pytest.fixture
async def sessions(records: AppRecords) -> SessionRecords:
    """Orch's sessions, over the same records."""
    return SessionRecords(records)


def stored(**fields: object) -> AgentRecord:
    """An agent as it is stored: its shape's fields."""
    return AgentRecord.model_validate(agent(**fields).model_dump())


async def test_a_saved_agent_is_read_back(agents: AgentRecords) -> None:
    saved = await agents.save_agent(stored(brief="Never push to main."))

    assert await agents.agent("fake-a") == saved
    assert [found.name for found in await agents.agents()] == ["fake-a"]
    assert await agents.agent("fake-b") is None


async def test_a_second_agent_with_the_same_name_is_refused(agents: AgentRecords) -> None:
    await agents.save_agent(stored())

    with pytest.raises(DuplicateKey):
        await agents.save_agent(stored(title="Again"))


async def test_an_agent_that_reports_to_an_agent_that_is_not_there_is_refused(
    agents: AgentRecords,
) -> None:
    with pytest.raises(DoesNotExist):
        await agents.save_agent(stored(manager="nobody"))


async def test_an_agent_that_reports_to_an_agent_that_is_not_a_manager_is_refused(
    agents: AgentRecords,
) -> None:
    await agents.save_agent(stored())

    with pytest.raises(ConfigError, match="is not a manager"):
        await agents.save_agent(stored(name="fake-b", manager="fake-a"))


async def test_a_manager_chain_that_reaches_itself_is_refused(agents: AgentRecords) -> None:
    await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER))
    await agents.save_agent(stored(name="lead", role=AgentRole.MANAGER, manager="boss"))

    # (lead → boss → lead would be a cycle: saving boss under lead is refused.)
    with pytest.raises(ConfigError, match="reaches itself"):
        await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER, manager="lead"))


async def test_an_agent_that_manages_itself_is_refused(agents: AgentRecords) -> None:
    await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER))

    with pytest.raises(ConfigError, match="reaches itself"):
        await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER, manager="boss"))


async def test_a_managers_reports_are_listed_by_name(agents: AgentRecords) -> None:
    await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER))
    await agents.save_agent(stored(name="one", manager="boss"))
    await agents.save_agent(stored(name="two", manager="boss"))
    await agents.save_agent(stored(name="alone"))

    assert [found.name for found in await agents.reports("boss")] == ["one", "two"]
    assert await agents.reports("alone") == []


async def test_a_manager_with_reports_cannot_stop_being_a_manager(agents: AgentRecords) -> None:
    boss = await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER))
    await agents.save_agent(stored(name="one", manager="boss"))

    with pytest.raises(ConfigError, match="has reports"):
        await agents.save_agent(
            AgentRecord.model_validate({**boss.model_dump(), "role": AgentRole.IMPLEMENTER})
        )


async def test_a_manager_with_reports_cannot_be_renamed(agents: AgentRecords) -> None:
    # (A rename would leave its reports pointing at a name that is not there.)
    boss = await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER))
    await agents.save_agent(stored(name="one", manager="boss"))

    with pytest.raises(ConfigError, match="has reports"):
        await agents.save_agent(AgentRecord.model_validate({**boss.model_dump(), "name": "chief"}))


async def test_a_manager_with_reports_cannot_be_deleted(agents: AgentRecords) -> None:
    await agents.save_agent(stored(name="boss", role=AgentRole.MANAGER))
    await agents.save_agent(stored(name="one", manager="boss"))

    with pytest.raises(InUse, match="manager of one"):
        await agents.delete_agent("boss")
    assert await agents.agent("boss") is not None


async def test_a_deleted_agents_sessions_and_transcript_are_gone(
    agents: AgentRecords, sessions: SessionRecords
) -> None:
    await agents.save_agent(stored())
    saved = await sessions.save_session(AgentSessionRecord.model_validate(session().model_dump()))
    await sessions.save_event(
        SessionEvent(agent="fake-a", session=saved.id, turn=1, event=TextDelta(text="On it."))
    )

    assert await agents.delete_agent("fake-a")

    assert await sessions.session(saved.id) is None
    assert await sessions.sessions("fake-a") == []
    assert await sessions.transcript(saved.id) == []


async def test_a_deleted_agent_is_gone(agents: AgentRecords) -> None:
    await agents.save_agent(stored())

    assert await agents.delete_agent("fake-a")
    assert await agents.agent("fake-a") is None
    assert not await agents.delete_agent("fake-a")


# ---------------------------------------------------------------------------------------------
# Sessions and their transcript
# ---------------------------------------------------------------------------------------------


async def test_a_saved_session_is_read_back(sessions: SessionRecords) -> None:
    saved = await sessions.save_session(AgentSessionRecord.model_validate(session().model_dump()))

    found = await sessions.session(saved.id)
    assert found == saved
    assert found is not None
    # (Its ref is the harness's own: the runner, its id, the folder and the host.)
    assert (found.ref.runner, found.ref.id, found.ref.host) == ("fake", "s-1", "local")
    assert (found.turns, found.tokens_in, found.tokens_out, found.cost) == (0, 0, 0, None)
    assert (found.state, found.limited_until, found.last_text) == (SessionState.STARTING, None, "")


async def test_an_agents_current_session_is_its_newest_unfinished_one(
    sessions: SessionRecords,
) -> None:
    first = AgentSessionRecord.model_validate(session(state=SessionState.ENDED).model_dump())
    second = AgentSessionRecord.model_validate(session(state=SessionState.RUNNING).model_dump())
    await sessions.save_session(first)
    await sessions.save_session(second)

    current = await sessions.current("fake-a")

    assert current is not None
    assert current.id == second.id
    assert [found.id for found in await sessions.sessions("fake-a")] == [second.id, first.id]
    assert await sessions.current("fake-b") is None


async def test_a_sessions_events_are_its_transcript_in_order(sessions: SessionRecords) -> None:
    saved = await sessions.save_session(AgentSessionRecord.model_validate(session().model_dump()))
    said = TextDelta(text="On it.")
    for turn in (1, 1, 2):
        await sessions.save_event(
            SessionEvent(agent="fake-a", session=saved.id, turn=turn, event=said)
        )

    found = await sessions.transcript(saved.id)

    assert [event.turn for event in found] == [1, 1, 2]
    assert found[0].event == said
    assert found[0].agent == "fake-a"
    assert await sessions.transcript(uuid4()) == []


def test_a_session_event_is_history_that_syncs_once_it_has_ended() -> None:
    # (The transcript is history: one runtime's, kept as what db sync copies once it happened.)
    assert SessionEvent.scope is RecordScope.RUNTIME
    assert SessionEvent.sync is SyncRule.ENDED
    assert SessionEvent(agent="fake-a", session=uuid4(), event=TextDelta(text="x")).ended() is True


def test_orch_keeps_a_sessions_transcript_for_thirty_days() -> None:
    assert OrchSettings().session_days == 30
    with pytest.raises(ValidationError):
        OrchSettings(session_days=0)


def test_orch_brings_its_agent_kinds() -> None:
    # (The exact set: a kind added or left in by mistake would be noticed here.)
    assert set(Orch.record_kinds()) == {
        ProjectRecord,
        RepoRecord,
        AgentRecord,
        AgentSessionRecord,
        SessionEvent,
    }


async def test_an_old_transcript_is_pruned_and_a_recent_one_kept(tmp_path: Path) -> None:
    # (The records are stamped with the database's clock, so the test moves it.)
    clock = FakeClock(NOW)
    db = databases(settings(tmp_path), registry(), clock=clock)
    await db.connect()
    sessions = SessionRecords(AppRecords(db))
    saved = await sessions.save_session(AgentSessionRecord.model_validate(session().model_dump()))
    old = await sessions.save_event(
        SessionEvent(agent="fake-a", session=saved.id, turn=1, event=TextDelta(text="Long ago."))
    )
    await clock.advance(timedelta(days=40))
    recent = await sessions.save_event(
        SessionEvent(agent="fake-a", session=saved.id, turn=2, event=TextDelta(text="Today."))
    )

    # (session_days is 30: what is older than that goes, and what is newer stays.)
    pruned = await sessions.prune(clock.now() - timedelta(days=30))

    assert pruned == 1
    assert await sessions.transcript(saved.id) == [recent]
    assert old.id != recent.id
