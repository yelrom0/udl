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
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

# Package Imports
import pytest
from pydantic import BaseModel, SecretStr

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from support.runners import FakeRunner, FakeRunnerOptions, Sessions, Turn
from udl.automations.models import EventList
from udl.clock import Clock
from udl.config import APISettings
from udl.core.models import PluginSettingsRequest, SetPluginSettingsRequest
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import NotAllowed
from udl.hosts.shell import HostShell
from udl.plugins.apps.base import (
    AppContext,
    AppEvent,
    AppEvents,
    AppPlugin,
    AppRecords,
    AppSettings,
    AppSubscription,
)
from udl.plugins.apps.orch.agents import SessionState
from udl.plugins.apps.orch.dispatcher import RULES, Dispatcher, first_prompt
from udl.plugins.apps.orch.models import (
    BLOCKED,
    IN_PROGRESS,
    QUEUED,
    READY,
    OwnerKind,
    Principal,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    AgentSession,
    Feature,
    FeatureRecords,
    LinkRecords,
    Project,
    ProjectRecords,
    SessionRecords,
    Task,
    TaskRecords,
)
from udl.plugins.apps.orch.settings import OrchSettings
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


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull (what readiness calls)."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.pull = FakePull()

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation(
                "get_pull", title="One pull", hints=OperationHints(read_only=True, open_world=True)
            )
        ]

    async def get_pull(self, request: GetPullRequest) -> FakePull:
        """The pull the test set (whatever number it asks for)."""
        return self.pull


# ---------------------------------------------------------------------------------------------
# A runner whose turns the test scripts
# ---------------------------------------------------------------------------------------------

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
        """Record the prompt, then send it as the fake does."""
        PROMPTS.append(prompt)
        return super().send(session, prompt)


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
) -> tuple[Dispatcher, AppContext]:
    """A dispatcher for `service`, with a test's own `prepare()` (a path, no git)."""
    made = clock if clock is not None else FakeClock()
    app = await service.apps.get("orch")
    assert isinstance(app, Orch)
    records = AppRecords(service.db)
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
    )
    return found, ctx


async def fake_prepare(task: Task, agent: Agent) -> Task:
    """A worktree for `task` on `agent`'s host: a path of its own, and its branch."""
    return task.model_copy(
        update={"worktree": f"/work/{task.key}", "branch": f"feat/{task.key}-{task.title}"}
    )


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
    PROMPTS.clear()
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


async def scripted(service: UDLService, key: str, *, prompt: str | None = None) -> None:
    """Script the turn that takes `key` to `ready`, for its first prompt (or `prompt`)."""
    task = await TaskRecords(AppRecords(service.db)).task("udl", key)
    assert task is not None
    SCRIPT[prompt if prompt is not None else first_prompt(task)] = ready_turn(key)


async def ask(name: str, request: dict[str, object]) -> object:
    """Call an operation of the test's service, as a scripted turn's tool call does."""
    assert SERVICE, "a turn ran with no service"
    return await SERVICE[0].call(name, request)


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


async def test_the_worktree_is_made_and_the_task_is_in_progress_before_the_turn(
    service: UDLService,
) -> None:
    await ran(service, clock=FakeClock())

    every = await tasks_of(service)
    assert every["F-1.1"].worktree == "/work/F-1.1"
    assert every["F-1.1"].branch == "feat/F-1.1-It 1"


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
    assert every["F-1.1"].blocked_on is not None
    assert "stalled" in every["F-1.1"].blocked_on
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
    assert "harness fell over" in (every["F-1.1"].blocked_on or "")


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
    assert session.waited_since is not None


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


async def test_the_orch_settings_a_session_is_given_are_file_only(service: UDLService) -> None:
    """(A session's token is limited by the agent's own deny list: a hub URL or a deny list set
    through udl could widen it, so both are the config file's alone.)"""
    shown = await service.get_plugin_settings(PluginSettingsRequest(name="orch"))

    assert sorted(shown.file_only) == ["deny", "hub_url"]
    with pytest.raises(NotAllowed, match="deny"):
        await service.set_plugin_settings(
            SetPluginSettingsRequest(name="orch", values={"deny": []}, reset=[])
        )
