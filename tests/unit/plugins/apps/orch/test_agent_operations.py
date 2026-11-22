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
import asyncio
import gc
import weakref
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import ClassVar
from uuid import UUID, uuid4

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from support.runners import SharedRunner
from udl.core.models import Deleted
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, InUse, NotAllowed, PluginFailed
from udl.interfaces.security import Principal, PrincipalKind, current_principal
from udl.plugins.apps.base import AppEvents, AppRecords
from udl.plugins.apps.orch import dispatcher
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
    an_event,
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
    SessionEvent,
    SessionRecords,
    TaskRecords,
)
from udl.plugins.apps.orch.records import Task as TaskRecord
from udl.plugins.registry import APPS, RUNNERS, PluginRegistry
from udl.plugins.runners.base import (
    AgentEvent,
    SessionRef,
    SessionSpec,
    TextDelta,
    ToolStarted,
    TurnEnded,
    TurnReason,
)


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store and the fake runner, and the orch app (which the
    tests are of)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, SharedRunner)
    return plugins


class WaitingRunner(SharedRunner):
    """The fake, with a turn that waits until the test lets it go (a turn a person stops while
    it runs)."""

    started: ClassVar[asyncio.Event] = asyncio.Event()
    """Set when a turn is in it (the test waits for that, not for a sleep)."""
    go_on: ClassVar[asyncio.Event] = asyncio.Event()
    """Set when the test lets the turn finish."""
    sends: ClassVar[int] = 0
    """How many turns it was asked for (a turn that waits for the lease never got here)."""

    async def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        WaitingRunner.sends += 1
        WaitingRunner.started.set()
        yield ToolStarted(name="udl.wait", summary="Waiting for udl")
        await WaitingRunner.go_on.wait()
        yield TurnEnded(reason=TurnReason.DONE, text="Done.")


def reset_waiting() -> None:
    """Fresh events for this test's loop, and no turns counted yet (an `Event` belongs to the
    loop that first awaited it, and a test runs in a loop of its own)."""
    WaitingRunner.started = asyncio.Event()
    WaitingRunner.go_on = asyncio.Event()
    WaitingRunner.sends = 0


class BrokenStopRunner(SharedRunner):
    """The fake, whose harness cannot stop a session (it is gone, or never had it)."""

    async def stop(self, session: SessionRef) -> None:
        raise PluginFailed(f"No session {session.id!r} to stop.")


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
    assert offered["get_session"].hints == OperationHints(admin=True, read_only=True)
    assert offered["set_agent"].hints == OperationHints(admin=True, idempotent=True)
    assert offered["delete_agent"].hints == OperationHints(
        admin=True, destructive=True, idempotent=True
    )
    # (Pausing, resuming, stopping a session and reading its transcript are the owner's: an
    # agent's role table admits every non-admin orch_* operation, and these are not its own.)
    assert offered["pause_agent"].hints == OperationHints(admin=True, idempotent=True)
    assert offered["resume_agent"].hints == OperationHints(admin=True, idempotent=True)
    assert offered["stop_session"].hints == OperationHints(admin=True, destructive=True)
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
    # (The turn it runs as: the session's count of turns before it, as the dispatcher numbers
    # them — a message is the session's next turn, not one past it.)
    assert sent.turn == 3
    after = await service.call("orch_get_session", {"session": str(found.id)})
    assert isinstance(after, SessionInfo)
    assert after.turns == 4
    assert after.total == 5  # (the three it had, what it said, and how the turn ended)


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


async def test_the_agents_of_one_project_are_the_only_ones_listed(service: UDLService) -> None:
    records = AppRecords(service.db)
    found = await AgentRecords(records).agent("ernie-c")
    assert found is not None
    await AgentRecords(records).save_agent(found.model_copy(update={"projects": ["other"]}))

    listed = await service.call("orch_list_agents", {"project": "udl"})

    assert isinstance(listed, Agents)
    assert [one.agent.name for one in listed.items] == ["deepseek-b"]


async def test_listing_the_agents_reads_no_transcript(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A list of agents is polled (the UI does): it reads each agent's session, never its whole
    transcript — only `orch_get_session` reads that."""
    await a_session(service)

    async def refuse(self: SessionRecords, session: UUID) -> list[SessionEvent]:
        raise AssertionError("the transcript was read")

    monkeypatch.setattr(SessionRecords, "transcript", refuse)

    found = await service.call("orch_list_agents", {})

    assert isinstance(found, Agents)
    assert [one.session.turns for one in found.items if one.session is not None] == [3]


async def test_a_queue_of_two_projects_says_which(service: UDLService) -> None:
    """A key is only unique within a project, so a queue that spans them says which task is
    whose (two tasks `F-1.1`, one in each project)."""
    records = AppRecords(service.db)
    await ProjectRecords(records).save_project(Project(name="other", title="Other"))
    await FeatureRecords(records).save_feature(Feature(project="other", key="F-1", title="Q"))
    await TaskRecords(records).save_task(
        TaskRecord(project="other", feature="F-1", key="F-1.1", title="Elsewhere")
    )
    await queue_task(service, "F-1.1", "ernie-c")
    await queue_task(service, "F-1.1", "ernie-c", project="other")

    found = await service.call("orch_get_agent", {"name": "ernie-c"})

    assert isinstance(found, AgentInfo)
    assert sorted((one.project, one.key) for one in found.queue) == [
        ("other", "F-1.1"),
        ("udl", "F-1.1"),
    ]


async def test_a_transcript_pages_from_an_offset(service: UDLService) -> None:
    found = await a_session(service)

    page = await service.call(
        "orch_get_session", {"session": str(found.id), "offset": 1, "limit": 1}
    )

    assert isinstance(page, SessionInfo)
    assert page.total == 3
    assert [one.turn for one in page.events] == [1]


async def test_a_message_from_an_agent_is_refused(service: UDLService) -> None:
    await a_session(service, state=SessionState.IDLE)

    token = current_principal.set(
        Principal(kind=PrincipalKind.AGENT, name="ernie-c", role="reviewer")
    )
    try:
        with pytest.raises(NotAllowed, match="owner's"):
            await service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})
    finally:
        current_principal.reset(token)


@pytest.mark.parametrize(
    ("name", "asked"),
    [
        ("orch_pause_agent", {"agent": "ernie-c"}),
        ("orch_resume_agent", {"agent": "ernie-c"}),
        ("orch_stop_session", {"session": "00000000-0000-0000-0000-000000000000"}),
        ("orch_get_session", {"session": "00000000-0000-0000-0000-000000000000"}),
    ],
)
async def test_the_owners_controls_are_not_an_agents(
    service: UDLService, name: str, asked: dict[str, str]
) -> None:
    """Pausing, resuming, stopping and reading a session are the owner's: a reviewer's role table
    admits every non-admin orch_* operation, so these four are refused as admin ones."""
    token = current_principal.set(
        Principal(kind=PrincipalKind.AGENT, name="ernie-c", role="reviewer")
    )
    try:
        with pytest.raises(NotAllowed, match="may call"):
            await service.call(name, asked)
    finally:
        current_principal.reset(token)


async def test_a_harness_that_cannot_stop_it_still_ends_the_session(tmp_path: Path) -> None:
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, BrokenStopRunner)
    async with UDLService(settings(tmp_path), registry=plugins) as service:
        found = await a_session(service, state=SessionState.RUNNING)

        stopped = await service.call("orch_stop_session", {"session": str(found.id)})

        assert isinstance(stopped, SessionInfo)
        assert stopped.state is SessionState.ENDED
        stored = await SessionRecords(AppRecords(service.db)).session(found.id)
        assert stored is not None
        assert stored.state is SessionState.ENDED
        assert stored.last_error.startswith("stopping it:")


async def test_a_message_does_not_bring_an_ended_session_back(service: UDLService) -> None:
    """A message that waits on the turn lock reads the session again once it has it: a session a
    turn ended (or a person stopped) while it waited stays ended, and nothing is sent into it."""
    found = await a_session(service)
    sessions = SessionRecords(AppRecords(service.db))
    lock = dispatcher.turn_lock(found.id)
    await lock.acquire()
    sending = asyncio.create_task(service.call("orch_send", {"agent": "ernie-c", "text": "Go on"}))
    try:
        await asyncio.sleep(0.05)  # (it reaches the lock, and waits there)
        await sessions.save_session(found.model_copy(update={"state": SessionState.ENDED}))
    finally:
        lock.release()

    with pytest.raises(NotAllowed, match="ended while the message"):
        await sending
    stored = await sessions.session(found.id)
    assert stored is not None
    assert stored.state is SessionState.ENDED
    assert stored.turns == found.turns


async def test_a_message_numbers_the_turn_it_runs(service: UDLService) -> None:
    """The turn a message runs as is read once it has the lock, not from the snapshot it took
    before it (a turn that ended while it waited is not the one it follows)."""
    found = await a_session(service)
    sessions = SessionRecords(AppRecords(service.db))
    lock = dispatcher.turn_lock(found.id)
    await lock.acquire()
    sending = asyncio.create_task(service.call("orch_send", {"agent": "ernie-c", "text": "Go on"}))
    try:
        await asyncio.sleep(0.05)
        await sessions.save_session(found.model_copy(update={"turns": found.turns + 1}))
    finally:
        lock.release()

    sent = await sending
    assert isinstance(sent, Sent)
    assert sent.turn == found.turns + 1


async def test_the_turn_locks_do_not_outlive_their_turns(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One lock per session, while a turn is in it: the table does not grow with every session
    the process has ever sent a turn to."""
    await a_session(service)
    made: list[asyncio.Lock] = []
    real = dispatcher.turn_lock

    def recording(session: UUID) -> asyncio.Lock:
        lock = real(session)
        made.append(lock)
        return lock

    monkeypatch.setattr(dispatcher, "turn_lock", recording)

    await service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})

    assert made  # (the turn took one)
    gone = weakref.ref(made.pop())
    gc.collect()
    # (The table holds it weakly, and nothing else holds the turn's own lock once it is done.)
    assert gone() is None


async def test_a_turn_waits_for_the_other_process_s_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two `Turns` of one session, each its own holder (as two processes' are): the second waits
    for the first's lease, and runs when it is given back (27.9).

    The in-process lock is a fresh one per turn here, as another process's would be: only the
    lease can keep the two apart."""
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, WaitingRunner)
    clock = FakeClock()
    async with UDLService(settings(tmp_path), registry=plugins, clock=clock) as service:
        await AgentRecords(AppRecords(service.db)).save_agent(
            Agent(name="ernie-c", title="ernie-c", runner="fake")
        )
        found = await a_session(service, state=SessionState.IDLE)
        agent = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
        assert agent is not None
        first = await a_turns(service)
        second = await a_turns(service)

        def another_process_s_lock(session: UUID) -> asyncio.Lock:
            """A lock of its own per turn, as another process's would be."""
            return asyncio.Lock()

        monkeypatch.setattr(dispatcher, "turn_lock", another_process_s_lock)
        reset_waiting()

        one = asyncio.create_task(first.one(agent, found, "Go on"))
        async with asyncio.timeout(10):
            await WaitingRunner.started.wait()
            two = asyncio.create_task(second.one(agent, found, "And again"))
            for _ in range(20):
                await asyncio.sleep(0)
            assert WaitingRunner.sends == 1  # (the second is waiting for the lease)
            assert not two.done()
            WaitingRunner.go_on.set()
            ran = await one
            # (The second turn's retry is a pause on the clock: moving it on is what lets it in.)
            await clock.advance(dispatcher.TurnLease.RETRY + timedelta(seconds=1))
            again = await two

        assert ran is not None
        assert ran.outcome is dispatcher.Outcome.DONE
        assert again is not None
        assert again.outcome is dispatcher.Outcome.DONE
        assert WaitingRunner.sends == 2


async def test_a_turn_that_never_gets_its_lease_is_no_turn(tmp_path: Path) -> None:
    """A turn whose lease another holder keeps past `TURN_WAIT` is no turn at all: the session is
    left exactly as it was — not counted, not failed, not ended (27.9)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, WaitingRunner)
    clock = FakeClock()
    async with UDLService(settings(tmp_path), registry=plugins, clock=clock) as service:
        await AgentRecords(AppRecords(service.db)).save_agent(
            Agent(name="ernie-c", title="ernie-c", runner="fake")
        )
        found = await a_session(service, state=SessionState.IDLE)
        agent = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
        assert agent is not None
        await service.db.claim_lease(
            f"orch-turn:{found.id}", "another-host:4242:abcd", timedelta(hours=1)
        )
        turns = await a_turns(service)
        reset_waiting()

        one = asyncio.create_task(turns.one(agent, found, "Go on"))
        async with asyncio.timeout(10):
            await clock.advance(dispatcher.TURN_WAIT + timedelta(seconds=1))
            assert await one is None

        assert WaitingRunner.sends == 0  # (the harness was never sent a turn)
        left = await SessionRecords(AppRecords(service.db)).session(found.id)
        assert left is not None
        assert left.state is SessionState.IDLE
        assert left.turns == found.turns
        assert left.failures == 0


async def test_a_long_turn_keeps_its_lease(tmp_path: Path) -> None:
    """A turn longer than the lease's ttl keeps it, renewed while it runs: a second holder cannot
    take a turn that is still going (27.9)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, WaitingRunner)
    clock = FakeClock()
    async with UDLService(settings(tmp_path), registry=plugins, clock=clock) as service:
        await AgentRecords(AppRecords(service.db)).save_agent(
            Agent(name="ernie-c", title="ernie-c", runner="fake")
        )
        found = await a_session(service, state=SessionState.IDLE)
        agent = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
        assert agent is not None
        turns = await a_turns(service)
        reset_waiting()
        other = dispatcher.TurnLease(
            service.db, session=found.id, holder="another-host:4242:abcd", clock=clock
        )

        one = asyncio.create_task(turns.one(agent, found, "Go on"))
        async with asyncio.timeout(10):
            await WaitingRunner.started.wait()
            await clock.advance(dispatcher.TURN_TTL * 2)
            assert not await other.claim()
            WaitingRunner.go_on.set()
            ran = await one

        assert ran is not None
        assert ran.outcome is dispatcher.Outcome.DONE
        # (Given back when the turn ends.)
        assert await other.claim()


async def test_a_turn_that_is_cancelled_ends_and_gives_its_lease_back(
    tmp_path: Path,
) -> None:
    """A turn whose loop is cancelled (udl stopping) ends, and its session's lease goes with it:
    nothing is left waiting on a turn that will never finish (27.9)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, WaitingRunner)
    clock = FakeClock()
    async with UDLService(settings(tmp_path), registry=plugins, clock=clock) as service:
        await AgentRecords(AppRecords(service.db)).save_agent(
            Agent(name="ernie-c", title="ernie-c", runner="fake")
        )
        found = await a_session(service, state=SessionState.IDLE)
        agent = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
        assert agent is not None
        turns = await a_turns(service)
        reset_waiting()

        one = asyncio.create_task(turns.one(agent, found, "Go on"))
        async with asyncio.timeout(10):
            await WaitingRunner.started.wait()
            one.cancel()
            with pytest.raises(asyncio.CancelledError):
                await one
            WaitingRunner.go_on.set()

        # (The lease is back: the next turn takes it without waiting for anything.)
        sent = await turns.one(agent, found, "And again")

    assert sent is not None
    assert sent.outcome is dispatcher.Outcome.DONE


async def test_a_message_is_refused_while_another_process_turns_the_session(
    service: UDLService,
) -> None:
    """`orch_send` takes the turn's lease without waiting: another process in a turn of that
    session is a refusal, as its stored state is (27.9)."""
    found = await a_session(service)
    await service.db.claim_lease(
        f"orch-turn:{found.id}", "another-host:4242:abcd", timedelta(minutes=5)
    )

    with pytest.raises(NotAllowed, match="is in a turn"):
        await service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})


async def test_a_turn_whose_process_died_is_taken_over(tmp_path: Path) -> None:
    """A lease whose holder died mid-turn expires, and the next turn takes it: a restart's turn
    still starts (27.9)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, SharedRunner)
    clock = FakeClock()
    async with UDLService(settings(tmp_path), registry=plugins, clock=clock) as service:
        await AgentRecords(AppRecords(service.db)).save_agent(
            Agent(name="ernie-c", title="ernie-c", runner="fake")
        )
        found = await a_session(service)
        await service.db.claim_lease(
            f"orch-turn:{found.id}", "another-host:4242:abcd", timedelta(minutes=1)
        )
        await clock.advance(timedelta(minutes=2))

        sent = await service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})

    assert isinstance(sent, Sent)


async def test_a_session_stopped_mid_turn_stays_ended(tmp_path: Path) -> None:
    plugins = registry()
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, WaitingRunner)
    async with UDLService(settings(tmp_path), registry=plugins) as service:
        records = AppRecords(service.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="Q"))
        await TaskRecords(records).save_task(
            TaskRecord(project="udl", feature="F-1", key="F-1.1", title="One")
        )
        await AgentRecords(records).save_agent(
            Agent(name="ernie-c", title="ernie-c", runner="fake")
        )
        found = await a_session(service, state=SessionState.IDLE)

        sessions = SessionRecords(records)
        reset_waiting()
        sending = asyncio.create_task(
            service.call("orch_send", {"agent": "ernie-c", "text": "Go on"})
        )
        try:
            async with asyncio.timeout(10):
                await WaitingRunner.started.wait()
                # (A person stops it while the turn runs: the record is ended, as
                # `orch_stop_session` writes it, and the turn is let go.)
                await sessions.save_session(found.model_copy(update={"state": SessionState.ENDED}))
                WaitingRunner.go_on.set()
                with pytest.raises(NotAllowed, match="ended while the message"):
                    await sending
        finally:
            WaitingRunner.go_on.set()  # (a failing test leaves no turn waiting)
            if not sending.done():
                sending.cancel()

        stored = await SessionRecords(records).session(found.id)
        assert stored is not None
        assert stored.state is SessionState.ENDED
        assert stored.turns == 3  # (the turn never finished, so it did not count)


# ---------------------------------------------------------------------------------------------
# Fixtures' own helpers
# ---------------------------------------------------------------------------------------------


async def a_session(
    service: UDLService, *, state: SessionState = SessionState.IDLE, turns: int = 3
) -> AgentSession:
    """A session of `ernie-c` that has taken three turns, as its dispatcher left it: a real one,
    started on the harness, so a turn sent to it runs (three events, one per turn)."""
    records = AppRecords(service.db)
    sessions = SessionRecords(records)
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    runner = await app.harnesses.runner("local", "fake")
    ref = await runner.start(SessionSpec(cwd="/work"))
    found = await sessions.save_session(
        AgentSession(agent="ernie-c", task="F-1.1", ref=ref, state=state, turns=turns)
    )
    for turn in range(turns):
        await sessions.save_event(
            stored_event(found, turn=turn, event=TextDelta(text=f"turn {turn}"))
        )
    return found


async def a_turns(service: UDLService) -> dispatcher.Turns:
    """A `Turns` of the test's own, over the service's database: a second holder, as another
    process's would be (its in-process lock is not this process's, which the lease must not
    rely on)."""
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    return dispatcher.Turns(
        records=AppRecords(service.db),
        events=AppEvents(service.bus, "orch", app.event_types()),
        harnesses=app.harnesses,
        clock=service.clock,
        settings=app.settings,
        database=service.db,
    )


async def queue_task(service: UDLService, key: str, agent: str, *, project: str = "udl") -> None:
    """Put the task `key` on `agent`'s queue."""
    await service.call("orch_assign", {"key": key, "agent": agent, "project": project})


async def reports_to(records: AppRecords, name: str, manager: str) -> None:
    """Make the agent called `name` report to `manager` (the one the fixture made)."""
    agents = AgentRecords(records)
    found = await agents.agent(name)
    assert found is not None
    await agents.save_agent(found.model_copy(update={"manager": manager}))


def stored_event(session: AgentSession, *, turn: int, event: AgentEvent) -> SessionEvent:
    """One thing said in `session`, as its transcript keeps it."""
    return SessionEvent(agent=session.agent, session=session.id, turn=turn, event=an_event(event))
