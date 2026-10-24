"""
-----------------
- test_loops.py -
-----------------

No subscription can make udl run for ever: what a run publishes is one deeper than its event,
and an event deeper than max_event_depth is dropped; a subscription's throttle skips the runs
over it, saying why
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.interfaces import Harness
from udl.automations.bus import NewEvent
from udl.automations.events import EventRecord, EventStatus
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.subscriptions import Subscription
from udl.config import AutomationsSettings
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.plugins.actions.base import Action
from udl.plugins.actions.emit import EmitAction
from udl.plugins.registry import ACTIONS


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def service(tmp_path: Path, clock: FakeClock) -> AsyncIterator[UDLService]:
    harness = Harness(tmp_path, clock=clock, automations=AutomationsSettings(max_event_depth=3))
    harness.registry.register(ACTIONS, EmitAction)
    async with harness.service() as found:
        yield found


async def configure(service: UDLService, action: Action, **subscription: Any) -> None:
    await service.db.upsert_record(action)
    subscription.setdefault("actions", [action.name])
    await service.db.upsert_record(Subscription.model_validate(subscription))
    service.bus.subscriptions_changed()


async def settle(service: UDLService, clock: FakeClock) -> None:
    """Dispatch and run until nothing is left to do."""
    while await service.bus.dispatch_pending() or await service.runner.run_due():
        await service.runner.idle()
        await clock.advance(timedelta(seconds=1))


async def events_of(service: UDLService, type_: str) -> list[EventRecord]:
    found = await service.db.list_records(EventRecord, RecordQuery(where={"type": type_}))
    return sorted(found, key=lambda record: record.depth)


async def runs_of(service: UDLService) -> list[ActionRun]:
    return list(reversed(await service.db.list_records(ActionRun, RecordQuery())))


async def test_an_action_that_emits_what_it_subscribes_to_stops(
    service: UDLService, clock: FakeClock
) -> None:
    echo = Action(name="echo", plugin="emit", options={"type": "custom.ping"})
    await configure(service, echo, name="loop", events=["custom.ping"])

    await service.bus.publish(NewEvent(type="custom.ping"))
    await settle(service, clock)

    pings = await events_of(service, "custom.ping")
    assert [ping.depth for ping in pings] == [0, 1, 2, 3, 4]
    assert [ping.status for ping in pings] == [EventStatus.DISPATCHED] * 4 + [EventStatus.DROPPED]
    assert pings[-1].dropped_reason is not None
    assert "max_event_depth (3)" in pings[-1].dropped_reason
    runs = await runs_of(service)
    assert len(runs) == 4
    assert all(run.status is RunStatus.SUCCEEDED for run in runs)
    for ping, run in zip(pings[1:], runs, strict=True):
        assert ping.caused_by == run.id  # (each ping was emitted by the run before it)


async def test_a_run_s_end_is_one_deeper_than_its_event(
    service: UDLService, clock: FakeClock
) -> None:
    await configure(
        service,
        Action(name="hello", plugin="emit", options={"type": "custom.done"}),
        name="greet",
        events=["custom.hello"],
    )

    await service.bus.publish(NewEvent(type="custom.hello", depth=2))
    await settle(service, clock)

    [succeeded] = await events_of(service, "udl.action.succeeded")
    [done] = await events_of(service, "custom.done")
    assert (succeeded.depth, done.depth) == (3, 3)


async def test_a_throttle_skips_the_runs_over_it_saying_why(
    service: UDLService, clock: FakeClock
) -> None:
    await configure(
        service,
        Action(name="note", plugin="emit", options={"type": "custom.noted"}),
        name="notes",
        events=["custom.hello"],
        throttle="2 per 1h",
    )

    for n in range(3):
        await service.bus.publish(NewEvent(type="custom.hello", dedupe_key=str(n)))
        await service.bus.dispatch_pending()
        await clock.advance(timedelta(minutes=1))

    runs = await runs_of(service)
    assert [run.status for run in runs] == [RunStatus.QUEUED, RunStatus.QUEUED, RunStatus.SKIPPED]
    assert runs[-1].skipped_reason == "throttled: 2 per 1h"

    await clock.advance(timedelta(hours=1))
    await service.bus.publish(NewEvent(type="custom.hello", dedupe_key="later"))
    await service.bus.dispatch_pending()

    assert (await runs_of(service))[-1].status is RunStatus.QUEUED


async def test_a_throttle_counts_each_action_s_runs(service: UDLService) -> None:
    await service.db.upsert_record(Action(name="one", plugin="emit", options={"type": "custom.a"}))
    await configure(
        service,
        Action(name="two", plugin="emit", options={"type": "custom.b"}),
        name="both",
        events=["custom.hello"],
        actions=["one", "two"],
        throttle="1 per 1h",
    )

    await service.bus.publish(NewEvent(type="custom.hello"))
    await service.bus.dispatch_pending()

    assert sorted(run.status for run in await runs_of(service)) == [
        RunStatus.QUEUED,
        RunStatus.SKIPPED,
    ]
