"""
------------
- clock.py -
------------

FakeClock: a clock that stands still until a test moves it on, waking whatever sleeps until a
time it passes, in order
"""

# System Imports
import asyncio
import heapq
import itertools
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Final

T0: Final = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
"""When a FakeClock starts, unless told otherwise."""

# How many times the event loop runs what is ready after a sleeper is woken: enough for it to
# get to its next sleep (or end) before the clock moves on.
_SETTLE: Final = 20


class FakeClock:
    def __init__(self, start: datetime = T0) -> None:
        self._now = start
        self._sleepers: list[tuple[datetime, int, asyncio.Future[None]]] = []
        self._order = itertools.count()  # (sleepers until the same time wake in turn)

    @property
    def sleepers(self) -> int:
        """How many are sleeping."""
        return sum(1 for *_, woken in self._sleepers if not woken.done())

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        if when <= self._now:
            await asyncio.sleep(0)
            return
        woken = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (when, next(self._order), woken))
        await woken

    async def sleep(self, delay: timedelta) -> None:
        await self.sleep_until(self._now + delay)

    @asynccontextmanager
    async def timeout(self, delay: timedelta) -> AsyncGenerator[None]:
        """A block that raises TimeoutError when the clock passes `delay` from now before it
        ends (as asyncio.timeout does: by cancelling it)."""
        task = asyncio.current_task()
        assert task is not None
        expired = False

        async def expire() -> None:
            nonlocal expired
            await self.sleep_until(self._now + delay)
            expired = True
            task.cancel()

        watcher = asyncio.create_task(expire())
        try:
            yield
        except asyncio.CancelledError:
            if not expired:
                raise
            task.uncancel()
            raise TimeoutError from None
        finally:
            watcher.cancel()

    async def advance(self, by: timedelta) -> None:
        """Move the clock on by `by`, waking each sleeper at its time, in order, and letting it
        run on (it may sleep again, and be woken again, within `by`)."""
        until = self._now + by
        await _settle()
        while self._sleepers and self._sleepers[0][0] <= until:
            when, _, woken = heapq.heappop(self._sleepers)
            if woken.done():  # (cancelled)
                continue
            self._now = max(self._now, when)
            woken.set_result(None)
            await _settle()
        self._now = until
        await _settle()


async def _settle() -> None:
    for _ in range(_SETTLE):
        await asyncio.sleep(0)
