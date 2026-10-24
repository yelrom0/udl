"""
---------------------
- test_schedules.py -
---------------------

Schedules end to end: a cron job (a schedule and its action, saved through the operations) runs
a real script from scripts_dir in the automations' runtime, on a FakeClock: once each weekday
at 08:00, the event it fired on the script's stdin. A runtime stopped over a due time follows
each schedule's misfire policy when it starts again
"""

# System Imports
import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.interfaces import Harness
from udl.automations.models import SetActionRequest, SetScheduleRequest
from udl.automations.runtime import AutomationRuntime
from udl.config import AutomationsSettings
from udl.core.service import UDLService
from udl.plugins.actions.script import ScriptAction
from udl.plugins.registry import ACTIONS

MONDAY = datetime(2026, 1, 5, 7, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(MONDAY)


@pytest.fixture
def out(tmp_path: Path) -> Path:
    """Where the script writes what it read on its stdin: a file per event."""
    found = tmp_path / "out"
    found.mkdir()
    return found


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock) -> Harness:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    record = scripts / "record.sh"
    record.write_text('#!/bin/sh\ncat > "$OUT_DIR/$UDL_EVENT_ID.json"\n')
    record.chmod(0o755)
    found = Harness(
        tmp_path,
        clock=clock,
        # (Long intervals, so the fake days pass quickly: what is published here wakes the
        # dispatcher anyway.)
        automations=AutomationsSettings(
            scripts_dir=scripts,
            timezone="UTC",
            lease_ttl=3 * HOUR,
            dispatch_interval=HOUR,
        ),
    )
    found.registry.register(ACTIONS, ScriptAction)
    return found


@pytest.fixture
async def service(harness: Harness, out: Path) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        found.scheduler.recheck = HOUR  # (nothing else saves schedules here)
        await found.set_action(
            SetActionRequest(
                name="record",
                plugin="script",
                options={"command": ["record.sh"], "env": {"OUT_DIR": str(out)}},
            )
        )
        yield found


def runs_recorded(out: Path) -> int:
    return len(list(out.glob("*.json")))


def recorded(out: Path) -> list[dict[str, Any]]:
    """The schedule.fired data the script read, by the time each was for."""
    events = [json.loads(path.read_text()) for path in out.glob("*.json")]
    return sorted(
        (event["data"] for event in events),
        key=lambda data: (data["scheduled_for"], data["schedule"]),
    )


async def until_recorded(service: UDLService, clock: FakeClock, out: Path, count: int) -> None:
    """Let the runtime dispatch and run what was fired until the script has run `count` times
    (the script runs in real time)."""
    deadline = time.monotonic() + 10
    while runs_recorded(out) < count:
        assert time.monotonic() < deadline, "the script never ran"
        await clock.advance(timedelta(0))
        await service.runner.idle()
        await asyncio.sleep(0.01)


async def test_a_cron_job_runs_its_script_each_weekday_at_eight(
    service: UDLService, clock: FakeClock, out: Path
) -> None:
    await service.set_schedule(
        SetScheduleRequest(name="digest", cron="0 8 * * 1-5", actions=["record"])
    )

    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))
        for day in range(3):
            await clock.advance(MONDAY + day * DAY + timedelta(hours=1, minutes=1) - clock.now())
            await until_recorded(service, clock, out, day + 1)

    assert [data["scheduled_for"] for data in recorded(out)] == [
        "2026-01-05T08:00:00Z",
        "2026-01-06T08:00:00Z",
        "2026-01-07T08:00:00Z",
    ]
    assert all(
        (data["schedule"], data["late"], data["manual"]) == ("digest", False, False)
        for data in recorded(out)
    )


async def test_a_runtime_stopped_over_a_due_time_follows_the_misfire_policy(
    service: UDLService, clock: FakeClock, out: Path
) -> None:
    for name, misfire in (("catch-up", "once"), ("skip-it", "skip")):
        await service.set_schedule(
            SetScheduleRequest.model_validate(
                {"name": name, "cron": "0 8 * * *", "misfire": misfire, "actions": ["record"]}
            )
        )

    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(minutes=30))
    await clock.advance(HOUR)  # (08:30: udl was stopped at 08:00)
    async with AutomationRuntime(service, holder="here:1:b"):
        await clock.advance(timedelta(0))
        await until_recorded(service, clock, out, 1)

        await clock.advance(MONDAY + DAY + timedelta(hours=1, minutes=1) - clock.now())
        await until_recorded(service, clock, out, 3)

    assert [(data["schedule"], data["scheduled_for"], data["late"]) for data in recorded(out)] == [
        ("catch-up", "2026-01-05T08:00:00Z", True),
        ("catch-up", "2026-01-06T08:00:00Z", False),
        ("skip-it", "2026-01-06T08:00:00Z", False),
    ]
