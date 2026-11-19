"""
----------------------------
- test_queue.py -
----------------------------

Agents' queues (27.3, the card's second bullet): assigning a task to an agent puts it in that
agent's queue, in the order the agent will work it; a reorder sets the order to exactly the keys
it is given; and unassigning takes a task out of the queue and leaves no gap.

The queue's positions are dense integers (0, 1, 2 …) per assignee, renumbered whenever it
changes, so the order is read from the positions alone. Assigning a task another agent holds
moves it: it leaves that agent's queue (which closes its gap), and a system note says who must
stop working on it.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import databases, registry, settings
from udl.config import Settings
from udl.errors import NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import Principal
from udl.plugins.apps.orch.queue import assign, next_task, queue_of, reorder, unassign
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

ME = Principal(kind="human", name="human:claude")


@pytest.fixture
async def tasks(tmp_path: Path) -> TaskRecords:
    """Orch's tasks over a fresh in-memory database, with one project and two features."""
    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    records = AppRecords(db)
    await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
    features = FeatureRecords(records)
    await features.save_feature(Feature(project="udl", key="F-1", title="The queue"))
    await features.save_feature(Feature(project="udl", key="F-2", title="The dispatcher"))
    return TaskRecords(records)


async def a_task(
    tasks: TaskRecords,
    key: str,
    *,
    title: str = "x",
    feature: str = "F-1",
    task_kind: str = "build",
) -> Task:
    """A task of the project, saved open, and read back (as a caller has it)."""
    await tasks.save_task(
        Task(project="udl", feature=feature, key=key, title=title, task_kind=task_kind)  # pyright: ignore[reportArgumentType]
    )
    found = await tasks.task("udl", key)
    assert found is not None
    return found


async def keys_of(tasks: TaskRecords, agent: str) -> list[str]:
    """The keys on `agent`'s queue, in the order they will be worked."""
    return [task.key for task in await queue_of(tasks, project="udl", agent=agent)]


async def notes_on(tasks: TaskRecords, key: str) -> list[Note]:
    """The notes on the task called `key`."""
    task = await tasks.task("udl", key)
    assert task is not None
    return await NoteRecords(tasks.records).notes("task", task.id)


# ---------------------------------------------------------------------------------------------
# Assigning
# ---------------------------------------------------------------------------------------------


async def test_assigning_an_open_task_queues_it_at_the_end(tasks: TaskRecords) -> None:
    await a_task(tasks, "F-1.1")

    assigned = await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)

    assert (assigned.state, assigned.assignee, assigned.position) == ("queued", "ernie-c", 0)
    assert await keys_of(tasks, "ernie-c") == ["F-1.1"]


async def test_a_second_task_joins_the_queue_after_the_first(tasks: TaskRecords) -> None:
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")

    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    second = await assign(tasks, "F-1.2", "ernie-c", project="udl", by=ME)

    assert second.position == 1
    assert await keys_of(tasks, "ernie-c") == ["F-1.1", "F-1.2"]


async def test_assigning_at_a_position_puts_it_there_and_renumbers_the_rest(
    tasks: TaskRecords,
) -> None:
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    moved = await assign(tasks, "F-1.3", "ernie-c", project="udl", position=1, by=ME)

    assert moved.position == 1
    assert await keys_of(tasks, "ernie-c") == ["F-1.1", "F-1.3", "F-1.2"]
    # (The positions are dense: 0, 1, 2, whatever was there before.)
    assert [task.position for task in await queue_of(tasks, project="udl", agent="ernie-c")] == [
        0,
        1,
        2,
    ]


async def test_assigning_past_the_end_of_a_queue_is_refused(tasks: TaskRecords) -> None:
    await a_task(tasks, "F-1.1")

    with pytest.raises(NotAllowed, match="position 3"):
        await assign(tasks, "F-1.1", "ernie-c", project="udl", position=3, by=ME)

    with pytest.raises(NotAllowed, match="position -1"):
        await assign(tasks, "F-1.1", "ernie-c", project="udl", position=-1, by=ME)

    # (A refused assign changes nothing.)
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    assert (found.state, found.assignee) == ("open", None)


async def test_a_task_another_agent_holds_leaves_its_queue_and_says_who_stops(
    tasks: TaskRecords,
) -> None:
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    await assign(tasks, "F-1.2", "ernie-c", project="udl", by=ME)

    moved = await assign(tasks, "F-1.1", "deepseek-b", project="udl", by=ME)

    assert (moved.state, moved.assignee, moved.position) == ("queued", "deepseek-b", 0)
    assert await keys_of(tasks, "ernie-c") == ["F-1.2"]
    # (The queue it left has no gap: the task after it is now the first.)
    assert [task.position for task in await queue_of(tasks, project="udl", agent="ernie-c")] == [0]
    assert await keys_of(tasks, "deepseek-b") == ["F-1.1"]
    # (A system note says who must stop, so the old agent's session is told to let it go.)
    written = await notes_on(tasks, "F-1.1")
    assert [(note.note_kind, note.author) for note in written] == [("system", "udl")]
    assert "ernie-c" in written[0].text
    assert "stop" in written[0].text


async def test_a_task_an_agent_is_on_is_taken_off_it_and_queued_again(
    tasks: TaskRecords,
) -> None:
    await a_task(tasks, "F-1.1")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    queued = await tasks.task("udl", "F-1.1")
    assert queued is not None
    await tasks.move(
        queued, "in_progress", by=Principal(kind="agent", name="ernie-c", role="start")
    )

    moved = await assign(tasks, "F-1.1", "deepseek-b", project="udl", by=ME)

    assert (moved.state, moved.assignee) == ("queued", "deepseek-b")
    assert await keys_of(tasks, "ernie-c") == []
    assert "ernie-c must stop" in (await notes_on(tasks, "F-1.1"))[0].text


async def test_a_task_is_moved_through_the_table_not_around_it(tasks: TaskRecords) -> None:
    # (A task in `ready` has a PR waiting: it is not somebody's to queue again.)
    await a_task(tasks, "F-1.1")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    queued = await tasks.task("udl", "F-1.1")
    assert queued is not None
    started = await tasks.move(
        queued, "in_progress", by=Principal(kind="agent", name="ernie-c", role="start")
    )
    await tasks.move(started, "ready", by=Principal(kind="agent", name="ernie-c", role="ready"))

    # (The table has no way out of `ready` into a queue, for the agent or for a person: a ready
    # task has a PR waiting, and is merged or sent back to work, not queued again.)
    with pytest.raises(NotAllowed, match="ready"):
        await assign(
            tasks, "F-1.1", "ernie-c", project="udl", by=Principal(kind="agent", name="ernie-c")
        )


async def test_a_human_task_is_a_persons_and_never_an_agents(tasks: TaskRecords) -> None:
    await a_task(tasks, "F-1.1", task_kind="human")

    with pytest.raises(NotAllowed, match="human"):
        await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)

    assigned = await assign(tasks, "F-1.1", "human:claude", project="udl", by=ME)

    assert (assigned.state, assigned.assignee) == ("queued", "human:claude")
    assert await keys_of(tasks, "human:claude") == ["F-1.1"]
    assert await keys_of(tasks, "ernie-c") == []


async def test_assigning_a_task_that_is_not_there_is_refused(tasks: TaskRecords) -> None:
    from udl.errors import DoesNotExist

    with pytest.raises(DoesNotExist):
        await assign(tasks, "F-1.9", "ernie-c", project="udl", by=ME)


# ---------------------------------------------------------------------------------------------
# Reordering
# ---------------------------------------------------------------------------------------------


async def test_a_reorder_sets_the_order_to_exactly_its_keys(tasks: TaskRecords) -> None:
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    reordered = await reorder(
        tasks, project="udl", agent="ernie-c", keys=["F-1.3", "F-1.1", "F-1.2"], by=ME
    )

    assert [task.key for task in reordered] == ["F-1.3", "F-1.1", "F-1.2"]
    assert [task.position for task in reordered] == [0, 1, 2]
    assert await keys_of(tasks, "ernie-c") == ["F-1.3", "F-1.1", "F-1.2"]


async def test_a_reorder_must_name_every_task_on_the_queue_once(tasks: TaskRecords) -> None:
    for key in ("F-1.1", "F-1.2"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    with pytest.raises(NotAllowed, match=r"F\-1\.2"):
        await reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.1"], by=ME)

    with pytest.raises(NotAllowed, match=r"F\-1\.9"):
        await reorder(
            tasks, project="udl", agent="ernie-c", keys=["F-1.1", "F-1.2", "F-1.9"], by=ME
        )

    with pytest.raises(NotAllowed, match=r"F\-1\.1"):
        await reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.1", "F-1.1"], by=ME)

    # (Refused, nothing moved.)
    assert await keys_of(tasks, "ernie-c") == ["F-1.1", "F-1.2"]


async def test_a_reorder_names_only_that_agents_queued_tasks(tasks: TaskRecords) -> None:
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)

    # (A task that is not on the queue is not the reorder's to move.)
    with pytest.raises(NotAllowed, match=r"F\-1\.2"):
        await reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.2", "F-1.1"], by=ME)


# ---------------------------------------------------------------------------------------------
# Unassigning
# ---------------------------------------------------------------------------------------------


async def test_unassigning_returns_the_task_to_open_and_closes_the_gap(tasks: TaskRecords) -> None:
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    freed = await unassign(tasks, project="udl", key="F-1.2", by=ME)

    assert (freed.state, freed.assignee, freed.position) == ("open", None, None)
    assert await keys_of(tasks, "ernie-c") == ["F-1.1", "F-1.3"]
    assert [task.position for task in await queue_of(tasks, project="udl", agent="ernie-c")] == [
        0,
        1,
    ]


async def test_unassigning_a_task_nobody_has_is_refused(tasks: TaskRecords) -> None:
    await a_task(tasks, "F-1.1")

    with pytest.raises(NotAllowed, match="nobody"):
        await unassign(tasks, project="udl", key="F-1.1", by=ME)


# ---------------------------------------------------------------------------------------------
# What an agent is to work next
# ---------------------------------------------------------------------------------------------


async def test_the_queue_is_read_by_position(tasks: TaskRecords) -> None:
    for key in ("F-1.1", "F-1.2"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    assert await keys_of(tasks, "ernie-c") == ["F-1.1", "F-1.2"]
    assert await keys_of(tasks, "deepseek-b") == []


async def test_my_task_is_the_one_i_am_on_or_the_first_queued(tasks: TaskRecords) -> None:
    for key in ("F-1.1", "F-1.2"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    first = await next_task(tasks, project="udl", agent="ernie-c")
    assert first is not None
    assert first.key == "F-1.1"

    # (Once it starts the first, that is the one it is on: not the next queued.)
    started = await tasks.move(
        first, "in_progress", by=Principal(kind="agent", name="ernie-c", role="start")
    )

    on_it = await next_task(tasks, project="udl", agent="ernie-c")
    assert on_it is not None
    assert on_it.key == started.key


async def test_an_agent_with_nothing_to_do_has_no_task(tasks: TaskRecords) -> None:
    assert await next_task(tasks, project="udl", agent="ernie-c") is None
