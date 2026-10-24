"""
---------------------
- test_scheduler.py -
---------------------

The scheduler, on a FakeClock: it sleeps until the earliest schedule is due (a minute at most,
so another udl's changes are seen; at once when this one saves a schedule), fires
schedule.fired for each time once (a time fired twice, as after a crash between firing and
saving the next time, is deduplicated), then saves its next time. A time found more than a
minute late was missed (udl was stopped, the machine slept): the schedule's misfire policy
says what to do. Jitter delays a time, but never moves the time an event is for
"""

# System Imports
import asyncio
import contextlib
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

# Package Imports
import pytest

# Local Imports
from support.clock import T0, FakeClock
from support.engine import databases, registry
from support.engine import settings as make_settings
from udl.automations.bus import EventBus
from udl.automations.events import EventRecord, EventTypes, ScheduleFiredData
from udl.automations.scheduler import LATE_AFTER, Scheduler, dedupe_key
from udl.automations.schedules import Schedule
from udl.config import AutomationsSettings
from udl.db.manager import DBManager
from udl.db.records import RecordQuery

UTC_ZONE = ZoneInfo("UTC")
MINUTE = timedelta(minutes=1)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def db(tmp_path: Path, clock: FakeClock) -> AsyncIterator[DBManager]:
    found = databases(make_settings(tmp_path), registry(), clock=clock)
    async with found:
        yield found


def scheduler_on(db: DBManager, clock: FakeClock, **settings: Any) -> Scheduler:
    automations = AutomationsSettings(**settings)
    bus = EventBus(db, EventTypes.default(), clock, automations)
    return Scheduler(db, bus, clock, automations)


@pytest.fixture
def scheduler(db: DBManager, clock: FakeClock) -> Scheduler:
    return scheduler_on(db, clock)


@contextlib.asynccontextmanager
async def serving(scheduler: Scheduler, clock: FakeClock) -> AsyncGenerator[None]:
    """The scheduler, running (until the block ends)."""
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.serve(stop))
    await clock.advance(timedelta(0))
    try:
        yield
    finally:
        stop.set()
        await clock.advance(timedelta(0))
        await asyncio.wait_for(task, 1)


async def save(db: DBManager, clock: FakeClock, **fields: Any) -> Schedule:
    """A schedule, saved as set_schedule saves one (planned from now), unless it says its next
    time."""
    fields.setdefault("name", "tick")
    fields.setdefault("spec", {"every": "1m"})
    made = Schedule.model_validate(fields)
    if "next_run_at" not in fields:
        made = made.planned(clock.now(), made.zone(""))
    return await db.upsert_record(made)


async def fired(db: DBManager) -> list[ScheduleFiredData]:
    """What the schedule.fired events say, by the time they were for."""
    found = await db.list_records(EventRecord, RecordQuery(where={"type": "schedule.fired"}))
    return sorted(
        (ScheduleFiredData.model_validate(event.data) for event in found),
        key=lambda data: (data.scheduled_for, data.fired_at),
    )


async def stored(db: DBManager, schedule: Schedule) -> Schedule:
    found = await db.get_record(Schedule, schedule.id)
    assert found is not None
    return found


# ---------------------------------------------------------------------------------------------
# Firing
# ---------------------------------------------------------------------------------------------


async def test_each_due_time_fires_once(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    tick = await save(db, clock, data={"n": 1})

    async with serving(scheduler, clock):
        await clock.advance(5 * MINUTE)

    found = await fired(db)
    assert [data.scheduled_for for data in found] == [T0 + n * MINUTE for n in range(1, 6)]
    assert all(data.fired_at == data.scheduled_for for data in found)
    assert all((data.schedule, data.data, data.late) == ("tick", {"n": 1}, False) for data in found)
    after = await stored(db, tick)
    assert (after.next_run_at, after.last_run_at) == (T0 + 6 * MINUTE, T0 + 5 * MINUTE)


async def test_a_fired_event_is_the_scheduler_s_and_keyed_by_its_time(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    tick = await save(db, clock)

    async with serving(scheduler, clock):
        await clock.advance(MINUTE)

    [event] = await db.list_records(EventRecord, RecordQuery(where={"type": "schedule.fired"}))
    assert (event.source, event.dedupe_key) == ("scheduler", dedupe_key(tick, T0 + MINUTE))
    assert event.dedupe_key == f"schedule:{tick.id}:{(T0 + MINUTE).isoformat()}"
    assert event.subject == "The tick schedule fired"


async def test_a_time_fired_before_a_crash_is_not_fired_again(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    # (It fired at T0 and stopped before saving its next time.)
    tick = await save(db, clock, next_run_at=T0)
    await scheduler.fire(tick, T0, now=T0)

    async with serving(scheduler, clock):
        await clock.advance(timedelta(seconds=1))

    assert [data.scheduled_for for data in await fired(db)] == [T0]
    assert (await stored(db, tick)).next_run_at == T0 + MINUTE


async def test_a_disabled_schedule_does_not_fire(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    await save(db, clock, enabled=False, next_run_at=T0 + MINUTE)

    async with serving(scheduler, clock):
        await clock.advance(5 * MINUTE)

    assert await fired(db) == []


async def test_an_at_schedule_fires_once_and_is_disabled(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    once = await save(db, clock, spec={"at": (T0 + MINUTE).isoformat()})

    async with serving(scheduler, clock):
        await clock.advance(10 * MINUTE)

    assert [data.scheduled_for for data in await fired(db)] == [T0 + MINUTE]
    after = await stored(db, once)
    assert (after.enabled, after.next_run_at, after.last_run_at) == (False, None, T0 + MINUTE)


async def test_cron_schedules_fire_in_their_zone(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    # (T0 is 2026-01-02 03:04:05 UTC: 22:04 the day before in New York.)
    await save(db, clock, spec={"cron": "0 23 * * *"}, timezone="America/New_York")

    async with serving(scheduler, clock):
        await clock.advance(timedelta(hours=1))

    [data] = await fired(db)
    assert data.scheduled_for == datetime(2026, 1, 2, 4, tzinfo=T0.tzinfo)


async def test_a_schedule_deleted_while_it_fires_is_not_saved_again(
    db: DBManager, clock: FakeClock, scheduler: Scheduler, monkeypatch: pytest.MonkeyPatch
) -> None:
    tick = await save(db, clock)
    published = scheduler.bus.publish

    async def publish_then_delete(new: Any) -> Any:
        stored_event = await published(new)
        await db.delete_record(Schedule, tick.id)
        return stored_event

    monkeypatch.setattr(scheduler.bus, "publish", publish_then_delete)
    async with serving(scheduler, clock):
        await clock.advance(MINUTE)

    assert await db.get_record(Schedule, tick.id) is None
    assert len(await fired(db)) == 1


# ---------------------------------------------------------------------------------------------
# Waking
# ---------------------------------------------------------------------------------------------


async def test_a_schedule_saved_here_wakes_the_scheduler(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    await save(db, clock, name="later", spec={"every": "1d"})

    async with serving(scheduler, clock):
        await clock.advance(timedelta(seconds=1))
        await save(db, clock, spec={"at": (clock.now() + timedelta(seconds=5)).isoformat()})
        scheduler.changed()
        await clock.advance(timedelta(seconds=5))

        assert [data.scheduled_for for data in await fired(db)] == [T0 + timedelta(seconds=6)]


async def test_a_schedule_saved_elsewhere_is_seen_within_a_minute(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    await save(db, clock, name="later", spec={"every": "1d"})

    async with serving(scheduler, clock):
        await clock.advance(timedelta(seconds=1))
        await save(db, clock, spec={"at": (clock.now() + timedelta(seconds=5)).isoformat()})
        await clock.advance(timedelta(seconds=30))

        assert await fired(db) == []  # (it sleeps on...)

        await clock.advance(timedelta(seconds=30))

        [data] = await fired(db)  # (...a minute at most)
        assert data.scheduled_for == T0 + timedelta(seconds=6)
        assert data.fired_at == T0 + MINUTE


async def test_the_scheduler_stops_when_told(clock: FakeClock, scheduler: Scheduler) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler.serve(stop))
    await clock.advance(timedelta(0))

    stop.set()

    await asyncio.wait_for(task, 1)
    assert clock.sleepers == 0


# ---------------------------------------------------------------------------------------------
# Jitter
# ---------------------------------------------------------------------------------------------


async def test_jitter_delays_a_time_within_its_bound_but_not_what_it_is_for(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    tick = await save(
        db,
        clock,
        spec={"every": "1h"},
        jitter="10m",
        id=UUID("00000000-0000-4000-8000-000000000009"),
    )
    due = tick.due_at()
    assert due is not None
    assert tick.next_run_at is not None
    assert tick.next_run_at < due <= tick.next_run_at + timedelta(minutes=10)

    async with serving(scheduler, clock):
        await clock.advance(due - clock.now() - timedelta(seconds=1))
        assert await fired(db) == []
        await clock.advance(timedelta(seconds=1))

        [data] = await fired(db)
        assert (data.scheduled_for, data.fired_at) == (tick.next_run_at, due)


# ---------------------------------------------------------------------------------------------
# Misfires
# ---------------------------------------------------------------------------------------------


async def test_a_time_missed_while_udl_was_stopped_is_skipped(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    hourly = await save(db, clock, spec={"cron": "0 * * * *"}, next_run_at=T0 - timedelta(hours=2))

    async with serving(scheduler, clock):
        await clock.advance(timedelta(seconds=1))

        assert await fired(db) == []
        assert (await stored(db, hourly)).next_run_at == T0.replace(minute=0, second=0) + (
            timedelta(hours=1)
        )


async def test_the_latest_time_missed_fires_once_late_when_its_misfire_is_once(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    hourly = await save(
        db,
        clock,
        spec={"cron": "0 * * * *"},
        misfire="once",
        next_run_at=T0 - timedelta(hours=2),
    )

    async with serving(scheduler, clock):
        await clock.advance(timedelta(seconds=1))

        [data] = await fired(db)
        assert (data.scheduled_for, data.late, data.fired_at) == (
            T0.replace(minute=0, second=0),
            True,
            T0,
        )
        after = await stored(db, hourly)
        assert (after.next_run_at, after.last_run_at) == (
            T0.replace(minute=0, second=0) + timedelta(hours=1),
            T0,
        )


async def test_a_time_found_less_than_a_minute_late_fires_as_usual(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    await save(db, clock, next_run_at=T0 - LATE_AFTER)

    async with serving(scheduler, clock):
        await clock.advance(timedelta(seconds=1))

    [first, *_] = await fired(db)
    assert (first.scheduled_for, first.late) == (T0 - LATE_AFTER, False)


# ---------------------------------------------------------------------------------------------
# Planning on start
# ---------------------------------------------------------------------------------------------


async def test_on_start_next_times_are_planned_again_in_today_s_zone(
    db: DBManager, clock: FakeClock
) -> None:
    # (Planned while [automations] timezone was UTC, which is now New York.)
    daily = await save(
        db, clock, spec={"cron": "0 8 * * *"}, next_run_at=T0.replace(hour=8, minute=0, second=0)
    )

    async with serving(scheduler_on(db, clock, timezone="America/New_York"), clock):
        pass

    assert (await stored(db, daily)).next_run_at == T0.replace(hour=13, minute=0, second=0)


async def test_an_enabled_schedule_without_a_next_time_is_planned(
    db: DBManager, clock: FakeClock, scheduler: Scheduler
) -> None:
    tick = await save(db, clock, next_run_at=None)

    async with serving(scheduler, clock):
        await clock.advance(MINUTE)

    assert (await stored(db, tick)).next_run_at == T0 + 2 * MINUTE
    assert len(await fired(db)) == 1
