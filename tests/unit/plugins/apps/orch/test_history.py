"""
------------------------------
- test_history.py -
------------------------------

A task's moves, kept (27.10): every move writes a `StateChange` — where the task went, when, and
by whom — in the same write as the move's own note, so a task's history is complete and no move
lands without it.

An import is not a move: a plan's `[x]` is what a plan says happened, so it writes no history.
A save that leaves the state alone writes none either.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import databases, registry, settings
from udl.config import Settings
from udl.db.records import RecordQuery
from udl.errors import NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    IN_PROGRESS,
    MERGED,
    OPEN,
    QUEUED,
    NoteKind,
    OwnerKind,
    Principal,
)
from udl.plugins.apps.orch.records import (
    Feature,
    FeatureRecords,
    NoteRecords,
    Project,
    ProjectRecords,
    StateChange,
    Task,
    TaskRecords,
)
from udl.plugins.apps.orch.transitions import move

ME = Principal(kind="human", name="human:claude")
AGENT = Principal(kind="agent", name="deepseek-a")


@pytest.fixture
async def records(tmp_path: Path) -> AppRecords:
    """Orch's records over a fresh in-memory database, with one project and a feature."""
    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    kept = AppRecords(db)
    await ProjectRecords(kept).save_project(Project(name="udl", title="udl"))
    await FeatureRecords(kept).save_feature(Feature(project="udl", key="F-1", title="History"))
    return kept


async def a_task(records: AppRecords, key: str = "F-1.1", **fields: object) -> Task:
    """A task of the project, saved, and read back (as a caller has it)."""
    tasks = TaskRecords(records)
    await tasks.save_task(
        Task(project="udl", feature="F-1", key=key, title="x", **fields)  # pyright: ignore[reportArgumentType]
    )
    found = await tasks.task("udl", key)
    assert found is not None
    return found


async def history(records: AppRecords, task: Task) -> list[StateChange]:
    """The moves recorded of `task`, oldest first."""
    found = await records.list(StateChange, RecordQuery(where={"task": task.id}))
    return sorted(found, key=lambda one: one.at)


async def test_a_move_is_recorded_with_where_when_and_who(records: AppRecords) -> None:
    task = await a_task(records)

    await TaskRecords(records).move(task, QUEUED, by=ME)

    (recorded,) = await history(records, task)
    assert (recorded.from_state, recorded.to_state) == (OPEN, QUEUED)
    assert recorded.by == "human:claude"
    assert recorded.task == task.id


async def test_a_move_s_record_lands_with_its_note(records: AppRecords) -> None:
    # (Both, or neither: a state change never lands without its history entry.)
    task = await a_task(records)

    moved = await TaskRecords(records).move(task, QUEUED, by=ME)

    notes = await NoteRecords(records).for_owner(OwnerKind.TASK, task.id)
    assert [(note.note_kind, note.text) for note in notes] == [
        (NoteKind.SYSTEM, "open → queued, by human:claude.")
    ]
    assert [one.to_state for one in await history(records, task)] == [QUEUED]
    assert moved.state == QUEUED


async def test_an_agent_s_move_names_the_agent(records: AppRecords) -> None:
    tasks = TaskRecords(records)
    task = await a_task(records, assignee="deepseek-a")
    queued = await tasks.move(task, QUEUED, by=ME)

    await tasks.move(queued, IN_PROGRESS, by=AGENT)

    last = (await history(records, task))[-1]
    assert (last.from_state, last.to_state, last.by) == (QUEUED, IN_PROGRESS, "deepseek-a")


async def test_a_batch_of_moves_records_each_of_them(records: AppRecords) -> None:
    tasks = TaskRecords(records)
    first = await a_task(records, "F-1.1")
    second = await a_task(records, "F-1.2")

    await tasks.save_all([move(one, QUEUED, by=ME) for one in (first, second)])

    assert [one.to_state for one in await history(records, first)] == [QUEUED]
    assert [one.to_state for one in await history(records, second)] == [QUEUED]


async def test_a_save_that_leaves_the_state_alone_records_nothing(records: AppRecords) -> None:
    task = await a_task(records)

    await TaskRecords(records).save_all([task.model_copy(update={"title": "renamed"})])

    assert await history(records, task) == []


async def test_a_refused_move_records_nothing(records: AppRecords) -> None:
    task = await a_task(records)

    with pytest.raises(NotAllowed):
        await TaskRecords(records).move(task, MERGED, by=AGENT)

    assert await history(records, task) == []


async def test_an_import_records_no_move(records: AppRecords) -> None:
    # (A plan's `[x]` is what the plan says happened, not a move of what udl is doing.)
    tasks = TaskRecords(records)
    task = await a_task(records)

    await tasks.import_task(task.model_copy(update={"state": MERGED}))

    assert await history(records, task) == []
