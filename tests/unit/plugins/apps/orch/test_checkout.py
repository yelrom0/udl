"""
--------------------
- test_checkout.py -
--------------------

28.8: after a merge, each host's checkout follows `main`.

On a temp repo with a bare `origin`: a merge moves a clean checkout to the merge commit; a dirty
one, one on another branch and one that diverged are left as they are, each named in a `system`
note and an `orch.checkout.behind` event. A host that cannot be reached is noted too.
"""

# ruff: noqa: S603  (a test's own git, with fixed argv)

# System Imports
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import settings
from support.git import GIT
from support.harnesses import ScriptedShell
from udl.core.service import UDLService
from udl.errors import PluginFailed
from udl.hosts.shell import HostShell, inherited
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
    done = subprocess.run(
        [*GIT, *args], cwd=cwd, env=inherited(), capture_output=True, text=True, check=True
    )
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


async def test_a_checkout_that_is_not_a_repo_says_the_fetch_failed(
    service: UDLService, repo: Path
) -> None:
    """A local checkout at a folder with no `.git`: git itself fails, and the reason says the
    fetch failed (not that the host is unknown). Scripted, so no real git or ssh runs."""
    nowhere = repo.parent / "nowhere"
    nowhere.mkdir()
    shell = ScriptedShell()

    async def run(argv: object = (), **kwargs: object) -> ProcessResult:
        raise PluginFailed("fatal: not a git repository")

    shell.run = run

    class _Shells(_Hosts):
        """Only the one checkout: the test is of its reason, not of the others."""

        async def shell(self, name: str) -> HostShell:
            assert name == "local"
            return shell

    found = await RepoRecords(AppRecords(service.db)).repo("udl", "udl")
    assert found is not None
    await RepoRecords(AppRecords(service.db)).save_repo(
        found.model_copy(update={"checkouts": {"local": str(nowhere)}})
    )

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=_Shells(shell),
    )

    assert len(behind) == 1
    assert behind[0].why.startswith("its fetch failed")
    assert "not a git repository" in behind[0].why


class _Hosts:
    """Hosts whose only shell is one a test scripts (`follow` needs `shell(name)`)."""

    def __init__(self, shell: HostShell) -> None:
        self._shell = shell

    async def shell(self, name: str) -> HostShell:
        return self._shell


async def test_a_timeout_is_noted_not_raised(service: UDLService, repo: Path) -> None:
    shell = ScriptedShell()
    shell.never(["git", "-C", str(repo), "fetch", "origin", "main"])

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=_Hosts(shell),
    )

    assert len(behind) == 1
    assert "timed out" in behind[0].why


async def test_an_unknown_host_is_named_as_such(service: UDLService, repo: Path) -> None:
    """A host the records do not have: `Hosts.shell` raises `DoesNotExist`, and the reason says
    so — not 'could not be reached', which is a host that is there and did not answer."""
    found = await RepoRecords(AppRecords(service.db)).repo("udl", "udl")
    assert found is not None
    await RepoRecords(AppRecords(service.db)).save_repo(
        found.model_copy(update={"checkouts": {**found.checkouts, "elsewhere": "/srv/udl"}})
    )
    bring(repo)

    behind = await follow(
        AppRecords(service.db),
        project="udl",
        key="F-1.1",
        repo="yelrom0/udl",
        shells=service.hosts,
    )

    lost = next(one for one in behind if one.host == "elsewhere")
    assert "not known" in lost.why
    # (The reachable one still followed: one bad host never stops the others.)
    assert git(repo, "rev-parse", "HEAD") == git(repo, "rev-parse", "origin/main")


async def test_each_pass_names_a_checkout_left_behind_again(
    service: UDLService, repo: Path
) -> None:
    """Every pass over a checkout that cannot follow adds its own note: what dedupes the event is
    the merge's own stamp (the caller's), so the same reason after another merge is said again."""
    at(repo / "dirty.txt", "work\n")
    for _ in range(2):
        await follow(
            AppRecords(service.db),
            project="udl",
            key="F-1.1",
            repo="yelrom0/udl",
            shells=service.hosts,
        )

    task = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert task is not None
    notes = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, task.id)
    assert len([note for note in notes if "did not follow" in note.text]) == 2


def test_the_event_is_offered() -> None:
    assert CHECKOUT_BEHIND.name == "orch.checkout.behind"
    assert CHECKOUT_BEHIND.data is CheckoutBehind
