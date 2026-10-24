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
from support.accounts import Fakes, connected, with_fake_integration
from support.actions import fake_action
from support.clock import T0, FakeClock
from support.engine import databases, registry
from support.engine import settings as make_settings
from support.interfaces import Harness
from udl.automations.bus import BusNotice, EventBus, NewEvent, NoticeKind
from udl.automations.events import CurrentRun, EventRecord, EventStatus, EventTypes, current_run
from udl.automations.models import SetSubscriptionRequest
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.subscriptions import Subscription
from udl.config import AutomationsSettings
from udl.core.service import UDLService
from udl.db.manager import DBManager
from udl.db.records import RecordQuery
from udl.errors import ConfigError, NetworkError
from udl.plugins.actions.base import Action
from udl.plugins.integrations.base import Account, ConnectMethod
from udl.plugins.registry import ACTIONS


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


async def test_listeners_hear_of_an_event_another_udl_published_before_its_runs(
    tmp_path: Path,
) -> None:
    harness = Harness(tmp_path)
    async with harness.service() as server, harness.service() as cli:
        await action(server.db)
        await subscribe(server.db)
        listening = asyncio.create_task(listen(server.bus, 3))
        await asyncio.sleep(0)

        event = await cli.bus.publish(hello())
        assert event is not None
        await server.bus.dispatch_pending()

        heard = await asyncio.wait_for(listening, 1)

    assert [(notice.kind, notice.event_id) for notice in heard] == [
        (NoticeKind.EVENT, event.id),
        (NoticeKind.RUN, event.id),
        (NoticeKind.EVENT, event.id),
    ]
    assert heard[0].event is not None
    assert heard[0].event.status is EventStatus.PENDING


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


async def test_the_bus_remembers_a_bounded_number_of_events_it_told_of(
    bus: EventBus, db: DBManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("udl.automations.bus._REMEMBERED", 1)
    first = await bus.publish(hello(dedupe_key="1"))
    await bus.publish(hello(dedupe_key="2"))
    assert first is not None
    listening = asyncio.create_task(listen(bus, 2))
    await asyncio.sleep(0)

    await bus.dispatch(first)  # (forgotten: told of again)

    heard = await asyncio.wait_for(listening, 1)
    assert [notice.event.status for notice in heard if notice.event] == [
        EventStatus.PENDING,
        EventStatus.DISPATCHED,
    ]


# ---------------------------------------------------------------------------------------------
# Source filters
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def fakes(tmp_path: Path) -> tuple[Harness, Fakes]:
    harness = Harness(tmp_path)
    harness.registry.register(ACTIONS, fake_action("fake"))
    return harness, with_fake_integration(harness)


@pytest.fixture
async def service(fakes: tuple[Harness, Fakes]) -> AsyncIterator[UDLService]:
    async with fakes[0].service() as found:
        await found.db.upsert_record(Action(name="hello", plugin="fake"))
        yield found


async def thing(service: UDLService, account: Account, text: str) -> EventRecord:
    published = await service.bus.publish(
        NewEvent(
            type="fake.thing.happened",
            data={"thing": text, "text": text},
            source="fake",
            account_id=account.id,
            dedupe_key=f"{account.id}:{text}",
        )
    )
    assert published is not None
    return published


async def save_subscription(service: UDLService, **fields: Any) -> None:
    await service.set_subscription(
        SetSubscriptionRequest.model_validate(
            {"name": "x-things", "events": ["fake.thing.happened"], "actions": ["hello"], **fields}
        )
    )


@pytest.mark.parametrize(
    ("source_filter", "problem"),
    [
        ({"contains": 1}, "contains"),
        ({"contains": "x", "more": True}, "more"),
        ({}, "contains"),
    ],
)
async def test_a_source_filter_is_checked_by_its_integration_when_saved(
    service: UDLService, source_filter: dict[str, Any], problem: str
) -> None:
    with pytest.raises(ConfigError, match=problem):
        await save_subscription(service, source_filter=source_filter)


async def test_a_source_filter_s_events_are_checked_by_their_integration(
    service: UDLService, fakes: tuple[Harness, Fakes]
) -> None:
    work = await connected(service, method=ConnectMethod.NONE, options={"source": "w"})
    await save_subscription(service, source_filter={"contains": "x"})

    wanted = await service.bus.dispatch(await thing(service, work, "has x"))
    unwanted = await service.bus.dispatch(await thing(service, work, "has none"))

    assert [run.status for run in wanted] == [RunStatus.QUEUED]
    assert unwanted == []
    assert fakes[1].plugin.filtered == ["has x", "has none"]


async def test_a_source_filter_is_checked_after_the_filter(
    service: UDLService, fakes: tuple[Harness, Fakes]
) -> None:
    work = await connected(service, method=ConnectMethod.NONE, options={"source": "w"})
    await save_subscription(
        service, filter='text startswith "has"', source_filter={"contains": "x"}
    )

    await service.bus.dispatch(await thing(service, work, "no x"))

    assert fakes[1].plugin.filtered == []  # (the filter said no first)


async def test_a_source_filter_s_answer_is_kept_for_its_event(
    service: UDLService, fakes: tuple[Harness, Fakes]
) -> None:
    work = await connected(service, method=ConnectMethod.NONE, options={"source": "w"})
    await save_subscription(service, source_filter={"contains": "x"})
    event = await thing(service, work, "has x")

    await service.bus.dispatch(event)
    again = await service.bus.dispatch(event, replay=True)

    assert len(again) == 1
    assert fakes[1].plugin.filtered == ["has x"]


async def test_a_source_filter_that_cannot_be_checked_skips_the_runs(
    service: UDLService, fakes: tuple[Harness, Fakes]
) -> None:
    work = await connected(service, method=ConnectMethod.NONE, options={"source": "w"})
    await save_subscription(service, source_filter={"contains": "x"})
    fakes[1].plugin.source_filter_error = NetworkError("The service is down")

    [run] = await service.bus.dispatch(await thing(service, work, "has x"))

    assert run.status is RunStatus.SKIPPED
    assert run.skipped_reason == "source filter failed: The service is down"


async def test_a_source_filter_is_not_guessed_for_an_event_without_its_account(
    service: UDLService,
) -> None:
    await save_subscription(service, source_filter={"contains": "x"})
    gone = Account(integration="fake", name="gone", method=ConnectMethod.NONE)

    [run] = await service.bus.dispatch(await thing(service, gone, "has x"))

    assert run.skipped_reason is not None
    assert run.skipped_reason.startswith("source filter failed: the event's account")


async def test_a_bus_that_no_integration_helps_skips_source_filtered_runs(
    db: DBManager, clock: FakeClock
) -> None:
    bus = bus_on(db, clock)
    await action(db)
    await subscribe(db, events=["custom.hello"], source_filter={"contains": "x"})
    event = await bus.publish(hello())
    assert event is not None

    [run] = await bus.dispatch(event)

    assert run.skipped_reason == "source filter failed: nothing here checks source filters"


async def test_a_source_filter_s_answers_are_kept_for_the_latest_events_only(
    service: UDLService, fakes: tuple[Harness, Fakes], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("udl.automations.bus._REMEMBERED", 1)
    work = await connected(service, method=ConnectMethod.NONE, options={"source": "w"})
    await save_subscription(service, source_filter={"contains": "x"})
    first, second = await thing(service, work, "x 1"), await thing(service, work, "x 2")
    await service.bus.dispatch(first)
    await service.bus.dispatch(second)

    await service.bus.dispatch(first, replay=True)

    assert fakes[1].plugin.filtered == ["x 1", "x 2", "x 1"]  # (asked again: forgotten)


@pytest.mark.parametrize(
    ("source_filter", "problem"),
    [
        ({"contains": 1}, "it is not valid: contains"),  # (as saved before it changed)
        ({"contains": "x"}, "The plain integration has no source filter"),
    ],
)
async def test_a_source_filter_its_integration_does_not_take_skips_the_runs(
    tmp_path: Path, source_filter: dict[str, Any], problem: str
) -> None:
    harness = Harness(tmp_path)
    harness.registry.register(ACTIONS, fake_action("fake"))
    plain = with_fake_integration(harness, "plain").plugin
    if "no source filter" in problem:
        plain.source_filter_model = None
    async with harness.service() as service:
        await service.db.upsert_record(Action(name="hello", plugin="fake"))
        work = await connected(
            service, integration="plain", method=ConnectMethod.NONE, options={"source": "w"}
        )
        await service.db.upsert_record(  # (as saved before it changed)
            Subscription(
                name="x", events=["plain.*"], source_filter=source_filter, actions=["hello"]
            )
        )
        event = await service.bus.publish(
            NewEvent(
                type="plain.thing.happened",
                data={"thing": "t", "text": "x"},
                account_id=work.id,
            )
        )
        assert event is not None

        [run] = await service.bus.dispatch(event)

    assert run.skipped_reason is not None
    assert problem in run.skipped_reason


async def test_a_source_filter_for_an_integration_without_one_is_refused(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    plain = with_fake_integration(harness, "plain").plugin
    plain.source_filter_model = None
    async with harness.service() as service:
        await service.db.upsert_record(Action(name="hello", plugin="fake"))

        with pytest.raises(ConfigError, match="The plain integration has no source filter"):
            await service.set_subscription(
                SetSubscriptionRequest(
                    name="x", events=["plain.*"], source_filter={"contains": "x"}, actions=["hello"]
                )
            )
