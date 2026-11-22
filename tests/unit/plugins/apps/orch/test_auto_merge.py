"""
-------------------------
- test_auto_merge.py -
-------------------------

Auto merge (28.7, O-D13): a project's switch, its merger, and the one operation that sets them.

The merger is an agent — a reviewer or a manager — that merges the project's ready pulls once it
has reviewed them. Only a person turns it on (`orch_set_auto_merge`, owner only), and no other
write can: `orch_set_project`, the importer and the rest keep both fields as they are stored.
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
from udl.errors import NotAllowed
from udl.interfaces.security import Principal as Caller
from udl.interfaces.security import PrincipalKind, current_principal
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers
from udl.plugins.apps.orch.models import (
    IN_PROGRESS,
    MERGED,
    READY,
    Inbox,
    Merged,
    MergeList,
    MergeListRequest,
    MergePolicy,
    OwnerKind,
    Principal,
    Reviewed,
    SetAutoMergeRequest,
    SetProjectRequest,
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

HEAD: str = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""The commit at the head of every pull the fake answers."""


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
    """A `github` app whose one operation answers a clean, reviewed pull."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.pulls: dict[int, FakePull] = {}
        self.merged: list[MergePullRequest] = []

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
        return self.pulls.get(request.number) or self.pull(request.number)

    async def merge_pull(self, request: MergePullRequest) -> MergeResult:
        """Record the merge and answer it."""
        self.merged.append(request)
        return MergeResult(sha=request.sha)


def orch_registry() -> PluginRegistry:
    """The memory database, the fake github and the orch app."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project, a feature, one ready task with its pull, and three agents: an
    implementer, a reviewer and a manager."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]")
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        tasks = TaskRecords(records)
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="One"))
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
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
        agents = AgentRecords(records)
        await agents.save_agent(
            Agent(name="deepseek-a", title="deepseek-a", runner="fake", role=AgentRole.IMPLEMENTER)
        )
        await agents.save_agent(
            Agent(
                name="claude",
                title="Claude",
                runner="fake",
                role=AgentRole.MANAGER,
                manages=ManagerPowers(assign=True, answer=True, review=True, plan=True),
            )
        )
        await agents.save_agent(
            Agent(name="ernie-c", title="ernie-c", runner="fake", role=AgentRole.REVIEWER)
        )
        yield running


async def project(service: UDLService) -> Project:
    """The project, as it is stored."""
    found = await ProjectRecords(AppRecords(service.db)).project("udl")
    assert found is not None
    return found


async def add_task(
    service: UDLService,
    key: str,
    *,
    number: int,
    state: str = IN_PROGRESS,
) -> None:
    """A task of the project with a pull linked, moved to `state`."""
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    await tasks.save_task(Task(project="udl", feature="F-1", key=key, title=f"Task {key}"))
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
    """The project, changed as `changed` says (the records' own write: a test's, not a request)."""
    projects = ProjectRecords(AppRecords(service.db))
    found = await projects.project("udl")
    assert found is not None
    await projects.save_project(found.model_copy(update=changed))


# ---------------------------------------------------------------------------------------------
# What the app offers
# ---------------------------------------------------------------------------------------------


def test_set_auto_merge_is_the_owner_s_alone() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert offered["set_auto_merge"].request is SetAutoMergeRequest
    assert offered["set_auto_merge"].hints.admin, "a person's switch, never a model's"
    assert not offered["set_auto_merge"].hints.mcp, "no agent is given it as a tool"


# ---------------------------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------------------------


async def test_auto_merge_is_off_until_a_person_turns_it_on(service: UDLService) -> None:
    found = await project(service)

    assert (found.auto_merge, found.merger) == (False, None)


async def test_a_person_turns_it_on_with_its_merger(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    found = await project(service)
    assert (found.auto_merge, found.merger) == (True, "claude")


async def test_a_reviewer_is_a_merger_too(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "ernie-c"})

    found = await project(service)
    assert found.merger == "ernie-c"


async def test_turning_it_on_needs_a_merger(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="merger"):
        await service.call("orch_set_auto_merge", {"on": True})


async def test_an_implementer_is_not_a_merger(service: UDLService) -> None:
    # (A merger reviews the work it merges: an implementer merges only its own, which is
    # `merge = agent`'s business, not auto merge's.)
    with pytest.raises(NotAllowed, match="reviewer or a manager"):
        await service.call("orch_set_auto_merge", {"on": True, "merger": "deepseek-a"})


async def test_a_merger_that_is_not_an_agent_of_this_project_is_refused(
    service: UDLService,
) -> None:
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(
            name="elsewhere",
            title="elsewhere",
            runner="fake",
            role=AgentRole.MANAGER,
            projects=["other"],
        )
    )

    with pytest.raises(NotAllowed, match="does not work on"):
        await service.call("orch_set_auto_merge", {"on": True, "merger": "elsewhere"})


async def test_an_agent_cannot_turn_it_on(service: UDLService) -> None:
    # (No model gives itself the merge: its token is refused, whoever it is.)
    caller = current_principal.set(Caller(kind=PrincipalKind.AGENT, name="claude", role="manager"))
    try:
        with pytest.raises(NotAllowed, match="not one of the operations"):
            await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})
    finally:
        current_principal.reset(caller)

    found = await project(service)
    assert found.auto_merge is False


async def test_turning_it_off_keeps_the_merger(service: UDLService) -> None:
    # (Off and on again is the same merger: a person does not name it twice.)
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})
    await service.call("orch_set_auto_merge", {"on": False})

    found = await project(service)
    assert (found.auto_merge, found.merger) == (False, "claude")


# ---------------------------------------------------------------------------------------------
# No other write can change it
# ---------------------------------------------------------------------------------------------


async def test_set_project_cannot_turn_auto_merge_on(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="orch_set_auto_merge"):
        await service.call(
            "orch_set_project", SetProjectRequest(name="udl", auto_merge=True, merger="claude")
        )

    found = await project(service)
    assert found.auto_merge is False


async def test_set_project_cannot_change_the_merger(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    with pytest.raises(NotAllowed, match="orch_set_auto_merge"):
        await service.call("orch_set_project", SetProjectRequest(name="udl", merger="ernie-c"))

    found = await project(service)
    assert found.merger == "claude"


async def test_set_project_keeps_what_it_is_not_given(service: UDLService) -> None:
    # (Saying what is stored is not a change: the importer sets a title, not a merge policy.)
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    await service.call(
        "orch_set_project", SetProjectRequest(name="udl", title="udl", auto_merge=True)
    )

    found = await project(service)
    assert (found.title, found.auto_merge, found.merger) == ("udl", True, "claude")


# ---------------------------------------------------------------------------------------------
# The lists say it
# ---------------------------------------------------------------------------------------------


async def test_the_merge_list_says_auto_merge_and_its_merger(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    found = await service.call("orch_merge_list", MergeListRequest())

    assert isinstance(found, MergeList)
    assert (found.auto_merge, found.merger) == (True, "claude")


async def test_the_inbox_says_auto_merge_and_its_merger(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    found = await service.call("orch_inbox", {})

    assert isinstance(found, Inbox)
    assert (found.auto_merge, found.merger) == (True, "claude")


# ---------------------------------------------------------------------------------------------
# The merger's merge (it widens 28.3's rule)
# ---------------------------------------------------------------------------------------------


def as_caller(name: str, role: str) -> Caller:
    """A caller for `with current_principal.set(...)` (an agent's own request)."""
    return Caller(kind=PrincipalKind.AGENT, name=name, role=role)


async def merge_ready(service: UDLService, *, as_: Caller | None = None, sha: str = HEAD) -> Any:
    """`orch_merge` for `F-1.1`, as `as_` (a person when None)."""
    caller = current_principal.set(as_) if as_ is not None else None
    try:
        return await service.call(
            "orch_merge",
            {
                "key": "F-1.1",
                "confirm": "yelrom0/udl#1",
                "sha": sha,
            },
        )
    finally:
        if caller is not None:
            current_principal.reset(caller)


async def test_the_merger_merges_a_ready_pull(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    merged = await merge_ready(service, as_=as_caller("claude", "manager"))

    assert isinstance(merged, Merged)
    assert merged.state == MERGED


async def test_the_merger_does_not_merge_another_project_s_task(
    service: UDLService,
) -> None:
    # (A merger merges the project whose merger it is, and no other.)
    records = AppRecords(service.db)
    await ProjectRecords(records).save_project(
        Project(name="liively", title="liively", review_bot="claude[bot]")
    )
    await FeatureRecords(records).save_feature(Feature(project="liively", key="F-2", title="It"))
    tasks = TaskRecords(records)
    await tasks.save_task(Task(project="liively", feature="F-2", key="F-2.1", title="Two"))
    found = await tasks.task("liively", "F-2.1")
    assert found is not None
    await tasks.move(found, IN_PROGRESS, by=Principal(kind="system", role="start"))
    await LinkRecords(records).add(
        project="liively",
        owner_kind=OwnerKind.TASK,
        owner="F-2.1",
        kind="pull",
        url="https://github.com/yelrom0/liively/pull/9",
    )
    found = await tasks.task("liively", "F-2.1")
    assert found is not None
    await tasks.move(found, READY, by=Principal(kind="system", role="ready"))
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude", "project": "udl"})

    caller = current_principal.set(as_caller("claude", "manager"))
    try:
        with pytest.raises(NotAllowed, match="not claude's to merge"):
            await service.call(
                "orch_merge",
                {
                    "key": "F-2.1",
                    "confirm": "yelrom0/liively#9",
                    "sha": HEAD,
                    "project": "liively",
                },
            )
    finally:
        current_principal.reset(caller)


async def test_the_merger_does_not_merge_a_pull_that_moved(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    with pytest.raises(NotAllowed, match="has moved since it was listed"):
        await merge_ready(service, as_=as_caller("claude", "manager"), sha="0000000" + HEAD[7:])


async def test_the_merger_s_own_task_needs_a_review_bot(service: UDLService) -> None:
    # (Nobody reviews the reviewer: without a review bot, its own task waits for a person.)
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_all([found.model_copy(update={"assignee": "claude"})])
    await set_project(service, review_bot=None)
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    with pytest.raises(NotAllowed, match="waiting for a person"):
        await merge_ready(service, as_=as_caller("claude", "manager"))


async def test_the_merger_s_own_task_merges_with_a_review_bot(service: UDLService) -> None:
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_all([found.model_copy(update={"assignee": "claude"})])
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    merged = await merge_ready(service, as_=as_caller("claude", "manager"))

    assert merged.state == MERGED


async def test_an_implementer_cannot_merge_while_auto_merge_is_on(
    service: UDLService,
) -> None:
    # (Auto merge wins over `merge = agent`: the merger reviews, and merges.)
    await set_project(service, merge=MergePolicy.AGENT)
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_all([found.model_copy(update={"assignee": "deepseek-a"})])
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    with pytest.raises(NotAllowed, match="not deepseek-a's to merge"):
        await merge_ready(service, as_=as_caller("deepseek-a", "implementer"))


async def test_a_manager_merger_reviews_a_task_outside_its_subtree(
    service: UDLService,
) -> None:
    # (A merger reviews what it merges, whether or not the task is its report's.)
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_all([found.model_copy(update={"assignee": "deepseek-a"})])
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    caller = current_principal.set(as_caller("claude", "manager"))
    try:
        reviewed = await service.call(
            "orch_review_task",
            {"key": "F-1.1", "verdict": "approve", "reasons": "Fine.", "head": HEAD},
        )
    finally:
        current_principal.reset(caller)

    assert isinstance(reviewed, Reviewed)
    assert reviewed.verdict == "approve"


async def test_a_ready_row_says_what_the_merger_is_doing(service: UDLService) -> None:
    # (The first ready one is the one being reviewed; the rest are next in its queue.)
    await add_task(service, "F-1.2", number=2, state=READY)
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})

    found = await service.call("orch_merge_list", MergeListRequest())

    assert isinstance(found, MergeList)
    assert [(one.task, one.merger) for one in found.ready] == [
        ("F-1.1", "reviewing"),
        ("F-1.2", "next"),
    ]


async def test_a_row_whose_changes_were_asked_says_so(service: UDLService) -> None:
    await service.call("orch_set_auto_merge", {"on": True, "merger": "claude"})
    caller = current_principal.set(as_caller("claude", "manager"))
    try:
        await service.call(
            "orch_review_task",
            {"key": "F-1.1", "verdict": "changes", "reasons": "Fix it.", "head": HEAD},
        )
    finally:
        current_principal.reset(caller)

    found = await service.call("orch_merge_list", MergeListRequest())

    assert isinstance(found, MergeList)
    assert [(one.task, one.merger) for one in found.coming] == [("F-1.1", "changes asked")]
