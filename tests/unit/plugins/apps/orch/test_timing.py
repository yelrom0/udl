"""
-----------------------------
- test_timing.py -
-----------------------------

How long a task really took (27.10): the time it spent `in_progress`, less the time it waited
(the dispatcher's `Wait` records: its PR's checks, the review bot, its harness's quota), and a
wait that crosses a state change counts only for the part inside `in_progress`.

A task that moved between agents counts each agent's part for that agent: an agent's rate is
learned from the tasks it worked, so time spent on a task while it was somebody else's is not
its own.
"""

# System Imports
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

# Local Imports
from udl.plugins.apps.orch.models import (
    BLOCKED,
    DONE,
    IN_PROGRESS,
    OPEN,
    QUEUED,
    READY,
    State,
)
from udl.plugins.apps.orch.records import StateChange, Wait
from udl.plugins.apps.orch.timing import by_agent, working_time

# (The day a test's history happens on: hours and minutes, so a span reads as a time.)
DAY = datetime(2026, 11, 22, tzinfo=UTC)
TASK: UUID = uuid4()
"""The task every history here is of."""


def _at(hour: int, minute: int = 0) -> datetime:
    """The day's time, as a history records it."""
    return DAY + timedelta(hours=hour, minutes=minute)


def _change(
    at: datetime, to: State, by: str = "deepseek-a", from_state: State = OPEN
) -> StateChange:
    """A state change at `at`, to `to`, by `by` (from `from_state`, which a test rarely needs:
    the times are what a working time is made of)."""
    return StateChange(task=TASK, from_state=from_state, to_state=to, at=at, by=by)


def test_the_time_a_task_spent_in_progress_is_its_working_time() -> None:
    changes = [_change(_at(9), QUEUED), _change(_at(9, 30), IN_PROGRESS), _change(_at(11), DONE)]

    assert working_time(changes, [], now=_at(12)) == timedelta(hours=1, minutes=30)


def test_a_task_still_in_progress_counts_to_now() -> None:
    changes = [_change(_at(9), IN_PROGRESS)]

    assert working_time(changes, [], now=_at(11)) == timedelta(hours=2)


def test_a_task_that_never_started_has_no_working_time() -> None:
    changes = [_change(_at(9), QUEUED)]

    assert working_time(changes, [], now=_at(11)) == timedelta(0)


def test_a_wait_inside_the_working_time_is_taken_off() -> None:
    changes = [_change(_at(9), IN_PROGRESS), _change(_at(13), DONE)]
    waits = [Wait(task=TASK, wait_kind="ci", start=_at(10), end=_at(11))]

    assert working_time(changes, waits, now=_at(14)) == timedelta(hours=3)


def test_a_wait_that_never_ended_counts_to_now() -> None:
    changes = [_change(_at(9), IN_PROGRESS)]
    waits = [Wait(task=TASK, wait_kind="bot", start=_at(10), end=None)]

    assert working_time(changes, waits, now=_at(12)) == timedelta(hours=1)


def test_a_wait_outside_the_working_time_counts_for_nothing() -> None:
    # (A wait while the task was blocked, and one before it started: neither is working time.)
    changes = [
        _change(_at(8), BLOCKED),
        _change(_at(9), IN_PROGRESS),
        _change(_at(10), BLOCKED),
        _change(_at(11), DONE),
    ]
    waits = [
        Wait(task=TASK, wait_kind="limit", start=_at(7), end=_at(8)),
        Wait(task=TASK, wait_kind="ci", start=_at(10), end=_at(10, 30)),
    ]

    assert working_time(changes, waits, now=_at(12)) == timedelta(hours=1)


def test_a_wait_across_a_state_change_counts_only_the_part_inside() -> None:
    # (A wait from 10:30 to 12:30, while the task was in progress from 9:00 to 11:00: half an
    # hour of it is inside.)
    changes = [_change(_at(9), IN_PROGRESS), _change(_at(11), READY), _change(_at(12, 30), DONE)]
    waits = [Wait(task=TASK, wait_kind="ci", start=_at(10, 30), end=_at(12, 30))]

    assert working_time(changes, waits, now=_at(13)) == timedelta(hours=1, minutes=30)


def test_two_stretches_in_progress_are_added_up() -> None:
    changes = [
        _change(_at(9), IN_PROGRESS),
        _change(_at(10), BLOCKED),
        _change(_at(11), IN_PROGRESS),
        _change(_at(12), DONE),
    ]

    assert working_time(changes, [], now=_at(13)) == timedelta(hours=2)


def test_a_wait_inside_two_stretches_counts_in_each() -> None:
    # (One wait, from 9:30 to 11:30, with the task blocked from 10:00 to 11:00: half an hour in
    # each stretch.)
    changes = [
        _change(_at(9), IN_PROGRESS),
        _change(_at(10), BLOCKED),
        _change(_at(11), IN_PROGRESS),
        _change(_at(12), DONE),
    ]
    waits = [Wait(task=TASK, wait_kind="bot", start=_at(9, 30), end=_at(11, 30))]

    assert working_time(changes, waits, now=_at(13)) == timedelta(hours=1)


def test_a_task_that_moved_between_agents_counts_each_agent_s_part() -> None:
    changes = [
        _change(_at(9), IN_PROGRESS, by="deepseek-a"),
        _change(_at(11), QUEUED, by="udl"),
        _change(_at(12), IN_PROGRESS, by="deepseek-b"),
        _change(_at(13), DONE, by="deepseek-b"),
    ]

    assert by_agent(changes, [], now=_at(14)) == {
        "deepseek-a": timedelta(hours=2),
        "deepseek-b": timedelta(hours=1),
    }


def test_an_agent_s_part_has_its_own_waits_taken_off() -> None:
    # (The wait belongs to the stretch it falls in, so only deepseek-b's hour loses it.)
    changes = [
        _change(_at(9), IN_PROGRESS, by="deepseek-a"),
        _change(_at(11), QUEUED, by="udl"),
        _change(_at(12), IN_PROGRESS, by="deepseek-b"),
        _change(_at(13), DONE, by="deepseek-b"),
    ]
    waits = [Wait(task=TASK, wait_kind="ci", start=_at(12, 15), end=_at(12, 45))]

    assert by_agent(changes, waits, now=_at(14)) == {
        "deepseek-a": timedelta(hours=2),
        "deepseek-b": timedelta(minutes=30),
    }


def test_a_since_counts_only_the_time_after_it() -> None:
    # (A stretch from 9:00 to 13:00, counted from 11:00: two hours.)
    changes = [_change(_at(9), IN_PROGRESS), _change(_at(13), DONE)]

    assert working_time(changes, [], now=_at(14), since=_at(11)) == timedelta(hours=2)
    assert by_agent(changes, [], now=_at(14), since=_at(11)) == {"deepseek-a": timedelta(hours=2)}
