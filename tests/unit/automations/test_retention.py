"""
---------------------
- test_retention.py -
---------------------

Retention: events and ended runs older than event_retention_days are pruned, but never an event
that is still pending, nor a run that is queued, running or waiting
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.engine import databases, registry, settings
from udl.automations.events import EventRecord, EventStatus
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.runtime import prune_history
from udl.db.manager import DBManager
from udl.db.records import RecordQuery


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def db(tmp_path: Path, clock: FakeClock) -> AsyncIterator[DBManager]:
    found = databases(settings(tmp_path), registry(), clock=clock)
    async with found:
        yield found


async def event(db: DBManager, status: EventStatus) -> EventRecord:
    return await db.upsert_record(
        EventRecord.example().model_copy(
            update={"id": uuid4(), "status": status, "dedupe_key": None}
        )
    )


async def run(db: DBManager, status: RunStatus) -> ActionRun:
    return await db.upsert_record(
        ActionRun.example().model_copy(
            update={"id": uuid4(), "status": status, "event_id": None, "action_id": uuid4()}
        )
    )


async def test_old_history_is_pruned_and_what_waits_is_kept(
    db: DBManager, clock: FakeClock
) -> None:
    old_events = {status: await event(db, status) for status in EventStatus}
    old_runs = {status: await run(db, status) for status in RunStatus}
    await clock.advance(timedelta(days=31))
    new_event = await event(db, EventStatus.DISPATCHED)
    new_run = await run(db, RunStatus.SUCCEEDED)

    pruned = await prune_history(db, clock.now() - timedelta(days=30))

    events = {found.id for found in await db.list_records(EventRecord, RecordQuery())}
    runs = {found.id for found in await db.list_records(ActionRun, RecordQuery())}
    assert events == {old_events[EventStatus.PENDING].id, new_event.id}
    assert runs == {
        old_runs[RunStatus.QUEUED].id,
        old_runs[RunStatus.RUNNING].id,
        old_runs[RunStatus.AWAITING_APPROVAL].id,
        new_run.id,
    }
    assert pruned == 2 + 4


async def test_pruning_takes_as_many_pages_as_there_are(
    db: DBManager, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("udl.automations.runtime.PRUNE_PAGE", 2)
    for _ in range(5):
        await event(db, EventStatus.DISPATCHED)
    await clock.advance(timedelta(days=31))

    assert await prune_history(db, clock.now()) == 5
    assert await db.list_records(EventRecord, RecordQuery()) == []
