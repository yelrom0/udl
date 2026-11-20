"""
--------------------------------
- test_pr_status.py -
--------------------------------

`orch_pr_status` (27.5): a task's pull's readiness (27.6) as an operation, and `wait`, which
holds until GitHub's checks and the review bot have finished with this head — or until the wait
runs out. The pull is read through udl's own `github_get_pull`, so these tests register a fake
`github` app that answers it, and a fake clock the wait sleeps on.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.models import (
    IN_PROGRESS,
    OwnerKind,
    Principal,
    PrStatusRequest,
    Readiness,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Feature,
    FeatureRecords,
    LinkRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)
from udl.plugins.registry import APPS, PluginRegistry

PULL: str = "https://github.com/yelrom0/udl/pull/7"
HEAD: str = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


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


def running(*, bot: bool = False) -> FakePull:
    """A pull whose check is still running, and that the bot has not commented on yet."""
    return FakePull(
        checks=[{"name": "check", "status": "in_progress", "conclusion": None}],
        comments=[],
    )


class GetPullRequest(BaseModel):
    """`github_get_pull`'s request, as the fake reads it."""

    repo: str
    number: int


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull, then the pulls a test queues."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.pulls: list[FakePull] = [FakePull()]
        """What each call answers, in order (the last one answers every call after it)."""
        self.asked: int = 0

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation(
                "get_pull", title="One pull", hints=OperationHints(read_only=True, open_world=True)
            )
        ]

    async def get_pull(self, request: GetPullRequest) -> FakePull:
        """The next pull the test queued (the last one, once they run out)."""
        found = self.pulls[min(self.asked, len(self.pulls) - 1)]
        self.asked += 1
        return found


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github and the orch app."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


def test_pr_status_is_read_only_and_waits() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert offered["pr_status"].hints == OperationHints(read_only=True, open_world=True)
    assert offered["pr_status"].request is PrStatusRequest
    assert offered["pr_status"].response is Readiness


@pytest.fixture
def clock() -> FakeClock:
    """The clock the wait sleeps on (a test moves it on)."""
    return FakeClock()


@pytest.fixture
async def service(tmp_path: Path, clock: FakeClock) -> AsyncIterator[UDLService]:
    """A service with a project (and its review bot), a task in progress, and its pull's link."""
    async with UDLService(settings(tmp_path), registry=orch_registry(), clock=clock) as running_:
        records = AppRecords(running_.db)
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
        yield running_


async def status_of(service: UDLService, **request: Any) -> Readiness:
    """`orch_pr_status`, as a caller of any interface calls it."""
    found = await service.call("orch_pr_status", request)

    assert isinstance(found, Readiness)
    return found


async def test_a_clean_pull_is_ready_without_waiting(service: UDLService) -> None:
    found = await status_of(service, key="F-1.1")

    assert found.ready
    assert found.waited == 0.0


async def test_without_wait_a_pull_whose_checks_are_running_is_not_ready(
    service: UDLService,
) -> None:
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pulls = [running()]

    found = await status_of(service, key="F-1.1")

    assert not found.ready
    assert found.blockers == [
        "its checks are still running: check",
        "the review bot (claude[bot]) has not commented on it",
    ]


async def test_wait_holds_until_the_checks_and_the_bot_have_finished(
    service: UDLService, clock: FakeClock
) -> None:
    """(The card's done-when: a wait ends when CI and the bot are done, not before.)"""
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pulls = [running(), running(), FakePull()]

    waiting = asyncio_status(service, key="F-1.1", wait=True)
    await clock.advance(timedelta(minutes=1))
    found = await waiting

    assert found.ready
    assert found.waited > 0
    assert app.asked == 3


async def test_wait_gives_up_after_the_time_it_is_given(
    service: UDLService, clock: FakeClock
) -> None:
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pulls = [running()]

    waiting = asyncio_status(service, key="F-1.1", wait=True, minutes=5)
    await clock.advance(timedelta(minutes=6))
    found = await waiting

    assert not found.ready
    assert found.blockers[0] == "its checks are still running: check"
    assert found.waited >= 5 * 60


async def test_wait_does_not_hold_for_a_pull_that_cannot_be_ready(
    service: UDLService, clock: FakeClock
) -> None:
    """(A draft, or a check that failed, is an answer at once: waiting changes nothing.)"""
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pulls = [FakePull(draft=True)]

    found = await status_of(service, key="F-1.1", wait=True)

    assert found.blockers == ["it is a draft"]
    assert found.waited == 0.0


def asyncio_status(service: UDLService, **request: Any) -> asyncio.Future[Readiness]:
    """`orch_pr_status` started, so a test can move the clock on before awaiting it."""
    return asyncio.ensure_future(service.call("orch_pr_status", request))  # pyright: ignore[reportReturnType]
