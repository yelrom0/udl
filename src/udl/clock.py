"""
------------
- clock.py -
------------

Clock: what tells udl the time and lets it wait for one. Everything time-driven (schedules,
watchers, leases, throttles, retries, refreshing tokens) asks a Clock, never datetime.now() or
asyncio.sleep() itself (a test checks udl.automations, udl.integrations and udl.ai), so tests
move time on (tests/support/clock.py's FakeClock) instead of waiting.
"""

# System Imports
import asyncio
from contextlib import AbstractAsyncContextManager
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

    async def sleep(self, delay: timedelta) -> None:
        """Wait for `delay`."""
        ...

    def timeout(self, delay: timedelta) -> AbstractAsyncContextManager[object]:
        """A block that raises TimeoutError when it has not ended within `delay`."""
        ...


class SystemClock:
    """The system's clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep(self, delay: timedelta) -> None:
        await self.sleep_until(self.now() + delay)

    def timeout(self, delay: timedelta) -> AbstractAsyncContextManager[object]:
        return asyncio.timeout(delay.total_seconds())

    async def sleep_until(self, when: datetime) -> None:
        # (Waiting for the wall clock, not for an event: hence a step at a time.)
        while (left := when - self.now()) > timedelta(0):  # noqa: ASYNC110
            await asyncio.sleep(min(left, _STEP).total_seconds())


async def pause(clock: Clock, delay: timedelta, *until: asyncio.Event) -> None:
    """Wait on `clock` for `delay`, or until one of `until` is set (what a loop that also
    wakes for news waits with)."""
    waits = [
        asyncio.ensure_future(clock.sleep(delay)),
        *(asyncio.ensure_future(event.wait()) for event in until),
    ]
    try:
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for wait in waits:
            wait.cancel()
