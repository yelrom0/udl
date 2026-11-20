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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Final, cast

# Package Imports
# Local Imports
# Local Imports
from pydantic import JsonValue, SecretStr

from udl.db.models import JsonObject
from udl.errors import DoesNotExist, PluginError, UDLError
from udl.plugins.apps.base import AppContext
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch import workspace as space
from udl.plugins.apps.orch.agents import AnAgentEvent, SessionState
from udl.plugins.apps.orch.models import (
    BLOCKED,
    CANCELLED,
    DONE,
    IN_PROGRESS,
    MERGED,
    QUEUED,
    OwnerKind,
    Principal,
    Readiness,
    Role,
)
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.apps.orch.tokens import issue_token
from udl.plugins.runners.base import (
    AgentEvent,
    McpEndpoint,
    RunnerFailed,
    RunnerLimited,
    SessionSpec,
    TurnEnded,
    TurnReason,
    Usage,
)
from udl.plugins.runners.manage import Harnesses

__all__ = ["RULES", "Dispatcher", "first_prompt", "instructions"]

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

type Preparing = Callable[[stored.Task, stored.Agent], Awaitable[stored.Task]]
"""What makes a task's worktree: the task, with its branch and path, or a refusal."""


def instructions(project: stored.Project, agent: stored.Agent) -> str:
    """What a session is told before it is given a task: udl's rules, the project's, and the
    agent's brief (the card's 2)."""
    parts = [RULES]
    if project.rules.strip():
        parts.append(f"# The project's rules ({project.name})\n\n{project.rules.strip()}")
    if agent.brief.strip():
        parts.append(f"# Your brief ({agent.name})\n\n{agent.brief.strip()}")
    return "\n\n".join(parts)


def first_prompt(task: stored.Task) -> str:
    """What a session is sent first: which task it is on, and where to read it (the card's 2)."""
    return (
        f"Your task is {task.key}: {task.title}.\n\n"
        "Call `orch_my_task` to read it — its spec, its feature's spec, the project's rules and "
        "its notes — then do it. Work test-first, push with `orch_push`, and say your work is "
        "done with `orch_ready` (or `orch_block`, with the reason, when it is not yours to "
        "settle)."
    )


def nudge(task: stored.Task) -> str:
    """What a session that is still on its task is told: carry on, and say where it is."""
    return (
        f"You are still on {task.key} ({task.title}). Say where it is with `orch_add_note`, and "
        "carry on: push with `orch_push`, and say it is done with `orch_ready`."
    )


def changed(task: stored.Task, what: str) -> str:
    """What a session that is still on its task is told when something did: what changed (the
    pull's checks, the bot's items), and what to do about it."""
    return (
        f"What changed on {task.key}: {what}\n\n"
        "Carry on: push with `orch_push`, and say it is done with `orch_ready`."
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
        self._harnesses = harnesses
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
        self._turns = asyncio.Semaphore(self._settings.max_agents_running)
        agents = [one for one in await stored.AgentRecords(self._records).agents() if one.enabled]
        if not agents:
            await self._stop.wait()
            return
        for agent in agents:
            self._wakes[agent.name] = asyncio.Event()
        loops = [asyncio.create_task(self._loop(agent)) for agent in agents]
        waking = asyncio.create_task(self._woken_by_events())
        stopping = asyncio.create_task(self._stop.wait())
        everything = [*loops, waking, stopping]
        try:
            done, _ = await asyncio.wait({*loops, stopping}, return_when=asyncio.FIRST_COMPLETED)
            if stopping not in done:
                for task in done:
                    if task.exception() is not None:
                        log.warning("an agent's loop ended: %r", task.exception())
        finally:
            for task in everything:
                task.cancel()
            for task in everything:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _woken_by_events(self) -> None:
        """Wake every agent's loop on each `orch.*` or `github.pr.*` event: what an agent waits
        on is its own task's pull going green, or an answer to a question."""
        async with (
            self._events.subscribe("orch.*") as orch,
            self._events.subscribe("github.pr.*") as pulls,
        ):
            await asyncio.gather(self._waking(orch), self._waking(pulls))

    async def _waking(self, subscription: AsyncIterator[object]) -> None:
        """Wake every loop on each event `subscription` gives."""
        async for _ in subscription:
            for wake in self._wakes.values():
                wake.set()

    async def _loop(self, agent: stored.Agent) -> None:
        """One agent's loop: do what its queue and its session say, then wait to be woken."""
        while not self._stop.is_set():
            try:
                await self._step(agent)
            except (PluginError, UDLError) as error:
                log.warning("dispatching to %s: %s", agent.name, error)
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
        task = await self._pick(agent)
        if task is None:
            return
        task = await self._started(agent, task)
        session = await self._session_for(agent, task)
        if session is None or session.state in (SessionState.FAILED, SessionState.LIMITED):
            return
        await self._work(agent, task, session, resume=False)

    async def _ready_again(self, session: stored.AgentSession) -> stored.AgentSession | None:
        """`session` if it may be dispatched to again, or None: a `limited` one whose quota is
        not back waits, and a `failed` one waits for a person (the card's 6)."""
        if session.state is SessionState.LIMITED:
            if session.limited_until is None or session.limited_until > self._clock.now():
                return None
            self._harnesses.limited(
                await self._host_of(session), await self._runner_of(session), None
            )
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

    async def _runner_of(self, session: stored.AgentSession) -> str:
        """The runner a session's agent runs."""
        return session.ref.runner

    async def _pick(self, agent: stored.Agent) -> stored.Task | None:
        """The task the agent is on: its first `in_progress` one, else the first on its queue
        whose `needs` are done (the card's 1)."""
        tasks = stored.TaskRecords(self._records)
        for project in await self._projects_of(agent):
            working = await tasks.tasks(project, assignee=agent.name, state=IN_PROGRESS)
            if working:
                return sorted(
                    working, key=lambda one: (one.position is None, one.position or 0, one.key)
                )[0]
            for one in await queues.queue_of(tasks, project=project, agent=agent.name):
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
        tasks = stored.TaskRecords(self._records)
        made = await self._prepare(task, agent)
        await tasks.save_task(made)
        return await tasks.move(made, IN_PROGRESS, by=_system("start"))

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
        if current is not None and current.task == task.key:
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
            stored.AgentSession(
                agent=agent.name, task=task.key, ref=ref, state=SessionState.RUNNING
            )
        )
        await self._live("orch.session.started", made, {"task": task.key})
        return made

    async def _mcp(self, agent: stored.Agent) -> McpEndpoint | None:
        """The MCP endpoint a session is given: udl's own, with the agent's token. None when udl
        has no `hub_url` (a session with nowhere to call has no tools to call it with)."""
        hub = self._settings.hub_url
        if hub is None:
            return None
        return McpEndpoint(url=hub, token=SecretStr(await issue_token(self._service, agent.name)))

    async def _project_of(self, task: stored.Task) -> stored.Project:
        """The project `task` is in."""
        found = await stored.ProjectRecords(self._records).project(task.project)
        if found is None:
            raise DoesNotExist(f"There is no project called {task.project!r}")
        return found

    # -- a task's working session --------------------------------------------------------------

    async def _work(
        self, agent: stored.Agent, task: stored.Task, session: stored.AgentSession, *, resume: bool
    ) -> None:
        """Work a task until it moves on, the session is limited or failed, or it stalls: the
        first turn says which task it is (or that udl restarted), and each one after it what
        changed — or, with nothing new, a nudge."""
        sessions = stored.SessionRecords(self._records)
        if resume:
            prompt = "udl restarted; continue where you were."
        elif session.turns == 0:
            prompt = first_prompt(task)
        else:
            prompt, session = await self._next_prompt(task, session)
        while True:
            if not await self._turn(agent, session, prompt):
                return
            session = await sessions.session(session.id) or session
            if session.state in (SessionState.LIMITED, SessionState.FAILED, SessionState.ENDED):
                return
            now = await self._task_of(session)
            if now is None or now.state != IN_PROGRESS:
                await self._end(agent, session)
                return
            session = await sessions.save_session(
                session.model_copy(update={"nudges": session.nudges + 1})
            )
            if session.nudges > agent.max_nudges:
                await self._stalled(agent, now, session)
                return
            prompt, session = await self._next_prompt(now, session)

    async def _next_prompt(
        self, task: stored.Task, session: stored.AgentSession
    ) -> tuple[str, stored.AgentSession]:
        """What a session still on its task is told — what changed since the last turn (its pull's
        checks, the review bot's open items), or with nothing new a nudge (the card's 3) — and the
        session, remembering what it was told so that the same news is not told twice."""
        what = await self._what_changed(task)
        if what is None or what == session.last_text:
            return nudge(task), session
        session = await stored.SessionRecords(self._records).save_session(
            session.model_copy(update={"last_text": what})
        )
        return changed(task, what), session

    async def _what_changed(self, task: stored.Task) -> str | None:
        """What is new about `task`'s pull, as a line a session can act on: its checks and the
        bot's open items, or that it may be merged. None when there is nothing to read."""
        links = await stored.LinkRecords(self._records).for_owner(
            task.project, OwnerKind.TASK, task.key
        )
        if not any(one.link_kind == "pull" for one in links):
            return "it has no pull yet"
        try:
            found = await self._service.call("orch_readiness", {"key": task.key})
        except (PluginError, UDLError) as error:
            return f"its pull could not be read: {error}"
        if not isinstance(found, Readiness):
            return None
        if found.ready:
            return "its pull is ready to merge: say so with `orch_ready`"
        parts = [f"its pull is not ready ({'; '.join(found.blockers)})"]
        if found.open_items:
            items = "; ".join(
                f"{one.text} ({one.status})" if one.status else one.text for one in found.open_items
            )
            parts.append(f"the review bot still lists: {items}")
        return ". ".join(parts)

    async def _stalled(
        self, agent: stored.Agent, task: stored.Task, session: stored.AgentSession
    ) -> None:
        """A task still going after its nudges: `blocked` ("agent stalled"), and the session
        ended so the loop takes the next one."""
        tasks = stored.TaskRecords(self._records)
        why = f"agent stalled: {agent.name} took {session.nudges} nudges without progress"
        await tasks.move(task, BLOCKED, by=_system("block"), note=why)
        await self._end(agent, session)

    async def _turn(self, agent: stored.Agent, session: stored.AgentSession, prompt: str) -> bool:
        """Send one turn and stream it: its events into the transcript and the live events, and
        what its end says into the session (the card's 3, 4 and 6)."""
        async with self._turns:  # (at most `max_agents_running` turns at once)
            return await self._one_turn(agent, session, prompt)

    async def _one_turn(
        self, agent: stored.Agent, session: stored.AgentSession, prompt: str
    ) -> bool:
        """One turn, streamed (what `_turn` does once it has a turn's place)."""
        sessions = stored.SessionRecords(self._records)
        session = await sessions.save_session(
            session.model_copy(update={"state": SessionState.RUNNING})
        )
        runner = await self._harnesses.runner(agent.host, agent.runner)
        ended: TurnEnded | None = None
        limited: RunnerLimited | None = None
        failed: RunnerFailed | None = None
        try:
            async for said in runner.send(session.ref, prompt):
                event = _an_event(said)
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
        return await self._ended(agent, session, ended, limited=limited, failed=failed)

    async def _said(
        self, agent: stored.Agent, session: stored.AgentSession, event: AnAgentEvent
    ) -> None:
        """One thing the runner said: its transcript record, its live event, and what it cost."""
        sessions = stored.SessionRecords(self._records)
        await sessions.save_event(
            stored.SessionEvent(
                agent=agent.name, session=session.id, turn=session.turns, event=event
            )
        )
        await self._live("orch.session.event", session, {"kind": event.kind, "turn": session.turns})
        if isinstance(event, Usage):
            await sessions.save_session(
                session.model_copy(
                    update={
                        "tokens_in": session.tokens_in + event.input_tokens,
                        "tokens_out": session.tokens_out + event.output_tokens,
                        "cost": event.cost if event.cost is not None else session.cost,
                    }
                )
            )

    async def _ended(
        self,
        agent: stored.Agent,
        session: stored.AgentSession,
        ended: TurnEnded | None,
        *,
        limited: RunnerLimited | None,
        failed: RunnerFailed | None,
    ) -> bool:
        """What a turn's end means for the session (the card's 3, 4 and 6): whether the loop
        carries on with this session, or waits (a limit, a failure) and comes back."""
        sessions = stored.SessionRecords(self._records)
        if limited is not None or (ended is not None and ended.reason is TurnReason.LIMITED):
            until = limited.until if limited is not None else None
            self._harnesses.limited(agent.host, agent.runner, until)
            await sessions.save_session(
                session.model_copy(update={"state": SessionState.LIMITED, "limited_until": until})
            )
            await self._live(
                "orch.agent.limited",
                session,
                {"until": until.isoformat() if until is not None else None},
            )
            return False
        if failed is not None or (ended is not None and ended.reason is TurnReason.ERROR):
            await self._retried(
                agent, session, failed.message if failed is not None else "its turn failed"
            )
            return False
        await sessions.save_session(
            session.model_copy(update={"state": SessionState.IDLE, "turns": session.turns + 1})
        )
        return True

    async def _retried(self, agent: stored.Agent, session: stored.AgentSession, why: str) -> None:
        """A turn that failed: retried with a backoff, and after `FAILURES` tries the session is
        `failed` and its task waits for a person (the card's 6)."""
        sessions = stored.SessionRecords(self._records)
        failures = session.failures + 1
        if failures < FAILURES:
            await sessions.save_session(
                session.model_copy(update={"failures": failures, "state": SessionState.IDLE})
            )
            await self._clock.sleep(BACKOFF * failures)
            return
        await sessions.save_session(
            session.model_copy(update={"failures": failures, "state": SessionState.FAILED})
        )
        await self._live("orch.session.failed", session, {"why": why})
        task = await self._task_of(session)
        if task is not None:
            await stored.TaskRecords(self._records).save_task(
                task.model_copy(update={"blocked_on": f"{agent.name}'s session failed: {why}"})
            )

    async def _task_of(self, session: stored.AgentSession) -> stored.Task | None:
        """The task a session is on, or None when there is none (it was deleted)."""
        if not session.task:
            return None
        tasks = stored.TaskRecords(self._records)
        for project in await self._projects_of_any():
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
            await sessions.save_session(session.model_copy(update={"state": SessionState.ENDED}))
            with contextlib.suppress(PluginError, UDLError):
                runner = await self._harnesses.runner(agent.host, agent.runner)
                await runner.stop(session.ref)
        self._harnesses.ended(agent.host, agent.runner, session.ref.id)

    async def _live(
        self, event_type: str, session: stored.AgentSession, data: Mapping[str, object]
    ) -> None:
        """Publish one of the session's live events (one that cannot be published is logged,
        never fatal: the transcript is the record)."""
        try:
            payload: JsonObject = {
                "agent": session.agent,
                "session": str(session.id),
                "task": session.task,
            }
            payload.update({name: _plain(value) for name, value in data.items()})
            await self._events.emit(
                event_type,
                payload,
                dedupe_key=f"{event_type}:{session.id}:{session.turns}:{self._clock.now()}",
            )
        except (PluginError, UDLError) as error:
            log.warning("publishing %s: %s", event_type, error)


def _an_event(event: AgentEvent) -> AnAgentEvent:
    """`event` as the transcript holds it (a runner's own event is one of the kinds that are)."""
    return cast(AnAgentEvent, event)


def _plain(value: object) -> JsonValue:
    """`value` as JSON holds it."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _system(role: Role) -> Principal:
    """udl's own principal for a move it makes (never a person's, never an agent's)."""
    return Principal(kind="system", role=role)
