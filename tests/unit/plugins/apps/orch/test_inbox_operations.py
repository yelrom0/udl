"""
------------------------------
- test_inbox_operations.py -
------------------------------

28.4's inbox over the app: the sections it gathers are the app's own (28.3's merge list with a
pull linked, 28.1's open questions, 28.2's actions, 27.1's agents), and the order each keeps.
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict

# Local Imports
from support.engine import registry, settings
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.models import (
    READY,
    AgentRole,
    Inbox,
    InboxRequest,
    OwnerKind,
    Principal,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    LinkRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)
from udl.plugins.registry import APPS, PluginRegistry


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FakePull(_Model):
    """A `github_get_pull` answer, as readiness and the merge list read it (one pull that is
    ready: open, clean, its checks passed and the bot's answer clear)."""

    repo: str = "yelrom0/udl"
    number: int = 1
    title: str = "It"
    state: str = "open"
    draft: bool = False
    merged: bool = False
    mergeable: bool | None = True
    mergeable_state: str = "clean"
    head: str = "feat/it"
    head_sha: str = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    head_committed_at: datetime | None = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
    base: str = "main"
    author: str = "deepseek-a"
    html_url: str = "https://github.com/yelrom0/udl/pull/1"
    checks: list[dict[str, Any]] = [
        {
            "name": "check",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-11-20T09:30:00Z",
        }
    ]
    comments: list[dict[str, Any]] = [
        {
            "author": "claude[bot]",
            "body": ("## Follow-up review: it (#1), after dc1fd19\n\n**Verdict: all clear**\n"),
            "created_at": "2026-11-20T10:00:00Z",
        }
    ]


class GetPullRequest(_Model):
    """`github_get_pull`'s request, as the fake reads it."""

    repo: str
    number: int


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull (the real one's tests are 25.5's)."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation(
                "get_pull", title="One pull", hints=OperationHints(read_only=True, open_world=True)
            )
        ]

    async def get_pull(self, request: GetPullRequest) -> FakePull:
        """The pull (one clean one, for whatever number is asked)."""
        return FakePull(number=request.number)


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github and the orch app."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project, a feature, a ready task with its pull, a blocking question, an
    action and an agent working on the task: what the inbox gathers."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]", promotion=["rc", "main"])
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        tasks = TaskRecords(records)
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="One"))
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.2", title="Two"))
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.move(found, "in_progress", by=Principal(kind="system", role="start"))
        await LinkRecords(records).add(
            project="udl",
            owner_kind=OwnerKind.TASK,
            owner="F-1.1",
            kind="pull",
            url="https://github.com/yelrom0/udl/pull/1",
        )
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.move(found, READY, by=Principal(kind="system", role="ready"))
        # (A blocking question moves its task to `blocked`, so it is asked in the task without a
        # pull: the ready one stays ready for the merge list.)
        await running.call(
            "orch_ask", {"task": "F-1.2", "title": "Which theme?", "blocking": True}
        )
        await running.call("orch_ask", {"task": "F-1.1", "title": "A later ask"})
        await running.call("orch_set_action", {"title": "Merge it", "when": "now"})
        await AgentRecords(records).save_agent(
            Agent(
                name="deepseek-a",
                title="deepseek-a",
                runner="github",
                role=AgentRole.IMPLEMENTER,
            )
        )
        yield running


async def test_the_inbox_has_the_app_s_own_three_lists(service: UDLService) -> None:
    found = await service.call("orch_inbox", {})

    assert isinstance(found, Inbox)
    assert [(row.task, row.url) for row in found.merge.ready] == [
        ("F-1.1", "https://github.com/yelrom0/udl/pull/1")
    ]
    assert [(one.key, one.blocking) for one in found.questions] == [
        ("Q-1", True),
        ("Q-2", False),
    ], "the blocking one first, then the newest"
    assert [(one.key, one.when.value) for one in found.actions] == [("A-1", "now")]
    assert [line.name for line in found.agents] == ["deepseek-a"]


async def test_a_question_s_newest_comes_first_among_the_blocking_ones(
    service: UDLService,
) -> None:
    await service.call("orch_ask", {"task": "F-1.1", "title": "A second ask"})

    found = await service.call("orch_inbox", {})

    assert isinstance(found, Inbox)
    assert [one.title for one in found.questions] == [
        "Which theme?",
        "A second ask",
        "A later ask",
    ], "the blocking one first, then the newest of the rest"


async def test_the_inbox_is_of_one_project(service: UDLService) -> None:
    # (Another project's things are not in it: the inbox is the one a person is reading.)
    await service.call("orch_set_project", {"name": "other", "title": "Other"})
    await service.call("orch_set_action", {"project": "other", "title": "Not mine"})

    found = await service.call("orch_inbox", {"project": "udl"})

    assert isinstance(found, Inbox)
    assert [one.title for one in found.actions] == ["Merge it"]
    assert found.project == "udl"


async def test_the_request_names_a_project_only(service: UDLService) -> None:
    assert list(InboxRequest.model_fields) == ["project"]
    found = await service.call("orch_inbox", InboxRequest())

    assert isinstance(found, Inbox)
    assert found.project == "udl"
