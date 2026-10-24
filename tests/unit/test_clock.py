"""
-----------------
- test_clock.py -
-----------------

Clocks: the system's, and the FakeClock tests move on instead of waiting
"""

# System Imports
import asyncio
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
import udl
from support.clock import T0, FakeClock
from udl.clock import Clock, SystemClock, pause


def test_the_system_clock_tells_the_time_in_utc() -> None:
    clock: Clock = SystemClock()

    now = clock.now()

    assert now.tzinfo is UTC
    assert abs(now - datetime.now(UTC)) < timedelta(seconds=1)


async def test_the_system_clock_sleeps_until_a_time() -> None:
    clock = SystemClock()
    started = time.monotonic()

    await clock.sleep_until(clock.now() + timedelta(milliseconds=50))

    assert time.monotonic() - started >= 0.045


async def test_the_system_clock_does_not_sleep_for_a_time_gone_by() -> None:
    clock = SystemClock()
    started = time.monotonic()

    await clock.sleep_until(clock.now() - timedelta(hours=1))

    assert time.monotonic() - started < 0.05


async def test_the_system_clock_wakes_on_time_after_long_sleeps_in_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A long sleep is slept a minute at a time, checking the time in between (a laptop that
    # slept, or a clock that was set, moves the wall clock under a monotonic sleep).
    clock = SystemClock()
    slept: list[float] = []
    now = [T0]

    def fake_now() -> datetime:
        return now[0]

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += timedelta(seconds=seconds)

    monkeypatch.setattr(clock, "now", fake_now)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await clock.sleep_until(T0 + timedelta(minutes=2, seconds=30))

    assert slept == [60, 60, 30]


# ---------------------------------------------------------------------------------------------
# FakeClock
# ---------------------------------------------------------------------------------------------


def test_a_fake_clock_stands_still_until_moved_on() -> None:
    clock = FakeClock()

    assert clock.now() == T0
    assert clock.now() == T0


async def test_moving_a_fake_clock_on_wakes_its_sleepers_in_order() -> None:
    clock = FakeClock()
    woke: list[tuple[str, datetime]] = []

    async def sleeper(name: str, seconds: int) -> None:
        await clock.sleep_until(T0 + timedelta(seconds=seconds))
        woke.append((name, clock.now()))

    tasks = [
        asyncio.create_task(sleeper(name, seconds))
        for name, seconds in [("late", 30), ("early", 10), ("never", 90)]
    ]
    await clock.advance(timedelta(seconds=60))

    assert woke == [("early", T0 + timedelta(seconds=10)), ("late", T0 + timedelta(seconds=30))]
    assert clock.now() == T0 + timedelta(seconds=60)
    assert not tasks[2].done()
    tasks[2].cancel()


async def test_a_sleeper_that_sleeps_again_within_the_move_wakes_again() -> None:
    clock = FakeClock()
    ticks: list[datetime] = []

    async def ticker() -> None:
        while True:
            await clock.sleep_until(clock.now() + timedelta(seconds=10))
            ticks.append(clock.now())

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    await clock.advance(timedelta(seconds=35))
    task.cancel()

    assert ticks == [T0 + timedelta(seconds=n) for n in (10, 20, 30)]


async def test_a_fake_clock_does_not_sleep_for_a_time_gone_by() -> None:
    clock = FakeClock()

    await asyncio.wait_for(clock.sleep_until(T0 - timedelta(seconds=1)), timeout=1)
    await asyncio.wait_for(clock.sleep_until(T0), timeout=1)


async def test_a_cancelled_sleeper_is_forgotten() -> None:
    clock = FakeClock()
    task = asyncio.create_task(clock.sleep_until(T0 + timedelta(seconds=5)))
    await asyncio.sleep(0)

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await clock.advance(timedelta(seconds=10))

    assert clock.sleepers == 0


async def test_a_fake_clock_can_be_set_to_start_anywhere() -> None:
    start = datetime(2030, 3, 31, 1, 30, tzinfo=UTC)

    assert FakeClock(start).now() == start


async def test_the_system_clock_sleeps_for_a_while() -> None:
    started = time.monotonic()

    await SystemClock().sleep(timedelta(milliseconds=30))

    assert time.monotonic() - started >= 0.025


async def test_the_system_clock_times_out_what_takes_too_long() -> None:
    with pytest.raises(TimeoutError):
        async with SystemClock().timeout(timedelta(milliseconds=20)):
            await asyncio.sleep(5)


async def test_a_fake_clock_sleeps_for_a_while() -> None:
    clock = FakeClock()
    task = asyncio.create_task(clock.sleep(timedelta(minutes=5)))

    await clock.advance(timedelta(minutes=4))
    assert not task.done()
    await clock.advance(timedelta(minutes=1))

    assert task.done()


async def test_a_fake_clock_times_out_what_takes_too_long() -> None:
    clock = FakeClock()

    async def waits() -> str:
        async with clock.timeout(timedelta(seconds=30)):
            await asyncio.Event().wait()
        return "never"

    task = asyncio.create_task(waits())
    await clock.advance(timedelta(seconds=29))
    assert not task.done()
    await clock.advance(timedelta(seconds=1))

    with pytest.raises(TimeoutError):
        await task


async def test_a_fake_clock_s_timeout_lets_what_is_quick_finish() -> None:
    clock = FakeClock()

    async with clock.timeout(timedelta(seconds=30)):
        await asyncio.sleep(0)
    await clock.advance(timedelta(minutes=1))

    assert clock.sleepers == 0


async def test_a_cancelled_task_in_a_fake_clock_s_timeout_stays_cancelled() -> None:
    clock = FakeClock()

    async def waits() -> None:
        async with clock.timeout(timedelta(seconds=30)):
            await asyncio.Event().wait()

    task = asyncio.create_task(waits())
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


def test_what_waits_for_a_time_asks_the_clock() -> None:
    # (So tests move time on instead of waiting: the scheduler, watchers, leases, retries...)
    root = Path(udl.__file__).parent
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for package in ("automations", "integrations", "ai")
        for path in sorted((root / package).rglob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if "datetime.now(" in line or "asyncio.sleep(" in line
    ]

    assert offenders == []


# ---------------------------------------------------------------------------------------------
# pause(): a sleep that something may end early
# ---------------------------------------------------------------------------------------------


async def test_a_pause_lasts_its_delay() -> None:
    clock = FakeClock()
    woken = asyncio.Event()
    paused = asyncio.create_task(pause(clock, timedelta(seconds=10), woken))

    await clock.advance(timedelta(seconds=9))
    assert not paused.done()
    await clock.advance(timedelta(seconds=1))

    assert paused.done()
    assert clock.sleepers == 0


async def test_a_pause_ends_when_what_it_waits_for_happens() -> None:
    clock = FakeClock()
    one, other = asyncio.Event(), asyncio.Event()
    paused = asyncio.create_task(pause(clock, timedelta(hours=1), one, other))
    await clock.advance(timedelta(0))

    other.set()
    await clock.advance(timedelta(0))

    assert paused.done()
    assert clock.sleepers == 0  # (its sleep is cancelled)
    assert clock.now() == T0
