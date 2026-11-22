"""
----------------
- test_events.py -
----------------

28.5's dispatcher half: an agent with nothing on its queue says so, once per idle stretch — and
not on every tick, since a dispatcher ticks every 30 seconds and an agent that is idle is idle
all of them.

The other events are asserted where they happen: a task's move and its `blocked`/`merged`
(tests/unit/plugins/apps/orch/test_events.py, the one write every move goes through), a
question's asking and answer (the same file), a pull's readiness (test_readiness_operations.py),
a session's start, events and end (test_dispatcher.py), and a link's change (test_links.py).
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.engine import settings
from support.orch import orch_registry
from udl.automations.events import EventRecord
from udl.automations.models import EventInfo, EventList
from udl.config import APISettings
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.plugins.apps.base import AppContext, AppEvents, AppRecords
from udl.plugins.apps.orch.dispatcher import Dispatcher
from udl.plugins.apps.orch.models import MergePolicy
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import Agent, AgentRecords, Project, ProjectRecords, Task

AGENT = "deepseek-b"


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project and one agent, whose queue is empty."""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
    ) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", merge=MergePolicy.HUMAN)
        )
        await AgentRecords(records).save_agent(Agent(name=AGENT, title=AGENT, runner="fake"))
        yield running


async def dispatcher(service: UDLService, *, clock: FakeClock) -> Dispatcher:
    """A dispatcher over `service`, with a test's own `prepare()` (a path, no git)."""
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    ctx = AppContext(
        app=app,
        service=service,
        records=AppRecords(service.db, events=AppEvents(service.bus, "orch", Orch.event_types())),
        events=AppEvents(service.bus, "orch", Orch.event_types()),
        secrets=service.secrets,
        clock=clock,
        settings=app.settings,
        stop=asyncio.Event(),
    )
    return Dispatcher(
        ctx,
        harnesses=app.harnesses,
        prepare=fake_prepare,
        tick=timedelta(seconds=30),
    )


async def fake_prepare(task: Task, agent: Agent) -> Task:
    """A worktree for `task` on `agent`'s host: a path of its own, and its branch."""
    return task.model_copy(
        update={"worktree": f"/work/{task.key}", "branch": f"feat/{task.key}-{task.title}"}
    )


async def idle_events(service: UDLService) -> list[EventRecord]:
    """The `orch.agent.idle` events stored so far, oldest first."""
    found = await service.db.list_records(
        EventRecord, RecordQuery(where={"type": "orch.agent.idle"})
    )
    return sorted(found, key=lambda one: one.occurred_at)


async def test_an_agent_with_nothing_on_its_queue_says_it_is_idle_once(
    service: UDLService,
) -> None:
    clock = FakeClock()
    found = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(4):  # (four ticks of an agent with nothing to do)
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    published = await idle_events(service)
    assert len(published) == 1
    assert published[0].data == {"agent": AGENT, "project": "udl"}
    assert AGENT in published[0].subject


async def test_the_listing_of_events_reads_it_as_any_other(service: UDLService) -> None:
    """(A subscriber finds it through `list_events`, as `udl events types` lists it.)"""
    clock = FakeClock()
    found = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    listed = await service.call("list_events", {"type": "orch.agent.idle"})
    assert isinstance(listed, EventList)
    assert [one.type for one in listed.events] == ["orch.agent.idle"]
    assert isinstance(listed.events[0], EventInfo)
    assert listed.events[0].data["agent"] == AGENT
