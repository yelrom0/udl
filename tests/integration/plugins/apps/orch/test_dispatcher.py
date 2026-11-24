"""
-------------------
- test_dispatcher.py -
-------------------

The dispatcher (27.7): one loop per enabled agent, which picks its task, prepares the worktree,
starts or resumes its session, streams the turn into the transcript, and looks at the task again
afterwards.

The runner is `tests/support/runners.py`'s `FakeRunner` with a scripted turn, so a turn is exactly
what a test says it is (and can call udl in the middle, as an agent's tool call does). The
worktree is a test's own: `prepare()` is 27.5's, tested there, and this is about what the
dispatcher does with it.
"""

# System Imports
import asyncio
import contextlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, Literal

# Package Imports
import pytest
from pydantic import BaseModel, SecretStr

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from support.runners import FakeRunner, FakeRunnerOptions, Sessions, Turn
from udl.automations.models import EventInfo, EventList
from udl.clock import Clock
from udl.config import APISettings
from udl.core.models import PluginSettingsRequest, SetPluginSettingsRequest
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.db.records import Record, RecordQuery
from udl.errors import DatabaseError, DoesNotExist, NotAllowed, PluginError, PluginFailed
from udl.hosts.shell import Host, HostShell
from udl.interfaces.security import Principal as Caller
from udl.interfaces.security import PrincipalKind, current_principal
from udl.plugins.apps.base import (
    AppContext,
    AppEvent,
    AppEvents,
    AppPlugin,
    AppRecords,
    AppSettings,
    AppSubscription,
)
from udl.plugins.apps.orch import questions as asked
from udl.plugins.apps.orch import transitions
from udl.plugins.apps.orch import workspace as space
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers, SessionState
from udl.plugins.apps.orch.dispatcher import (
    PRUNE_EVERY,
    RULES,
    Dispatcher,
    fenced,
    first_prompt,
)
from udl.plugins.apps.orch.health import Health
from udl.plugins.apps.orch.models import (
    BLOCKED,
    DONE,
    IN_PROGRESS,
    MERGED,
    QUEUED,
    READY,
    HostHealth,
    MergeList,
    OwnerKind,
    Principal,
    PullState,
    QuestionState,
    Suggestions,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    AgentSession,
    Feature,
    FeatureRecords,
    LinkRecords,
    NoteRecords,
    Project,
    ProjectRecords,
    Question,
    QuestionRecords,
    Repo,
    RepoRecords,
    ReviewRecords,
    SessionEvent,
    SessionRecords,
    StateChange,
    Task,
    TaskRecords,
    WaitRecords,
)
from udl.plugins.apps.orch.settings import HealthSettings, OrchSettings
from udl.plugins.apps.orch.tokens import secret_ref
from udl.plugins.registry import APPS, RUNNERS, PluginRegistry
from udl.plugins.runners.base import (
    AgentEvent,
    RunnerFailed,
    RunnerLimited,
    SessionRef,
    SessionSpec,
    TextDelta,
    TurnEnded,
    TurnReason,
    Usage,
)

PULL: str = "https://github.com/yelrom0/udl/pull/7"
"""The pull the fake `github` answers for."""
HEAD: str = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""The commit at its head."""
AGENT: str = "deepseek-b"


# ---------------------------------------------------------------------------------------------
# A fake github, so `orch_ready` has a pull to find ready (as 27.6's own tests do)
# ---------------------------------------------------------------------------------------------


class FakePull(BaseModel):
    """A `github_get_pull` answer, as far as readiness reads it."""

    repo: str = "yelrom0/udl"
    number: int = 7
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
    author: str = AGENT
    html_url: str = PULL
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
            "body": "## Follow-up review: it (#7), after dc1fd19\n\n**Verdict: all clear**\n",
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
    """A `github` app whose one operation answers a pull (what readiness calls)."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.pull = FakePull()
        self.reads = 0
        """How many pulls it was asked for (what a test counts a wake by)."""
        self.fails: Exception | None = None
        """What reading the pull raises, when a test wants one that cannot be read."""
        self.merged: list[MergePullRequest] = []
        """The merges it was asked for (28.7's merger merges through it)."""

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

    async def get_pull(self, request: GetPullRequest) -> FakePull:
        """The pull the test set (whatever number it asks for)."""
        self.reads += 1
        if self.fails is not None:
            raise self.fails
        return self.pull

    async def merge_pull(self, request: MergePullRequest) -> MergeResult:
        """Record the merge and answer it (28.7)."""
        self.merged.append(request)
        return MergeResult(sha=request.sha)


# ---------------------------------------------------------------------------------------------
# A runner whose turns the test scripts
# ---------------------------------------------------------------------------------------------

RAISES: dict[str, Exception] = {}
"""Prompts whose turn raises (not a UDL error: what a loop must survive)."""
SCRIPT: dict[str, Turn] = {}
"""The scripted turns, by prompt: what this test's turns do."""
STORE: Sessions = Sessions()
"""The sessions the harness has (a fresh one per test)."""
REVIEWS = Path("tests/fixtures/reviews")
"""The bot's real review comments (27.6's fixtures)."""
PROMPTS: list[str] = []
"""Every prompt a session was sent (in order)."""
SERVICE: list[UDLService] = []
"""The service the scripted turns call (a fresh one per test)."""


class ScriptedRunner(FakeRunner):
    """`FakeRunner`, made by the harnesses' own factory: it reads this module's script."""

    name: ClassVar[str] = "fake"

    def __init__(self, options: FakeRunnerOptions, *, shell: HostShell, clock: Clock) -> None:
        super().__init__(
            options,
            shell=shell,
            clock=clock,
            script=SCRIPT,
            store=STORE,
        )

    def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        """Record the prompt, then send it as the fake does (or raise, if it is scripted to).

        A manager's prompt carries its reports' news, so it is scripted by `MANAGER_KEY` and the
        turn it gets is the one scripted for `manager`."""
        PROMPTS.append(prompt)
        if prompt in RAISES:
            return _raising(RAISES[prompt])
        if prompt.startswith(MERGED_KEY):
            # (28.7's merger: its prompt is the list, so a test scripts the merger's turn.)
            MERGER_PROMPT[0] = prompt
            if "manager" in SCRIPT:
                return self._manager(session, SCRIPT["manager"])
        if prompt.startswith(MANAGER_KEY) and "manager" in SCRIPT:
            # (A turn's body runs when it is first iterated, after other sessions' prompts may
            # have been recorded: keep this one aside for it.)
            MANAGER_PROMPT[0] = prompt
            return self._manager(session, SCRIPT["manager"])
        named = _A_TASK.search(prompt)
        if named is not None and f"task:{named.group(0)}" in SCRIPT:
            # (A task's prompts change as its pull does; a test scripts the task, not each line.)
            return self._manager(session, SCRIPT[f"task:{named.group(0)}"])
        return super().send(session, prompt)

    async def _manager(self, session: SessionRef, turn: Turn) -> AsyncIterator[AgentEvent]:
        """`turn`, as a manager's session runs it: the fake's bookkeeping, and its own ending."""
        STORE.turns += 1
        async for event in turn(self, session):
            yield event
        yield TurnEnded(reason=TurnReason.DONE, text="done")


_A_TASK = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+(?:\.[0-9]+)*")
"""A task key in a prompt (27.8's done-when scripts a task, whatever its session is told)."""
STARTED: asyncio.Event = asyncio.Event()
"""Set when a waiting turn has begun."""
GO_ON: asyncio.Event = asyncio.Event()
"""Let a waiting turn finish."""
MANAGER_PROMPT: list[str] = [""]
"""The prompt the manager's running turn was given (a turn's body starts after `send`)."""
MANAGER_KEY: str = "You are ernie-c"
"""How a manager's prompt starts (its news follows, so it is scripted by this prefix)."""


async def _raising(error: Exception) -> AsyncIterator[AgentEvent]:
    """A turn that raises `error` (not a UDL error: what a loop must survive)."""
    raise error
    yield TextDelta(text="never")  # pragma: no cover (it is what makes this a generator)


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github, the fake runner and orch."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    plugins.register(RUNNERS, ScriptedRunner)
    return plugins


# ---------------------------------------------------------------------------------------------
# The dispatcher, run until it has done what the test waits for
# ---------------------------------------------------------------------------------------------


async def dispatcher(
    service: UDLService,
    *,
    clock: FakeClock | None = None,
    tick: timedelta | None = None,
    settings: OrchSettings | None = None,
    records: AppRecords | None = None,
    health: Health | None = None,
) -> tuple[Dispatcher, AppContext]:
    """A dispatcher for `service`, with a test's own `prepare()` (a path, no git), and a test's
    own records when it needs a store that fails."""
    made = clock if clock is not None else FakeClock()
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    records = records if records is not None else AppRecords(service.db)
    events = AppEvents(service.bus, "orch", Orch.event_types())
    ctx = AppContext(
        app=app,
        service=service,
        records=records,
        events=events,
        secrets=service.secrets,
        clock=made,
        settings=settings if settings is not None else OrchSettings(),
        stop=__import__("asyncio").Event(),
    )
    found = Dispatcher(
        ctx,
        harnesses=app.harnesses,
        prepare=fake_prepare,
        tick=tick if tick is not None else timedelta(seconds=30),
        health=health,
    )
    return found, ctx


async def fake_prepare(task: Task, agent: Agent) -> Task:
    """A worktree for `task` on `agent`'s host: a path of its own, and its branch."""
    return task.model_copy(
        update={"worktree": f"/work/{task.key}", "branch": f"feat/{task.key}-{task.title}"}
    )


async def a_while(service: UDLService, *, steps: int = 6) -> None:
    """Run a dispatcher of the test's own for a few ticks, then stop it (a test that asserts
    nothing was done has nothing to wait for)."""
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(steps):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running


async def until_there(
    service: UDLService,
    what: Callable[[], Awaitable[bool]],
    *,
    steps: int = 40,
) -> None:
    """Run a dispatcher of the test's own on a fake clock until `what` says so (or fail).

    How many turns one tick runs is the dispatcher's to decide: a test that waits for what it
    wants, rather than for a count of turns, is not racing the loop's own speed."""
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(steps):
            if await what():
                return
            await clock.advance(timedelta(seconds=31))
        raise AssertionError("the dispatcher never got there")
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running


async def until(what: Callable[[], Awaitable[bool]], *, steps: int = 50) -> None:
    """Let the dispatcher work until `what` says so (or fail the test)."""
    for _ in range(steps):
        if await what():
            return
        await __import__("asyncio").sleep(0)
    raise AssertionError("the dispatcher never got there")


# ---------------------------------------------------------------------------------------------
# The service, its project, its agent, and its queue
# ---------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean() -> Iterator[None]:
    """A fresh script and store for each test."""
    SCRIPT.clear()
    RAISES.clear()
    PROMPTS.clear()
    MERGER_PROMPT[0] = ""
    STORE.specs.clear()
    STORE.stopped.clear()
    STORE.gates.clear()
    STORE.turns = 0
    SERVICE.clear()
    yield
    SERVICE.clear()


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project (with rules), its feature, an agent, and one queued task."""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
    ) as running:
        SERVICE.append(running)
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(
                name="udl",
                title="udl",
                review_bot="claude[bot]",
                rules="Never push to main.",
            )
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await AgentRecords(records).save_agent(
            Agent(
                name=AGENT,
                title=AGENT,
                runner="fake",
                host="local",
                brief="Build the task on your queue.",
            )
        )
        tasks = TaskRecords(records)
        for number in (1, 2):
            key = f"F-1.{number}"
            task = Task(project="udl", feature="F-1", key=key, title=f"It {number}", assignee=AGENT)
            await tasks.save_task(task)
            await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))
            await LinkRecords(records).add(
                project="udl", owner_kind=OwnerKind.TASK, owner=key, kind="pull", url=PULL
            )
        yield running


def first_prompt_of(key: str) -> str:
    """What the dispatcher's first prompt for `key` is (as `first_prompt` writes it)."""
    return first_prompt(Task(project="udl", feature="F-1", key=key, title=f"It {key[-1]}"))


async def collecting(subscription: AppSubscription, into: list[AppEvent]) -> None:
    """Take every event `subscription` gives into `into` (until the test cancels it)."""
    async with subscription as notes:
        async for event in notes:
            into.append(event)


async def tasks_of(service: UDLService) -> dict[str, Task]:
    """Every task of the project, by key."""
    found = await TaskRecords(AppRecords(service.db)).tasks("udl")
    return {one.key: one for one in found}


async def questions_of(service: UDLService) -> list[Question]:
    """Every question of the project, oldest first."""
    return await QuestionRecords(AppRecords(service.db)).questions("udl", state=None)


async def session_of(service: UDLService, agent: str = AGENT) -> AgentSession | None:
    """The newest session `agent` was in, or None."""
    found = await SessionRecords(AppRecords(service.db)).sessions(agent)
    return found[0] if found else None


async def ran(
    service: UDLService,
    *,
    clock: FakeClock,
    turns: int = 1,
    ready: bool = False,
) -> None:
    """Run the dispatcher until it has sent `turns` turns (or, with `ready`, until both tasks are
    `ready`), then stop it."""
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    every: dict[str, Task] = {}
    try:
        for _ in range(20):
            every = await tasks_of(service)
            if ready and all(one.state == READY for one in every.values()):
                return
            if not ready and STORE.turns >= turns:
                await clock.advance(timedelta(seconds=31)) if turns == 0 else None
                return
            await clock.advance(timedelta(seconds=31))
        raise AssertionError(
            f"never got there: {[(k, t.state) for k, t in every.items()]}"
            f" scripted: {list(SCRIPT)} sent: {PROMPTS}"
        )
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running


async def two_agents(service: UDLService, second: str = "other") -> None:
    """Give a second agent the fixture's second task (both are one agent's to begin with)."""
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(name=second, title=second, runner="fake", host="local", brief="Build it.")
    )
    tasks = TaskRecords(records)
    task = await tasks.task("udl", "F-1.2")
    assert task is not None
    await tasks.save_task(task.model_copy(update={"assignee": second}))


async def events_of(service: UDLService, type_: str) -> list[EventInfo]:
    """The events published so far whose type matches `type_`."""
    published = await service.call("list_events", {"type": type_, "limit": 200})
    assert isinstance(published, EventList)
    return published.events


async def scripted(service: UDLService, key: str, *, prompt: str | None = None) -> None:
    """Script the turn that takes `key` to `ready`, for its first prompt (or `prompt`)."""
    task = await TaskRecords(AppRecords(service.db)).task("udl", key)
    assert task is not None
    SCRIPT[prompt if prompt is not None else first_prompt(task)] = ready_turn(key)


async def ask(name: str, request: dict[str, object]) -> object:
    """Call an operation of the test's service, as a scripted turn's tool call does."""
    assert SERVICE, "a turn ran with no service"
    try:
        return await SERVICE[0].call(name, request)
    except Exception as error:
        import traceback

        traceback.print_exc()
        raise AssertionError(f"{name} failed: {error}") from error


def ready_turn(key: str) -> Turn:
    """A scripted turn that does the task and says it is ready, as an agent's tool calls do."""

    async def turn(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
        await ask("orch_ready", {"key": key})
        yield TextDelta(text=f"{key} is ready.")

    return turn


# ---------------------------------------------------------------------------------------------
# The card's done-when
# ---------------------------------------------------------------------------------------------


async def test_two_queued_tasks_are_taken_to_ready_in_order(service: UDLService) -> None:
    """(The card's done-when: a FakeRunner agent takes two queued tasks to `ready` in order with
    nobody prompting it.)"""
    await scripted(service, "F-1.1")
    await scripted(service, "F-1.2")

    await ran(service, clock=FakeClock(), ready=True)

    every = await tasks_of(service)
    assert {key: one.state for key, one in every.items()} == {"F-1.1": READY, "F-1.2": READY}
    assert len(STORE.specs) == 2, "one session per task, not one per agent"


# ---------------------------------------------------------------------------------------------
# What a session is started with
# ---------------------------------------------------------------------------------------------


async def test_the_move_that_starts_a_task_is_the_agent_s_and_on_the_clock(
    service: UDLService,
) -> None:
    """(27.10: a stretch credited to `udl` would teach no model anything, and its time is the
    app's clock — not the wall clock, which a `FakeClock` test's history would mix with.)"""
    await scripted(service, "F-1.1")
    clock = FakeClock()

    await ran(service, clock=clock, turns=1)

    task = (await tasks_of(service))["F-1.1"]
    moves = await AppRecords(service.db).list(StateChange, RecordQuery(where={"task": task.id}))
    started = [one for one in moves if one.to_state == IN_PROGRESS]
    assert [one.by for one in started] == [AGENT]
    assert started[0].at < datetime.now(UTC), "the clock's time, not a made-up one"


async def test_the_worktree_is_made_and_the_task_is_in_progress_before_the_turn(
    service: UDLService,
) -> None:
    await ran(service, clock=FakeClock())

    every = await tasks_of(service)
    assert every["F-1.1"].worktree == "/work/F-1.1"
    assert every["F-1.1"].branch == "feat/F-1.1-It 1"


async def test_a_session_is_started_under_its_memory_cap_and_its_own_unit(
    service: UDLService,
) -> None:
    """(27.12: a runaway in a turn is that turn's failure, not the host's — each turn its own
    `udl-session-<agent>-<8 hex>`, and the cap the settings give.)"""
    await ran(service, clock=FakeClock())

    spec = next(iter(STORE.specs.values()))
    assert spec.memory == OrchSettings().session_memory
    assert spec.unit == f"udl-session-{AGENT}"


async def test_the_instructions_are_the_rules_the_project_and_the_brief(
    service: UDLService,
) -> None:
    await ran(service, clock=FakeClock())

    spec = next(iter(STORE.specs.values()))
    assert spec.instructions is not None
    assert spec.instructions.startswith(RULES)
    assert "Never push to main." in spec.instructions
    assert "Build the task on your queue." in spec.instructions
    assert spec.cwd == "/work/F-1.1"


async def test_the_first_prompt_names_the_task_and_says_to_call_my_task(
    service: UDLService,
) -> None:
    await ran(service, clock=FakeClock())

    assert "F-1.1" in PROMPTS[0]
    assert "orch_my_task" in PROMPTS[0]
    assert "orch_ready" in PROMPTS[0]


async def test_what_the_runner_says_is_the_transcript(service: UDLService) -> None:
    await scripted(service, "F-1.1")
    await ran(service, clock=FakeClock())

    sessions = await SessionRecords(AppRecords(service.db)).sessions(AGENT)
    first = next(one for one in sessions if one.task == "F-1.1")
    said = await SessionRecords(AppRecords(service.db)).transcript(first.id)

    assert [one.event.kind for one in said] == ["text_delta", "turn_ended"]
    assert said[0].agent == AGENT
    assert said[0].turn == 0


async def test_the_session_is_ended_when_its_task_moves_on(service: UDLService) -> None:
    await scripted(service, "F-1.1")

    await ran(service, clock=FakeClock())

    sessions = await SessionRecords(AppRecords(service.db)).sessions(AGENT)

    assert {one.state for one in sessions} == {SessionState.ENDED}
    assert {one.ref.id for one in sessions} <= STORE.stopped


# ---------------------------------------------------------------------------------------------
# A turn that leaves the task where it was
# ---------------------------------------------------------------------------------------------


async def test_a_task_still_going_is_nudged_and_then_blocked_as_stalled(
    service: UDLService,
) -> None:
    await ran(service, clock=FakeClock(), turns=5)

    every = await tasks_of(service)
    assert every["F-1.1"].state == BLOCKED
    # (28.1: a stall is a question, and each task waits on its own rather than on a line of text)
    asked_of = await questions_of(service)
    assert {one.task for one in asked_of} == {"F-1.1", "F-1.2"}
    assert {one.title for one in asked_of} == {
        "agent stalled: deepseek-b took 3 nudges without progress"
    }
    assert all(one.blocking and one.state is QuestionState.OPEN for one in asked_of)
    assert every["F-1.1"].blocked_on in {one.key for one in asked_of}
    assert any("still on F-1.1" in prompt for prompt in PROMPTS)
    # (one turn that said what changed — progress — then `max_nudges` turns that said nothing
    # new, and the task is blocked rather than nudged again)
    assert sum(1 for prompt in PROMPTS if "F-1.1" in prompt) == 5


async def test_a_turn_says_what_changed_and_then_nudges_when_nothing_did(
    service: UDLService,
) -> None:
    await ran(service, clock=FakeClock(), turns=3)

    assert "What changed on F-1.1: its pull is ready to merge" in PROMPTS[1]
    assert "orch_push" in PROMPTS[1]
    # (the same news twice is not news: the turn after it is a nudge)
    assert "You are still on F-1.1" in PROMPTS[2]
    assert "orch_add_note" in PROMPTS[2]
    assert "orch_ready" in PROMPTS[2]


# ---------------------------------------------------------------------------------------------
# Limits, failures, restarts and pauses
# ---------------------------------------------------------------------------------------------


async def test_a_limited_harness_is_not_dispatched_again_until_its_quota_is_back(
    service: UDLService,
) -> None:
    reset_at = datetime(2026, 1, 2, 4, 4, 5, tzinfo=UTC)

    async def limited(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        yield RunnerLimited(until=reset_at, message="out of quota")
        yield TurnEnded(reason=TurnReason.LIMITED, text="out of quota")

    SCRIPT[first_prompt_of("F-1.1")] = limited

    clock = FakeClock()
    await ran(service, clock=clock, turns=1)

    session = await session_of(service)
    assert session is not None
    assert session.state is SessionState.LIMITED
    assert session.limited_until == reset_at
    assert STORE.turns == 1
    assert (await tasks_of(service))["F-1.1"].state == IN_PROGRESS
    # (27.10: the quota's wait is a `limit` one, so the hours it spent out of quota are not its
    # work.)
    task = (await tasks_of(service))["F-1.1"]
    waits = await WaitRecords(AppRecords(service.db)).open_for(task.id)
    assert [one.wait_kind for one in waits] == ["limit"]


async def test_a_failed_turn_is_retried_and_then_the_session_fails(
    service: UDLService,
) -> None:
    async def broken(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        yield RunnerFailed(message="the harness fell over")
        yield TurnEnded(reason=TurnReason.ERROR, text="the harness fell over")

    SCRIPT[first_prompt_of("F-1.1")] = broken

    clock = FakeClock()
    await ran(service, clock=clock, turns=3)

    session = await session_of(service)
    assert session is not None
    assert session.failures == 3
    # (it is ended as well as failed: a failed session that stayed "current" would hold the
    # agent for good, and its task is `blocked` for a person anyway)
    assert session.state is SessionState.ENDED
    every = await tasks_of(service)
    assert every["F-1.1"].state == BLOCKED
    # (28.1: a failed session is a question too, so a person is asked about it)
    asked_of = await questions_of(service)
    assert [one.title for one in asked_of] == ["deepseek-b's session failed: the harness fell over"]
    assert every["F-1.1"].blocked_on == asked_of[0].key


async def test_a_session_udl_restarted_is_resumed_with_continue(service: UDLService) -> None:
    sessions = SessionRecords(AppRecords(service.db))
    made = await sessions.save_session(
        AgentSession(
            agent=AGENT,
            task="F-1.1",
            ref=SessionRef(runner="fake", id="fake-9", cwd="/work/F-1.1", host="local"),
            state=SessionState.RUNNING,
            turns=2,
        )
    )
    STORE.specs["fake-9"] = SessionSpec(cwd="/work/F-1.1")

    await ran(service, clock=FakeClock())

    assert PROMPTS[0] == "udl restarted; continue where you were."
    again = await sessions.session(made.id)
    assert again is not None
    assert again.turns == 3


async def test_a_paused_agent_is_given_nothing(service: UDLService) -> None:
    agents = AgentRecords(AppRecords(service.db))
    agent = await agents.agent(AGENT)
    assert agent is not None
    await agents.save_agent(agent.model_copy(update={"paused": True}))

    await ran(service, clock=FakeClock(), turns=0)

    assert PROMPTS == []
    assert (await tasks_of(service))["F-1.1"].state == QUEUED


async def test_a_task_whose_needs_are_not_done_is_left_for_later(
    service: UDLService,
) -> None:
    tasks = TaskRecords(AppRecords(service.db))
    later = await tasks.task("udl", "F-1.2")
    assert later is not None
    await tasks.save_task(later.model_copy(update={"needs": ["F-1.1"], "position": 1}))
    first = await tasks.task("udl", "F-1.1")
    assert first is not None
    await tasks.save_task(first.model_copy(update={"position": 2}))

    await ran(service, clock=FakeClock(), turns=0)

    assert PROMPTS[0].startswith("Your task is F-1.1")
    every = await tasks_of(service)
    assert every["F-1.2"].worktree is None, "the task that waits was never taken"


# ---------------------------------------------------------------------------------------------
# The live events (27.9's stream reads them)
# ---------------------------------------------------------------------------------------------


async def test_a_session_says_what_happened_as_it_happens(service: UDLService) -> None:
    await scripted(service, "F-1.1")

    await ran(service, clock=FakeClock())

    published = await service.call("list_events", {"type": "orch.session.*"})
    assert isinstance(published, EventList)
    names = {one.type for one in published.events}
    assert {"orch.session.started", "orch.session.event"} <= names
    said = [
        one
        for one in published.events
        if one.type == "orch.session.event" and one.data["task"] == "F-1.1"
    ]
    assert said[0].data["agent"] == AGENT


def test_the_session_event_types_are_declared() -> None:
    names = [event_type.name for event_type in Orch.event_types()]

    assert {
        "orch.session.started",
        "orch.session.event",
        "orch.session.ended",
        "orch.session.failed",
        "orch.agent.limited",
    } <= set(names)


async def test_a_session_is_told_what_only_udl_may_do(service: UDLService) -> None:
    await ran(service, clock=FakeClock())

    spec = next(iter(STORE.specs.values()))
    assert "git push *" in spec.deny
    assert "gh pr merge *" in spec.deny


async def test_a_session_with_a_hub_url_is_given_udl_and_its_own_token(
    tmp_path: Path,
) -> None:
    """(The card's 2: the session's MCP endpoint is udl's, with the agent's token.)"""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("a-key"))), registry=orch_registry()
    ) as running:
        SERVICE.append(running)
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await AgentRecords(records).save_agent(
            Agent(name=AGENT, title=AGENT, runner="fake", host="local")
        )
        tasks = TaskRecords(records)
        task = Task(project="udl", feature="F-1", key="F-1.1", title="It 1", assignee=AGENT)
        await tasks.save_task(task)
        await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))

        found, _ = await dispatcher(
            running,
            clock=FakeClock(),
            settings=OrchSettings(hub_url="http://127.0.0.1:8080/mcp"),
        )
        running_task = asyncio.ensure_future(found.run())
        try:
            for _ in range(10):
                if STORE.turns >= 1:
                    break
                await asyncio.sleep(0)
        finally:
            running_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_task

        spec = next(iter(STORE.specs.values()))
        assert spec.mcp is not None
        assert spec.mcp.url == "http://127.0.0.1:8080/mcp"
        assert spec.mcp.token is not None
        assert spec.mcp.token.get_secret_value().startswith("udl_agent_")


async def test_a_server_with_no_api_key_starts_no_session(tmp_path: Path) -> None:
    """(27.2's rule, which 27.7 must keep: without `api.api_key` every request is the owner's, so
    a session's token would limit nothing — udl starts no agent at all.)"""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        SERVICE.append(running)
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await AgentRecords(records).save_agent(
            Agent(name=AGENT, title=AGENT, runner="fake", host="local")
        )
        tasks = TaskRecords(records)
        task = Task(project="udl", feature="F-1", key="F-1.1", title="It 1", assignee=AGENT)
        await tasks.save_task(task)
        await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))

        found, _ = await dispatcher(running, clock=FakeClock())
        running_task = asyncio.ensure_future(found.run())
        try:
            for _ in range(20):
                await asyncio.sleep(0)
        finally:
            running_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_task

        assert STORE.specs == {}
        assert PROMPTS == []
        every = await tasks_of(running)
        assert every["F-1.1"].state == QUEUED


async def test_a_turn_says_what_its_pull_says(tmp_path: Path) -> None:
    """(The card's 3: a turn adds the pull's checks and the bot's comment.)"""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
    ) as running:
        SERVICE.append(running)
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]")
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await AgentRecords(records).save_agent(
            Agent(name=AGENT, title=AGENT, runner="fake", host="local")
        )
        tasks = TaskRecords(records)
        task = Task(project="udl", feature="F-1", key="F-1.1", title="It 1", assignee=AGENT)
        await tasks.save_task(task)
        await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))
        await LinkRecords(records).add(
            project="udl",
            owner_kind=OwnerKind.TASK,
            owner="F-1.1",
            kind="pull",
            url="https://github.com/yelrom0/udl/pull/7",
        )

        github = await running.apps.get("github")
        assert isinstance(github, FakeGitHub)
        github.pull = FakePull(
            draft=True,
            head_sha="7a09481",
            checks=[],
            comments=[
                {
                    "author": "claude[bot]",
                    "body": (REVIEWS / "follow-up-changes.md").read_text(),
                    "created_at": "2026-11-20T10:00:00Z",
                }
            ],
        )

        await ran(running, clock=FakeClock(), turns=2)

    assert "What changed on F-1.1: its pull is not ready" in PROMPTS[1]
    assert "it is a draft" in PROMPTS[1]
    # (what the bot says is outside text: it is fenced and labelled, never instructions)
    assert "```review-bot" in PROMPTS[1]


async def test_only_so_many_agents_are_mid_turn_at_once(tmp_path: Path) -> None:
    """(The card's 5: `max_agents_running` agents mid-turn at once, the rest waiting.)"""

    midturn: list[str] = []

    async def hold(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
        midturn.append(session.id)
        await runner.waiting(session)  # (a turn that only ends when its session is stopped)
        yield TextDelta(text="done.")

    SCRIPT[first_prompt_of("F-1.1")] = hold
    SCRIPT[first_prompt_of("F-1.2")] = hold
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
    ) as running:
        SERVICE.append(running)
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        agents = AgentRecords(records)
        tasks = TaskRecords(records)
        for number in (1, 2):
            await agents.save_agent(
                Agent(name=f"agent-{number}", title=f"Agent {number}", runner="fake", host="local")
            )
            task = Task(
                project="udl",
                feature="F-1",
                key=f"F-1.{number}",
                title=f"It {number}",
                assignee=f"agent-{number}",
            )
            await tasks.save_task(task)
            await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))

        clock = FakeClock()
        found, _ = await dispatcher(
            running, clock=clock, settings=OrchSettings(max_agents_running=1)
        )
        running_task = asyncio.ensure_future(found.run())
        try:
            for _ in range(20):
                if midturn:
                    break
                await clock.advance(timedelta(seconds=31))
            await asyncio.sleep(0)
            turning = len(midturn)
        finally:
            running_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_task

    assert turning == 1, "the second agent waits for the first turn's place"


async def test_an_agent_whose_host_does_not_answer_is_offline(service: UDLService) -> None:
    """(The card's 6: an agent whose host fails `check_host` is `offline`, and nothing is sent.)"""
    await AgentRecords(AppRecords(service.db)).save_agent(
        Agent(name="far", title="Far", runner="fake", host="nowhere", projects=["udl"])
    )
    task = Task(project="udl", feature="F-1", key="F-1.9", title="Far away", assignee="far")
    tasks = TaskRecords(AppRecords(service.db))
    await tasks.save_task(task)
    await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))

    found, _ = await dispatcher(service, clock=FakeClock())
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    far = await AgentRecords(AppRecords(service.db)).agent("far")
    assert far is not None
    assert far.offline
    assert all(spec.cwd != "/work/F-1.9" for spec in STORE.specs.values())


async def test_a_stall_is_asked_of_the_agent_s_manager_when_it_may_answer(
    service: UDLService,
) -> None:
    """(28.1's routing, from the dispatcher's side: what an agent cannot settle itself goes to
    its manager when that manager may answer, not to the humans.)"""
    agents = AgentRecords(AppRecords(service.db))
    await agents.save_agent(
        Agent(
            name="ernie-c",
            title="ernie-c",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(answer=True),
        )
    )
    found = await agents.agent(AGENT)
    assert found is not None
    await agents.save_agent(found.model_copy(update={"manager": "ernie-c"}))

    await ran(service, clock=FakeClock(), turns=5)

    asked_of = await questions_of(service)
    assert asked_of
    assert {one.to for one in asked_of} == {"ernie-c"}


async def test_an_answer_reaches_the_agent_s_next_session(service: UDLService) -> None:
    """(28.1: the answer is delivered into the agent's session — a session that picks the task up
    again is told what was answered while it waited.)"""
    await TaskRecords(AppRecords(service.db)).delete_task("udl", "F-1.2")
    agents = AgentRecords(AppRecords(service.db))
    working = await agents.agent(AGENT)
    assert working is not None
    # (The agent stalls after two turns with nothing to show, which is what asks the question.)
    await agents.save_agent(working.model_copy(update={"max_nudges": 2}))

    async def asked() -> bool:
        return bool(await questions_of(service))

    await until_there(service, asked)
    asked_of = await questions_of(service)
    assert len(asked_of) == 1
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer",
            {"project": "udl", "key": asked_of[0].key, "answer": "Go on: it is one more file."},
        )
    finally:
        current_principal.reset(token)
    PROMPTS.clear()
    STORE.turns = 0

    await ran(service, clock=FakeClock(), turns=1)

    assert PROMPTS
    assert "these were answered" in PROMPTS[0]
    assert asked_of[0].key in PROMPTS[0]
    assert "Go on: it is one more file." in PROMPTS[0]


async def test_a_session_another_process_is_turning_is_left_alone(service: UDLService) -> None:
    """(27.9: the loop's turn for a session another process is turning is no turn at all — the
    session is not counted, failed or ended, and the answer its prompt would have carried is
    still to be delivered by the turn that does run.)"""
    await TaskRecords(AppRecords(service.db)).delete_task("udl", "F-1.2")
    agents = AgentRecords(AppRecords(service.db))
    working = await agents.agent(AGENT)
    assert working is not None
    # (No stall, and so no blocking question: this test is about a session's turn, not a nudge.)
    await agents.save_agent(working.model_copy(update={"max_nudges": 1000}))
    await ran(service, clock=FakeClock(), turns=1)  # (a session of its own, with a turn behind it)
    session = await session_of(service)
    assert session is not None

    token = current_principal.set(Caller(kind=PrincipalKind.AGENT, name=AGENT, role="implementer"))
    try:
        await service.call(
            "orch_ask",
            {
                "project": "udl",
                "task": "F-1.1",
                "title": "Which file?",
                "text": "There are two that fit.",
                "blocking": False,
            },
        )
    finally:
        current_principal.reset(token)
    mine = next(one for one in await questions_of(service) if one.title == "Which file?")
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer",
            {"project": "udl", "key": mine.key, "answer": "The one under src."},
        )
    finally:
        current_principal.reset(token)

    # (another process is in a turn of that session)
    held = await service.db.claim_lease(
        f"orch-turn:{session.id}", "another-host:4242:abcd", timedelta(hours=1)
    )
    assert held is not None
    PROMPTS.clear()
    STORE.turns = 0

    await a_while(service)

    assert STORE.turns == 0  # (no turn was sent)
    left = await SessionRecords(AppRecords(service.db)).session(session.id)
    assert left is not None
    assert left.state is session.state
    assert left.turns == session.turns
    assert left.failures == 0
    assert not [one for one in await questions_of(service) if one.delivered]
    # (and the turn that does run carries it)
    await service.db.release_lease(f"orch-turn:{session.id}", "another-host:4242:abcd")
    await ran(service, clock=FakeClock(), turns=1)
    assert PROMPTS
    assert "these were answered" in PROMPTS[0]
    assert "The one under src." in PROMPTS[0]


async def test_a_manager_s_session_another_process_is_turning_is_left_alone(
    service: UDLService,
) -> None:
    """(27.8 with 27.9: a manager whose session another process is turning is not failed or
    ended, and its news is not counted as told — the turn that does run tells it.)"""
    await a_manager_and_its_reports(service)
    SCRIPT["manager"] = silent_turn
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    records = AppRecords(service.db)
    # (the manager's standing task, and the session another process is in a turn of: the loop
    # takes a RUNNING session over, which is what the lease must stop it doing)
    standing = Task(
        project="udl",
        feature="managing",
        key="managing.1",
        title="ernie-c's reports",
        task_kind="review",
        assignee="ernie-c",
    )
    await FeatureRecords(records).save_feature(
        Feature(project="udl", key="managing", title="Managing")
    )
    await TaskRecords(records).save_task(standing)
    tasks = TaskRecords(records)
    queued = await tasks.move(standing, QUEUED, by=Principal(kind="system", role="assign"))
    await tasks.move(queued, IN_PROGRESS, by=Principal(kind="system", role="start"))
    running = await SessionRecords(records).save_session(
        AgentSession(
            agent="ernie-c",
            project="udl",
            task=standing.key,
            ref=SessionRef(runner="fake", id="fake-9", cwd="/work/managing.1", host="local"),
            state=SessionState.RUNNING,
            turns=3,
        )
    )
    await service.db.claim_lease(
        f"orch-turn:{running.id}", "another-host:4242:abcd", timedelta(hours=1)
    )
    MANAGER_PROMPT[0] = ""
    loop = asyncio.ensure_future(found.run())
    try:
        for _ in range(6):
            await clock.advance(timedelta(seconds=31))
    finally:
        loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop

    assert MANAGER_PROMPT[0] == ""  # (the manager's turn was not taken from under it)
    left = await SessionRecords(records).session(running.id)
    assert left is not None
    assert left.state is SessionState.RUNNING
    assert left.turns == 3
    assert left.failures == 0
    told = await AgentRecords(records).agent("ernie-c")
    assert told is not None
    assert told.last_news == ""  # (nothing was told, so nothing is remembered as told)


async def test_a_turn_that_says_what_changed_keeps_the_turns_it_ran(service: UDLService) -> None:
    """(27.9: what a prompt told is written onto the session as it is stored after the turn, not
    the copy taken before it — so the turn's own number, its tokens and its cost stay.)"""
    await TaskRecords(AppRecords(service.db)).delete_task("udl", "F-1.2")
    agents = AgentRecords(AppRecords(service.db))
    working = await agents.agent(AGENT)
    assert working is not None
    await agents.save_agent(working.model_copy(update={"max_nudges": 1000}))
    await ran(service, clock=FakeClock(), turns=1)
    before = await session_of(service)
    assert before is not None
    assert before.turns >= 1

    # (news for a "changed" turn: a question answered)
    token = current_principal.set(Caller(kind=PrincipalKind.AGENT, name=AGENT, role="implementer"))
    try:
        await service.call(
            "orch_ask",
            {
                "project": "udl",
                "task": "F-1.1",
                "title": "Which file?",
                "text": "There are two that fit.",
                "blocking": False,
            },
        )
    finally:
        current_principal.reset(token)
    mine = next(one for one in await questions_of(service) if one.title == "Which file?")
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer",
            {"project": "udl", "key": mine.key, "answer": "The one under src."},
        )
    finally:
        current_principal.reset(token)

    SCRIPT["task:F-1.1"] = a_turn_that_finishes_the_task
    STORE.turns = 0
    await ran(service, clock=FakeClock(), turns=1)

    carried = next(one for one in await questions_of(service) if one.title == "Which file?")
    assert carried.delivered  # (the turn that ran carried the answer)
    after = await SessionRecords(AppRecords(service.db)).session(before.id)
    assert after is not None
    assert after.turns == before.turns + 1  # (the turn's own number is not overwritten)
    assert after.tokens_in == before.tokens_in + 100  # (nor what it cost)
    assert after.state is SessionState.ENDED  # (nor the end the loop gave it)
    assert after.last_text


async def test_a_session_a_person_stops_mid_turn_stays_ended(service: UDLService) -> None:
    """(27.9: the `ENDED` a person gave a session mid-turn is not overwritten by the copy of the
    session taken before the turn.)"""
    await TaskRecords(AppRecords(service.db)).delete_task("udl", "F-1.2")
    agents = AgentRecords(AppRecords(service.db))
    working = await agents.agent(AGENT)
    assert working is not None
    await agents.save_agent(working.model_copy(update={"max_nudges": 1000}))
    await ran(service, clock=FakeClock(), turns=1)
    session = await session_of(service)
    assert session is not None

    # (news for a "changed" turn, so the prompt is written before the turn runs)
    token = current_principal.set(Caller(kind=PrincipalKind.AGENT, name=AGENT, role="implementer"))
    try:
        await service.call(
            "orch_ask",
            {
                "project": "udl",
                "task": "F-1.1",
                "title": "Which file?",
                "text": "There are two that fit.",
                "blocking": False,
            },
        )
    finally:
        current_principal.reset(token)
    mine = next(one for one in await questions_of(service) if one.title == "Which file?")
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer",
            {"project": "udl", "key": mine.key, "answer": "The one under src."},
        )
    finally:
        current_principal.reset(token)

    STARTED.clear()
    GO_ON.clear()
    SCRIPT["task:F-1.1"] = a_waiting_turn
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    loop = asyncio.ensure_future(found.run())
    try:
        await clock.advance(timedelta(seconds=31))
        async with asyncio.timeout(10):
            await STARTED.wait()
            # (a person stops it while the turn runs)
            token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
            try:
                await service.call("orch_stop_session", {"session": str(session.id)})
            finally:
                current_principal.reset(token)
            GO_ON.set()
        for _ in range(5):
            await clock.advance(timedelta(seconds=31))
    finally:
        GO_ON.set()  # (a failing test leaves no turn waiting)
        loop.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await loop

    left = await SessionRecords(AppRecords(service.db)).session(session.id)
    assert left is not None
    assert left.state is SessionState.ENDED


async def test_a_session_whose_pull_is_only_waiting_holds_no_turn(service: UDLService) -> None:
    """(The card's 5: while waiting on the checks or the bot, the agent holds no turn.)"""
    github = await service.apps.get("github")
    assert isinstance(github, FakeGitHub)
    github.pull = FakePull(
        checks=[{"name": "check", "status": "in_progress", "conclusion": None}],
        comments=[],
    )

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await clock.advance(timedelta(seconds=31))
        prompts = len(PROMPTS)
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert prompts == 1, "the first turn, and then no turn while the checks run"
    session = await session_of(service)
    assert session is not None
    assert session.state is SessionState.IDLE
    # (27.10: how long it waited on the checks is a `Wait`, not the agent's work — one record,
    # not one a tick, and it is still open.)
    task = (await tasks_of(service))["F-1.1"]
    waits = await WaitRecords(AppRecords(service.db)).open_for(task.id)
    assert [one.wait_kind for one in waits] == ["ci"]


async def test_a_pull_only_waiting_on_the_bot_asks_once_how_long_to_wait(
    service: UDLService,
) -> None:
    """(28.1: the rerun that would hurry the bot needs an operation udl lacks, so the wait is a
    question — asked once, not on every tick — and the session holds no turn meanwhile.)"""
    github = await service.apps.get("github")
    assert isinstance(github, FakeGitHub)
    github.pull = FakePull(
        checks=[{"name": "check", "status": "in_progress", "conclusion": None}],
        comments=[],
    )

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    asked_of = await questions_of(service)
    assert [one.title for one in asked_of] == ["The bot has not answered: how long to wait?"]
    assert not asked_of[0].blocking
    assert asked_of[0].to == "humans"


async def test_a_turn_s_live_events_publish(service: UDLService) -> None:
    """(The card's 3: the session started, each thing the runner said, and its end, as they
    happen — the types' own data, so a person watching gets them.)"""
    seen: list[AppEvent] = []
    subscription = AppEvents(service.bus, "orch", Orch.event_types()).subscribe("orch.*")
    taking = asyncio.create_task(collecting(subscription, seen))
    try:
        await ran(service, clock=FakeClock(), turns=1)
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        taking.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await taking

    names = [one.type for one in seen]
    assert "orch.session.started" in names
    assert "orch.session.event" in names
    assert "orch.session.ended" in names
    started = next(one for one in seen if one.type == "orch.session.started")
    assert started.data["host"] == "local"
    assert started.data["runner"] == "fake"


async def test_a_limit_publishes_and_holds_the_agent(service: UDLService) -> None:
    """(The card's 6: `RunnerLimited` sets `limited_until` and publishes `orch.agent.limited` —
    whose data has no task, which is why it was never published.)"""
    until = FakeClock().now() + timedelta(minutes=30)

    async def limited(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        yield RunnerLimited(until=until, message="out of quota")
        yield TurnEnded(reason=TurnReason.LIMITED, text="out of quota")

    SCRIPT[first_prompt_of("F-1.1")] = limited
    seen: list[AppEvent] = []
    subscription = AppEvents(service.bus, "orch", Orch.event_types()).subscribe("orch.*")
    taking = asyncio.create_task(collecting(subscription, seen))
    try:
        await ran(service, clock=FakeClock(), turns=1)
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        taking.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await taking

    assert "orch.agent.limited" in [one.type for one in seen]
    session = await session_of(service)
    assert session is not None
    assert session.state is SessionState.LIMITED
    assert session.limited_until == until


async def limits_of(service: UDLService) -> EventList:
    """The `orch.agent.limited` events udl has published."""
    found = await service.call("list_events", {"type": "orch.agent.limited", "limit": 20})
    assert isinstance(found, EventList)
    return found


async def test_a_task_that_names_its_repo_gets_that_one(service: UDLService) -> None:
    """(The task's `repo`, when it has one: a project may have several.)"""
    records = AppRecords(service.db)
    repos = RepoRecords(records)
    for name in ("api", "web"):
        await repos.save_repo(
            Repo(project="udl", name=name, github=f"yelrom0/{name}", account="github:yelrom0")
        )
    found, _ = await dispatcher(service)
    task = (await tasks_of(service))["F-1.1"].model_copy(update={"repo": "web"})

    got = await found._repo_of(task)  # pyright: ignore[reportPrivateUsage]  (the helper itself, tested here)
    assert got.name == "web"


async def test_a_task_whose_repo_is_not_there_says_so(service: UDLService) -> None:
    """(A repo the project does not have is a refusal that names it, not a fallback to the only
    one: the task's work would go somewhere else.)"""
    await RepoRecords(AppRecords(service.db)).save_repo(
        Repo(project="udl", name="api", github="yelrom0/api", account="github:yelrom0")
    )
    found, _ = await dispatcher(service)
    task = (await tasks_of(service))["F-1.1"].model_copy(update={"repo": "web"})

    with pytest.raises(DoesNotExist, match=r"no repo called 'web'"):
        await found._repo_of(task)  # pyright: ignore[reportPrivateUsage]  (the helper itself, tested here)


async def test_a_task_that_names_no_repo_needs_the_project_to_have_one(
    service: UDLService,
) -> None:
    """(The project's only repo, when the task names none; several is a question for the task's
    owner, and none is no repo at all.)"""
    records = AppRecords(service.db)
    repos = RepoRecords(records)
    found, _ = await dispatcher(service)
    task = (await tasks_of(service))["F-1.1"]

    with pytest.raises(PluginError, match=r"Which repo\? 'udl' has 0"):
        await found._repo_of(task)  # pyright: ignore[reportPrivateUsage]  (the helper itself, tested here)

    await repos.save_repo(
        Repo(project="udl", name="udl", github="yelrom0/udl", account="github:yelrom0")
    )
    got = await found._repo_of(task)  # pyright: ignore[reportPrivateUsage]  (the helper itself, tested here)
    assert got.name == "udl"

    await repos.save_repo(
        Repo(project="udl", name="docs", github="yelrom0/docs", account="github:yelrom0")
    )
    with pytest.raises(PluginError, match=r"Which repo\? 'udl' has 2"):
        await found._repo_of(task)  # pyright: ignore[reportPrivateUsage]  (the helper itself, tested here)


async def test_a_task_that_has_a_worktree_is_not_prepared_again(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(A session resumed on its task keeps the worktree it already has: `prepare()` is a git
    clone and a branch, not something to do twice.)"""

    async def never(**_: object) -> None:
        raise AssertionError("a task that has a worktree was prepared again")

    monkeypatch.setattr(space, "prepare", never)
    found, _ = await dispatcher(service)
    task = (await tasks_of(service))["F-1.1"]
    agent = await AgentRecords(AppRecords(service.db)).agent(AGENT)
    assert agent is not None
    already = task.model_copy(update={"worktree": "/work/F-1.1", "branch": "feat/F-1.1"})

    assert await found._prepared(already, agent) == already  # pyright: ignore[reportPrivateUsage]  (the helper itself, tested here)


async def test_a_task_with_no_worktree_is_prepared_on_its_agent_s_host(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(A task that has none gets one, from the project's only repo, on the agent's own host; the
    branch and the path are the task's from then on.)"""
    await RepoRecords(AppRecords(service.db)).save_repo(
        Repo(project="udl", name="udl", github="yelrom0/udl", account="github:yelrom0")
    )
    asked: list[tuple[str, str, str]] = []

    async def counted(task: Task, *, repo: Repo, host: Host, shell: HostShell) -> space.Worktree:
        asked.append((task.key, repo.name, host.name))
        return space.Worktree(
            host=host.name, path=f"/work/{task.key}", branch=f"feat/{task.key}", base="main"
        )

    monkeypatch.setattr(space, "prepare", counted)
    found, _ = await dispatcher(service)
    task = (await tasks_of(service))["F-1.1"]
    agent = await AgentRecords(AppRecords(service.db)).agent(AGENT)
    assert agent is not None

    made = await found._prepared(task, agent)  # pyright: ignore[reportPrivateUsage]  (the helper itself, tested here)
    assert asked == [("F-1.1", "udl", "local")]
    assert made.worktree == "/work/F-1.1"
    assert made.branch == "feat/F-1.1"


async def test_a_session_limited_twice_publishes_both_limits(service: UDLService) -> None:
    """(A limited turn is not a turn, so a key of the session and its turn number dropped the
    second limit: what tells one limit from the next is how many it has had.)"""
    clock = FakeClock()
    until = clock.now() + timedelta(minutes=30)

    async def limited(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        yield RunnerLimited(until=until, message="out of quota")
        yield TurnEnded(reason=TurnReason.LIMITED, text="out of quota")

    SCRIPT[first_prompt_of("F-1.1")] = limited
    await ran(service, clock=clock, turns=1)

    before = len((await limits_of(service)).events)
    assert before == 1

    # (the quota is back, and the same session is limited again on its resumed turn)
    SCRIPT["udl restarted; continue where you were."] = limited
    await clock.advance(timedelta(minutes=31))
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await clock.advance(timedelta(seconds=31))
            if len((await limits_of(service)).events) > before:
                break
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    seen = await limits_of(service)
    assert len(seen.events) > before, "the second limit was dropped as a repeat of the first"
    session = await session_of(service)
    assert session is not None
    assert session.limits > 1


async def test_the_orch_settings_a_session_is_given_are_file_only(service: UDLService) -> None:
    """(A session's token is limited by the agent's own deny list: a hub URL or a deny list set
    through udl could widen it, so both are the config file's alone.)"""
    shown = await service.get_plugin_settings(PluginSettingsRequest(name="orch"))

    assert sorted(shown.file_only) == ["deny", "hub_url"]
    with pytest.raises(NotAllowed, match="deny"):
        await service.set_plugin_settings(
            SetPluginSettingsRequest(name="orch", values={"deny": []}, reset=[])
        )


# ---------------------------------------------------------------------------------------------
# The review's regressions: one per fix (the bot's N2)
# ---------------------------------------------------------------------------------------------


async def test_a_loop_survives_what_is_not_a_udl_error_and_the_others_carry_on(
    service: UDLService,
) -> None:
    """(A turn that raises something udl does not know is logged, and the loop carries on: the
    other agents' loops are not cancelled with it.)"""
    await two_agents(service)
    RAISES[first_prompt_of("F-1.1")] = RuntimeError("the harness fell over")
    SCRIPT["udl restarted; continue where you were."] = ready_turn("F-1.1")
    await scripted(service, "F-1.2")

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(30):
            every = await tasks_of(service)
            if every["F-1.2"].state == READY:
                break
            await clock.advance(timedelta(seconds=31))
        every = await tasks_of(service)
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert every["F-1.2"].state == READY, "the other agent's loop carried on"
    assert every["F-1.1"].state == READY, "and the loop that raised carried on, and retried"


async def test_a_turn_that_made_progress_starts_the_nudge_count_again(
    service: UDLService,
) -> None:
    """(The card's 3: a nudge is a turn that brought nothing new; a turn that said what changed
    is progress, and the count goes back to 0 — so `max_nudges` counts turns in a row, not in
    all. With `max_nudges` 1, a first turn and a change are not yet a nudge; the one after them
    is.)"""
    agents = AgentRecords(AppRecords(service.db))
    agent = await agents.agent(AGENT)
    assert agent is not None
    await agents.save_agent(agent.model_copy(update={"max_nudges": 1}))

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            every = await tasks_of(service)
            if every["F-1.1"].state == BLOCKED:
                break
            await clock.advance(timedelta(seconds=31))
        every = await tasks_of(service)
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert PROMPTS[1].startswith("What changed on F-1.1")
    assert sum(1 for one in PROMPTS if "F-1.1" in one) == 3, (
        "the first turn, the change, and then one nudge: the change started the count again"
    )
    assert every["F-1.1"].state == BLOCKED


async def test_the_live_session_events_do_not_wake_the_dispatcher(service: UDLService) -> None:
    """(The card's 3: a turn publishes as it runs, and waking on those would have the dispatcher
    read every pull again for every line a runner says.)"""
    github = await service.apps.get("github")
    assert isinstance(github, FakeGitHub)
    github.pull = FakePull(checks=[{"name": "check", "status": "in_progress", "conclusion": None}])

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await asyncio.sleep(0)
        before = github.reads
        live = AppEvents(service.bus, "orch", Orch.event_types())
        await live.emit(
            "orch.session.event",
            {
                "agent": AGENT,
                "session": "00000000-0000-0000-0000-000000000000",
                "task": "F-1.1",
                "turn": 1,
                "kind": "text_delta",
            },
            dedupe_key="a-live-event",
        )
        for _ in range(20):
            await asyncio.sleep(0)
        after_live = github.reads

        await live.emit(
            "orch.task.ready",
            {"project": "udl", "task": "F-1.1", "pull": PULL, "sha": HEAD},
            dedupe_key="a-real-one",
        )
        for _ in range(20):
            await asyncio.sleep(0)
        after_wake = github.reads
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert after_live == before, "a live session event wakes nobody"
    assert after_wake > after_live, "another one does"


def test_what_a_bot_says_that_carries_backticks_stays_inside_its_fence() -> None:
    """(What comes from outside is data: text with its own fence cannot close udl's.)"""
    said = "a comment that quotes code:\n```\nrm -rf /\n```\nand ends"
    shown = fenced("claude[bot] says", said)

    assert shown.startswith("````claude[bot] says\n")
    assert shown.endswith("\n````")
    assert said in shown


async def test_a_session_s_task_is_the_one_in_its_own_project(service: UDLService) -> None:
    """(Two projects may each have a task `F-1.1`: a session is never moved to the other's — its
    own is `in_progress`, and the other one is `queued`, which would end it.)"""
    records = AppRecords(service.db)
    await ProjectRecords(records).save_project(Project(name="other", title="Other"))
    await FeatureRecords(records).save_feature(Feature(project="other", key="F-1", title="It"))
    tasks = TaskRecords(records)
    there = Task(project="other", feature="F-1", key="F-1.1", title="Theirs", assignee=AGENT)
    await tasks.save_task(there)
    await tasks.move(there, QUEUED, by=Principal(kind="system", role="assign"))
    await tasks.move(there, IN_PROGRESS, by=Principal(kind="system", role="start"))

    sessions = SessionRecords(records)
    made = await sessions.save_session(
        AgentSession(
            agent=AGENT,
            project="other",
            task="F-1.1",
            ref=SessionRef(runner="fake", id="fake-1", cwd="/work/F-1.1", host="local"),
            state=SessionState.RUNNING,
        )
    )
    STORE.specs["fake-1"] = SessionSpec(cwd="/work/F-1.1")

    await ran(service, clock=FakeClock())

    again = await sessions.session(made.id)
    assert again is not None
    assert again.project == "other"
    assert PROMPTS[0] == "udl restarted; continue where you were."
    # (its own project's task is the one it is told about: `Theirs` has no pull link, while
    # udl's `It 1` has one, so what it was told says which it read)
    assert again.last_text == "it has no pull yet"
    assert any("Theirs" in one for one in PROMPTS)


async def test_one_agent_s_token_is_kept_across_its_sessions(service: UDLService) -> None:
    """(Issuing a token rotates it: a second session must not take the first one's away.)"""
    await scripted(service, "F-1.1")
    await scripted(service, "F-1.2")

    await ran(service, clock=FakeClock(), ready=True)

    tokens = {
        spec.mcp.token.get_secret_value()
        for spec in STORE.specs.values()
        if spec.mcp is not None and spec.mcp.token is not None
    }
    assert len(STORE.specs) == 2, "two sessions, one agent"
    assert len(tokens) == 1, "the same token for both"


async def test_with_no_hub_url_a_session_is_given_this_machine_s_mcp(service: UDLService) -> None:
    """(A session with nowhere to call could call nothing, and so never finish a task.)"""
    await ran(service, clock=FakeClock())

    spec = next(iter(STORE.specs.values()))
    assert spec.mcp is not None
    assert spec.mcp.url == f"http://127.0.0.1:{service.settings.api.port}/mcp"


async def test_a_good_turn_starts_the_failure_count_again(service: UDLService) -> None:
    """(The card's 6: a failure is retried; a turn that works is not still failing.)"""
    hiccups = 0

    async def flaky(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        nonlocal hiccups
        if hiccups == 0:
            hiccups += 1
            yield RunnerFailed(message="a hiccup")
            yield TurnEnded(reason=TurnReason.ERROR, text="a hiccup")
            return
        yield TextDelta(text="carrying on")

    SCRIPT[first_prompt_of("F-1.1")] = flaky

    await ran(service, clock=FakeClock(), turns=2)

    session = await session_of(service)
    assert session is not None
    assert session.failures == 0
    assert session.state is not SessionState.FAILED


async def test_a_backoff_does_not_hold_a_turn_s_place(service: UDLService) -> None:
    """(The card's 6: a retry waits outside `max_agents_running`, so another agent turns.)"""
    await two_agents(service)

    async def broken(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        yield RunnerFailed(message="the harness fell over")
        yield TurnEnded(reason=TurnReason.ERROR, text="the harness fell over")

    SCRIPT[first_prompt_of("F-1.1")] = broken
    await scripted(service, "F-1.2")

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock, settings=OrchSettings(max_agents_running=1))
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(10):
            await clock.advance(timedelta(seconds=31))
        every = await tasks_of(service)
        sleeping = clock.sleepers
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert every["F-1.2"].state == READY, "the other agent turned while the first was backing off"
    assert sleeping >= 1, "and the first is still waiting"


async def test_the_transcripts_older_than_session_days_are_pruned(tmp_path: Path) -> None:
    """(The card's 3: a transcript is kept `session_days`, and a udl that runs for weeks prunes on
    a tick as well as when it starts.)"""
    clock = FakeClock()  # (the database stamps with the service's clock: the test moves it)
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
        clock=clock,
    ) as running:
        SERVICE.append(running)
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        await AgentRecords(records).save_agent(
            Agent(name=AGENT, title=AGENT, runner="fake", host="local")
        )
        tasks = TaskRecords(records)
        task = Task(project="udl", feature="F-1", key="F-1.1", title="It 1", assignee=AGENT)
        await tasks.save_task(task)
        await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))
        sessions = SessionRecords(records)
        made = await sessions.save_session(
            AgentSession(
                agent=AGENT,
                project="udl",
                task="F-1.1",
                ref=SessionRef(runner="fake", id="fake-1", cwd="/work/F-1.1", host="local"),
            )
        )
        stale = await sessions.save_event(
            SessionEvent(agent=AGENT, session=made.id, turn=1, event=TextDelta(text="stale"))
        )
        await clock.advance(timedelta(days=2))
        kept = await sessions.save_event(
            SessionEvent(agent=AGENT, session=made.id, turn=2, event=TextDelta(text="kept"))
        )

        found, _ = await dispatcher(running, clock=clock, settings=OrchSettings(session_days=1))
        running_task = asyncio.ensure_future(found.run())
        try:
            for _ in range(20):
                await asyncio.sleep(0)
            left = {one.id for one in await sessions.transcript(made.id)}

            # (one that is old enough, stored after the start-up prune: the tick prunes it)
            later = await sessions.save_event(
                SessionEvent(agent=AGENT, session=made.id, turn=3, event=TextDelta(text="old"))
            )
            await clock.advance(PRUNE_EVERY + timedelta(days=2))
            after = {one.id for one in await sessions.transcript(made.id)}
        finally:
            running_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running_task

    assert stale.id not in left, "forgotten when the dispatcher started"
    assert kept.id in left
    assert later.id not in after, "forgotten on the tick, in a udl that keeps running"


async def test_a_pull_that_cannot_be_read_is_fenced_like_the_bot_s_text(
    service: UDLService,
) -> None:
    """(Text from outside is data: an error from GitHub is fenced and labelled as well.)"""
    github = await service.apps.get("github")
    assert isinstance(github, FakeGitHub)
    github.fails = PluginFailed("the checks' log had ``` in it")

    await ran(service, clock=FakeClock(), turns=2)

    told = PROMPTS[1]
    assert told.startswith("What changed on F-1.1: its pull could not be read:")
    assert "````udl\nthe checks' log had ``` in it\n````" in told


async def test_two_things_said_alike_in_one_turn_are_two_events(service: UDLService) -> None:
    """(The bus drops a repeat by its key: a key made from what was said would drop the second
    `"."` of a turn, or the second time `make check` started.)"""

    async def twice(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        yield TextDelta(text=".")
        yield TextDelta(text=".")
        yield TurnEnded(reason=TurnReason.DONE, text="done.")

    SCRIPT[first_prompt_of("F-1.1")] = twice

    await ran(service, clock=FakeClock(), turns=2)

    sessions = SessionRecords(AppRecords(service.db))
    session = await session_of(service)
    assert session is not None
    transcript = await sessions.transcript(session.id)
    said = [one for one in transcript if one.event.kind == "text_delta"]
    assert len(said) >= 2, "the turn said the same thing twice"

    published = await events_of(service, "orch.session.event")
    deltas = [
        one
        for one in published
        if one.data["kind"] == "text_delta" and one.data["session"] == str(session.id)
    ]
    assert len(deltas) == len(said), "every one was published, none dropped as its twin's repeat"


async def test_a_restart_s_first_live_event_is_not_the_last_run_s_repeat(
    service: UDLService,
) -> None:
    """(A key that counted from 0 made each run's first events the last run's repeats, which the
    bus drops without a warning.)"""

    async def for_a_while(clock: FakeClock, ticks: int) -> list[EventInfo]:
        """Run a dispatcher for up to `ticks`, then stop it: what it published."""
        found, _ = await dispatcher(service, clock=clock)
        running = asyncio.ensure_future(found.run())
        try:
            for _ in range(ticks):
                await clock.advance(timedelta(seconds=31))
                if await events_of(service, "orch.session.event"):
                    break
        finally:
            running.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await running
        return await events_of(service, "orch.session.event")

    first = await for_a_while(FakeClock(), 1)
    assert first, "the first run published what its turn said"

    # (something left for the restarted udl to do: a second agent with a task of its own)
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(name="second", title="Second", runner="fake", host="local", brief="Build it.")
    )
    tasks = TaskRecords(records)
    later = Task(project="udl", feature="F-1", key="F-1.9", title="It 9", assignee="second")
    await tasks.save_task(later)
    await tasks.move(later, QUEUED, by=Principal(kind="system", role="assign"))

    # (a new dispatcher, as a restart makes: it publishes its own turns' events)
    second = await for_a_while(FakeClock(), 5)

    assert len(second) > len(first), "what the second run published was not dropped as a repeat"


async def test_a_token_stored_before_the_run_is_the_one_a_session_gets(
    service: UDLService,
) -> None:
    """(A token a person issued is the one its sessions get: issuing one rotates it.)"""
    stored = SecretStr("udl_agent_kept_by_a_person")
    await service.secrets.set(secret_ref(AGENT), stored)

    await ran(service, clock=FakeClock())

    spec = next(iter(STORE.specs.values()))
    assert spec.mcp is not None
    assert spec.mcp.token is not None
    assert spec.mcp.token.get_secret_value() == stored.get_secret_value()


# ---------------------------------------------------------------------------------------------
# The review's first round: a stall that blocks whatever question is open, and answers told once
# ---------------------------------------------------------------------------------------------


async def test_a_stall_blocks_the_task_even_with_an_earlier_question_open(
    service: UDLService,
) -> None:
    """(A task that already has an open question — the bot's wait, say — is still blocked when
    its agent stalls: the session is ended, and the task waits rather than looping.)"""
    await asked.ask(
        QuestionRecords(AppRecords(service.db)),
        project="udl",
        task="F-1.1",
        title="The bot has not answered: how long to wait?",
        asked_by=AGENT,
    )

    await ran(service, clock=FakeClock(), turns=5)

    waiting = (await tasks_of(service))["F-1.1"]
    assert waiting.state == BLOCKED
    assert waiting.blocked_on == "Q-1", "the stall waits on the question that was open"


async def test_a_blocking_question_that_cannot_be_written_is_not_dropped(
    service: UDLService,
) -> None:
    """(Dropping it would leave the task running and the loop nudging it for good: a blocking ask
    that fails is raised, so the session is not ended and the tick tries again.)"""

    class _CannotWriteQuestions(AppRecords):
        """A store that cannot write a question."""

        async def save[R: Record](self, record: R) -> R:
            if isinstance(record, Question):
                raise DatabaseError("the question could not be written")
            return await super().save(record)

        async def save_all[R: Record](self, records: Sequence[R]) -> list[R]:
            if any(isinstance(one, Question) for one in records):
                raise DatabaseError("the question could not be written")
            return await super().save_all(records)

    await TaskRecords(AppRecords(service.db)).delete_task("udl", "F-1.2")
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock, records=_CannotWriteQuestions(service.db))
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    task = (await tasks_of(service))["F-1.1"]
    assert task.state == IN_PROGRESS, "it is not blocked, because the question was not written"
    session = await session_of(service)
    assert session is not None
    assert session.state is not SessionState.ENDED, "the session is kept, and the tick retries"
    assert not await questions_of(service)


async def test_a_bot_wait_that_cannot_be_written_is_dropped(service: UDLService) -> None:
    """(The bot's wait blocks nothing: a store that cannot write it costs the question, not the
    tick.)"""

    class _CannotWriteQuestions(AppRecords):
        """A store that cannot write a question."""

        async def save_all[R: Record](self, records: Sequence[R]) -> list[R]:
            if any(isinstance(one, Question) for one in records):
                raise DatabaseError("the question could not be written")
            return await super().save_all(records)

    github = await service.apps.get("github")
    assert isinstance(github, FakeGitHub)
    github.pull = FakePull(
        checks=[{"name": "check", "status": "in_progress", "conclusion": None}], comments=[]
    )
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock, records=_CannotWriteQuestions(service.db))
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert not await questions_of(service), "the question was not written, and nothing failed"
    session = await session_of(service)
    assert session is not None
    assert session.state is not SessionState.ENDED


async def one_tick(service: UDLService) -> None:
    """Run the dispatcher for one tick, so the prompts are the ones the first turn sent."""
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        await clock.advance(timedelta(seconds=31))
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running


async def test_an_answer_is_told_once(service: UDLService) -> None:
    """(An answer is delivered into the session that waited for it, and not again into the one
    after it: the question remembers it was delivered.)"""
    await TaskRecords(AppRecords(service.db)).delete_task("udl", "F-1.2")
    await ran(service, clock=FakeClock(), turns=5)
    asked_of = await questions_of(service)
    assert len(asked_of) == 1
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer",
            {"project": "udl", "key": asked_of[0].key, "answer": "Go on: it is one more file."},
        )
    finally:
        current_principal.reset(token)

    PROMPTS.clear()
    await one_tick(service)

    assert sum("these were answered" in one for one in PROMPTS) == 1, "told once, and once only"
    delivered = await QuestionRecords(AppRecords(service.db)).question("udl", asked_of[0].key)
    assert delivered is not None
    assert delivered.delivered, "the question remembers, so the session after this is not told"


async def test_an_answer_to_another_s_question_is_not_told(service: UDLService) -> None:
    """(The session is told the answers to its own questions, not to everyone's.)"""
    await TaskRecords(AppRecords(service.db)).delete_task("udl", "F-1.2")
    questions = QuestionRecords(AppRecords(service.db))
    theirs = await asked.ask(
        questions,
        project="udl",
        task="F-1.1",
        title="Their question",
        asked_by="ernie-d",
    )
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer", {"project": "udl", "key": theirs.key, "answer": "Theirs, answered."}
        )
    finally:
        current_principal.reset(token)

    PROMPTS.clear()
    await ran(service, clock=FakeClock(), turns=1)

    assert PROMPTS
    assert "Theirs, answered." not in PROMPTS[0]


async def test_a_non_blocking_answer_reaches_a_session_still_running(
    service: UDLService,
) -> None:
    """(The answer is delivered while the session is on the task, not only when it picks it up
    again: a session that went on is told at its next turn.)"""
    records = AppRecords(service.db)
    task = (await tasks_of(service))["F-1.1"]
    questions = QuestionRecords(records)
    mine = await asked.ask(
        questions, project="udl", task=task.key, title="Which repo?", asked_by=AGENT
    )
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer", {"project": "udl", "key": mine.key, "answer": "The `api` one."}
        )
    finally:
        current_principal.reset(token)
    await SessionRecords(records).save_session(
        AgentSession(
            agent=AGENT,
            project="udl",
            task=task.key,
            ref=SessionRef(runner="fake", id="ses_running", cwd="/work/F-1.1", host="local"),
            state=SessionState.RUNNING,
            turns=1,
        )
    )

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        await clock.advance(timedelta(seconds=31))
        for _ in range(20):
            await asyncio.sleep(0)
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert PROMPTS
    assert "these were answered" in PROMPTS[0]
    assert "The `api` one." in PROMPTS[0]
    delivered = await QuestionRecords(records).question("udl", mine.key)
    assert delivered is not None
    assert delivered.delivered


async def test_the_bot_s_wait_is_asked_once_however_it_was_answered(
    service: UDLService,
) -> None:
    """(The wait is asked once per task, not once per open question: an answer that says "wait"
    does not make the next tick ask again.)"""
    github = await service.apps.get("github")
    assert isinstance(github, FakeGitHub)
    github.pull = FakePull(
        checks=[{"name": "check", "status": "in_progress", "conclusion": None}], comments=[]
    )
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    asked_of = await questions_of(service)
    assert len(asked_of) == 1
    token = current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))
    try:
        await service.call(
            "orch_answer", {"project": "udl", "key": asked_of[0].key, "answer": "Wait an hour."}
        )
    finally:
        current_principal.reset(token)

    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert len(await questions_of(service)) == 1, "it was asked, and not asked again"


async def test_a_failed_session_on_a_task_a_person_blocked_ends(service: UDLService) -> None:
    """(A task a person blocked has no open question to block on: the reason is written where its
    history is, and the session is still ended — a second block would be refused.)"""
    tasks = TaskRecords(AppRecords(service.db))
    who = Principal(kind="system", role="block")

    async def blocked_then_broken(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.save_moved(found, transitions.move(found, BLOCKED, by=who), by=who)
        yield RunnerFailed(message="the harness fell over")
        yield TurnEnded(reason=TurnReason.ERROR, text="the harness fell over")

    SCRIPT[first_prompt_of("F-1.1")] = blocked_then_broken

    await ran(service, clock=FakeClock(), turns=3)

    session = await session_of(service)
    assert session is not None
    assert session.state is SessionState.ENDED, "it is ended, not left holding the agent"
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    notes = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, found.id)
    assert any("session failed" in one.text for one in notes), "the reason is written"


async def test_a_failed_session_on_a_task_blocked_on_another_question_ends(
    service: UDLService,
) -> None:
    """(Blocked on Q-2 with Q-1 still open: the failure is said on Q-1's thread and the session is
    ended, rather than a second block being attempted.)"""
    records = AppRecords(service.db)
    open_one = await asked.ask(
        QuestionRecords(records), project="udl", task="F-1.1", title="Which repo?", asked_by=AGENT
    )
    tasks = TaskRecords(records)
    who = Principal(kind="system", role="block")

    async def blocked_then_broken(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
        found = await tasks.task("udl", "F-1.1")
        assert found is not None
        await tasks.save_moved(found, transitions.move(found, BLOCKED, by=who, note="Q-2"), by=who)
        yield RunnerFailed(message="the harness fell over")
        yield TurnEnded(reason=TurnReason.ERROR, text="the harness fell over")

    SCRIPT[first_prompt_of("F-1.1")] = blocked_then_broken

    await ran(service, clock=FakeClock(), turns=3)

    session = await session_of(service)
    assert session is not None
    assert session.state is SessionState.ENDED
    thread = await QuestionRecords(records).thread(open_one)
    assert any("session failed" in one.text for one in thread), "the reason is said on its thread"


# ---------------------------------------------------------------------------------------------
# 27.8's done-when: a manager keeps two reports busy across four tasks, with no human
# ---------------------------------------------------------------------------------------------


async def a_manager_and_its_reports(service: UDLService) -> None:
    """`ernie-c` managing `deepseek-b` and `deepseek-a`, and four open tasks to give out."""
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    await agents.save_agent(
        Agent(
            name="ernie-c",
            title="ernie-c",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(assign=True, answer=True, review=True, plan=True),
            brief="Keep your reports busy.",
        )
    )
    await agents.save_agent(
        Agent(name="deepseek-a", title="deepseek-a", runner="fake", host="local", manager="ernie-c")
    )
    for name in (AGENT, "deepseek-a"):
        found = await agents.agent(name)
        assert found is not None
        await agents.save_agent(found.model_copy(update={"manager": "ernie-c"}))
    tasks = TaskRecords(records)
    for number in (3, 4):
        key = f"F-1.{number}"
        task = Task(project="udl", feature="F-1", key=key, title=f"It {number}")
        await tasks.save_task(task)
    links = LinkRecords(records)
    for number in (1, 2, 3, 4):
        await links.add(
            project="udl",
            owner_kind=OwnerKind.TASK,
            owner=f"F-1.{number}",
            kind="pull",
            url=PULL,
            state=PullState(head_sha=HEAD),
        )
    # (udl has read each pull at its head: a review is per head, and readiness compares them.)
    for number in (1, 2, 3, 4):
        for one in await links.for_owner("udl", OwnerKind.TASK, f"F-1.{number}"):
            if one.link_kind == "pull":
                await links.records.save(one.model_copy(update={"state": PullState(head_sha=HEAD)}))
    # (No review is saved here: a report's ready waits on its manager's OK (27.8), and the
    # manager's own turns are what give it.)


async def silent_turn(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
    """A turn that says nothing and does nothing."""
    yield TextDelta(text="nothing to do")


async def a_turn_that_finishes_the_task(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
    """A turn that says what it cost and says the task is ready (so the loop ends the session
    after the one turn)."""
    yield Usage(input_tokens=100, output_tokens=20)
    await ask("orch_ready", {"key": "F-1.1"})
    yield TurnEnded(reason=TurnReason.DONE, text="F-1.1 is ready.")


async def a_waiting_turn(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
    """A turn that starts, says it is working, and waits to be let go."""
    STARTED.set()
    yield TextDelta(text="working on it")
    await GO_ON.wait()
    yield TurnEnded(reason=TurnReason.DONE, text="done")


def a_report_s_turn(key: str) -> Turn:
    """A report's turn on `key`: it says it is ready, and says what it waits on when it cannot
    (its manager's review, 27.8)."""

    async def turn(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
        try:
            await ask("orch_ready", {"key": key})
        except NotAllowed as error:
            yield TextDelta(text=f"{key} waits: {error.message}")
            return
        yield TextDelta(text=f"{key} is ready.")

    return turn


def the_manager_s_turn() -> Turn:
    """The manager's turn: it reads what its reports need from its prompt, gives a report with
    nothing to do the next suggested task, and reviews what its reports have pushed."""

    async def turn(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
        # (As its token's principal: in udl a session's calls reach the MCP server, which
        # authenticates the agent's token — the fake harness does that here.)
        token = current_principal.set(
            Caller(kind=PrincipalKind.AGENT, name="ernie-c", role="manager")
        )
        try:
            async for event in _managing_turn():
                yield event
        finally:
            current_principal.reset(token)

    return turn


async def _managing_turn() -> AsyncIterator[AgentEvent]:
    """What the manager does with its news, as its turn."""
    said = MANAGER_PROMPT[0]
    for line in said.splitlines():
        if line.startswith("- assign: "):
            name = line.split()[2]
            suggested = await ask("orch_suggest", {"project": "udl", "agent": name})
            assert isinstance(suggested, Suggestions)
            if suggested.suggestions:
                key = suggested.suggestions[0].task
                await ask("orch_assign", {"project": "udl", "key": key, "agent": name})
                # (Its pull is green already, so it reads it and says so while the report
                # starts: a report's ready waits on its manager's OK, 27.8.)
                await ask(
                    "orch_review_task",
                    {
                        "project": "udl",
                        "key": key,
                        "verdict": "approve",
                        "reasons": "It does what its card says.",
                    },
                )
        elif line.startswith("- review: "):
            key = line.split()[3]
            await ask(
                "orch_review_task",
                {"project": "udl", "key": key, "verdict": "approve", "reasons": "It does it."},
            )
    yield TextDelta(text="done")


async def test_a_manager_keeps_two_reports_busy_across_four_tasks(service: UDLService) -> None:
    """(27.8's done-when: with all four powers on, a manager keeps two reports busy across four
    tasks without a human — it gives each one with an empty queue the next task, and approves
    what they push, so each task reaches `ready`.)"""
    await a_manager_and_its_reports(service)
    SCRIPT["manager"] = the_manager_s_turn()
    for key in ("F-1.1", "F-1.2", "F-1.3", "F-1.4"):
        SCRIPT[f"task:{key}"] = a_report_s_turn(key)

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(40):
            every = await tasks_of(service)
            if all(
                one.state == READY
                for key, one in every.items()
                if key.startswith("F-1.") and not key.startswith("F-1.0")
            ):
                break
            await clock.advance(timedelta(seconds=31))
        else:
            now = await tasks_of(service)
            raise AssertionError(
                f"never got there: {[(k, t.state, t.assignee) for k, t in now.items()]}"
                f" sent: {PROMPTS[-6:]}"
            )
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    every = {key: one for key, one in (await tasks_of(service)).items() if key != "managing.1"}
    assert sorted(every) == ["F-1.1", "F-1.2", "F-1.3", "F-1.4"]
    assert all(one.state == READY for one in every.values())
    assert {one.assignee for one in every.values()} == {AGENT, "deepseek-a"}, "both were kept busy"
    reviews = await ReviewRecords(AppRecords(service.db)).for_task("udl", "F-1.1")
    assert [one.verdict for one in reviews] == ["approve"]


async def test_an_idle_manager_gets_no_turn_and_its_own_work_does(
    service: UDLService,
) -> None:
    """(A manager with nothing to hear is not woken every tick — and its own queued task is
    worked like anyone's, rather than its standing task being picked and left in progress.)"""
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    await agents.save_agent(
        Agent(
            name="ernie-c",
            title="ernie-c",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(assign=True),
            brief="Keep your reports busy.",
        )
    )
    mine = await TaskRecords(records).task("udl", "F-1.1")
    assert mine is not None
    await TaskRecords(records).save_task(
        mine.model_copy(update={"assignee": "ernie-c", "state": QUEUED, "position": 0})
    )
    SCRIPT["task:F-1.1"] = a_report_s_turn("F-1.1")

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(10):
            one = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
            assert one is not None
            if one.state == READY:
                break
            await clock.advance(timedelta(seconds=31))
        else:
            now = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
            raise AssertionError(f"its own task never moved: {now}")
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert not [one for one in PROMPTS if "Here is what your reports need" in one]


async def test_the_same_news_is_not_told_to_a_manager_twice(service: UDLService) -> None:
    """(An item the manager does not act on is not sent again on every tick: a manager is told
    once, and again only when the news changes.)"""
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    await agents.save_agent(
        Agent(
            name="ernie-c",
            title="ernie-c",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(answer=True),
            brief="Answer what you can.",
        )
    )
    # (Its reports are paused, so they never work, stall or ask anything: the news is the
    # question below, and it does not change.)
    for one in await agents.agents():
        if one.name == "ernie-c":
            continue
        await agents.save_agent(one.model_copy(update={"manager": "ernie-c", "paused": True}))
    await QuestionRecords(records).save_question(
        Question(
            project="udl",
            key="Q-1",
            asked_by=AGENT,
            to="ernie-c",
            title="Which way?",
            task="F-1.1",
        )
    )
    SCRIPT["manager"] = silent_turn

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(6):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert len([one for one in PROMPTS if "Here is what your reports need" in one]) == 1


async def fails_to_start(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
    """A turn whose harness never started: nothing was said, so nothing was told."""
    raise PluginFailed("the harness did not start")
    yield TextDelta(text="")  # pragma: no cover  (it is an async generator)


async def test_a_turn_that_never_started_does_not_count_as_told(service: UDLService) -> None:
    """(A runner that refuses to start has told the manager nothing: what it was to hear is not
    marked told, so the next tick says it again rather than leaving it stuck.)"""
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    await agents.save_agent(
        Agent(
            name="ernie-c",
            title="ernie-c",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(answer=True),
            brief="Answer what you can.",
        )
    )
    for one in await agents.agents():
        if one.name == "ernie-c":
            continue
        await agents.save_agent(one.model_copy(update={"manager": "ernie-c", "paused": True}))
    await QuestionRecords(records).save_question(
        Question(
            project="udl",
            key="Q-1",
            asked_by=AGENT,
            to="ernie-c",
            title="Which way?",
            task="F-1.1",
        )
    )
    SCRIPT["manager"] = fails_to_start

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(3):
            await clock.advance(timedelta(seconds=31))
        told = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
        assert told is not None
        assert told.last_news == ""  # (nothing reached it, so nothing was counted)
        SCRIPT["manager"] = silent_turn
        for _ in range(4):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert len([one for one in PROMPTS if "Here is what your reports need" in one]) >= 2
    told = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
    assert told is not None
    assert told.last_news  # (told, once one turn got through)


async def test_a_limited_turn_does_not_count_the_news_as_told(service: UDLService) -> None:
    """(A harness that answers with a limit said something, but the manager heard none of the
    news: what it was to hear is not marked told, so it is said again once the limit lifts.)"""
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    await agents.save_agent(
        Agent(
            name="ernie-c",
            title="ernie-c",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(answer=True),
            brief="Answer what you can.",
        )
    )
    for one in await agents.agents():
        if one.name == "ernie-c":
            continue
        await agents.save_agent(one.model_copy(update={"manager": "ernie-c", "paused": True}))
    await QuestionRecords(records).save_question(
        Question(
            project="udl",
            key="Q-1",
            asked_by=AGENT,
            to="ernie-c",
            title="Which way?",
            task="F-1.1",
        )
    )
    until = FakeClock().now() + timedelta(minutes=30)

    async def limited(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
        yield RunnerLimited(until=until, message="out of quota")

    SCRIPT["manager"] = limited

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(3):
            await clock.advance(timedelta(seconds=31))
        told = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
        assert told is not None
        assert told.last_news == ""  # (a limit is not the news arriving)
        SCRIPT["manager"] = silent_turn
        await clock.advance(timedelta(minutes=31))  # (the limit has lifted)
        for _ in range(4):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert len([one for one in PROMPTS if "Here is what your reports need" in one]) >= 2
    told = await AgentRecords(AppRecords(service.db)).agent("ernie-c")
    assert told is not None
    assert told.last_news


# ---------------------------------------------------------------------------------------------
# 28.7: the merger's turn
# ---------------------------------------------------------------------------------------------


MERGED_KEY: str = "You are claude, udl's merger"
SPEC: str = "It draws the thing, and says so when it cannot."
"""The ready pull's task spec (28.7: the merger reviews the diff against it)."""
"""How a merger's prompt starts (28.7)."""


async def a_merger_and_a_ready_pull(service: UDLService, *, own: bool = False) -> None:
    """`claude` as udl's merger, auto merge on, and `F-1.1` ready with its pull read at `HEAD`.

    `own` makes the ready pull the merger's own task (what needs a review bot, or a person)."""
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    await agents.save_agent(
        Agent(
            name="claude",
            title="Claude",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(assign=True, answer=True, review=True, plan=True),
            brief="Merge what is ready.",
        )
    )
    tasks = TaskRecords(records)
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.save_all(
        [
            found.model_copy(
                update={
                    "assignee": "claude" if own else AGENT,
                    "spec": SPEC,
                    "state": IN_PROGRESS,
                }
            )
        ]
    )
    found = await tasks.task("udl", "F-1.1")
    assert found is not None
    await tasks.move(found, READY, by=Principal(kind="system", role="ready"))
    links = LinkRecords(records)
    for one in await links.for_owner("udl", OwnerKind.TASK, "F-1.1"):
        if one.link_kind == "pull":
            await links.records.save(one.model_copy(update={"state": PullState(head_sha=HEAD)}))
    projects = ProjectRecords(records)
    project = await projects.project("udl")
    assert project is not None
    await projects.save_project(project.model_copy(update={"auto_merge": True, "merger": "claude"}))


def the_merger_s_turn() -> Turn:
    """The merger's turn (28.7): it reviews each pull its prompt lists, then merges it."""

    async def turn(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
        token = current_principal.set(
            Caller(kind=PrincipalKind.AGENT, name="claude", role="manager")
        )
        try:
            said = MERGER_PROMPT[0]
            for line in said.splitlines():
                found = _A_NUMBERED_PULL.match(line)
                if found is None:
                    continue
                key, number, sha = found.groups()
                await ask(
                    "orch_review_task",
                    {
                        "project": "udl",
                        "key": key,
                        "verdict": "approve",
                        "reasons": "It does what its card says.",
                        "head": sha,
                    },
                )
                await ask(
                    "orch_merge",
                    {
                        "project": "udl",
                        "key": key,
                        "confirm": f"yelrom0/udl#{number}",
                        "sha": sha,
                    },
                )
            yield TextDelta(text="merged what was ready")
        finally:
            current_principal.reset(token)

    return turn


_A_NUMBERED_PULL = re.compile(r"^- ([A-Z][A-Z0-9_]*-[0-9.]+) \(.*?\) — .*/pull/(\d+) at (\w+)")
"""A line of the merger's prompt: the task, its pull's number and the sha it is at."""
MERGER_PROMPT: list[str] = [""]
"""The prompt the merger's running turn was given (a turn's body starts after `send`)."""


async def test_the_merger_s_turn_merges_a_ready_pull(service: UDLService) -> None:
    """(28.7's done-when: with auto merge on, the merger's turn is given the row's data — the
    task, its title, its pull's URL and head sha, its feature — it reviews the pull for that head
    and merges it, and the merge carries its name.)"""
    await a_merger_and_a_ready_pull(service)
    SCRIPT["manager"] = the_merger_s_turn()

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(40):
            one = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
            assert one is not None
            if one.state in (MERGED, DONE):
                break
            await clock.advance(timedelta(seconds=31))
        else:
            now = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
            raise AssertionError(f"never merged: {now} sent: {PROMPTS[-4:]}")
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    said = MERGER_PROMPT[0]
    assert said.startswith(MERGED_KEY)
    assert "F-1.1 (It 1)" in said
    assert f"{PULL} at {HEAD}" in said
    assert "feature F-1" in said
    assert f"Its spec: {SPEC}" in said
    assert "git -C /work/merging." in said, "the diff is read in the merger's own worktree"
    assert f"confirm=yelrom0/udl#7, sha={HEAD}" in said, "the call it is told to make is exact"
    one = await TaskRecords(AppRecords(service.db)).task("udl", "F-1.1")
    assert one is not None
    # (Its review is the merger's, so a project with nothing after its base sees it done.)
    assert one.state == DONE
    notes = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, one.id)
    assert any("Merged by claude (auto merge)" in note.text for note in notes)


async def _quiet(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
    """A turn that does nothing, and ends done (a merger's, when it acts on nothing)."""
    yield TextDelta(text="nothing to merge")
    yield TurnEnded(reason=TurnReason.DONE, text="nothing to merge")


async def test_the_merger_is_not_told_the_same_list_twice(service: UDLService) -> None:
    """(A list the merger did nothing with is not sent again on every tick.)"""
    await a_merger_and_a_ready_pull(service)
    SCRIPT["manager"] = _quiet

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(10):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert len([one for one in PROMPTS if one.startswith(MERGED_KEY)]) == 1


async def test_a_merger_s_own_task_with_no_review_bot_is_skipped(service: UDLService) -> None:
    """(Nobody reviews the reviewer: with no review bot, the merger's own ready pull is left to a
    person, and the list says so rather than asking it to review itself.)"""
    await a_merger_and_a_ready_pull(service, own=True)
    records = AppRecords(service.db)
    projects = ProjectRecords(records)
    project = await projects.project("udl")
    assert project is not None
    await projects.save_project(project.model_copy(update={"review_bot": None}))
    SCRIPT["manager"] = _quiet

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(10):
            if MERGER_PROMPT[0]:
                break
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert MERGER_PROMPT[0] == "", "nothing to act on is no turn"
    listed = await service.call("orch_merge_list", {"project": "udl"})
    assert isinstance(listed, MergeList)
    assert "waiting for a person: the merger's own task" in listed.ready[0].blockers
    one = await TaskRecords(records).task("udl", "F-1.1")
    assert one is not None
    assert one.state == READY, "a person's to merge, not the merger's"


async def test_a_stop_that_lands_while_the_loop_rests_ends_it(service: UDLService) -> None:
    """(Between turns a loop rests on its tick and on its wake, and ending both of those must
    not end the stop with them: a stop that landed there once left the loop resting again, on a
    clock nobody moves, and the dispatcher never came back.)"""
    SCRIPT["manager"] = _quiet

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    for _ in range(5):  # (let every loop take its first step, and come to rest)
        await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=5)


async def _the_merger_merges_the_first(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
    """One pull per turn: the first its prompt lists (28.7's queue moves on, one at a time)."""
    token = current_principal.set(Caller(kind=PrincipalKind.AGENT, name="claude", role="manager"))
    try:
        for line in MERGER_PROMPT[0].splitlines():
            found = _A_NUMBERED_PULL.match(line)
            if found is None:
                continue
            key, number, sha = found.groups()
            await ask(
                "orch_review_task",
                {
                    "project": "udl",
                    "key": key,
                    "verdict": "approve",
                    "reasons": "Fine.",
                    "head": sha,
                },
            )
            await ask(
                "orch_merge",
                {"project": "udl", "key": key, "confirm": f"yelrom0/udl#{number}", "sha": sha},
            )
            break
        yield TextDelta(text="merged one")
    finally:
        current_principal.reset(token)


async def a_stack_of_three(service: UDLService) -> None:
    """`claude` as udl's merger, auto merge on, and three ready pulls, each stacked on the one
    before it (28.7's done when)."""
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(
            name="claude",
            title="Claude",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(assign=True, answer=True, review=True, plan=True),
            brief="Merge what is ready.",
        )
    )
    tasks = TaskRecords(records)
    links = LinkRecords(records)
    for key, need in (("F-1.1", None), ("F-1.2", "F-1.1"), ("F-1.3", "F-1.2")):
        found = await tasks.task("udl", key)
        if found is None:
            await tasks.save_task(
                Task(
                    project="udl",
                    feature="F-1",
                    key=key,
                    title=f"It {key[-1]}",
                    assignee=AGENT,
                    needs=[need] if need else [],
                )
            )
            found = await tasks.task("udl", key)
            assert found is not None
            await links.add(
                project="udl", owner_kind=OwnerKind.TASK, owner=key, kind="pull", url=PULL
            )
        await tasks.save_all([found.model_copy(update={"assignee": AGENT, "state": IN_PROGRESS})])
        found = await tasks.task("udl", key)
        assert found is not None
        for one in await links.for_owner("udl", OwnerKind.TASK, key):
            if one.link_kind == "pull":
                await links.records.save(one.model_copy(update={"state": PullState(head_sha=HEAD)}))
        await tasks.move(found, READY, by=Principal(kind="system", role="ready"))
    projects = ProjectRecords(records)
    project = await projects.project("udl")
    assert project is not None
    await projects.save_project(project.model_copy(update={"auto_merge": True, "merger": "claude"}))


async def test_the_merger_takes_a_stack_of_three_by_itself(service: UDLService) -> None:
    """(28.7's done when: on the fakes, a reviewer merger takes a stack of three pulls from Ready
    to merge to merged, bottom first, with nobody else acting.)"""
    await a_stack_of_three(service)
    SCRIPT["manager"] = _the_merger_merges_the_first

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(80):
            every = await tasks_of(service)
            if all(every[key].state in (MERGED, DONE) for key in ("F-1.1", "F-1.2", "F-1.3")):
                break
            await clock.advance(timedelta(seconds=31))
        else:
            every = await tasks_of(service)
            raise AssertionError(f"never all merged: {every} said: {PROMPTS[-3:]}")
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    every = await tasks_of(service)
    assert all(every[key].state in (MERGED, DONE) for key in ("F-1.1", "F-1.2", "F-1.3"))
    # (Bottom first, one at a time: each prompt is the one pull the merger is on, and the first
    # is the stack's bottom.)
    said = [one for one in PROMPTS if one.startswith(MERGED_KEY)]
    assert [one.group(1) for one in map(_A_NUMBERED_PULL.match, said[0].splitlines()) if one] == [
        "F-1.1"
    ], f"the stack's bottom is first: {said[0]}"
    assert "- F-1.2 (It 2)" in said[1], f"then the next: {said[1]}"
    assert "- F-1.3 (It 3)" in said[2], f"then the last: {said[2]}"
    app = await service.apps.get("github")
    assert isinstance(app, FakeGitHub)
    assert len(app.merged) == 3


async def test_nothing_is_merged_with_auto_merge_off(service: UDLService) -> None:
    """(Auto merge off: no merger's turn, and the pull stays where it is for a person.)"""
    await a_merger_and_a_ready_pull(service)
    records = AppRecords(service.db)
    projects = ProjectRecords(records)
    project = await projects.project("udl")
    assert project is not None
    await projects.save_project(project.model_copy(update={"auto_merge": False}))
    SCRIPT["manager"] = the_merger_s_turn()

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(10):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert not [one for one in PROMPTS if one.startswith(MERGED_KEY)]
    one = await TaskRecords(records).task("udl", "F-1.1")
    assert one is not None
    assert one.state == READY


async def _changes_then_merge(_: FakeRunner, __: SessionRef) -> AsyncIterator[AgentEvent]:
    """Changes on the first pull it is told of, a merge of the next one (28.7's queue moves on)."""
    token = current_principal.set(Caller(kind=PrincipalKind.AGENT, name="claude", role="manager"))
    try:
        for line in MERGER_PROMPT[0].splitlines():
            found = _A_NUMBERED_PULL.match(line)
            if found is None:
                continue
            key, number, sha = found.groups()
            if key == "F-1.1":
                await ask(
                    "orch_review_task",
                    {
                        "project": "udl",
                        "key": key,
                        "verdict": "changes",
                        "reasons": "Its tests do not cover the empty case.",
                        "head": sha,
                    },
                )
                continue
            await ask(
                "orch_review_task",
                {
                    "project": "udl",
                    "key": key,
                    "verdict": "approve",
                    "reasons": "Fine.",
                    "head": sha,
                },
            )
            await ask(
                "orch_merge",
                {"project": "udl", "key": key, "confirm": f"yelrom0/udl#{number}", "sha": sha},
            )
            break
        yield TextDelta(text="asked changes, merged the next")
    finally:
        current_principal.reset(token)


async def test_the_merger_moves_on_after_asking_changes(service: UDLService) -> None:
    """(A `changes` verdict takes the task out of Ready, so the next one not stacked on it is
    prompted at once: the queue always moves on.)"""
    await a_merger_and_a_ready_pull(service)
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    later = await tasks.task("udl", "F-1.2")
    assert later is not None
    await tasks.save_all([later.model_copy(update={"assignee": AGENT, "state": IN_PROGRESS})])
    later = await tasks.task("udl", "F-1.2")
    assert later is not None
    links = LinkRecords(records)
    for one in await links.for_owner("udl", OwnerKind.TASK, "F-1.2"):
        if one.link_kind == "pull":
            await links.records.save(one.model_copy(update={"state": PullState(head_sha=HEAD)}))
    await tasks.move(later, READY, by=Principal(kind="system", role="ready"))
    SCRIPT["manager"] = _changes_then_merge

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(40):
            every = await tasks_of(service)
            if every["F-1.2"].state in (MERGED, DONE):
                break
            await clock.advance(timedelta(seconds=31))
        else:
            every = await tasks_of(service)
            raise AssertionError(f"never merged the next: {every} said: {PROMPTS[-3:]}")
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    every = await tasks_of(service)
    # (The verdict sent it back to its agent, 28.7; the dispatcher then gave that agent a turn,
    # and a turn that says nothing blocks the task on a question — so it is no longer ready.)
    assert every["F-1.1"].state in (IN_PROGRESS, BLOCKED)
    assert every["F-1.2"].state in (MERGED, DONE)


async def test_the_merger_s_task_is_in_the_project_it_merges(service: UDLService) -> None:
    """(A merger may work on more than one project: its standing task is in the one it is
    merging, not whichever of its projects came first.)"""
    await a_merger_and_a_ready_pull(service)
    records = AppRecords(service.db)
    projects = ProjectRecords(records)
    await projects.save_project(Project(name="aaa", title="Another"))
    agents = AgentRecords(records)
    claude = await agents.agent("claude")
    assert claude is not None
    await agents.save_agent(claude.model_copy(update={"projects": ["aaa", "udl"]}))
    SCRIPT["manager"] = the_merger_s_turn()

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(20):
            every = await tasks_of(service)
            if "merging.1" in every:
                break
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    every = await tasks_of(service)
    merging = every.get("merging.1")
    assert merging is not None, f"no standing merge task: {sorted(every)}"
    assert merging.project == "udl"
    assert merging.worktree, "a worktree to read the diff in"


async def test_the_merger_is_told_the_next_one_it_may_act_on(service: UDLService) -> None:
    """(The card: the next ready task that isn't stacked on the skipped one. The merger's own
    pull is a person's, and what is stacked on it waits with it: the one it is told of is the
    next that is neither.)"""
    await a_merger_and_a_ready_pull(service, own=True)
    records = AppRecords(service.db)
    projects = ProjectRecords(records)
    project = await projects.project("udl")
    assert project is not None
    await projects.save_project(project.model_copy(update={"review_bot": None}))
    tasks = TaskRecords(records)
    # (`F-1.2` is the harness's own queued task: it is stacked on the merger's own pull.)
    second = await tasks.task("udl", "F-1.2")
    assert second is not None
    await tasks.save_all([second.model_copy(update={"needs": ["F-1.1"]})])
    second = await tasks.task("udl", "F-1.2")
    assert second is not None
    await tasks.move(second, IN_PROGRESS, by=Principal(kind="system", role="start"))
    second = await tasks.task("udl", "F-1.2")
    assert second is not None
    await tasks.move(second, READY, by=Principal(kind="system", role="ready"))
    free = await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.3", title="It 3"))
    await tasks.move(free, IN_PROGRESS, by=Principal(kind="system", role="start"))
    free = await tasks.task("udl", "F-1.3")
    assert free is not None
    await LinkRecords(records).add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="F-1.3",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/3",
    )
    await tasks.move(free, READY, by=Principal(kind="system", role="ready"))
    SCRIPT["manager"] = _quiet

    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(10):
            if MERGER_PROMPT[0]:
                break
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    said = MERGER_PROMPT[0]
    assert "- F-1.3 (It 3)" in said, f"the next it may act on: {said}"
    row = said.split("This one is ready:")[1].split("Its diff")[0]
    assert "F-1.1" not in row, "the merger's own is a person's"
    assert "F-1.2" not in row, "and what is stacked on it waits with it"


# ---------------------------------------------------------------------------------------------
# A host in trouble holds its agent (27.13)
# ---------------------------------------------------------------------------------------------


def _a_host(*, io: float) -> HostHealth:
    """A host whose numbers are all fine but its I/O pressure."""
    return HostHealth(
        host="local",
        memory_available=8 * 1024**3,
        io_pressure=io,
        at=datetime(2026, 11, 24, 12, tzinfo=UTC),
    )


Breach = Literal["act_and_tell", "tell", "act"]


class _AHost:
    """What a host says about itself, as the check asks it: what the test set, and how often."""

    def __init__(self, health: HostHealth) -> None:
        self.health = health
        self.asked = 0
        self.fails = False
        """Whether the read raises, as a host whose `df` is missing does."""

    async def __call__(self, name: str) -> HostHealth:
        self.asked += 1
        if self.fails:
            raise RuntimeError("df: not found")
        return self.health


def _health_over(service: UDLService, said: _AHost, *, on_breach: Breach = "act") -> Health:
    """The check over `service`, reading what `said` says, holding the host when it acts."""
    return Health(
        records=AppRecords(service.db),
        db=service.db,
        clock=service.clock,
        settings=HealthSettings(on_breach=on_breach),
        reading=said,
    )


async def test_a_host_in_trouble_holds_its_agent_s_turn(service: UDLService) -> None:
    """(A machine with no room left must not get a gate or a turn: the check says so, and the
    loop holds the agent while it does.)"""
    await scripted(service, "F-1.1")
    said = _AHost(_a_host(io=90.0))
    clock = FakeClock()
    found, _ = await dispatcher(
        service, clock=clock, health=_health_over(service, said, on_breach="act")
    )
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(4):
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert STORE.turns == 0
    assert PROMPTS == []
    assert said.asked >= 1


async def test_a_check_that_fails_does_not_hold_the_agent(service: UDLService) -> None:
    """(A read that raises says nothing about the host: the agent goes by the issues stored, so a
    broken `df` on a healthy machine cannot stop every turn there.)"""

    said = _AHost(_a_host(io=1.0))
    said.fails = True

    await scripted(service, "F-1.1")
    clock = FakeClock()
    found, _ = await dispatcher(
        service, clock=clock, health=_health_over(service, said, on_breach="act")
    )
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(6):
            if STORE.turns:
                break
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert STORE.turns >= 1


async def test_a_turn_the_harness_is_running_is_resumed_even_on_a_held_host(
    service: UDLService,
) -> None:
    """(The card holds *new* turns and lets what runs finish: a session udl restarted mid-turn is
    resumed, not stranded, even if the host has since been found in trouble.)"""
    sessions = SessionRecords(AppRecords(service.db))
    made = await sessions.save_session(
        AgentSession(
            agent=AGENT,
            task="F-1.1",
            ref=SessionRef(runner="fake", id="fake-9", cwd="/work/F-1.1", host="local"),
            state=SessionState.RUNNING,
            turns=2,
        )
    )
    STORE.specs["fake-9"] = SessionSpec(cwd="/work/F-1.1")
    said = _AHost(_a_host(io=90.0))
    check = _health_over(service, said, on_breach="act")
    await check.check(["local"])
    assert await check.held("local") is True
    clock = FakeClock()
    found, _ = await dispatcher(service, clock=clock, health=check)
    running = asyncio.ensure_future(found.run())
    try:
        for _ in range(6):
            if PROMPTS:
                break
            await clock.advance(timedelta(seconds=31))
    finally:
        running.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await running

    assert PROMPTS[0] == "udl restarted; continue where you were."
    again = await sessions.session(made.id)
    assert again is not None
    assert again.turns == 3


async def test_a_host_that_gets_better_lets_its_agent_go(service: UDLService) -> None:
    said = _AHost(_a_host(io=90.0))
    check = _health_over(service, said, on_breach="act")
    await check.check(["local"])
    assert await check.held("local") is True

    said.health = _a_host(io=1.0)
    await check.check(["local"])

    assert await check.held("local") is False
