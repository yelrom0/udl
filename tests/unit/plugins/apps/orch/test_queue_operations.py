"""
--------------------------------------
- test_queue_operations.py -
--------------------------------------

The queue's operations (27.3): `orch_assign`, `orch_reorder_queue`, `orch_unassign`,
`orch_get_queue` and `orch_my_task`, each one method of the `orch` app, offered on the CLI, the
API and MCP by udl's generated adapters (tests/interface/test_parity.py lists them).

A project is optional while udl has one, as an account is (11.4); with two, it must be named.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    AssignTaskRequest,
    MyTaskResponse,
    NoteKind,
    OwnerKind,
    TaskQueue,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    Note,
    NoteRecords,
    Project,
    ProjectRecords,
    TaskRecords,
)
from udl.plugins.apps.orch.records import Task as TaskRecord
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.registry import APPS, PluginRegistry


def orch_registry() -> PluginRegistry:
    """The memory database and the secret store, and the orch app (which the tests are of)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    return plugins


# ---------------------------------------------------------------------------------------------
# What the app offers
# ---------------------------------------------------------------------------------------------


QUEUE_OPERATIONS: list[str] = [
    "issue_token",
    "assign",
    "reorder_queue",
    "unassign",
    "get_queue",
    "my_task",
    "suggest",
]
"""What comes before the harnesses' (25.11): the token's, the queue's and what to do next."""


def test_orch_offers_the_queue_s_operations() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert list(offered)[: len(QUEUE_OPERATIONS)] == QUEUE_OPERATIONS
    # (Reading a queue changes nothing; the ones that move a task are idempotent, and a task
    # that leaves one is not destructive: it is open again.)
    assert offered["get_queue"].hints == OperationHints(read_only=True)
    assert offered["my_task"].hints == OperationHints(read_only=True)
    assert offered["assign"].hints == OperationHints(idempotent=True)
    assert offered["reorder_queue"].hints == OperationHints(idempotent=True)
    assert offered["unassign"].hints == OperationHints()
    # (Every operation's request and answer are its own models, so the API and MCP have schemas.)
    assert offered["assign"].request is AssignTaskRequest
    assert offered["assign"].response is TaskQueue
    assert offered["my_task"].response is MyTaskResponse


async def test_the_operations_are_offered_as_orch_s(tmp_path: Path) -> None:
    async with UDLService(settings(tmp_path), registry=orch_registry()) as service:
        names = [op.name for op in service.operations.in_namespace("orch")]

    assert names[: len(QUEUE_OPERATIONS)] == [f"orch_{name}" for name in QUEUE_OPERATIONS]


# ---------------------------------------------------------------------------------------------
# The operations, through the service (as the CLI, the API and MCP call them)
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service over a fresh in-memory database, with a project, a feature and three tasks."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="Q"))
        for key in ("F-1.1", "F-1.2", "F-1.3"):
            await TaskRecords(records).save_task(
                TaskRecord(project="udl", feature="F-1", key=key, title=key)
            )
        agents = AgentRecords(records)
        for name in ("ernie-c", "deepseek-b"):
            await agents.save_agent(Agent(name=name, title=name, runner="fake"))
        yield running


async def a_queue(service: UDLService, name: str, **request: Any) -> TaskQueue:
    """The answer of an operation that answers a queue, as any interface's caller gets it."""
    found = await service.call(name, request)

    assert isinstance(found, TaskQueue)
    return found


async def assign(service: UDLService, key: str, agent: str, **fields: Any) -> TaskQueue:
    """`orch_assign`, as a caller of any interface calls it."""
    return await a_queue(service, "orch_assign", key=key, agent=agent, project="udl", **fields)


async def test_assigning_through_the_operation_answers_the_queue(service: UDLService) -> None:
    await assign(service, "F-1.1", "ernie-c")
    queue = await assign(service, "F-1.2", "ernie-c", position=0)

    assert queue.agent == "ernie-c"
    assert [(entry.key, entry.position, entry.state) for entry in queue.tasks] == [
        ("F-1.2", 0, "queued"),
        ("F-1.1", 1, "queued"),
    ]


async def test_a_reassign_through_the_operation_moves_the_task(service: UDLService) -> None:
    # (The board's drag from one agent to another, 29.4: it is one call, and it must not strand
    # the task between the two queues.)
    await assign(service, "F-1.1", "ernie-c")
    await assign(service, "F-1.2", "ernie-c")

    moved = await assign(service, "F-1.1", "deepseek-b")

    assert [(entry.key, entry.assignee) for entry in moved.tasks] == [("F-1.1", "deepseek-b")]
    left = await a_queue(service, "orch_get_queue", agent="ernie-c", project="udl")
    assert [entry.key for entry in left.tasks] == ["F-1.2"]
    assert [entry.position for entry in left.tasks] == [0]


async def test_an_agent_that_is_not_one_is_refused_through_the_operation(
    service: UDLService,
) -> None:
    with pytest.raises(NotAllowed, match="erine-c"):
        await service.call("orch_assign", {"key": "F-1.1", "agent": "erine-c", "project": "udl"})


async def test_the_queue_is_read_by_its_operation(service: UDLService) -> None:
    await assign(service, "F-1.1", "ernie-c")

    queue = await a_queue(service, "orch_get_queue", agent="ernie-c", project="udl")

    assert [entry.key for entry in queue.tasks] == ["F-1.1"]


async def test_a_reorder_through_the_operation_answers_the_new_order(service: UDLService) -> None:
    for key in ("F-1.1", "F-1.2", "F-1.3"):
        await assign(service, key, "ernie-c")

    reordered = await a_queue(
        service,
        "orch_reorder_queue",
        agent="ernie-c",
        keys=["F-1.3", "F-1.2", "F-1.1"],
        project="udl",
    )

    assert [entry.key for entry in reordered.tasks] == ["F-1.3", "F-1.2", "F-1.1"]
    assert [entry.position for entry in reordered.tasks] == [0, 1, 2]


async def test_unassigning_through_the_operation_answers_the_queue_it_left(
    service: UDLService,
) -> None:
    for key in ("F-1.1", "F-1.2"):
        await assign(service, key, "ernie-c")

    queue = await a_queue(service, "orch_unassign", key="F-1.1", project="udl")

    assert (queue.agent, [entry.key for entry in queue.tasks]) == ("ernie-c", ["F-1.2"])
    assert [entry.position for entry in queue.tasks] == [0]


async def test_my_task_is_the_one_the_agent_is_on(service: UDLService) -> None:
    await assign(service, "F-1.1", "ernie-c")
    await assign(service, "F-1.2", "ernie-c")

    first = await service.call("orch_my_task", {"agent": "ernie-c", "project": "udl"})
    assert isinstance(first, MyTaskResponse)
    assert first.task is not None
    assert first.task.key == "F-1.1"

    nothing = await service.call("orch_my_task", {"agent": "deepseek-b", "project": "udl"})
    assert isinstance(nothing, MyTaskResponse)
    assert nothing.task is None


async def test_my_task_brings_what_the_agent_is_told_before_it_starts(
    service: UDLService,
) -> None:
    # (The dispatcher hands a session the task with what it is for: the feature's spec, the
    # task's spec, the project's rules and the task's notes, oldest first.)
    records = AppRecords(service.db)
    projects, features, tasks = (
        ProjectRecords(records),
        FeatureRecords(records),
        TaskRecords(records),
    )
    project = await projects.require("udl")
    await projects.save_project(
        project.model_copy(update={"rules": "Test first. Never push ungreen."})
    )
    feature = await features.feature("udl", "F-1")
    assert feature is not None
    await features.save_feature(feature.model_copy(update={"spec": "The queue's rules."}))
    task = await tasks.task("udl", "F-1.1")
    assert task is not None
    await tasks.save_task(task.model_copy(update={"spec": "Do the thing."}))
    await NoteRecords(records).add(
        Note(
            owner_kind=OwnerKind.TASK,
            owner=task.id,
            note_kind=NoteKind.DECISION,
            text="It is a table, not a list.",
            author="human:claude",
        )
    )
    await assign(service, "F-1.1", "ernie-c")

    found = await service.call("orch_my_task", {"agent": "ernie-c", "project": "udl"})

    assert isinstance(found, MyTaskResponse)
    assert found.feature_spec == "The queue's rules."
    assert found.spec == "Do the thing."
    assert found.rules == "Test first. Never push ungreen."
    assert [(note.note_kind, note.text, note.author) for note in found.notes] == [
        ("decision", "It is a table, not a list.", "human:claude")
    ]


# ---------------------------------------------------------------------------------------------
# A project: the only one, or named
# ---------------------------------------------------------------------------------------------


async def test_the_only_project_need_not_be_named(service: UDLService) -> None:
    queue = await a_queue(service, "orch_assign", key="F-1.1", agent="ernie-c")

    assert queue.project == "udl"


async def test_with_two_projects_the_one_to_work_in_is_named(service: UDLService) -> None:
    await ProjectRecords(AppRecords(service.db)).save_project(Project(name="liively", title="L"))

    with pytest.raises(NotAllowed, match="Which project"):
        await service.call("orch_assign", {"key": "F-1.1", "agent": "ernie-c"})

    queue = await a_queue(service, "orch_get_queue", agent="ernie-c", project="liively")
    assert queue.tasks == []


async def test_a_queue_of_a_task_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist):
        await service.call("orch_assign", {"key": "F-1.9", "agent": "ernie-c", "project": "udl"})


async def test_the_app_an_operation_runs_on_has_its_records(tmp_path: Path) -> None:
    # (The supervisor makes the app with its records, so an operation reads and writes them; an
    # app made without them says so, rather than failing somewhere deeper.)
    async with UDLService(
        settings(tmp_path), registry=orch_registry(), clock=FakeClock()
    ) as service:
        app = await service.apps.get("orch")

        assert isinstance(app, Orch)
        assert isinstance(app.records, AppRecords)

    with pytest.raises(RuntimeError, match="no records"):
        _ = Orch(OrchSettings(), clock=FakeClock()).records
