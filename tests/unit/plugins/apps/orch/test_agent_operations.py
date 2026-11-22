"""
--------------------------------------
- test_agent_operations.py -
--------------------------------------

The agents' and their sessions' operations (27.9): `orch_list_agents`, `orch_get_agent`,
`orch_set_agent`, `orch_delete_agent`, `orch_pause_agent`, `orch_resume_agent`,
`orch_get_session`, `orch_stop_session` and `orch_send`, each one method of the `orch` app,
offered on the CLI, the API and MCP by udl's generated adapters.

A session is a harness's own: these tests make one over the fake runner's session, and what they
check of `orch_send` is what a person typing into it gets — a turn of its own, in the transcript.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

# Package Imports
import pytest

# Local Imports
from support.engine import registry, settings
from support.runners import FakeRunner
from udl.core.models import Deleted
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, InUse, NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import (
    Agent as AgentShape,
)
from udl.plugins.apps.orch.agents import (
    AgentInfo,
    AgentRole,
    Agents,
    AgentStatus,
    Sent,
    SessionInfo,
    SessionState,
)
from udl.plugins.apps.orch.models import AssignTaskRequest
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    AgentSession,
    Feature,
    FeatureRecords,
    Project,
    ProjectRecords,
    SessionRecords,
    TaskRecords,
)
from udl.plugins.apps.orch.records import Task as TaskRecord
from udl.plugins.registry import APPS, RUNNERS, PluginRegistry


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store and the fake runner, and the orch app (which the
    tests are of)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, FakeRunner)
    return plugins


# ---------------------------------------------------------------------------------------------
# What the app offers
# ---------------------------------------------------------------------------------------------

AGENT_OPERATIONS: list[str] = [
    "list_agents",
    "get_agent",
    "set_agent",
    "delete_agent",
    "pause_agent",
    "resume_agent",
    "get_session",
    "stop_session",
    "send",
]
"""The operations 27.9 adds, in the order it offers them."""


def test_orch_offers_the_agents_operations() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert list(offered)[-len(AGENT_OPERATIONS) :] == AGENT_OPERATIONS
    # (Reading is reading; setting an agent and deleting one are the owner's, and a pause is
    # idempotent: pausing a paused agent is not an error.)
    assert offered["list_agents"].hints == OperationHints(read_only=True)
    assert offered["get_agent"].hints == OperationHints(read_only=True)
    assert offered["get_session"].hints == OperationHints(read_only=True)
    assert offered["set_agent"].hints == OperationHints(admin=True, idempotent=True)
    assert offered["delete_agent"].hints == OperationHints(
        admin=True, destructive=True, idempotent=True
    )
    assert offered["pause_agent"].hints == OperationHints(idempotent=True)
    assert offered["resume_agent"].hints == OperationHints(idempotent=True)
    assert offered["stop_session"].hints == OperationHints(destructive=True)
    # (A turn reaches a harness on a host: that is what the open world is.)
    assert offered["send"].hints == OperationHints(open_world=True)


async def test_the_operations_are_offered_as_orch_s(tmp_path: Path) -> None:
    async with UDLService(settings(tmp_path), registry=orch_registry()) as service:
        names = [op.name for op in service.operations.in_namespace("orch")]

    assert names[-len(AGENT_OPERATIONS) :] == [f"orch_{name}" for name in AGENT_OPERATIONS]


# ---------------------------------------------------------------------------------------------
# The operations, through the service (as the CLI, the API and MCP call them)
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service over a fresh in-memory database, with a project, a feature and three tasks."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="Q"))
        for key in ("F-1.1", "F-1.2", "F-1.3"):
            await TaskRecords(records).save_task(
                TaskRecord(project="udl", feature="F-1", key=key, title=key)
            )
        agents = AgentRecords(records)
        for name in ("ernie-c", "deepseek-b"):
            await agents.save_agent(Agent(name=name, title=name, runner="fake"))
        yield running


async def an_info(service: UDLService, name: str) -> AgentInfo:
    """`orch_get_agent`, as a caller of any interface calls it."""
    found = await service.call("orch_get_agent", {"name": name})

    assert isinstance(found, AgentInfo)
    return found


async def test_the_agents_are_listed_with_what_they_are_doing(service: UDLService) -> None:
    found = await service.call("orch_list_agents", {})

    assert isinstance(found, Agents)
    assert [one.agent.name for one in found.items] == ["deepseek-b", "ernie-c"]
    assert [one.status for one in found.items] == [AgentStatus.IDLE, AgentStatus.IDLE]


async def test_an_agent_is_read_with_its_queue_and_its_reports(service: UDLService) -> None:
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(name="boss", title="boss", runner="fake", role=AgentRole.MANAGER)
    )
    await reports_to(records, "ernie-c", "boss")
    await service.call(
        "orch_assign", AssignTaskRequest(key="F-1.1", agent="ernie-c", project="udl").model_dump()
    )

    found = await an_info(service, "boss")

    assert found.agent.name == "boss"
    assert found.reports == ["ernie-c"]
    assert found.queue == []
    assert found.session is None
    assert found.status is AgentStatus.IDLE


async def test_an_agent_is_set_and_then_edited(service: UDLService) -> None:
    made = await service.call(
        "orch_set_agent", {"name": "boss", "title": "Boss", "runner": "fake", "role": "manager"}
    )

    assert isinstance(made, AgentShape)
    assert made.name == "boss"
    assert made.role == "manager"

    edited = await service.call("orch_set_agent", {"name": "boss", "brief": "Watch the queue"})

    assert isinstance(edited, AgentShape)
    assert edited.brief == "Watch the queue"
    assert edited.title == "Boss"  # (what is not given is kept)


async def test_a_new_agent_needs_a_title_and_a_runner(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="needs a title and a runner"):
        await service.call("orch_set_agent", {"name": "boss"})


async def test_an_agent_that_is_not_one_is_not_found(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match="boss"):
        await an_info(service, "boss")


async def test_a_manager_is_cleared_with_an_empty_name(service: UDLService) -> None:
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(name="boss", title="boss", runner="fake", role=AgentRole.MANAGER)
    )
    await reports_to(records, "ernie-c", "boss")

    cleared = await service.call("orch_set_agent", {"name": "ernie-c", "manager": ""})

    assert isinstance(cleared, AgentShape)
    assert cleared.manager is None


async def test_an_agent_that_is_paused_is_paused_and_resumed(service: UDLService) -> None:
    paused = await service.call("orch_pause_agent", {"name": "ernie-c"})

    assert isinstance(paused, AgentShape)
    assert paused.paused is True
    assert (await an_info(service, "ernie-c")).status is AgentStatus.PAUSED

    resumed = await service.call("orch_resume_agent", {"name": "ernie-c"})

    assert isinstance(resumed, AgentShape)
    assert resumed.paused is False
    assert (await an_info(service, "ernie-c")).status is AgentStatus.IDLE


async def test_an_agent_on_an_unfinished_task_is_not_deleted(service: UDLService) -> None:
    await service.call(
        "orch_assign", AssignTaskRequest(key="F-1.1", agent="ernie-c", project="udl").model_dump()
    )

    with pytest.raises(InUse, match=r"F-1\.1"):
        await service.call("orch_delete_agent", {"name": "ernie-c"})


async def test_a_manager_is_not_deleted_while_it_has_reports(service: UDLService) -> None:
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(name="boss", title="boss", runner="fake", role=AgentRole.MANAGER)
    )
    await reports_to(records, "ernie-c", "boss")

    with pytest.raises(InUse, match="ernie-c"):
        await service.call("orch_delete_agent", {"name": "boss"})


async def test_an_agent_with_nothing_on_is_deleted(service: UDLService) -> None:
    deleted = await service.call("orch_delete_agent", {"name": "ernie-c"})

    assert isinstance(deleted, Deleted)
    assert deleted.deleted is True
    with pytest.raises(DoesNotExist):
        await an_info(service, "ernie-c")


async def test_a_session_is_read_with_a_page_of_its_transcript(service: UDLService) -> None:
    found = await a_session(service)

    read = await service.call("orch_get_session", {"session": str(found.id), "limit": 2})

    assert isinstance(read, SessionInfo)
    assert read.id == found.id
    assert read.total == 3
    assert [one.turn for one in read.events] == [0, 1]


async def test_a_session_that_is_not_one_is_not_found(service: UDLService) -> None:
    with pytest.raises(DoesNotExist):
        await service.call("orch_get_session", {"session": str(uuid4())})


async def test_stopping_a_session_ends_it(service: UDLService) -> None:
    found = await a_session(service, state=SessionState.RUNNING)

    stopped = await service.call("orch_stop_session", {"session": str(found.id)})

    assert isinstance(stopped, SessionInfo)
    assert stopped.state is SessionState.ENDED
    stored = await SessionRecords(AppRecords(service.db)).session(found.id)
    assert stored is not None
    assert stored.state is SessionState.ENDED
    assert (await an_info(service, "ernie-c")).session is None


async def test_a_message_is_a_turn_of_its_own(service: UDLService) -> None:
    found = await a_session(service, state=SessionState.IDLE)

    sent = await service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})

    assert isinstance(sent, Sent)
    assert sent.session == found.id
    assert sent.turn == 1
    after = await service.call("orch_get_session", {"session": str(found.id)})
    assert isinstance(after, SessionInfo)
    assert after.turns == 1
    assert after.total == 4  # (the three it had, and what the turn said)


async def test_a_message_is_refused_into_a_session_in_a_turn(service: UDLService) -> None:
    await a_session(service, state=SessionState.RUNNING)

    with pytest.raises(NotAllowed, match="in a turn"):
        await service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})


async def test_a_steer_is_refused_while_no_harness_takes_one(service: UDLService) -> None:
    await a_session(service, state=SessionState.RUNNING)

    with pytest.raises(NotAllowed, match="no input yet"):
        await service.call("orch_send", {"agent": "ernie-c", "text": "Go on", "delivery": "steer"})


async def test_a_message_into_an_agent_with_no_session_is_refused(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="no session"):
        await service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})


# ---------------------------------------------------------------------------------------------
# Fixtures' own helpers
# ---------------------------------------------------------------------------------------------


async def a_session(
    service: UDLService, *, state: SessionState = SessionState.RUNNING, **fields: Any
) -> AgentSession:
    """A session of `ernie-c`, with three events in its transcript, as its dispatcher left it."""
    from udl.plugins.runners.base import SessionRef, TextDelta

    records = AppRecords(service.db)
    sessions = SessionRecords(records)
    found = await sessions.save_session(
        AgentSession(
            agent="ernie-c",
            task="F-1.1",
            ref=SessionRef(runner="fake", id="ses_1", cwd="/work", host="local"),
            state=state,
            turns=0,
            **fields,
        )
    )
    for turn in (0, 1, 2):
        await sessions.save_event(
            stored_event(found, turn=turn, event=TextDelta(text=f"turn {turn}"))
        )
    return found


async def reports_to(records: AppRecords, name: str, manager: str) -> None:
    """Make the agent called `name` report to `manager` (the one the fixture made)."""
    agents = AgentRecords(records)
    found = await agents.agent(name)
    assert found is not None
    await agents.save_agent(found.model_copy(update={"manager": manager}))


def stored_event(session: AgentSession, *, turn: int, event: Any) -> Any:
    """One thing said in `session`, as its transcript keeps it."""
    from udl.plugins.apps.orch.records import SessionEvent

    return SessionEvent(agent=session.agent, session=session.id, turn=turn, event=event)
