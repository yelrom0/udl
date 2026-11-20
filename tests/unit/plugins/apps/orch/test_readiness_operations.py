"""
----------------------------------------
- test_readiness_operations.py -
----------------------------------------

`orch_readiness` and `orch_ready` (27.6), through the service as the CLI, the API and MCP call
them: whether a task's pull may be merged, and marking it ready (which moves the task, writes the
note and publishes `orch.task.ready`).

The pull is read through udl's own `github_get_pull`, so these tests register a fake `github` app
that answers it (the real integration's own tests are 25.5's).
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.engine import registry, settings
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.models import (
    IN_PROGRESS,
    OwnerKind,
    Principal,
    Readiness,
    ReadinessRequest,
    ReadyRequest,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Feature,
    FeatureRecords,
    LinkRecords,
    NoteRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)
from udl.plugins.registry import APPS, PluginRegistry

PULL: str = "https://github.com/yelrom0/udl/pull/7"
"""The pull the fake answers for."""
HEAD: str = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""The commit at its head."""


class FakePull(BaseModel):
    """A `github_get_pull` answer, as far as readiness reads it."""

    repo: str = "yelrom0/udl"
    number: int = 7
    title: str = "It"
    state: str = "open"
    draft: bool = False
    merged: bool = False
    mergeable: bool | None = True
    mergeable_state: str = "clean"
    head: str = "feat/it"
    head_sha: str = HEAD
    head_committed_at: datetime | None = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    base: str = "main"
    author: str = "deepseek-b"
    html_url: str = PULL
    checks: list[dict[str, Any]] = [
        {"name": "check", "status": "completed", "conclusion": "success"}
    ]
    comments: list[dict[str, Any]] = [
        {
            "author": "claude[bot]",
            "body": "## Follow-up review: it (#7), after dc1fd19\n\n**Verdict: all clear**\n",
            "created_at": "2026-11-20T10:00:00Z",
        }
    ]


class GetPullRequest(BaseModel):
    """`github_get_pull`'s request, as the fake reads it."""

    repo: str
    number: int


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull (what readiness calls)."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.pull = FakePull()
        self.asked: list[tuple[str, int]] = []

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation(
                "get_pull", title="One pull", hints=OperationHints(read_only=True, open_world=True)
            )
        ]

    async def get_pull(self, request: GetPullRequest) -> FakePull:
        """The pull the test set (whatever number it asks for)."""
        self.asked.append((request.repo, request.number))
        return self.pull


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github and the orch app."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


# ---------------------------------------------------------------------------------------------
# What the app offers
# ---------------------------------------------------------------------------------------------


def test_readiness_is_read_only_and_ready_changes_things() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert offered["readiness"].hints == OperationHints(read_only=True, open_world=True)
    assert offered["readiness"].request is ReadinessRequest
    assert offered["readiness"].response is Readiness
    assert offered["ready"].hints == OperationHints(open_world=True)
    assert offered["ready"].request is ReadyRequest
    assert offered["ready"].response is Readiness


# ---------------------------------------------------------------------------------------------
# The operations, through the service
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project, its feature, one task with its pull link, and the fake github."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]")
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        tasks = TaskRecords(records)
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="It"))
        started = await tasks.task("udl", "F-1.1")
        assert started is not None
        await tasks.move(started, IN_PROGRESS, by=Principal(kind="system", role="start"))
        await LinkRecords(records).add(
            project="udl", owner_kind=OwnerKind.TASK, owner="F-1.1", kind="pull", url=PULL
        )
        yield running


async def readiness_of(service: UDLService, **request: Any) -> Readiness:
    """`orch_readiness`, as a caller of any interface calls it."""
    found = await service.call("orch_readiness", request)

    assert isinstance(found, Readiness)
    return found


async def test_a_clean_pull_for_this_head_is_ready(service: UDLService) -> None:
    found = await readiness_of(service, key="F-1.1")

    assert found.ready
    assert found.blockers == []
    assert found.open_items == []


async def test_a_task_without_a_pull_is_not_ready(service: UDLService) -> None:
    await TaskRecords(AppRecords(service.db)).save_task(
        Task(project="udl", feature="F-1", key="F-1.2", title="No pull yet")
    )

    found = await readiness_of(service, key="F-1.2")

    assert not found.ready
    assert found.blockers == ["it has no pull"]


async def test_a_draft_pull_is_not_ready(service: UDLService) -> None:
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull = FakePull(draft=True)

    found = await readiness_of(service, key="F-1.1")

    assert found.blockers == ["it is a draft"]


async def test_a_task_that_waits_on_an_unmerged_one_is_not_ready(service: UDLService) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    await tasks.save_task(
        Task(project="udl", feature="F-1", key="F-1.2", title="Later", needs=["F-1.1"])
    )
    await LinkRecords(AppRecords(service.db)).add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="F-1.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/8",
    )

    found = await readiness_of(service, key="F-1.2")

    assert not found.ready
    assert found.blockers == ["it waits on F-1.1, which is in_progress"]


async def test_ready_moves_the_task_writes_the_note_and_publishes_the_event(
    service: UDLService,
) -> None:
    found = await service.call(
        "orch_ready", {"key": "F-1.1", "note": "Green, and the bot is clear."}
    )

    assert isinstance(found, Readiness)
    assert found.ready
    records = AppRecords(service.db)
    task = await TaskRecords(records).task("udl", "F-1.1")
    assert task is not None
    assert task.state == "ready"
    (note,) = await NoteRecords(records).notes(OwnerKind.TASK, task.id)
    assert note.text == "Green, and the bot is clear."
    assert note.note_kind == "progress"


async def test_ready_writes_a_note_of_its_own_when_none_is_given(service: UDLService) -> None:
    await service.call("orch_ready", {"key": "F-1.1"})

    records = AppRecords(service.db)
    task = await TaskRecords(records).task("udl", "F-1.1")
    assert task is not None
    (note,) = await NoteRecords(records).notes(OwnerKind.TASK, task.id)
    assert note.text == "Ready to merge: dc1fd19."


async def test_ready_refuses_with_what_stops_it_and_writes_nothing(service: UDLService) -> None:
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull = FakePull(draft=True)

    with pytest.raises(NotAllowed, match="is not ready: it is a draft"):
        await service.call("orch_ready", {"key": "F-1.1"})

    records = AppRecords(service.db)
    task = await TaskRecords(records).task("udl", "F-1.1")
    assert task is not None
    assert task.state == "in_progress"
    assert await NoteRecords(records).notes(OwnerKind.TASK, task.id) == []


async def test_ready_refuses_a_task_that_has_no_pull(service: UDLService) -> None:
    await TaskRecords(AppRecords(service.db)).save_task(
        Task(project="udl", feature="F-1", key="F-1.2", title="No pull yet")
    )

    with pytest.raises(NotAllowed, match="cannot be ready: it has no pull"):
        await service.call("orch_ready", {"key": "F-1.2"})


async def test_a_held_task_is_not_ready(service: UDLService) -> None:
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    task = await tasks.task("udl", "F-1.1")
    assert task is not None
    await tasks.save_task(task.model_copy(update={"hold": "merge after 27.1"}))

    found = await readiness_of(service, key="F-1.1")

    assert not found.ready
    assert found.held
    assert found.blockers == ["it is on hold: merge after 27.1"]


async def test_a_task_that_is_not_in_progress_cannot_be_made_ready(service: UDLService) -> None:
    """(27.6's review: a person may move a task anywhere, so udl acting as one would have moved
    an `open` task to `ready` and cleared a `blocked` one's reason. It acts as the system, in the
    role `ready`, which the table allows from `in_progress` only.)"""
    tasks = TaskRecords(AppRecords(service.db))
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.2", title="Not started"))
    await LinkRecords(AppRecords(service.db)).add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="F-1.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/8",
    )

    with pytest.raises(NotAllowed, match="cannot move from 'open' to 'ready'"):
        await service.call("orch_ready", {"key": "F-1.2"})

    still = await tasks.task("udl", "F-1.2")
    assert still is not None
    assert still.state == "open"


async def test_a_task_that_is_not_there_is_said(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"There is no task called 'F-9\.9'"):
        await readiness_of(service, key="F-9.9")


def test_the_event_type_is_declared() -> None:
    names = [event_type.name for event_type in Orch.event_types()]

    assert "orch.task.ready" in names
