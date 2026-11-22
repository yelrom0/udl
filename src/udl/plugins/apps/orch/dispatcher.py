"""
-------------------
- dispatcher.py -
-------------------

The dispatcher (27.7): what makes an agent work without anybody prompting it.

One loop per enabled agent. Each takes its next task, prepares the worktree, starts or resumes its
session, sends it a turn and streams what the runner says into the transcript, then looks at the
task again: a task that moved on (`ready`, `blocked`, `merged`, `moved`) ends that session's
business, and one that is still `in_progress` is nudged — and a task still going after
`max_nudges` nudges is `blocked` ("agent stalled").

A turn is a long thing (minutes), so a loop holds its own agent's turn and nothing else: one agent
waiting on its harness never holds up another. Loops are woken by `orch.*` and `github.pr.*`
events and by a tick, so an agent whose pull went green hears of it without polling.

What the runner says is stored as the session's transcript (SessionEvent) and published as
`orch.session.*` live events, so a person watching sees the turn as it happens.

Nothing here calls `datetime.now()` or `asyncio.sleep()`: the clock is udl's (tests/support/clock
gives it a FakeClock).
"""

# System Imports
import asyncio
import contextlib
import logging
import os
import re
import socket
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4
from weakref import WeakValueDictionary

# Package Imports
from pydantic import JsonValue, SecretStr

# Local Imports
from udl.clock import Clock, pause
from udl.db.manager import DBManager
from udl.db.models import JsonObject
from udl.errors import DoesNotExist, PluginError, UDLError
from udl.hosts.models import HostRequest
from udl.plugins.apps.base import AppContext, AppEvent, AppEvents, AppRecords
from udl.plugins.apps.orch import managers
from udl.plugins.apps.orch import questions as asked
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch import workspace as space
from udl.plugins.apps.orch.agents import AnAgentEvent, SessionState, an_event
from udl.plugins.apps.orch.models import (
    BLOCKED,
    CANCELLED,
    DONE,
    IN_PROGRESS,
    MERGED,
    QUEUED,
    READY,
    MergeList,
    MergeRow,
    MergerState,
    OwnerKind,
    PendingKind,
    Principal,
    QuestionState,
    Readiness,
    Role,
    TaskKind,
    WaitKind,
)
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.apps.orch.tokens import issue_token, secret_ref
from udl.plugins.runners.base import (
    McpEndpoint,
    RunnerFailed,
    RunnerLimited,
    SessionSpec,
    TurnEnded,
    TurnReason,
    Usage,
)
from udl.plugins.runners.manage import Harnesses

__all__ = ["RULES", "Dispatcher", "first_prompt", "instructions", "merger_prompt"]

log = logging.getLogger(__name__)

RULES: Final = (Path(__file__).with_name("rules.md")).read_text(encoding="utf-8").strip()
"""udl's own rules for an agent session (rules.md, in this package): what every session is told
first, before the project's rules and the agent's brief."""

TICK: Final = timedelta(seconds=30)
"""How often a loop looks at its queue with no event to wake it."""

FAILURES: Final = 3
"""How many times a turn that failed is retried before its session is `failed`."""

BACKOFF: Final = timedelta(seconds=30)
"""How long the first retry of a failed turn waits (`FAILURES` multiplies it)."""

PRUNE_EVERY: Final = timedelta(hours=6)
"""How often the transcripts older than `session_days` are forgotten (and once at start-up)."""

type Preparing = Callable[[stored.Task, stored.Agent], Awaitable[stored.Task]]
"""What makes a task's worktree: the task, with its branch and path, or a refusal."""


class Outcome(StrEnum):
    """How a turn ended, as far as the dispatcher's loop cares (the card's 6)."""

    DONE = "done"  # it answered: the task may have moved, or it is told what changed
    LIMITED = "limited"  # its harness is out of quota: nothing until the quota is back
    RETRY = "retry"  # it failed: retried with a backoff, until `FAILURES`
    FAILED = "failed"  # it failed `FAILURES` times: the session is failed, the task waits
    STOPPED = "stopped"  # a person stopped the session mid-turn: it stays ended


def instructions(project: stored.Project, agent: stored.Agent) -> str:
    """What a session is told before it is given a task: udl's rules, the project's, and the
    agent's brief (the card's 2)."""
    parts = [RULES]
    if project.rules.strip():
        parts.append(f"# The project's rules ({project.name})\n\n{project.rules.strip()}")
    if agent.brief.strip():
        parts.append(f"# Your brief ({agent.name})\n\n{agent.brief.strip()}")
    return "\n\n".join(parts)


def _waiting_kind(pending: Sequence[PendingKind]) -> WaitKind:
    """Which kind of wait a pull's pending blockers are (27.10): `ci` while its checks run (or
    GitHub has not worked out whether it merges), else `bot` — its checks gate the bot, so a
    pull waiting on both is waiting on the checks."""
    return "ci" if {"checks", "mergeable"} & set(pending) else "bot"


def first_prompt(task: stored.Task, *, answers: str = "") -> str:
    """What a session is sent first: which task it is on, where to read it (the card's 2), and
    what was answered while it waited (28.1: the answer is delivered into the session)."""
    said = (
        f"Your task is {task.key}: {task.title}.\n\n"
        "Call `orch_my_task` to read it — its spec, its feature's spec, the project's rules and "
        "its notes — then do it. Work test-first, push with `orch_push`, and say your work is "
        "done with `orch_ready` (or `orch_block`, with the reason, when it is not yours to "
        "settle)."
    )
    return f"{said}\n\n{answers}" if answers else said


def answered(questions: Sequence[stored.Question]) -> str:
    """What a session is told of the answers to its questions: each one's key, what it asked and
    what was answered, fenced as udl's own words (28.1)."""
    told = "\n".join(
        f"- {one.key}, which you asked: {fenced('udl', one.answer or '')}\n  {one.title}"
        for one in questions
    )
    return f"While you waited, these were answered:\n\n{told}"


def nudge(task: stored.Task) -> str:
    """What a session that is still on its task is told: carry on, and say where it is."""
    return (
        f"You are still on {task.key} ({task.title}). Say where it is with `orch_add_note`, and "
        "carry on: push with `orch_push`, and say it is done with `orch_ready`."
    )


def fenced(label: str, text: str) -> str:
    """`text` from outside, in a fenced block labelled with where it came from: what a session is
    told is never instructions from the bot or a CI log (as 17.7 passes events' text).

    The fence is longer than any run of backticks in `text`, so text that carries its own fence
    (a comment quoting code) cannot close it and pass itself off as udl's words."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{label}\n{text}\n{fence}"


def changed(task: stored.Task, what: str) -> str:
    """What a session that is still on its task is told when something did: what changed (the
    pull's checks, the bot's items), and what to do about it."""
    return (
        f"What changed on {task.key}: {what}\n\n"
        "Carry on: push with `orch_push`, and say it is done with `orch_ready`."
    )


def merger_prompt(
    agent: stored.Agent,
    project: stored.Project,
    rows: Sequence[MergeRow],
    skipped: Sequence[MergeRow],
    specs: Mapping[str, str] | None = None,
) -> str:
    """The merger's turn (28.7): the ready pulls of `project`, in the order they merge, with what
    it needs to review and merge each — and the ones it is not to touch, and why."""
    lines = [
        f"You are {agent.name}, {project.name}'s merger: the person who runs it turned auto merge "
        "on, and you merge its ready pulls once you have reviewed them.",
        "",
        "Review each pull's diff against its task's spec, then merge it:",
    ]
    told = specs or {}
    for number, row in enumerate(rows, start=1):
        spec = (told.get(row.task) or "").strip()
        lines.append(
            f"{number}. {row.task} ({row.title}) — {row.url} at {row.sha} (feature "
            f"{row.feature})."
            + (f" Its spec: {spec}" if spec else "")
            + " Review its diff against that (it is in the worktree you are on), then merge it "
            "(`orch_review_task` with `approve`, or `changes` and the reasons, then `orch_merge` "
            "with the pull's number and this sha)."
        )
    if skipped:
        lines.append("")
        for row in skipped:
            lines.append(
                f"Skipped: {row.task} ({row.title}) is your own task, and {project.name} has no "
                "review bot to say whether it is good: a person merges it."
            )
    lines.append("")
    lines.append(
        "Merge them in this order, one at a time: after each, read the list again "
        "(`orch_merge_list`) and carry on with what is still ready."
    )
    return "\n".join(lines)


def manager_prompt(agent: stored.Agent, reports: Sequence[str], items: Sequence[str]) -> str:
    """What a manager is told: what its reports need, as data (27.8), and what it may do about
    each — its powers are the only ones it has, and what it decides is a note a person can read."""
    told = "\n".join(f"- {one}" for one in items)
    return (
        f"You are {agent.name}, and you manage: {', '.join(reports) or 'nobody yet'}.\n\n"
        f"Here is what your reports need:\n\n{told}\n\n"
        "Do what you can with your powers, and nothing else: `orch_suggest` and `orch_assign` "
        "when a report has nothing to do, `orch_answer` when a question is covered by the spec "
        "or a recorded decision (say which) and `orch_escalate` when it is not, and "
        "`orch_review_task` with `approve` or `changes` and the reasons when a report's pull is "
        "ready. Everything you decide is a note on the task with you as its author, so a person "
        "sees who decided what."
    )


class Dispatcher:
    """The dispatcher: one loop per enabled agent, until udl's app stops (the lease going)."""

    def __init__(
        self,
        ctx: AppContext,
        *,
        harnesses: Harnesses,
        prepare: Preparing | None = None,
        tick: timedelta = TICK,
    ) -> None:
        self._ctx = ctx
        self._records = ctx.records
        self._events = ctx.events
        self._service = ctx.service
        self._clock = ctx.clock
        self._settings = OrchSettings.model_validate(ctx.settings.model_dump())
        self._stop = ctx.stop
        self._turns = asyncio.Semaphore(self._settings.max_agents_running)
        self._tokens: dict[str, SecretStr] = {}
        """The MCP token issued per agent, kept for as long as udl runs: issuing one rotates it,
        so a session started twice must not invalidate the other's."""
        self._harnesses = harnesses
        self._turns_made = Turns(
            records=ctx.records,
            events=ctx.events,
            harnesses=harnesses,
            clock=ctx.clock,
            settings=self._settings,
            place=self._turns,
            database=ctx.service.db,
        )
        self._prepare = prepare if prepare is not None else self._prepared
        self._tick = tick
        self._wakes: dict[str, asyncio.Event] = {}
        """One wake per agent: an event that may concern it, or its tick."""

    # -- running -------------------------------------------------------------------------------

    async def run(self) -> None:
        """Dispatch to every enabled agent until `stop` is set. A loop that ends on its own ends
        that agent's dispatching; the others carry on.

        On a server with no `api.api_key` nothing is dispatched at all: without one every request
        is the owner's, so a session's token would limit nothing and an agent would have every
        operation (27.2's rule, which this keeps)."""
        if self._service.settings.api.api_key is None:
            log.warning(
                "no api.api_key: no agent session is started (its token would limit nothing)"
            )
            await self._stop.wait()
            return
        agents = [one for one in await stored.AgentRecords(self._records).agents() if one.enabled]
        if not agents:
            await self._stop.wait()
            return
        await self._pruned()
        for agent in agents:
            self._wakes[agent.name] = asyncio.Event()
        loops = [asyncio.create_task(self._loop(agent)) for agent in agents]
        waking = asyncio.create_task(self._woken_by_events())
        pruning = asyncio.create_task(self._pruning())
        checking = [asyncio.create_task(self._reaching(agent)) for agent in agents]
        stopping = asyncio.create_task(self._stop.wait())
        everything = [*loops, *checking, waking, pruning, stopping]
        try:
            await self._stop.wait()  # (a loop never ends on its own: it logs and carries on)
        finally:
            for task in everything:
                task.cancel()
            for task in everything:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _pruned(self) -> None:
        """Forget the transcripts older than `session_days` (when the app starts, and on every
        `PRUNE_EVERY` tick: a udl that runs for weeks would otherwise never prune)."""
        before = self._clock.now() - timedelta(days=self._settings.session_days)
        pruned = await stored.SessionRecords(self._records).prune(before)
        if pruned:
            log.info(
                "forgot %d session events older than %d days", pruned, self._settings.session_days
            )

    async def _pruning(self) -> None:
        """Prune on every `PRUNE_EVERY` tick, for as long as udl runs."""
        while True:
            await self._clock.sleep(PRUNE_EVERY)
            with contextlib.suppress(PluginError, UDLError):
                await self._pruned()

    async def _woken_by_events(self) -> None:
        """Wake every agent's loop on each `orch.*` or `github.pr.*` event: what an agent waits
        on is its own task's pull going green, or an answer to a question."""
        async with (
            self._events.subscribe("orch.*") as orch,
            self._events.subscribe("github.pr.*") as pulls,
        ):
            await asyncio.gather(self._waking(orch), self._waking(pulls))

    async def _waking(self, subscription: AsyncIterator[AppEvent]) -> None:
        """Wake every loop on each event `subscription` gives — except udl's own live session
        events, which a turn publishes as it runs: waking on those would have the dispatcher
        wake itself, and read every pull again, for every line a runner says."""
        async for event in subscription:
            if event.type.startswith(("orch.session.", "orch.agent.")):
                continue
            for wake in self._wakes.values():
                wake.set()

    async def _loop(self, agent: stored.Agent) -> None:
        """One agent's loop: do what its queue and its session say, then wait to be woken.

        It re-reads the agent every time (a person's pause, a new `max_nudges`, a brief) and never
        ends on its own: one agent's trouble is logged, and the other agents carry on."""
        while not self._stop.is_set():
            try:
                fresh = await stored.AgentRecords(self._records).agent(agent.name)
                if fresh is None:
                    return  # (it was deleted: nothing to dispatch to)
                agent = fresh
                await self._step(agent)
            except Exception as error:  # noqa: BLE001 - (one agent's trouble is not another's)
                log.warning("dispatching to %s: %r", agent.name, error)
            await self._rest(agent)

    async def _rest(self, agent: stored.Agent) -> None:
        """Wait for a wake, or for the tick: whichever comes first."""
        wake = self._wakes.setdefault(agent.name, asyncio.Event())
        wake.clear()
        ticking = asyncio.ensure_future(self._clock.sleep(self._tick))
        woken = asyncio.ensure_future(wake.wait())
        waiting = (ticking, woken)
        try:
            await asyncio.wait(set(waiting), return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in waiting:
                task.cancel()
            for task in waiting:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # -- one step ------------------------------------------------------------------------------

    async def _step(self, agent: stored.Agent) -> None:
        """What one loop does each time it is woken: resume a session udl restarted, or take the
        agent's next task and work it to the point where it moves on."""
        if agent.paused or not agent.enabled:
            return
        if agent.offline:
            return  # (until its host answers again: see `_reaching`)
        sessions = stored.SessionRecords(self._records)
        current = await sessions.current(agent.name)
        if current is not None:
            current = await self._ready_again(current)
            if current is None:
                return
        if current is not None and current.state is SessionState.RUNNING:
            # (udl restarted mid-turn: the harness still has the session, the card's 7.)
            task = await self._task_of(current)
            if task is None:
                await self._end(agent, current)
                return
            await self._work(agent, task, current, resume=True)
            return
        if await self._managing(agent):
            return
        if await self._merging(agent):
            return
        task = await self._pick(agent)
        if task is None:
            return
        task = await self._started(agent, task)
        session = await self._session_for(agent, task)
        if session is None or session.state in (SessionState.FAILED, SessionState.LIMITED):
            return
        await self._work(agent, task, session, resume=False)

    async def _managing(self, agent: stored.Agent) -> bool:
        """One manager's turn, when its reports need something (27.8): what they need is the
        prompt's data, the standing review task is what the session is on, and it is never
        finished — the manager reads its inbox again when there is more in it.

        False when the agent is not a manager with powers, or nothing needs it."""
        if not managers.switched_on(agent.manages):
            return False
        reports = [
            one.name
            for one in await managers.reports(stored.AgentRecords(self._records), agent.name)
        ]
        told = await stored.AgentRecords(self._records).agent(agent.name)
        agent = told or agent
        items = await self._needs(agent, reports)
        news = "\n".join(items)

        if not items:
            if agent.last_news:
                await self._told(agent, "")
            return False
        if news == agent.last_news:
            return False  # (told already, and nothing it did changed that: no turn, 27.8)
        task = await self._standing(agent)
        if task is None:
            return False
        session = await self._session_for(agent, task)
        if session is None:
            return False
        spoke = await self._work(agent, task, session, resume=session.state is SessionState.RUNNING)
        if spoke:
            # (Only what the manager was actually told is remembered: a turn whose runner never
            # started said nothing, and the next tick says it again, 27.8.)
            await self._told(agent, news)
        return True

    async def _merging(self, agent: stored.Agent) -> bool:
        """One merger's turn (28.7), when its project's ready pulls need it: what they are is the
        prompt's data, the standing merge task is what the session is on, and it is never
        finished — the merger reads the list again when there is more on it.

        False when the agent is not a project's merger, or nothing is waiting on it."""
        news = await self._merge_news(agent)
        if not news:
            if agent.last_merge_news:
                await self._merge_told(agent, "")
            return False
        if news == agent.last_merge_news:
            return False  # (told already, and nothing it did changed that: no turn)
        task = await self._standing_merge(agent)
        if task is None:
            return False
        session = await self._session_for(agent, task)
        if session is None:
            return False
        outcome, spoke = await self._turn(agent, session, news)
        session = await stored.SessionRecords(self._records).session(session.id) or session
        if outcome is Outcome.RETRY:
            await self._retried(agent, session, session.last_error)
        await self._end(agent, session)
        if spoke:
            # (Only a turn whose runner started heard the news: the next tick says it again.)
            await self._merge_told(agent, news)
        return True

    async def _merge_told(self, agent: stored.Agent, news: str) -> None:
        """Remember what the merger was last told, so the same list is not a turn again."""
        await stored.AgentRecords(self._records).save_agent(
            agent.model_copy(update={"last_merge_news": news})
        )

    async def _merge_news(self, agent: stored.Agent) -> str:
        """What `agent`'s merger turn is told (28.7): per project it merges, the ready pulls it
        is to review and merge, and the ones it is not (its own, with no review bot)."""
        told: list[str] = []
        projects = stored.ProjectRecords(self._records)
        tasks = stored.TaskRecords(self._records)
        for name in await self._projects_of(agent):
            found = await projects.project(name)
            if found is None or not found.auto_merge or found.merger != agent.name:
                continue
            listed = await self._service.call("orch_merge_list", {"project": name})
            if not isinstance(listed, MergeList):
                continue
            rows = [one for one in listed.ready if one.merger is not MergerState.CHANGES_ASKED]
            kept: list[MergeRow] = []
            skipped: list[MergeRow] = []
            for row in rows:
                task = await tasks.task(name, row.task)
                if task is not None and task.assignee == agent.name and not found.review_bot:
                    skipped.append(row)
                else:
                    kept.append(row)
            if kept or skipped:
                specs: dict[str, str] = {}
                for row in kept:
                    task = await tasks.task(name, row.task)
                    if task is not None:
                        specs[row.task] = task.spec
                told.append(merger_prompt(agent, found, kept, skipped, specs))
        return "\n\n".join(told)

    async def _standing_merge(self, agent: stored.Agent) -> stored.Task | None:
        """The merger's standing task (28.7): a `merge` task of its own, made once and never
        finished, so its sessions always have something to be on."""
        return await self._standing(
            agent, task_kind="merge", feature="merging", title=f"{agent.name}'s merge queue"
        )

    async def _told(self, agent: stored.Agent, news: str) -> None:
        """Remember what the manager was last told, so the same news is not a turn again."""
        await stored.AgentRecords(self._records).save_agent(
            agent.model_copy(update={"last_news": news})
        )

    async def _standing(
        self,
        agent: stored.Agent,
        *,
        task_kind: TaskKind = "review",
        feature: str = "managing",
        title: str = "",
    ) -> stored.Task | None:
        """The manager's standing task (27.8), or the merger's (28.7): a task of its own of
        `task_kind`, made once and never finished, so its sessions always have something to be
        on."""
        tasks = stored.TaskRecords(self._records)
        # (One of the manager's own projects: its `projects` when it names them, else any that
        # udl has — and none at all means there is nothing to manage, 27.8.)
        project = (await self._projects_of(agent) or [""])[0]
        if not project:
            return None  # (no project, nothing to manage)
        for one in await tasks.tasks(project):
            if one.task_kind == task_kind and one.assignee == agent.name:
                return one
        features = stored.FeatureRecords(self._records)
        if await features.feature(project, feature) is None:
            await features.save_feature(
                stored.Feature(project=project, key=feature, title="Managing")
            )
        key = await self._next_task_key(tasks, project, feature)
        made = stored.Task(
            project=project,
            feature=feature,
            key=key,
            title=title or f"{agent.name}'s reports",
            task_kind=task_kind,
            assignee=agent.name,
        )
        await tasks.save_task(made)
        return await tasks.move(made, QUEUED, by=_system("assign"))

    async def _next_task_key(self, tasks: stored.TaskRecords, project: str, feature: str) -> str:
        """The next key of `feature`'s tasks (`<feature>.<n>`): the feature's counter, past any
        key a project already has."""
        counters = stored.Counters(self._records)
        for _ in range(100):
            key = f"{feature}.{await counters.claim(project, f'{feature}.')}"
            if await tasks.task(project, key) is None:
                return key
        raise PluginError(f"No key is free for a task of {feature!r} in {project!r}")

    async def _needs(self, agent: stored.Agent, reports: Sequence[str]) -> list[str]:
        """What `agent`'s reports need, as the lines its turn is told (27.8): a report with
        nothing to do (`assign`), a question asked of it (`answer`), and a report's pull that is
        ready and not reviewed for its head (`review`)."""
        powers = agent.manages
        tasks = stored.TaskRecords(self._records)
        sessions = stored.SessionRecords(self._records)
        questions = stored.QuestionRecords(self._records)
        found: list[str] = []
        for name in reports:
            if powers.assign and await sessions.current(name) is None:
                waiting = [
                    one
                    for project in await self._projects_of_any()
                    for one in await queues.queue_of(tasks, project=project, agent=name)
                ]
                if not waiting:
                    found.append(
                        f"assign: {name} has nothing to do — its queue is empty. Suggest a task "
                        f"(`orch_suggest`), then assign it (`orch_assign`, agent {name})."
                    )
            if powers.review:
                for project in await self._projects_of_any():
                    for one in await tasks.tasks(project):
                        if one.assignee != name or one.state not in (IN_PROGRESS, READY):
                            continue
                        try:
                            ready = await self._service.call("orch_readiness", {"key": one.key})
                        except PluginError, UDLError:
                            continue
                        if not isinstance(ready, Readiness) or ready.ready:
                            continue
                        # (What it waits on is its manager's OK for this head: nothing else, or
                        # the report could not act on it anyway, 27.8.)
                        if not any(one.startswith("its manager ") for one in ready.blockers):
                            continue
                        found.append(
                            f"review: {name}'s {one.key} ({one.title}) has a pull its manager has "
                            f"not reviewed for this head (project {project}). Read the diff "
                            f"against the task's spec, then `orch_review_task` with `approve` or "
                            f"`changes` and the reasons."
                        )
        if powers.answer:
            for project in await self._projects_of_any():
                for one in await questions.questions(
                    project, state=QuestionState.OPEN, to=agent.name
                ):
                    found.append(
                        f'answer: {one.asked_by} asked {one.key} "{one.title}" about '
                        f"{project}:{one.task} (project {project}). Answer it (`orch_answer`) only "
                        f"where the spec or a recorded decision covers it, and say which; else "
                        f"escalate it (`orch_escalate`)."
                    )
        return found

    async def _reaching(self, agent: stored.Agent) -> None:
        """Check `agent`'s host, then wait: an agent whose host does not answer is `offline`, so no
        session is started there until udl is restarted and finds it up again (the card's 6)."""
        await self._reachable(agent)
        await self._stop.wait()

    async def _reachable(self, agent: stored.Agent) -> bool:
        """Whether `agent`'s host answers: an agent whose host does not is `offline` (and no
        session is started there) until it does again (the card's 6)."""
        agents = stored.AgentRecords(self._records)
        try:
            checked = await self._service.check_host(HostRequest(host=agent.host))
        except (PluginError, UDLError) as error:
            log.warning("checking %s's host %s: %s", agent.name, agent.host, error)
            if not agent.offline:
                await agents.save_agent(agent.model_copy(update={"offline": True}))
            return False
        if checked.ok:
            if agent.offline:
                await agents.save_agent(agent.model_copy(update={"offline": False}))
            return True
        if not agent.offline:
            log.warning("%s's host %s does not answer: it is offline", agent.name, agent.host)
            await agents.save_agent(agent.model_copy(update={"offline": True}))
        return False

    async def _ready_again(self, session: stored.AgentSession) -> stored.AgentSession | None:
        """`session` if it may be dispatched to again, or None: a `limited` one whose quota is
        not back waits, and a `failed` one waits for a person (the card's 6)."""
        if session.state is SessionState.LIMITED:
            if session.limited_until is None or session.limited_until > self._clock.now():
                task = await self._task_of(session)
                if task is not None:
                    # (its harness is out of quota: a `limit` wait, so the time it spent waiting
                    # for the quota is not counted as its work, 27.10.)
                    await stored.WaitRecords(self._records).opening(
                        task.id, "limit", at=self._clock.now()
                    )
                return None
            task = await self._task_of(session)
            if task is not None:
                await stored.WaitRecords(self._records).closing(task.id, at=self._clock.now())
            self._harnesses.limited(await self._host_of(session), session.ref.runner, None)
            return await stored.SessionRecords(self._records).save_session(
                session.model_copy(update={"state": SessionState.IDLE, "limited_until": None})
            )
        if session.state is SessionState.FAILED:
            return None
        return session

    async def _host_of(self, session: stored.AgentSession) -> str:
        """The host a session's agent runs on."""
        agent = await stored.AgentRecords(self._records).agent(session.agent)
        return agent.host if agent is not None else "local"

    async def _pick(self, agent: stored.Agent) -> stored.Task | None:
        """The task the agent is on: its first `in_progress` one, else the first on its queue
        whose `needs` are done (the card's 1)."""
        tasks = stored.TaskRecords(self._records)
        projects = await self._projects_of(agent)
        working: list[stored.Task] = []
        for project in projects:  # (an agent works one task at a time, in any of its projects)
            working.extend(await tasks.tasks(project, assignee=agent.name, state=IN_PROGRESS))
        if working:
            return sorted(
                working, key=lambda one: (one.position is None, one.position or 0, one.key)
            )[0]
        for project in projects:
            for one in await queues.queue_of(tasks, project=project, agent=agent.name):
                # (A manager's standing task is not work to pick: its turn is `_managing`'s, and
                # picking it would move it to `in_progress` and leave it there, 27.8.)
                if one.task_kind in ("review", "merge"):
                    continue
                if await self._allowed(tasks, one):
                    return one
        return None

    async def _projects_of(self, agent: stored.Agent) -> list[str]:
        """The projects an agent works: its own, or every one when it names none."""
        if agent.projects:
            return list(agent.projects)
        return [one.name for one in await stored.ProjectRecords(self._records).projects()]

    async def _allowed(self, tasks: stored.TaskRecords, task: stored.Task) -> bool:
        """Whether every task `task` needs is done, merged or cancelled."""
        for key in task.needs:
            needed = await tasks.task(task.project, key)
            if needed is None or needed.state not in (DONE, MERGED, CANCELLED):
                return False
        return True

    async def _started(self, agent: stored.Agent, task: stored.Task) -> stored.Task:
        """`task` with its worktree made, and moved to `in_progress` if it was only queued."""
        if task.state != QUEUED:
            return task
        tasks = stored.TaskRecords(self._records, clock=self._clock)
        made = await self._prepare(task, agent)
        await tasks.save_task(made)
        # (The agent starts it, not udl: its history is what its model's rate is learned from
        # — a stretch credited to `udl` would teach nobody anything, 27.10.)
        return await tasks.move(made, IN_PROGRESS, by=_starting(agent))

    async def _prepared(self, task: stored.Task, agent: stored.Agent) -> stored.Task:
        """The task's worktree on its agent's host (27.5's `prepare()`), saved on the task."""
        if task.worktree:
            return task
        repo = await self._repo_of(task)
        host = await self._service.hosts.get(agent.host)
        found = await space.prepare(
            task,
            repo=repo,
            host=host,
            shell=await self._service.hosts.shell(host.name),
        )
        return task.model_copy(update={"branch": found.branch, "worktree": found.path})

    async def _repo_of(self, task: stored.Task) -> stored.Repo:
        """The repo a task's work is in: the one it names, or the project's only one."""
        repos = stored.RepoRecords(self._records)
        if task.repo:
            found = await repos.repo(task.project, task.repo)
            if found is None:
                raise DoesNotExist(f"{task.project!r} has no repo called {task.repo!r}")
            return found
        every = await repos.repos(task.project)
        if len(every) == 1:
            return every[0]
        raise PluginError(f"Which repo? {task.project!r} has {len(every)}: name the task's repo")

    async def _session_for(
        self, agent: stored.Agent, task: stored.Task
    ) -> stored.AgentSession | None:
        """The agent's session on `task`: the one it is in for that task, or a new one (one
        session per agent and task, the card's 2). None when it cannot be started."""
        sessions = stored.SessionRecords(self._records)
        current = await sessions.current(agent.name)
        if (
            current is not None
            and current.task == task.key
            and current.state
            not in (
                SessionState.ENDED,
                SessionState.FAILED,
            )
        ):
            return current
        if current is not None:
            await self._end(agent, current)
        project = await self._project_of(task)
        spec = SessionSpec(
            cwd=task.worktree or "",
            model=agent.model or None,
            instructions=instructions(project, agent),
            mcp=await self._mcp(agent),
            deny=list(self._settings.deny),
        )
        runner = await self._harnesses.runner(agent.host, agent.runner)
        ref = await runner.start(spec)
        self._harnesses.started(agent.host, agent.runner, ref.id)
        made = await sessions.save_session(
            stored.AgentSession(agent=agent.name, project=task.project, task=task.key, ref=ref)
        )
        await self._live(
            "orch.session.started",
            {
                "agent": made.agent,
                "session": str(made.id),
                "task": made.task,
                "host": agent.host,
                "runner": agent.runner,
            },
            dedupe=str(made.id),
        )
        return made

    async def _mcp(self, agent: stored.Agent) -> McpEndpoint:
        """The MCP endpoint a session is given: udl's own, with the agent's token — `hub_url` when
        udl is told where agents on other hosts reach it, else this machine's API (a session with
        no endpoint could call nothing, and so never finish a task).

        The token is issued once per agent and kept: issuing one rotates it, so a second session
        of the same agent would otherwise take the first one's away."""
        hub = self._settings.hub_url
        if hub is None:
            api = self._service.settings.api
            # (a wildcard bind is not an address to call: a session on this machine calls the
            # loopback, and one on another host is told `hub_url`)
            host = "127.0.0.1" if api.host in ("0.0.0.0", "::") else api.host  # noqa: S104
            hub = f"http://{_address(host)}:{api.port}/mcp"
        token = self._tokens.get(agent.name)
        if token is None:
            token = await self._token_of(agent.name)
            self._tokens[agent.name] = token
        return McpEndpoint(url=hub, token=token)

    async def _token_of(self, agent: str) -> SecretStr:
        """The agent's MCP token: the one udl keeps in its secret store, or a new one when it has
        none (issuing rotates, so one that is stored is never issued again).

        The store is where 27.2's `orch_issue_token` leaves it, so a token a person issued is the
        one its sessions get, across restarts."""
        stored = await self._service.secrets.get(secret_ref(agent))
        if stored is not None:
            return stored
        return SecretStr(await issue_token(self._service, agent))

    async def _project_of(self, task: stored.Task) -> stored.Project:
        """The project `task` is in."""
        found = await stored.ProjectRecords(self._records).project(task.project)
        if found is None:
            raise DoesNotExist(f"There is no project called {task.project!r}")
        return found

    # -- a task's working session --------------------------------------------------------------

    async def _work(
        self, agent: stored.Agent, task: stored.Task, session: stored.AgentSession, *, resume: bool
    ) -> bool:
        """Work a task until it moves on, the session is limited or failed, or it stalls: the
        first turn says which task it is (or that udl restarted), and each one after it what
        changed — or, with nothing new, a nudge.

        True when a manager's turn ended done: the harness heard the news and answered it. A
        turn that never started, or that ended limited or failed, is False, so its news is not
        counted as told."""
        sessions = stored.SessionRecords(self._records)
        prompt: str
        kind = "changed"
        if task.task_kind == "review":
            # (A manager's standing task is never finished: one turn per batch of news, then the
            # session is ended and the task waits for the next batch, 27.8.)
            reports = [
                one.name
                for one in await managers.reports(stored.AgentRecords(self._records), agent.name)
            ]
            items = await self._needs(agent, reports)
            ran = await self._turn(agent, session, manager_prompt(agent, reports, items))
            if ran is None:
                return False  # (another holder had its turn: nothing told, nothing failed)
            outcome = ran.outcome
            session = await sessions.session(session.id) or session
            if outcome is Outcome.STOPPED:
                return False  # (a person stopped it: its news was not told)
            if outcome is Outcome.RETRY:
                await self._retried(agent, session, session.last_error)
            await self._end(agent, session)
            # (Only a turn that ended done carried the news: a limit or a failure said
            # something, but the manager did not hear what it was to, 27.8.)
            return outcome is Outcome.DONE
        telling = Telling()
        if resume:
            said = await self._answers(task, session, telling)
            base = "udl restarted; continue where you were."
            prompt = f"{base}\n\n{said}" if said else base
        elif session.turns == 0:
            prompt = first_prompt(task, answers=await self._answers(task, session, telling))
        else:
            next_prompt, session, kind, waiting = await self._next_prompt(task, session, telling)
            if next_prompt is None:
                # (its pull is only waiting on time: the event wakes it, and how long it waited
                # is a `Wait` — 27.10's, so a checks' hour is not the agent's work.)
                await stored.WaitRecords(self._records).opening(
                    task.id, waiting or "ci", at=self._clock.now()
                )
                return True
            prompt = next_prompt
        spoke = False
        while True:
            began = self._clock.now()  # (the wait ended when the turn began, not when it ended)
            ran = await self._turn(agent, session, prompt)
            if ran is None:
                return spoke  # (another holder had its turn: nothing told, not this one's to fail)
            if ran.started:
                await self._wrote(telling, session)
                await stored.WaitRecords(self._records).closing(task.id, at=began)
            telling = Telling()
            outcome, spoke = ran.outcome, ran.spoke
            session = await sessions.session(session.id) or session
            if outcome is Outcome.STOPPED:
                return spoke  # (a person stopped it: it stays ended)
            if outcome in (Outcome.LIMITED, Outcome.FAILED):
                return spoke
            if outcome is Outcome.RETRY:
                if not await self._retried(agent, session, session.last_error):
                    return spoke
                session = await sessions.session(session.id) or session
                continue
            now = await self._task_of(session)
            if now is None or now.state != IN_PROGRESS:
                await self._end(agent, session)
                return spoke
            # (`nudges` counts the turns that brought nothing new: a turn that told the session
            # what changed is progress, and starts the count again.)
            nudges = 0 if kind == "changed" else session.nudges + 1
            session = await sessions.save_session(session.model_copy(update={"nudges": nudges}))
            if nudges >= agent.max_nudges:
                await self._stalled(agent, now, session)
                return spoke
            next_prompt, session, kind, waiting = await self._next_prompt(now, session, telling)
            if next_prompt is None:
                # (only time stands in the way: no turn until the pull changes)
                await stored.WaitRecords(self._records).opening(
                    now.id, waiting or "ci", at=self._clock.now()
                )
                return spoke
            prompt = next_prompt

    async def _answers(
        self, task: stored.Task, session: stored.AgentSession, telling: Telling
    ) -> str:
        """What `session` is told of the answers to its own questions, to be marked told once the
        turn carrying them has run: an answer reaches a session once, not on every prompt
        (28.1)."""
        agent = session.agent or ""
        if not agent:
            return ""
        found = await stored.QuestionRecords(self._records).questions(
            task.project, state=QuestionState.ANSWERED, to=None, task=task.key
        )
        fresh = [one for one in found if one.answer and one.asked_by == agent and not one.delivered]
        if not fresh:
            return ""
        telling.questions.extend(fresh)
        return answered(fresh)

    async def _next_prompt(
        self, task: stored.Task, session: stored.AgentSession, telling: Telling
    ) -> tuple[str | None, stored.AgentSession, str, WaitKind | None]:
        """What a session still on its task is told — what changed since the last turn (its pull's
        checks, the review bot's open items), or with nothing new a nudge (the card's 3) — the
        session, remembering what it was told so that the same news is not told twice, and which
        of the two it was (`changed`, or `nudge`: what tells progress from a nudge).

        None means the session holds no turn: its pull is only waiting on time (the checks
        running, the bot not done with this head), and a `github.pr.*` event wakes it (the card's
        5)."""
        said = await self._answers(task, session, telling)
        what, session, waiting = await self._what_changed(task, session)
        if said and what is not None:
            what = f"{said}\n\n{what}"
        elif said:
            what = said
        if waiting and not said:
            return None, session, "waiting", waiting
        if what is None or what == session.last_text:
            return nudge(task), session, "nudge", None
        session = session.model_copy(update={"last_text": what})
        telling.last_text = what  # (written when the turn has run: not before)
        return changed(task, what), session, "changed", None

    async def _wrote(self, telling: Telling, session: stored.AgentSession) -> None:
        """Write what a prompt told, now that its turn has run: the answers as delivered, and the
        session's memory of what it was told — onto the session as it is stored now, never the
        copy taken before the turn (its turn number, its tokens and cost, and the state it ended
        in are the turn's), and not at all onto a session that has ended."""
        if telling.questions:
            await self._records.save_all(
                [one.model_copy(update={"delivered": True}) for one in telling.questions]
            )
        if not telling.last_text:
            return
        sessions = stored.SessionRecords(self._records)
        now = await sessions.session(session.id)
        if now is not None and now.state is not SessionState.ENDED:
            await sessions.save_session(now.model_copy(update={"last_text": telling.last_text}))

    async def _what_changed(
        self, task: stored.Task, session: stored.AgentSession
    ) -> tuple[str | None, stored.AgentSession, WaitKind | None]:
        """What is new about `task`'s pull, as a line a session can act on (its checks and the
        bot's open items, or that it may be merged), the session, and — when it is only time that
        stands in the way — which kind of wait that is (27.10's `Wait`: `ci` while its checks
        run, `bot` while the review bot has not answered this head)."""
        links = await stored.LinkRecords(self._records).for_owner(
            task.project, OwnerKind.TASK, task.key
        )
        if not any(one.link_kind == "pull" for one in links):
            return "it has no pull yet", session, None
        try:
            found = await self._service.call("orch_readiness", {"key": task.key})
        except (PluginError, UDLError) as error:
            return f"its pull could not be read:\n{fenced('udl', str(error))}", session, None
        if not isinstance(found, Readiness):
            return None, session, None
        if found.ready:
            return "its pull is ready to merge: say so with `orch_ready`", session, None
        if found.blockers and all(one.startswith("its manager ") for one in found.blockers):
            # (Its manager has not reviewed this head: nothing the session can do about it, so it
            # holds no turn, as it does while only time stands in the way, 27.8 — and the wait is
            # the review's, which is the bot's, 27.10.)
            return None, session, "bot"
        if found.pending and set(found.pending) == set(found.blockers):
            # (Only time stands in the way, and the rerun that would hurry it needs an operation
            # udl lacks: the session holds no turn, and 28.1's question asks how long to wait.)
            agent = session.agent or ""
            if agent:
                await self._ask_once(
                    agent,
                    task,
                    title="The bot has not answered: how long to wait?",
                    text=(
                        "Its pull's checks and review are still to come, and udl cannot rerun "
                        "them while they are green. Say how long to wait, or whether to go on "
                        "without them."
                    ),
                    blocking=False,
                )
            return None, session, _waiting_kind(found.pending_kinds)
        parts = [f"its pull is not ready ({'; '.join(found.blockers)})"]
        if found.open_items:
            items = "\n".join(
                f"{one.text} ({one.status})" if one.status else one.text for one in found.open_items
            )
            parts.append(f"the review bot still lists:\n\n{fenced('review-bot', items)}")
        return ". ".join(parts), session, None

    async def _stalled(
        self, agent: stored.Agent, task: stored.Task, session: stored.AgentSession
    ) -> None:
        """A task still going after its nudges: `blocked` on a question ("agent stalled"), and
        the session ended so the loop takes the next one.

        The question is 28.1's: the stall is what an agent cannot answer itself (was it told to
        go on, or is the work bigger than the task said?), so it goes to its manager, or to the
        humans (27.7 left this here, and 28.1 builds it)."""
        why = f"agent stalled: {agent.name} took {session.nudges} nudges without progress"
        await self._ask_once(agent.name, task, title=why, text=why, blocking=True)
        await self._end(agent, session)

    async def _ask(
        self,
        name: str,
        task: stored.Task,
        *,
        title: str,
        text: str,
        blocking: bool,
    ) -> stored.Question | None:
        """Ask a question in `task` on `name`'s behalf (28.1): to its manager when that manager
        may answer, and to the humans otherwise. A blocking one blocks the task."""
        try:
            return await asked.ask(
                stored.QuestionRecords(self._records),
                project=task.project,
                task=task.key,
                title=title,
                text=text,
                blocking=blocking,
                asked_by=name,
                clock=self._clock,
            )
        except UDLError:
            # (A blocking question is the task's state: dropping it would leave the task running
            # and the loop nudging it for good, so it is raised and the tick tries again. The
            # bot's wait is best-effort: nothing is blocked by it.)
            if blocking:
                raise
            log.warning("could not ask %r's question", task.key, exc_info=True)
            return None

    async def _ask_once(
        self,
        name: str,
        task: stored.Task,
        *,
        title: str,
        text: str,
        blocking: bool,
    ) -> None:
        """`_ask()`, with the task's one question at a time kept:
        - a blocking ask blocks the task on the open question it already has (a stall is a stall
          however the question got there), and asks nothing new;
        - the bot's wait is asked once per task, whatever became of the question after it (a wait
          re-read every tick would otherwise ask again as soon as it was answered)."""
        questions = stored.QuestionRecords(self._records)
        if blocking:
            open_ones = await questions.questions(task.project, to=None, task=task.key)
            if open_ones:
                await asked.block_with(
                    questions,
                    project=task.project,
                    task=task.key,
                    question=open_ones[0],
                    asked_by=name,
                    why=title,
                    clock=self._clock,
                )
                return
            if task.state == BLOCKED:
                # (A person blocked it, or its question was closed: it waits already, so nothing
                # is asked — but why it is waiting now is written where its history is.)
                await stored.NoteRecords(self._records).system(OwnerKind.TASK, task.id, title)
                return
        else:
            ever = await questions.questions(task.project, state=None, to=None, task=task.key)
            if any(one.title == title for one in ever):
                return
        await self._ask(name, task, title=title, text=text, blocking=blocking)

    async def _turn(
        self, agent: stored.Agent, session: stored.AgentSession, prompt: str
    ) -> Ran | None:
        """Send one turn: `Turns.one`, the same turn `orch_send` makes (27.9), waiting for the
        session's lease (the loop has a turn to make, so it waits rather than skip it).

        None when another holder had the session's turn for longer than `TURN_WAIT`: that turn is
        not this one's, so nothing is said, nothing failed, and the loop comes back to it — a
        session another process is turning is not a session this one may fail, end or count."""
        return await self._turns_made.one(agent, session, prompt)

    async def _retried(self, agent: stored.Agent, session: stored.AgentSession, why: str) -> bool:
        """A turn that failed: retried after a backoff, and after `FAILURES` tries the session is
        `failed` and its task waits for a person (the card's 6). True when it may be tried again.

        The wait is the loop's, not a turn's: a retry does not hold one of the `max_agents_running`
        places while it waits."""
        sessions = stored.SessionRecords(self._records)
        current = await sessions.session(session.id)
        if current is None or current.state is SessionState.ENDED:
            return False  # (a person stopped it: it is not retried)
        session = current
        failures = session.failures + 1
        if failures < FAILURES:
            await sessions.save_session(
                session.model_copy(update={"failures": failures, "state": SessionState.IDLE})
            )
            await self._clock.sleep(BACKOFF * failures)
            return True
        session = await sessions.save_session(
            session.model_copy(update={"failures": failures, "state": SessionState.FAILED})
        )
        await self._live(
            "orch.session.failed",
            {
                "agent": session.agent,
                "session": str(session.id),
                "task": session.task,
                "why": why,
            },
            dedupe=f"{session.id}:{session.turns}",
        )
        task = await self._task_of(session)
        if task is not None:
            # (A failed session is what only a person can look into: 28.1's question, blocking.)
            said = f"{agent.name}'s session failed: {why}"
            await self._ask_once(agent.name, task, title=said, text=said, blocking=True)
        await self._end(agent, session)
        return False

    async def _task_of(self, session: stored.AgentSession) -> stored.Task | None:
        """The task a session is on, or None when there is none (it was deleted).

        Its key is looked up in the session's own project: two projects may each have a task
        `1.1`, and a session is never moved to the other one's."""
        if not session.task:
            return None
        tasks = stored.TaskRecords(self._records)
        projects = [session.project] if session.project else await self._projects_of_any()
        for project in projects:
            found = await tasks.task(project, session.task)
            if found is not None:
                return found
        return None

    async def _projects_of_any(self) -> list[str]:
        """Every project udl has, by name."""
        return [one.name for one in await stored.ProjectRecords(self._records).projects()]

    async def _end(self, agent: stored.Agent, session: stored.AgentSession) -> None:
        """A session that is done with its task: ended, so the loop takes the next one (its
        harness is told too, so nothing it was doing goes on)."""
        sessions = stored.SessionRecords(self._records)
        if session.state is not SessionState.ENDED:
            ended = await sessions.save_session(
                session.model_copy(update={"state": SessionState.ENDED})
            )
            with contextlib.suppress(PluginError, UDLError):
                runner = await self._harnesses.runner(agent.host, agent.runner)
                await runner.stop(session.ref)
            await self._live(
                "orch.session.ended",
                {
                    "agent": ended.agent,
                    "session": str(ended.id),
                    "task": ended.task,
                    "state": str(session.state),
                },
                dedupe=f"{ended.id}:{ended.turns}",
            )
        self._harnesses.ended(agent.host, agent.runner, session.ref.id)

    async def _live(self, event_type: str, data: Mapping[str, object], *, dedupe: str) -> None:
        """Publish one of the session's live events (publish: one that cannot be is logged)."""
        await publish(self._events, event_type, data, dedupe=dedupe)


# ---------------------------------------------------------------------------------------------
# One turn (the loop's, and a person's typed one)
# ---------------------------------------------------------------------------------------------

_LOCKS: Final[WeakValueDictionary[tuple[int, UUID], asyncio.Lock]] = WeakValueDictionary()
"""Each session's turn lock, per event loop (a lock belongs to the loop that first awaited it,
and a process may run more than one): the dispatcher's loop and a person's typed turn
(`orch_send`, 27.9) take the same one, so two turns are never in one session at once.

It is weak: a turn holds its lock while it runs (and a turn waiting for it holds it too), so two
turns of one session always meet on one lock, and a session nobody is turning leaves none behind
— the table does not grow with every session the process has ever sent a turn to."""


def turn_lock(session: UUID) -> asyncio.Lock:
    """The lock a session's turns share in this process, one per event loop (a lock belongs to
    the loop that first awaited it, and a process may run more than one).

    It is weak: a turn holds its lock while it runs, and a turn waiting for it holds it too, so
    two turns of one session always meet on one lock, while a session nobody is turning leaves
    none behind — the table does not grow with every session the process has ever turned."""
    return _LOCKS.setdefault((id(asyncio.get_running_loop()), session), asyncio.Lock())


TURN_TTL: Final = timedelta(minutes=5)
"""How long a turn's lease holds without a renewal (a turn renews it while it runs)."""

TURN_WAIT: Final = timedelta(minutes=10)
"""How long a turn waits for another holder's before it gives up: a turn may be long, but the
loop comes back to it, and a wait with no end is a hang."""


class TurnLease:
    """One session's turn, held across processes (27.9): a `Lease` named `orch-turn:<session>`,
    the way a gate's lane is `orch-gate:<host>` (27.5), so a person's typed turn (in the CLI's
    process) and the loop's (in `udl serve`) never run one session at once.

    `held()` takes it — waiting for it, or not — and gives it back however the turn ends, renewing
    it while the turn runs: a process that dies mid-turn lets the next one in when its lease
    expires, so a restart's turn still starts."""

    RETRY: Final = timedelta(seconds=2)
    """How long to wait before asking for the turn again (a turn is minutes long: twice a second
    would be noise on the database)."""

    def __init__(
        self,
        database: DBManager,
        *,
        session: UUID,
        holder: str,
        clock: Clock,
        ttl: timedelta = TURN_TTL,
    ) -> None:
        self._db = database
        self._name = f"orch-turn:{session}"
        self._holder = holder
        self._clock = clock
        self._ttl = ttl

    @property
    def name(self) -> str:
        """The lease it takes (what a session's turn is, when it is looked for)."""
        return self._name

    async def claim(self) -> bool:
        """Take the lease if nobody else holds it (or the holder's lease has expired)."""
        return await self._db.claim_lease(self._name, self._holder, self._ttl) is not None

    async def take(self, *, within: timedelta = TURN_WAIT) -> bool:
        """Wait up to `within` for the lease, and say whether it is this holder's (a turn that
        gives up is one the loop comes back to, not one that hangs)."""
        deadline = self._clock.now() + within
        while not await self.claim():
            if self._clock.now() >= deadline:
                return False
            await pause(self._clock, self.RETRY)
        return True

    async def release(self) -> None:
        """Give the lease back (nothing when it is not the holder's any more)."""
        await self._db.release_lease(self._name, self._holder)

    async def _renew(self) -> None:
        """Keep the lease while the turn runs: a turn may take longer than its ttl, and a lease
        that ran out is one another turn can take.

        Its own end is not the turn's: it is cancelled when the turn is done, and it says so
        itself rather than leaving a `CancelledError` for the turn to swallow (which would swallow
        the turn's own cancellation too)."""
        every = self._ttl / 3
        try:
            while True:
                await pause(self._clock, every)
                await self.claim()
        except asyncio.CancelledError:
            return

    @asynccontextmanager
    async def held(self, *, wait: bool = True) -> AsyncGenerator[bool]:
        """The lease, for as long as the block runs: yielded False when another holder has it and
        this turn will not wait for it."""
        if wait:
            if not await self.take():
                yield False
                return
        elif not await self.claim():
            yield False
            return
        keeping = asyncio.create_task(self._renew())
        try:
            yield True
        finally:
            keeping.cancel()
            try:
                # (`return_exceptions`, so a renewal cancelled before it ever ran is not this
                # turn's `CancelledError` — while this turn's own cancellation still is.)
                await asyncio.gather(keeping, return_exceptions=True)
            finally:
                await self.release()


@dataclass(frozen=True, slots=True)
class Ran:
    """What a turn did: how it ended, whether it said anything at all, the turn it ran as (read
    under the lock, not from the caller's snapshot), and whether it started — a turn that never
    started (the session was gone or ended, or another holder kept its lease) told nothing."""

    outcome: Outcome
    spoke: bool
    turn: int
    started: bool = True


@dataclass(slots=True)
class Telling:
    """What a prompt says it told, written once its turn has run rather than when the prompt is
    built: a turn that never starts — another process had the session's turn — told nothing, so
    an answer it carried is still to be delivered and its news is still new (27.9)."""

    questions: list[stored.Question] = field(default_factory=list[stored.Question])
    """The answers it carries, to be marked delivered."""

    last_text: str = ""
    """What the session is to remember it was told (empty when the prompt said nothing new)."""


class Turns:
    """A session's turns, as the dispatcher's loop runs them (27.7): the numbering, the
    transcript and the live events, what a turn cost, and what its end means.

    `orch_send` (27.9) runs a person's typed turn through this too, so it is the same turn the
    loop makes — and a turn never overwrites a session a person ended while it ran."""

    def __init__(
        self,
        *,
        records: AppRecords,
        events: AppEvents,
        harnesses: Harnesses,
        clock: Clock,
        settings: OrchSettings,
        place: asyncio.Semaphore | None = None,
        database: DBManager | None = None,
    ) -> None:
        self._records = records
        self._events = events
        self._harnesses = harnesses
        self._clock = clock
        self._settings = settings
        self._place = place
        """The dispatcher's `max_agents_running` places; None for a turn nobody queued."""
        self._database = database
        """Where a turn's cross-process lease is kept (None for a `Turns` of tests that do not
        need one: a turn in one process only)."""
        self._holder = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        """Who holds this `Turns`' turns: unique to the process and to it, so a second `Turns` in
        one process is a second holder — as another process's is — and the lease tells them
        apart."""

    async def one(
        self, agent: stored.Agent, session: stored.AgentSession, prompt: str, *, wait: bool = True
    ) -> Ran | None:
        """Send one turn and stream it: its events into the transcript and the live events, and
        what its end says into the session — and whether it said anything at all (a turn that
        never started said nothing).

        The session's turn is held twice over: by this process's lock, and by a lease across
        processes (27.9), so the loop's turn and a person's typed one never run one session at
        once. A turn that does not wait (`orch_send`) is None when another holder has it."""
        async with self._leased(session.id, wait=wait) as held:
            if not held:
                return None
            # (The lease first: a turn that will not wait is refused before it queues on this
            # process's own lock, where the holder may be a turn in this very process.)
            async with turn_lock(session.id):
                if self._place is None:
                    return await self._one_turn(agent, session, prompt)
                async with self._place:  # (at most `max_agents_running` turns at once)
                    return await self._one_turn(agent, session, prompt)

    @asynccontextmanager
    async def _leased(self, session: UUID, *, wait: bool) -> AsyncGenerator[bool]:
        """The session's turn, held across processes for as long as the block runs (yielded False
        when another holder has it and this turn will not wait)."""
        if self._database is None:
            yield True
            return
        lease = TurnLease(self._database, session=session, holder=self._holder, clock=self._clock)
        async with lease.held(wait=wait) as held:
            yield held

    async def _one_turn(
        self, agent: stored.Agent, session: stored.AgentSession, prompt: str
    ) -> Ran:
        """One turn, streamed (what `one` does once it has the session and its place).

        The session is read again here, under the lock: a turn that ended it (or a person who
        stopped it) while this one waited leaves it `ENDED`, and this turn does not start — and
        the number it runs as is the one it read, not the caller's snapshot."""
        sessions = stored.SessionRecords(self._records)
        current = await sessions.session(session.id)
        if current is None:
            return Ran(Outcome.STOPPED, False, session.turns, False)  # (gone: nothing to say)
        if current.state is SessionState.ENDED:
            return Ran(Outcome.STOPPED, False, current.turns, False)
        # (The lock and the lease are what say a turn is already in it, in this process and in
        # another; the stored state says it too — `orch_send` refuses a RUNNING session. A record
        # that still says RUNNING after a restart is a turn that never ended, and this one takes
        # it over.)
        session = await sessions.save_session(
            current.model_copy(update={"state": SessionState.RUNNING})
        )
        runner = await self._harnesses.runner(agent.host, agent.runner)
        ended: TurnEnded | None = None
        limited: RunnerLimited | None = None
        failed: RunnerFailed | None = None
        spoke = False
        try:
            async for said in runner.send(session.ref, prompt):
                event = an_event(said)
                spoke = True
                await self._said(agent, session, event)
                if isinstance(event, TurnEnded):
                    ended = event
                    break
                if isinstance(event, RunnerLimited):
                    limited = event
                elif isinstance(event, RunnerFailed):
                    failed = event
        except (PluginError, UDLError) as error:
            failed = RunnerFailed(message=str(error))
            ended = TurnEnded(reason=TurnReason.ERROR, text=str(error))
        outcome = await self._ended(agent, session, ended, limited=limited, failed=failed)
        return Ran(outcome, spoke, session.turns)

    async def _saved(
        self, session: stored.AgentSession, **changes: object
    ) -> stored.AgentSession | None:
        """The session saved with `changes`, from what is stored now — or None when a person
        ended it while the turn ran (a stop wins over what the turn was about to write)."""
        sessions = stored.SessionRecords(self._records)
        current = await sessions.session(session.id)
        if current is None or current.state is SessionState.ENDED:
            return None
        return await sessions.save_session(current.model_copy(update=changes))

    async def _said(
        self, agent: stored.Agent, session: stored.AgentSession, event: AnAgentEvent
    ) -> None:
        """One thing the runner said: its transcript record, its live event, and what it cost."""
        sessions = stored.SessionRecords(self._records)
        said = await sessions.save_event(
            stored.SessionEvent(
                agent=agent.name, session=session.id, turn=session.turns, event=event
            )
        )
        # (Its own record's id: two things said in one turn are two events, even when they say
        # the same, and the same thing said again after a restart is not the last run's repeat.)
        await self.live(
            "orch.session.event",
            {
                "agent": session.agent,
                "session": str(session.id),
                "task": session.task,
                "turn": session.turns,
                "kind": event.kind,
            },
            dedupe=str(said.id),
        )
        if isinstance(event, Usage):
            current = await sessions.session(session.id)
            if current is None or current.state is SessionState.ENDED:
                return
            await self._saved(
                current,
                tokens_in=current.tokens_in + event.input_tokens,
                tokens_out=current.tokens_out + event.output_tokens,
                cost=event.cost if event.cost is not None else current.cost,
            )

    async def _ended(
        self,
        agent: stored.Agent,
        session: stored.AgentSession,
        ended: TurnEnded | None,
        *,
        limited: RunnerLimited | None,
        failed: RunnerFailed | None,
    ) -> Outcome:
        """What a turn's end means for the session: whether the loop carries on with this
        session, or waits (a limit, a failure) and comes back — or a person ended it meanwhile,
        in which case it stays ended."""
        if limited is not None or (ended is not None and ended.reason is TurnReason.LIMITED):
            until = limited.until if limited is not None else None
            self._harnesses.limited(agent.host, agent.runner, until)
            current = await stored.SessionRecords(self._records).session(session.id)
            if current is None or current.state is SessionState.ENDED:
                return Outcome.STOPPED
            session = (
                await self._saved(
                    current,
                    state=SessionState.LIMITED,
                    limited_until=until,
                    limits=current.limits + 1,
                )
                or current
            )
            await self.live(
                "orch.agent.limited",
                {
                    "agent": session.agent,
                    "session": str(session.id),
                    "until": until.isoformat() if until is not None else None,
                },
                dedupe=f"{session.id}:{session.limits}",
            )
            return Outcome.LIMITED
        if failed is not None or (ended is not None and ended.reason is TurnReason.ERROR):
            why = (
                failed.message if failed is not None else (ended.text if ended is not None else "")
            )
            if await self._saved(session, last_error=why) is None:
                return Outcome.STOPPED
            return Outcome.RETRY
        if (
            await self._saved(
                session,
                state=SessionState.IDLE,
                turns=session.turns + 1,
                failures=0,
            )
            is None
        ):
            return Outcome.STOPPED
        return Outcome.DONE

    async def live(self, event_type: str, data: Mapping[str, object], *, dedupe: str) -> None:
        """Publish one of the session's live events (publish: one that cannot be is logged)."""
        await publish(self._events, event_type, data, dedupe=dedupe)


async def publish(
    events: AppEvents, event_type: str, data: Mapping[str, object], *, dedupe: str
) -> None:
    """Publish one of a session's live events, as its own type has it (one that cannot be
    published is logged, never fatal: the transcript is the record).

    Its dedupe key is something stored and unique (`dedupe`: a record's id, a session and its
    turn), never what the event says and never a count: the bus drops a repeat, so a key from
    what was said would drop two of the same thing in one turn, and a key that counted from 0
    would make this run's first events the last run's repeats."""
    try:
        payload: JsonObject = {name: _plain(value) for name, value in data.items()}
        await events.emit(event_type, payload, dedupe_key=f"{event_type}:{dedupe}")
    except (PluginError, UDLError) as error:
        log.warning("publishing %s: %s", event_type, error)


def _address(host: str) -> str:
    """`host` as a URL's host: an IPv6 address goes in brackets (`http://[::1]:8000`)."""
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def _plain(value: object) -> JsonValue:
    """`value` as JSON holds it."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _system(role: Role) -> Principal:
    """udl's own principal for a move it makes on its own account (never a person's, never an
    agent's: assigning a queued task to the agent that takes it is the queue's bookkeeping, while
    the start of the work is the agent's, `_starting`)."""
    return Principal(kind="system", role=role)


def _starting(agent: stored.Agent) -> Principal:
    """The principal a dispatcher's start is: the agent's own (it is the one who starts working),
    with the role the table calls it."""
    return Principal(kind="agent", name=agent.name, role="start")
