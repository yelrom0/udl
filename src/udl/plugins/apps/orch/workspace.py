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
import asyncio
import contextlib
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Final, Literal, Protocol, runtime_checkable
from uuid import uuid4

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.clock import Clock, pause
from udl.core.service import UDLService
from udl.db.manager import DBManager
from udl.errors import ConfigError, NotAllowed, PluginFailed, ToolUnavailable
from udl.hosts import Host, HostName, HostShell
from udl.hosts.shell import PROBE, SYSTEMD, ResourceLimits, scope_of, unit_name
from udl.interfaces.security import current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.links import pull_ref
from udl.plugins.apps.orch.models import (
    MERGED,
    GateRun,
    OwnerKind,
    PushResult,
    render_branch,
)
from udl.plugins.apps.orch.records import LinkRecords, Task, TaskRecords
from udl.process import ProcessResult

__all__ = ["GateLane", "Worktree", "prepare", "push", "remove"]

GATE_MARGIN: Final = timedelta(minutes=10)
"""How long a gate's lane lease outlasts the gate itself, so a lane never runs out while the
gate it holds for is still running (it is renewed as well: this is the first lease's ttl)."""


@runtime_checkable
class PullLike(Protocol):
    """What a pull has to be for a push (the github integration's `PullInfo`, or a fake's): where
    it is on github.com."""

    html_url: str


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
    tasks: TaskRecords | None = None,
) -> Worktree:
    """`task`'s worktree on `host`, made off `stacked_on` (or the repo's base branch).

    The task's branch is its own (`task.branch`), or the repo's pattern with the task's key and
    title in it. An existing worktree of the task's is reused as it is. With `tasks`, the branch
    and the path are saved on the task (what a push then reads). Raises ConfigError when the
    repo has no checkout on `host`, and NotAllowed when the branch is already there without the
    task's worktree (its commits are not the task's, so udl does not take it over)."""
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
    found = Worktree(host=host.name, path=path, branch=branch, base=base)
    if await _branch_of(shell, path) == branch:
        return await _saved(found, task=task, tasks=tasks)
    await _git(shell, ["-C", checkout, "fetch", "origin"])
    if await _has_branch(shell, checkout, branch):
        raise NotAllowed(
            f"there is already a branch {branch!r} in {repo.github}",
            hint="A branch udl did not make is not the task's: move it, or name the task's.",
        )
    await _git(
        shell, ["-C", checkout, "worktree", "add", path, "-b", branch, _off(stacked_on, base)]
    )
    return await _saved(found, task=task, tasks=tasks)


async def _saved(found: Worktree, *, task: Task, tasks: TaskRecords | None) -> Worktree:
    """`found`, saved on `task` when the records are given (a push reads them there)."""
    if tasks is not None and (task.branch, task.worktree) != (found.branch, found.path):
        await tasks.save_task(
            task.model_copy(update={"branch": found.branch, "worktree": found.path})
        )
    return found


async def remove(found: Worktree, *, repo: RepoLike, shell: HostShell) -> None:
    """Take `found`'s worktree and its branch away (after a merge), and what its pushes wrote
    about the branch: nothing when it is already gone, so removing twice is what removing once
    is."""
    checkout = repo.checkouts.get(found.host)
    if checkout is None:
        raise ConfigError(f"the repo {repo.name!r} has no checkout on {found.host!r}")
    if await _branch_of(shell, found.path) is not None:
        await _git(shell, ["-C", checkout, "worktree", "remove", "--force", found.path])
    if await _has_branch(shell, checkout, found.branch):
        await _git(shell, ["-C", checkout, "branch", "-D", found.branch])
    if await _sha_of(shell, checkout, _pushed(found.branch)):
        await _git(shell, ["-C", checkout, "update-ref", "-d", _pushed(found.branch)])


def _off(stacked_on: str | None, base: str) -> str:
    """What a new branch is made off: `origin/<base>` for the repo's own, `origin/<branch>` for a
    stacked task's (the fetch just brought it in, and another host's checkout has no local
    branch to offer)."""
    return f"origin/{stacked_on}" if stacked_on else f"origin/{base}"


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
    """The gate runs of a host: `slots` of them at once (`gates_per_host`, 1 by default), each a
    `Lease` named `orch-gate:<host>:<n>`, so two agents' test runs on one machine never fight for
    a slot (and a run that dies still lets the next in, once its lease expires).

    The lane is taken with `take()` (which waits for a free slot) and given back with
    `release()`, or both with `held()`, which gives it back however the gate ends."""

    RETRY: Final = timedelta(seconds=2)
    """How long to wait before asking for a slot again (a gate is minutes long: twice a second
    would be noise on the database)."""

    def __init__(
        self,
        database: DBManager,
        *,
        host: str,
        holder: str,
        clock: Clock,
        ttl: timedelta = timedelta(minutes=5),
        slots: int = 1,
    ) -> None:
        if slots < 1:
            raise ValueError(f"{slots!r} is not one slot or more")
        self._db = database
        self._names = [f"orch-gate:{host}:{number}" for number in range(slots)]
        self._taken: str | None = None
        self._holder = holder
        self._clock = clock
        self._ttl = ttl

    @property
    def names(self) -> list[str]:
        """The leases its slots are (what a stuck gate's lane is, when it is looked for)."""
        return list(self._names)

    @property
    def name(self) -> str:
        """The lease it holds (the first slot's, before it holds one)."""
        return self._taken or self._names[0]

    @property
    def holder(self) -> str:
        """Who holds it (unique to this lane: two lanes of one name are two different runs, so
        neither may be taken for the other)."""
        return self._holder

    async def take(self) -> None:
        """Wait until a slot of this lane is the holder's (however long the gates before it
        run)."""
        while True:
            for name in self._names:
                if await self._db.claim_lease(name, self._holder, self._ttl) is not None:
                    self._taken = name
                    return
            await pause(self._clock, self.RETRY)

    async def release(self) -> None:
        """Give the slot back (nothing when it is not the holder's any more)."""
        if self._taken is not None:
            await self._db.release_lease(self._taken, self._holder)
            self._taken = None

    async def _renew(self) -> None:
        """Keep the slot while a gate runs: a gate may take longer than the lease's ttl, and a
        slot whose lease ran out is a slot another gate can take (27.5)."""
        every = self._ttl / 3
        while True:
            await pause(self._clock, every)
            if self._taken is not None:
                await self._db.claim_lease(self._taken, self._holder, self._ttl)

    @asynccontextmanager
    async def held(self) -> AsyncGenerator[None]:
        """The lane, for as long as the block runs: renewed while it runs, and given back
        however it ends."""
        await self.take()
        keeping = asyncio.create_task(self._renew())
        try:
            yield
        finally:
            keeping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeping
            await self.release()


async def push(
    task: Task,
    *,
    repo: RepoLike,
    host: Host,
    shell: HostShell,
    service: UDLService,
    records: AppRecords,
    title: str | None,
    body: str | None,
    within: timedelta,
    memory: int | None = None,
    cleanup: Sequence[str] | None = None,
    slots: int = 1,
) -> PushResult:
    """Push `task`'s work: its branch to origin, then its pull request (27.5).

    Nothing uncommitted (else NotAllowed with `git status`'s summary), nothing on the branch this
    task did not push and the worktree does not have (else NotAllowed: someone else's commits are
    not overwritten — what the task pushed last is kept under `refs/udl/pushed/<branch>`), rebased
    on what it is built on when that has
    moved, the repo's gate run in its host's lane (one at a time, 27.5), and on a pass `git push
    --force-with-lease=<branch>:<origin's tip when we looked>`, then `github_open_pull` and a
    `pull` link on the task. A gate that fails, a rebase
    that conflicts or a push git refuses is NotAllowed: nothing is pushed, and the agent is told
    why.

    `title` and `body` are the pull's, and None leaves them as they are on a second push: what a
    person wrote in the description is not a push's to wipe.

    `memory` caps the gate's memory and `slots` is how many gates the host runs at once (27.12):
    a gate the kernel stops at the cap, or one udl stops at its time, pushes nothing and says
    which it was; `cleanup` (the repo's own argv) runs in the worktree after either, and what it
    says is added to the refusal."""
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
    # (Whatever is on the branch now, against what this task last pushed (`refs/udl/pushed`, which
    # only its own pushes write: the tracking ref is shared by every worktree of the checkout and
    # any fetch moves it). Its own rewritten commits are its own to replace; anyone else's are
    # refused rather than overwritten, and the lease pinned to origin's tip means the same holds
    # for a push to the branch while the gate runs, 27.5.)
    seen = await _sha_of(shell, path, _pushed(branch))
    await _git(shell, ["-C", path, "fetch", "origin"])
    moved = await _sha_of(shell, path, f"origin/{branch}")
    if moved and moved != seen and not await _contains(shell, path, moved):
        raise NotAllowed(
            f"{branch!r} has commits this worktree does not",
            hint=(
                f"Someone else pushed to it: `git pull --rebase origin {branch}` in the worktree,"
                " then push again."
            ),
        )
    onto = await _built_on(task, repo=repo, records=records)
    if not await _contains(shell, path, onto):
        await _rebase(shell, path, onto, key=task.key)
    lane = GateLane(
        service.db,
        host=host.name,
        holder=f"{task.assignee or 'owner'}:{task.key}:{uuid4().hex[:8]}",
        clock=service.clock,
        ttl=within + GATE_MARGIN,
        slots=slots,
    )
    unit = unit_name("udl-gate", task.project, task.key, uuid4().hex[:8])
    limits = ResourceLimits(memory=memory) if memory else None
    started = service.clock.now()
    async with lane.held():
        gate = await _gate(
            shell,
            repo.gate,
            path,
            within=within,
            key=task.key,
            limits=limits,
            unit=unit,
            cleanup=cleanup,
        )
    capped = _over_cap(gate, limits)
    output = _tails(gate.stdout, gate.stderr)
    if capped is not None and cleanup:
        output = _said(output, await _cleanup(shell, cleanup, path))
    ran = GateRun(
        exit_code=gate.returncode,
        output=output,
        seconds=round((service.clock.now() - started).total_seconds(), 1),
    )
    if capped is not None:
        raise NotAllowed(
            f"the gate passed its memory cap of {capped.words}",
            hint=_stopped_hint("memory", ran.output),
        )
    if gate.returncode != 0:
        raise NotAllowed(
            f"the gate failed with exit code {gate.returncode}",
            hint=ran.output or None,
        )
    await _git(shell, ["-C", path, "push", *_lease(branch, moved), "origin", branch])
    await _git(shell, ["-C", path, "update-ref", _pushed(branch), "HEAD"], within=GIT_QUIET)
    first = not await _has_pull(task, records=records)
    pull = await _as_udl(
        service,
        "github_open_pull",
        {
            "repo": repo.github,
            "head": branch,
            "title": title or (task.title if first else None),
            "base": repo.base_branch,
            "body": await _body(task, body, records=records) if body or first else None,
        },
    )
    url = pull.html_url if isinstance(pull, PullLike) else ""
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


def _pushed(branch: str) -> str:
    """The ref a task's own pushes write with the sha they pushed: what tells its own earlier
    commits (rewritten by a rebase, an amend or a squash, and its own to replace) from someone
    else's on the same branch (27.5)."""
    return f"refs/udl/pushed/{branch}"


def _lease(branch: str, at: str) -> list[str]:
    """`--force-with-lease` for `branch`, pinned to the sha the remote had when we looked: the
    plain form compares against a tracking ref that the fetch just before the push has moved,
    and it would protect nothing (27.5). A branch that is not there yet has nothing to lease."""
    return [f"--force-with-lease={branch}:{at}"] if at else []


async def _built_on(task: Task, *, repo: RepoLike, records: AppRecords) -> str:
    """What the task's branch is built on: the pull of the task it is stacked on, while that
    pull is open (its branch is still there), else the repo's base branch on origin."""
    base = f"origin/{repo.base_branch}"
    if not task.needs:
        return base
    links = LinkRecords(records)
    tasks = TaskRecords(records)
    for key in task.needs:
        needed = await tasks.task(task.project, key)
        pulls = [
            link.url
            for link in await links.for_owner(task.project, OwnerKind.TASK, key)
            if link.link_kind == "pull"
        ]
        if needed is None or needed.state == MERGED or not pulls:
            continue
        if needed.branch:
            return f"origin/{needed.branch}"
    return base


async def _has_pull(task: Task, *, records: AppRecords) -> bool:
    """Whether the task already has a pull (so a push updates it, rather than opening one)."""
    links = await LinkRecords(records).for_owner(task.project, OwnerKind.TASK, task.key)
    return any(link.link_kind == "pull" for link in links)


async def _as_udl(service: UDLService, name: str, request: dict[str, object]) -> BaseModel:
    """Call an operation as udl itself, not as the agent whose push it is: opening the pull is
    udl's, on the agent's behalf (an agent may not call it itself)."""
    token = current_principal.set(None)
    try:
        return await service.call(name, request)
    finally:
        current_principal.reset(token)


async def _body(task: Task, body: str | None, *, records: AppRecords) -> str:
    """What the pull says: `body` (when there is one), then the task it is for, and what it
    merges after when it is stacked on another task's pull."""
    text = (body or "").strip()
    lines = [text, "", f"Task: `{task.key}`"] if text else [f"Task: `{task.key}`"]
    lines += await _merge_after(task, records=records)
    return "\n".join(lines)


async def _merge_after(task: Task, *, records: AppRecords) -> list[str]:
    """`Merge after #N` for each task `task` needs that has a pull (an empty list when it needs
    none, or none of them has one yet)."""
    links = LinkRecords(records)
    found: list[str] = []
    for key in task.needs:
        urls = [
            link.url
            for link in await links.for_owner(task.project, OwnerKind.TASK, key)
            if link.link_kind == "pull"
        ]
        ref = pull_ref(urls[0]) if urls else None
        if ref is not None:
            found.append(f"Merge after #{ref[1]}")
    return found


def _summary(dirty: str) -> str:
    """`git status --porcelain`'s lines, as a refusal's hint (the first few of them)."""
    lines = [line for line in dirty.splitlines() if line.strip()]
    return "\n".join(lines[:10])


def _last(output: str, lines: int = 200) -> str:
    """The end of a program's output: what a failure says, and what a gate is read by."""
    found = output.strip().splitlines()
    return "\n".join(found[-lines:])


def _tails(stdout: str, stderr: str, lines: int = 100) -> str:
    """The end of each of a gate's two streams (half the card's 200 lines each, so neither one
    pushes the other's tail out: a suite's summary is on stdout, a compiler's error on stderr)."""
    found = [
        *(_last(stdout, lines).splitlines() if stdout.strip() else []),
        *(_last(stderr, lines).splitlines() if stderr.strip() else []),
    ]
    return "\n".join(found[-2 * lines :])


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
    shell: HostShell,
    gate: list[str],
    path: str,
    *,
    within: timedelta,
    key: str,
    limits: ResourceLimits | None = None,
    unit: str | None = None,
    cleanup: Sequence[str] | None = None,
) -> ProcessResult:
    """Run the repo's gate in the worktree, and answer how it ended: a failing gate is an
    answer, not an error (its exit code and the end of its output are what the agent is told),
    and one that runs past its time or is not installed is NotAllowed with what happened.

    A capped run (27.12) is a transient scope named `unit`, which is what udl stops when the
    gate runs past its time (so the suite's own children go with it, rather than being left
    running on a host nobody is watching); `cleanup` runs after either kind of stop."""
    try:
        return await shell.run(
            gate, cwd=path, within=within, check=False, tail=True, limits=limits, unit=unit
        )
    except TimeoutError as slow:
        said = await _stopped_gate(shell, unit, limits=limits, cleanup=cleanup, path=path)
        raise NotAllowed(
            f"the gate ran past the {within} it gets",
            hint=_said(
                _stopped_hint("timeout", ""),
                _said(
                    "Run it yourself, and fix what hangs, or raise [apps.orch] gate_minutes.",
                    said,
                ),
            ),
        ) from slow
    except ToolUnavailable as missing:
        raise NotAllowed(
            f"the gate is not installed here: {missing.message}",
            hint="Install it on the host that runs the gate.",
        ) from missing
    except PluginFailed as failed:
        # (An ssh that could not reach the host: the gate never ran.)
        raise NotAllowed(f"the gate did not run: {failed.message}", hint=failed.hint) from failed


STOP_GRACE: Final = timedelta(minutes=1)
"""How long stopping a gate's unit gets: it is one call, but a host under a suite's load is
slow to answer."""

CLEANUP_GRACE: Final = timedelta(minutes=5)
"""How long a repo's cleanup gets after a stopped gate (27.12): a container teardown, not a
suite."""


def _over_cap(gate: ProcessResult, limits: ResourceLimits | None) -> ResourceLimits | None:
    """The cap a gate ended at, when the kernel stopped it there: SIGKILL (137, or -9 from a
    shell) on a run whose cap the host enforced, which udl did not stop itself (a timeout stops
    the unit and says so, 27.12). None: it ended on its own, or the cap was not enforced."""
    if limits is None or gate.limits != "enforced" or gate.returncode not in (137, -9):
        return None
    return limits


async def _stopped_gate(
    shell: HostShell,
    unit: str | None,
    *,
    limits: ResourceLimits | None,
    cleanup: Sequence[str] | None,
    path: str,
) -> str:
    """Stop the gate's unit (so its children go too) and run the repo's cleanup: what a timeout
    does before the agent is told, and what it says, when it says anything.

    Only a capped run on a host that enforces its caps is a unit: anywhere else the gate was the
    plain program, and stopping a unit that does not exist is a failure an agent would read as
    the gate's. A stop that fails is said rather than raised, and the cleanup runs whatever the
    stop did (it is what removes what the suite left behind)."""
    said = ""
    if unit is not None and limits is not None and await _enforces(shell):
        try:
            await shell.run(
                ["systemctl", "--user", "stop", scope_of(unit)], within=STOP_GRACE, check=False
            )
        except (PluginFailed, ToolUnavailable, TimeoutError) as failed:
            said = f"stopping it: {failed}"
    if cleanup is None:
        return said
    return _said(said, await _cleanup(shell, cleanup, path))


async def _enforces(shell: HostShell) -> bool:
    """Whether a capped run on this host really is a unit to stop (27.12): the same probe the
    shell makes before it caps a run, asked here because a gate that ran past its time has no
    result to read. A host without user systemd has no unit to stop, so it is not asked to."""
    try:
        said = await shell.run([*SYSTEMD, "true"], within=PROBE, check=False)
    except PluginFailed, ToolUnavailable, TimeoutError:
        return False
    return said.returncode == 0


async def _cleanup(shell: HostShell, cleanup: Sequence[str], path: str) -> str:
    """Run the repo's cleanup argv in the worktree, and answer the end of what it said (a
    cleanup that fails is said, not raised: the gate's stop is what the agent is told)."""
    try:
        result = await shell.run(
            list(cleanup), cwd=path, within=CLEANUP_GRACE, check=False, tail=True
        )
    except (TimeoutError, PluginFailed, ToolUnavailable) as failed:
        return f"cleanup: {failed}"
    return _tails(result.stdout, result.stderr)


def _stopped_hint(reason: Literal["memory", "timeout"], said: str) -> str:
    """What a stopped gate's refusal says first: which stop it was (`stopped: memory`, or
    `stopped: timeout`), so an agent and a person read the same two words the result's field
    has — then what the gate, or the cleanup, said."""
    return _said(f"stopped: {reason}", said)


def _said(first: str, second: str) -> str:
    """Two things to say, the second only when it has something in it."""
    return f"{first}\n{second}" if second.strip() else first


async def _sha_of(shell: HostShell, path: str, ref: str) -> str:
    """The commit `ref` points at, or "" when there is no such ref (a branch not pushed yet)."""
    if not await _succeeds(shell, ["-C", path, "rev-parse", "--verify", f"{ref}^{{commit}}"]):
        return ""
    return await _git(shell, ["-C", path, "rev-parse", ref], within=GIT_QUIET)
