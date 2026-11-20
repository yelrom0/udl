"""
----------------
- plugin.py -
----------------

Orch: udl's orchestrator (Phase 26), an app plugin (udl.apps) whose data is the work udl
tracks — projects and their repos, features and their tasks, links and notes.

Its records are in orch.records (a Project, a Repo, a Feature, a Task, a Note); its shapes are in
orch.models. Its queue operations are here (27.3), its issue_token (27.2) and the rest of its
operations come in 26.6: importing this module registers them, as importing any other app's
module does.
"""

# System Imports
import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, Protocol

# Package Imports
from pydantic import BaseModel, ValidationError

# Local Imports
from udl.automations.events import EventType
from udl.clock import Clock
from udl.core.operations import CLISpec, Operation, OperationHints
from udl.db.models import JsonObject
from udl.db.records import Record
from udl.errors import DoesNotExist, NotAllowed, PluginError, UDLError
from udl.plugins.apps.base import (
    AppContext,
    AppEvent,
    AppEvents,
    AppPlugin,
    AppRecords,
)
from udl.plugins.apps.orch import actions as acted
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch import suggest as suggested
from udl.plugins.apps.orch import transitions
from udl.plugins.apps.orch.agents import AgentRole
from udl.plugins.apps.orch.links import (
    LINK_CHANGED,
    LinkChanged,
    LinkRecords,
    event_url,
    pull_ref,
)
from udl.plugins.apps.orch.models import (
    READY,
    Action,
    Actions,
    ActionState,
    AssignTaskRequest,
    DropActionRequest,
    FinishActionRequest,
    ListActionsRequest,
    MyTaskNote,
    MyTaskRequest,
    MyTaskResponse,
    NoteKind,
    OwnerKind,
    Principal,
    PullReader,
    QueueEntry,
    QueueRequest,
    Readiness,
    ReadinessRequest,
    ReadyRequest,
    ReorderQueueRequest,
    SetActionRequest,
    Suggestions,
    SuggestRequest,
    TaskQueue,
    UnassignTaskRequest,
)
from udl.plugins.apps.orch.readiness import TASK_READY, Pull, TaskReady
from udl.plugins.apps.orch.readiness import readiness as assess
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.apps.orch.tokens import (
    IssueTokenRequest,
    IssueTokenResponse,
)
from udl.plugins.apps.orch.tokens import (
    issue_token as issue,
)
from udl.plugins.runners.manage import (
    Harnesses,
    InstallRunnerRequest,
    ListRunnersRequest,
    LoginAttempt,
    LoginReplyRequest,
    LoginStatus,
    LoginStatusRequest,
    LogoutRunnerRequest,
    RunnerInstalled,
    RunnerSetup,
    RunnerSignedOut,
    RunnerStatusList,
    SetupRunnerRequest,
    StartLoginRequest,
)

if TYPE_CHECKING:
    from udl.core.service import UDLService

__all__ = ["EventEmission", "Orch", "OrchContext", "Stopping", "Subscription"]

log = logging.getLogger(__name__)

_UDL: Final = Principal(kind="human", name="udl")
"""(Who udl is when it moves a task itself: a person, since the table lets only one move a task to
`ready` or `merged`.)"""


class Subscription(Protocol):
    """What orch's service reads of a subscription: the next event, as it is stored."""

    def __anext__(self) -> Awaitable[AppEvent]:
        """The next event (StopAsyncIteration when the subscription has ended)."""
        ...


class EventEmission(Protocol):
    """What orch's service needs of an app's events: publish one of its own."""

    async def emit(
        self,
        event_type: str,
        data: BaseModel | JsonObject,
        *,
        dedupe_key: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Publish `event_type` with `data`."""
        ...


class Stopping(Protocol):
    """What stops orch's service: udl's `asyncio.Event` (and a test's flag, which is read the
    same way)."""

    def is_set(self) -> bool:
        """Whether it has been set."""
        ...

    async def wait(self) -> object:
        """Wait until it is set (what it gives is its own business)."""
        ...


class OrchContext(Protocol):
    """What orch's service reads of its AppContext: its records, its events, the service to call
    `github_get_pull` on, and what stops it.

    It is a Protocol so the service can be run in a test with a fake of each, without a whole
    AppContext (which needs a service, a secret store, a clock and settings)."""

    records: AppRecords
    events: AppEvents
    stop: Stopping

    @property
    def service(self) -> PullReader:
        """The service to call `github_get_pull` on."""
        ...


_RUNNERS: tuple[str, ...] = ("orch", "runners")
"""The CLI words a harness's operations live under: `udl orch runners [install]`."""

__all__ = ["Orch"]


class Orch(AppPlugin[OrchSettings]):
    """udl's orchestrator: the projects and repos it tracks, and the agents that work them.

    It brings its records (a Project, a Repo) and its shapes (orch.models); its queue operations
    are here (27.3), the rest are 26.6's, and its service the dispatcher of Phase 27."""

    name = "orch"
    title = "Orchestrator"
    summary = "The work udl tracks: projects, repos, features, tasks, links and notes."
    settings_model = OrchSettings
    # (Its UI is Phase 29's, a udl.ui plugin's manifest; until then the generated page.)

    @classmethod
    def record_kinds(cls) -> Sequence[type[Record]]:
        """The kinds of record orch defines: a project, a repo, a feature, a task, a note and the
        counter their keys come from (26.2-26.5), a link (26.4), an agent, its session and the
        events of it (27.1), and the tokens its agents call udl with (27.2)."""
        return (
            stored.Project,
            stored.Repo,
            stored.Feature,
            stored.Task,
            stored.Note,
            stored.Counter,
            stored.Link,
            stored.Agent,
            stored.AgentSession,
            stored.SessionEvent,
            stored.AgentToken,
            stored.HumanAction,
        )

    async def issue_token(
        self, request: IssueTokenRequest, *, service: UDLService
    ) -> IssueTokenResponse:
        """Issue a new token for the agent called `agent` (rotating the one it had, which stops
        working at once). The token is given here, once, and kept in udl's secret store, so the
        dispatcher can hand it to the agent's runner.

        Raises NotAllowed while the server has no `api.api_key`: without one every request is
        the owner's, so a token would limit nothing."""
        return IssueTokenResponse(agent=request.agent, token=await issue(service, request.agent))

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        """The events orch publishes: its links' changes (26.4), and its tasks' from 26.6 on."""
        return (LINK_CHANGED, TASK_READY)

    def __init__(
        self, settings: OrchSettings, *, clock: Clock, records: AppRecords | None = None
    ) -> None:
        super().__init__(settings, clock=clock, records=records)
        self._harnesses: Harnesses | None = None

    @property
    def harnesses(self) -> Harnesses:
        """The harnesses of every host (25.11): what its runner operations act on. One manager
        per app, so a relayed sign-in outlives the call that started it."""
        if self._harnesses is None:
            service = self.service
            if service is None:
                raise PluginError("The orch app is not running on a service")
            self._harnesses = Harnesses(service)
        return self._harnesses

    @classmethod
    def operations(cls) -> list[Operation]:
        """The operations it adds: the queue's (27.3), what to do next (27.4), the agent token's
        (27.2) and the harnesses' (25.11), each one of its methods (the rest are 26.6's)."""
        return [
            cls.operation(
                "issue_token",
                title="Issue an agent's token",
                hints=OperationHints(admin=True),
            ),
            cls.operation(
                "assign",
                title="Assign a task",
                hints=OperationHints(idempotent=True),
            ),
            cls.operation(
                "reorder_queue",
                title="Reorder a queue",
                hints=OperationHints(idempotent=True),
            ),
            cls.operation("unassign", title="Unassign a task"),
            cls.operation(
                "get_queue",
                title="An agent's queue",
                hints=OperationHints(read_only=True),
            ),
            cls.operation(
                "my_task",
                title="What an agent is on",
                hints=OperationHints(read_only=True),
            ),
            cls.operation(
                "suggest",
                title="What to do next",
                hints=OperationHints(read_only=True),
            ),
            cls.operation("set_action", title="Add a human action"),
            cls.operation(
                "list_actions",
                title="What is to be done",
                hints=OperationHints(read_only=True),
            ),
            cls.operation("finish_action", title="Finish a human action"),
            cls.operation(
                "drop_action",
                title="Drop a human action",
                hints=OperationHints(idempotent=True),
            ),
            cls.operation(
                "readiness",
                title="Whether a pull may be merged",
                hints=OperationHints(read_only=True, open_world=True),
            ),
            cls.operation(
                "ready",
                title="Mark a task's pull ready",
                hints=OperationHints(open_world=True),
            ),
            cls.operation(
                "list_runners",
                title="List the harnesses",
                hints=OperationHints(read_only=True, open_world=True),
                cli=CLISpec(path=_RUNNERS, options={"host": "--host"}),
            ),
            cls.operation(
                "runner_install",
                title="Install or update a harness",
                hints=OperationHints(admin=True, open_world=True),
                cli=CLISpec(
                    path=(*_RUNNERS, "install"),
                    arguments=("host", "runner"),
                    options={"version": "--version"},
                ),
            ),
            cls.operation(
                "runner_setup",
                title="Set a harness up",
                hints=OperationHints(admin=True, idempotent=True),
                cli=CLISpec(path=(*_RUNNERS, "setup"), arguments=("host", "runner")),
            ),
            cls.operation(
                "runner_login",
                title="Sign a harness in",
                hints=OperationHints(admin=True, open_world=True, mcp=False),
                cli=CLISpec(
                    path=(*_RUNNERS, "login"),
                    arguments=("host", "runner"),
                    options={"provider": "--provider"},
                ),
            ),
            cls.operation(
                "runner_login_reply",
                title="Answer a sign-in",
                hints=OperationHints(admin=True, open_world=True, mcp=False),
                cli=CLISpec(path=(*_RUNNERS, "login-reply"), arguments=("id", "text")),
            ),
            cls.operation(
                "runner_login_status",
                title="Check a sign-in",
                hints=OperationHints(admin=True, open_world=True, mcp=False),
                cli=CLISpec(path=(*_RUNNERS, "login-status"), arguments=("id",)),
            ),
            cls.operation(
                "runner_logout",
                title="Sign a harness out",
                hints=OperationHints(admin=True, destructive=True),
                cli=CLISpec(path=(*_RUNNERS, "logout"), arguments=("host", "runner", "confirm")),
            ),
        ]

    # -- the harnesses (25.11) -----------------------------------------------------------------

    async def list_runners(self, request: ListRunnersRequest) -> RunnerStatusList:
        """Every harness on every host: its version, whether it is signed in and on which plan,
        what it can run there, and what its own doctor says is wrong."""
        return await self.harnesses.all(request)

    async def runner_install(self, request: InstallRunnerRequest) -> RunnerInstalled:
        """Install or update a harness on a host: only its own fixed argv, and never while a
        session is running there."""
        return await self.harnesses.install(request)

    async def runner_setup(self, request: SetupRunnerRequest) -> RunnerSetup:
        """Write a harness's deny policies and its agent files on a host (idempotent), and set
        the provider keys it needs."""
        return await self.harnesses.setup(
            request, roles=[role.value for role in AgentRole], env=self._provider_keys(request)
        )

    async def runner_login(self, request: StartLoginRequest) -> LoginAttempt:
        """Sign a harness in, and relay what it asks: the URL it prints, then the code it takes.
        One attempt per host and runner; it ends after ten minutes."""
        return await self.harnesses.login(request)

    async def runner_login_reply(self, request: LoginReplyRequest) -> LoginAttempt:
        """Give a running sign-in what it asked for (a code, or a line)."""
        return await self.harnesses.reply(request)

    async def runner_login_status(self, request: LoginStatusRequest) -> LoginStatus:
        """How a sign-in is going, and whether it ended signed in."""
        return await self.harnesses.status(request)

    async def runner_logout(self, request: LogoutRunnerRequest) -> RunnerSignedOut:
        """Sign a harness out (its `confirm` names `<host>/<runner>`, so it is never a typo)."""
        return await self.harnesses.logout(request)

    def _provider_keys(self, request: SetupRunnerRequest) -> dict[str, str]:
        """The provider keys the service's environment is given when it is set up: none yet
        (the AI integration's secrets are 24.x's), so a setup writes only the policies and the
        agent files."""
        return {}

    async def serve(self, ctx: AppContext) -> None:
        """Orch's service: it keeps its pull links current (26.4), until the dispatcher
        (Phase 27) gives it more to do."""
        await self._keep_links(ctx.records, ctx.events, ctx.service, ctx.stop)

    async def _keep_links(
        self,
        records: AppRecords,
        events: AppEvents,
        service: PullReader,
        stop: Stopping,
    ) -> None:
        """Listen for `github.pr.*` events from now on, and for each one refresh every `pull`
        link whose URL is that pull through `github_get_pull`, store it, and publish
        `orch.link.changed`. It ends when `stop` is set (the lease going), or when the
        subscription ends (the runtime closing it: the next lease starts this again)."""
        links = LinkRecords(records)
        async with events.subscribe("github.pr.*") as subs:
            waiting = asyncio.ensure_future(asyncio.shield(stop.wait()))
            try:
                await self.follow(links, events, service, subs, waiting)
            finally:
                waiting.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiting

    async def follow(
        self,
        links: LinkRecords,
        events: EventEmission,
        service: PullReader,
        subs: Subscription,
        waiting: asyncio.Future[object],
    ) -> None:
        """Handle each event until `waiting` (the app told to stop) ends first."""
        while True:
            try:
                event = await _first(subs.__anext__(), waiting)
            except StopAsyncIteration:
                return
            if event is None:
                return
            try:
                url = event_url(event)
                if not url:
                    continue
                for found in await links.for_url(url):
                    if found.link_kind != "pull":
                        continue
                    link = await links.refresh(found, github=service)
                    if link.state != found.state:
                        # (Its own name: `link.changed` would be read as the whole name, and
                        # this app's is `orch.link.changed`. The key carries what changed, so a
                        # link that changed again is published again.)
                        await events.emit(
                            LINK_CHANGED.name,
                            LinkChanged(
                                owner_kind=link.owner_kind,
                                owner=link.owner,
                                url=link.url,
                            ),
                            dedupe_key=f"{link.id}:{link.state.model_dump_json()}",
                        )
            except (DoesNotExist, PluginError, UDLError, ValidationError) as error:
                # (A pull that has gone, or one udl cannot read: say so and go on. Anything
                # else is a bug, and the supervisor's backoff is what reports it.)
                log.warning("orch could not refresh a pull link: %s", error)

    # -- what to do next (27.4) ----------------------------------------------------------------

    async def suggest(self, request: SuggestRequest) -> Suggestions:
        """The tasks to do next, best first, each with the agent it suits and why.

        A task is a candidate once everything it needs is merged (or done) — or `ready`, when it
        may start on that task's branch instead. An active feature, the agent's last task and the
        tasks it unblocks all count. With no agent, each task is paired with the idle agent it
        suits best."""
        project = await self._project(request.project)
        # (An agent on a task of another project is not idle here: a task of this project
        # cannot know about another's, so they are read across every project.)
        on = [one for one in await self.records.list(stored.Task) if one.state == "in_progress"]
        found = suggested.suggest(
            await stored.TaskRecords(self.records).tasks(project),
            await stored.FeatureRecords(self.records).features(project),
            await stored.AgentRecords(self.records).agents(),
            agent=request.agent,
            limit=request.limit,
            busy=sorted({one.assignee for one in on if one.assignee}),
        )
        return Suggestions(project=project, suggestions=found)

    # -- what only a person can do (28.2) -------------------------------------------------------

    async def set_action(self, request: SetActionRequest) -> Action:
        """Add something only a person can do: now, after a task merges, or later.

        An action `after` a task names it, and becomes `now` when that task merges (28.3)."""
        project = await self._project(request.project)
        found = await acted.set_action(
            acted.ActionRecords(self.records),
            project=project,
            title=request.title,
            text=request.text,
            when=request.when,
            after=request.after,
            task=request.task,
            assignee=request.assignee,
        )
        return _action(found)

    async def list_actions(self, request: ListActionsRequest) -> Actions:
        """What is to be done, in the order it is due: now, then after, then later."""
        project = await self._project(request.project)
        found = await acted.ActionRecords(self.records).actions(
            project,
            state=request.state if request.state is not None else ActionState.OPEN,
            when=request.when,
            assignee=request.assignee,
        )
        return Actions(project=project, actions=[_action(one) for one in found])

    async def finish_action(self, request: FinishActionRequest) -> Action:
        """Finish something only a person could do: the result is written as a note on the task
        it is about, and that task, when it is a person's, is `merged`."""
        project = await self._project(request.project)
        found = await acted.finish(
            acted.ActionRecords(self.records),
            project=project,
            key=request.key,
            result=request.result,
        )
        return _action(found)

    async def drop_action(self, request: DropActionRequest) -> Action:
        """Drop something that is not going to be done, with the reason."""
        project = await self._project(request.project)
        found = await acted.drop(
            acted.ActionRecords(self.records),
            project=project,
            key=request.key,
            reason=request.reason,
        )
        return _action(found)

    # -- readiness (27.6) ----------------------------------------------------------------------

    async def readiness(self, request: ReadinessRequest, *, service: UDLService) -> Readiness:
        """Whether the pull of the task `key` may be merged (27.6): ready or not, why not, and
        the review bot's items it still lists. It reads: nothing changes."""
        project = await self._project(request.project)
        task, pull, why = await self._pull_of(service, project, request.key)
        if pull is None:
            return Readiness(ready=False, blockers=[why])
        return await self._assess(task, pull, project)

    async def ready(self, request: ReadyRequest, *, service: UDLService) -> Readiness:
        """Mark the task `key`'s pull ready: it moves to `ready`, with `note` as its progress
        note, and `orch.task.ready` is published.

        Raises NotAllowed with the blockers unless it is ready (27.6): not a draft, cleanly
        mergeable, its checks passed, the review bot's latest comment for this head clean, every
        task it needs merged, and no hold. A task with a hold is not ready however clean it is
        (it is built now and merged later, 28.3)."""
        project = await self._project(request.project)
        task, pull, why = await self._pull_of(service, project, request.key)
        if pull is None:
            raise NotAllowed(
                f"{request.key!r} cannot be ready: {why}",
                hint="Open its pull first (27.5), then ask again.",
            )
        found = await self._assess(task, pull, project)
        if not found.ready:
            raise NotAllowed(
                f"{request.key!r} is not ready: {'; '.join(found.blockers)}",
                hint="Fix what it says, then ask again.",
            )
        moved = transitions.move(task, READY, by=_UDL)
        note = stored.Note(
            owner_kind=OwnerKind.TASK,
            owner=task.id,
            note_kind=NoteKind.PROGRESS,
            text=request.note or f"Ready to merge: {pull.head_sha[:7]}.",
            author="udl",
        )
        await stored.TaskRecords(self.records).save_all([moved], also=[note])
        events = AppEvents(service.bus, self.name, self.event_types())
        await events.emit(
            TASK_READY.name,
            TaskReady(
                project=project,
                task=request.key,
                pull=await self._pull_url(project, request.key),
                sha=pull.head_sha,
            ),
            dedupe_key=f"{project}:{request.key}:{pull.head_sha}",
        )
        return found

    async def _pull_of(
        self, service: UDLService, project: str, key: str
    ) -> tuple[stored.Task, Pull | None, str]:
        """The task `key` of `project`, and its pull as readiness reads it (None and why when it
        has none), read through udl's own `github_get_pull`."""
        task = await stored.TaskRecords(self.records).task(project, key)
        if task is None:
            raise DoesNotExist(f"There is no task called {key!r} in {project!r}")
        links = await stored.LinkRecords(self.records).for_owner(project, OwnerKind.TASK, key)
        found = next((link for link in links if link.link_kind == "pull"), None)
        if found is None:
            return task, None, "it has no pull"
        ref = pull_ref(found.url)
        if ref is None:
            return task, None, f"its pull link is not a pull ({found.url})"
        repo, number = ref
        detail = await service.call("github_get_pull", {"repo": repo, "number": number})
        return task, Pull.model_validate(detail.model_dump()), ""

    async def _pull_url(self, project: str, key: str) -> str:
        """The URL of the task `key`'s pull, or "" when it has none."""
        links = await stored.LinkRecords(self.records).for_owner(project, OwnerKind.TASK, key)
        return next((link.url for link in links if link.link_kind == "pull"), "")

    async def _assess(self, task: stored.Task, pull: Pull, project: str) -> Readiness:
        """`readiness()` for `task`, with its project's review bot and every task's state."""
        found = await stored.ProjectRecords(self.records).project(project)
        if found is None:
            raise DoesNotExist(f"There is no project called {project!r}")
        tasks = {
            one.key: one.state for one in await stored.TaskRecords(self.records).tasks(project)
        }
        return assess(pull, bot=found.review_bot, task=task, tasks=tasks)

    # -- the queue (27.3) ----------------------------------------------------------------------

    async def assign(self, request: AssignTaskRequest) -> TaskQueue:
        """Assign a task to an agent: it joins that agent's queue (at its end, or at a position).

        A task another agent holds leaves that agent's queue, and a system note says who must
        stop. A person's task is assigned to `human:<name>` (28.2's To action), never to an
        agent. The answer is the agent's queue, in the order it will work it."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        await queues.assign(
            tasks, request.key, request.agent, project=project, position=request.position
        )
        return await self._queue(tasks, project=project, agent=request.agent)

    async def reorder_queue(self, request: ReorderQueueRequest) -> TaskQueue:
        """Set an agent's queue to exactly these keys, in this order.

        Every task on the queue is named once: one that is not on it is not the reorder's to
        move, and one left out would be lost. The answer is the queue as it now is."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        await queues.reorder(tasks, project=project, agent=request.agent, keys=request.keys)
        return await self._queue(tasks, project=project, agent=request.agent)

    async def unassign(self, request: UnassignTaskRequest) -> TaskQueue:
        """Take a task out of its agent's queue: it is open again, and nobody's.

        The answer is the queue it left, with no gap in it."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        task = await tasks.task(project, request.key)
        if task is None:
            raise DoesNotExist(f"There is no task called {request.key!r} in {project!r}")
        agent = task.assignee or ""
        await queues.unassign(tasks, project=project, key=request.key)
        return await self._queue(tasks, project=project, agent=agent)

    async def get_queue(self, request: QueueRequest) -> TaskQueue:
        """What an agent's queue is: its tasks, in the order it will work them.

        A person's own list is asked for the same way, by `human:<name>`."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        return await self._queue(tasks, project=project, agent=request.agent)

    async def my_task(self, request: MyTaskRequest) -> MyTaskResponse:
        """What an agent is to work next: the task it is in progress on, else the first on its
        queue, and nothing when it has none.

        The dispatcher asks this when it starts a session (27.7); an agent asks it when it is
        told to go on."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        found = await queues.next_task(tasks, project=project, agent=request.agent)
        if found is None:
            return MyTaskResponse()
        feature = await stored.FeatureRecords(self.records).feature(project, found.feature)
        project_record = await stored.ProjectRecords(self.records).project(project)
        notes = await stored.NoteRecords(self.records).notes("task", found.id)
        return MyTaskResponse(
            task=_entry(found),
            feature_spec="" if feature is None else feature.spec,
            spec=found.spec,
            rules="" if project_record is None else project_record.rules,
            notes=[
                MyTaskNote(note_kind=note.note_kind, text=note.text, author=note.author, at=note.at)
                for note in notes
            ],
        )

    async def _project(self, project: str | None) -> str:
        """The project an operation is of: `project`, or the only one udl has. Raises NotAllowed
        when there is none, or more than one and none is named."""
        if project is not None:
            return project
        projects = await stored.ProjectRecords(self.records).projects()
        if len(projects) == 1:
            return projects[0].name
        if not projects:
            raise NotAllowed(
                "There is no project to work in",
                hint="Add one first: a project is what features, tasks and queues are of.",
            )
        named = ", ".join(found.name for found in projects)
        raise NotAllowed(
            f"Which project? udl has {len(projects)} ({named})",
            hint="Name the project its task is in.",
        )

    async def _queue(self, tasks: stored.TaskRecords, *, project: str, agent: str) -> TaskQueue:
        """`agent`'s queue, as the operations answer with it."""
        found = await queues.queue_of(tasks, project=project, agent=agent)
        return TaskQueue(project=project, agent=agent, tasks=[_entry(task) for task in found])


def _action(found: stored.HumanAction) -> Action:
    """`found` as the operations answer with it."""
    return Action(
        key=found.key,
        project=found.project,
        title=found.title,
        text=found.text,
        when=found.when,
        after=found.after,
        task=found.task,
        assignee=found.assignee,
        state=found.state,
        result=found.result,
    )


def _entry(task: stored.Task) -> QueueEntry:
    """`task` as the queue's operations answer with it."""
    return QueueEntry(
        key=task.key,
        feature=task.feature,
        title=task.title,
        state=task.state,
        position=task.position,
        assignee=task.assignee,
        repo=task.repo,
    )


async def _first(waiting: Awaitable[object], stopped: asyncio.Future[object]) -> object | None:
    """The next event, or None when `stopped` is set first (the app is shutting down)."""
    waiter = asyncio.ensure_future(waiting)
    try:
        done, _ = await asyncio.wait({waiter, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if waiter in done:
            return waiter.result()
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter
        return None
    finally:
        waiter.cancel()
