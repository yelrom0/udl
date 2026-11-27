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
import asyncio
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
from support.git import git
from udl.automations.events import EventRecord
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import ConfigError, DoesNotExist, NotAllowed, PluginError, PluginFailed
from udl.hosts import Host
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
    PullState,
    ReadyRequest,
    SetActionRequest,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    LinkRecords,
    Note,
    NoteRecords,
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
    Task,
    TaskRecords,
)
from udl.plugins.apps.orch.workspace import prepare
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
    # (28.7: not admin — a project's merger may be a reviewer, and the role table cannot know
    # which agent that is, so the operation's own checks decide who may merge.)
    assert offered["merge"].hints == OperationHints(destructive=True, open_world=True)
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


async def test_a_row_carries_its_pulls_checks_and_review(service: UDLService) -> None:
    """(29.8: the row says what the link's state says — the checks and the bot's word.)"""
    records = AppRecords(service.db)
    links = LinkRecords(records)
    link = (await links.for_owner("udl", OwnerKind.TASK, "F-1.1"))[0]
    await links.save(link.model_copy(update={"state": PullState(checks="fail", review="changes")}))

    row = (await listed(service)).ready[0]

    assert (row.checks, row.review) == ("fail", "changes")


async def test_a_done_row_says_who_merged_it_and_whether_it_was_auto(
    service: UDLService,
) -> None:
    """(A hand merge says who; the merger's own says so too — Q-8's Done.)"""
    await add_task(service, "F-1.2", number=2)
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    found = await tasks.task("udl", "F-1.2")
    assert found is not None
    moved = await tasks.move(found, MERGED, by=Principal(kind="human", name="human:owner"))
    await NoteRecords(records).add(
        Note(
            owner_kind=OwnerKind.TASK,
            owner=moved.id,
            note_kind=NoteKind.SYSTEM,
            text="Merged by claude.",
        )
    )

    row = (await listed(service)).done[0]

    assert row.merged_by == "claude"


async def test_an_auto_merge_says_so_in_the_done_row(service: UDLService) -> None:
    await add_task(service, "F-1.2", number=2)
    records = AppRecords(service.db)
    found = await TaskRecords(records).task("udl", "F-1.2")
    assert found is not None
    moved = await TaskRecords(records).move(
        found, MERGED, by=Principal(kind="human", name="human:owner")
    )
    await NoteRecords(records).add(
        Note(
            owner_kind=OwnerKind.TASK,
            owner=moved.id,
            note_kind=NoteKind.SYSTEM,
            text="Merged by claude (auto merge).",
        )
    )

    row = (await listed(service)).done[0]

    assert row.merged_by == "claude (auto merge)"


async def test_a_merged_by_name_holds_a_dot(service: UDLService) -> None:
    """(A person's name is not cut at its first dot: the note's own tail is what ends it.)"""
    await add_task(service, "F-1.2", number=2)
    records = AppRecords(service.db)
    found = await TaskRecords(records).task("udl", "F-1.2")
    assert found is not None
    moved = await TaskRecords(records).move(
        found, MERGED, by=Principal(kind="human", name="human:owner")
    )
    await NoteRecords(records).add(
        Note(
            owner_kind=OwnerKind.TASK,
            owner=moved.id,
            note_kind=NoteKind.SYSTEM,
            text="Merged by a.b, and done: nothing follows the base.",
        )
    )

    row = (await listed(service)).done[0]

    assert row.merged_by == "a.b"


async def test_a_row_that_was_not_merged_says_nobody(service: UDLService) -> None:
    row = (await listed(service)).ready[0]

    assert row.merged_by == ""


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
    await tasks.move(found, MERGED, by=Principal(kind="human", name="human:owner"))

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
        await tasks.move(found, MERGED, by=Principal(kind="human", name="human:owner"))

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


async def test_a_task_stacked_on_an_unmerged_one_cannot_be_merged_first(
    service: UDLService,
) -> None:
    """(The card's order: nothing above a pull that is not merged. `needs` is 27.6's, so the
    merge re-checks it rather than trusting the list it was read from.)"""
    await add_task(service, "F-1.2", number=2, needs=["F-1.1"], state=READY)
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    app.pull(2)

    with pytest.raises(NotAllowed, match=r"it waits on F-1\.1"):
        await merged(service, key="F-1.2", confirm="yelrom0/udl#2")

    assert app.merged == []


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
    await tasks.move(found, MERGED, by=Principal(kind="human", name="human:owner"))

    listed_now = await service.call("orch_promotion", PromotionRequest())

    assert isinstance(listed_now, PromotionList)
    assert listed_now.promotion == ["rc", "main"]
    assert [one.task for one in listed_now.rows] == ["F-1.1"]


async def test_promote_marks_them_done_with_its_note(service: UDLService) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.move(found, MERGED, by=Principal(kind="human", name="human:owner"))

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
    await tasks.move(found, MERGED, by=Principal(kind="human", name="human:owner"))

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
    await tasks.move(found, MERGED, by=Principal(kind="human", name="human:owner"))

    with pytest.raises(NotAllowed, match="no branches after its base"):
        await service.call("orch_promote", PromoteRequest(tasks=["F-1.1"]))


async def test_promote_refuses_a_task_that_is_not_there(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"F-1\.9"):
        await service.call("orch_promote", PromoteRequest(tasks=["F-1.9"]))


# ---------------------------------------------------------------------------------------------
# What a merge leaves behind: the worktree goes, unless it cannot or must not yet
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def a_repo(tmp_path: Path) -> Path:
    """A bare `origin` and a checkout of it, as 27.5's pushes have."""
    (tmp_path / "work").mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    bare = tmp_path / "origin.git"
    git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    git("init", "-b", "main", cwd=checkout)
    (checkout / "README.md").write_text("udl\n")
    git("add", "README.md", cwd=checkout)
    git("commit", "-m", "the first commit", cwd=checkout)
    git("config", "user.name", "udl", cwd=checkout)
    git("config", "user.email", "udl@example.com", cwd=checkout)
    git("config", "commit.gpgsign", "false", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-u", "origin", "main", cwd=checkout)
    return tmp_path


@pytest.fixture
async def working(tmp_path: Path, a_repo: Path) -> AsyncIterator[UDLService]:
    """The service, its repo and agent, and the task in a real worktree, ready to merge.

    (The project has nothing after its base, so a person's merge makes the task `done`.)"""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        await running.db.upsert_record(Host.local(str(a_repo / "work")))
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]", promotion=[])
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await RepoRecords(records).save_repo(
            Repo(
                project="udl",
                name="udl",
                github="yelrom0/udl",
                account="github:yelrom0",
                base_branch="main",
                gate=["true"],
                checkouts={"local": str(a_repo / "checkout")},
            )
        )
        await AgentRecords(records).save_agent(
            Agent(name="deepseek-b", title="deepseek-b", runner="opencode", host="local")
        )
        tasks = TaskRecords(records)
        await tasks.save_task(
            Task(
                project="udl",
                feature="F-1",
                key="F-1.1",
                title="It",
                repo="udl",
                assignee="deepseek-b",
            )
        )
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
        started = await tasks.task("udl", "F-1.1")
        assert started is not None
        repo = await RepoRecords(records).repo("udl", "udl")
        assert repo is not None
        made = await prepare(
            started,
            repo=repo,
            host=Host.local(str(a_repo / "work")),
            shell=await running.hosts.shell("local"),
        )
        await tasks.save_task(
            started.model_copy(update={"branch": made.branch, "worktree": made.path})
        )
        await LinkRecords(records).add(
            project="udl",
            owner_kind=OwnerKind.TASK,
            owner="F-1.1",
            kind="pull",
            url="https://github.com/yelrom0/udl/pull/1",
        )
        ready = await tasks.task("udl", "F-1.1")
        assert ready is not None
        await tasks.move(ready, READY, by=Principal(kind="system", role="ready"))
        yield running


async def the_task(service: UDLService) -> Task:
    """The task, as it is stored now."""
    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert found is not None
    return found


async def notes_of(service: UDLService) -> list[str]:
    """What the task's notes say, oldest first."""
    found = await the_task(service)
    notes = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, found.id)
    return [one.text for one in notes]


async def merged_events(service: UDLService) -> list[EventRecord]:
    """The `orch.task.merged` events stored so far."""
    return await service.db.list_records(
        EventRecord, RecordQuery(where={"type": "orch.task.merged"})
    )


async def test_a_merge_takes_the_worktree_and_the_branch_away(
    working: UDLService, a_repo: Path
) -> None:
    task = await the_task(working)
    assert task.worktree is not None
    assert task.branch is not None
    path, branch = Path(task.worktree), task.branch

    found = await merged(working, key="F-1.1", confirm="yelrom0/udl#1")

    assert found.state == DONE
    assert not await asyncio.to_thread(path.exists)
    assert git("branch", "--list", branch, cwd=a_repo / "checkout") == ""
    after = await the_task(working)
    assert (after.worktree, after.branch) == (None, None)


async def test_a_merge_follows_the_checkout_to_the_merge_commit(
    working: UDLService, a_repo: Path
) -> None:
    """After a merge, the host's checkout is at `origin/main`'s head. The test itself moves
    `origin/main` (a real merge does; the fake's `merge_pull` only records), and the checkout —
    clean, on `main` — fast-forwards to it."""
    checkout = a_repo / "checkout"
    seed = a_repo / "seed"
    git("clone", str(a_repo / "origin.git"), str(seed), cwd=a_repo)
    (seed / "new.txt").write_text("new\n")
    git("add", "new.txt", cwd=seed)
    git("commit", "-m", "new", cwd=seed)
    git("push", "origin", "main", cwd=seed)
    before = git("rev-parse", "HEAD", cwd=checkout)

    found = await merged(working, key="F-1.1", confirm="yelrom0/udl#1")

    assert found.state == DONE
    after = git("rev-parse", "HEAD", cwd=checkout)
    assert after == git("rev-parse", "origin/main", cwd=checkout)
    assert after != before


async def test_a_checkout_left_behind_after_a_merge_says_so(
    working: UDLService, a_repo: Path
) -> None:
    """A checkout that cannot follow is named in a `system` note and an
    `orch.checkout.behind` event — through the merge, not only through `follow`."""
    checkout = a_repo / "checkout"
    (checkout / "dirty.txt").write_text("work\n")

    await merged(working, key="F-1.1", confirm="yelrom0/udl#1")

    assert any("did not follow" in one for one in await notes_of(working))
    behind = await working.db.list_records(
        EventRecord, RecordQuery(where={"type": "orch.checkout.behind"})
    )
    assert len(behind) == 1


async def test_two_merges_name_the_checkout_twice(working: UDLService, a_repo: Path) -> None:
    """The event's key carries the merge's own stamp: a checkout left behind after a second
    merge is said again, not swallowed by the first one's key."""
    checkout = a_repo / "checkout"
    (checkout / "dirty.txt").write_text("work\n")
    await merged(working, key="F-1.1", confirm="yelrom0/udl#1")
    tasks = TaskRecords(AppRecords(working.db))
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.2", title="Two", repo="udl"))
    found = await tasks.task("udl", "F-1.2")
    assert found is not None
    await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
    started = await tasks.task("udl", "F-1.2")
    assert started is not None
    await LinkRecords(AppRecords(working.db)).add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="F-1.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/2",
    )
    ready = await tasks.task("udl", "F-1.2")
    assert ready is not None
    await tasks.move(ready, READY, by=Principal(kind="system", role="ready"))

    await merged(working, key="F-1.2", confirm="yelrom0/udl#2")

    behind = await working.db.list_records(
        EventRecord, RecordQuery(where={"type": "orch.checkout.behind"})
    )
    assert len(behind) == 2
    assert len({one.dedupe_key for one in behind}) == 2


async def test_a_three_pr_stack_leaves_the_checkout_at_the_last_merge(
    working: UDLService, a_repo: Path
) -> None:
    """The card's Done when: three tasks stacked one on the next, each merged in turn (a stack
    goes bottom first), and after each merge the checkout is at that merge's commit — so the
    test would fail if only the first one followed."""
    checkout = a_repo / "checkout"
    tasks = TaskRecords(AppRecords(working.db))
    for number in (2, 3):
        key = f"F-1.{number}"
        await tasks.save_task(
            Task(
                project="udl",
                feature="F-1",
                key=key,
                title=f"Task {number}",
                repo="udl",
                needs=[f"F-1.{number - 1}"],
            )
        )
        found = await tasks.task("udl", key)
        assert found is not None
        await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
        started = await tasks.task("udl", key)
        assert started is not None
        await LinkRecords(AppRecords(working.db)).add(
            project="udl",
            owner_kind=OwnerKind.TASK,
            owner=key,
            kind="pull",
            url=f"https://github.com/yelrom0/udl/pull/{number}",
        )
        ready = await tasks.task("udl", key)
        assert ready is not None
        await tasks.move(ready, READY, by=Principal(kind="system", role="ready"))
    seed = a_repo / "seed"
    git("clone", str(a_repo / "origin.git"), str(seed), cwd=a_repo)

    for number in (1, 2, 3):
        # (A commit per merge: each merge has something of its own to fast-forward to.)
        (seed / f"new-{number}.txt").write_text(f"{number}\n")
        git("add", f"new-{number}.txt", cwd=seed)
        git("commit", "-m", f"new {number}", cwd=seed)
        git("push", "origin", "main", cwd=seed)
        last = git("rev-parse", "HEAD", cwd=seed)
        await merged(working, key=f"F-1.{number}", confirm=f"yelrom0/udl#{number}")
        assert git("rev-parse", "HEAD", cwd=checkout) == last


async def test_a_follow_that_raises_still_leaves_the_merge(
    working: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_follow_checkout`'s own `except` branch: a checkout step that raises (a bug, not a
    checkout that cannot follow) is a `system` note, no event, and the merge still stands."""
    from udl.plugins.apps.orch import plugin as plugin_module

    async def boom(*args: object, **kwargs: object) -> object:
        raise PluginFailed("the hosts are gone")

    monkeypatch.setattr(plugin_module, "follow_checkouts", boom)

    found = await merged(working, key="F-1.1", confirm="yelrom0/udl#1")

    assert found.state == DONE
    assert any("could not be followed" in one for one in await notes_of(working))
    assert [one.data["task"] for one in await merged_events(working)] == ["F-1.1"]
    assert (
        await working.db.list_records(
            EventRecord, RecordQuery(where={"type": "orch.checkout.behind"})
        )
        == []
    )


async def test_a_worktree_that_cannot_be_taken_away_keeps_the_merge(
    working: UDLService,
) -> None:
    """(The pull is merged whatever became of the checkout: the note says so, and the merge
    still says it happened.)"""
    repos = RepoRecords(AppRecords(working.db))
    repo = await repos.repo("udl", "udl")
    assert repo is not None
    await repos.save_repo(repo.model_copy(update={"checkouts": {}}))

    found = await merged(working, key="F-1.1", confirm="yelrom0/udl#1")

    assert found.state == DONE
    assert any("could not be taken away" in one for one in await notes_of(working))
    after = await the_task(working)
    assert after.worktree is not None
    assert [one.data["task"] for one in await merged_events(working)] == ["F-1.1"]


async def test_a_task_whose_agent_is_gone_keeps_its_worktree(working: UDLService) -> None:
    """(udl cannot say which host it is on, so it says so rather than guessing this one.)"""
    tasks = TaskRecords(AppRecords(working.db))
    found = await the_task(working)
    await tasks.save_task(found.model_copy(update={"assignee": "nobody-udl-knows"}))

    merged_task = await merged(working, key="F-1.1", confirm="yelrom0/udl#1")

    assert merged_task.state == DONE
    assert any("does not know its agent's host" in one for one in await notes_of(working))
    after = await the_task(working)
    assert after.worktree is not None


async def test_an_agent_s_own_ready_leaves_the_worktree_its_session_is_in(
    working: UDLService,
) -> None:
    """(Where `merge = agent`, `orch_ready` is how the agent merges its own task — from inside
    that worktree, so taking it away in the call would take away the folder its session runs in.
    It stays, with a note, until a person takes it away.)"""
    await set_project(working, merge=MergePolicy.AGENT)
    task = await the_task(working)
    assert task.worktree is not None
    path = Path(task.worktree)
    caller = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name="deepseek-b", role="implementer")
    )
    try:
        await working.call("orch_ready", ReadyRequest(key="F-1.1", note="done"))
    finally:
        current_principal.reset(caller)

    assert (await the_task(working)).state == MERGED
    assert await asyncio.to_thread(path.exists)
    assert any("until a person takes it away" in one for one in await notes_of(working))
