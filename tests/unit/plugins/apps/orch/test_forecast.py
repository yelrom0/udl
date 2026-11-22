"""
------------------------------
- test_forecast.py -
------------------------------

The numbers a progress page is made of (27.10), from the work as it stands: the burn-up's series
(a day's scope and what merged that day), the pace it has been going at, and when the agents
will be done — a likely day with an early and a late one, from each agent's own rate and spread.

A day's scope counts the tasks that existed and were not cancelled then, at the size they have
now. An agent's hours become days at its own working hours a day; below an hour a day (a new or
idle agent) the project's average is used, then eight hours — so a forecast is never a division
by zero, and never ends before now.
"""

# System Imports
from datetime import UTC, date, datetime, timedelta

# Local Imports
from udl.plugins.apps.orch.progress import Card, Load, finishes, pace, series

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


def _card(
    points: int, *, made: int, merged: int | None = None, cancelled: int | None = None
) -> Card:
    """A task for the series: its points, the day it was made, and the day it merged or was
    cancelled (days before `NOW`)."""
    return Card(
        points=points,
        created_at=NOW - timedelta(days=made),
        merged_at=None if merged is None else NOW - timedelta(days=merged),
        cancelled_at=None if cancelled is None else NOW - timedelta(days=cancelled),
    )


def test_the_series_counts_a_day_s_scope_and_what_merged() -> None:
    cards = [_card(3, made=10, merged=1), _card(5, made=2)]

    found = series(cards, since=NOW - timedelta(days=3), now=NOW)

    assert [(one.day, one.points, one.merged) for one in found] == [
        (date(2026, 11, 19), 3, 0),
        (date(2026, 11, 20), 8, 0),
        (date(2026, 11, 21), 8, 3),
        (date(2026, 11, 22), 8, 0),
    ]


def test_a_cancelled_task_leaves_the_scope_from_the_day_it_was_cancelled() -> None:
    cards = [_card(3, made=10), _card(2, made=10, cancelled=1)]

    found = series(cards, since=NOW - timedelta(days=2), now=NOW)

    assert [(one.day, one.points) for one in found] == [
        (date(2026, 11, 20), 5),
        (date(2026, 11, 21), 3),
        (date(2026, 11, 22), 3),
    ]


def test_a_task_made_after_a_day_is_not_in_its_scope() -> None:
    cards = [_card(3, made=1)]

    found = series(cards, since=NOW - timedelta(days=2), now=NOW)

    # (Made yesterday at noon: it is in that day's scope, counted at the day's end.)
    assert [one.points for one in found] == [0, 3, 3]


def test_the_pace_is_the_points_merged_a_day_over_the_last_seven() -> None:
    cards = [_card(2, made=10, merged=1), _card(3, made=10, merged=1), _card(5, made=20, merged=9)]
    found = series(cards, since=NOW - timedelta(days=9), now=NOW)

    assert pace(found) == 5 / 7  # (five points in the last seven days)


def test_an_agent_finishes_after_its_hours_at_its_own_day() -> None:
    loads = [Load(agent="deepseek-a", hours=timedelta(hours=16), hours_a_day=8, low=1.0, high=1.0)]

    found = finishes(loads, now=NOW)

    assert found.likely == NOW + timedelta(days=2)
    assert (found.early, found.late) == (found.likely, found.likely)
    assert found.unsized == 0


def test_the_last_agent_to_finish_is_the_likely_day() -> None:
    loads = [
        Load(agent="deepseek-a", hours=timedelta(hours=8), hours_a_day=8, low=1.0, high=1.0),
        Load(agent="deepseek-b", hours=timedelta(hours=24), hours_a_day=8, low=1.0, high=1.0),
    ]

    found = finishes(loads, now=NOW)

    assert found.likely == NOW + timedelta(days=3)


def test_the_spread_makes_the_early_and_late_days() -> None:
    loads = [Load(agent="deepseek-a", hours=timedelta(hours=16), hours_a_day=8, low=0.5, high=2.0)]

    found = finishes(loads, now=NOW)

    assert found.early == NOW + timedelta(days=1)
    assert found.likely == NOW + timedelta(days=2)
    assert found.late == NOW + timedelta(days=4)


def test_an_idle_agent_s_hours_a_day_is_the_project_s() -> None:
    # (No working hours in the last seven days: the caller passes the average, and nothing here
    # divides by zero.)
    loads = [Load(agent="new", hours=timedelta(hours=8), hours_a_day=4, low=1.0, high=1.0)]

    found = finishes(loads, now=NOW)

    assert found.likely == NOW + timedelta(days=2)


def test_no_work_at_all_is_done_now() -> None:
    found = finishes([], now=NOW)

    assert (found.early, found.likely, found.late) == (NOW, NOW, NOW)


def test_a_forecast_never_ends_before_now() -> None:
    loads = [Load(agent="a", hours=timedelta(hours=-5), hours_a_day=8, low=0.5, high=1.5)]

    found = finishes(loads, now=NOW)

    assert NOW <= found.early <= found.likely <= found.late
