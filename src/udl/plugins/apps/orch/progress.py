"""
----------------
- progress.py -
----------------

The numbers a progress page is made of (27.10), from the work as it stands: the burn-up's series
(a day's scope and what merged that day), the pace it has been going at, and when the agents will
be done — a likely day with an early and a late one, from each agent's own rate and spread.

`series`, `pace` and `finishes` are pure: cards, loads and times in, numbers out. What fills the
cards and the loads (records, history and each agent's hours a day) is `Progress`'s own job,
which is the app's: it reads them through `records`.
"""

# System Imports
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

__all__ = ["Card", "Forecast", "Load", "finishes", "pace", "series"]

PACED: int = 7
"""The days a pace is the points merged over."""

DEFAULT_HOURS: float = 8.0
"""A day's hours when an agent has not worked in the last seven days (nor its project's agents)."""


@dataclass(frozen=True)
class Card:
    """A task, as the series needs it: its points now, and when it was made, merged or
    cancelled."""

    points: int
    created_at: datetime
    merged_at: datetime | None = None
    cancelled_at: datetime | None = None


@dataclass(frozen=True)
class Point:
    """One day of the series: the scope's points at its end, and the points merged in it."""

    day: date
    points: int
    merged: int


@dataclass(frozen=True)
class Load:
    """An agent's open, sized work in working hours, and the day it gets through it at: `low` and
    `high` are its rate's spread (1.0 and 1.0 when there is too little to say)."""

    agent: str
    hours: timedelta
    hours_a_day: float
    low: float = 1.0
    high: float = 1.0


@dataclass(frozen=True)
class Forecast:
    """When the work will be done: the last agent's own day, and the spread's range around it.
    `unsized` counts the tasks left out because nobody sized them."""

    likely: datetime
    early: datetime
    late: datetime
    unsized: int = 0


def series(cards: Sequence[Card], *, since: datetime, now: datetime) -> list[Point]:
    """The burn-up: each day from `since`'s to `now`'s, the scope's points at its end (the cards
    made by then and not cancelled by then, at the size they have now) and the points merged in
    it."""
    found: list[Point] = []
    day = since.date()
    while day <= now.date():
        end = datetime.combine(day + timedelta(days=1), time(), tzinfo=now.tzinfo)
        points = sum(
            card.points
            for card in cards
            if card.created_at < end and (card.cancelled_at is None or card.cancelled_at >= end)
        )
        merged = sum(
            card.points
            for card in cards
            if card.merged_at is not None and day == card.merged_at.date()
        )
        found.append(Point(day=day, points=points, merged=merged))
        day += timedelta(days=1)
    return found


def pace(points: Sequence[Point]) -> float:
    """The points merged a day over the last `PACED` days of the series (0.0 when it has none)."""
    last = points[-PACED:]
    return sum(one.merged for one in last) / PACED if last else 0.0


def finishes(loads: Sequence[Load], *, now: datetime) -> Forecast:
    """When the last of `loads` is done: each agent's hours at its own day, and the spread's
    range around that. Never before `now` (a day that has gone by is no forecast)."""

    def end(load: Load, by: float) -> datetime:
        hours = max(load.hours.total_seconds() / 3600, 0.0) * by
        days = hours / load.hours_a_day if load.hours_a_day > 0 else 0.0
        return now + timedelta(days=days)

    likely = max((end(one, 1.0) for one in loads), default=now)
    early = max(min((end(one, one.low) for one in loads), default=now), now)
    late = max(max((end(one, one.high) for one in loads), default=now), likely)
    return Forecast(likely=max(likely, now), early=early, late=late)
