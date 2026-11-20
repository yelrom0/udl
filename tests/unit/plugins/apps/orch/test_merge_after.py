"""
------------------
- test_merge_after.py -
------------------

What a merge sets off (28.3): the task moves to `merged` (and `done` at once when the merge is a
review and nothing follows the base), the tasks stacked on it are told to rebase and push again,
and a person's actions waiting `after` it are `now`. And a pull closed without merging sends its
task back to work, with the closing comment as data.
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import settings
from udl.core.service import UDLService
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.actions import ActionRecords, set_action
from udl.plugins.apps.orch.merge import after_close, after_merge, stacked
from udl.plugins.apps.orch.models import (
    DONE,
    IN_PROGRESS,
    MERGED,
    OPEN,
    READY,
    ActionState,
    ActionWhen,
    NoteKind,
    Principal,
)
from udl.plugins.apps.orch.records import (
    Feature,
    FeatureRecords,
    NoteRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)

AT: datetime = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
"""When the tests are at."""


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project, a feature and a task in `ready`."""
    async with UDLService(settings(tmp_path)) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", promotion=["rc", "main"])
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        tasks = TaskRecords(records)
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="It"))
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
        moved = await tasks.task("udl", "F-1.1")
        assert moved is not None
        await tasks.move(moved, READY, by=Principal(kind="system", role="ready"))
        yield running


async def add_task(service: UDLService, key: str, *, needs: list[str] | None = None) -> Task:
    """A task of the project, in progress (as an agent's is while it works)."""
    tasks = TaskRecords(AppRecords(service.db))
    await tasks.save_task(
        Task(project="udl", feature="F-1", key=key, title=f"Task {key}", needs=needs or [])
    )
    found = await tasks.task("udl", key)
    assert found is not None
    await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
    moved = await tasks.task("udl", key)
    assert moved is not None
    return moved


def records_of(service: UDLService) -> AppRecords:
    """The app records the tests read through."""
    return AppRecords(service.db)


async def test_a_merge_moves_the_task_to_merged(service: UDLService) -> None:
    await after_merge(
        records_of(service),
        project="udl",
        key="F-1.1",
        by="yelrom0",
        reviewed=False,
        promoted=True,
    )

    found = await TaskRecords(records_of(service)).task("udl", "F-1.1")

    assert found is not None
    assert found.state == MERGED


async def test_a_reviewed_merge_with_nothing_after_the_base_is_done_at_once(
    service: UDLService,
) -> None:
    await after_merge(
        records_of(service),
        project="udl",
        key="F-1.1",
        by="claude[bot]",
        reviewed=True,
        promoted=False,
    )

    found = await TaskRecords(records_of(service)).task("udl", "F-1.1")

    assert found is not None
    assert found.state == DONE


async def test_a_reviewed_merge_where_promotion_follows_stops_at_merged(
    service: UDLService,
) -> None:
    await after_merge(
        records_of(service),
        project="udl",
        key="F-1.1",
        by="claude[bot]",
        reviewed=True,
        promoted=True,
    )

    found = await TaskRecords(records_of(service)).task("udl", "F-1.1")

    assert found is not None
    assert found.state == MERGED


async def test_the_task_says_who_merged_it(service: UDLService) -> None:
    await after_merge(
        records_of(service),
        project="udl",
        key="F-1.1",
        by="yelrom0",
        reviewed=False,
        promoted=True,
    )

    found = await TaskRecords(records_of(service)).task("udl", "F-1.1")
    assert found is not None
    notes = await NoteRecords(records_of(service)).notes("task", found.id)

    assert [note.text for note in notes] == ["Merged by yelrom0."]
    assert notes[0].note_kind == NoteKind.SYSTEM


async def test_each_stacked_task_is_told_to_rebase_and_push(service: UDLService) -> None:
    await add_task(service, "F-1.2", needs=["F-1.1"])
    await add_task(service, "F-1.3", needs=["F-1.2"])

    changed = await after_merge(
        records_of(service),
        project="udl",
        key="F-1.1",
        by="yelrom0",
        reviewed=False,
        promoted=True,
    )

    assert [one.key for one in changed] == ["F-1.1", "F-1.2"]
    stacked_task = await TaskRecords(records_of(service)).task("udl", "F-1.2")
    assert stacked_task is not None
    notes = await NoteRecords(records_of(service)).notes("task", stacked_task.id)
    assert "Rebase onto the base and push again" in notes[-1].text
    assert notes[-1].note_kind == NoteKind.SYSTEM


async def test_stacked_are_the_tasks_that_need_it_and_are_not_done(service: UDLService) -> None:
    await add_task(service, "F-1.2", needs=["F-1.1"])
    await add_task(service, "F-1.3", needs=["F-1.2"])
    await add_task(service, "F-1.4")

    found = await stacked(records_of(service), project="udl", key="F-1.1")

    assert [one.key for one in found] == ["F-1.2"]


async def test_an_action_waiting_after_it_is_now(service: UDLService) -> None:
    actions = ActionRecords(records_of(service))
    await set_action(
        actions,
        project="udl",
        title="Check the docs",
        when=ActionWhen.AFTER,
        after="F-1.1",
    )

    await after_merge(
        records_of(service),
        project="udl",
        key="F-1.1",
        by="yelrom0",
        reviewed=False,
        promoted=True,
    )

    waiting = await actions.actions("udl", state=ActionState.OPEN)

    assert [(one.when, one.after) for one in waiting] == [(ActionWhen.NOW, None)]


async def test_a_pull_closed_without_merging_sends_the_task_back_to_work(
    service: UDLService,
) -> None:
    found = await after_close(
        records_of(service), project="udl", key="F-1.1", why="Not this way: split it."
    )

    assert found is not None
    assert found.state == IN_PROGRESS
    notes = await NoteRecords(records_of(service)).notes("task", found.id)
    assert "Not this way: split it." in notes[-1].text


async def test_a_closed_pull_for_a_task_nobody_started_moves_nothing(service: UDLService) -> None:
    tasks = TaskRecords(records_of(service))
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.9", title="Not yet"))

    found = await after_close(records_of(service), project="udl", key="F-1.9", why="")

    assert found is None
    found = await tasks.task("udl", "F-1.9")
    assert found is not None
    assert found.state == OPEN
