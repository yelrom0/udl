"""
-----------------------------
- test_merge_operations.py -
-----------------------------

`orch_merge_list`, `orch_merge`, `orch_promotion` and `orch_promote` (28.3), through the service
as the CLI, the API and MCP call them.

The pulls are read through udl's own `github_get_pull` and merged through `github_merge_pull`, so
these tests register a fake `github` app that answers both (the real integration's own tests are
25.5's).
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel, ValidationError

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import ConfigError, DoesNotExist, NotAllowed, PluginError
from udl.interfaces.security import Principal as Caller
from udl.interfaces.security import PrincipalKind, current_principal
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.models import (
    DONE,
    IN_PROGRESS,
    MERGED,
    READY,
    Actions,
    ActionWhen,
    Merged,
    MergeList,
    MergeListRequest,
    MergePolicy,
    MergeRequest,
    NoteKind,
    OwnerKind,
    Principal,
    Promoted,
    PromoteRequest,
    PromotionList,
    PromotionRequest,
    SetActionRequest,
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

HEAD: str = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""The commit at the head of every pull the fake answers."""
NEXT: str = "77ab012bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
"""The commit a pull that moved since has."""


class FakePull(BaseModel):
    """A `github_get_pull` answer, as readiness and a merge list read it."""

    repo: str = "yelrom0/udl"
    number: int = 1
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
            "body": "## Follow-up review: it (#1), after dc1fd19\n\n**Verdict: all clear**\n",
            "created_at": "2026-11-20T10:00:00Z",
        }
    ]


class GetPullRequest(BaseModel):
    """`github_get_pull`'s request, as the fake reads it."""

    repo: str
    number: int


class MergePullRequest(BaseModel):
    """`github_merge_pull`'s request, as the fake reads it."""

    repo: str
    number: int
    sha: str
    confirm: str
    method: str = "squash"


class MergeResult(BaseModel):
    """`github_merge_pull`'s answer."""

    merged: bool = True
    sha: str = HEAD
    message: str = "merged"


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose two operations answer a pull and merge it."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.pulls: dict[int, FakePull] = {}
        self.merged: list[MergePullRequest] = []
        self.fail_for: int | None = None
        """A pull number `get_pull` refuses with a plugin error (the others answer)."""

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation(
                "get_pull", title="One pull", hints=OperationHints(read_only=True, open_world=True)
            ),
            cls.operation(
                "merge_pull",
                title="Merge a pull",
                hints=OperationHints(destructive=True, open_world=True, admin=True, mcp=False),
            ),
        ]

    def pull(self, number: int, **changed: object) -> FakePull:
        """The pull `number` the fake answers, changed as `changed` says."""
        found = FakePull(
            number=number,
            html_url=f"https://github.com/yelrom0/udl/pull/{number}",
            **changed,  # pyright: ignore[reportArgumentType]
        )
        self.pulls[number] = found
        return found

    async def get_pull(self, request: GetPullRequest) -> FakePull:
        """The pull the test set for that number (a clean one when it set none)."""
        if self.fail_for == request.number:
            raise PluginError(f"pull {request.number} is gone")
        return self.pulls.get(request.number) or self.pull(request.number)

    async def merge_pull(self, request: MergePullRequest) -> MergeResult:
        """Record the merge and answer it."""
        self.merged.append(request)
        return MergeResult(sha=request.sha)


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github and the orch app."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


# ---------------------------------------------------------------------------------------------
# What the app offers
# ---------------------------------------------------------------------------------------------


def test_the_operations_and_their_hints() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert offered["merge_list"].hints == OperationHints(read_only=True, open_world=True)
    assert offered["merge_list"].request is MergeListRequest
    assert offered["merge_list"].response is MergeList
    assert offered["merge"].hints == OperationHints(destructive=True, open_world=True, admin=True)
    assert offered["merge"].request is MergeRequest
    assert offered["merge"].response is Merged
    assert offered["promotion"].hints == OperationHints(read_only=True)
    assert offered["promotion"].request is PromotionRequest
    assert offered["promotion"].response is PromotionList
    assert offered["promote"].request is PromoteRequest
    assert offered["promote"].response is Promoted


# ---------------------------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project, a feature and one task in `ready`, its pull linked."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]", promotion=["rc", "main"])
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await add_task(running, "F-1.1", number=1, state=READY)
        yield running


async def add_task(
    service: UDLService,
    key: str,
    *,
    number: int,
    state: str = IN_PROGRESS,
    needs: list[str] | None = None,
    hold: str | None = None,
) -> None:
    """A task of the project with a pull linked, moved to `state`."""
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    await tasks.save_task(
        Task(
            project="udl",
            feature="F-1",
            key=key,
            title=f"Task {key}",
            needs=needs or [],
            hold=hold,
        )
    )
    found = await tasks.task("udl", key)
    assert found is not None
    await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
    await LinkRecords(records).add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner=key,
        kind="pull",
        url=f"https://github.com/yelrom0/udl/pull/{number}",
    )
    if state != IN_PROGRESS:
        found = await tasks.task("udl", key)
        assert found is not None
        await tasks.move(found, state, by=Principal(kind="system", role="ready"))


async def set_project(service: UDLService, **changed: Any) -> None:
    """The project, changed as `changed` says."""
    projects = ProjectRecords(AppRecords(service.db))
    found = await projects.project("udl")
    assert found is not None
    await projects.save_project(found.model_copy(update=changed))


async def listed(service: UDLService) -> MergeList:
    """`orch_merge_list`, as a caller of any interface calls it."""
    found = await service.call("orch_merge_list", MergeListRequest())

    assert isinstance(found, MergeList)
    return found


async def test_a_ready_task_is_listed_with_its_pull(service: UDLService) -> None:
    found = await listed(service)

    assert [one.task for one in found.ready] == ["F-1.1"]
    row = found.ready[0]
    assert (row.repo, row.number, row.url) == (
        "yelrom0/udl",
        1,
        "https://github.com/yelrom0/udl/pull/1",
    )
    assert (row.feature, row.state, row.title) == ("F-1", READY, "Task F-1.1")
    assert row.blockers == []


async def test_a_pull_that_cannot_be_read_is_that_row_s_own_blocker(
    service: UDLService,
) -> None:
    """(One pull udl cannot read does not hide the others.)"""
    await add_task(service, "F-1.2", number=2)
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.fail_for = 2

    found = await listed(service)

    assert [one.task for one in found.ready] == ["F-1.1"]
    assert [one.task for one in found.coming] == ["F-1.2"]
    assert "its pull could not be read" in found.coming[0].blockers[0]


async def test_the_head_listed_is_the_one_linked(service: UDLService) -> None:
    """(A row says the commit it read: the pull's head, as its link keeps it.)"""
    await service.call("orch_readiness", {"key": "F-1.1"})
    found = await listed(service)

    assert found.ready[0].sha == ""


async def test_a_task_still_coming_is_listed_with_what_it_waits_on(
    service: UDLService,
) -> None:
    await add_task(service, "F-1.2", number=2)
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull(2, mergeable=False, mergeable_state="dirty")

    found = await listed(service)

    assert [one.task for one in found.coming] == ["F-1.2"]
    assert found.coming[0].blockers == ["it does not merge cleanly (dirty)"]


async def test_a_held_task_is_listed_with_its_reason(service: UDLService) -> None:
    await add_task(service, "F-1.3", number=3, state=READY, hold="after 28.3")
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull(3)

    found = await listed(service)

    assert [one.task for one in found.held] == ["F-1.3"]
    assert found.held[0].blockers == ["it is on hold: after 28.3"]
    assert [one.task for one in found.ready] == ["F-1.1"]


async def test_what_merged_lately_says_what_followed_it(service: UDLService) -> None:
    await add_task(service, "F-1.2", number=2, needs=["F-1.1"])
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.move(found, MERGED, by=Principal(kind="system", role="merge"))

    listed_now = await listed(service)

    assert [one.task for one in listed_now.done] == ["F-1.1"]
    assert listed_now.done[0].followed == ["F-1.2"]


async def test_what_merged_more_than_fourteen_days_ago_leaves_the_list(
    tmp_path: Path,
) -> None:
    """(The list is what merged lately: the card's 14 days.)"""
    clock = FakeClock()
    async with UDLService(settings(tmp_path), registry=orch_registry(), clock=clock) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await add_task(running, "F-1.1", number=1, state=READY)
        tasks = TaskRecords(records)
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.move(found, MERGED, by=Principal(kind="system", role="merge"))

        fresh = await listed(running)
        await clock.advance(timedelta(days=15))
        later = await listed(running)

    assert [one.task for one in fresh.done] == ["F-1.1"]
    assert later.done == []


async def test_a_stack_of_three_is_listed_in_merge_order(service: UDLService) -> None:
    await add_task(service, "F-1.2", number=2, needs=["F-1.1"], state=READY)
    await add_task(service, "F-1.3", number=3, needs=["F-1.2"], state=READY)
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull(1)
    app.pull(2)
    app.pull(3)

    found = await listed(service)

    assert [one.task for one in found.ready] == ["F-1.1", "F-1.2", "F-1.3"]


async def test_a_task_with_no_pull_is_not_in_the_list(service: UDLService) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.9", title="Nothing yet"))
    found = await tasks.task("udl", "F-1.9")
    assert found is not None
    await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))

    listed_now = await listed(service)

    assert "F-1.9" not in [one.task for one in listed_now.coming]


# ---------------------------------------------------------------------------------------------
# The merge
# ---------------------------------------------------------------------------------------------


async def merged(service: UDLService, **request: Any) -> Merged:
    """`orch_merge`, as a caller calls it (the head listed, unless it names one)."""
    request.setdefault("sha", HEAD)
    found = await service.call("orch_merge", request)

    assert isinstance(found, Merged)
    return found


async def test_a_ready_task_is_merged_at_its_head(service: UDLService) -> None:
    found = await merged(service, key="F-1.1", confirm="yelrom0/udl#1")

    assert (found.task, found.sha, found.state) == ("F-1.1", HEAD, MERGED)
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert [(one.number, one.sha, one.confirm) for one in app.merged] == [
        (1, HEAD, "yelrom0/udl#1")
    ]


async def test_a_merge_that_names_no_head_is_refused(service: UDLService) -> None:
    """(The head is not optional: a merge always says which head it merges.)"""
    with pytest.raises(ValidationError, match="sha"):
        await service.call("orch_merge", {"key": "F-1.1", "confirm": "yelrom0/udl#1"})

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert app.merged == []


async def test_a_pull_that_moved_since_it_was_listed_is_refused(service: UDLService) -> None:
    """(The card's third test: the head the caller saw is the one merged, or nothing is.)"""
    with pytest.raises(NotAllowed, match="has moved since it was listed"):
        await merged(service, key="F-1.1", confirm="yelrom0/udl#1", sha=NEXT)

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert app.merged == []


async def test_the_head_that_was_listed_is_the_one_merged(service: UDLService) -> None:
    found = await merged(service, key="F-1.1", confirm="yelrom0/udl#1", sha=HEAD)

    assert found.sha == HEAD


async def test_a_merge_that_is_not_confirmed_merges_nothing(service: UDLService) -> None:
    with pytest.raises(ConfigError, match="confirm=yelrom0/udl#1"):
        await merged(service, key="F-1.1", confirm="yelrom0/udl#2")

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert app.merged == []


async def test_a_task_that_is_not_ready_is_not_merged(service: UDLService) -> None:
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull(1, mergeable=False, mergeable_state="dirty")

    with pytest.raises(NotAllowed, match="it does not merge cleanly"):
        await merged(service, key="F-1.1", confirm="yelrom0/udl#1")

    assert app.merged == []


async def test_a_person_merging_with_nothing_after_the_base_is_done_at_once(
    service: UDLService,
) -> None:
    """(The reviewer's merge is the review, and udl's own base is its last branch.)"""
    await set_project(service, promotion=[])

    found = await merged(service, key="F-1.1", confirm="yelrom0/udl#1")

    assert found.state == DONE


async def test_an_agent_may_not_merge_its_task_when_a_person_merges(
    service: UDLService,
) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_task(found.model_copy(update={"assignee": "deepseek-b"}))
    caller = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name="deepseek-b", role="implementer")
    )
    try:
        with pytest.raises(NotAllowed, match="not deepseek-b's to merge"):
            await merged(service, key="F-1.1", confirm="yelrom0/udl#1")
    finally:
        current_principal.reset(caller)


async def test_an_agent_merges_its_own_task_where_the_project_says_agent(
    service: UDLService,
) -> None:
    records = AppRecords(service.db)
    await set_project(service, merge=MergePolicy.AGENT)
    tasks = TaskRecords(records)
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_task(found.model_copy(update={"assignee": "deepseek-b"}))
    caller = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name="deepseek-b", role="implementer")
    )
    try:
        found = await merged(service, key="F-1.1", confirm="yelrom0/udl#1")
    finally:
        current_principal.reset(caller)

    # (An agent's own merge is not a review: it waits at `merged` for a person.)
    assert found.state == MERGED


async def test_a_merge_tells_the_stacked_tasks_to_rebase(service: UDLService) -> None:
    await add_task(service, "F-1.2", number=2, needs=["F-1.1"])

    await merged(service, key="F-1.1", confirm="yelrom0/udl#1")

    stacked = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.2")
    assert stacked is not None
    notes = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, stacked.id)
    assert "Rebase onto the base and push again" in notes[-1].text


async def test_a_merge_makes_what_waited_after_it_now(service: UDLService) -> None:
    await service.call(
        "orch_set_action",
        SetActionRequest(
            project="udl", title="Check the docs", when=ActionWhen.AFTER, after="F-1.1"
        ),
    )

    await merged(service, key="F-1.1", confirm="yelrom0/udl#1")

    listed_actions = await service.call("orch_list_actions", {"project": "udl"})
    actions = Actions.model_validate(listed_actions.model_dump())
    assert [one.when for one in actions.actions] == [ActionWhen.NOW]


async def test_a_task_with_no_pull_cannot_be_merged(service: UDLService) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.8", title="No pull"))
    found = await tasks.task("udl", "F-1.8")
    assert found is not None
    await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))

    with pytest.raises(NotAllowed, match="it has no pull"):
        await merged(service, key="F-1.8", confirm="yelrom0/udl#8")


async def test_a_stack_of_three_merges_in_order_each_prompting_the_next(
    service: UDLService,
) -> None:
    """(The card's done-when: a three-pull stack merges in order on the fakes, and each task
    stacked on what merged is told to rebase and push again.)"""
    await add_task(service, "F-1.2", number=2, needs=["F-1.1"], state=READY)
    await add_task(service, "F-1.3", number=3, needs=["F-1.2"], state=READY)
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    for number in (1, 2, 3):
        app.pull(number)

    first = await merged(service, key="F-1.1", confirm="yelrom0/udl#1")
    after_first = await NoteRecords(AppRecords(service.db)).for_owner(
        OwnerKind.TASK,
        (await TaskRecords(AppRecords(service.db)).task("udl", "F-1.2")).id,  # pyright: ignore[reportOptionalMemberAccess]
    )
    second = await merged(service, key="F-1.2", confirm="yelrom0/udl#2")
    third = await merged(service, key="F-1.3", confirm="yelrom0/udl#3")

    assert [one.state for one in (first, second, third)] == [MERGED, MERGED, MERGED]
    assert "Rebase onto the base and push again" in after_first[-1].text
    assert [(one.number, one.sha) for one in app.merged] == [(1, HEAD), (2, HEAD), (3, HEAD)]


async def test_ready_merges_where_the_project_says_agent_and_the_caller_holds_it(
    service: UDLService,
) -> None:
    """(27.6's deferred branch: an agent that marks its own pull ready merges it where the project
    lets its agents merge, and the task goes `merged`.)"""
    await set_project(service, merge=MergePolicy.AGENT)
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_task(found.model_copy(update={"assignee": "deepseek-b"}))
    caller = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name="deepseek-b", role="implementer")
    )
    try:
        await service.call("orch_ready", {"key": "F-1.1"})
    finally:
        current_principal.reset(caller)

    moved = await tasks.task("udl", "F-1.1")
    assert moved is not None
    assert moved.state == MERGED
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert [(one.number, one.sha) for one in app.merged] == [(1, HEAD)]


async def test_ready_only_marks_ready_where_a_person_merges(service: UDLService) -> None:
    """(The other side: a project where a person merges leaves the task at `ready`.)"""
    await add_task(service, "F-1.2", number=2)
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull(2)
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.2")
    assert found is not None
    await tasks.save_task(found.model_copy(update={"assignee": "deepseek-b"}))
    caller = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name="deepseek-b", role="implementer")
    )
    try:
        await service.call("orch_ready", {"key": "F-1.2"})
    finally:
        current_principal.reset(caller)

    moved = await tasks.task("udl", "F-1.2")
    assert moved is not None
    assert moved.state == READY


# ---------------------------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------------------------


async def test_promotion_lists_what_is_merged_and_not_done(service: UDLService) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.move(found, MERGED, by=Principal(kind="system", role="merge"))

    listed_now = await service.call("orch_promotion", PromotionRequest())

    assert isinstance(listed_now, PromotionList)
    assert listed_now.promotion == ["rc", "main"]
    assert [one.task for one in listed_now.rows] == ["F-1.1"]


async def test_promote_marks_them_done_with_its_note(service: UDLService) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.move(found, MERGED, by=Principal(kind="system", role="merge"))

    promoted = await service.call("orch_promote", PromoteRequest(tasks=["F-1.1"], note="In rc."))

    assert isinstance(promoted, Promoted)
    assert promoted.tasks == ["F-1.1"]
    moved = await tasks.task("udl", "F-1.1")
    assert moved is not None
    assert moved.state == DONE
    notes = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, moved.id)
    assert [one.text for one in notes if one.note_kind == NoteKind.PROGRESS] == ["In rc."]
    assert notes[-1].text == "merged → done, by reviewer."


async def test_promote_refuses_a_task_that_is_not_merged(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="is not merged: it is 'ready'"):
        await service.call("orch_promote", PromoteRequest(tasks=["F-1.1"]))


async def test_promote_writes_nothing_when_one_of_them_is_refused(service: UDLService) -> None:
    """(All or nothing: the merged one is not done because a later key was wrong.)"""
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.move(found, MERGED, by=Principal(kind="system", role="merge"))

    with pytest.raises(DoesNotExist, match=r"F-1\.9"):
        await service.call("orch_promote", PromoteRequest(tasks=["F-1.1", "F-1.9"]))

    untouched = await tasks.task("udl", "F-1.1")
    assert untouched is not None
    assert untouched.state == MERGED


async def test_promote_refuses_a_project_with_nothing_after_the_base(service: UDLService) -> None:
    await set_project(service, promotion=[])
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.move(found, MERGED, by=Principal(kind="system", role="merge"))

    with pytest.raises(NotAllowed, match="no branches after its base"):
        await service.call("orch_promote", PromoteRequest(tasks=["F-1.1"]))


async def test_promote_refuses_a_task_that_is_not_there(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"F-1\.9"):
        await service.call("orch_promote", PromoteRequest(tasks=["F-1.9"]))
