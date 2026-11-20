"""
------------------------------
- test_actions.py -
------------------------------

Human actions (28.2): something only a person can do, with a result line — what human-comms.md's
"To action" section is today, as records.

An action is `now`, `after` a task, or `later`; an action waiting `after` a task becomes `now`
when that task merges (28.3 calls `become_now`). A person's task (a `human` one) has an action
from the moment it is assigned to `human:<name>`: finishing that action moves the task to
`merged`, with the result as a note on it.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import databases, registry, settings
from udl.config import Settings
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch.actions import (
    ActionRecords,
    become_now,
    drop,
    finish,
    for_task,
    set_action,
)
from udl.plugins.apps.orch.models import (
    AFTER,
    DONE,
    DROPPED,
    LATER,
    NOW,
    OPEN,
    Principal,
)
from udl.plugins.apps.orch.queue import assign, queue_of
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    HumanAction,
    Note,
    NoteRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)


@pytest.fixture
async def actions(tmp_path: Path) -> ActionRecords:
    """Orch's actions over a fresh in-memory database, with one project and two features."""
    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    records = AppRecords(db)
    await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
    features = FeatureRecords(records)
    await features.save_feature(Feature(project="udl", key="F-1", title="The queue"))
    await features.save_feature(Feature(project="udl", key="F-2", title="The dispatcher"))
    agents = AgentRecords(records)
    for name in ("ernie-c", "deepseek-b"):
        await agents.save_agent(Agent(name=name, title=name, runner="fake"))
    return ActionRecords(records)


@pytest.fixture
async def store(tmp_path: Path) -> tuple[ActionRecords, TaskRecords]:
    """Orch's actions and tasks over a fresh in-memory database, with a project and two tasks."""
    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    records = AppRecords(db)
    await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
    await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="The queue"))
    tasks = TaskRecords(records)
    await tasks.save_task(
        Task(project="udl", feature="F-1", key="F-1.1", title="F-1.1", task_kind="human")
    )
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.2", title="F-1.2"))
    return ActionRecords(records), tasks


async def a_task(tasks: TaskRecords, key: str, *, task_kind: str = "build") -> Task:
    """A task of the project, saved and read back."""
    await tasks.save_task(
        Task(project="udl", feature="F-1", key=key, title="x", task_kind=task_kind)  # pyright: ignore[reportArgumentType]
    )
    found = await tasks.task("udl", key)
    assert found is not None
    return found


# ---------------------------------------------------------------------------------------------
# Setting and listing
# ---------------------------------------------------------------------------------------------


async def test_an_action_gets_a_key_of_its_own_and_what_it_says(actions: ActionRecords) -> None:
    action = await set_action(
        actions,
        project="udl",
        title="Send the token to Liively",
        text="The one from 1Password.",
        assignee="human:claude",
    )

    assert action.key == "A-1"
    assert (action.when, action.state, action.assignee) == (NOW, OPEN, "human:claude")
    assert (await actions.action("udl", "A-1")) == action


async def test_each_action_gets_the_next_key(actions: ActionRecords) -> None:
    first = await set_action(actions, project="udl", title="One")
    second = await set_action(actions, project="udl", title="Two")

    assert (first.key, second.key) == ("A-1", "A-2")


async def test_actions_are_listed_now_then_after_then_later(actions: ActionRecords) -> None:
    await a_task(TaskRecords(actions.records), "F-1.1")
    await set_action(actions, project="udl", title="Later", when=LATER)
    await set_action(actions, project="udl", title="After", when=AFTER, after="F-1.1")
    await set_action(actions, project="udl", title="Now")

    assert [found.title for found in await actions.actions("udl")] == ["Now", "After", "Later"]


async def test_an_action_after_a_task_must_name_one(actions: ActionRecords) -> None:
    with pytest.raises(NotAllowed, match="after which task"):
        await set_action(actions, project="udl", title="After", when=AFTER)


async def test_an_action_that_is_not_after_names_no_task(actions: ActionRecords) -> None:
    with pytest.raises(NotAllowed, match="does not wait for a task"):
        await set_action(actions, project="udl", title="Now", after="F-1.1")


async def test_an_action_is_a_person_s(actions: ActionRecords) -> None:
    with pytest.raises(NotAllowed, match="is a person's"):
        await set_action(actions, project="udl", title="Now", assignee="ernie-c")


# ---------------------------------------------------------------------------------------------
# After, becoming now
# ---------------------------------------------------------------------------------------------


async def test_an_action_waiting_after_a_task_becomes_now_when_it_merges(
    actions: ActionRecords,
) -> None:
    tasks = TaskRecords(actions.records)
    for key in ("F-1.2", "F-1.3"):
        await a_task(tasks, key)
    waiting = await set_action(
        actions, project="udl", title="Promote 26.2", when=AFTER, after="F-1.2"
    )
    other = await set_action(actions, project="udl", title="Later", when=LATER)
    await set_action(actions, project="udl", title="After another", when=AFTER, after="F-1.3")

    became = await become_now(actions, project="udl", task="F-1.2")

    assert [found.key for found in became] == [waiting.key]
    assert (await actions.action("udl", waiting.key)).when == NOW  # type: ignore[union-attr]
    assert (await actions.action("udl", other.key)).when == LATER  # type: ignore[union-attr]
    # (Only the open ones: a done action that waited for the same task is left as it was.)
    assert [found.key for found in await become_now(actions, project="udl", task="F-1.2")] == []


# ---------------------------------------------------------------------------------------------
# Finishing and dropping
# ---------------------------------------------------------------------------------------------


async def test_finishing_an_action_keeps_its_result(actions: ActionRecords) -> None:
    action = await set_action(actions, project="udl", title="Send the token")

    finished = await finish(actions, project="udl", key=action.key, result="Sent it.")

    assert (finished.state, finished.result) == (DONE, "Sent it.")


async def test_a_finished_action_cannot_be_finished_again(actions: ActionRecords) -> None:
    action = await set_action(actions, project="udl", title="Send the token")
    await finish(actions, project="udl", key=action.key, result="Sent it.")

    with pytest.raises(NotAllowed, match="already done"):
        await finish(actions, project="udl", key=action.key, result="Sent it twice.")


async def test_finishing_writes_its_result_as_a_note_on_the_task(
    actions: ActionRecords,
) -> None:
    tasks = TaskRecords(actions.records)
    task = await a_task(tasks, "F-1.1")
    action = await set_action(actions, project="udl", title="Check it", task="F-1.1")

    await finish(
        actions,
        project="udl",
        key=action.key,
        result="Checked: it is fine.",
        by=Principal(kind="human", name="human:claude"),
    )

    [note] = await NoteRecords(actions.records).notes("task", task.id)
    assert note.text == "Checked: it is fine."
    assert (note.note_kind, note.author) == ("progress", "human:claude")


async def test_finishing_a_person_s_task_merges_it(actions: ActionRecords) -> None:
    tasks = TaskRecords(actions.records)
    await a_task(tasks, "F-1.1", task_kind="human")
    await assign(tasks, "F-1.1", "human:claude", project="udl")
    [action] = await actions.actions("udl")

    await finish(actions, project="udl", key=action.key, result="Done by hand.")

    merged = await tasks.task("udl", "F-1.1")
    assert merged is not None
    assert merged.state == "merged"
    assert [found.key for found in await queue_of(tasks, project="udl", agent="human:claude")] == []


async def test_dropping_an_action_keeps_it_with_the_reason(actions: ActionRecords) -> None:
    action = await set_action(actions, project="udl", title="Send the token")

    dropped = await drop(actions, project="udl", key=action.key, reason="Not needed.")

    assert (dropped.state, dropped.result) == (DROPPED, "Not needed.")
    assert [found.key for found in await actions.actions("udl", state=OPEN)] == []


async def test_an_action_that_is_not_there_is_told_so(actions: ActionRecords) -> None:
    with pytest.raises(DoesNotExist, match="A-9"):
        await finish(actions, project="udl", key="A-9", result="Done.")


# ---------------------------------------------------------------------------------------------
# A person's task
# ---------------------------------------------------------------------------------------------


async def test_a_person_s_task_gets_an_action_when_it_is_assigned(
    actions: ActionRecords,
) -> None:
    tasks = TaskRecords(actions.records)
    await a_task(tasks, "F-1.1", task_kind="human")

    await assign(tasks, "F-1.1", "human:claude", project="udl")

    [action] = await actions.actions("udl")
    assert (action.title, action.task, action.assignee) == ("x", "F-1.1", "human:claude")
    assert (action.when, action.state) == (NOW, OPEN)


async def test_a_person_s_task_assigned_again_keeps_its_one_action(
    actions: ActionRecords,
) -> None:
    tasks = TaskRecords(actions.records)
    await a_task(tasks, "F-1.1", task_kind="human")
    await assign(tasks, "F-1.1", "human:claude", project="udl")

    await assign(tasks, "F-1.1", "human:sam", project="udl")

    [action] = await actions.actions("udl")
    assert action.assignee == "human:sam"


async def test_an_agent_s_task_gets_no_action(actions: ActionRecords) -> None:
    tasks = TaskRecords(actions.records)
    await a_task(tasks, "F-1.1")

    await assign(tasks, "F-1.1", "ernie-c", project="udl")

    assert await actions.actions("udl") == []


def test_the_action_record_is_one_of_orch_s_kinds() -> None:
    from udl.plugins.apps.orch.plugin import Orch

    assert HumanAction in Orch.record_kinds()
    assert Note in Orch.record_kinds()


# ---------------------------------------------------------------------------------------------
# What deleting a task, a feature or a project takes with it (the review's first item)
# ---------------------------------------------------------------------------------------------


async def test_deleting_a_task_deletes_the_actions_about_it(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, tasks = store
    about = await set_action(actions, project="udl", title="A", task="F-1.1")
    after = await set_action(actions, project="udl", title="B", when=AFTER, after="F-1.1")
    other = await set_action(actions, project="udl", title="C", task="F-1.2")

    assert await tasks.delete_task("udl", "F-1.1") is True

    left = await actions.actions("udl", state=None)
    assert [action.key for action in left] == [other.key]
    assert about.key not in [action.key for action in left]
    assert after.key not in [action.key for action in left]


async def test_deleting_a_feature_deletes_the_actions_of_its_tasks(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, tasks = store
    await set_action(actions, project="udl", title="A", task="F-1.1")

    assert await FeatureRecords(tasks.records).delete_feature("udl", "F-1") is True

    assert await actions.actions("udl", state=None) == []


async def test_deleting_a_project_deletes_its_actions_and_its_action_numbers_start_over(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, tasks = store
    await set_action(actions, project="udl", title="A")
    await set_action(actions, project="udl", title="B", when=AFTER, after="F-1.1")

    assert await ProjectRecords(tasks.records).delete_project("udl") is True

    assert await actions.actions("udl", state=None) == []
    # (And a project of the same name starts its numbers over, so `A-1` is not a duplicate.)
    await ProjectRecords(tasks.records).save_project(Project(name="udl", title="udl"))
    again = await set_action(actions, project="udl", title="C")
    assert again.key == "A-1"


# ---------------------------------------------------------------------------------------------
# A person's task merging: what waited after it, and a second action about it
# ---------------------------------------------------------------------------------------------


async def test_finishing_a_person_s_task_makes_what_waited_after_it_now(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, _ = store
    mine = await set_action(
        actions, project="udl", title="Merge the checklist", task="F-1.1", assignee="human:claude"
    )
    waited = await set_action(actions, project="udl", title="Tell them", when=AFTER, after="F-1.1")

    await finish(actions, project="udl", key=mine.key, result="Done.")

    found = await actions.action("udl", waited.key)
    assert found is not None
    assert found.when == NOW


async def test_a_second_action_about_a_finished_person_s_task_changes_nothing(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, tasks = store
    mine = await set_action(
        actions, project="udl", title="Merge the checklist", task="F-1.1", assignee="human:claude"
    )
    await finish(actions, project="udl", key=mine.key, result="Done.")
    task = await tasks.task("udl", "F-1.1")
    assert task is not None
    notes = len(await NoteRecords(actions.records).notes("task", task.id))
    second = await set_action(actions, project="udl", title="Again", task="F-1.1")

    with pytest.raises(NotAllowed, match="already merged"):
        await finish(actions, project="udl", key=second.key, result="Done again.")

    # (The refusal came before anything was written: no second note, no half-finished action.)
    assert len(await NoteRecords(actions.records).notes("task", task.id)) == notes
    after = await actions.action("udl", second.key)
    assert after is not None
    assert after.state == OPEN


# ---------------------------------------------------------------------------------------------
# The rest of the review
# ---------------------------------------------------------------------------------------------


async def test_a_dropped_action_is_reopened_when_the_task_goes_to_a_person(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, tasks = store
    task = await tasks.task("udl", "F-1.1")
    assert task is not None
    first = await for_task(actions, task, assignee="human:claude")
    await drop(actions, project="udl", key=first.key, reason="Not now.")

    again = await for_task(actions, task, assignee="human:sam")

    assert (again.key, again.state, again.result) == (first.key, OPEN, None)
    assert again.assignee == "human:sam"


async def test_dropping_an_action_twice_answers_it_unchanged(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    # (The operation is marked idempotent, so a client may retry it.)
    actions, _ = store
    found = await set_action(actions, project="udl", title="A")
    dropped = await drop(actions, project="udl", key=found.key, reason="Not needed.")

    again = await drop(actions, project="udl", key=found.key, reason="Not needed.")

    assert again == dropped


async def test_an_action_about_a_task_that_is_not_there_is_refused(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, _ = store

    with pytest.raises(DoesNotExist, match=r"28\.33"):
        await set_action(actions, project="udl", title="A", task="28.33")
    with pytest.raises(DoesNotExist, match=r"28\.33"):
        await set_action(actions, project="udl", title="A", when=AFTER, after="28.33")


async def test_an_assignee_of_human_alone_says_it_has_no_name(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, _ = store

    with pytest.raises(NotAllowed, match="name"):
        await set_action(actions, project="udl", title="A", assignee="human:")


async def test_unassigning_a_person_s_task_takes_the_action_off_them(
    store: tuple[ActionRecords, TaskRecords],
) -> None:
    actions, tasks = store
    await assign(tasks, "F-1.1", "human:claude", project="udl")
    mine = (await actions.actions("udl", state=OPEN, task="F-1.1"))[0]

    await queues.unassign(tasks, project="udl", key="F-1.1")

    after = await actions.action("udl", mine.key)
    assert after is not None
    assert after.assignee is None
    assert after.state == OPEN
