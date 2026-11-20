"""
-------------------------
- test_merge_watcher.py -
-------------------------

A pull that merged or closed on GitHub, however udl learned it (28.3): the orch service's event
loop passes it to what a merge sets off, and a pull closed without merging sends its task back to
work with the closing comment as data.

The subscription itself is 26.4's (`test_links.py`); these tests hand the loop one event, as that
file's do.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.engine import registry, settings
from udl.core.service import UDLService
from udl.db.models import JsonObject
from udl.plugins.apps.base import AppEvent, AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.models import (
    DONE,
    IN_PROGRESS,
    MERGED,
    READY,
    NoteKind,
    OwnerKind,
    Principal,
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
"""The pull the events are about."""


class PullDetail(BaseModel):
    """A `github_get_pull` answer, as the closing comment is read from it."""

    repo: str = "yelrom0/udl"
    number: int = 7
    comments: list[dict[str, Any]] = [
        {
            "author": "yelrom0",
            "body": "Not this way: split it.",
            "created_at": "2026-11-21T09:00:00Z",
        }
    ]


class GetPullRequest(BaseModel):
    """`github_get_pull`'s request."""

    repo: str
    number: int


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull's detail."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.asked: list[tuple[str, int]] = []

    @classmethod
    def operations(cls) -> list[Any]:
        return [cls.operation("get_pull", title="One pull")]

    async def get_pull(self, request: GetPullRequest) -> PullDetail:
        """The pull's detail, as its comments are read from it."""
        self.asked.append((request.repo, request.number))
        return PullDetail()


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github and the orch app."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project, a feature and one task in `ready` with its pull linked."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]")
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        tasks = TaskRecords(records)
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="It"))
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
        started = await tasks.task("udl", "F-1.1")
        assert started is not None
        await tasks.move(started, READY, by=Principal(kind="system", role="ready"))
        await LinkRecords(records).add(
            project="udl", owner_kind=OwnerKind.TASK, owner="F-1.1", kind="pull", url=PULL
        )
        yield running


def merged_event(*, merged_by: str = "yelrom0") -> AppEvent:
    """A `github.pr.merged` event for the pull."""
    data: JsonObject = {
        "repo": "yelrom0/udl",
        "number": 7,
        "title": "It",
        "head_sha": "dc1fd19",
        "html_url": PULL,
        "merged_by": merged_by,
    }
    return AppEvent(
        type="github.pr.merged", data=data, occurred_at=datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    )


def closed_event() -> AppEvent:
    """A `github.pr.closed` event for the pull."""
    data: JsonObject = {
        "repo": "yelrom0/udl",
        "number": 7,
        "title": "It",
        "head_sha": "dc1fd19",
        "html_url": PULL,
    }
    return AppEvent(
        type="github.pr.closed", data=data, occurred_at=datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
    )


async def app_of(service: UDLService) -> Orch:
    """The orch app the service runs."""
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    return app


async def test_a_merge_seen_on_github_moves_the_task(service: UDLService) -> None:
    await (await app_of(service)).pull_happened(merged_event(), PULL, service=service)

    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")

    assert found is not None
    assert found.state == MERGED


async def test_a_merge_the_review_bot_made_is_done_at_once(service: UDLService) -> None:
    """(Its merge is its review, and nothing follows udl's base.)"""
    await (await app_of(service)).pull_happened(
        merged_event(merged_by="claude[bot]"), PULL, service=service
    )

    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")

    assert found is not None
    assert found.state == DONE


async def test_a_closed_pull_sends_the_task_back_with_its_comment(service: UDLService) -> None:
    await (await app_of(service)).pull_happened(closed_event(), PULL, service=service)

    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert found is not None
    assert found.state == IN_PROGRESS
    notes = await NoteRecords(AppRecords(service.db)).notes("task", found.id)
    assert "Not this way: split it." in notes[-1].text
    assert notes[-1].note_kind == NoteKind.SYSTEM


async def test_a_merge_of_a_task_already_merged_changes_nothing(service: UDLService) -> None:
    """(The watcher publishes each once, but a merge udl made itself is already in.)"""
    app = await app_of(service)
    await app.pull_happened(merged_event(), PULL, service=service)
    first = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert first is not None

    await app.pull_happened(merged_event(), PULL, service=service)

    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert found is not None
    assert found.state == first.state


async def test_an_event_for_a_pull_no_task_has_is_ignored(service: UDLService) -> None:
    other = "https://github.com/yelrom0/udl/pull/99"

    await (await app_of(service)).pull_happened(merged_event(), other, service=service)

    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")

    assert found is not None
    assert found.state == READY


async def test_the_loop_hands_a_merge_to_what_it_sets_off(service: UDLService) -> None:
    """(The loop's own branch: `follow` calls `happened` for a merge and for a close, and only
    for those.)"""
    seen: list[tuple[str, str]] = []

    async def happened(event: AppEvent, url: str) -> None:
        seen.append((event.type, url))

    waiting: asyncio.Future[object] = asyncio.get_running_loop().create_future()
    links = LinkRecords(AppRecords(service.db))

    await (await app_of(service)).follow(
        links,
        _Sink(),
        _Reader(),
        _Events([merged_event(), closed_event()]),
        waiting,
        happened=happened,
    )

    assert seen == [("github.pr.merged", PULL), ("github.pr.closed", PULL)]


class _Reader:
    """What the loop reads a pull through: a `service.call` that answers a pull's detail."""

    async def call(self, name: str, request: BaseModel | Mapping[str, object]) -> PullDetail:
        return PullDetail()


class _Sink:
    """What the loop publishes to."""

    async def emit(
        self, event_type: str, data: object, *, dedupe_key: str, occurred_at: object = None
    ) -> bool:
        return True


class _Events:
    """A subscription of some events, then the end."""

    def __init__(self, events: list[AppEvent]) -> None:
        self._events = events

    async def __aenter__(self) -> _Events:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> _Events:
        return self

    async def __anext__(self) -> AppEvent:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)
