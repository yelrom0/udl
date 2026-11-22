"""
--------------
- timing.py -
--------------

How long a task really took (27.10), from its history: the time it spent `in_progress`, less the
time it waited (a `Wait` record: its PR's checks, the review bot, its harness's quota), and a
wait that crosses a state change counts only for the part inside `in_progress`.

Pure: it reads records and returns times, and nothing here writes one. A task that moved between
agents counts each agent's part for that agent (`by_agent`): an agent's rate is learned from the
tasks it worked, so an hour spent on a task while it was somebody else's is not its own.
"""

# System Imports
from collections.abc import Sequence
from datetime import datetime, timedelta

# Local Imports
from udl.plugins.apps.orch.models import IN_PROGRESS
from udl.plugins.apps.orch.records import StateChange, Wait

__all__ = ["by_agent", "working_time"]


def working_time(
    changes: Sequence[StateChange],
    waits: Sequence[Wait],
    *,
    now: datetime,
    since: datetime | None = None,
) -> timedelta:
    """How long the task whose history this is worked: every stretch it was `in_progress`, less
    the waits inside them. A stretch that has not ended counts to `now`, and `since` (when it is
    given) is where counting starts: a stretch that began before it counts from it."""
    return sum(
        (
            end - start - _taken(start, end, waits, now)
            for _, start, end in _spans(changes, now, since)
        ),
        timedelta(0),
    )


def by_agent(
    changes: Sequence[StateChange],
    waits: Sequence[Wait],
    *,
    now: datetime,
    since: datetime | None = None,
) -> dict[str, timedelta]:
    """The working time each agent gave the task: the agent that moved it into `in_progress`
    owns that stretch, so a task handed on is split between them (an agent that only moved it
    out is not given the time). `since`, when it is given, is where counting starts."""
    parts: dict[str, timedelta] = {}
    for by, start, end in _spans(changes, now, since):
        worked = end - start - _taken(start, end, waits, now)
        parts[by] = parts.get(by, timedelta(0)) + worked
    return parts


def _spans(
    changes: Sequence[StateChange], now: datetime, since: datetime | None = None
) -> list[tuple[str, datetime, datetime]]:
    """The stretches the task was `in_progress`: who moved it in, and when each began and ended
    (the next change's time, or `now` while it still is), from `since` when it is given."""
    ordered = sorted(changes, key=lambda one: one.at)
    spans: list[tuple[str, datetime, datetime]] = []
    for index, change in enumerate(ordered):
        if change.to_state != IN_PROGRESS:
            continue
        end = ordered[index + 1].at if index + 1 < len(ordered) else now
        start = change.at if since is None else max(change.at, since)
        if end > start:
            spans.append((change.by, start, end))
    return spans


def _taken(start: datetime, end: datetime, waits: Sequence[Wait], now: datetime) -> timedelta:
    """How much of the stretch from `start` to `end` a wait covered: a wait that crosses either end
    counts only the part inside."""
    taken = timedelta(0)
    for wait in waits:
        inside = min(end, wait.end or now) - max(start, wait.start)
        if inside > timedelta(0):
            taken += inside
    return taken
