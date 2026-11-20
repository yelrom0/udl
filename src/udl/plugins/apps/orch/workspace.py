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
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Final, Protocol

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.clock import Clock, pause
from udl.core.service import UDLService
from udl.db.manager import DBManager
from udl.errors import ConfigError, NotAllowed, PluginFailed
from udl.hosts import Host, HostName, HostShell
from udl.interfaces.security import current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import GateRun, OwnerKind, PushResult, render_branch
from udl.plugins.apps.orch.records import LinkRecords, Task
from udl.process import ProcessResult

__all__ = ["GateLane", "Worktree", "prepare", "push", "remove"]


class RepoLike(Protocol):
    """What a repo has to be for a worktree and a push (its record, or the models' shape of it):
    where it is, what its gate is, and how its branches are named."""

    project: str
    name: str
    github: str
    base_branch: str
    gate: list[str]
    checkouts: dict[str, str]
    branch_pattern: str


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
    repo: RepoLike,
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
    branch = task.branch or render_branch(
        repo.branch_pattern, type="feat", key=task.key, slug=task.title
    )
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


async def remove(found: Worktree, *, repo: RepoLike, shell: HostShell) -> None:
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


class GateLane:
    """One gate run at a time per host: a `Lease` named `orch-gate:<host>`, so two agents' test
    runs on one machine never fight for it (and a run that dies still lets the next in, once its
    lease expires).

    The lane is taken with `take()` (which waits for it) and given back with `release()`, or
    both with `held()`, which gives it back however the gate ends."""

    RETRY: Final = timedelta(seconds=2)
    """How long to wait before asking for the lane again (a gate is minutes long: twice a
    second would be noise on the database)."""

    def __init__(
        self,
        database: DBManager,
        *,
        host: str,
        holder: str,
        clock: Clock,
        ttl: timedelta = timedelta(minutes=5),
    ) -> None:
        self._db = database
        self._name = f"orch-gate:{host}"
        self._holder = holder
        self._clock = clock
        self._ttl = ttl

    @property
    def name(self) -> str:
        """The lease it takes (what a stuck gate's lane is, when it is looked for)."""
        return self._name

    async def take(self) -> None:
        """Wait until this lane is the holder's (however long the gate before it runs)."""
        while await self._db.claim_lease(self._name, self._holder, self._ttl) is None:
            await pause(self._clock, self.RETRY)

    async def release(self) -> None:
        """Give the lane back (nothing when it is not the holder's any more)."""
        await self._db.release_lease(self._name, self._holder)

    @asynccontextmanager
    async def held(self) -> AsyncGenerator[None]:
        """The lane, for as long as the block runs: given back however it ends."""
        await self.take()
        try:
            yield
        finally:
            await self.release()


async def push(
    task: Task,
    *,
    repo: RepoLike,
    host: Host,
    shell: HostShell,
    service: UDLService,
    records: AppRecords,
    title: str,
    body: str,
    within: timedelta,
) -> PushResult:
    """Push `task`'s work: its branch to origin, then its pull request (27.5).

    Nothing uncommitted (else NotAllowed with `git status`'s summary), rebased on the base when
    that has moved, the repo's gate run in its host's lane (one at a time, 27.5), and on a pass
    `git push --force-with-lease`, then `github_open_pull` and a `pull` link on the task. A gate
    that fails, a rebase that conflicts or a push git refuses is NotAllowed: nothing is pushed,
    and the agent is told why."""
    if not task.worktree or not task.branch:
        raise NotAllowed(
            f"{task.key!r} has no worktree yet",
            hint="The dispatcher makes one before an agent works on it (27.7).",
        )
    path, branch = task.worktree, task.branch
    dirty = await _git(shell, ["-C", path, "status", "--porcelain"], within=GIT_QUIET)
    if dirty:
        raise NotAllowed(
            f"{task.key!r} has uncommitted work",
            hint=_summary(dirty),
        )
    await _git(shell, ["-C", path, "fetch", "origin"])
    base = f"origin/{repo.base_branch}"
    if not await _contains(shell, path, base):
        await _rebase(shell, path, base, key=task.key)
    lane = GateLane(service.db, host=host.name, holder=task.assignee or "udl", clock=service.clock)
    started = time.monotonic()
    async with lane.held():
        gate = await _gate(shell, repo.gate, path, within=within)
    ran = GateRun(
        exit_code=gate.returncode,
        output=_last(gate.stderr or gate.stdout),
        seconds=round(time.monotonic() - started, 1),
    )
    if gate.returncode != 0:
        raise NotAllowed(
            f"the gate failed with exit code {gate.returncode}",
            hint=ran.output or None,
        )
    await _git(shell, ["-C", path, "push", "--force-with-lease", "origin", branch])
    pull = await _as_udl(
        service,
        "github_open_pull",
        {
            "repo": repo.github,
            "head": branch,
            "title": title or task.title,
            "base": repo.base_branch,
            "body": _body(task, body),
        },
    )
    url = str(getattr(pull, "html_url", "") or "")
    if url:
        await LinkRecords(records).add(
            project=task.project,
            owner_kind=OwnerKind.TASK,
            owner=task.key,
            kind="pull",
            url=url,
            added_by=task.assignee or "",
        )
    return PushResult(project=task.project, key=task.key, branch=branch, gate=ran, pull=url)


async def _as_udl(service: UDLService, name: str, request: dict[str, object]) -> BaseModel:
    """Call an operation as udl itself, not as the agent whose push it is: opening the pull is
    udl's, on the agent's behalf (an agent may not call it itself)."""
    token = current_principal.set(None)
    try:
        return await service.call(name, request)
    finally:
        current_principal.reset(token)


def _body(task: Task, body: str) -> str:
    """What the pull says: `body`, then the task it is for (and, when it is stacked, what it
    merges after)."""
    lines = [body.strip(), "", f"Task: `{task.key}`"] if body.strip() else [f"Task: `{task.key}`"]
    return "\n".join(lines)


def _summary(dirty: str) -> str:
    """`git status --porcelain`'s lines, as a refusal's hint (the first few of them)."""
    lines = [line for line in dirty.splitlines() if line.strip()]
    return "\n".join(lines[:10])


def _last(output: str, lines: int = 200) -> str:
    """The end of a program's output: what a failure says, and what a gate is read by."""
    found = output.strip().splitlines()
    return "\n".join(found[-lines:])


async def _contains(shell: HostShell, path: str, commit: str) -> bool:
    """Whether the worktree at `path` already has `commit` in it (nothing to rebase onto)."""
    return await _succeeds(shell, ["-C", path, "merge-base", "--is-ancestor", commit, "HEAD"])


async def _rebase(shell: HostShell, path: str, onto: str, *, key: str) -> None:
    """Rebase the worktree on `onto`, or refuse: a conflict is the agent's to fix, so nothing is
    pushed and the files git left are what it fixes."""
    try:
        await shell.run(["git", "-C", path, "rebase", onto], within=GIT)
    except PluginFailed as failed:
        raise NotAllowed(
            f"{key!r} does not rebase onto {onto}: fix the conflict and push again",
            hint=failed.hint,
        ) from failed


async def _gate(
    shell: HostShell, gate: list[str], path: str, *, within: timedelta
) -> ProcessResult:
    """Run the repo's gate in the worktree, and answer how it ended (a failing gate is an answer,
    not an error: its output is what the agent is told)."""
    try:
        return await shell.run(gate, cwd=path, within=within)
    except PluginFailed as failed:
        # (`HostShell.run` raises for a non-zero exit; the code and output are what a push says.)
        return ProcessResult(returncode=1, stdout="", stderr=failed.hint or failed.message)
