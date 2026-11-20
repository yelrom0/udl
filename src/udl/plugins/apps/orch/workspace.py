"""
-------------------
- workspace.py -
-------------------

A task's worktree on its agent's host (27.5): where its work is done, and the branch it is done
on, made off the repo's base (or the branch of the task it is stacked on).

Every git command goes through the host's `HostShell`, so nothing here knows whether the host is
this machine or another: `prepare()` fetches, makes the worktree and answers it; `remove()` takes
it and its branch away once it is merged. A worktree the task already has is reused, never
remade: an agent's uncommitted work is never thrown away by asking where it works.
"""

# System Imports
from datetime import timedelta

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.errors import ConfigError, NotAllowed, PluginFailed
from udl.hosts import Host, HostName, HostShell
from udl.plugins.apps.orch.models import Repo
from udl.plugins.apps.orch.records import Task

__all__ = ["Worktree", "prepare", "remove"]

GIT: timedelta = timedelta(minutes=5)
"""How long one git command gets: a fetch of a big repo is the slow one."""

GIT_QUIET: timedelta = timedelta(minutes=1)
"""How long a git command that reads one thing gets (a branch's name, a path's existence)."""


class Worktree(BaseModel):
    """A task's worktree: on which host, at which path, on which branch, off which base."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    host: HostName
    """The host it is on (an agent's own: the work never leaves it)."""
    path: str
    """Its path there (`<host.work_dir>/<project>/<key>-<repo>`)."""
    branch: str
    """The branch it is on: the task's, from the repo's `branch_pattern`."""
    base: str
    """What it was made off: the repo's base branch, or the stacked task's."""


async def prepare(
    task: Task,
    *,
    repo: Repo,
    host: Host,
    shell: HostShell,
    stacked_on: str | None = None,
) -> Worktree:
    """`task`'s worktree on `host`, made off `stacked_on` (or the repo's base branch).

    The task's branch is its own (`task.branch`), or the repo's pattern with the task's key and
    title in it. An existing worktree of the task's is reused as it is. Raises ConfigError when
    the repo has no checkout on `host`, and NotAllowed when the branch is already there without
    the task's worktree (its commits are not the task's, so udl does not take it over)."""
    checkout = repo.checkouts.get(host.name)
    if checkout is None:
        raise ConfigError(
            f"the repo {repo.name!r} has no checkout on {host.name!r}",
            hint=f"Its checkouts are on: {', '.join(sorted(repo.checkouts)) or 'no host yet'}.",
        )
    branch = task.branch or repo.branch(type="feat", key=task.key, slug=task.title)
    base = stacked_on or repo.base_branch
    path = f"{host.work_dir.rstrip('/')}/{task.project}/{task.key}-{repo.name}"
    if await _branch_of(shell, path) == branch:
        return Worktree(host=host.name, path=path, branch=branch, base=base)
    await _git(shell, ["-C", checkout, "fetch", "origin"])
    if await _has_branch(shell, checkout, branch):
        raise NotAllowed(
            f"there is already a branch {branch!r} in {repo.github}",
            hint="A branch udl did not make is not the task's: move it, or name the task's.",
        )
    await _git(
        shell, ["-C", checkout, "worktree", "add", path, "-b", branch, _off(stacked_on, base)]
    )
    return Worktree(host=host.name, path=path, branch=branch, base=base)


async def remove(found: Worktree, *, repo: Repo, shell: HostShell) -> None:
    """Take `found`'s worktree and its branch away (after a merge): nothing when it is already
    gone, so removing twice is what removing once is."""
    checkout = repo.checkouts.get(found.host)
    if checkout is None:
        raise ConfigError(f"the repo {repo.name!r} has no checkout on {found.host!r}")
    if await _branch_of(shell, found.path) is not None:
        await _git(shell, ["-C", checkout, "worktree", "remove", "--force", found.path])
    if await _has_branch(shell, checkout, found.branch):
        await _git(shell, ["-C", checkout, "branch", "-D", found.branch])


def _off(stacked_on: str | None, base: str) -> str:
    """What a new branch is made off: `origin/<base>` for the repo's own, the branch itself for a
    stacked task's (it is a local branch, not one origin has)."""
    return stacked_on or f"origin/{base}"


async def _git(shell: HostShell, args: list[str], *, within: timedelta = GIT) -> str:
    """Run `git <args>` on the host and answer what it printed."""
    done = await shell.run(["git", *args], within=within)
    return done.stdout.strip()


async def _succeeds(shell: HostShell, args: list[str], *, within: timedelta = GIT_QUIET) -> bool:
    """Whether `git <args>` succeeds (a question git answers with its exit code, not its
    output)."""
    try:
        await shell.run(["git", *args], within=within)
    except PluginFailed:
        return False
    return True


async def _branch_of(shell: HostShell, path: str) -> str | None:
    """The branch checked out at `path`, or None when there is no worktree there."""
    try:
        return await _git(
            shell, ["-C", path, "rev-parse", "--abbrev-ref", "HEAD"], within=GIT_QUIET
        )
    except PluginFailed:
        return None


async def _has_branch(shell: HostShell, checkout: str, branch: str) -> bool:
    """Whether the checkout has a local branch called `branch`."""
    return await _succeeds(
        shell, ["-C", checkout, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"]
    )
