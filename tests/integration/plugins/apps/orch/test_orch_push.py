"""
---------------------
- test_orch_push.py -
---------------------

`orch_push` (27.5): an agent's whole path to a pull request, on a real git repository with a bare
`origin` and a fake `github` app. Nothing here touches the network or this repository.

The gate is an argv in the repo's record (`["sh", "-c", "..."]`), so a test says what a passing
one and a failing one do.
"""

# System Imports
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.engine import registry, settings
from support.git import GIT, git
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import NotAllowed
from udl.hosts import Host
from udl.hosts.shell import inherited
from udl.interfaces.security import Principal, PrincipalKind, current_principal
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch.models import IN_PROGRESS, OwnerKind, PushResult
from udl.plugins.apps.orch.models import Principal as Who
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    LinkRecords,
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
    Task,
    TaskRecords,
)
from udl.plugins.apps.orch.workspace import prepare
from udl.plugins.registry import APPS, PluginRegistry

AGENT: str = "deepseek-b"


class OpenPull(BaseModel):
    """What `github_open_pull` is asked (the parts a push gives it; None: leave it as it is)."""

    repo: str
    head: str
    title: str | None = None
    base: str | None = None
    body: str | None = None


class FakePull(BaseModel):
    """What `github_open_pull` answers, as a push reads it (a `PullInfo`)."""

    repo: str = "yelrom0/udl"
    number: int = 7
    title: str = "It"
    state: str = "open"
    draft: bool = False
    merged: bool = False
    head: str = "feat/F-1.1-it"
    head_sha: str = "abc1234"
    base: str = "main"
    html_url: str = "https://github.com/yelrom0/udl/pull/7"


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull (what a push opens)."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.opened: list[dict[str, Any]] = []

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation("open_pull", title="Open a pull", hints=OperationHints(open_world=True))
        ]

    async def open_pull(self, request: OpenPull) -> FakePull:
        """Record the pull that was asked for (a new one, or the same one updated)."""
        asked = request.model_dump()
        self.opened.append(asked)
        return FakePull(title=str(asked.get("title") or "It"))


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github and the orch app."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


# ---------------------------------------------------------------------------------------------
# A real repository, and a task with a worktree in it
# ---------------------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A temp folder: the host's work dir, the bare `origin` and a checkout of it."""
    (tmp_path / "work").mkdir()
    (tmp_path / "checkout").mkdir()
    bare = tmp_path / "origin.git"
    git("init", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    checkout = tmp_path / "checkout"
    git("init", "-b", "main", cwd=checkout)
    (checkout / "README.md").write_text("udl\n")
    git("add", "README.md", cwd=checkout)
    git("commit", "-m", "the first commit", cwd=checkout)
    git("config", "user.name", "udl", cwd=checkout)
    git("config", "user.email", "udl@example.com", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-u", "origin", "main", cwd=checkout)
    return tmp_path


@pytest.fixture
async def service(tmp_path: Path, home: Path) -> AsyncIterator[UDLService]:
    """A service with the project, its repo, the agent, and the task assigned to it, in its
    worktree."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        await running.db.upsert_record(Host.local(str(home / "work")))
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await RepoRecords(records).save_repo(
            Repo(
                project="udl",
                name="udl",
                github="yelrom0/udl",
                account="github:yelrom0",
                base_branch="main",
                gate=["true"],
                checkouts={"local": str(home / "checkout")},
            )
        )
        await AgentRecords(records).save_agent(
            Agent(name=AGENT, title=AGENT, runner="opencode", host="local")
        )
        task = Task(
            project="udl",
            feature="F-1",
            key="F-1.1",
            title="It",
            repo="udl",
            assignee=AGENT,
        )
        tasks = TaskRecords(records)
        await tasks.save_task(task)
        await tasks.move(task, IN_PROGRESS, by=Who(kind="system", role="start"))
        started = await tasks.task("udl", "F-1.1")
        assert started is not None
        repo = await RepoRecords(records).repo("udl", "udl")
        assert repo is not None
        found = await prepare(
            started,
            repo=repo,
            host=Host.local(str(home / "work")),
            shell=await running.hosts.shell("local"),
        )
        await tasks.save_task(
            started.model_copy(update={"branch": found.branch, "worktree": found.path})
        )
        yield running


@pytest.fixture
def as_agent() -> Iterator[None]:
    """The caller is the agent (an agent token's principal, as an interface sets it)."""
    token = current_principal.set(
        Principal(kind=PrincipalKind.AGENT, name=AGENT, role="implementer")
    )
    try:
        yield
    finally:
        current_principal.reset(token)


@pytest.fixture
async def worktree(service: UDLService) -> Path:
    """The task's worktree, with one commit of its own in it."""
    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert found is not None
    assert found.worktree is not None
    path = Path(found.worktree)
    (path / "work.txt").write_text("the work\n")
    git("add", "work.txt", cwd=path)
    git("commit", "-m", "feat: the work", cwd=path)
    return path


async def pushed(service: UDLService, **request: Any) -> PushResult:
    """`orch_push`, as the agent calls it."""
    found = await service.call("orch_push", request)
    assert isinstance(found, PushResult)
    return found


async def set_gate(service: UDLService, gate: list[str]) -> None:
    """Make the repo's gate `gate` (what a push runs)."""
    repos = RepoRecords(AppRecords(service.db))
    repo = await repos.repo("udl", "udl")
    assert repo is not None
    await repos.save_repo(repo.model_copy(update={"gate": gate}))


# ---------------------------------------------------------------------------------------------
# What it refuses
# ---------------------------------------------------------------------------------------------


async def test_a_push_with_uncommitted_work_is_refused(
    service: UDLService, worktree: Path, as_agent: None
) -> None:
    (worktree / "draft.txt").write_text("not committed\n")

    with pytest.raises(NotAllowed, match="uncommitted work"):
        await pushed(service, key="F-1.1", title="It")

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert app.opened == []


async def test_an_agent_that_is_not_the_task_s_assignee_is_refused(
    service: UDLService, worktree: Path
) -> None:
    token = current_principal.set(
        Principal(kind=PrincipalKind.AGENT, name="deepseek-a", role="implementer")
    )
    try:
        with pytest.raises(NotAllowed, match="is not yours to push"):
            await pushed(service, key="F-1.1", title="It")
    finally:
        current_principal.reset(token)


async def test_a_failing_gate_pushes_nothing(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    script = home / "no-gate.sh"
    script.write_text("echo 'the gate says no'\nexit 5\n")
    await set_gate(service, ["sh", str(script)])

    with pytest.raises(NotAllowed, match="the gate failed with exit code 5") as refused:
        await pushed(service, key="F-1.1", title="It")

    assert "the gate says no" in (refused.value.hint or "")

    assert (
        git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=home / "origin.git")
        == "main"
    )
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert app.opened == []


async def test_a_long_failing_gate_says_its_code_and_the_end_of_its_output(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    """(The card's "the last 200 lines": the start of a suite's noise is not what a failure is
    read from.)"""
    script = home / "gate.sh"
    script.write_text('i=0\nwhile [ $i -lt 300 ]; do echo "line $i"; i=$((i+1)); done\nexit 4\n')
    await set_gate(service, ["sh", str(script)])

    with pytest.raises(NotAllowed, match="exit code 4") as refused:
        await pushed(service, key="F-1.1", title="It")

    hint = refused.value.hint or ""
    assert "line 299" in hint
    assert "line 0" not in hint


async def test_a_gate_that_prints_more_than_udl_keeps_its_end(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    """(A suite that prints more than udl keeps: what a failure says is at the end, not the
    first 64 KiB, 27.5.)"""
    script = home / "loud.sh"
    script.write_text(
        'i=0\nwhile [ $i -lt 3000 ]; do echo "noise line $i of the run, with some padding to '
        'make it long"; i=$((i+1)); done\necho "the summary: 2 failed, 1 passed"\nexit 2\n'
    )
    await set_gate(service, ["sh", str(script)])

    with pytest.raises(NotAllowed, match="exit code 2") as refused:
        await pushed(service, key="F-1.1", title="It")

    hint = refused.value.hint or ""
    assert "the summary: 2 failed, 1 passed" in hint
    assert "noise line 0" not in hint


# ---------------------------------------------------------------------------------------------
# What it does: push, then open the pull
# ---------------------------------------------------------------------------------------------


async def test_a_passing_gate_pushes_and_opens_the_pull(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    found = await pushed(service, key="F-1.1", title="feat(orch): the work (27.5)", body="It.")

    assert found.branch == "feat/F-1.1-it"
    assert found.gate.exit_code == 0
    assert found.pull == "https://github.com/yelrom0/udl/pull/7"
    # (The branch is on origin, at the worktree's commit.)
    assert git("rev-parse", "feat/F-1.1-it", cwd=home / "origin.git") == git(
        "rev-parse", "HEAD", cwd=worktree
    )
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert [one["head"] for one in app.opened] == ["feat/F-1.1-it"]
    assert app.opened[0]["title"] == "feat(orch): the work (27.5)"
    assert "Task: `F-1.1`" in str(app.opened[0]["body"])
    # (The task has the pull's link, so readiness has a pull to ask about.)
    task = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert task is not None
    links = await LinkRecords(AppRecords(service.db)).for_owner("udl", OwnerKind.TASK, "F-1.1")
    assert [(link.link_kind, link.url) for link in links] == [("pull", found.pull)]


async def test_prepare_saves_the_branch_and_the_path_on_the_task(
    service: UDLService, home: Path
) -> None:
    """(What 27.7's dispatcher does: prepare, then the agent pushes what it made.)"""
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.2", title="Later"))
    later = await tasks.task("udl", "F-1.2")
    assert later is not None
    repo = await RepoRecords(records).repo("udl", "udl")
    assert repo is not None

    found = await prepare(
        later,
        repo=repo,
        host=Host.local(str(home / "work")),
        shell=await service.hosts.shell("local"),
        tasks=tasks,
    )

    saved = await tasks.task("udl", "F-1.2")
    assert saved is not None
    assert (saved.branch, saved.worktree) == (found.branch, found.path)


async def test_a_stacked_task_s_pull_says_what_it_merges_after(
    service: UDLService, home: Path, as_agent: None
) -> None:
    """(The card's body: the task's key, then the pull it waits for.)"""
    await pushed(service, key="F-1.1", title="It")
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    await tasks.save_task(
        Task(
            project="udl",
            feature="F-1",
            key="F-1.2",
            title="On top",
            repo="udl",
            assignee=AGENT,
            needs=["F-1.1"],
        )
    )
    on_top = await tasks.task("udl", "F-1.2")
    assert on_top is not None
    repo = await RepoRecords(records).repo("udl", "udl")
    assert repo is not None
    found = await prepare(
        on_top,
        repo=repo,
        host=Host.local(str(home / "work")),
        shell=await service.hosts.shell("local"),
        tasks=tasks,
    )
    (Path(found.path) / "work.txt").write_text("on top\n")
    git("add", "work.txt", cwd=Path(found.path))
    git("commit", "-m", "feat: on top", cwd=Path(found.path))

    await pushed(service, key="F-1.2", title="On top")

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    body = str(app.opened[-1]["body"])
    assert "Task: `F-1.2`" in body
    assert "Merge after #7" in body


async def test_a_second_push_updates_the_same_pull(
    service: UDLService, worktree: Path, as_agent: None
) -> None:
    await pushed(service, key="F-1.1", title="It")
    (worktree / "work.txt").write_text("the work, again\n")
    git("commit", "-am", "fix: the work again", cwd=worktree)

    await pushed(service, key="F-1.1", title="It, again")

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert [one["title"] for one in app.opened] == ["It", "It, again"]


async def test_a_second_push_with_no_body_leaves_the_description(
    service: UDLService, worktree: Path, as_agent: None
) -> None:
    """(What a person wrote in the pull's description is not a push's to wipe.)"""
    await pushed(service, key="F-1.1", title="It", body="Why it is done this way.")
    (worktree / "work.txt").write_text("the work, again\n")
    git("commit", "-am", "fix: the work again", cwd=worktree)

    await pushed(service, key="F-1.1")

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert [one["body"] for one in app.opened] == [
        "Why it is done this way.\n\nTask: `F-1.1`",
        None,
    ]
    assert [one["title"] for one in app.opened] == ["It", None]


async def test_a_push_whose_branch_someone_else_moved_is_refused(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    """(The lease is pinned to what the worktree last saw, and a branch that grew on origin
    before the push is not overwritten: the agent rebases onto it instead, 27.5.)"""
    await pushed(service, key="F-1.1", title="It")
    elsewhere = home / "elsewhere"
    git("clone", str(home / "origin.git"), str(elsewhere), cwd=home)
    git("config", "user.name", "someone", cwd=elsewhere)
    git("config", "user.email", "someone@example.com", cwd=elsewhere)
    git("checkout", "-b", "feat/F-1.1-it", "origin/feat/F-1.1-it", cwd=elsewhere)
    (elsewhere / "theirs.txt").write_text("their work\n")
    git("add", "theirs.txt", cwd=elsewhere)
    git("commit", "-m", "feat: their work", cwd=elsewhere)
    git("push", "origin", "feat/F-1.1-it", cwd=elsewhere)
    theirs = git("rev-parse", "HEAD", cwd=elsewhere)

    # (A fetch in the main checkout moves the tracking ref every worktree shares, as another
    # task's push or an agent's own fetch would: the check does not lean on it.)
    git("fetch", "origin", cwd=home / "checkout")
    (worktree / "work.txt").write_text("the work, again\n")
    git("commit", "-am", "fix: the work again", cwd=worktree)

    with pytest.raises(NotAllowed, match="has commits this worktree does not") as refused:
        await pushed(service, key="F-1.1")

    assert "git pull --rebase" in (refused.value.hint or "")
    # (Their commit is still on origin: the push did not overwrite it.)
    assert git("rev-parse", "feat/F-1.1-it", cwd=home / "origin.git") == theirs

    # (And the way out works: once the agent has taken their commit in, the push goes through.)
    git("pull", "--rebase", "origin", "feat/F-1.1-it", cwd=worktree)
    await pushed(service, key="F-1.1")

    assert git("rev-parse", "feat/F-1.1-it", cwd=home / "origin.git") == git(
        "rev-parse", "HEAD", cwd=worktree
    )
    assert git("log", "--format=%s", "feat/F-1.1-it", cwd=home / "origin.git").splitlines()[:2] == [
        "fix: the work again",
        "feat: their work",
    ]


async def test_a_task_that_rewrote_its_own_pushed_commits_pushes_again(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    """(A rebase the agent resolved, an amend, a squash: the task's own commits rewritten after a
    push are its own to replace, so the push goes through, 27.5.)"""
    await pushed(service, key="F-1.1", title="It")
    git("commit", "--amend", "-m", "feat: the work, said better", cwd=worktree)

    await pushed(service, key="F-1.1")

    assert git("log", "-1", "--format=%s", "feat/F-1.1-it", cwd=home / "origin.git").strip() == (
        "feat: the work, said better"
    )


async def test_a_branch_udl_never_pushed_is_not_overwritten_either(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    """(The branch was pushed with plain git, or from another host's checkout: nothing of this
    task says what is on it, and its commits are not overwritten, 27.5.)"""
    # (The worktree fixture's one commit, pushed with plain git.)
    git("push", "origin", "HEAD:refs/heads/feat/F-1.1-it", cwd=worktree)
    elsewhere = home / "elsewhere"
    git("clone", str(home / "origin.git"), str(elsewhere), cwd=home)
    git("config", "user.name", "someone", cwd=elsewhere)
    git("config", "user.email", "someone@example.com", cwd=elsewhere)
    git("checkout", "feat/F-1.1-it", cwd=elsewhere)
    (elsewhere / "theirs.txt").write_text("their work\n")
    git("add", "theirs.txt", cwd=elsewhere)
    git("commit", "-m", "feat: their work", cwd=elsewhere)
    git("push", "origin", "feat/F-1.1-it", cwd=elsewhere)
    theirs = git("rev-parse", "HEAD", cwd=elsewhere)

    with pytest.raises(NotAllowed, match="has commits this worktree does not"):
        await pushed(service, key="F-1.1", title="It")

    assert git("rev-parse", "feat/F-1.1-it", cwd=home / "origin.git") == theirs


async def test_the_branch_is_rebased_when_the_base_moved(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    """(Two agents' branches in one repo: a push rebases on origin's base, so the PR is not
    behind it.)"""
    checkout = home / "checkout"
    (checkout / "README.md").write_text("udl, and a change on main\n")
    git("commit", "-am", "docs: a change on main", cwd=checkout)
    git("push", "origin", "main", cwd=checkout)
    moved = git("rev-parse", "origin/main", cwd=checkout)

    await pushed(service, key="F-1.1", title="It")

    # (Its commit is on top of the base's, not beside it.)
    done = subprocess.run(  # noqa: S603, ASYNC221 - (a test's own argv, a git that ends at once)
        [*GIT, "merge-base", "--is-ancestor", moved, "HEAD"],
        cwd=worktree,
        env=inherited(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0
    assert git("rev-parse", "HEAD~1", cwd=worktree) == moved


async def test_a_conflict_with_the_base_pushes_nothing(
    service: UDLService, worktree: Path, home: Path, as_agent: None
) -> None:
    checkout = home / "checkout"
    (checkout / "work.txt").write_text("main's version\n")
    git("add", "work.txt", cwd=checkout)
    git("commit", "-m", "docs: main's version", cwd=checkout)
    git("push", "origin", "main", cwd=checkout)

    with pytest.raises(NotAllowed, match="does not rebase"):
        await pushed(service, key="F-1.1", title="It")

    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert app.opened == []


async def test_an_owner_may_push_any_task(service: UDLService, worktree: Path) -> None:
    """(The person who owns udl is not held to an agent's own task.)"""
    current_principal.set(Principal(kind=PrincipalKind.OWNER, name="owner"))

    found = await pushed(service, key="F-1.1", title="It")

    assert found.pull
