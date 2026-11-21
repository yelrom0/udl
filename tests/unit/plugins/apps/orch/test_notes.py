"""
--------------------------
- test_notes.py -
--------------------------

Notes (udl.plugins.apps.orch.records.Note, NoteRecords): the phase's bullet — a note on a task or
a feature (what was done, a decision, a finding, a review), append-only, with its author and
when it was written; ordering and authorship; and the `system` note a state change writes
("ready → merged, by …").
"""

# System Imports
from pathlib import Path
from typing import Final
from uuid import UUID

# Package Imports
import pytest

# Local Imports
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    IN_PROGRESS,
    MERGED,
    QUEUED,
    READY,
    NoteKind,
    OwnerKind,
    Principal,
)
from udl.plugins.apps.orch.records import (
    Feature,
    FeatureRecords,
    Note,
    NoteRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)


@pytest.fixture
async def records(tmp_path: Path) -> AppRecords:
    """Orch's records over a fresh in-memory database (as tests/support/engine.py makes one)."""
    from support.engine import databases, registry, settings
    from udl.config import Settings

    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    return AppRecords(db)


@pytest.fixture
async def notes(records: AppRecords) -> NoteRecords:
    """Orch's notes, over the same records."""
    return NoteRecords(records)


@pytest.fixture
async def tasks(records: AppRecords) -> TaskRecords:
    """A task to hang notes on: `26.5`, in a feature of udl's."""
    await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
    features = FeatureRecords(records)
    await features.save_feature(Feature(project="udl", key="26", title="Work tracking"))
    tasks = TaskRecords(records)
    await tasks.save_task(
        Task(
            id=TASK_ID,
            project="udl",
            feature="26",
            key="26.5",
            title="Notes",
            spec="The 26.5 card.",
        )
    )
    return tasks


def note_on(kind: NoteKind, text: str, *, author: str = "ernie-c") -> Note:
    """A note on a task (the fixture's), as an agent would write it."""
    return Note(
        owner_kind=OwnerKind.TASK,
        owner=TASK_ID,
        note_kind=kind,
        text=text,
        author=author,
    )


TASK_ID: Final = UUID("6f1c2f0e-9d4a-4a37-8c1e-0d2b7a5c9e11")
"""The id of the task the fixtures hang their notes on (the record contract's own example)."""


# ---------------------------------------------------------------------------------------------
# Appending, and reading a history
# ---------------------------------------------------------------------------------------------


async def test_a_note_is_read_back(notes: NoteRecords) -> None:
    written = await notes.add(note_on(NoteKind.FINDING, "The gate takes 6 minutes."))

    found = await notes.get(written.id)
    assert found is not None
    assert (found.note_kind, found.text, found.author) == (
        NoteKind.FINDING,
        "The gate takes 6 minutes.",
        "ernie-c",
    )
    assert found.at is not None


async def test_a_tasks_history_reads_top_to_bottom(notes: NoteRecords) -> None:
    """The Done-when: a task's notes read as its card's notes do in the trackers today."""
    first = await notes.add(note_on(NoteKind.PROGRESS, "Tests first: 72 failing."))
    second = await notes.add(note_on(NoteKind.PROGRESS, "Green: 72 passing."))
    third = await notes.add(note_on(NoteKind.DECISION, "The record is its own table."))

    history = await notes.for_owner(OwnerKind.TASK, TASK_ID)

    assert [one.text for one in history] == [
        "Tests first: 72 failing.",
        "Green: 72 passing.",
        "The record is its own table.",
    ]
    assert [one.id for one in history] == [first.id, second.id, third.id]


async def test_notes_are_ordered_by_when_they_were_written(notes: NoteRecords) -> None:
    """Notes written in one tick are ordered by their time, then by when they were stored."""
    earlier = note_on(NoteKind.PROGRESS, "Earlier.").model_copy(
        update={"at": note_on(NoteKind.PROGRESS, "x").at}
    )
    later = note_on(NoteKind.PROGRESS, "Later.")
    await notes.add(later)
    await notes.add(earlier)

    assert [one.text for one in await notes.for_owner(OwnerKind.TASK, TASK_ID)] == [
        "Earlier.",
        "Later.",
    ]


async def test_a_note_is_never_replaced(notes: NoteRecords) -> None:
    """A correction is another note: there is no update, and the first is still there."""
    await notes.add(note_on(NoteKind.FINDING, "The gate is make check."))
    await notes.add(note_on(NoteKind.FINDING, "Correction: it is make gates."))

    history = await notes.for_owner(OwnerKind.TASK, TASK_ID)

    assert [one.text for one in history] == [
        "The gate is make check.",
        "Correction: it is make gates.",
    ]


async def test_notes_are_kept_apart_by_owner(notes: NoteRecords) -> None:
    await notes.add(note_on(NoteKind.PROGRESS, "On the task."))
    await notes.add(
        Note(
            owner_kind=OwnerKind.FEATURE,
            owner=TASK_ID,
            note_kind=NoteKind.PROGRESS,
            text="On the feature.",
            author="human:claude",
        )
    )

    assert [one.text for one in await notes.for_owner(OwnerKind.TASK, TASK_ID)] == ["On the task."]
    assert [one.text for one in await notes.for_owner(OwnerKind.FEATURE, TASK_ID)] == [
        "On the feature."
    ]


async def test_a_note_of_nothing_is_empty(notes: NoteRecords) -> None:
    assert await notes.for_owner(OwnerKind.QUESTION, TASK_ID) == []


async def test_a_notes_author_is_whoever_wrote_it(notes: NoteRecords) -> None:
    for author in ("ernie-c", "human:claude", "udl"):
        await notes.add(note_on(NoteKind.PROGRESS, f"By {author}.", author=author))

    assert [one.author for one in await notes.for_owner(OwnerKind.TASK, TASK_ID)] == [
        "ernie-c",
        "human:claude",
        "udl",
    ]


# ---------------------------------------------------------------------------------------------
# What a state change writes
# ---------------------------------------------------------------------------------------------


async def test_moving_a_task_writes_a_system_note(tasks: TaskRecords, notes: NoteRecords) -> None:
    """The card's own example: a state change is a `system` note ("ready → merged, by …"),
    and it names who did it — here the watcher that saw the merge."""
    # (A person's name is never guessed from the state it moved to.)
    task = await tasks.task("udl", "26.5")
    assert task is not None
    moved = await tasks.move(
        task.model_copy(update={"state": READY}),
        MERGED,
        by=Principal(kind="system", name="watcher"),
    )

    history = await notes.for_owner(OwnerKind.TASK, moved.id)

    assert [one.text for one in history] == ["ready → merged, by watcher."]
    assert history[0].note_kind is NoteKind.SYSTEM
    assert history[0].author == "udl"


async def test_a_merge_udl_makes_itself_is_not_the_watchers(
    tasks: TaskRecords, notes: NoteRecords
) -> None:
    """The actor is the caller's own name: a merge udl made itself is not the watcher's."""
    task = await tasks.task("udl", "26.5")
    assert task is not None
    moved = await tasks.move(
        task.model_copy(update={"state": READY}),
        MERGED,
        by=Principal(kind="system", name="udl"),
    )

    history = await notes.for_owner(OwnerKind.TASK, moved.id)

    assert [one.text for one in history] == ["ready → merged, by udl."]


async def test_a_move_by_a_person_names_them(tasks: TaskRecords, notes: NoteRecords) -> None:
    task = await tasks.task("udl", "26.5")
    assert task is not None
    moved = await tasks.move(
        task.model_copy(update={"state": QUEUED}),
        "cancelled",
        by=Principal(kind="human", name="human:claude"),
        note="no longer needed",
    )

    history = await notes.for_owner(OwnerKind.TASK, moved.id)

    assert [one.text for one in history] == ["queued → cancelled, by human:claude."]
    assert history[0].note_kind is NoteKind.SYSTEM


async def test_a_moved_task_writes_one_note_for_the_move(
    tasks: TaskRecords, notes: NoteRecords
) -> None:
    """An agent's own move is written the same way (the assign is its own to make)."""
    task = await tasks.task("udl", "26.5")
    assert task is not None
    moved = await tasks.move(
        task.model_copy(update={"state": QUEUED, "assignee": "ernie-c"}),
        "in_progress",
        by=Principal(kind="agent", name="ernie-c"),
    )

    history = await notes.for_owner(OwnerKind.TASK, moved.id)

    assert [one.text for one in history] == ["queued → in_progress, by ernie-c."]


async def test_a_note_the_state_change_wrote_is_the_only_one(
    tasks: TaskRecords, notes: NoteRecords
) -> None:
    """A move writes its note and nothing else: the task's own notes are left alone."""
    await notes.add(note_on(NoteKind.PROGRESS, "Tests first."))
    task = await tasks.task("udl", "26.5")
    assert task is not None
    moved = await tasks.move(
        task.model_copy(update={"state": QUEUED, "assignee": "ernie-c"}),
        "in_progress",
        by=Principal(kind="agent", name="ernie-c"),
    )

    history = await notes.for_owner(OwnerKind.TASK, moved.id)

    assert [one.text for one in history] == [
        "Tests first.",
        "queued → in_progress, by ernie-c.",
    ]
    assert [one.note_kind for one in history] == [NoteKind.PROGRESS, NoteKind.SYSTEM]


@pytest.mark.parametrize("by", ["udl", "watcher"])
async def test_a_merge_says_who_merged_it(tmp_path: Path, by: str) -> None:
    """The real path (`after_merge`): the note names who merged — udl itself, or the watcher
    that saw GitHub merge it — never a guess from the state."""
    from support.engine import databases, registry, settings
    from udl.config import Settings
    from udl.plugins.apps.orch.merge import after_merge

    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    records = AppRecords(db)
    notes = NoteRecords(records)
    await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
    await FeatureRecords(records).save_feature(Feature(project="udl", key="26", title="Work"))
    tasks = TaskRecords(records)
    await tasks.save_task(Task(project="udl", feature="26", key="26.5", title="Notes"))
    started = await tasks.task("udl", "26.5")
    assert started is not None
    queued = await tasks.move(started, QUEUED, by=Principal(kind="system", name="udl"))
    working = await tasks.move(queued, IN_PROGRESS, by=Principal(kind="system", name="udl"))
    ready = await tasks.move(working, READY, by=Principal(kind="system", name="udl"))
    await after_merge(records, project="udl", key="26.5", by=by, reviewed=False, promoted=False)

    history = await notes.for_owner(OwnerKind.TASK, ready.id)
    assert f"ready → merged, by {by}." in [one.text for one in history]


async def test_the_notes_are_stored_as_records(notes: NoteRecords) -> None:
    """A note is a udl record: the contract's example round-trips, and the kind is registered."""
    from udl.db.records import record_kinds

    assert Note in record_kinds()
    example = Note.example()
    assert Note.model_validate_json(example.model_dump_json()) == example
    assert await notes.add(note_on(NoteKind.REVIEW, "Reviewed against the card.")) is not None
