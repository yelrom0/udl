"""
-------------------------------------
- test_tasks_operations.py -
-------------------------------------

The catalogue's tasks (26.6): `orch_list_tasks` (by project, feature, state, assignee and text),
`orch_get_task` (with its feature, its links, its notes and its questions), `orch_set_task`
(create or edit), `orch_move_task` and `orch_split_task`, on the CLI, the API and MCP through
udl's generated adapters.

A task's state is never set by a save: it moves through `orch.transitions.move()`, which is what
`orch_move_task` runs (and which writes the note that says so).
"""

# System Imports
from typing import Any

# Package Imports
import pytest

# Local Imports
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed
from udl.interfaces.security import Principal as Caller
from udl.interfaces.security import PrincipalKind, current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    GetTaskRequest,
    ListTasksRequest,
    MoveTaskRequest,
    NoteInfo,
    OwnerKind,
    SetTaskRequest,
    Split,
    SplitTaskRequest,
    Task,
    TaskInfo,
    Tasks,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import Feature, FeatureRecords, Note, NoteRecords, TaskRecords

TASK_OPERATIONS: list[str] = [
    "list_tasks",
    "get_task",
    "set_task",
    "move_task",
    "split_task",
]


def test_orch_offers_the_tasks_operations() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert [name for name in offered if name in TASK_OPERATIONS] == TASK_OPERATIONS
    assert offered["list_tasks"].hints == OperationHints(read_only=True)
    assert offered["get_task"].hints == OperationHints(read_only=True)
    assert offered["set_task"].hints == OperationHints(idempotent=True)
    assert offered["move_task"].hints == OperationHints()
    assert offered["split_task"].hints == OperationHints()
    assert offered["list_tasks"].request is ListTasksRequest
    assert offered["list_tasks"].response is Tasks
    assert offered["get_task"].request is GetTaskRequest
    assert offered["get_task"].response is TaskInfo
    assert offered["set_task"].request is SetTaskRequest
    assert offered["set_task"].response is Task
    assert offered["move_task"].request is MoveTaskRequest
    assert offered["move_task"].response is Task
    assert offered["split_task"].request is SplitTaskRequest
    assert offered["split_task"].response is Tasks


# ---------------------------------------------------------------------------------------------
# Reading them
# ---------------------------------------------------------------------------------------------


async def test_the_tasks_are_listed_by_key(service: UDLService) -> None:
    found = await service.call("orch_list_tasks", {})

    assert isinstance(found, Tasks)
    assert found.project == "udl"
    assert [task.key for task in found.tasks] == ["26.1", "26.2"]


async def test_the_tasks_are_narrowed_by_feature(service: UDLService) -> None:
    await _a_task(service, key="27.1", feature="27", title="Elsewhere")

    found = await service.call("orch_list_tasks", {"feature": "27"})

    assert isinstance(found, Tasks)
    assert [task.key for task in found.tasks] == ["27.1"]


async def test_the_tasks_are_narrowed_by_state(service: UDLService) -> None:
    await service.call("orch_move_task", {"key": "26.1", "to": "cancelled"})

    found = await service.call("orch_list_tasks", {"state": "cancelled"})

    assert isinstance(found, Tasks)
    assert [task.key for task in found.tasks] == ["26.1"]


async def test_the_tasks_are_narrowed_by_assignee(service: UDLService) -> None:
    await service.call("orch_assign", {"key": "26.1", "agent": "deepseek-a"})

    found = await service.call("orch_list_tasks", {"assignee": "deepseek-a"})

    assert isinstance(found, Tasks)
    assert [task.key for task in found.tasks] == ["26.1"]


async def test_the_tasks_are_narrowed_by_their_text(service: UDLService) -> None:
    # (The text is read in the title and in the spec, whatever its case.)
    found = await service.call("orch_list_tasks", {"text": "repos"})

    assert isinstance(found, Tasks)
    assert [task.key for task in found.tasks] == ["26.1"]


async def test_a_task_is_read_with_its_feature_links_notes_and_questions(
    service: UDLService,
) -> None:
    records = AppRecords(service.db)
    stored = await TaskRecords(records).task("udl", "26.1")
    assert stored is not None
    notes = NoteRecords(records)
    await notes.add(
        Note(owner_kind=OwnerKind.TASK, owner=stored.id, text="First.", author="deepseek-a")
    )
    await notes.add(
        Note(owner_kind=OwnerKind.TASK, owner=stored.id, text="Second.", author="deepseek-a")
    )

    found = await service.call("orch_get_task", {"key": "26.1"})

    assert isinstance(found, TaskInfo)
    assert found.task.title == "Projects and repos"
    assert found.feature is not None
    assert found.feature.key == "26"
    assert found.links == []
    assert [note.text for note in found.notes] == ["First.", "Second."]
    assert isinstance(found.notes[0], NoteInfo)
    # (A task's questions are 28.1's record: none yet, so the list is empty, never missing.)
    assert found.questions == []


async def test_a_task_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"26\.9"):
        await service.call("orch_get_task", {"key": "26.9"})


# ---------------------------------------------------------------------------------------------
# Setting them
# ---------------------------------------------------------------------------------------------


async def _a_task(service: UDLService, **request: Any) -> Task:
    """The answer of `orch_set_task`, as any interface's caller gets it (making the feature it
    names, so a test can have a task of one)."""
    records = AppRecords(service.db)
    feature = request.get("feature")
    if isinstance(feature, str) and await FeatureRecords(records).feature("udl", feature) is None:
        await FeatureRecords(records).save_feature(
            Feature(project="udl", key=feature, title=feature)
        )
    found = await service.call("orch_set_task", request)

    assert isinstance(found, Task)
    return found


async def test_a_task_is_created(service: UDLService) -> None:
    made = await _a_task(
        service,
        key="26.3",
        feature="26",
        title="Links and notes",
        spec="The plan's 26.3 card.",
        needs=["26.2"],
        repo="udl",
        task_kind="build",
    )

    assert (made.key, made.state, made.needs) == ("26.3", "open", ["26.2"])
    read = await service.call("orch_get_task", {"key": "26.3"})
    assert isinstance(read, TaskInfo)
    assert read.task.repo == "udl"


async def test_a_task_set_without_a_key_is_the_feature_s_next_one(service: UDLService) -> None:
    # (26.1 and 26.2 are taken, so the counter's next free number is 26.3.)
    made = await _a_task(service, feature="26", title="Another piece")

    assert made.key == "26.3"


async def test_setting_a_task_again_edits_what_is_given(service: UDLService) -> None:
    edited = await _a_task(
        service, key="26.2", title="Features, tasks and their links", hold="after 26.1"
    )

    assert edited.title == "Features, tasks and their links"
    assert edited.hold == "after 26.1"
    assert edited.spec == "The plan's 26.2 card."
    assert edited.state == "open"


async def test_a_save_does_not_set_a_task_s_state(service: UDLService) -> None:
    # (The state is the table's: a save keeps the one the task has, whatever else it changes.)
    await service.call("orch_move_task", {"key": "26.1", "to": "cancelled"})

    edited = await _a_task(service, key="26.1", title="Projects, repos and their branches")

    assert edited.state == "cancelled"
    assert "state" not in SetTaskRequest.model_fields


async def test_a_new_task_needs_a_title(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="title"):
        await _a_task(service, key="26.3", feature="26")


async def test_a_task_of_a_feature_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"22"):
        await service.call("orch_set_task", {"key": "22.1", "feature": "22", "title": "One"})


async def test_a_new_task_s_key_must_be_of_the_feature_it_names(service: UDLService) -> None:
    # (26.1 is a task of 26: a key of 27's given with 26's feature is a mistake, not a task.)
    with pytest.raises(NotAllowed, match=r"27\.1"):
        await _a_task(service, key="27.1", feature="26", title="Something else")


async def test_a_task_does_not_change_its_feature(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match=r"not of '22'"):
        await _a_task(service, key="26.2", feature="22", title="Features and tasks")


# ---------------------------------------------------------------------------------------------
# Moving them
# ---------------------------------------------------------------------------------------------


async def test_a_task_moves(service: UDLService) -> None:
    moved = await service.call("orch_move_task", {"key": "26.1", "to": "cancelled"})

    assert isinstance(moved, Task)
    assert moved.state == "cancelled"


async def test_a_move_writes_the_note_that_says_so(service: UDLService) -> None:
    await service.call("orch_move_task", {"key": "26.1", "to": "cancelled"})

    found = await service.call("orch_get_task", {"key": "26.1"})

    assert isinstance(found, TaskInfo)
    # (A system note is udl's own voice, and it says who moved the task.)
    assert [(note.note_kind, note.text, note.author) for note in found.notes] == [
        ("system", "open → cancelled, by human:owner.", "udl")
    ]


async def test_the_cli_s_user_may_move_a_task_to_done(service: UDLService) -> None:
    # (The person at the keyboard is the owner, as over the API: only an agent is held to the
    # table's own-task rule and kept from `done` (27.2, 26.3).)
    for state in ("ready", "merged", "done"):
        moved = await service.call("orch_move_task", {"key": "26.1", "to": state})
        assert isinstance(moved, Task)
        assert moved.state == state


async def test_an_agent_does_not_move_a_task_itself(service: UDLService) -> None:
    # (An implementer moves its work with `orch_start`, `orch_ready` and the rest: 27.2's table
    # does not let it or a manager call `orch_move_task` at all, so `_by`'s agent branch is the
    # note's — and a reviewer, which the table does let call it, keeps its role: the test above.)
    caller = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name="deepseek-a", role="implementer")
    )
    try:
        with pytest.raises(NotAllowed, match="not one of the operations"):
            await service.call("orch_move_task", {"key": "26.1", "to": "cancelled"})
    finally:
        current_principal.reset(caller)


async def test_a_reviewer_agent_moves_another_agent_s_task_to_done(
    service: UDLService,
) -> None:
    # (A reviewer reviews anyone's work: `_by` keeps its role, or `transitions` would take it for
    # an implementer and refuse both the other agent's task and `done`. The note says so.)
    records = AppRecords(service.db)
    stored = await TaskRecords(records).task("udl", "26.2")
    assert stored is not None
    await TaskRecords(records).save_all(
        [stored.model_copy(update={"state": "merged", "assignee": "deepseek-b"})]
    )
    caller = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name="claude-r", role="reviewer")
    )
    try:
        moved = await service.call("orch_move_task", {"key": "26.2", "to": "done"})
        found = await service.call("orch_get_task", {"key": "26.2"})
    finally:
        current_principal.reset(caller)

    assert isinstance(moved, Task)
    assert moved.state == "done"
    assert isinstance(found, TaskInfo)
    assert found.notes[-1].text == "merged → done, by reviewer."


async def test_a_move_s_note_is_bounded() -> None:
    with pytest.raises(ValueError, match="2000"):
        MoveTaskRequest.model_validate({"key": "26.1", "to": "cancelled", "note": "x" * 2001})


async def test_a_blocked_task_keeps_what_it_waits_on(service: UDLService) -> None:
    moved = await service.call(
        "orch_move_task", {"key": "26.2", "to": "blocked", "note": "Q-7: memory is read-only"}
    )

    assert isinstance(moved, Task)
    assert moved.blocked_on == "Q-7: memory is read-only"


async def test_moving_a_task_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"26\.9"):
        await service.call("orch_move_task", {"key": "26.9", "to": "cancelled"})


# ---------------------------------------------------------------------------------------------
# Splitting them
# ---------------------------------------------------------------------------------------------


async def test_a_task_splits_into_its_pieces(service: UDLService) -> None:
    records = AppRecords(service.db)
    stored = await TaskRecords(records).task("udl", "26.1")
    assert stored is not None
    await NoteRecords(records).add(
        Note(owner_kind=OwnerKind.TASK, owner=stored.id, text="Its history.", author="deepseek-a")
    )

    found = await service.call(
        "orch_split_task",
        {
            "key": "26.1",
            "into": [
                {"title": "Projects", "spec": "The project itself."},
                {"title": "Repos", "spec": "A project's repositories."},
                {"title": "Their branches", "spec": "How a branch is named."},
            ],
        },
    )

    assert isinstance(found, Tasks)
    # (The first keeps its key and its history: its PR and its notes are not thrown away.)
    assert [task.key for task in found.tasks] == ["26.1", "26.3", "26.4"]
    assert [task.title for task in found.tasks] == [
        "Projects",
        "Repos",
        "Their branches",
    ]
    read = await service.call("orch_get_task", {"key": "26.1"})
    assert isinstance(read, TaskInfo)
    assert [note.text for note in read.notes] == [
        "Its history.",
        "Split into 26.1, 26.3 and 26.4.",
    ]


async def test_a_split_of_a_task_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"26\.9"):
        await service.call("orch_split_task", {"key": "26.9", "into": [{"title": "One"}]})


async def test_splitting_a_task_that_has_started_is_refused(service: UDLService) -> None:
    # (Its spec is being written: the split would rewrite it under the agent doing it.)
    records = AppRecords(service.db)
    stored = await TaskRecords(records).task("udl", "26.1")
    assert stored is not None
    await TaskRecords(records).save_all(
        [stored.model_copy(update={"state": "in_progress", "assignee": "deepseek-a"})]
    )

    with pytest.raises(NotAllowed, match="in_progress"):
        await service.call("orch_split_task", {"key": "26.1", "into": [{"title": "One"}]})


async def test_splitting_a_finished_task_is_refused(service: UDLService) -> None:
    # (A finished task's title and spec are what was done: they are not rewritten.)
    records = AppRecords(service.db)
    stored = await TaskRecords(records).task("udl", "26.2")
    assert stored is not None
    await TaskRecords(records).save_all([stored.model_copy(update={"state": "done"})])

    with pytest.raises(NotAllowed, match="done"):
        await service.call("orch_split_task", {"key": "26.2", "into": [{"title": "One"}]})


def test_a_split_needs_at_least_one_piece() -> None:
    with pytest.raises(ValueError, match="into"):
        SplitTaskRequest.model_validate({"key": "26.1", "into": []})
    assert Split.model_fields["title"].is_required()
