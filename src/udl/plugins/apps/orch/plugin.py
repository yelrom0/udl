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
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID

# Package Imports
from pydantic import BaseModel, SecretStr, ValidationError

# Local Imports
from udl.automations.events import EventType
from udl.clock import Clock
from udl.core.models import Deleted
from udl.core.operations import CLISpec, Operation, OperationHints
from udl.db.manager import DBManager
from udl.db.models import JsonObject
from udl.db.records import Record, RecordQuery
from udl.errors import ConfigError, DoesNotExist, NotAllowed, PluginError, UDLError
from udl.hosts import LOCAL
from udl.interfaces import security
from udl.plugins.ai.base import AIProviderConnection
from udl.plugins.apps.base import (
    AppContext,
    AppEvent,
    AppEvents,
    AppPlugin,
    AppRecords,
)
from udl.plugins.apps.orch import actions as acted
from udl.plugins.apps.orch import agents as agents_shape
from udl.plugins.apps.orch import managers as bosses
from udl.plugins.apps.orch import merge as merged
from udl.plugins.apps.orch import questions as asked
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch import suggest as suggested
from udl.plugins.apps.orch import transitions
from udl.plugins.apps.orch import workspace as space
from udl.plugins.apps.orch.agents import (
    Agent,
    AgentInfo,
    AgentRole,
    Agents,
    DeleteAgentRequest,
    GetAgentRequest,
    GetSessionRequest,
    ListAgentsRequest,
    PauseAgentRequest,
    ResumeAgentRequest,
    SendRequest,
    Sent,
    SessionEventShape,
    SessionInfo,
    SessionState,
    SetAgentRequest,
    StopSessionRequest,
)
from udl.plugins.apps.orch.agents import (
    AgentSession as AgentSessionShape,
)
from udl.plugins.apps.orch.chat import (
    AskPermissionRequest,
    ChatInfo,
    ChatList,
    Chats,
    DeleteChatRequest,
    GetChatRequest,
    ListChatsRequest,
    PermissionAnswer,
    PermissionRequest,
    SendChatRequest,
    StartChatRequest,
    StopChatRequest,
)
from udl.plugins.apps.orch.dispatcher import Dispatcher, Outcome, Turns
from udl.plugins.apps.orch.events import (
    ACTION_NEEDED,
    AGENT_IDLE,
    QUESTION_ANSWERED,
    QUESTION_ASKED,
    TASK_BLOCKED,
    TASK_CHANGED,
)
from udl.plugins.apps.orch.importer import (
    PLAN_KEY,
    ParsedPlan,
    ParsedTask,
    merge,
    read_plan_file,
)
from udl.plugins.apps.orch.links import (
    LINK_CHANGED,
    LinkChanged,
    LinkRecords,
    canonical,
    event_url,
    pull_ref,
)
from udl.plugins.apps.orch.managers import in_subtree, managers_of
from udl.plugins.apps.orch.merge import TASK_MERGED, TaskMerged, after_close, after_merge, order
from udl.plugins.apps.orch.models import (
    CANCELLED,
    DONE,
    IN_PROGRESS,
    MERGED,
    MOVED,
    OPEN,
    PROJECT_CHANGED,
    QUEUED,
    READY,
    SPEC_MAX,
    Action,
    Actions,
    ActionState,
    AddLinkRequest,
    AddNoteRequest,
    AgentLine,
    AnswerRequest,
    AQuestion,
    AskRequest,
    AssignTaskRequest,
    BlockRequest,
    CloseQuestionRequest,
    DeleteProjectRequest,
    DeleteRepoRequest,
    DropActionRequest,
    EscalateRequest,
    Estimate,
    EstimateRequest,
    Feature,
    FeatureInfo,
    Features,
    FindLinksRequest,
    FinishActionRequest,
    GetFeatureRequest,
    GetProjectRequest,
    GetQuestionRequest,
    GetTaskRequest,
    ImportPlanRequest,
    ImportReport,
    Inbox,
    InboxRequest,
    Link,
    Links,
    ListActionsRequest,
    ListFeaturesRequest,
    ListProjectsRequest,
    ListQuestionsRequest,
    ListTasksRequest,
    ManagerVerdict,
    Merged,
    MergeList,
    MergeListRequest,
    MergePolicy,
    MergeRequest,
    MergeRow,
    MergerState,
    Message,
    MoveTaskRequest,
    MyTaskNote,
    MyTaskRequest,
    MyTaskResponse,
    NoteInfo,
    NoteKind,
    OwnerKind,
    Principal,
    Progress,
    ProgressRequest,
    Project,
    ProjectChanged,
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
    Question,
    Questions,
    QuestionState,
    QueueEntry,
    QueueRequest,
    Readiness,
    ReadinessRequest,
    ReadyRequest,
    RemoveLinkRequest,
    ReorderQueueRequest,
    ReplyRequest,
    Repo,
    Reviewed,
    ReviewRequest,
    SetActionRequest,
    SetAutoMergeRequest,
    SetFeatureRequest,
    SetProjectRequest,
    SetRepoRequest,
    SetTaskRequest,
    Size,
    SplitTaskRequest,
    Suggestions,
    SuggestRequest,
    Task,
    TaskInfo,
    TaskQueue,
    Tasks,
    Totals,
    UnassignTaskRequest,
    agent_line,
    as_shape,
)
from udl.plugins.apps.orch.readiness import TASK_READY, Pull, TaskReady
from udl.plugins.apps.orch.readiness import readiness as assess
from udl.plugins.apps.orch.records import scan_links
from udl.plugins.apps.orch.reporting import Reporting
from udl.plugins.apps.orch.sessions import (
    AGENT_LIMITED,
    CHAT_ANSWERED,
    CHAT_ENDED,
    CHAT_EVENT,
    CHAT_FAILED,
    CHAT_PERMISSION,
    CHAT_STARTED,
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
from udl.plugins.secrets.base import SecretField, SecretStore, load_secrets

if TYPE_CHECKING:
    from udl.core.service import UDLService
    from udl.interfaces.security import Principal as Caller

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
_INBOX: Final = ("orch", "inbox")
"""The inbox's own command (`udl orch inbox`): the one thing a person reads first (28.4)."""


_UDL: Final = Principal(kind="system", role="ready")
"""(Who udl is when it moves a task itself: the system, in the role `ready` — not a person, who may
move a task anywhere, so an `open` or `blocked` task would be marked ready with its reason
cleared. The table allows `ready` from `in_progress` only.)"""


def _by(who: security.Principal | None) -> Principal:
    """Who a move or a note is by: the person at the keyboard when no interface set a caller
    (the CLI, a test), the agent when one is calling, and a person for the server's owner.

    A person may move a task anywhere (the table is for the agents, orch.transitions); an agent
    is held to its own tasks and to the roles its state allows. A reviewer is the one agent the
    table lets review anyone's work, so its role is kept (`transitions` knows it by name)."""
    if who is not None and who.is_agent:
        if who.role == "reviewer":
            return _REVIEWER
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

_UNSTARTED: Final = ("open", "blocked")
"""The states a task is split in: its spec is not yet being written (nor written)."""

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


class _Key(BaseModel):
    """A connection's options as udl reads its key (the rest of them is the provider's)."""

    api_key: SecretField | None = None
    """The key, or where udl's secret store has it."""


_AGENTS: tuple[str, ...] = ("orch", "agents")
"""The CLI words the agents' operations live under: `udl orch agents [list|show|set|…]`."""

_QUEUE: tuple[str, ...] = ("orch", "queue")
_CHATS: tuple[str, ...] = ("orch", "chats")
"""The CLI words a person's chats live under: `udl orch chats [start|send|show|stop|rm|allow]`."""

_RUNNERS: tuple[str, ...] = ("orch", "runners")
"""The CLI words a harness's operations live under: `udl orch runners [install]`."""

__all__ = ["Orch"]


UI_FOLDER: Final[Path] = (Path(__file__).parent / "ui").resolve()
"""Its own UI (29.2), where `make web` builds it: the folder is the bundle (`manifest.json` and
its files). It is named here and loaded by discovery, so a checkout that has not built the web
tree has no bundle (the generated page alone) and a half-written one is a problem on
`GET /ui/plugins.json` rather than an import that takes the CLI, the API and the MCP down."""


class Orch(AppPlugin[OrchSettings]):
    """udl's orchestrator: the projects and repos it tracks, and the agents that work them.

    It brings its records (a Project, a Repo) and its shapes (orch.models); its queue operations
    are here (27.3), the rest are 26.6's, and its service the dispatcher of Phase 27."""

    name = "orch"
    title = "Orchestrator"
    summary = "The work udl tracks: projects, repos, features, tasks, links and notes."
    settings_model = OrchSettings
    # (Its own UI (29.2), built beside this module by `make web`; until it is built, the
    # generated page alone.)
    ui_folder = UI_FOLDER

    @classmethod
    def record_kinds(cls) -> Sequence[type[Record]]:
        """The kinds of record orch defines: a project, a repo, a feature, a task, a note and the
        counter their keys come from (26.2-26.5), a link (26.4), an agent, its session and the
        events of it (27.1), the tokens its agents call udl with (27.2), something only a person
        can do (28.2), a question with its thread (28.1), and a manager's review of a report's
        pull (27.8), and a person's chat with their own harness, what it asked to do and the
        token it was given (27.11)."""
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
            stored.Chat,
            stored.ChatPermission,
            stored.ChatToken,
            stored.HumanAction,
            stored.Question,
            stored.Message,
            stored.Review,
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
        """The events orch publishes (28.5): its links' changes (26.4), its tasks' — a move, a
        ready pull, a block, a merge — its questions' (28.1), its people's actions (28.2), its
        agents' limits and idle queues, and its sessions' (27.7)."""
        return (
            LINK_CHANGED,
            PROJECT_CHANGED,
            TASK_CHANGED,
            TASK_READY,
            TASK_BLOCKED,
            TASK_MERGED,
            QUESTION_ASKED,
            QUESTION_ANSWERED,
            ACTION_NEEDED,
            AGENT_LIMITED,
            AGENT_IDLE,
            SESSION_STARTED,
            SESSION_EVENT,
            SESSION_ENDED,
            SESSION_FAILED,
            CHAT_STARTED,
            CHAT_EVENT,
            CHAT_ANSWERED,
            CHAT_FAILED,
            CHAT_ENDED,
            CHAT_PERMISSION,
        )

    def __init__(
        self, settings: OrchSettings, *, clock: Clock, records: AppRecords | None = None
    ) -> None:
        super().__init__(settings, clock=clock, records=records)
        self._harnesses: Harnesses | None = None
        self._chats: Chats | None = None

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
                cli=CLISpec(path=(*_QUEUE, "add"), arguments=("key",)),
            ),
            cls.operation(
                "reorder_queue",
                title="Reorder a queue",
                hints=OperationHints(idempotent=True),
                cli=CLISpec(path=(*_QUEUE, "set"), arguments=("agent",)),
            ),
            cls.operation(
                "unassign",
                title="Unassign a task",
                cli=CLISpec(path=(*_QUEUE, "rm"), arguments=("key",)),
            ),
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
                cli=CLISpec(path=(*_QUEUE, "show"), arguments=("agent",)),
            ),
            cls.operation(
                "my_task",
                title="What an agent is on",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=(*_QUEUE, "mine"), arguments=("agent",)),
            ),
            cls.operation(
                "suggest",
                title="What to do next",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=("orch", "suggest")),
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
            cls.operation("ask", title="Ask a question"),
            cls.operation("block", title="Block a task on a person"),
            cls.operation("answer", title="Answer a question"),
            cls.operation("reply", title="Reply on a question"),
            cls.operation("escalate", title="Pass a question up"),
            cls.operation("close_question", title="Close a question unanswered"),
            cls.operation(
                "list_questions",
                title="The questions waiting",
                hints=OperationHints(read_only=True),
            ),
            cls.operation(
                "get_question",
                title="A question with its thread",
                hints=OperationHints(read_only=True),
            ),
            cls.operation("review_task", title="A manager's verdict on a report's pull"),
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
                # (28.7: not admin, because the project's merger may be a reviewer, and the role
                # table cannot know which agent that is: the operation itself decides who may
                # merge — a person, the assignee where `merge = agent`, or the project's merger.)
                hints=OperationHints(destructive=True, open_world=True),
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
                "inbox",
                title="What waits on a person",
                hints=OperationHints(read_only=True, open_world=True),
                # (How the CLI draws it, and its `--watch`, are the CLI's own: udl.interfaces.cli)
                cli=CLISpec(path=_INBOX),
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
                "set_auto_merge",
                title="Turn auto merge on or off, with its merger",
                hints=OperationHints(admin=True, mcp=False),
                cli=CLISpec(path=(*_PROJECTS, "auto-merge"), arguments=("project",)),
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
                "progress",
                title="A project's progress: its burn-up, pace and forecast",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=("orch", "progress"), arguments=("project",)),
            ),
            cls.operation(
                "estimate",
                title="A task's size, its estimate and the time worked on it",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=("orch", "estimate"), arguments=("project", "task")),
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
                cli=CLISpec(path=(*_TASKS, "set"), arguments=("key",)),
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
            cls.operation(
                "import_plan",
                title="Read plan files into udl",
                hints=OperationHints(admin=True, idempotent=True),
                cli=CLISpec(path=("orch", "import"), arguments=("files",)),
            ),
            cls.operation(
                "list_agents",
                title="List the agents",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=_AGENTS, options={"project": "--project"}),
            ),
            cls.operation(
                "get_agent",
                title="An agent, its queue and its session",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=(*_AGENTS, "show"), arguments=("name",)),
            ),
            cls.operation(
                "set_agent",
                title="Create or edit an agent",
                hints=OperationHints(admin=True, idempotent=True),
                cli=CLISpec(path=(*_AGENTS, "set")),
            ),
            cls.operation(
                "delete_agent",
                title="Delete an agent",
                hints=OperationHints(admin=True, destructive=True, idempotent=True),
                cli=CLISpec(path=(*_AGENTS, "rm"), arguments=("name",)),
            ),
            cls.operation(
                "pause_agent",
                title="Pause an agent",
                hints=OperationHints(admin=True, idempotent=True),
                cli=CLISpec(path=(*_AGENTS, "pause"), arguments=("name",)),
            ),
            cls.operation(
                "resume_agent",
                title="Resume an agent",
                hints=OperationHints(admin=True, idempotent=True),
                cli=CLISpec(path=(*_AGENTS, "resume"), arguments=("name",)),
            ),
            cls.operation(
                "get_session",
                title="A session and its transcript",
                hints=OperationHints(admin=True, read_only=True),
                cli=CLISpec(path=(*_AGENTS, "session"), arguments=("session",)),
            ),
            cls.operation(
                "stop_session",
                title="Stop a session",
                hints=OperationHints(admin=True, destructive=True),
                cli=CLISpec(path=(*_AGENTS, "stop"), arguments=("session",)),
            ),
            cls.operation(
                "send",
                title="Send a message into an agent's session",
                hints=OperationHints(open_world=True),
                cli=CLISpec(path=(*_AGENTS, "send"), arguments=("agent", "text")),
            ),
            # -- a person's chat with their own harness (27.11) ---------------------------------
            cls.operation(
                "chat_start",
                title="Start a chat with your own harness",
                hints=OperationHints(open_world=True),
                cli=CLISpec(
                    path=(*_CHATS, "start"),
                    options={
                        "runner": "--runner",
                        "directory": "--in",
                        "model": "--model",
                        "host": "--host",
                        "title": "--title",
                    },
                ),
            ),
            cls.operation(
                "chat_send",
                title="Say something to a chat",
                hints=OperationHints(open_world=True),
                cli=CLISpec(path=(*_CHATS, "send"), arguments=("chat", "text")),
            ),
            cls.operation(
                "chat_list",
                title="Your chats",
                hints=OperationHints(read_only=True),
                cli=CLISpec(path=_CHATS, options={"all": "--all"}),
            ),
            cls.operation(
                "chat_get",
                title="A chat, and what it said",
                hints=OperationHints(read_only=True),
                cli=CLISpec(
                    path=(*_CHATS, "show"), arguments=("chat",), options={"limit": "--limit"}
                ),
            ),
            cls.operation(
                "chat_stop",
                title="End a chat",
                hints=OperationHints(destructive=True),
                cli=CLISpec(path=(*_CHATS, "stop"), arguments=("chat",)),
            ),
            cls.operation(
                "chat_delete",
                title="Forget a chat",
                hints=OperationHints(destructive=True, idempotent=True),
                cli=CLISpec(path=(*_CHATS, "rm"), arguments=("chat",)),
            ),
            cls.operation(
                "chat_ask_permission",
                title="Ask whether the harness may use a tool",
                hints=OperationHints(open_world=True),
            ),
            cls.operation(
                "chat_permission",
                title="Answer what a chat's harness asked",
                hints=OperationHints(),
                cli=CLISpec(path=(*_CHATS, "allow"), arguments=("chat", "request")),
            ),
        ]

    # -- a person's chat with their own harness (27.11) -----------------------------------------

    @property
    def chats(self) -> Chats:
        """People's chats (27.11): the turns their harnesses run, and what they ask before they
        act. One per app, so a chat's turn outlives the call that started it."""
        if self._chats is None:
            service = self.service
            if service is None:
                raise PluginError("The orch app is not running on a service")
            self._chats = Chats(
                service=service,
                records=self.records,
                events=AppEvents(service.bus, "orch", self.event_types()),
                harnesses=self.harnesses,
                clock=self.clock,
                permission_minutes=self.settings.chat_permission_minutes,
            )
        return self._chats

    async def chat_start(self, request: StartChatRequest) -> ChatInfo:
        """Start a chat with your own harness (27.11): your runner, your folder, your model, and
        `manual` permissions so it asks udl before it acts. Only udl's owner starts chats."""
        who = _caller()
        if who is not None and not who.is_owner:
            raise NotAllowed(
                "Only udl's owner starts a chat",
                hint="A chat is a person's own harness; its token reaches that one chat.",
            )
        return await self.chats.start(request, owner=f"human:{_name_of(who)}")

    async def chat_send(self, request: SendChatRequest) -> ChatInfo:
        """Say something to a chat: one turn of its harness, streamed into the chat's events."""
        chat = await self._chat(request.chat)
        return await self.chats.send(chat, request.text)

    async def chat_list(self, request: ListChatsRequest) -> ChatList:
        """Your chats (only udl's owner asks for every chat: a chat's token sees its own)."""
        who = _caller()
        if request.all and who is not None and not who.is_owner:
            raise NotAllowed(
                "Only udl's owner lists every chat",
                hint="A chat's token reads that one chat.",
            )
        owner = None if request.all and (who is None or who.is_owner) else f"human:{_name_of(who)}"
        found = (
            await self.records.list(stored.Chat, RecordQuery())
            if owner is None
            else await stored.ChatRecords(self.records).of(owner)
        )
        items = [
            await self.chats.info(chat, limit=0)
            for chat in sorted(found, key=lambda one: one.updated_at, reverse=True)
        ]
        return ChatList(items=items)

    async def chat_get(self, request: GetChatRequest) -> ChatInfo:
        """One chat, with the newest page of its transcript and what its harness is waiting
        on."""
        return await self.chats.info(await self._chat(request.chat), limit=request.limit)

    async def chat_stop(self, request: StopChatRequest) -> ChatInfo:
        """End a chat: its harness's session ends, and a turn it was in is ended with it."""
        return await self.chats.stop(await self._chat(request.chat))

    async def chat_delete(self, request: DeleteChatRequest) -> ChatInfo:
        """Forget a chat, with its transcript, what its harness asked and the token it was
        given."""
        chat = await self._chat(request.chat)
        info = await self.chats.info(chat, limit=0)
        await self.chats.delete(chat)
        return info

    async def chat_ask_permission(self, request: AskPermissionRequest) -> PermissionAnswer:
        """What a chat's harness asks udl before it acts (27.11's `--permission-prompt-tool`):
        the person answers, and the harness is told — allowed, or denied with why.

        It is the one chat operation a chat's own token may call, and the chat is its own: a
        token never says which chat it is asking for."""
        who = _caller()
        if who is None or not who.is_chat or who.chat is None:
            raise NotAllowed(
                "only a chat's harness asks this, with the chat's own token",
                hint="The person answers a chat's permission prompt; udl's owner does not ask.",
            )
        chat = await self._chat(who.chat)  # (its own chat, never one the request names)
        if await self.chats.allowed(chat, request.tool_name, request.input):
            return PermissionAnswer(behavior="allow", updatedInput=request.input)
        return await self.chats.ask(chat, request)

    async def chat_permission(self, request: PermissionRequest) -> ChatInfo:
        """The person's answer to what a chat's harness asked: allow it once, allow the same
        request for the rest of the chat, or deny it and say why."""
        return await self.chats.answer(await self._chat(request.chat), request)

    async def _chat(self, chat: UUID) -> stored.Chat:
        """The chat called `chat`, if the caller may touch it (its own token touches its own,
        and nobody else's; udl's owner touches any)."""
        found = await stored.ChatRecords(self.records).by_id(chat)
        if found is None:
            raise DoesNotExist(f"There is no chat {chat}", hint="It may have been deleted.")
        who = _caller()
        if who is not None and who.is_chat and who.chat != found.id:
            raise NotAllowed(
                "That is not your chat",
                hint="A chat's token reaches that one chat.",
            )
        return found

    # -- the agents and their sessions (27.9) ---------------------------------------------------

    async def list_agents(self, request: ListAgentsRequest) -> Agents:
        """Every agent, by name, with what it is doing, its queue and its session (the agents
        that work `project`, or all of them)."""
        found = await stored.AgentRecords(self.records).agents()
        if request.project is not None:
            found = [one for one in found if not one.projects or request.project in one.projects]
        return Agents(
            items=[await self._agent_info(one) for one in sorted(found, key=lambda one: one.name)]
        )

    async def get_agent(self, request: GetAgentRequest) -> AgentInfo:
        """One agent, with its queue, its current session and the agents that report to it."""
        return await self._agent_info(await self._require_agent(request.name))

    async def set_agent(self, request: SetAgentRequest) -> Agent:
        """Create or edit an agent, by its name: what is not given is kept, a new one needs a
        title and a runner, and `manager: ""` makes it report to the humans (admin).

        A manager must be another agent whose role is manager, and no chain of managers may
        reach itself (what storing one checks)."""
        agents = stored.AgentRecords(self.records)
        found = await agents.agent(request.name)
        given = request.model_dump(exclude={"name", "manager"}, exclude_none=True)
        if found is None:
            if request.title is None or request.runner is None:
                raise NotAllowed(
                    f"{request.name!r} is a new agent, so it needs a title and a runner",
                    hint="Set both: a runner is what starts its sessions (e.g. claude-code).",
                )
            found = stored.Agent(name=request.name, **given)
        else:
            found = found.model_copy(update=given)
        if request.manager is not None:
            found = found.model_copy(update={"manager": request.manager or None})
        return as_shape(Agent, await agents.save_agent(found))

    async def delete_agent(self, request: DeleteAgentRequest) -> Deleted:
        """Delete an agent, its sessions and its tokens (admin). Raises InUse while it manages
        another agent, or while it has a task that is not finished."""
        return Deleted(deleted=await stored.AgentRecords(self.records).delete_agent(request.name))

    async def pause_agent(self, request: PauseAgentRequest) -> Agent:
        """Pause an agent: nothing is dispatched to it until it is resumed, and its running
        session is left to finish (stopping it is `orch_stop_session`)."""
        return await self._set_paused(request.name, paused=True)

    async def resume_agent(self, request: ResumeAgentRequest) -> Agent:
        """Resume an agent: what it has queued is dispatched again."""
        return await self._set_paused(request.name, paused=False)

    async def get_session(self, request: GetSessionRequest) -> SessionInfo:
        """A session and a page of its transcript, oldest first (`offset` and `limit` page it;
        `total` is how many events it has)."""
        found = await stored.SessionRecords(self.records).session(request.session)
        if found is None:
            raise DoesNotExist(
                f"There is no session {request.session}",
                hint="An agent's current session is in its `orch_get_agent` answer.",
            )
        return await self._session_info(found, offset=request.offset, limit=request.limit)

    async def stop_session(self, request: StopSessionRequest) -> SessionInfo:
        """Stop a session: its runner is told to stop it, and it is ended, so nothing more is
        dispatched to it and its agent's next turn starts a new one."""
        sessions = stored.SessionRecords(self.records)
        found = await sessions.session(request.session)
        if found is None:
            raise DoesNotExist(
                f"There is no session {request.session}",
                hint="An agent's current session is in its `orch_get_agent` answer.",
            )
        if found.state is not SessionState.ENDED:
            try:
                runner = await self.harnesses.runner(found.ref.host, found.ref.runner)
                await runner.stop(found.ref)
            except UDLError as error:
                # (A harness that is gone, or a session it no longer has: what is left to do is
                # end it here, which is what keeps the dispatcher off it — and why it could not
                # be told is written where the session's errors are.)
                found = found.model_copy(update={"last_error": f"stopping it: {error}"})
            found = await sessions.save_session(
                found.model_copy(update={"state": SessionState.ENDED})
            )
        return await self._session_info(found, limit=0)

    async def send(self, request: SendRequest, *, service: UDLService) -> Sent:
        """A message typed into an agent's session (the owner's only): given as a turn of its
        own, through the agent's runner, and kept in its transcript as it happens.

        A session in a turn is not interrupted: `delivery: "steer"` would need a harness that
        takes input mid-turn, and none of udl's do yet, so it is refused rather than pretended."""
        who = _caller()
        if who is not None and who.is_agent:
            raise NotAllowed(
                "a message into an agent's session is the owner's",
                hint="An agent asks with `orch_ask` (28.1); what a person says reaches it there.",
            )
        agent = await self._require_agent(request.agent)
        sessions = stored.SessionRecords(self.records)
        found = await sessions.current(agent.name)
        if found is None:
            raise NotAllowed(
                f"{agent.name!r} has no session to send to",
                hint="Start one by assigning it a task: its dispatcher takes it from there.",
            )
        if request.delivery == "steer":
            raise NotAllowed(
                "a running turn takes no input yet",
                hint="Send it as a turn: the agent reads it when its current one ends.",
            )
        if found.state in (SessionState.STARTING, SessionState.RUNNING):
            raise NotAllowed(
                f"{agent.name!r} is in a turn",
                hint="Wait for it to answer, or stop the session and send again.",
            )
        turns = Turns(
            records=self.records,
            events=AppEvents(service.bus, "orch", self.event_types()),
            harnesses=self.harnesses,
            clock=self.clock,
            settings=self.settings,
            database=service.db,
        )
        ran = await turns.one(agent, found, request.text, wait=False)
        if ran is None:
            raise NotAllowed(
                f"{agent.name!r} is in a turn",
                hint="Wait for it to answer, or stop the session and send again.",
            )
        if ran.outcome is Outcome.STOPPED:
            raise NotAllowed(
                f"{agent.name!r}'s session ended while the message was sent",
                hint="Its next turn starts a new session; send it again after that.",
            )
        return Sent(session=found.id, turn=ran.turn)

    async def _set_paused(self, name: str, *, paused: bool) -> Agent:
        """`name`'s agent, paused or resumed, as an operation answers with it."""
        agents = stored.AgentRecords(self.records)
        found = await self._require_agent(name)
        return as_shape(Agent, await agents.save_agent(found.model_copy(update={"paused": paused})))

    async def _require_agent(self, name: str) -> stored.Agent:
        """The agent called `name`, or DoesNotExist."""
        found = await stored.AgentRecords(self.records).agent(name)
        if found is None:
            raise DoesNotExist(
                f"There is no agent {name!r}",
                hint="List the agents to see their names, or add one with `orch_set_agent`.",
            )
        return found

    async def _agent_info(self, agent: stored.Agent) -> AgentInfo:
        """`agent`, as the operations answer with it: its status, its session, its queue (in
        every project: a task's key is only unique within one) and its reports."""
        sessions = stored.SessionRecords(self.records)
        session = await sessions.current(agent.name)
        assigned = await self.records.list(stored.Task, RecordQuery(where={"assignee": agent.name}))
        queue = sorted(
            (one for one in assigned if one.state in (QUEUED, IN_PROGRESS)),
            key=lambda one: (one.position is None, one.position or 0, one.key),
        )
        return AgentInfo(
            agent=as_shape(Agent, agent),
            status=as_shape(Agent, agent).status(
                None if session is None else as_shape(AgentSessionShape, session),
                offline=agent.offline,
            ),
            session=None if session is None else as_shape(AgentSessionShape, session),
            queue=[_entry(one) for one in queue],
            reports=[
                one.name for one in await stored.AgentRecords(self.records).reports(agent.name)
            ],
        )

    async def _session_info(
        self, session: stored.AgentSession, *, offset: int = 0, limit: int = 50
    ) -> SessionInfo:
        """`session`, with a page of its transcript (`limit` 0: none of it, only the count)."""
        events = await stored.SessionRecords(self.records).transcript(session.id)
        page = events[offset : offset + limit] if limit else []
        return SessionInfo(
            id=session.id,
            agent=session.agent,
            project=session.project,
            task=session.task,
            ref=session.ref,
            state=session.state,
            turns=session.turns,
            nudges=session.nudges,
            limited_until=session.limited_until,
            tokens_in=session.tokens_in,
            tokens_out=session.tokens_out,
            cost=session.cost,
            last_text=session.last_text,
            created_at=session.created_at,
            events=[
                SessionEventShape(turn=one.turn, at=one.created_at, event=one.event) for one in page
            ],
            total=len(events),
        )

    # -- the harnesses (25.11) -----------------------------------------------------------------

    async def list_runners(self, request: ListRunnersRequest) -> RunnerStatusList:
        """Every harness on every host: its version, whether it is signed in and on which plan,
        what it can run there, and what its own doctor says is wrong."""
        return await self.harnesses.all(request)

    async def runner_install(self, request: InstallRunnerRequest) -> RunnerInstalled:
        """Install or update a harness on a host: only its own fixed argv, and never while a
        session is running there."""
        return await self.harnesses.install(request)

    async def runner_setup(
        self, request: SetupRunnerRequest, *, service: UDLService
    ) -> RunnerSetup:
        """Write a harness's deny policies and its agent files on a host (idempotent), and set
        the provider keys it needs: the keys of the AI connections udl has (O-D5: an `op://`
        reference works, because what is set is what the store gave)."""
        return await self.harnesses.setup(
            request,
            roles=[role.value for role in AgentRole],
            env=await self.provider_keys(service.db, service.secrets),
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

    async def provider_keys(self, db: DBManager, secrets: SecretStore) -> dict[str, str]:
        """The provider keys a harness's environment is given when it is set up: the ones of the
        AI providers udl is set up with, by the variable their service takes them in.

        A connection's options are read as the provider reads them (`load_secrets`), so a key
        kept as a reference — `op://…`, or whatever the store holds — is read from the store or
        resolved, and what is sent is never the reference. A connection that is off, or one of
        another provider (one that takes no key), sends nothing; and a key udl has not been
        given is not invented: a harness signed in with a plan needs none."""
        wanted = {
            "openrouter": "OPENROUTER_API_KEY",
            "openai": "OPENAI_API_KEY",
        }
        keys: dict[str, str] = {}
        connections = await db.list_records(AIProviderConnection, RecordQuery())
        for connection in connections:
            variable = wanted.get(connection.plugin)
            if variable is None or not connection.enabled:
                continue
            options = await load_secrets(_Key.model_validate(connection.options), secrets)
            key = options.api_key
            if isinstance(key, SecretStr):
                keys[variable] = key.get_secret_value()
        return keys

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
        # (Auto merge and its merger have one writer, `orch_set_auto_merge`: otherwise a chat's
        # owner-level token could turn it on through here, 28.7.)
        for field, unset in (("auto_merge", False), ("merger", None)):
            if field not in given:
                continue
            if found is None:
                # (A new project starts with auto merge off: making one on is the switch's, too.)
                if given[field] != unset:
                    raise NotAllowed(
                        f"{request.name!r} is a new project, and {field} is "
                        "`orch_set_auto_merge`'s to set",
                        hint="Make it, then turn auto merge on there: it is the owner's alone.",
                    )
                given.pop(field)
            elif given[field] != getattr(found, field):
                raise NotAllowed(
                    f"{request.name!r}'s {field} is `orch_set_auto_merge`'s to change",
                    hint="Turn auto merge on or off, and name its merger, there: it is the "
                    "owner's alone.",
                )
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

    async def set_auto_merge(self, request: SetAutoMergeRequest) -> Project:
        """Turn auto merge on or off, with its merger (28.7): the owner's alone, since a model
        that could turn it on could give itself the merge.

        Turning it on needs a merger — an agent of role `reviewer` or `manager` that works on this
        project — and turning it off keeps the one stored, so on again is the same merger. It is
        the only writer of both fields: `orch_set_project` and the importer refuse a change."""
        who = _caller()
        if who is not None and not who.is_owner:
            # (A chat's token is the person's, but it is a harness's to spend: it is refused
            # here as an agent's is, 27.11's rule for what a chat may not do.)
            raise NotAllowed(
                "only a person turns auto merge on or off",
                hint="A merger merges what it reviewed; who the merger is, is the owner's call.",
            )
        project = await self._project(request.project)
        projects = stored.ProjectRecords(self.records)
        found = await projects.project(project)
        if found is None:
            raise DoesNotExist(f"There is no project called {project!r}")
        merger = request.merger or found.merger
        if request.on:
            if not merger:
                raise NotAllowed(
                    "auto merge needs a merger",
                    hint="Name the agent that merges: a reviewer or a manager of this project.",
                )
            agent = await stored.AgentRecords(self.records).agent(merger)
            if agent is None:
                raise DoesNotExist(f"There is no agent called {merger!r}")
            if agent.role not in (AgentRole.REVIEWER, AgentRole.MANAGER):
                raise NotAllowed(
                    f"{merger!r} is a merger only if it is a reviewer or a manager",
                    hint=(
                        "A merger reviews what it merges; an implementer merges only its own, "
                        "which is `merge = agent`'s."
                    ),
                )
            if agent.projects and project not in agent.projects:
                raise NotAllowed(
                    f"{merger!r} does not work on {project!r}",
                    hint=f"Its projects are {', '.join(agent.projects)}: it cannot merge here.",
                )
        saved = await projects.save_project(
            found.model_copy(update={"auto_merge": request.on, "merger": merger})
        )
        service = self.service
        if service is None:
            raise PluginError("The orch app is not running on a service")
        events = AppEvents(service.bus, self.name, self.event_types())
        await events.emit(
            PROJECT_CHANGED.name,
            ProjectChanged(
                project=project,
                auto_merge=saved.auto_merge,
                merger=saved.merger,
            ),
            # (A second on, or off, with the same merger is news again: the time is the key, so
            # the bus does not drop it as one it has seen.)
            dedupe_key=(
                f"{project}:auto_merge:{saved.auto_merge}:{saved.merger}:"
                f"{saved.updated_at.isoformat()}"
            ),
        )
        return as_shape(Project, saved)

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
        tasks = await stored.TaskRecords(self.records).tasks(project)
        return Features(
            project=project,
            features=[as_shape(Feature, one) for one in found],
            totals=Totals.of(tasks),
        )

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
            totals=Totals.of(tasks),
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

    async def progress(self, request: ProgressRequest) -> Progress:
        """A project's progress (27.10): its burn-up, its pace, its forecast, each feature's
        points, what a point costs each model, and the tasks merged lately."""
        return await Reporting(self.records, now=self.clock.now()).progress(
            request.project, since=request.since
        )

    async def estimate(self, request: EstimateRequest) -> Estimate:
        """One task's size, what it is expected to take at its model's rate, and the time worked
        on it so far (its history, less its waits)."""
        return await Reporting(self.records, now=self.clock.now()).estimate(
            request.project, request.task
        )

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
            await self._plans_in(project, feature)
            key = request.key or await self._next_key(tasks, project, feature)
            found = stored.Task(project=project, feature=feature, key=key, **given)
        else:
            if request.feature is not None and request.feature != found.feature:
                raise NotAllowed(
                    f"{found.key!r} is a task of {found.feature!r}, not of {request.feature!r}",
                    hint="A task does not move between features: set the one it is of.",
                )
            if (found.assignee or "").startswith(queues.HUMAN):
                raise NotAllowed(
                    f"{found.key!r} is a person's task",
                    hint="A person's task is theirs to plan (28.2); an agent plans its reports'.",
                )
            if found.assignee:
                await self._in_subtree(found.assignee)
            else:
                # (A task nobody is on is not anyone's: what holds it is where it is — a manager
                # plans in its reports' features, 27.8.)
                await self._plans_in(project, found.feature)
            found = found.model_copy(update=given)
        if request.size is not None:
            await self._may_size(found, sized=request.size)
        return as_shape(Task, await tasks.save_task(found))

    async def _may_size(self, task: stored.Task, *, sized: Size) -> None:
        """Whether the caller may set a task's size (26.8): udl and a person may; an agent only
        with the `plan` power, never on a task it is the assignee of, and only within its own
        subtree — `plan` sizes its reports' work, as it assigns it (27.8).

        The edit that carries a size is held to the task already (`_in_subtree()` for a task
        someone is on, `_plans_in()` for one nobody is on); this repeats both here, so the power
        is the operation's own rule and not its caller's, and the second walk of the tree is what
        that costs on a sized edit — a rare edit, and the backstop is `_may_size()`."""
        from udl.core.service import principal  # (the caller's, as each interface set it)

        caller = principal()
        if caller is None or caller.kind is not security.PrincipalKind.AGENT:
            return
        agent = await stored.AgentRecords(self.records).agent(caller.name)
        if agent is None or agent.role is not AgentRole.MANAGER or not agent.manages.plan:
            raise NotAllowed(
                f"{caller.name!r} does not size {task.key!r}: it takes the `plan` power",
                hint="A manager's `plan` power is what sizes work; a person may size anything.",
            )
        if task.assignee == caller.name:
            raise NotAllowed(
                f"{task.key!r} is {caller.name!r}'s own task, so it does not size it",
                hint="Who does the work does not say how big it is: its manager, or a person.",
            )
        if task.assignee:
            await self._in_subtree(task.assignee)
        else:
            await self._plans_in(task.project, task.feature)

    async def move_task(self, request: MoveTaskRequest) -> Task:
        """Move a task to another state, through the table (orch.transitions): what it waits on
        is its note while it is blocked, and the move writes the system note that says so."""
        project = await self._project(request.project)
        tasks = stored.TaskRecords(self.records, clock=self.clock)
        found = await self._require_task(project, request.key)
        from udl.core.service import principal  # (the caller's, as each interface set it)

        moved = await tasks.move(found, request.to, by=_by(principal()), note=request.note)
        return as_shape(Task, moved)

    async def split_task(self, request: SplitTaskRequest) -> Tasks:
        """Split a task into several: the first keeps its key (so its links and its notes stay
        with it), and each of the others takes the feature's next key.

        Only a task that has not started is split (`open`, `blocked`): the first piece takes
        the task's own title and spec, which an in-progress or a finished task's are not to
        lose (NotAllowed otherwise)."""
        project = await self._project(request.project)
        tasks = stored.TaskRecords(self.records)
        found = await self._require_task(project, request.key)
        if (found.assignee or "").startswith(queues.HUMAN):
            raise NotAllowed(
                f"{found.key!r} is a person's task",
                hint="A person's task is theirs to plan (28.2); an agent plans its reports'.",
            )
        if found.assignee:
            await self._in_subtree(found.assignee)
        else:
            await self._plans_in(project, found.feature)
        if found.state not in _UNSTARTED:
            raise NotAllowed(
                f"{found.key!r} is {found.state}: only a task that has not started is split",
                hint="A split rewrites the first piece, so it is done before the work begins.",
            )
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

    async def import_plan(self, request: ImportPlanRequest) -> ImportReport:
        """Read plan files into udl: their features and their tasks, by key (26.7).

        A feature or a task the plan names and udl does not have is made; one udl has is changed
        where the plan changed it, and left alone where it did not. A task's state is udl's: a
        plan's mark never moves a task udl has moved on, and the report says where they differ.
        A `dry_run` reports what it would do and writes nothing. What the plan names is read
        and every spec is checked (against `SPEC_MAX`) before anything is written, and the write
        is one batch: an import is all of a plan, or none of it — a spec over the limit is a
        NotAllowed that names the feature or task and its length."""
        project = await self._project(request.project)
        plan = merge(*(read_plan_file(name) for name in request.files))
        features = stored.FeatureRecords(self.records)
        tasks = stored.TaskRecords(self.records)
        report = ImportReport(unplaced=plan.unplaced)
        # (A project that imported before 26.7's fix has a `plan` feature: say so, rather than
        # leaving it silently stale beside the new `plan-<stem>` ones. An import whose own plan
        # feature is `plan` — a file named `___.md` — is writing to it, not finding it stale.)
        if PLAN_KEY not in {one.key for one in plan.features} and (
            await features.feature(project, PLAN_KEY) is not None
        ):
            report.kept.append(f"{PLAN_KEY}: an older import's plan feature, left as it is")
        made = await self._planned(features, tasks, project, plan, report)
        if not request.dry_run:
            await self._write_import(made)
        return report

    async def _planned(
        self,
        features: stored.FeatureRecords,
        tasks: stored.TaskRecords,
        project: str,
        plan: ParsedPlan,
        report: ImportReport,
    ) -> list[Record]:
        """What the import would write: every feature and task the plan names that udl does not
        have, and every one it has that the plan changed, as records.

        Nothing is written here, so a dry run and a real import agree; and every spec's length
        is checked (against `SPEC_MAX`), so a too-long one refuses the whole import before
        anything is written rather than half of it."""
        made: list[Record] = []
        for parsed in plan.features:
            if parsed.spec and len(parsed.spec) > SPEC_MAX:
                raise NotAllowed(
                    f"feature {parsed.key}'s spec is {len(parsed.spec)} characters, over the "
                    f"{SPEC_MAX} a spec may be",
                    hint="Split the file, or leave the section out of the plan.",
                )
            found = await features.feature(project, parsed.key)
            title = parsed.title or parsed.key
            if found is None:
                made.append(
                    stored.Feature(
                        project=project,
                        key=parsed.key,
                        title=title,
                        spec=parsed.spec,
                        order=int(parsed.key) if parsed.key.isdigit() else 0,
                    )
                )
                report.created.append(parsed.key)
            else:
                changed: dict[str, object] = {}
                if found.title != title:
                    changed["title"] = title
                if parsed.spec and found.spec != parsed.spec:
                    changed["spec"] = parsed.spec
                if changed:
                    made.append(found.model_copy(update=changed))
                    report.updated.append(parsed.key)
                else:
                    report.unchanged.append(parsed.key)
            for one in parsed.tasks:
                await self._planned_task(tasks, project, parsed.key, one, made, report)
        return made

    async def _planned_task(
        self,
        tasks: stored.TaskRecords,
        project: str,
        feature: str,
        parsed: ParsedTask,
        made: list[Record],
        report: ImportReport,
    ) -> None:
        """One task the plan names, as a record in the same batch as its feature (made, or
        kept): one write is what keeps a task from being left without its feature, not the
        order within it, so there is no per-task check here."""
        if parsed.spec and len(parsed.spec) > SPEC_MAX:
            raise NotAllowed(
                f"task {parsed.key}'s spec is {len(parsed.spec)} characters, over the "
                f"{SPEC_MAX} a spec may be",
                hint="Split the card, or leave the section out of the plan.",
            )
        title = parsed.title or parsed.key
        held = await tasks.task(project, parsed.key)
        if held is None:
            made.append(
                stored.Task(
                    project=project,
                    feature=feature,
                    key=parsed.key,
                    title=title,
                    spec=parsed.spec,
                    needs=parsed.needs,
                    state=parsed.state,
                    size=parsed.size,
                )
            )
            report.created.append(parsed.key)
            return
        changed: dict[str, object] = {}
        if held.title != title:
            changed["title"] = title
        if parsed.spec and held.spec != parsed.spec:
            changed["spec"] = parsed.spec
        if parsed.needs and held.needs != parsed.needs:
            changed["needs"] = parsed.needs
        if parsed.size is not None and held.size != parsed.size:
            changed["size"] = parsed.size
        if changed:
            made.append(held.model_copy(update=changed))
            report.updated.append(parsed.key)
        else:
            report.unchanged.append(parsed.key)
        if parsed.state != held.state:
            report.kept.append(
                f"{parsed.key}: udl has it {held.state}, the plan says {parsed.state}"
            )

    async def _write_import(self, made: list[Record]) -> None:
        """Everything the import planned, as one write — all of it, or none: its features and
        its tasks, with each feature's status following the tasks the batch writes."""
        if not made:
            return
        await stored.TaskRecords(self.records).import_all(made)
        for one in made:
            if isinstance(one, stored.Feature):
                await scan_links(self.records, one.project, OwnerKind.FEATURE, one.key, one.spec)
            elif isinstance(one, stored.Task):
                await scan_links(self.records, one.project, OwnerKind.TASK, one.key, one.spec)

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
                    clock=self.clock,
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
        if request.agent:
            await self._in_subtree(request.agent)
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

    # -- questions (28.1) ----------------------------------------------------------------------

    async def ask(self, request: AskRequest) -> Question:
        """Ask something only the manager or a person can answer: it goes to the agent's manager
        when that manager may answer, and to the humans when it may not.

        A blocking question moves the task to `blocked`, so the agent's loop takes its next
        queued one (27.7); a non-blocking one leaves it working."""
        project = await self._project(request.project)
        found = await asked.ask(
            stored.QuestionRecords(self.records),
            project=project,
            task=request.task,
            title=request.title,
            text=request.text,
            options=request.options,
            blocking=request.blocking,
            tasks=request.tasks,
            features=request.features,
            asked_by=await self._asker(request.task, project=project),
            clock=self.clock,
        )
        return _question(found)

    async def block(self, request: BlockRequest) -> Question:
        """Say that only a person can do this (28.1): `orch_ask`, blocking."""
        project = await self._project(request.project)
        found = await asked.block(
            stored.QuestionRecords(self.records),
            project=project,
            task=request.task,
            reason=request.reason,
            asked_by=await self._asker(request.task, project=project),
            clock=self.clock,
        )
        return _question(found)

    async def answer(self, request: AnswerRequest) -> Question:
        """Answer a question: the answer is written as a `decision` note on every task and
        feature it names, and a task it blocked is queued again at the front.

        An agent answers only what was asked of it (a manager its reports' questions); a person
        answers any."""
        project = await self._project(request.project)
        who = _caller()
        if who is not None and who.is_agent:
            mine = await stored.QuestionRecords(self.records).question(project, request.key)
            if mine is None:
                raise DoesNotExist(f"There is no question called {request.key!r} in {project!r}")
            if mine.to != who.name:
                raise NotAllowed(
                    f"{request.key!r} is not yours to answer: it is asked of {mine.to!r}",
                    hint="A manager answers what its own reports ask it.",
                )
        found = await asked.answer(
            stored.QuestionRecords(self.records),
            project=project,
            key=request.key,
            answer=request.answer,
            by=_principal(),
            clock=self.clock,
        )
        return _question(found)

    async def reply(self, request: ReplyRequest) -> Message:
        """Add a follow-up to a question's thread, from either side.

        An agent says its piece only on a question it asked or one asked of it."""
        project = await self._project(request.project)
        await self._may_touch(project, request.key, sides=True)
        said = await asked.reply(
            stored.QuestionRecords(self.records),
            project=project,
            key=request.key,
            text=request.text,
            by=_principal(),
        )
        return Message(text=said.text, author=said.author, at=said.at)

    async def escalate(self, request: EscalateRequest) -> Question:
        """Pass a question up: to the manager's manager when that one may answer, and to the
        humans at the top."""
        project = await self._project(request.project)
        await self._may_touch(project, request.key)
        found = await asked.escalate(
            stored.QuestionRecords(self.records),
            project=project,
            key=request.key,
            why=request.why,
            by=_principal(),
        )
        return _question(found)

    async def close_question(self, request: CloseQuestionRequest) -> Question:
        """Close a question nobody answered, with what was done about it instead.

        An agent closes only what was asked of it; a person closes any."""
        project = await self._project(request.project)
        await self._may_touch(project, request.key)
        found = await asked.close(
            stored.QuestionRecords(self.records),
            project=project,
            key=request.key,
            outcome=request.outcome,
        )
        return _question(found)

    async def _may_touch(self, project: str, key: str, *, sides: bool = False) -> None:
        """Refuse an agent that may not touch question `key`: what is asked of it, and — with
        `sides` — what it asked itself. A person (or udl itself) may touch any.

        Raises DoesNotExist when there is no such question, and NotAllowed when it is another's."""
        who = _caller()
        if who is None or not who.is_agent:
            return
        found = await stored.QuestionRecords(self.records).question(project, key)
        if found is None:
            raise DoesNotExist(f"There is no question called {key!r} in {project!r}")
        mine: set[str] = {found.to}
        if sides and found.asked_by:
            mine.add(found.asked_by)
        if who.name not in mine:
            raise NotAllowed(
                f"{key!r} is not yours: it is asked of {found.to!r}"
                + (f" and was asked by {found.asked_by!r}" if found.asked_by else ""),
                hint="An agent touches the questions asked of it (and, in a thread, its own).",
            )

    async def list_questions(self, request: ListQuestionsRequest) -> Questions:
        """The questions waiting, oldest first, narrowed by state, who they are asked of, and the
        task they were asked in. It reads: nothing changes."""
        project = await self._project(request.project)
        found = await stored.QuestionRecords(self.records).questions(
            project, state=request.state, to=request.to, task=request.task
        )
        return Questions(project=project, questions=[_question(one) for one in found])

    async def get_question(self, request: GetQuestionRequest) -> AQuestion:
        """One question with everything about it: its thread, and the tasks and features it
        names. It reads: nothing changes."""
        project = await self._project(request.project)
        questions = stored.QuestionRecords(self.records)
        found = await questions.question(project, request.key)
        if found is None:
            raise DoesNotExist(f"There is no question called {request.key!r} in {project!r}")
        thread = await questions.thread(found)
        named = await asked.named(questions, found)
        return AQuestion(
            question=_question(found),
            thread=[Message(text=one.text, author=one.author, at=one.at) for one in thread],
            tasks=[one.key for kind, one in named if kind is OwnerKind.TASK],
            features=[one.key for kind, one in named if kind is OwnerKind.FEATURE],
        )

    async def _asker(self, key: str, *, project: str) -> str:
        """Who is asking: the caller's name. An agent asks only in its own task, as it pushes
        only its own work."""
        who = _caller()
        if who is None:
            return ""
        if not who.is_agent:
            return f"human:{who.name}"
        found = await stored.TaskRecords(self.records).task(project, key)
        if found is None:
            raise DoesNotExist(f"There is no task called {key!r} in {project!r}")
        if found.assignee != who.name:
            raise NotAllowed(
                f"{key!r} is not yours to ask about: it is {found.assignee or 'nobody'}'s",
                hint="An agent asks only in the task it is on.",
            )
        return who.name

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
        # (The app's clock: this move ends a working stretch, and a stretch's hours are what
        # 27.10 learns a model's rate from.)
        await stored.TaskRecords(self.records, clock=self.clock).save_all(
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
        ready = order([one for one in rows if one.state == READY and not one.hold])
        if found.auto_merge:
            # (The first ready pull the merger may act on is the one it is on; the rest are next
            # in its queue. Its own task, where the project has no review bot, is nobody's but a
            # person's — marked so, and passed over — and a `changes asked` row keeps its state.)
            by_key = {one.key: one for one in tasks}
            marked: list[MergeRow] = []
            on_it = True
            for one in ready:
                own = (
                    found.merger is not None
                    and not found.review_bot
                    and (by_key.get(one.task) is not None)
                    and by_key[one.task].assignee == found.merger
                )
                # (Only what it can merge now is its to be on: a pull with a blocker of its own —
                # a check still running, a pull stacked on one not merged — or the merger's own
                # with no review bot, or one it asked changes of, waits where it is. A pull
                # stacked on one of those is blocked by it, so it waits too, 28.7.)
                if one.blockers or own or one.merger is MergerState.CHANGES_ASKED:
                    marked.append(
                        one.model_copy(
                            update={
                                "blockers": (
                                    [*one.blockers, "waiting for a person: the merger's own task"]
                                    if own and not one.blockers
                                    else list(one.blockers)
                                )
                            }
                        )
                    )
                    continue
                marked.append(
                    one.model_copy(
                        update={
                            "merger": MergerState.REVIEWING if on_it else MergerState.NEXT,
                        }
                    )
                )
                on_it = False
            ready = marked
        return MergeList(
            auto_merge=found.auto_merge,
            merger=found.merger,
            ready=ready,
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

    async def inbox(self, request: InboxRequest, *, service: UDLService) -> Inbox:
        """Everything that waits on a person (28.4): what is ready to merge, the open questions,
        what only a person can do, and what every agent is doing — in one read.

        The three lists keep each one's own rules (28.3's merge order, blocking questions first
        and newest first within them, actions now/after/later), so one call is what a person
        reads today in human-comms.md. It reads: nothing changes."""
        project = await self._project(request.project)
        merge = await self.merge_list(MergeListRequest(project=project), service=service)
        # (Only what is asked of the humans: a question a manager is asked waits on that agent,
        # not on a person. Blocking first, then newest first: what blocks an agent today is what
        # a person reads first, and the rest are the most recent asks. The records carry the
        # times the shape does not, so the inbox reads them itself.)
        open_questions = await stored.QuestionRecords(self.records).questions(
            project, state=QuestionState.OPEN, to="humans"
        )
        questions = [
            _question(one)
            for one in sorted(
                open_questions, key=lambda one: (not one.blocking, -one.created_at.timestamp())
            )
        ]
        to_do = await self.list_actions(ListActionsRequest(project=project))
        return Inbox(
            project=project,
            merge=merge,
            auto_merge=merge.auto_merge,
            merger=merge.merger,
            questions=questions,
            actions=list(to_do.actions),
            agents=await self._agent_lines(),
            updated_at=self.clock.now(),
        )

    async def _agent_lines(self) -> list[AgentLine]:
        """What every agent is doing, by name: its role, its state from its session, and the task
        it is on (27.1's records and 27.7's sessions, derived — never stored)."""
        agents = stored.AgentRecords(self.records)
        sessions = stored.SessionRecords(self.records)
        lines: list[AgentLine] = []
        for stored_agent in await agents.agents():
            current = await sessions.current(stored_agent.name)
            agent = as_shape(agents_shape.Agent, stored_agent)
            line = AgentLine(
                name=agent.name,
                title=agent.title,
                role=agent.role,
                state=agent.status(
                    None if current is None else as_shape(agents_shape.AgentSession, current),
                    offline=stored_agent.offline,
                    now=self.clock.now(),
                ),
                task=current.task or None if current is not None else None,
                since=current.updated_at if current is not None else None,
            )
            lines.append(
                line.model_copy(
                    update={"line": agent_line(line.name, line.role, line.state, line.task)}
                )
            )
        return lines

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
        # (The newest verdict on this pull: `changes asked` is the merger's, and a review of a
        # head the pull has since moved past says nothing about this one.)
        reviews = await stored.ReviewRecords(self.records).for_task(project.name, task.key)
        asked = reviews[0] if reviews else None
        if asked is not None and link.state.head_sha and asked.head != link.state.head_sha:
            asked = None
        return MergeRow(
            merger=(
                MergerState.CHANGES_ASKED
                if (
                    project.auto_merge
                    and project.merger is not None
                    and asked is not None
                    and asked.verdict == "changes"
                    and asked.by == project.merger
                )
                else None
            ),
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
        auto = (
            found.auto_merge
            and found.merger is not None
            and who is not None
            and who.is_agent
            and who.name == found.merger
        )
        if (
            who is not None
            and who.is_agent
            and not auto
            and not (
                not found.auto_merge
                and found.merge == MergePolicy.AGENT
                and task.assignee == who.name
            )
        ):
            raise NotAllowed(
                f"{request.key!r} is not {who.name}'s to merge",
                hint=(
                    "A person merges a ready pull, unless the project's merge is `agent` and the "
                    "caller is the task's own assignee, or it is the project's merger and auto "
                    "merge is on."
                ),
            )
        if auto and task.assignee == found.merger and not found.review_bot:
            # (Nobody reviews the reviewer: with no bot to say whether its own pull is good, the
            # merger's own task waits for a person, 28.7.)
            raise NotAllowed(
                f"{request.key!r} is waiting for a person: the merger's own task",
                hint=(
                    "A merger merges its own task only where the project has a review bot; "
                    "without one, it is a person's to merge."
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
            # (An auto merge is a review — the merger approved this head — so a project with
            # nothing after its base sees it done, as a person's merge does.)
            reviewed=auto or who is None or not who.is_agent,
            promoted=bool(found.promotion),
            auto=auto,
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
        auto: bool = False,
    ) -> Merged:
        """Merge `task`'s pull at `pull.head_sha` and do what a merge sets off: the one place a
        merge is made, whether a person asked for it or its own agent marked it ready."""
        link = await self._pull_url(project, task.key)
        before = task.state
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
            auto=auto,
        )
        # (28.6: a merged task's worktree and branch are taken away, so the next merge is not
        # held up by a checkout of work that is already in `main`.)
        moved = await self._removed(service, moved, by=by)
        if moved.state != before:
            events = AppEvents(service.bus, self.name, self.event_types())
            await events.emit(
                TASK_MERGED.name,
                TaskMerged(
                    project=project,
                    task=task.key,
                    pull=link,
                    sha=pull.head_sha,
                    by=by,
                    auto=auto,
                ),
                dedupe_key=f"{project}:{task.key}:{pull.head_sha}",
            )
        return Merged(task=task.key, url=link, sha=pull.head_sha, state=moved.state)

    async def _removed(self, service: UDLService, task: stored.Task, *, by: str) -> stored.Task:
        """`task` with its worktree and its branch taken away, now that it is merged (27.5's
        `remove()`), and its record saying it has neither.

        It never fails a merge: the pull is merged whatever became of the checkout, so anything
        that stops a removal (a repo it cannot find, a host udl cannot reach, a checkout that is
        already gone) is written on the task's notes and the task comes back as it was.

        Nor does it take away the worktree of the agent that made the merge itself — with
        `merge = agent`, `orch_ready` merges from inside that worktree, and its own session is
        running in the folder a removal would take away. That worktree stays, with a note saying
        so, until a person takes it away (`git worktree remove` and `git branch -D` in the repo's
        checkout); nothing in udl does it when the session ends (left out of 28.6, as its card
        says)."""
        if not task.worktree or not task.branch:
            return task
        if by == task.assignee:
            return await self._left(
                task, "its worktree stays until a person takes it away: its session is in it"
            )
        try:
            agent = await stored.AgentRecords(self.records).agent(task.assignee or "")
            if agent is None:
                return await self._left(
                    task,
                    "its worktree could not be taken away: udl does not know its agent's host",
                )
            repo = await self._repo_of(task)
            hosts = service.hosts
            host = await hosts.get(agent.host)
            found = space.Worktree(
                host=host.name,
                path=task.worktree,
                branch=task.branch,
                base=repo.base_branch,
            )
            await space.remove(found, repo=repo, shell=await hosts.shell(host.name))
        except (PluginError, UDLError) as error:
            return await self._left(task, f"its worktree could not be taken away: {error}")
        return await stored.TaskRecords(self.records).save_task(
            task.model_copy(update={"worktree": None, "branch": None})
        )

    async def _left(self, task: stored.Task, why: str) -> stored.Task:
        """`task` as it is, with `why` its worktree is still there on its notes."""
        await stored.NoteRecords(self.records).system(OwnerKind.TASK, task.id, why)
        return task

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
        auto: bool = False,
    ) -> stored.Task:
        """What a merge sets off (28.3), however it happened: `merge.after_merge` (the task's
        state, the rebase notes, the actions waiting on it)."""
        changed = await after_merge(
            self.records,
            project=project,
            key=key,
            by=by,
            reviewed=reviewed,
            promoted=promoted,
            auto=auto,
            clock=self.clock,
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
        # (udl reads GitHub on its own behalf: a merger may be a reviewer, which is not given
        # GitHub's operations as tools, 28.7.)
        from udl.interfaces.security import current_principal

        calling = current_principal.set(None)
        try:
            detail = await service.call("github_get_pull", {"repo": repo, "number": number})
        finally:
            current_principal.reset(calling)
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
        manager, approval = await self._reviewed(project, task, pull.head_sha)
        return assess(
            pull,
            bot=found.review_bot,
            task=task,
            tasks=tasks,
            manager=manager,
            approval=approval,
        )

    async def _reviewed(
        self, project: str, task: stored.Task, head: str
    ) -> tuple[str | None, ManagerVerdict | None]:
        """The manager whose OK `task` needs for `head`, and what it said (None when it has not
        looked): the nearest agent above the task's assignee that has the `review` power (27.8)."""
        if not task.assignee or task.assignee.startswith("human:"):
            return None, None
        agents = stored.AgentRecords(self.records)
        above = managers_of(await agents.agents(), task.assignee)
        for one in above:
            if not one.manages.review:
                continue
            found = await stored.ReviewRecords(self.records).for_head(project, task.key, head)
            # (Only this manager's review is its OK: one saved under another name — a report's
            # own, say — is not the manager's verdict, 27.8.)
            return one.name, found.verdict if found is not None and found.by == one.name else None
        return None, None

    async def _plans_in(self, project: str, feature: str) -> None:
        """Refuse a change to a feature's tasks when none of the caller's reports works on it
        (27.8: `plan` is its reports' work, so it reaches their features and no other).

        The whole subtree counts, as it does for every other power: a manager's reports' reports
        work in features that are its own. A person, or an agent without the `plan` power, plans
        anywhere; a feature whose only work is a person's (`human:<name>`) has no report of
        anyone's in it, so no manager plans there."""
        who = _caller()
        if who is None or not who.is_agent:
            return
        found = await stored.AgentRecords(self.records).agent(who.name)
        if found is None or not found.manages.plan:
            return
        below = await bosses.subtree(stored.AgentRecords(self.records), who.name)
        tasks = await stored.TaskRecords(self.records).tasks(project)
        if any(one.feature == feature and one.assignee in below for one in tasks):
            return
        raise NotAllowed(
            f"{feature!r} has none of {who.name}'s reports' tasks",
            hint="A manager plans in the features its reports work on (27.8).",
        )

    async def _in_subtree(self, other: str) -> None:
        """Refuse an agent acting on `other` unless it is itself or reports to it (27.8: a
        manager's powers reach its own subtree, never another manager's reports)."""
        who = _caller()
        if who is None or not who.is_agent:
            return
        if await in_subtree(stored.AgentRecords(self.records), who.name, other):
            return
        raise NotAllowed(
            f"{other!r} is not in {who.name}'s subtree",
            hint="A manager acts for its own reports (27.8): an agent outside them is not its "
            "to move, answer for or review.",
        )

    async def review_task(self, request: ReviewRequest) -> Reviewed:
        """A manager's verdict on a report's pull, for the head it read (27.8).

        It is the `review` power's: the caller must have it, and the task must be one of its
        reports'. A review is per head, so a new push needs a new one; readiness asks for it when
        the task's manager has the power. What it says is a `decision` note on the task with the
        manager as author, in the same write."""
        who = _caller()
        if who is None or not who.is_agent:
            raise NotAllowed("a review is an agent's: a person merges, and does not need one")
        agents = stored.AgentRecords(self.records)
        found = await agents.agent(who.name)
        if found is None or not found.manages.review:
            raise NotAllowed(
                f"{who.name} has no review power",
                hint="A manager reviews its reports' work when its `manages.review` is on (27.8).",
            )
        project = await self._project(request.project)
        tasks = stored.TaskRecords(self.records)
        task = await tasks.task(project, request.key)
        if task is None:
            raise DoesNotExist(f"There is no task called {request.key!r} in {project!r}")
        if (task.assignee or "") == who.name:
            raise NotAllowed(
                f"{request.key!r} is {who.name}'s own work",
                hint="A review is someone else's: a manager reviews its reports' work (27.8).",
            )
        # (A merger reviews what it merges, whether or not it is its report's: 28.7 widens 27.8's
        # subtree rule for it, and for its project's ready tasks only.)
        found_project = await stored.ProjectRecords(self.records).project(project)
        merging = (
            found_project is not None
            and found_project.auto_merge
            and found_project.merger == who.name
            and task.state == READY
        )
        if not merging and (
            not task.assignee or not await in_subtree(agents, who.name, task.assignee)
        ):
            raise NotAllowed(
                f"{request.key!r} is not one of {who.name}'s reports' tasks",
                hint="A manager reviews its own reports' work (27.8).",
            )
        head = request.head or await stored.LinkRecords(self.records).head_of(project, request.key)
        if head is None:
            raise NotAllowed(
                f"{request.key!r} has no pull to review",
                hint="Open its pull first (27.5), then review it.",
            )
        review = stored.Review(
            project=project,
            task=task.key,
            head=head,
            verdict=request.verdict,
            reasons=request.reasons,
            by=who.name,
        )
        said = f"review: {request.verdict} {head[:7]}"
        if request.reasons:
            said = f"{said} — {request.reasons}"
        note = stored.Note(
            owner_kind=OwnerKind.TASK,
            owner=task.id,
            note_kind=NoteKind.DECISION,
            text=said,
            author=who.name,
        )
        moved = None
        if request.verdict == "changes" and task.state == READY and merging:
            # (The merger's changes verdict sends a ready pull back to its agent: the reasons are
            # the note above, which the dispatcher tells the agent of, 28.7.)
            actor = Principal(kind="system", name=who.name, role="move")
            moved = transitions.move(task, IN_PROGRESS, by=actor)
            # (The verdict, its note and the move are one write: the pull goes back to work with
            # the review that sent it.)
            await stored.TaskRecords(self.records, clock=self.clock).save_all(
                [moved], also=[review, note, stored.move_note(task, moved, actor)]
            )
        else:
            await self.records.save_all([review, note])
        return Reviewed(
            project=project,
            task=task.key,
            head=head,
            verdict=request.verdict,
            reasons=request.reasons,
            by=who.name,
            at=review.at,
            state=moved.state if moved is not None else task.state,
        )

    # -- the queue (27.3) ----------------------------------------------------------------------

    async def assign(self, request: AssignTaskRequest) -> TaskQueue:
        """Assign a task to an agent: it joins that agent's queue (at its end, or at a position).

        A task another agent holds leaves that agent's queue, and a system note says who must
        stop. A person's task is assigned to `human:<name>` (28.2's To action), never to an
        agent. The answer is the agent's queue, in the order it will work it."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        await self._in_subtree(request.agent)
        held = await tasks.task(project, request.key)
        if held is not None and held.assignee:
            # (A task another manager's report holds is not the caller's to move: moving it
            # would stop that report's session, 27.8.)
            await self._in_subtree(held.assignee)
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
        await self._in_subtree(request.agent)
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


def _caller() -> Caller | None:
    """Who the operation runs for as the interface set it (an owner, or an agent with its own
    token), or None for udl's own Python."""
    from udl.core.service import principal  # (the caller's, as each interface set it)

    return principal()


def _name_of(who: Caller | None) -> str:
    """What `who` is called, as a person's name in a chat: udl's owner, or the agent's name."""
    if who is None or who.is_owner:
        return "owner"
    return who.name


def _principal() -> Principal | None:
    """`_caller()`, as orch's own `Principal`: a person, an agent, or None for udl itself."""
    who = _caller()
    if who is None:
        return None
    if who.is_agent:
        return Principal(kind="agent", name=who.name)
    return Principal(kind="human", name=f"human:{who.name}")


def _question(found: stored.Question) -> Question:
    """`found` as the operations answer with it."""
    return Question(
        key=found.key,
        project=found.project,
        asked_by=found.asked_by,
        to=found.to,
        title=found.title,
        text=found.text,
        options=found.options,
        blocking=found.blocking,
        task=found.task,
        state=found.state,
        answer=found.answer,
        answered_by=found.answered_by,
        outcome=found.outcome,
        escalated=found.escalated,
    )


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
        project=task.project,
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
