"""
------------------------------
- test_features.py -
------------------------------

Features and tasks (udl.plugins.apps.orch): what a Feature says (its key in its project, its
spec, its status and its order), what a Task says (its feature, its key, its kind, its state,
what it needs, its repo, its assignee and position, its hold, what blocks it, its branch and
worktree, who moved it), and the Counter that hands out the keys — a claim at a time, so two
callers never get the same number. Stored, they are udl records (orch.records).
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

from udl.config import Settings
from udl.errors import DoesNotExist, DuplicateKey, NotAllowed
from udl.plugins.apps.base import AppRecords

# Local Imports
from udl.plugins.apps.orch.models import OPEN, Principal
from udl.plugins.apps.orch.records import (
    Counters,
    Feature,
    FeatureRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)

# ---------------------------------------------------------------------------------------------
# A feature
# ---------------------------------------------------------------------------------------------


def test_a_feature_holds_its_project_key_title_and_spec() -> None:
    feature = Feature(
        project="udl",
        key="F-1",
        title="Work tracking",
        spec="The plan's Phase 26, as its section reads.",
        status="active",
        order=26,
    )

    assert (feature.project, feature.key, feature.title, feature.order) == (
        "udl",
        "F-1",
        "Work tracking",
        26,
    )
    assert feature.status == "active"
    assert "Phase 26" in feature.spec


def test_a_new_feature_is_planned() -> None:
    assert Feature(project="udl", key="F-2", title="x").status == "planned"


def test_a_feature_may_not_be_in_a_status_there_is_not() -> None:
    with pytest.raises(ValueError, match="status"):
        Feature(project="udl", key="F-3", title="x", status="nearly")  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------------------------
# A task
# ---------------------------------------------------------------------------------------------


def test_a_task_holds_what_the_card_says() -> None:
    task = Task(
        project="udl",
        feature="F-1",
        key="F-1.1",
        title="The records",
        spec="Its card.",
        task_kind="build",
        needs=["F-1.0"],
        repo="udl",
        assignee="ernie-c",
        position=3,
        hold="built now, merged later",
        blocked_on="Q-7",
        branch="feat/F-1.1-records",
        worktree="~/Documents/code/udl-wt/F-1.1-records",
        updated_by="ernie-c",
    )

    assert (task.feature, task.key, task.task_kind, task.state) == ("F-1", "F-1.1", "build", OPEN)
    assert task.needs == ["F-1.0"]
    assert (task.assignee, task.position) == ("ernie-c", 3)
    assert (task.hold, task.blocked_on) == ("built now, merged later", "Q-7")
    assert (task.worktree or "").endswith("F-1.1-records")


def test_a_new_task_is_open_and_nobodys() -> None:
    task = Task(project="udl", feature="F-1", key="F-1.2", title="x")

    assert (task.state, task.assignee, task.task_kind) == (OPEN, None, "build")


# ---------------------------------------------------------------------------------------------
# Storing them
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def records(tmp_path: Path) -> FeatureRecords:
    """Orch's records over a fresh in-memory database (as tests/support/engine.py makes one)."""
    from support.engine import databases, registry, settings

    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    feature_records = FeatureRecords(AppRecords(db))
    await ProjectRecords(feature_records.records).save_project(Project(name="udl", title="udl"))
    return feature_records


@pytest.fixture
async def tasks(records: FeatureRecords) -> TaskRecords:
    """Orch's tasks, over the same records (so a task's project is the one saved above)."""
    return TaskRecords(records.records)


@pytest.fixture
async def counters(records: FeatureRecords) -> Counters:
    """Orch's counters, over the same records."""
    return Counters(records.records)


async def test_a_saved_feature_is_read_back(records: FeatureRecords) -> None:
    feature = Feature(project="udl", key="F-1", title="Work tracking", order=1)
    saved = await records.save_feature(feature)

    assert await records.feature("udl", "F-1") == saved
    assert [found.key for found in await records.features("udl")] == ["F-1"]
    assert await records.feature("udl", "F-2") is None


async def test_a_second_feature_with_the_same_key_is_refused(records: FeatureRecords) -> None:
    await records.save_feature(Feature(project="udl", key="F-1", title="Work tracking"))

    with pytest.raises(DuplicateKey):
        await records.save_feature(Feature(project="udl", key="F-1", title="again"))


async def test_a_feature_of_a_project_that_is_not_there_is_refused(
    records: FeatureRecords,
) -> None:
    with pytest.raises(DoesNotExist):
        await records.save_feature(Feature(project="liively", key="F-1", title="x"))


async def test_features_are_read_in_their_order_and_then_by_key(
    records: FeatureRecords,
) -> None:
    for key, order in (("F-3", 2), ("F-1", 1), ("F-2", 1)):
        await records.save_feature(Feature(project="udl", key=key, title=key, order=order))

    assert [found.key for found in await records.features("udl")] == ["F-1", "F-2", "F-3"]


async def test_deleting_a_feature(records: FeatureRecords) -> None:
    await records.save_feature(Feature(project="udl", key="F-1", title="x"))

    assert await records.delete_feature("udl", "F-1") is True
    assert await records.delete_feature("udl", "F-1") is False
    assert await records.feature("udl", "F-1") is None


async def test_a_saved_task_is_read_back(tasks: TaskRecords) -> None:
    task = Task(project="udl", feature="F-1", key="F-1.1", title="The records")
    saved = await tasks.save_task(task)

    assert await tasks.task("udl", "F-1.1") == saved
    assert [found.key for found in await tasks.tasks("udl")] == ["F-1.1"]
    assert await tasks.task("udl", "F-1.2") is None


async def test_a_second_task_with_the_same_key_is_refused(tasks: TaskRecords) -> None:
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="x"))

    with pytest.raises(DuplicateKey):
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="again"))


async def test_a_task_of_a_project_that_is_not_there_is_refused(tasks: TaskRecords) -> None:
    with pytest.raises(DoesNotExist):
        await tasks.save_task(Task(project="liively", feature="F-1", key="F-1.1", title="x"))


async def test_tasks_are_read_by_feature_state_and_assignee(tasks: TaskRecords) -> None:
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="a"))
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.2", title="b"))
    await tasks.save_task(Task(project="udl", feature="F-2", key="F-2.1", title="c"))
    # (A task is saved open and then moved: `queued` is a state a move sets, never a save.)
    mine = Principal(kind="human", name="human:claude")
    second = await tasks.task("udl", "F-1.2")
    assert second is not None
    await tasks.move(second, "queued", by=mine)
    queued = await tasks.task("udl", "F-1.2")
    assert queued is not None
    await tasks.save_task(queued.model_copy(update={"assignee": "ernie-c"}))

    def keys(found: list[Task]) -> list[str]:
        return [task.key for task in found]

    assert keys(await tasks.tasks("udl")) == ["F-1.1", "F-1.2", "F-2.1"]
    assert keys(await tasks.tasks("udl", feature="F-1")) == ["F-1.1", "F-1.2"]
    assert keys(await tasks.tasks("udl", state="queued")) == ["F-1.2"]
    assert keys(await tasks.tasks("udl", assignee="ernie-c")) == ["F-1.2"]


async def test_deleting_a_task(tasks: TaskRecords) -> None:
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="x"))

    assert await tasks.delete_task("udl", "F-1.1") is True
    assert await tasks.delete_task("udl", "F-1.1") is False
    assert await tasks.task("udl", "F-1.1") is None


async def test_a_task_is_saved_open_and_moved_through_the_table(tasks: TaskRecords) -> None:
    # (The card's done-when: no code path sets `state` except move(). A save cannot, and a move
    # goes through the table, so a role the table refuses is refused here too.)
    with pytest.raises(NotAllowed, match="cannot start in 'ready'"):
        await tasks.save_task(
            Task(project="udl", feature="F-1", key="F-1.1", title="x", state="ready")
        )

    saved = await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="x"))
    moved = await tasks.move(saved, "queued", by=Principal(kind="human", name="human:claude"))

    assert (moved.state, await tasks.task("udl", "F-1.1")) == ("queued", moved)

    theirs = await tasks.save_task(moved.model_copy(update={"assignee": "ernie-b"}))
    with pytest.raises(NotAllowed, match=r"from 'queued' to 'ready'"):
        await tasks.move(theirs, "ready", by=Principal(kind="agent", name="ernie-b", role="ready"))


async def test_a_save_may_not_move_a_task_to_another_state(tasks: TaskRecords) -> None:
    saved = await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="x"))

    with pytest.raises(NotAllowed, match="would be saved in 'ready'"):
        await tasks.save_task(saved.model_copy(update={"state": "ready"}))


# ---------------------------------------------------------------------------------------------
# A feature's status follows its tasks
# ---------------------------------------------------------------------------------------------


async def test_a_feature_is_planned_while_its_tasks_are_open(
    records: FeatureRecords, tasks: TaskRecords
) -> None:
    await records.save_feature(Feature(project="udl", key="F-1", title="x"))
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="a"))

    found = await records.feature("udl", "F-1")
    assert found is not None
    assert found.status == "planned"


async def test_a_feature_is_active_once_a_task_is_past_open(
    records: FeatureRecords, tasks: TaskRecords
) -> None:
    await records.save_feature(Feature(project="udl", key="F-1", title="x"))
    saved = await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="a"))
    await tasks.move(saved, "queued", by=Principal(kind="human", name="human:claude"))

    found = await records.feature("udl", "F-1")
    assert found is not None
    assert found.status == "active"


async def test_a_feature_is_done_when_all_its_tasks_are_done_or_cancelled(
    records: FeatureRecords, tasks: TaskRecords
) -> None:
    await records.save_feature(Feature(project="udl", key="F-1", title="x"))
    first = await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="a"))
    second = await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.2", title="b"))
    reviewer = Principal(kind="agent", name="reviewer")
    person = Principal(kind="human", name="human:claude")
    first = await tasks.move(first, "ready", by=person)
    first = await tasks.move(first, "merged", by=reviewer)
    await tasks.move(first, "done", by=reviewer)

    halfway = await records.feature("udl", "F-1")
    assert halfway is not None
    assert halfway.status == "active"  # (one done, one still open)

    await tasks.move(second, "cancelled", by=Principal(kind="human", name="human:claude"))
    found = await records.feature("udl", "F-1")
    assert found is not None
    assert found.status == "done"


async def test_a_parked_feature_is_left_as_a_person_set_it(
    records: FeatureRecords, tasks: TaskRecords
) -> None:
    await records.save_feature(Feature(project="udl", key="F-1", title="x", status="parked"))
    saved = await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="a"))
    await tasks.move(saved, "queued", by=Principal(kind="human", name="human:claude"))

    found = await records.feature("udl", "F-1")
    assert found is not None
    assert found.status == "parked"


async def test_a_task_of_a_feature_that_is_not_there_is_still_stored(tasks: TaskRecords) -> None:
    # (An imported task may arrive before its feature does: only the project must be there.)
    saved = await tasks.save_task(Task(project="udl", feature="F-9", key="F-9.1", title="a"))

    assert await tasks.task("udl", "F-9.1") == saved


# ---------------------------------------------------------------------------------------------
# Keys from the counters
# ---------------------------------------------------------------------------------------------


async def test_a_feature_takes_the_next_f_number(records: FeatureRecords) -> None:
    first = await records.create_feature("udl", "Work tracking")
    second = await records.create_feature("udl", "Shared data layer")

    assert (first.key, second.key) == ("F-1", "F-2")
    assert (await records.feature("udl", "F-2")) == second


async def test_a_feature_may_be_given_its_key(records: FeatureRecords) -> None:
    imported = await records.create_feature("udl", "The plan's 22", key="22")

    assert imported.key == "22"
    assert (await records.create_feature("udl", "next")).key == "F-1"  # (the counter is untouched)


async def test_a_task_takes_the_next_number_of_its_feature(tasks: TaskRecords) -> None:
    first = await tasks.create_task("udl", "F-1", "The records")
    second = await tasks.create_task("udl", "F-1", "The table")
    other = await tasks.create_task("udl", "F-2", "Elsewhere")

    assert (first.key, second.key, other.key) == ("F-1.1", "F-1.2", "F-2.1")


async def test_a_task_may_be_given_its_key(tasks: TaskRecords) -> None:
    imported = await tasks.create_task("udl", "F-1", "A card", key="26.3")

    assert imported.key == "26.3"


# ---------------------------------------------------------------------------------------------
# The counters keys come from
# ---------------------------------------------------------------------------------------------


async def test_a_counter_hands_out_one_number_at_a_time(counters: Counters) -> None:
    assert await counters.claim("udl", "F-") == 1
    assert await counters.claim("udl", "F-") == 2
    assert await counters.claim("udl", "F-") == 3


async def test_each_prefix_and_project_has_its_own_numbers(counters: Counters) -> None:
    assert await counters.claim("udl", "F-") == 1
    assert await counters.claim("udl", "Q-") == 1

    await ProjectRecords(counters.records).save_project(Project(name="liively", title="Liively"))

    assert await counters.claim("liively", "F-") == 1
    assert await counters.claim("udl", "F-") == 2


async def test_a_counter_of_a_project_that_is_not_there_is_refused(counters: Counters) -> None:
    with pytest.raises(DoesNotExist):
        await counters.claim("liively", "F-")


async def test_a_number_claimed_is_never_handed_out_again(counters: Counters) -> None:
    claimed = [await counters.claim("udl", "F-") for _ in range(5)]

    latest = await counters.latest("udl", "F-")

    assert claimed == [1, 2, 3, 4, 5]
    assert latest is not None
    assert (latest.project, latest.prefix, latest.last) == ("udl", "F-", 5)


async def test_nothing_claimed_means_no_counter(counters: Counters) -> None:
    assert await counters.latest("udl", "F-") is None


async def test_only_the_last_claim_is_kept(counters: Counters) -> None:
    # (A claim is a row, and the older ones go: reading the last number is one row however long
    # the project has been counting.)
    for _ in range(5):
        await counters.claim("udl", "F-")

    kept = await counters.claims("udl", "F-")

    assert [(counter.prefix, counter.last) for counter in kept] == [("F-", 5)]
