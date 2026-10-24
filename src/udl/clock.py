"""
------------
- clock.py -
------------

Clock: what tells udl the time and lets it wait for one. Everything time-driven (schedules,
watchers, leases, throttles, retries, refreshing tokens) asks a Clock, never datetime.now() or
asyncio.sleep() itself, so tests move time on (tests/support/clock.py's FakeClock) instead of
waiting.
"""

# System Imports
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol

# The longest one sleep lasts: the wall clock can move under a sleep (a laptop suspended, the
# time set), so a long one wakes now and then to look at it.
_STEP: Final = timedelta(minutes=1)


class Clock(Protocol):
    def now(self) -> datetime:
        """The time now: aware, in UTC."""
        ...

    async def sleep_until(self, when: datetime) -> None:
        """Wait until `when` (at once, when it has gone by)."""
        ...


class SystemClock:
    """The system's clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_until(self, when: datetime) -> None:
        # (Waiting for the wall clock, not for an event: hence a step at a time.)
        while (left := when - self.now()) > timedelta(0):  # noqa: ASYNC110
            await asyncio.sleep(min(left, _STEP).total_seconds())
