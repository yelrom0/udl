"""
----------------------------
- test_schedule_records.py -
----------------------------

The Schedule record: a spec in a time zone, with what it passes on (data), what it does about
the times it missed while udl was stopped (its misfire policy: skip them, or run the latest of
them once, late, within misfire_grace), a jitter, and its next and last times. A schedule is
planned (its next time) when it is saved and each time it fires; an `at` schedule that has
fired is disabled. When it fires, it is a schedule.fired event
"""

# System Imports
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from support.engine import connection
from udl.automations.events import EventTypes, ScheduleFiredData
from udl.automations.filters import check, parse
from udl.automations.schedules import (
    AtSpec,
    CronSpec,
    EverySpec,
    Misfire,
    Schedule,
    due_after_downtime,
)
from udl.db.records import record_kinds
from udl.plugins.databases.memory import MemoryDatabase

LONDON = ZoneInfo("Europe/London")
UTC_ZONE = ZoneInfo("UTC")


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def schedule(**fields: Any) -> Schedule:
    fields.setdefault("name", "digest")
    fields.setdefault("spec", {"cron": "0 8 * * 1-5"})
    return Schedule.model_validate(fields)


# ---------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------


def test_schedules_are_a_kind_of_record() -> None:
    assert Schedule in record_kinds()


async def test_a_schedule_round_trips() -> None:
    stored = schedule(
        spec={"every": "15m", "start": "2026-01-01T09:00:00"},
        timezone="Europe/London",
        data={"report": "daily"},
        misfire="once",
        misfire_grace="2h",
        jitter="30s",
        next_run_at=utc(2026, 1, 5, 8),
    )

    async with MemoryDatabase(connection()) as db:
        await db.upsert_record(stored)
        found = await db.get_record(Schedule, stored.id)

    assert found is not None
    assert found.spec == EverySpec(every=timedelta(minutes=15), start=datetime(2026, 1, 1, 9))
    assert (found.misfire, found.misfire_grace, found.jitter) == (
        Misfire.ONCE,
        timedelta(hours=2),
        timedelta(seconds=30),
    )
    assert found.next_run_at == utc(2026, 1, 5, 8)
    assert found.model_dump(mode="json")["misfire_grace"] == "2h"


def test_a_schedule_s_defaults() -> None:
    found = schedule(timezone=None)

    assert found.spec == CronSpec(cron="0 8 * * 1-5")
    assert (found.timezone, found.data, found.misfire) == (None, {}, Misfire.SKIP)
    assert (found.misfire_grace, found.jitter) == (timedelta(hours=1), timedelta(0))
    assert (found.enabled, found.next_run_at, found.last_run_at) == (True, None, None)


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "x" * 92},  # (schedule:<name> names its subscription, at most 100)
        {"name": "-digest"},
        {"name": "a b"},
        {"timezone": "Mars/Olympus_Mons"},
        {"spec": {"cron": "0 8 * *"}},
        {"spec": {"every": "30s"}},
        {"spec": {}},
        {"jitter": "-1s"},
        {"jitter": timedelta(seconds=-1)},
        {"misfire_grace": timedelta(hours=-1)},
        {"misfire": "twice"},
    ],
)
def test_what_is_no_schedule_is_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        schedule(**fields)


def test_a_long_name_still_names_its_subscription() -> None:
    assert schedule(name="x" * 91).name == "x" * 91


def test_a_schedule_s_zone_is_its_own_else_the_default() -> None:
    assert schedule(timezone="Europe/London").zone("America/New_York") == LONDON
    assert schedule().zone("America/New_York") == ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------------------------


def test_a_planned_schedule_s_next_time_is_after_the_time_given() -> None:
    planned = schedule().planned(utc(2026, 1, 2, 9), UTC_ZONE)  # (a Friday)

    assert planned.next_run_at == utc(2026, 1, 5, 8)
    assert planned.enabled


def test_planning_is_in_the_schedule_s_zone() -> None:
    planned = schedule(spec={"cron": "0 8 * * *"}).planned(utc(2026, 7, 1), LONDON)

    assert planned.next_run_at == utc(2026, 7, 1, 7)


def test_an_interval_without_a_start_starts_when_it_is_first_planned() -> None:
    planned = schedule(spec={"every": "1h"}).planned(utc(2026, 1, 1, 9, 30), UTC_ZONE)

    assert planned.spec == EverySpec(every=timedelta(hours=1), start=utc(2026, 1, 1, 9, 30))
    assert planned.next_run_at == utc(2026, 1, 1, 10, 30)
    assert planned.planned(utc(2026, 1, 1, 12), UTC_ZONE).next_run_at == utc(2026, 1, 1, 12, 30)


def test_an_interval_without_a_start_starts_on_a_whole_second() -> None:
    planned = schedule(spec={"every": "1m"}).planned(utc(2026, 1, 1, 9, 30, 15, 72_423), UTC_ZONE)

    assert planned.spec == EverySpec(every=timedelta(minutes=1), start=utc(2026, 1, 1, 9, 30, 15))
    assert planned.next_run_at == utc(2026, 1, 1, 9, 31, 15)


def test_an_at_schedule_is_disabled_once_its_time_has_gone_by() -> None:
    once = schedule(spec={"at": "2026-11-01T09:00:00+00:00"})

    planned = once.planned(utc(2026, 1, 1), UTC_ZONE)
    fired = planned.planned(utc(2026, 11, 1, 9), UTC_ZONE)

    assert (planned.next_run_at, planned.enabled) == (utc(2026, 11, 1, 9), True)
    assert (fired.next_run_at, fired.enabled) == (None, False)
    assert fired.spec == AtSpec(at=utc(2026, 11, 1, 9))


# ---------------------------------------------------------------------------------------------
# Jitter
# ---------------------------------------------------------------------------------------------


def test_without_jitter_a_schedule_is_due_at_its_time() -> None:
    found = schedule(next_run_at=utc(2026, 1, 5, 8))

    assert found.due_at() == utc(2026, 1, 5, 8)
    assert schedule().due_at() is None


def delay(found: Schedule, time: datetime) -> timedelta:
    """How long after `time` the schedule is due, when that is its next time."""
    due = found.model_copy(update={"next_run_at": time}).due_at()
    assert due is not None
    return due - time


def test_jitter_delays_each_time_by_up_to_its_bound_the_same_each_time_it_is_asked() -> None:
    times = [utc(2026, 1, 5, 8) + timedelta(days=n) for n in range(200)]
    jittery = schedule(jitter="10m", id=UUID("00000000-0000-4000-8000-000000000007"))

    delays = [delay(jittery, time) for time in times]
    again = [delay(jittery, time) for time in times]

    assert delays == again
    assert all(timedelta(0) <= delay <= timedelta(minutes=10) for delay in delays)
    assert len(set(delays)) > 150  # (spread, not one delay)
    assert min(delays) < timedelta(minutes=2)
    assert max(delays) > timedelta(minutes=8)


# ---------------------------------------------------------------------------------------------
# Misfires: the times missed while udl was stopped
# ---------------------------------------------------------------------------------------------


def missed(**fields: Any) -> Schedule:
    """A schedule every hour on the hour, due at 08:00 (udl was stopped then)."""
    fields.setdefault("spec", {"cron": "0 * * * *"})
    fields.setdefault("next_run_at", utc(2026, 1, 5, 8))
    return schedule(**fields)


def test_skip_fires_nothing_and_goes_on_from_the_next_time() -> None:
    due = due_after_downtime(missed(), utc(2026, 1, 5, 10, 30), UTC_ZONE)

    assert (due.fire, due.next_run_at) == (None, utc(2026, 1, 5, 11))


def test_once_fires_the_latest_time_missed_once() -> None:
    due = due_after_downtime(
        missed(misfire="once", misfire_grace="1h"), utc(2026, 1, 5, 10, 30), UTC_ZONE
    )

    assert (due.fire, due.next_run_at) == (utc(2026, 1, 5, 10), utc(2026, 1, 5, 11))


def test_once_fires_nothing_when_every_time_missed_is_older_than_its_grace() -> None:
    due = due_after_downtime(
        missed(spec={"cron": "0 8 * * *"}, misfire="once", misfire_grace="1h"),
        utc(2026, 1, 5, 10),
        UTC_ZONE,
    )

    assert (due.fire, due.next_run_at) == (None, utc(2026, 1, 6, 8))


def test_once_fires_the_time_missed_at_the_edge_of_its_grace() -> None:
    due = due_after_downtime(
        missed(spec={"cron": "0 8 * * *"}, misfire="once", misfire_grace="2h"),
        utc(2026, 1, 5, 10),
        UTC_ZONE,
    )

    assert due.fire == utc(2026, 1, 5, 8)


def test_once_never_fires_a_time_before_the_one_that_was_due() -> None:
    # (Times before next_run_at had fired, or came before the schedule was saved.)
    due = due_after_downtime(
        missed(next_run_at=utc(2026, 1, 5, 10), misfire="once", misfire_grace="1d"),
        utc(2026, 1, 5, 10, 30),
        UTC_ZONE,
    )

    assert due.fire == utc(2026, 1, 5, 10)


def test_an_at_schedule_missed_has_no_next_time() -> None:
    due = due_after_downtime(
        missed(spec={"at": "2026-01-05T08:00:00+00:00"}, misfire="once"),
        utc(2026, 1, 5, 8, 30),
        UTC_ZONE,
    )

    assert (due.fire, due.next_run_at) == (utc(2026, 1, 5, 8), None)


# ---------------------------------------------------------------------------------------------
# schedule.fired
# ---------------------------------------------------------------------------------------------


def test_a_schedule_firing_is_a_schedule_fired_event() -> None:
    fired = EventTypes.default().get("schedule.fired")
    data = ScheduleFiredData(
        schedule="digest",
        scheduled_for=utc(2026, 1, 5, 8),
        fired_at=utc(2026, 1, 5, 8, 0, 3),
        data={"report": "daily"},
    )

    assert fired.data is ScheduleFiredData
    assert fired.subject_of(data) == "The digest schedule fired"
    assert fired.subject_of(data.model_copy(update={"late": True})) == (
        "The digest schedule fired, late"
    )
    assert fired.subject_of(data.model_copy(update={"manual": True})) == (
        "The digest schedule was run by hand"
    )


def test_a_subscription_filters_schedule_fired_by_its_schedule() -> None:
    fired = EventTypes.default().get("schedule.fired")

    check(parse('schedule == "digest" and not late and data.report == "daily"'), [fired])
