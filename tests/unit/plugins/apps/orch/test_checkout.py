"""
--------------------
- test_checkout.py -
--------------------

28.8: after a merge, each host's checkout follows `main`.

On a temp repo with a bare `origin`: a merge moves a clean checkout to the merge commit; a dirty
one, one on another branch and one that diverged are left as they are, each named in a `system`
note and an `orch.checkout.behind` event. A host that cannot be reached is noted too.
"""

# ruff: noqa: S603, S607  (a test's own git, with fixed argv)

# System Imports
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import settings
from support.harnesses import ScriptedShell
from udl.core.service import UDLService
from udl.hosts.shell import HostShell
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.checkout import CHECKOUT_BEHIND, CheckoutBehind, follow
from udl.plugins.apps.orch.models import OwnerKind
from udl.plugins.apps.orch.records import (
    Feature,
    FeatureRecords,
    NoteRecords,
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
    Task,
    TaskRecords,
)
from udl.process import ProcessResult


def git(cwd: Path, *args: str) -> str:
    """Run `git <args>` in `cwd` and answer what it printed."""
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def commit(cwd: Path, message: str) -> str:
    """Make a commit with everything in `cwd`, and answer its sha."""
    git(cwd, "add", "-A")
    git(cwd, "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", message)
    return git(cwd, "rev-parse", "HEAD")


def at(path: Path, text: str = "one\n") -> None:
    """Write `text` to `path`, making its parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A bare `origin` and a clean checkout of it on `main`."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "--bare", "-b", "main")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(origin), "checkout")
    git(checkout, "config", "user.email", "t@e")
    git(checkout, "config", "user.name", "t")
    at(checkout / "one.txt")
    commit(checkout, "one")
    git(checkout, "push", "origin", "main")
    return checkout


@pytest.fixture
async def service(tmp_path: Path, repo: Path) -> AsyncIterator[UDLService]:
    """A service with the project, its repo and a task in `ready`, whose repo's checkout on
    `local` is the one `repo` made."""
    async with UDLService(settings(tmp_path)) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        tasks = TaskRecords(records)
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="It"))
        await RepoRecords(records).save_repo(
            Repo(
                project="udl",
                name="udl",
                github="yelrom0/udl",
                account="github:yelrom0",
                checkouts={"local": str(repo)},
            )
        )
        yield running


def bring(repo: Path, *, ahead: int = 1) -> None:
    """`origin/main` moved on (as a merge would): `ahead` commits past the checkout."""
    seed = repo.parent / "seed"
    git(repo.parent, "clone", str(repo.parent / "origin.git"), "seed")
    for number in range(ahead):
        at(seed / f"new-{number}.txt", f"{number}\n")
        commit(seed, f"new {number}")
    git(seed, "push", "origin", "main")


async def test_a_clean_checkout_follows_the_merge(service: UDLService, repo: Path) -> None:
    bring(repo)
    before = git(repo, "rev-parse", "HEAD")

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=service.hosts,
    )

    assert behind == []
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")
    assert git(repo, "rev-parse", "HEAD") != before


async def test_a_dirty_checkout_is_left_and_noted(service: UDLService, repo: Path) -> None:
    bring(repo)
    at(repo / "dirty.txt", "work\n")

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=service.hosts,
    )

    assert len(behind) == 1
    assert behind[0].host == "local"
    assert "uncommitted changes" in behind[0].why
    found = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert found is not None
    notes = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, found.id)
    assert any("did not follow" in note.text for note in notes)


async def test_a_checkout_on_another_branch_is_left_and_noted(
    service: UDLService, repo: Path
) -> None:
    bring(repo)
    git(repo, "checkout", "-b", "feat/x")

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=service.hosts,
    )

    assert len(behind) == 1
    assert "another branch" in behind[0].why
    assert "feat/x" in behind[0].why


async def test_a_diverged_checkout_is_left_and_noted(service: UDLService, repo: Path) -> None:
    at(repo / "mine.txt", "mine\n")
    commit(repo, "mine")
    bring(repo)
    # (The checkout has its own commit and origin/main moved on: the two have diverged.)

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=service.hosts,
    )

    assert len(behind) == 1
    assert "diverged" in behind[0].why
    # (Left as it was: its own commit is still at the top, not origin/main's.)
    assert git(repo, "rev-parse", "HEAD") != git(repo, "rev-parse", "origin/main")
    assert "mine" in git(repo, "log", "-1", "--format=%s")


async def test_a_checkout_that_is_not_a_repo_is_noted_not_raised(
    service: UDLService, repo: Path
) -> None:
    nowhere = repo.parent / "nowhere"
    nowhere.mkdir()
    found = await RepoRecords(AppRecords(service.db)).repo("udl", "udl")
    assert found is not None
    await RepoRecords(AppRecords(service.db)).save_repo(
        found.model_copy(
            update={
                "checkouts": {
                    "local": str(repo),
                    # (A second host that is this machine, at a path that is not a repo: the
                    # honest way to name one that is here and cannot be read.)
                    "nowhere": str(nowhere),
                }
            }
        )
    )
    bring(repo)

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=service.hosts,
    )

    lost = next(one for one in behind if one.checkout == str(nowhere))
    assert "could not be reached" in lost.why or "not a git repository" in lost.why
    # (The reachable one still followed: one bad host never stops the others.)
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")


class _TimeoutShell(ScriptedShell):
    """A host's shell whose every command runs out of time (28.8: a fetch that hangs)."""

    async def run(self, argv: object = (), **kwargs: object) -> ProcessResult:
        raise TimeoutError("git fetch timed out")


class _TimeoutHosts:
    """Hosts whose only shell never answers (a typed fake: `follow` needs `shell(name)`)."""

    async def shell(self, name: str) -> HostShell:
        return _TimeoutShell()


async def test_a_timeout_is_noted_not_raised(service: UDLService, repo: Path) -> None:
    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=_TimeoutHosts(),
    )

    assert len(behind) == 1
    assert "timed out" in behind[0].why


async def test_the_event_follows_a_checkout_left_behind(service: UDLService, repo: Path) -> None:
    """An `orch.checkout.behind` event names a checkout that could not follow."""
    bring(repo)
    at(repo / "dirty.txt", "work\n")

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=service.hosts,
    )

    assert [one.host for one in behind] == ["local"]
    assert CHECKOUT_BEHIND.name == "orch.checkout.behind"
    assert CHECKOUT_BEHIND.data is CheckoutBehind


def test_the_event_is_offered() -> None:
    assert CHECKOUT_BEHIND.name == "orch.checkout.behind"
    assert CHECKOUT_BEHIND.data is CheckoutBehind
