"""
---------------
- test_bus.py -
---------------

The event bus: publishing stores an event (validated by its type, deduplicated by its key,
even after a restart) before anything is dispatched; dispatching queues a run for each action
of each subscription that matches (once, however often it is dispatched again), drops an event
deeper than max_event_depth, and says what happens to whoever listens (the live stream)
"""

# System Imports
import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

# Package Imports
import pytest

# Local Imports
from support.clock import T0, FakeClock
from support.engine import databases, registry
from support.engine import settings as make_settings
from support.interfaces import Harness
from udl.automations.bus import BusNotice, EventBus, NewEvent, NoticeKind
from udl.automations.events import CurrentRun, EventRecord, EventStatus, EventTypes, current_run
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.subscriptions import Subscription
from udl.config import AutomationsSettings
from udl.db.manager import DBManager
from udl.db.records import RecordQuery
from udl.errors import ConfigError
from udl.plugins.actions.base import Action


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DBManager]:
    found = databases(make_settings(tmp_path), registry())
    async with found:
        yield found


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def bus_on(db: DBManager, clock: FakeClock, **settings: Any) -> EventBus:
    return EventBus(db, EventTypes.default(), clock, AutomationsSettings(**settings))


@pytest.fixture
def bus(db: DBManager, clock: FakeClock) -> EventBus:
    return bus_on(db, clock)


async def action(db: DBManager, name: str = "hello", **fields: Any) -> Action:
    return await db.upsert_record(Action(name=name, plugin="fake", **fields))


async def subscribe(db: DBManager, name: str = "greet", **fields: Any) -> Subscription:
    fields.setdefault("events", ["custom.hello"])
    fields.setdefault("actions", ["hello"])
    return await db.upsert_record(Subscription.model_validate({"name": name, **fields}))


def hello(**fields: Any) -> NewEvent:
    return NewEvent.model_validate({"type": "custom.hello", "data": {"name": "x"}, **fields})


async def runs(db: DBManager) -> list[ActionRun]:
    return list(reversed(await db.list_records(ActionRun, RecordQuery())))


# ---------------------------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------------------------


async def test_an_event_is_stored_as_pending(bus: EventBus, db: DBManager) -> None:
    stored = await bus.publish(hello(dedupe_key="k", source="api"))

    assert stored is not None
    assert stored.status is EventStatus.PENDING
    assert (stored.type, stored.source, stored.data, stored.depth) == (
        "custom.hello",
        "api",
        {"name": "x"},
        0,
    )
    assert stored.occurred_at == T0
    assert await db.get_record(EventRecord, stored.id) == stored


async def test_publishing_dispatches_nothing(bus: EventBus, db: DBManager) -> None:
    await action(db)
    await subscribe(db)

    await bus.publish(hello())

    assert await runs(db) == []


async def test_an_event_s_data_is_validated_by_its_type(bus: EventBus) -> None:
    with pytest.raises(ConfigError, match=r"udl\.download\.finished"):
        await bus.publish(NewEvent(type="udl.download.finished", data={"url": 1}))


async def test_an_unknown_type_is_refused(bus: EventBus) -> None:
    with pytest.raises(ConfigError, match=r"nothing\.at\.all"):
        await bus.publish(NewEvent(type="nothing.at.all"))


async def test_an_event_s_subject_is_written_from_its_data(bus: EventBus) -> None:
    stored = await bus.publish(
        NewEvent(
            type="udl.download.finished",
            data={
                "download": str(uuid4()),
                "batch": str(uuid4()),
                "url": "https://example.com/a",
                "site": "example.com",
                "title": "A video",
            },
        )
    )

    assert stored is not None
    assert stored.subject == "Downloaded A video"
    assert stored.data["files"] == []  # (as its type's model has it)


async def test_an_event_seen_before_is_not_stored_again(
    bus: EventBus, db: DBManager, clock: FakeClock
) -> None:
    first = await bus.publish(hello(dedupe_key="mail-1"))

    again = await bus.publish(hello(dedupe_key="mail-1"))
    after_a_restart = await bus_on(db, clock).publish(hello(dedupe_key="mail-1"))
    other = await bus.publish(hello(dedupe_key="mail-2"))

    assert first is not None
    assert (again, after_a_restart) == (None, None)
    assert other is not None
    assert len(await db.list_records(EventRecord, RecordQuery())) == 2


async def test_what_a_run_publishes_is_caused_by_it_and_deeper(bus: EventBus) -> None:
    run_id = uuid4()
    token = current_run.set(CurrentRun(id=run_id, depth=2, action="hello"))
    try:
        inside = await bus.publish(hello())
    finally:
        current_run.reset(token)
    outside = await bus.publish(hello())

    assert inside is not None
    assert outside is not None
    assert (inside.caused_by, inside.depth) == (run_id, 3)
    assert (outside.caused_by, outside.depth) == (None, 0)


# ---------------------------------------------------------------------------------------------
# Dispatching
# ---------------------------------------------------------------------------------------------


async def test_each_matched_action_gets_a_run_of_its_own(
    bus: EventBus, db: DBManager, clock: FakeClock
) -> None:
    hi = await action(db, "hello")
    log = await action(db, "log-it")
    greet = await subscribe(db, actions=["hello", "log-it"])
    event = await bus.publish(hello())
    assert event is not None
    clock_then = clock.now()

    queued = await bus.dispatch(event)

    assert {(run.action_id, run.action) for run in queued} == {(hi.id, "hello"), (log.id, "log-it")}
    for run in queued:
        assert run.status is RunStatus.QUEUED
        assert (run.event_id, run.event_type, run.subscription_id) == (
            event.id,
            "custom.hello",
            greet.id,
        )
        assert (run.subscription, run.plugin, run.depth) == ("greet", "fake", 0)
    stored = await db.get_record(EventRecord, event.id)
    assert stored is not None
    assert stored.status is EventStatus.DISPATCHED
    assert stored.dispatched_at == clock_then


async def test_two_subscriptions_on_one_event_give_two_runs(bus: EventBus, db: DBManager) -> None:
    await action(db)
    await subscribe(db, "one")
    await subscribe(db, "two", events=["custom.*"])
    await subscribe(db, "other", events=["custom.bye"])
    await subscribe(db, "off", enabled=False)
    event = await bus.publish(hello())
    assert event is not None

    queued = await bus.dispatch(event)

    assert sorted(run.subscription or "" for run in queued) == ["one", "two"]


async def test_a_subscription_s_filter_picks_its_events(bus: EventBus, db: DBManager) -> None:
    await action(db)
    await subscribe(db, filter='name == "y"')
    event = await bus.publish(hello())
    assert event is not None

    assert await bus.dispatch(event) == []


async def test_dispatching_again_queues_nothing_twice(bus: EventBus, db: DBManager) -> None:
    await action(db)
    await subscribe(db)
    event = await bus.publish(hello())
    assert event is not None

    first = await bus.dispatch(event)
    again = await bus.dispatch(event)  # (as after a crash, before it was marked dispatched)

    assert len(first) == 1
    assert again == []
    assert len(await runs(db)) == 1


async def test_a_replay_is_a_new_dispatch(bus: EventBus, db: DBManager) -> None:
    await action(db)
    await subscribe(db, "one")
    await subscribe(db, "two")
    event = await bus.publish(hello())
    assert event is not None
    first = await bus.dispatch(event)

    replayed = await bus.dispatch(event, replay=True)
    just_one = await bus.dispatch(event, replay=True, subscription="two")

    assert len(first) == len(replayed) == 2
    assert {run.dispatch for run in replayed}.isdisjoint({run.dispatch for run in first})
    assert [run.subscription for run in just_one] == ["two"]
    assert len(await runs(db)) == 5


async def test_a_disabled_action_s_run_is_skipped(bus: EventBus, db: DBManager) -> None:
    await action(db, enabled=False)
    await subscribe(db)
    event = await bus.publish(hello())
    assert event is not None

    [run] = await bus.dispatch(event)

    assert run.status is RunStatus.SKIPPED
    assert run.skipped_reason == "the action is disabled"
    assert run.finished_at is not None


async def test_an_action_that_is_gone_gets_no_run(
    bus: EventBus, db: DBManager, caplog: pytest.LogCaptureFixture
) -> None:
    await subscribe(db, actions=["gone"])
    event = await bus.publish(hello())
    assert event is not None

    assert await bus.dispatch(event) == []
    assert "gone" in caplog.text


async def test_an_event_too_deep_is_dropped_not_dispatched(
    db: DBManager, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    bus = bus_on(db, clock, max_event_depth=2)
    await action(db)
    await subscribe(db)
    deep = await bus.publish(hello(depth=3))
    assert deep is not None

    with caplog.at_level(logging.WARNING):
        queued = await bus.dispatch(deep)

    stored = await db.get_record(EventRecord, deep.id)
    assert stored is not None
    assert queued == []
    assert stored.status is EventStatus.DROPPED
    assert stored.dropped_reason is not None
    assert "max_event_depth" in stored.dropped_reason
    assert "max_event_depth" in caplog.text


async def test_pending_events_are_dispatched_oldest_first(
    bus: EventBus, db: DBManager, clock: FakeClock
) -> None:
    await action(db)
    await subscribe(db)
    published: list[UUID] = []
    for n in range(3):
        found = await bus.publish(hello(dedupe_key=str(n)))
        assert found is not None
        published.append(found.id)
        await clock.advance(timedelta(seconds=1))

    assert await bus.dispatch_pending() == 3

    assert [run.event_id for run in await runs(db)] == published
    assert await bus.dispatch_pending() == 0


async def test_pending_events_are_dispatched_in_batches(
    bus: EventBus, db: DBManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("udl.automations.bus.BATCH", 2)
    for n in range(5):
        await bus.publish(hello(dedupe_key=str(n)))

    assert await bus.dispatch_pending() == 5

    pending = await db.list_records(EventRecord, RecordQuery(where={"status": "pending"}))
    assert pending == []


async def test_a_change_to_the_subscriptions_is_seen(bus: EventBus, db: DBManager) -> None:
    await action(db)
    first = await bus.publish(hello(dedupe_key="1"))
    assert first is not None
    assert await bus.dispatch(first) == []

    await subscribe(db)
    second = await bus.publish(hello(dedupe_key="2"))
    assert second is not None
    without_reloading = await bus.dispatch(second)
    bus.subscriptions_changed()
    third = await bus.publish(hello(dedupe_key="3"))
    assert third is not None

    assert without_reloading == []  # (cached: another process's change is seen by
    assert len(await bus.dispatch(third)) == 1  # dispatch_pending, this one's at once)


async def test_an_event_another_udl_published_is_dispatched_here(tmp_path: Path) -> None:
    harness = Harness(tmp_path)

    async with harness.service() as cli, harness.service() as server:
        await action(server.db)
        await subscribe(server.db)
        await cli.bus.publish(hello())

        assert await server.bus.dispatch_pending() == 1
        assert len(await runs(server.db)) == 1


def test_every_service_has_a_bus(tmp_path: Path) -> None:
    service = Harness(tmp_path).service()

    assert isinstance(service.bus, EventBus)
    assert service.bus.types is service.event_types


# ---------------------------------------------------------------------------------------------
# What listeners hear
# ---------------------------------------------------------------------------------------------


async def listen(bus: EventBus, count: int, **filters: Any) -> list[BusNotice]:
    heard: list[BusNotice] = []
    async for notice in bus.notices(**filters):
        heard.append(notice)
        if len(heard) == count:
            break
    return heard


async def test_listeners_hear_events_and_runs_in_order(bus: EventBus, db: DBManager) -> None:
    await action(db)
    await subscribe(db)
    listening = asyncio.create_task(listen(bus, 3))
    await asyncio.sleep(0)

    event = await bus.publish(hello())
    assert event is not None
    [run] = await bus.dispatch(event)

    heard = await asyncio.wait_for(listening, 1)
    assert [(notice.kind, notice.event_id) for notice in heard] == [
        (NoticeKind.EVENT, event.id),
        (NoticeKind.RUN, event.id),
        (NoticeKind.EVENT, event.id),
    ]
    assert heard[0].event is not None
    assert heard[0].event.status is EventStatus.PENDING
    assert heard[1].run == run
    assert heard[2].event is not None
    assert heard[2].event.status is EventStatus.DISPATCHED


async def test_listeners_hear_only_the_types_they_ask_for(bus: EventBus, db: DBManager) -> None:
    await action(db)
    await subscribe(db)
    listening = asyncio.create_task(listen(bus, 1, types="custom.bye", runs=False))
    await asyncio.sleep(0)

    event = await bus.publish(hello())
    assert event is not None
    await bus.dispatch(event)
    await bus.publish(NewEvent(type="custom.bye"))

    [heard] = await asyncio.wait_for(listening, 1)
    assert heard.event is not None
    assert heard.event.type == "custom.bye"


async def test_a_listener_hears_what_comes_after_it_caught_up(bus: EventBus) -> None:
    listening = asyncio.create_task(listen(bus, 2))
    await asyncio.sleep(0)

    await bus.publish(hello(dedupe_key="1"))
    await asyncio.sleep(0.01)  # (it hears it, and waits again)
    await bus.publish(hello(dedupe_key="2"))

    heard = await asyncio.wait_for(listening, 1)
    assert [notice.event.dedupe_key for notice in heard if notice.event] == ["1", "2"]


async def test_a_slow_listener_hears_that_it_missed_some(bus: EventBus) -> None:
    notices = bus.notices()
    first = asyncio.create_task(anext(notices))
    await asyncio.sleep(0)
    for n in range(600):
        await bus.publish(hello(dedupe_key=str(n)))

    lagged = await asyncio.wait_for(first, 1)
    following = await anext(notices)
    await notices.aclose()

    assert lagged.kind is NoticeKind.LAGGED
    assert following.event is not None
    assert following.event.dedupe_key == "100"  # (the oldest 100 were dropped)
