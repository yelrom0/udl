"""
------------------------
- test_workspace.py -
------------------------

A task's worktree, on a real git repository: a bare `origin` on a temp folder, and a checkout of
it that `prepare()` makes a worktree from. Nothing here touches the network or this repository.
"""

# System Imports
import subprocess
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.errors import ConfigError, NotAllowed
from udl.hosts import Host, shell_for
from udl.plugins.apps.orch.models import Repo
from udl.plugins.apps.orch.records import Task
from udl.plugins.apps.orch.workspace import prepare, remove

GIT: list[str] = ["git", "-c", "user.name=udl", "-c", "user.email=udl@example.com"]


def git(*args: str, cwd: Path) -> str:
    """Run git in `cwd` and answer what it printed (its own error when it fails)."""
    done = subprocess.run(  # noqa: S603 - (a test's own argv, never a user's)
        [*GIT, *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A temp folder: the host's work dir, the bare `origin` and a checkout of it."""
    (tmp_path / "work").mkdir()
    (tmp_path / "checkout").mkdir()
    return tmp_path


@pytest.fixture
def repo(home: Path) -> Repo:
    """The `origin` with one commit on `main`, and a checkout of it, as a repo record names
    them."""
    bare = home / "origin.git"
    git("init", "--bare", "-b", "main", str(bare), cwd=home)
    checkout = home / "checkout"
    git("init", "-b", "main", cwd=checkout)
    (checkout / "README.md").write_text("udl\n")
    git("add", "README.md", cwd=checkout)
    git("commit", "-m", "the first commit", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-u", "origin", "main", cwd=checkout)
    return Repo(
        project="udl",
        name="udl",
        github="yelrom0/udl",
        account="github:yelrom0",
        base_branch="main",
        checkouts={"local": str(checkout)},
    )


@pytest.fixture
def host(home: Path) -> Host:
    """This machine, with the temp folder as its work dir."""
    return Host.local(str(home / "work"))


@pytest.fixture
def task() -> Task:
    """A task to do in the repo."""
    return Task(project="udl", feature="F-1", key="F-1.1", title="The worktree a task is done in")


# ---------------------------------------------------------------------------------------------
# prepare: the worktree a task is done in
# ---------------------------------------------------------------------------------------------


async def test_prepare_makes_the_worktree_on_the_task_s_branch(
    repo: Repo, host: Host, task: Task, home: Path
) -> None:
    found = await prepare(task, repo=repo, host=host, shell=shell_for(host))

    assert found.branch == "feat/F-1.1-the-worktree-a-task-is-done-in"
    assert found.base == "main"
    assert found.path == str(home / "work" / "udl" / "F-1.1-udl")
    assert Path(found.path, "README.md").read_text() == "udl\n"  # noqa: ASYNC240
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=Path(found.path)) == found.branch
    # (Off origin's main, not the checkout's: what a fresh fetch has.)
    assert git("rev-parse", "HEAD", cwd=Path(found.path)) == git(
        "rev-parse", "origin/main", cwd=home / "checkout"
    )


async def test_prepare_reuses_the_worktree_a_task_already_has(
    repo: Repo, host: Host, task: Task
) -> None:
    first = await prepare(task, repo=repo, host=host, shell=shell_for(host))
    (Path(first.path) / "work.txt").write_text("still here\n")

    again = await prepare(task, repo=repo, host=host, shell=shell_for(host))

    assert again == first
    assert (Path(again.path) / "work.txt").read_text() == "still here\n"


async def test_prepare_branches_off_the_task_it_is_stacked_on(
    repo: Repo, host: Host, task: Task, home: Path
) -> None:
    """(Off `origin/<branch>`, which the fetch brings in: the needed task's worktree is on
    another host, and a checkout here has no local branch of it to offer.)"""
    stacked = Task(project="udl", feature="F-1", key="F-1.2", title="It goes on top")
    await prepare(task, repo=repo, host=host, shell=shell_for(host))
    git("push", "origin", "feat/F-1.1-the-worktree-a-task-is-done-in", cwd=home / "checkout")

    found = await prepare(
        stacked,
        repo=repo,
        host=host,
        shell=shell_for(host),
        stacked_on="feat/F-1.1-the-worktree-a-task-is-done-in",
    )

    assert found.base == "feat/F-1.1-the-worktree-a-task-is-done-in"
    assert found.branch == "feat/F-1.2-it-goes-on-top"
    assert git("rev-parse", "HEAD", cwd=Path(found.path)) == git(
        "rev-parse", "feat/F-1.1-the-worktree-a-task-is-done-in", cwd=home / "origin.git"
    )


async def test_a_repo_udl_does_not_know_where_it_is_is_refused(
    repo: Repo, host: Host, task: Task
) -> None:
    elsewhere = repo.model_copy(update={"checkouts": {"thevault": "/srv/udl"}})

    with pytest.raises(ConfigError, match="no checkout on 'local'"):
        await prepare(task, repo=elsewhere, host=host, shell=shell_for(host))


async def test_a_task_whose_branch_is_taken_is_refused(
    repo: Repo, host: Host, task: Task, home: Path
) -> None:
    """(A branch already made by hand, with no worktree of the task's: udl does not take it
    over, since its commits are not the task's.)"""
    branch = repo.branch(type="feat", key=task.key, slug=task.title)
    git("branch", branch, cwd=home / "checkout")

    with pytest.raises(NotAllowed, match="already a branch"):
        await prepare(task, repo=repo, host=host, shell=shell_for(host))


# ---------------------------------------------------------------------------------------------
# remove: after a merge
# ---------------------------------------------------------------------------------------------


async def test_remove_takes_the_worktree_and_its_branch_away(
    repo: Repo, host: Host, task: Task, home: Path
) -> None:
    found = await prepare(task, repo=repo, host=host, shell=shell_for(host))

    # (What a push of it wrote about the branch.)
    git("update-ref", f"refs/udl/pushed/{found.branch}", "HEAD", cwd=home / "checkout")
    await remove(found, repo=repo, shell=shell_for(host))

    assert not Path(found.path).exists()  # noqa: ASYNC240
    branches = git("branch", "--list", cwd=home / "checkout")
    assert found.branch not in branches
    # (And what its pushes wrote about the branch: nothing of the task is left.)
    assert (
        subprocess.run(  # noqa: S603, ASYNC221 - (a test's own argv, a git that ends at once)
            [*GIT, "show-ref", "--verify", f"refs/udl/pushed/{found.branch}"],
            cwd=home / "checkout",
            capture_output=True,
            check=False,
        ).returncode
        != 0
    )


async def test_remove_is_happy_when_the_worktree_is_already_gone(
    repo: Repo, host: Host, task: Task
) -> None:
    found = await prepare(task, repo=repo, host=host, shell=shell_for(host))
    await remove(found, repo=repo, shell=shell_for(host))

    await remove(found, repo=repo, shell=shell_for(host))
