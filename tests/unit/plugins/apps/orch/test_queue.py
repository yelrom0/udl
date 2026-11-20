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
import asyncio
from collections.abc import Sequence
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import databases, registry, settings
from udl.config import Settings
from udl.db.records import Record
from udl.errors import DatabaseError, NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import Principal
from udl.plugins.apps.orch.queue import assign, next_task, queue_of, reorder, unassign
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
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
    # (The agents the queues are of: a task is only assigned to an agent there is, 27.1.)
    agents = AgentRecords(records)
    for name in ("ernie-c", "deepseek-b"):
        await agents.save_agent(Agent(name=name, title=name, runner="fake"))
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


async def test_an_agent_that_is_not_one_is_refused(tasks: TaskRecords) -> None:
    # (A typo would make a queue no dispatcher ever picks up: the task would vanish from view.)
    await a_task(tasks, "F-1.1")

    with pytest.raises(NotAllowed, match="erine-c") as refused:
        await assign(tasks, "F-1.1", "erine-c", project="udl", by=ME)

    assert "ernie-c" in (refused.value.hint or "")
    assert "deepseek-b" in (refused.value.hint or "")


async def test_a_person_is_named_and_a_persons_task_is_their_own(tasks: TaskRecords) -> None:
    await a_task(tasks, "F-1.1", task_kind="human")
    await a_task(tasks, "F-1.2")

    # (A person is named: `human:` alone is nobody.)
    with pytest.raises(NotAllowed, match="human"):
        await assign(tasks, "F-1.1", "human:", project="udl", by=ME)

    # (And a task that is nobody's person's work is not a person's to take: a build task is an
    # agent's, and a person's is theirs.)
    with pytest.raises(NotAllowed, match="not a person's task"):
        await assign(tasks, "F-1.2", "human:claude", project="udl", by=ME)

    assigned = await assign(tasks, "F-1.1", "human:claude", project="udl", by=ME)
    assert assigned.assignee == "human:claude"


async def test_a_task_an_agent_is_on_is_not_requeued_to_it(tasks: TaskRecords) -> None:
    # (An in-progress task's session is running: a drag that put it back on the same agent's
    # queue would leave that session working a task that is no longer its own.)
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    await assign(tasks, "F-1.2", "ernie-c", project="udl", by=ME)
    queued = await tasks.task("udl", "F-1.1")
    assert queued is not None
    await tasks.move(
        queued, "in_progress", by=Principal(kind="agent", name="ernie-c", role="start")
    )

    with pytest.raises(NotAllowed, match="on it"):
        await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)


async def test_a_refused_reassign_leaves_the_task_where_it_was(tasks: TaskRecords) -> None:
    # (The old agent's queue is only touched once the new assignment is certain.)
    await a_task(tasks, "F-1.1")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    queued = await tasks.task("udl", "F-1.1")
    assert queued is not None
    started = await tasks.move(
        queued, "in_progress", by=Principal(kind="agent", name="ernie-c", role="start")
    )
    await tasks.move(started, "ready", by=Principal(kind="agent", name="ernie-c", role="ready"))

    with pytest.raises(NotAllowed, match="ready"):
        await assign(
            tasks,
            "F-1.1",
            "deepseek-b",
            project="udl",
            by=Principal(kind="agent", name="ernie-c", role="assign"),
        )

    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    assert (found.state, found.assignee) == ("ready", "ernie-c")
    assert await notes_on(tasks, "F-1.1") == []


async def test_a_reassign_without_a_caller_moves_the_task_itself(tasks: TaskRecords) -> None:
    # (The operations call the queue with no caller: udl does it, and the two moves of a
    # reassign are the same move — not an assign that the table would refuse after the first.)
    await a_task(tasks, "F-1.1")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=None)
    queued = await tasks.task("udl", "F-1.1")
    assert queued is not None
    await tasks.move(
        queued, "in_progress", by=Principal(kind="agent", name="ernie-c", role="start")
    )

    moved = await assign(tasks, "F-1.1", "deepseek-b", project="udl", by=None)

    assert (moved.state, moved.assignee, moved.position) == ("queued", "deepseek-b", 0)
    assert await keys_of(tasks, "ernie-c") == []
    assert await keys_of(tasks, "deepseek-b") == ["F-1.1"]


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


# ---------------------------------------------------------------------------------------------
# One write, and one at a time (27.3a)
# ---------------------------------------------------------------------------------------------


async def test_a_queue_change_is_one_write(tasks: TaskRecords) -> None:
    # (Its positions are written together: half of one is a queue with two tasks in one place.)
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    await assign(tasks, "F-1.2", "ernie-c", project="udl", by=ME)
    writes: list[int] = []
    saving = tasks.records.save_all

    async def counted[R: Record](records: Sequence[R]) -> list[R]:
        writes.append(len(records))
        return await saving(records)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(tasks.records, "save_all", counted)
    try:
        await reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.2", "F-1.1"], by=ME)
    finally:
        monkeypatch.undo()

    assert writes == [2]


async def test_a_reassign_writes_only_the_tasks_whose_place_changed(
    tasks: TaskRecords,
) -> None:
    # (The ones that keep their place are not written: a write bumps updated_at, and on MongoDB
    # it is one more document in the transaction.)
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)
    writes: list[list[str]] = []
    saving = tasks.records.save_all

    async def counted[R: Record](records: Sequence[R]) -> list[R]:
        writes.append([getattr(record, "key", "?") for record in records])
        return await saving(records)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tasks.records, "save_all", counted)
        # (F-1.3 leaves ernie-c's queue: F-1.1 and F-1.2 keep their places there.)
        await assign(tasks, "F-1.3", "deepseek-b", project="udl", by=ME)

    # (The moved task, and the system note that says ernie-c must stop: nothing else.)
    assert writes == [["F-1.3", "?"]]
    assert len(writes) == 1


class MovedFirst(TaskRecords):
    """A store whose first read of a task is the one before it changed hands (27.3a's retry)."""

    def __init__(self, records: AppRecords) -> None:
        super().__init__(records)
        self.reads = 0
        self.stale: Task | None = None

    async def task(self, project: str, key: str) -> Task | None:
        found = await super().task(project, key)
        self.reads += 1
        if self.reads == 1 and self.stale is not None:
            return self.stale
        return found


async def test_an_assign_that_finds_the_task_moved_while_it_waited_starts_over(
    tasks: TaskRecords,
) -> None:
    # (The queues to lock are read before the lock is taken. If the task changed hands while the
    # call waited, the queue it is on now is not held: it is read again and both taken, so the
    # write never touches an unheld queue.)
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    await assign(tasks, "F-1.2", "deepseek-b", project="udl", by=ME)
    stale = await tasks.task("udl", "F-1.2")
    assert stale is not None
    moved = MovedFirst(tasks.records)
    moved.stale = stale.model_copy(update={"assignee": "ernie-c", "position": 0})

    assigned = await assign(moved, "F-1.2", "deepseek-b", project="udl", by=ME)

    assert (assigned.key, assigned.assignee) == ("F-1.2", "deepseek-b")
    assert moved.reads == 4  # (read, read under the lock, then both again: 4 in all)
    assert await keys_of(tasks, "deepseek-b") == ["F-1.2"]
    assert await keys_of(tasks, "ernie-c") == ["F-1.1"]


async def test_an_unassign_writes_only_the_tasks_whose_place_changed(
    tasks: TaskRecords,
) -> None:
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)
    writes: list[list[str]] = []
    saving = tasks.records.save_all

    async def counted[R: Record](records: Sequence[R]) -> list[R]:
        writes.append([getattr(record, "key", "?") for record in records])
        return await saving(records)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tasks.records, "save_all", counted)
        await unassign(tasks, project="udl", key="F-1.3", by=ME)

    assert writes == [["F-1.3"]]  # (the one that left, and no other: 0 and 1 are still 0 and 1)


async def test_a_queue_change_that_fails_leaves_the_queue_as_it_was(
    tasks: TaskRecords,
) -> None:
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    await assign(tasks, "F-1.2", "ernie-c", project="udl", by=ME)

    async def failing[R: Record](records: Sequence[R]) -> list[R]:
        raise DatabaseError("the database is gone")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(tasks.records, "save_all", failing)
        with pytest.raises(DatabaseError):
            await reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.2", "F-1.1"], by=ME)

    assert await keys_of(tasks, "ernie-c") == ["F-1.1", "F-1.2"]
    positions = [task.position for task in await queue_of(tasks, project="udl", agent="ernie-c")]
    assert positions == [0, 1]


async def test_two_reorders_at_once_leave_one_dense_order(tasks: TaskRecords) -> None:
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    await asyncio.gather(
        reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.3", "F-1.1", "F-1.2"], by=ME),
        reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.2", "F-1.3", "F-1.1"], by=ME),
    )

    queue = await queue_of(tasks, project="udl", agent="ernie-c")
    assert [task.position for task in queue] == [0, 1, 2]
    assert sorted(task.key for task in queue) == ["F-1.1", "F-1.2", "F-1.3"]


async def test_a_reassign_racing_a_reorder_of_the_old_queue_leaves_both_dense(
    tasks: TaskRecords,
) -> None:
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await a_task(tasks, key)
        await assign(tasks, key, "ernie-c", project="udl", by=ME)

    moved, reordered = await asyncio.gather(
        assign(tasks, "F-1.2", "deepseek-b", project="udl", by=ME),
        reorder(tasks, project="udl", agent="ernie-c", keys=["F-1.3", "F-1.1", "F-1.2"], by=ME),
        return_exceptions=True,
    )

    # (Whichever took the queue's lock first: the reassign always goes through, and the reorder
    # is refused only when it ran second, naming a task that had just left the queue.)
    assert not isinstance(moved, BaseException)
    if isinstance(reordered, BaseException):
        assert isinstance(reordered, NotAllowed)

    left = await queue_of(tasks, project="udl", agent="ernie-c")
    right = await queue_of(tasks, project="udl", agent="deepseek-b")
    assert [task.position for task in left] == list(range(len(left)))
    assert [task.position for task in right] == list(range(len(right)))
    assert "F-1.2" not in [task.key for task in left]
    assert [task.key for task in right] == ["F-1.2"]


async def test_two_opposite_reassigns_at_once_both_finish(tasks: TaskRecords) -> None:
    # (A reassign takes both queues' locks, in a fixed order: taken in the callers' order
    # instead, A→B and B→A would wait on each other forever.)
    await a_task(tasks, "F-1.1")
    await a_task(tasks, "F-1.2")
    await assign(tasks, "F-1.1", "ernie-c", project="udl", by=ME)
    await assign(tasks, "F-1.2", "deepseek-b", project="udl", by=ME)

    async with asyncio.timeout(5):
        await asyncio.gather(
            assign(tasks, "F-1.1", "deepseek-b", project="udl", by=ME),
            assign(tasks, "F-1.2", "ernie-c", project="udl", by=ME),
        )

    assert await keys_of(tasks, "ernie-c") == ["F-1.2"]
    assert await keys_of(tasks, "deepseek-b") == ["F-1.1"]
