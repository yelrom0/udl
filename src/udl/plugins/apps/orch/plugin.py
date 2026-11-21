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
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID

# Package Imports
from pydantic import BaseModel, ValidationError

# Local Imports
from udl.automations.events import EventType
from udl.clock import Clock
from udl.core.models import Deleted
from udl.core.operations import CLISpec, Operation, OperationHints
from udl.db.models import JsonObject
from udl.db.records import Record
from udl.errors import ConfigError, DoesNotExist, NotAllowed, PluginError, UDLError
from udl.hosts import LOCAL
from udl.interfaces import security
from udl.plugins.apps.base import (
    AppContext,
    AppEvent,
    AppEvents,
    AppPlugin,
    AppRecords,
)
from udl.plugins.apps.orch import actions as acted
from udl.plugins.apps.orch import merge as merged
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch import suggest as suggested
from udl.plugins.apps.orch import transitions
from udl.plugins.apps.orch import workspace as space
from udl.plugins.apps.orch.agents import AgentRole
from udl.plugins.apps.orch.dispatcher import Dispatcher
from udl.plugins.apps.orch.links import (
    LINK_CHANGED,
    LinkChanged,
    LinkRecords,
    canonical,
    event_url,
    pull_ref,
)
from udl.plugins.apps.orch.merge import after_close, after_merge, order
from udl.plugins.apps.orch.models import (
    CANCELLED,
    DONE,
    MERGED,
    MOVED,
    OPEN,
    READY,
    Action,
    Actions,
    ActionState,
    AddLinkRequest,
    AddNoteRequest,
    AssignTaskRequest,
    DeleteProjectRequest,
    DeleteRepoRequest,
    DropActionRequest,
    Feature,
    FeatureInfo,
    Features,
    FindLinksRequest,
    FinishActionRequest,
    GetFeatureRequest,
    GetProjectRequest,
    GetTaskRequest,
    Link,
    Links,
    ListActionsRequest,
    ListFeaturesRequest,
    ListProjectsRequest,
    ListTasksRequest,
    Merged,
    MergeList,
    MergeListRequest,
    MergePolicy,
    MergeRequest,
    MergeRow,
    MoveTaskRequest,
    MyTaskNote,
    MyTaskRequest,
    MyTaskResponse,
    NoteInfo,
    NoteKind,
    OwnerKind,
    Principal,
    Project,
    ProjectInfo,
    Projects,
    Promoted,
    PromoteRequest,
    PromotionList,
    PromotionRequest,
    PrStatusRequest,
    PullReader,
    PushRequest,
    PushResult,
    QueueEntry,
    QueueRequest,
    Readiness,
    ReadinessRequest,
    ReadyRequest,
    RemoveLinkRequest,
    ReorderQueueRequest,
    Repo,
    SetActionRequest,
    SetFeatureRequest,
    SetProjectRequest,
    SetRepoRequest,
    SetTaskRequest,
    SplitTaskRequest,
    Suggestions,
    SuggestRequest,
    Task,
    TaskInfo,
    TaskQueue,
    Tasks,
    UnassignTaskRequest,
    as_shape,
)
from udl.plugins.apps.orch.readiness import TASK_READY, Pull, TaskReady
from udl.plugins.apps.orch.readiness import readiness as assess
from udl.plugins.apps.orch.sessions import (
    AGENT_LIMITED,
    SESSION_ENDED,
    SESSION_EVENT,
    SESSION_FAILED,
    SESSION_STARTED,
)
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

_PROJECTS: Final = ("orch", "projects")
"""The CLI group the projects' commands are under (`udl orch projects`)."""
_REPOS: Final = ("orch", "repos")
"""The CLI group the repos' commands are under (`udl orch repos`)."""
_FEATURES: Final = ("orch", "features")
"""The CLI group the features' commands are under (`udl orch features`)."""
_TASKS: Final = ("orch", "tasks")
"""The CLI group the tasks' commands are under (`udl orch tasks`)."""
_LINKS: Final = ("orch", "links")
"""The CLI group the links' commands are under (`udl orch links`)."""

_UDL: Final = Principal(kind="system", role="ready")
"""(Who udl is when it moves a task itself: the system, in the role `ready` — not a person, who may
move a task anywhere, so an `open` or `blocked` task would be marked ready with its reason
cleared. The table allows `ready` from `in_progress` only.)"""


def _by(who: security.Principal | None) -> Principal:
    """Who a move or a note is by: the person at the keyboard when no interface set a caller
    (the CLI, a test), the agent when one is calling, and a person for the server's owner.

    A person may move a task anywhere (the table is for the agents, orch.transitions); an agent
    is held to its own tasks and to the roles its state allows."""
    if who is not None and who.is_agent:
        return Principal(kind="agent", name=who.name)
    return Principal(kind="human", name=f"human:{who.name if who is not None else 'owner'}")


def _listed(keys: Sequence[str]) -> str:
    """The keys as a person reads a list: one, `a and b`, or `a, b and c`."""
    if len(keys) == 1:
        return keys[0]
    return f"{', '.join(keys[:-1])} and {keys[-1]}"


_MERGED: Final = "github.pr.merged"
"""The event the GitHub watcher publishes for a pull that merged (25.5)."""

_CLOSED: Final = "github.pr.closed"
"""The event it publishes for one closed without merging."""

_REVIEWER: Final = Principal(kind="agent", name="reviewer", role="review")
"""The reviewer: what a promotion's `done` is (only a reviewer or a person reviews)."""


class Subscription(Protocol):
    """What orch's service reads of a subscription: the next event, as it is stored."""

    def __anext__(self) -> Awaitable[AppEvent]:
        """The next event (StopAsyncIteration when the subscription has ended)."""
        ...


class PullHappened(Protocol):
    """What orch's service calls when a pull it follows merged or closed (28.3): what a merge
    sets off is more than a link's refresh, so it is done with the whole service."""

    def __call__(self, event: AppEvent, url: str) -> Awaitable[None]:
        """`event` (a `github.pr.merged` or `github.pr.closed`) about the pull at `url`."""
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
        return (
            LINK_CHANGED,
            TASK_READY,
            SESSION_STARTED,
            SESSION_EVENT,
            SESSION_ENDED,
            SESSION_FAILED,
            AGENT_LIMITED,
        )

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
                "push",
                title="Push a task's work",
                hints=OperationHints(open_world=True),
            ),
            cls.operation(
                "pr_status",
                title="Whether a task's pull is ready",
                hints=OperationHints(read_only=True, open_world=True),
            ),
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
                "merge_list",
                title="What is ready to merge",
                hints=OperationHints(read_only=True, open_world=True),
            ),
            cls.operation(
                "merge",
                title="Merge a task's pull",
                hints=OperationHints(destructive=True, open_world=True, admin=True),
            ),
            cls.operation(
                "promotion",
                title="What a promotion would carry",
                hints=OperationHints(read_only=True),
            ),
            cls.operation("promote", title="Mark a promotion's tasks done"),
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
            cls.operation(
                "list_projects",
                title="List the projects",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=_PROJECTS),
            ),
            cls.operation(
                "get_project",
                title="A project and its repos",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=(*_PROJECTS, "show"), arguments=("name",)),
            ),
            cls.operation(
                "set_project",
                title="Create or edit a project",
                hints=OperationHints(admin=True),
                cli=CLISpec(path=(*_PROJECTS, "set")),
            ),
            cls.operation(
                "delete_project",
                title="Delete a project",
                hints=OperationHints(destructive=True, idempotent=True, admin=True),
                cli=CLISpec(path=(*_PROJECTS, "rm"), arguments=("name",)),
            ),
            cls.operation(
                "set_repo",
                title="Create or edit a repo",
                hints=OperationHints(admin=True),
                cli=CLISpec(path=(*_REPOS, "set")),
            ),
            cls.operation(
                "delete_repo",
                title="Delete a repo",
                hints=OperationHints(destructive=True, idempotent=True, admin=True),
                cli=CLISpec(path=(*_REPOS, "rm"), arguments=("name",)),
            ),
            cls.operation(
                "list_features",
                title="List a project's features",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=_FEATURES),
            ),
            cls.operation(
                "get_feature",
                title="A feature, its tasks, links and notes",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=(*_FEATURES, "show"), arguments=("key",)),
            ),
            cls.operation(
                "set_feature",
                title="Create or edit a feature",
                hints=OperationHints(idempotent=True),
                cli=CLISpec(path=(*_FEATURES, "set")),
            ),
            cls.operation(
                "list_tasks",
                title="List a project's tasks",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=_TASKS),
            ),
            cls.operation(
                "get_task",
                title="A task, its links, notes and questions",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=(*_TASKS, "show"), arguments=("key",)),
            ),
            cls.operation(
                "set_task",
                title="Create or edit a task",
                hints=OperationHints(idempotent=True),
                cli=CLISpec(path=(*_TASKS, "set")),
            ),
            cls.operation(
                "move_task",
                title="Move a task",
                cli=CLISpec(path=(*_TASKS, "move"), arguments=("key", "to")),
            ),
            cls.operation(
                "split_task",
                title="Split a task into several",
                cli=CLISpec(path=(*_TASKS, "split"), arguments=("key",)),
            ),
            cls.operation(
                "add_link",
                title="Link something to a task or a feature",
                cli=CLISpec(path=(*_LINKS, "add")),
            ),
            cls.operation(
                "remove_link",
                title="Unlink something",
                cli=CLISpec(path=(*_LINKS, "rm")),
            ),
            cls.operation(
                "find_links",
                title="What hangs on a URL",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=(*_LINKS, "find")),
            ),
            cls.operation(
                "add_note",
                title="Write a note on a task or a feature",
                cli=CLISpec(path=("orch", "note"), arguments=("owner", "text")),
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

    # -- the catalogue (26.6) ------------------------------------------------------------------

    async def list_projects(self, request: ListProjectsRequest) -> Projects:
        """Every project udl knows, by name."""
        found = await stored.ProjectRecords(self.records).projects()
        return Projects(projects=[as_shape(Project, one) for one in found])

    async def get_project(self, request: GetProjectRequest) -> ProjectInfo:
        """A project, with its repos."""
        projects = stored.ProjectRecords(self.records)
        found = await projects.require(request.name)
        repos = await stored.RepoRecords(self.records, projects).repos(found.name)
        return ProjectInfo(
            project=as_shape(Project, found), repos=[as_shape(Repo, one) for one in repos]
        )

    async def set_project(self, request: SetProjectRequest) -> Project:
        """Create or edit a project, by its name: what is not given is kept (and a new one needs
        a title)."""
        projects = stored.ProjectRecords(self.records)
        found = await projects.project(request.name)
        given = request.model_dump(exclude={"name"}, exclude_none=True)
        if found is None:
            if request.title is None:
                raise NotAllowed(
                    f"{request.name!r} is a new project, so it needs a title",
                    hint="Set one: it is how the project is shown.",
                )
            found = stored.Project(name=request.name, **given)
        else:
            found = found.model_copy(update=given)
        return as_shape(Project, await projects.save_project(found))

    async def delete_project(self, request: DeleteProjectRequest) -> Deleted:
        """Delete a project, with its repos, its features, its tasks and their notes and links."""
        deleted = await stored.ProjectRecords(self.records).delete_project(request.name)
        return Deleted(deleted=deleted)

    async def set_repo(self, request: SetRepoRequest) -> Repo:
        """Create or edit a repo of a project, by its name: what is not given is kept (and a new
        one needs its GitHub repository and the account udl reaches it through)."""
        project = await self._project(request.project)
        repos = stored.RepoRecords(self.records, stored.ProjectRecords(self.records))
        found = await repos.repo(project, request.name)
        given = request.model_dump(exclude={"project", "name"}, exclude_none=True)
        if found is None:
            missing = [name for name in ("github", "account") if name not in given]
            if missing:
                raise NotAllowed(
                    f"{request.name!r} is a new repo, so it needs {' and '.join(missing)}",
                    hint="A repo is its GitHub repository (owner/repo) and the github account "
                    "udl reaches it through (github:<name>).",
                )
            found = stored.Repo(project=project, name=request.name, **given)
        else:
            found = found.model_copy(update=given)
        return as_shape(Repo, await repos.save_repo(found))

    async def delete_repo(self, request: DeleteRepoRequest) -> Deleted:
        """Delete a repo of a project (its tasks stay: a task names its repo, it does not hang
        on it)."""
        project = await self._project(request.project)
        repos = stored.RepoRecords(self.records, stored.ProjectRecords(self.records))
        return Deleted(deleted=await repos.delete_repo(project, request.name))

    async def list_features(self, request: ListFeaturesRequest) -> Features:
        """A project's features, in their order."""
        project = await self._project(request.project)
        found = await stored.FeatureRecords(self.records).features(project)
        return Features(project=project, features=[as_shape(Feature, one) for one in found])

    async def get_feature(self, request: GetFeatureRequest) -> FeatureInfo:
        """A feature, with its tasks, its links and its notes."""
        project = await self._project(request.project)
        found = await self._require_feature(project, request.key)
        tasks = await stored.TaskRecords(self.records).tasks(project, feature=found.key)
        links = await LinkRecords(self.records).for_owner(project, OwnerKind.FEATURE, found.key)
        notes = await stored.NoteRecords(self.records).for_owner(OwnerKind.FEATURE, found.id)
        return FeatureInfo(
            feature=as_shape(Feature, found),
            tasks=[as_shape(Task, one) for one in tasks],
            links=[as_shape(Link, one) for one in links],
            notes=[as_shape(NoteInfo, one) for one in notes],
        )

    async def set_feature(self, request: SetFeatureRequest) -> Feature:
        """Create or edit a feature, by its key: what is not given is kept (and a new one needs
        a title). Its spec is scanned for links as it is saved (26.4)."""
        project = await self._project(request.project)
        features = stored.FeatureRecords(self.records)
        found = await features.feature(project, request.key)
        given = request.model_dump(exclude={"project", "key"}, exclude_none=True)
        if found is None:
            if request.title is None:
                raise NotAllowed(
                    f"{request.key!r} is a new feature, so it needs a title",
                    hint="Set one: it is how the feature is shown.",
                )
            found = stored.Feature(project=project, key=request.key, **given)
        else:
            found = found.model_copy(update=given)
        return as_shape(Feature, await features.save_feature(found))

    async def list_tasks(self, request: ListTasksRequest) -> Tasks:
        """A project's tasks, by key, narrowed by feature, state, assignee and text."""
        project = await self._project(request.project)
        found = await stored.TaskRecords(self.records).tasks(
            project, feature=request.feature, state=request.state, assignee=request.assignee
        )
        if request.text:
            needle = request.text.casefold()
            found = [
                task
                for task in found
                if needle in task.title.casefold() or needle in task.spec.casefold()
            ]
        return Tasks(project=project, tasks=[as_shape(Task, one) for one in found])

    async def get_task(self, request: GetTaskRequest) -> TaskInfo:
        """A task, with its feature, its links, its notes and its open questions."""
        project = await self._project(request.project)
        found = await self._require_task(project, request.key)
        feature = await stored.FeatureRecords(self.records).feature(project, found.feature)
        links = await LinkRecords(self.records).for_owner(project, OwnerKind.TASK, found.key)
        notes = await stored.NoteRecords(self.records).for_owner(OwnerKind.TASK, found.id)
        return TaskInfo(
            task=as_shape(Task, found),
            feature=as_shape(Feature, feature) if feature is not None else None,
            links=[as_shape(Link, one) for one in links],
            notes=[as_shape(NoteInfo, one) for one in notes],
            questions=[],  # (28.1's record: a task has none until questions are stored)
        )

    async def set_task(self, request: SetTaskRequest) -> Task:
        """Create or edit a task, by its key: what is not given is kept, and its state is never
        set here (it moves, `orch_move_task`). A new task needs its feature and a title; without
        a key it takes the feature's next one."""
        project = await self._project(request.project)
        tasks = stored.TaskRecords(self.records)
        found = await tasks.task(project, request.key) if request.key is not None else None
        given = request.model_dump(exclude={"project", "key", "feature"}, exclude_none=True)
        if found is None:
            feature = await self._feature_of(project, request)
            if request.title is None:
                raise NotAllowed(
                    f"{request.key or 'A new task'} is a new task, so it needs a title",
                    hint="Set one: it is how the task is shown, in its queue and its PR.",
                )
            key = request.key or await self._next_key(tasks, project, feature)
            found = stored.Task(project=project, feature=feature, key=key, **given)
        else:
            if request.feature is not None and request.feature != found.feature:
                raise NotAllowed(
                    f"{found.key!r} is a task of {found.feature!r}, not of {request.feature!r}",
                    hint="A task does not move between features: set the one it is of.",
                )
            found = found.model_copy(update=given)
        return as_shape(Task, await tasks.save_task(found))

    async def move_task(self, request: MoveTaskRequest) -> Task:
        """Move a task to another state, through the table (orch.transitions): what it waits on
        is its note while it is blocked, and the move writes the system note that says so."""
        project = await self._project(request.project)
        tasks = stored.TaskRecords(self.records)
        found = await self._require_task(project, request.key)
        from udl.core.service import principal  # (the caller's, as each interface set it)

        moved = await tasks.move(found, request.to, by=_by(principal()), note=request.note)
        return as_shape(Task, moved)

    async def split_task(self, request: SplitTaskRequest) -> Tasks:
        """Split a task into several: the first keeps its key (so its links and its notes stay
        with it), and each of the others takes the feature's next key."""
        project = await self._project(request.project)
        tasks = stored.TaskRecords(self.records)
        found = await self._require_task(project, request.key)
        first, *rest = request.into
        keys = [found.key]
        for _ in rest:
            keys.append(await self._next_key(tasks, project, found.feature))
        pieces = [
            found.model_copy(update={"title": first.title, "spec": first.spec}),
            *(
                stored.Task(
                    project=project,
                    feature=found.feature,
                    key=key,
                    title=piece.title,
                    spec=piece.spec,
                    task_kind=found.task_kind,
                    repo=found.repo,
                    needs=list(found.needs),
                )
                for key, piece in zip(keys[1:], rest, strict=True)
            ),
        ]
        note = stored.Note(
            owner_kind=OwnerKind.TASK,
            owner=found.id,
            note_kind=NoteKind.SYSTEM,
            text=f"Split into {_listed(keys)}.",
            author="udl",
        )
        saved = await tasks.save_all(pieces, also=[note])
        return Tasks(project=project, tasks=[as_shape(Task, one) for one in saved])

    async def add_link(self, request: AddLinkRequest) -> Link:
        """Link something to a feature or a task (one URL is one link: adding it
        again gives the one that is there)."""
        project = await self._project(request.project)
        await self._owner_ids(project, request.owner_kind, request.owner)
        try:
            found = await LinkRecords(self.records).add(
                project=project,
                owner_kind=request.owner_kind,
                owner=request.owner,
                kind=request.link_kind,
                url=request.url,
                title=request.title,
            )
        except ValueError as error:
            raise ConfigError(
                str(error), hint="A pull, issue, commit or branch link is a GitHub URL."
            ) from None
        return as_shape(Link, found)

    async def remove_link(self, request: RemoveLinkRequest) -> Deleted:
        """Unlink something from what it hangs on (canonicalised again, so any form of the URL
        finds the one stored)."""
        project = await self._project(request.project)
        links = LinkRecords(self.records)
        url = canonical(request.url)
        for found in await links.for_owner(project, request.owner_kind, request.owner):
            if found.url == url:
                return Deleted(deleted=await links.delete(found))
        return Deleted(deleted=False)

    async def find_links(self, request: FindLinksRequest) -> Links:
        """What hangs on a URL (the other way round from a task's own links): the tasks a PR
        belongs to, however the URL is written."""
        found = await LinkRecords(self.records).by_url(request.url)
        return Links(url=canonical(request.url), links=[as_shape(Link, one) for one in found])

    async def add_note(self, request: AddNoteRequest) -> NoteInfo:
        """Write a note on a feature or a task (appended: a correction is another note), as
        whoever is calling: an agent's note is the agent's, and a system note is udl's own."""
        if request.note_kind is NoteKind.SYSTEM:
            raise NotAllowed(
                "A system note is udl's own: the moves and the splits write them",
                hint="Write a note of another kind: a progress, a decision, a finding or a review.",
            )
        project = await self._project(request.project)
        owners = await self._owner_ids(project, request.owner_kind, request.owner)
        from udl.core.service import principal  # (the caller's, as each interface set it)

        note = await stored.NoteRecords(self.records).add(
            stored.Note(
                owner_kind=request.owner_kind,
                owner=owners[0],
                note_kind=request.note_kind,
                text=request.text,
                author=_by(principal()).who,
            )
        )
        return as_shape(NoteInfo, note)

    async def _require_feature(self, project: str, key: str) -> stored.Feature:
        """The feature `key` of `project`, or DoesNotExist."""
        found = await stored.FeatureRecords(self.records).feature(project, key)
        if found is None:
            raise DoesNotExist(
                f"There is no feature {key!r} in {project!r}",
                hint="List the project's features to see its keys.",
            )
        return found

    async def _require_task(self, project: str, key: str) -> stored.Task:
        """The task `key` of `project`, or DoesNotExist."""
        found = await stored.TaskRecords(self.records).task(project, key)
        if found is None:
            raise DoesNotExist(
                f"There is no task {key!r} in {project!r}",
                hint="List the project's tasks to see its keys.",
            )
        return found

    async def _feature_of(self, project: str, request: SetTaskRequest) -> str:
        """The feature a new task is of: the one it names, or the one its key is numbered from
        (`26.3` is 26's), which must be there."""
        feature = request.feature
        if feature is None and request.key is not None and "." in request.key:
            feature = request.key.split(".", 1)[0]
        if feature is None:
            raise NotAllowed(
                f"{request.key or 'A new task'} is a new task, so it needs the feature it is of",
                hint="Name the feature (26), or give the key it is to have (26.3).",
            )
        await self._require_feature(project, feature)
        if request.key is not None and "." in request.key:
            numbered = request.key.split(".", 1)[0]
            if numbered != feature:
                raise NotAllowed(
                    f"{request.key!r} is numbered from {numbered!r}, not from {feature!r}",
                    hint="A task's key is `<feature key>.<n>`: give the one its feature numbers.",
                )
        return feature

    async def _next_key(self, tasks: stored.TaskRecords, project: str, feature: str) -> str:
        """The next key a task of `feature` may take: the counter's, past any a project has (a
        project imported with keys already, 22.x, keeps counting past them)."""
        counters = stored.Counters(self.records)
        for _ in range(100):
            key = f"{feature}.{await counters.claim(project, f'{feature}.')}"
            if await tasks.task(project, key) is None:
                return key
        raise NotAllowed(
            f"No key is free for a task of {feature!r} in {project!r}",
            hint="Its counter has run past a hundred taken keys: give the task its key.",
        )

    async def _owner_ids(self, project: str, owner_kind: OwnerKind, owner: str) -> list[UUID]:
        """The ids of the record `owner` names (`owner_kind`/`owner`), or DoesNotExist."""
        if owner_kind is OwnerKind.TASK:
            return [(await self._require_task(project, owner)).id]
        if owner_kind is OwnerKind.FEATURE:
            return [(await self._require_feature(project, owner)).id]
        raise NotAllowed(
            f"udl does not keep {owner_kind.value}s yet",
            hint="A note hangs on a task or a feature; questions and actions come with 28.x.",
        )

    async def serve(self, ctx: AppContext) -> None:
        """Orch's service: it keeps its pull links current (26.4) and dispatches to its agents
        (27.7), until the lease goes."""
        keeping = self._keep_links(ctx.records, ctx.events, ctx.service, ctx.stop)
        if self.service is None:  # (an app made outside a service has no agents to dispatch to)
            await keeping
            return
        await asyncio.gather(keeping, Dispatcher(ctx, harnesses=self.harnesses).run())

    async def _keep_links(
        self,
        records: AppRecords,
        events: AppEvents,
        service: UDLService,
        stop: Stopping,
    ) -> None:
        """Listen for `github.pr.*` events from now on, and for each one refresh every `pull`
        link whose URL is that pull through `github_get_pull`, store it, and publish
        `orch.link.changed`; and when a pull merged or closed, do what that sets off (28.3). It
        ends when `stop` is set (the lease going), or when the subscription ends (the runtime
        closing it: the next lease starts this again)."""
        links = LinkRecords(records)
        async with events.subscribe("github.pr.*") as subs:
            waiting = asyncio.ensure_future(asyncio.shield(stop.wait()))
            try:
                await self.follow(
                    links, events, service, subs, waiting, happened=self._pull_happened(service)
                )
            finally:
                waiting.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiting

    def _pull_happened(self, service: UDLService) -> PullHappened:
        """What to do when a pull merged or closed: the service's own, bound to `service`."""

        async def happened(event: AppEvent, url: str) -> None:
            await self.pull_happened(event, url, service=service)

        return happened

    async def follow(
        self,
        links: LinkRecords,
        events: EventEmission,
        service: PullReader,
        subs: Subscription,
        waiting: asyncio.Future[object],
        *,
        happened: PullHappened | None = None,
    ) -> None:
        """Handle each event until `waiting` (the app told to stop) ends first. `happened` is
        called for a pull that merged or closed, with the whole service (28.3)."""
        while True:
            try:
                event = await _first(subs.__anext__(), waiting)
            except StopAsyncIteration:
                return
            if event is None:
                return
            if not isinstance(event, AppEvent):  # (what `_first` answers: the event, or None)
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
                continue
            if happened is not None and event.type in (_MERGED, _CLOSED):
                try:
                    await happened(event, url)
                except (DoesNotExist, PluginError, UDLError, ValidationError) as error:
                    log.warning("orch could not finish what %s set off: %s", event.type, error)

    async def pull_happened(self, event: AppEvent, url: str, *, service: UDLService) -> None:
        """A pull merged or closed (28.3): the task its link is on moves as the event says, and
        what a merge sets off runs (`_after_merge`), or its task goes back to work."""
        links = LinkRecords(self.records)
        for found in await links.for_url(url):
            if found.link_kind != "pull":
                continue
            task = await stored.TaskRecords(self.records).task(found.project, found.owner)
            if task is None or task.state in (MERGED, DONE, CANCELLED):
                continue
            project = await stored.ProjectRecords(self.records).project(found.project)
            if project is None:
                continue
            if event.type == _MERGED:
                by = str(event.data.get("merged_by") or "")
                await self._after_merge(
                    service,
                    project=found.project,
                    key=task.key,
                    by=by or "someone",
                    # (Anyone who merges on GitHub is the review bot — its merge is the review —
                    # or a person, whose merge is the review too: an agent cannot merge there.)
                    reviewed=True,
                    promoted=bool(project.promotion),
                )
            else:
                await after_close(
                    self.records,
                    project=found.project,
                    key=task.key,
                    why=await self._closing_comment(service, url),
                )

    async def _closing_comment(self, service: UDLService, url: str) -> str:
        """The last thing said on a pull that was closed without merging ("" when nothing was):
        what its agent is told, as data."""
        ref = pull_ref(url)
        if ref is None:
            return ""
        repo, number = ref
        detail = await service.call("github_get_pull", {"repo": repo, "number": number})
        pull = Pull.model_validate(detail.model_dump())
        return pull.comments[-1].body if pull.comments else ""

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
        from udl.interfaces.security import current_principal

        who = current_principal.get()
        merging = await stored.ProjectRecords(self.records).project(project)
        if (
            merging is not None
            and merging.merge == MergePolicy.AGENT
            and who is not None
            and who.is_agent
            and task.assignee == who.name
        ):
            # (27.6's deferred branch: where a project lets its own agent merge, an agent that
            # marks its pull ready merges it, through the same merge `orch_merge` makes.)
            link = await self._pull_url(project, request.key)
            ref = pull_ref(link)
            if ref is None:
                raise NotAllowed(f"{request.key!r} has no pull to merge")
            repo, number = ref
            await self._merge_now(
                service,
                project=project,
                task=task,
                pull=pull,
                confirm=f"{repo}#{number}",
                by=who.name,
                reviewed=False,
                promoted=bool(merging.promotion),
            )
            return found
        moved = transitions.move(task, READY, by=_UDL)
        note = stored.Note(
            owner_kind=OwnerKind.TASK,
            owner=task.id,
            note_kind=NoteKind.PROGRESS,
            text=request.note or f"Ready to merge: {pull.head_sha[:7]}.",
            author="udl",
        )
        await stored.TaskRecords(self.records).save_all(
            [moved], also=[note, stored.move_note(task, moved, _UDL)]
        )
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

    # -- merging (28.3) ------------------------------------------------------------------------

    async def merge_list(self, request: MergeListRequest, *, service: UDLService) -> MergeList:
        """What there is to merge (28.3): the ready ones in merge order, grouped by repo; the ones
        still coming, with what they wait on; the held ones; and what merged in the last 14 days,
        with what followed each. It reads: nothing changes."""
        project = await self._project(request.project)
        found = await stored.ProjectRecords(self.records).project(project)
        if found is None:
            raise DoesNotExist(f"There is no project called {project!r}")
        tasks = await stored.TaskRecords(self.records).tasks(project)
        # (One read per task, all at once: a list of ten pulls must not take ten round trips. A
        # pull that cannot be read is that row's blocker, not the whole list's failure.)
        read = await asyncio.gather(*(self._merge_row(service, found, one, tasks) for one in tasks))
        rows = [row for row in read if row is not None]
        since = self.clock.now() - timedelta(days=merged.DAYS)
        return MergeList(
            ready=order([one for one in rows if one.state == READY and not one.hold]),
            coming=[one for one in rows if one.state not in (READY, MERGED, DONE) and not one.hold],
            held=[one for one in rows if one.hold],
            done=order(
                [
                    one
                    for one in rows
                    if one.state in (MERGED, DONE) and (one.since is None or one.since >= since)
                ]
            ),
        )

    async def _merge_row(
        self,
        service: UDLService | None,
        project: stored.Project,
        task: stored.Task,
        tasks: Sequence[stored.Task],
    ) -> MergeRow | None:
        """`task` as a merge list reads it, or None when it has no pull to show (one that is open,
        cancelled, or taken for another agent). `service` is None to read no GitHub (a merged
        task's row needs none)."""
        if task.state in (OPEN, CANCELLED, MOVED):
            return None
        links = await stored.LinkRecords(self.records).for_owner(
            project.name, OwnerKind.TASK, task.key
        )
        link = next((one for one in links if one.link_kind == "pull"), None)
        if link is None:
            return None
        ref = pull_ref(link.url)
        if ref is None:
            return None
        repo, number = ref
        blockers: list[str] = []
        if task.state not in (MERGED, DONE) and service is not None:
            try:
                detail = await service.call("github_get_pull", {"repo": repo, "number": number})
                pull = Pull.model_validate(detail.model_dump())
                blockers = (await self._assess(task, pull, project.name)).blockers
            except (DoesNotExist, PluginError, UDLError, ValidationError) as error:
                # (That row's blocker: one pull udl cannot read does not hide the others.)
                blockers = [f"its pull could not be read: {error}"]
        if task.hold is not None:
            blockers = [f"it is on hold: {task.hold}"]
        return MergeRow(
            project=project.name,
            task=task.key,
            feature=task.feature,
            title=task.title,
            repo=repo,
            number=number,
            url=link.url,
            sha=link.state.head_sha,
            state=task.state,
            needs=list(task.needs),
            blockers=blockers,
            hold=task.hold is not None,
            since=task.updated_at,
            followed=[one.key for one in tasks if task.key in one.needs],
        )

    async def merge(self, request: MergeRequest, *, service: UDLService) -> Merged:
        """Merge the pull of the task `key` (28.3), once more against its current head: it must be
        ready, `confirm` must say which pull it is (`owner/repo#number`), and the head that was
        checked is the commit merged, so a pull that changed since is not merged blind.

        A person merges, or — where the project's `merge` is `agent` — the task's own assignee.
        After it the task is `merged` (and `done` at once when a person merged and nothing follows
        the base), and each task stacked on it is told to rebase and push again. Its worktree is
        taken away by 27.5's `remove()`, which this PR does not reach (it is in #52, not in
        `main`)."""
        project = await self._project(request.project)
        task, pull, why = await self._pull_of(service, project, request.key)
        if pull is None:
            raise NotAllowed(
                f"{request.key!r} cannot be merged: {why}",
                hint="Open its pull first (27.5), then ask again.",
            )
        found = await stored.ProjectRecords(self.records).project(project)
        if found is None:
            raise DoesNotExist(f"There is no project called {project!r}")
        from udl.interfaces.security import current_principal

        who = current_principal.get()
        if (
            who is not None
            and who.is_agent
            and not (found.merge == MergePolicy.AGENT and task.assignee == who.name)
        ):
            raise NotAllowed(
                f"{request.key!r} is not {who.name}'s to merge",
                hint=(
                    "A person merges a ready pull, unless the project's merge is `agent` and the "
                    "caller is the task's own assignee."
                ),
            )
        if request.sha != pull.head_sha:
            raise NotAllowed(
                f"{request.key!r} has moved since it was listed: {request.sha[:7]} is now "
                f"{pull.head_sha[:7]}",
                hint="Ask for the merge list again, and merge what it now says.",
            )
        ready = await self._assess(task, pull, project)
        if not ready.ready:
            raise NotAllowed(
                f"{request.key!r} is not ready: {'; '.join(ready.blockers)}",
                hint="Fix what it says, then ask again.",
            )
        link = await self._pull_url(project, request.key)
        ref = pull_ref(link)
        if ref is None:
            raise NotAllowed(f"{request.key!r} has no pull to merge")
        repo, number = ref
        expected = f"{repo}#{number}"
        if request.confirm != expected:
            raise ConfigError(
                f"Merging {expected} is confirmed by confirm={expected}",
                hint=f"Say which pull it is: confirm={expected}.",
            )
        return await self._merge_now(
            service,
            project=project,
            task=task,
            pull=pull,
            confirm=expected,
            by=who.name if who is not None else "udl",
            reviewed=who is None or not who.is_agent,
            promoted=bool(found.promotion),
        )

    async def _merge_now(
        self,
        service: UDLService,
        *,
        project: str,
        task: stored.Task,
        pull: Pull,
        confirm: str,
        by: str,
        reviewed: bool,
        promoted: bool,
    ) -> Merged:
        """Merge `task`'s pull at `pull.head_sha` and do what a merge sets off: the one place a
        merge is made, whether a person asked for it or its own agent marked it ready."""
        link = await self._pull_url(project, task.key)
        ref = pull_ref(link)
        if ref is None:
            raise NotAllowed(f"{task.key!r} has no pull to merge")
        repo, number = ref
        # (The merge is udl's on the caller's behalf: `github_merge_pull` is not an agent's to
        # call, and who may merge at all is decided by the caller.)
        from udl.interfaces.security import current_principal

        calling = current_principal.set(None)
        try:
            await service.call(
                "github_merge_pull",
                {"repo": repo, "number": number, "sha": pull.head_sha, "confirm": confirm},
            )
        finally:
            current_principal.reset(calling)
        moved = await self._after_merge(
            service,
            project=project,
            key=task.key,
            by=by,
            reviewed=reviewed,
            promoted=promoted,
        )
        return Merged(task=task.key, url=link, sha=pull.head_sha, state=moved.state)

    async def promotion(self, request: PromotionRequest) -> PromotionList:
        """What a promotion would carry (28.3): the tasks merged and not done, per repo, for a
        project whose branches go on after the base."""
        project = await self._project(request.project)
        found = await stored.ProjectRecords(self.records).project(project)
        if found is None:
            raise DoesNotExist(f"There is no project called {project!r}")
        tasks = await stored.TaskRecords(self.records).tasks(project)
        rows = [
            await self._merge_row(None, found, one, tasks) for one in tasks if one.state == MERGED
        ]
        return PromotionList(
            project=project,
            promotion=list(found.promotion),
            rows=order([one for one in rows if one is not None]),
        )

    async def promote(self, request: PromoteRequest) -> Promoted:
        """Mark the tasks a promotion carried done (28.3), after its pull merged: `note` is written
        on each as a `progress` note. A task that is not `merged` is refused."""
        project = await self._project(request.project)
        found = await stored.ProjectRecords(self.records).project(project)
        if found is None:
            raise DoesNotExist(f"There is no project called {project!r}")
        if not found.promotion:
            raise NotAllowed(
                f"{project!r} has no branches after its base: nothing is promoted",
                hint="A project with no `promotion` merges straight to `done` (28.3).",
            )
        tasks = stored.TaskRecords(self.records)
        # (All or nothing: every task is checked before one is written, so a mistyped key in the
        # list does not leave half a promotion done.)
        checked: list[stored.Task] = []
        for key in request.tasks:
            task = await tasks.task(project, key)
            if task is None:
                raise DoesNotExist(f"There is no task called {key!r} in {project!r}")
            if task.state != MERGED:
                raise NotAllowed(
                    f"{key!r} is not merged: it is {task.state!r}",
                    hint="Only what a promotion carried (merged, not done) is marked done.",
                )
            checked.append(task)
        moved = [transitions.move(one, DONE, by=_REVIEWER) for one in checked]
        notes = [
            stored.Note(
                owner_kind=OwnerKind.TASK,
                owner=one.id,
                note_kind=NoteKind.PROGRESS,
                text=request.note or "Promoted: it is done.",
                author=_REVIEWER.who,
            )
            for one in moved
        ]
        notes += [
            stored.move_note(one, moved_one, _REVIEWER)
            for one, moved_one in zip(checked, moved, strict=True)
        ]
        await tasks.save_all(moved, also=notes)
        return Promoted(tasks=[one.key for one in moved])

    async def _after_merge(
        self,
        service: UDLService,
        *,
        project: str,
        key: str,
        by: str,
        reviewed: bool,
        promoted: bool,
    ) -> stored.Task:
        """What a merge sets off (28.3), however it happened: `merge.after_merge` (the task's
        state, the rebase notes, the actions waiting on it)."""
        changed = await after_merge(
            self.records, project=project, key=key, by=by, reviewed=reviewed, promoted=promoted
        )
        return changed[0]

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
        notes = await stored.NoteRecords(self.records).for_owner(OwnerKind.TASK, found.id)
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

    # -- a pull's readiness, waited for (27.5) --------------------------------------------------

    async def pr_status(self, request: PrStatusRequest, *, service: UDLService) -> Readiness:
        """Whether the task `key`'s pull may be merged (27.6), and with `wait`, holding until
        GitHub's checks and the review bot have finished with this head (or until the time it
        is given runs out).

        A pull that is not ready for a reason waiting cannot change (a draft, a failed check,
        an open item) is answered at once, as is one that has no pull yet."""
        project = await self._project(request.project)
        started = self.clock.now()
        limit = timedelta(minutes=request.minutes or self.settings.wait_minutes)
        while True:
            task, pull, why = await self._pull_of(service, project, request.key)
            if pull is None:
                return Readiness(ready=False, blockers=[why])
            found = await self._assess(task, pull, project)
            waited = round((self.clock.now() - started).total_seconds(), 1)
            spent = self.clock.now() - started >= limit
            if not request.wait or found.ready or spent or not _not_yet(found):
                return found.model_copy(update={"waited": waited})
            await self.clock.sleep(timedelta(seconds=self.settings.poll_seconds))

    # -- the worktree, the gate and the push (27.5) ---------------------------------------------

    async def push(self, request: PushRequest, *, service: UDLService) -> PushResult:
        """Push a task's work: its branch to origin, then its pull request (27.5).

        Only the task's own agent may (or udl's owner), in the task's worktree on the agent's
        host: nothing uncommitted, rebased on the base, the repo's gate run in its host's lane,
        and on a pass the push, the pull, and the pull's link on the task."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        task = await tasks.task(project, request.key)
        if task is None:
            raise NotAllowed(
                f"There is no task {request.key!r} in {project!r}",
                hint="List them with `udl orch queue`.",
            )
        from udl.core.service import principal  # (the caller's, as each interface set it)

        who = principal()
        if who is not None and who.is_agent and who.name != task.assignee:
            raise NotAllowed(
                f"{task.key!r} is not yours to push: it is {task.assignee or 'nobody'}'s",
                hint="An agent pushes only the work it is assigned.",
            )
        repo = await self._repo_of(task)
        agent = (
            await stored.AgentRecords(self.records).agent(task.assignee)
            if task.assignee and not task.assignee.startswith("human:")
            else None
        )
        hosts = service.hosts
        host = await hosts.get(agent.host if agent is not None else LOCAL)
        return await space.push(
            task,
            repo=repo,
            host=host,
            shell=await hosts.shell(host.name),
            service=service,
            records=self.records,
            title=request.title,
            body=request.body,
            within=timedelta(minutes=self.settings.gate_minutes),
        )

    async def _repo_of(self, task: stored.Task) -> stored.Repo:
        """The repo a task's work is in: the one it names, or the project's only one."""
        repos = stored.RepoRecords(self.records)
        if task.repo:
            found = await repos.repo(task.project, task.repo)
            if found is not None:
                return found
            raise NotAllowed(
                f"{task.key!r} names the repo {task.repo!r}, and {task.project!r} has none of it",
                hint="Name one it has, or add it first.",
            )
        every = await repos.repos(task.project)
        if len(every) == 1:
            return every[0]
        raise NotAllowed(
            f"Which repo? {task.project!r} has {len(every)}",
            hint="Name the task's repo.",
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


def _not_yet(found: Readiness) -> bool:
    """Whether everything still in the way is only a matter of time — the checks running, the
    bot not done with this head, GitHub not knowing yet — so a wait is worth it (27.5)."""
    return bool(found.blockers) and len(found.pending) == len(found.blockers)


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
