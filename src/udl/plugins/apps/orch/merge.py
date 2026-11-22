"""
------------
- merge.py -
------------

Merging (28.3): what is ready to merge, in the order it merges, and what a merge sets off.

`order()` is the pure part: the ready pulls of a project, grouped by repo, each after the ones it
needs (a stack merges bottom first), then by when it became ready, then by key. What reads GitHub
is the app's `orch_merge_list` and `orch_merge`; what a merge sets off, however it happened, is
`after_merge()`.
"""

# System Imports
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

from udl.automations.events import EventType
from udl.clock import Clock

# Local Imports
from udl.errors import DoesNotExist
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.actions import ActionRecords, become_now
from udl.plugins.apps.orch.models import (
    CANCELLED,
    DONE,
    IN_PROGRESS,
    MERGED,
    OPEN,
    Key,
    MergeRow,
    NoteKind,
    OwnerKind,
    Principal,
)
from udl.plugins.apps.orch.records import Note, NoteRecords, Task, TaskRecords

__all__ = ["TASK_MERGED", "TaskMerged", "after_close", "after_merge", "order", "stacked"]


class TaskMerged(BaseModel):
    """What orch publishes when a task's pull is merged (28.7): by whom, and whether a merger
    merged it rather than a person."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    project: str
    """The project the task is in."""
    task: str
    """The task, by key (`28.3`)."""
    pull: str
    """Its pull's URL."""
    sha: str
    """The commit that merged."""
    by: str
    """Who merged it: a person's name, or the project's merger's."""
    auto: bool = False
    """Whether a merger merged it (auto merge, 28.7) rather than a person."""


TASK_MERGED: Final = EventType(
    name="orch.task.merged",
    data=TaskMerged,
    summary="A task's pull is merged",
    example=TaskMerged(
        project="udl",
        task="28.7",
        pull="https://github.com/yelrom0/udl/pull/80",
        sha="dc1fd19",
        by="claude",
        auto=True,
    ),
    subject=lambda event: f"Task {event.task} is merged: {event.pull}",
    source="orch",
)


def _actor(name: str) -> Principal:
    """Who a merge is, for the note: udl's own hand on it, named as it was given.

    A merge is udl's to make (O-C7: an agent never merges), so the principal is udl's — what it
    is called in the note is who asked for or saw it: the watcher that saw GitHub merge it, the
    caller of `orch_merge`, or udl itself."""
    return Principal(kind="system", name=name or "udl")


_REVIEWER: Final = Principal(kind="agent", name="reviewer", role="review")
"""The reviewer's merge is its review: it is what reaches `done`."""

_BACK: Final = Principal(kind="system", role="move")
"""udl itself moving a task back to work: its pull was closed without merging."""

DAYS: Final = 14
"""How long a merged task stays in the list: what merged lately (the card's 14 days)."""


def order(rows: Sequence[MergeRow]) -> list[MergeRow]:
    """`rows` in the order they merge: by repo, and inside a repo each after the rows it needs,
    then by when it became ready, then by key.

    A row whose need is not in `rows` is not held up by it (it is in another state or another
    list), and two rows that need each other are taken by key rather than left out: the sort
    always ends, and every row is in its answer."""
    found: list[MergeRow] = []
    waiting = sorted(rows, key=_by)
    here = {one.task for one in waiting}
    while waiting:
        ready = [
            one
            for one in waiting
            if not any(need in here and not _taken(need, found) for need in one.needs)
        ]
        one = (ready or waiting)[0]  # (a cycle: nothing can go first, so by key)
        found.append(one)
        waiting.remove(one)
    return found


def _by(row: MergeRow) -> tuple[str, float, str]:
    """What a row sorts by when nothing it needs decides: its repo, when it became ready, its
    key."""
    return (row.repo, row.since.timestamp() if row.since is not None else 0.0, row.task)


def _taken(need: str, found: Sequence[MergeRow]) -> bool:
    """Whether `need` is already in the answer."""
    return any(one.task == need for one in found)


# ---------------------------------------------------------------------------------------------
# What a merge sets off
# ---------------------------------------------------------------------------------------------


async def after_merge(
    records: AppRecords,
    *,
    project: str,
    key: Key,
    by: str,
    reviewed: bool,
    promoted: bool,
    auto: bool = False,
    clock: Clock | None = None,
) -> list[Task]:
    """`key` merged: its task moves to `merged`, and to `done` at once when the merge was a
    review (`reviewed`) and nothing follows the base (`promoted` is False).

    Every task stacked on it is told, in a `system` note, to rebase onto the base and push again,
    and every action waiting `after` it is `now`. Answers with the tasks that changed: the merged
    one first, then each stacked one."""
    tasks = TaskRecords(records, clock=clock)
    found = await tasks.task(project, key)
    if found is None:
        raise DoesNotExist(f"There is no task called {key!r} in {project!r}")
    if found.state in (MERGED, DONE):
        # (A merge udl made itself is also one the watcher sees: the second pass writes nothing,
        # so the notes and the rebase prompts are written once.)
        return [found]
    notes = NoteRecords(records)
    moved = await tasks.move(found, MERGED, by=_actor(by))
    done = reviewed and not promoted
    if done:
        moved = await tasks.move(moved, DONE, by=_REVIEWER)
    await notes.add(
        Note(
            owner_kind=OwnerKind.TASK,
            owner=moved.id,
            note_kind=NoteKind.SYSTEM,
            text=(
                f"Merged by {by}{' (auto merge)' if auto else ''}."
                if not done
                else f"Merged by {by}{' (auto merge)' if auto else ''}, and done: nothing "
                "follows the base."
            ),
        )
    )
    changed = [moved]
    for following in await stacked(records, project=project, key=key):
        await notes.add(
            Note(
                owner_kind=OwnerKind.TASK,
                owner=following.id,
                note_kind=NoteKind.SYSTEM,
                text=(
                    f"{key} merged. Rebase onto the base and push again (`orch_push`): what it "
                    "was built on has moved."
                ),
            )
        )
        changed.append(following)
    await become_now(ActionRecords(records), project=project, task=key)
    return changed


async def after_close(
    records: AppRecords, *, project: str, key: Key, why: str, clock: Clock | None = None
) -> Task | None:
    """The pull of `key` was closed without merging: its task goes back to `in_progress`, with
    `why` (the closing comment, as data) in a `system` note. Answers with the task, or None when
    there is none to move."""
    tasks = TaskRecords(records, clock=clock)
    found = await tasks.task(project, key)
    if found is None or found.state in (MERGED, DONE, CANCELLED, OPEN):
        return None
    await NoteRecords(records).add(
        Note(
            owner_kind=OwnerKind.TASK,
            owner=found.id,
            note_kind=NoteKind.SYSTEM,
            text=(
                f"Its pull was closed without merging.\n\n{why}"
                if why
                else "Its pull was closed without merging."
            ),
        )
    )
    return await tasks.move(found, IN_PROGRESS, by=_BACK)


async def stacked(records: AppRecords, *, project: str, key: Key) -> list[Task]:
    """The tasks of `project` that need `key` and are not done yet: what a merge sends back to
    its work."""
    return [
        one
        for one in await TaskRecords(records).tasks(project)
        if key in one.needs and one.state not in (DONE, CANCELLED)
    ]
