"""
-------------
- models.py -
-------------

The shapes of the orchestrator's operations (Phase 26): a project and its repos (26.2), a
feature and a task (26.3), a link (26.4) and a note (26.5). The records they are stored as are
in orch.records, typed with these same aliases, so a shape and a record say the same thing.

These are not records: they are what the operations take and answer, and what the cards' fields
say. Nothing here reaches outside udl: a project is a body of work, and the rules an agent on
it is given are the project's own text.
"""

# System Imports
import string
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Final, Literal, Protocol, Self, get_args

# Package Imports
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    computed_field,
)

# Local Imports
from udl.db.models import JsonObject
from udl.db.records import Name

__all__ = [
    "AFTER",
    "BLOCKED",
    "BRANCH_PARTS",
    "CANCELLED",
    "DEFAULT_BRANCH_PATTERN",
    "DEFAULT_GATE",
    "DONE",
    "DROPPED",
    "IN_PROGRESS",
    "LATER",
    "MERGED",
    "MOVED",
    "NOW",
    "OPEN",
    "POINTS",
    "QUEUED",
    "READY",
    "REF_MARKS",
    "ROLE",
    "STATE",
    "STATE_MARK",
    "AQuestion",
    "Action",
    "ActionState",
    "ActionWhen",
    "Actions",
    "AddLinkRequest",
    "AddNoteRequest",
    "AgentLine",
    "AgentRole",
    "AgentStatus",
    "AnswerRequest",
    "AskRequest",
    "AssignTaskRequest",
    "BlockRequest",
    "BranchName",
    "BranchPattern",
    "CloseQuestionRequest",
    "DeleteProjectRequest",
    "DeleteRepoRequest",
    "DropActionRequest",
    "EscalateRequest",
    "Estimate",
    "EstimateRequest",
    "Feature",
    "FeatureInfo",
    "FeatureProgress",
    "FeatureStatus",
    "Features",
    "FindLinksRequest",
    "FinishActionRequest",
    "ForecastInfo",
    "Gate",
    "GateArg",
    "GateRun",
    "GetFeatureRequest",
    "GetProjectRequest",
    "GetQuestionRequest",
    "GetTaskRequest",
    "GitHubAccount",
    "GitHubSlug",
    "HostName",
    "ImportPlanRequest",
    "ImportReport",
    "Inbox",
    "InboxRequest",
    "Key",
    "Link",
    "LinkKind",
    "Links",
    "ListActionsRequest",
    "ListFeaturesRequest",
    "ListProjectsRequest",
    "ListQuestionsRequest",
    "ListTasksRequest",
    "ManagerVerdict",
    "MergePolicy",
    "Message",
    "ModelRow",
    "MoveTaskRequest",
    "MyTaskNote",
    "MyTaskRequest",
    "MyTaskResponse",
    "NoteInfo",
    "NoteKind",
    "OwnerKind",
    "PrStatusRequest",
    "Principal",
    "Progress",
    "ProgressRequest",
    "Project",
    "ProjectInfo",
    "Projects",
    "Promotion",
    "PullState",
    "PushRequest",
    "PushResult",
    "Question",
    "QuestionState",
    "Questions",
    "QueueEntry",
    "QueueRequest",
    "RateInfo",
    "RecentTask",
    "RemoveLinkRequest",
    "ReorderQueueRequest",
    "ReplyRequest",
    "Repo",
    "ReviewVerdict",
    "Role",
    "SeriesPoint",
    "SetActionRequest",
    "SetFeatureRequest",
    "SetProjectRequest",
    "SetRepoRequest",
    "SetTaskRequest",
    "Size",
    "Sized",
    "Spec",
    "Split",
    "SplitTaskRequest",
    "State",
    "SuggestRequest",
    "Suggestion",
    "Suggestions",
    "Task",
    "TaskInfo",
    "TaskKind",
    "TaskQueue",
    "Tasks",
    "Title",
    "Totals",
    "URLText",
    "UnassignTaskRequest",
    "WaitKind",
    "as_shape",
    "branch_pattern_problem",
    "render_branch",
    "slugify",
]

BRANCH_PARTS: tuple[str, ...] = ("type", "key", "slug")
"""What a branch pattern's {blocks} may be: the kind of change, the task's key, its slug."""

REF_MARKS: Final[str] = "~^:?*[\\"
"""What a git ref may not hold (git check-ref-format)."""

BranchName = Annotated[str, StringConstraints(pattern=r"^[^\s~^:?*\[\\]+$", max_length=200)]
"""A git branch's name (no whitespace, no ref punctuation)."""

GitHubSlug = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")]
"""A repository on GitHub: owner/repo."""

HostName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=64)]
"""The name of a host (as udl.hosts knows it)."""

GitHubAccount = Annotated[str, StringConstraints(pattern=r"^github:[A-Za-z0-9][A-Za-z0-9_.-]*$")]
"""A github account, as udl names one: github:<name> (25.4)."""

BranchPattern = Annotated[str, StringConstraints(max_length=200)]
"""A pattern like `{type}/{key}-{slug}`: udl's parts, and / - . _ between them."""

DEFAULT_BRANCH_PATTERN: str = "{type}/{key}-{slug}"
"""The pattern a repo gets unless it says otherwise (23.1's feat/<task>-<slug>)."""

DEFAULT_GATE: list[str] = ["make", "check"]
"""The gate argv a repo gets unless it says otherwise (23.1)."""

GateArg = Annotated[str, StringConstraints(min_length=1)]
"""One argument of a gate argv (an empty argument is a mistake, never meant)."""

Key = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", max_length=40)]
"""A feature's or a task's key within its project (`F-<n>`, `22`, `22.4`)."""

SPEC_MAX: Final = 100_000
"""What a task's or a feature's spec may be (characters). Named for the refusal that says it."""

Spec = Annotated[str, StringConstraints(max_length=SPEC_MAX)]
"""A task's or a feature's spec, as markdown (`SPEC_MAX` is what an import refuses over)."""

URLText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
"""A URL, as it is stored (a `udl:` one for an internal kind)."""

Text = Annotated[str, StringConstraints(max_length=2000)]
"""A line of text (a link's title), as long as a title is."""


def _not_empty(text: str) -> str:
    """`text` with something in it. Raises ValueError otherwise."""
    if not text.strip():
        raise ValueError("is empty (a title says what it is)")
    return text


Title = Annotated[str, StringConstraints(min_length=1), AfterValidator(_not_empty)]
"""A title: not empty, not only spaces."""


class OwnerKind(StrEnum):
    """What a note (or a link) is about: a feature or a task (26.4-26.5); a question or an
    action, with 28.x."""

    FEATURE = "feature"
    TASK = "task"
    QUESTION = "question"
    ACTION = "action"


class NoteKind(StrEnum):
    """What kind of note it is: what was done, a decision, a finding, a review, or what udl itself
    says (a state change, a task moved to another agent's queue)."""

    PROGRESS = "progress"
    DECISION = "decision"
    FINDING = "finding"
    REVIEW = "review"
    SYSTEM = "system"


class MergePolicy(StrEnum):
    """Who merges a ready PR (O-D3): a human, or the task's own agent when it is green."""

    HUMAN = "human"
    AGENT = "agent"


class ActionWhen(StrEnum):
    """When a person's action is due (28.2): now, after a task merges, or later."""

    NOW = "now"
    AFTER = "after"
    LATER = "later"


class ActionState(StrEnum):
    """Where an action is: still to do, done (with its result), or dropped."""

    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"


type State = Literal[
    "open", "queued", "in_progress", "blocked", "ready", "merged", "done", "moved", "cancelled"
]
"""A task's state (the Concepts table in planning.md)."""

STATE: Final[tuple[State, ...]] = get_args(State.__value__)
"""Every state a task is in (read from `State`, so there is one list of them)."""

type Role = Literal["assign", "start", "block", "ready", "merge", "review", "move", "cancel"]
"""What a caller is doing when it moves a task: one role per verb (26.3's table)."""

ROLE: Final[tuple[Role, ...]] = get_args(Role.__value__)
"""Every role, read from `Role` (one list of them)."""

type TaskKind = Literal["build", "human", "review"]
"""What kind of work a task is (the card's build | human | review)."""

type ManagerVerdict = Literal["approve", "changes"]
"""A manager's verdict on a report's pull (27.8): `approve` is its OK for that head, `changes`
is what the report must do before it may be ready."""

type WaitKind = Literal["ci", "bot", "limit"]
"""Why a task's agent held no turn (27.10): its PR's checks, the review bot, or its harness's
quota (`limit`)."""

type FeatureStatus = Literal["planned", "active", "done", "parked"]
"""Where a feature is: its own setting, or what its tasks make it (orch.records)."""

type LinkKind = Literal[
    "pull",
    "issue",
    "commit",
    "branch",
    "ci_run",
    "file",
    "url",
    "doc",
    "stitch",
    "task",
    "feature",
    "question",
]
"""What a link points at (26.4's kinds)."""

STATE_MARK: Final[dict[str, str]] = {
    "open": "[ ]",
    "queued": "[ ]",
    "in_progress": "[ ]",
    "blocked": "[!]",
    "ready": "[r]",
    "merged": "[~]",
    "done": "[x]",
    "moved": "[m]",
    "cancelled": "\u2014",
}
"""The mark a state is imported from and printed as (the Concepts table): `[ ]` open, queued or
in progress, `[!]` blocked, `[r]` ready, `[~]` merged, `[x]` done, `[m]` moved, and none for a
cancelled task."""

OPEN: Final = "open"
"""A task nobody has taken: not assigned (the Concepts table's first state)."""

IN_PROGRESS: Final = "in_progress"
"""A task the agent is on."""

QUEUED: Final = "queued"
"""A task in an agent's queue."""

BLOCKED: Final = "blocked"
"""A task only a human or a manager can unblock."""

READY: Final = "ready"
"""A task whose PR is green, waiting to be merged."""

MERGED: Final = "merged"
"""A task merged into its repo's base branch."""

DONE: Final = "done"
"""A task reviewed against its spec (a reviewer's or a human's mark)."""

MOVED: Final = "moved"
"""A task taken from one agent for another."""

CANCELLED: Final = "cancelled"
"""A task that is not going to be done."""

Size = Literal["S", "M", "L", "XL"]
"""How big a task is: small, medium, large or extra large (26.8). A task may be unsized."""

POINTS: Final[dict[Size, int]] = {"S": 1, "M": 3, "L": 5, "XL": 8}
"""What each size is worth, in points: the one place a size becomes points (26.8)."""

NOW: Final = "now"
"""An action that is due now (28.2)."""

AFTER: Final = "after"
"""An action that waits for a task to merge."""

LATER: Final = "later"
"""An action that is due when someone gets to it."""

DROPPED: Final = "dropped"
"""An action that is not going to be done."""


def _an_argv(argv: list[str]) -> list[str]:
    """A gate's argv, as udl runs it: a program and its arguments. Raises ValueError otherwise.

    It is an argv, not a shell line: a shell line is one argument with a space in it, so an
    argument that has one is refused rather than run as one word."""
    if not argv:
        raise ValueError("is an argv: a program and its arguments (it is empty)")
    for part in argv:
        if not part:
            raise ValueError("has an empty argument (an argv's arguments are not empty)")
        if any(char.isspace() for char in part):
            raise ValueError(f"has an argument with a space in it ({part!r}): a gate is an argv")
    return argv


Gate = Annotated[list[GateArg], AfterValidator(_an_argv)]
"""A gate's argv: a program, then its arguments — at least one, none of them empty."""


def slugify(text: str) -> str:
    """`text` as a branch's tail: lower case, and anything but a letter or digit a dash."""
    slug = "".join(char.lower() if char.isalnum() else "-" for char in text.strip())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def branch_pattern_problem(pattern: str) -> str | None:
    """What is wrong with `pattern` as a repo's branch pattern, or None when it is one.

    Only udl's parts may be named ({type}, {key}, {slug}), each once; the pattern may not
    change how they are named (text before the first part, or after the last, would render
    the branches of two repos apart); and what a branch is made of must read as one git ref: no
    whitespace, no empty part, no trailing dot and nothing git reads specially."""
    # (The pattern's parts, once each, and its fixed text: the text is what a branch is, once
    # its parts are filled in, so it is checked as the text. The parts are read before anything
    # is rendered, so a pattern naming a part udl does not fill in is refused, not a KeyError.)
    try:
        parsed = list(string.Formatter().parse(pattern))
    except ValueError:
        return "is not a pattern (its braces do not match)"
    blocks = [field for _, field, _, _ in parsed if field is not None]
    unknown = [block for block in blocks if block not in BRANCH_PARTS]
    if unknown:
        return f"may only name {', '.join(BRANCH_PARTS)} (not {', '.join(unknown)})"
    fixed = render_branch(pattern, type="x", key="1", slug="x")
    if not blocks:
        return "names none of the parts udl fills in"
    if len(blocks) != len(set(blocks)):
        return "names one of its parts twice"
    first = pattern.split("{", 1)[0]
    if first:
        return "has text before its first part (a pattern begins with one, e.g. {type})"
    if pattern.rfind("}") < pattern.rfind("/"):
        return "has text after its last part (a pattern ends with a part, e.g. {slug})"
    if any(char.isspace() for char in fixed) or "\\" in fixed:
        return "is one branch name, with no spaces"
    if fixed.startswith(("/", ".")) or fixed.endswith(("/", ".")):
        return "does not start or end with / or ."
    if "//" in fixed or "@{" in fixed:
        return "has no empty part or @{"
    # (What git refuses, on the branch as it renders: a component that begins with a dot or at,
    # one that ends in `.lock`, and `..` anywhere. `.locker` and `x.lock-1` are both fine.)
    for part in fixed.split("/"):
        if part.startswith((".", "@")):
            return "has no part starting with . or @"
        if part.endswith(".lock"):
            return "has no part ending in .lock"
    if ".." in fixed:
        return "has no part with .. in it"
    if not render_branch(pattern, type="feat", key="1.1", slug="x"):
        return "renders an empty branch name"
    return None


def render_branch(pattern: str, *, type: str, key: str, slug: str) -> str:  # noqa: A002 - (as a pattern names it)
    """The pattern with udl's parts filled in: the task's kind of change, its key and its slug."""
    return pattern.format(type=type, key=key, slug=slugify(slug))


def _checked_branch_pattern(pattern: str) -> str:
    """A repo's branch pattern, as udl can render it. Raises ValueError otherwise."""
    problem = branch_pattern_problem(pattern)
    if problem is not None:
        raise ValueError(f"a repo's branch pattern {problem}")
    return pattern


def _promotion(branches: list[str]) -> list[str]:
    """The branches after a repo's base, in order: no branch twice."""
    if len(branches) != len(set(branches)):
        raise ValueError("names one of its branches twice")
    return branches


CheckedBranchPattern = Annotated[BranchPattern, AfterValidator(_checked_branch_pattern)]
"""A branch pattern, checked: only udl's parts, once each, and one git ref."""

Promotion = Annotated[list[BranchName], AfterValidator(_promotion)]
"""The branches after the repo's base, in order (e.g. ["rc", "main"]); each once."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Project(_Model):
    """A body of work (udl, liively): its repos and its merge policy.

    `promotion` names the branches after the repo's base, in order (e.g. ["rc", "main"]); a
    project whose base is its last branch (udl's `main`) has none, and its reviewer's merge is
    its review. `rules` is the markdown every agent on this project is given: the ground rules."""

    name: Name
    """What the project is called (unique; e.g. udl)."""
    title: Title
    """How it is shown (e.g. udl)."""
    description: str = ""
    """What it is, in a line or two."""
    merge: MergePolicy = MergePolicy.HUMAN
    """Who merges a ready PR (O-D3)."""
    promotion: Promotion = Field(default_factory=list)
    """The branches after the repo's base, in order (e.g. ["rc", "main"]); empty for udl."""
    review_bot: str | None = None
    """The review bot's login, whose verdict a ready PR waits on (e.g. claude[bot])."""
    rules: str = ""
    """The ground rules, as markdown, given to every agent on this project."""


class Repo(_Model):
    """A GitHub repository of a project: where it is, what it is called, its base branch, its
    gate, its main checkout on each host, and how its task branches are named."""

    project: Name
    """The project it belongs to."""
    name: Name
    """What the repo is called within the project (unique with its project)."""
    github: GitHubSlug
    """Its GitHub repository: owner/repo."""
    account: GitHubAccount
    """The github account udl reaches it through (25.4)."""
    base_branch: BranchName = "main"
    """The branch PRs go into."""
    gate: Gate = Field(default_factory=lambda: list(DEFAULT_GATE))
    """The argv that is its gate (e.g. ["make", "check"]), run before a push."""
    checkouts: dict[HostName, str] = Field(default_factory=dict)
    """Host name → the main checkout's path there (empty: udl does not know where it is yet)."""
    branch_pattern: CheckedBranchPattern = DEFAULT_BRANCH_PATTERN
    """How its task branches are named: {type}/{key}-{slug} (23.1)."""

    def branch(self, *, type: str, key: str, slug: str) -> str:  # noqa: A002 - (as its pattern names it)
        """The branch for a task of this repo: its pattern with `type`, `key` and `slug` in it.

        The slug is slugified (lower case, dashes), so a task's title is a branch's tail."""
        return render_branch(self.branch_pattern, type=type, key=key, slug=slug)


class Principal(_Model):
    """Who is acting: a person, an agent, or udl itself.

    A person is `human:<name>`; an agent is one of udl's agents (`ernie-c`), or the reviewer
    (`reviewer`), whose merge is a review."""

    kind: Literal["human", "agent", "system"]
    """What kind of caller it is."""
    name: str = ""
    """Who it is: an agent's name, or `human:<name>`; empty for udl itself."""
    role: Role | None = None
    """What it is doing (the operation it calls: `ready`, `block`, `merge`), or None."""

    @property
    def is_human(self) -> bool:
        """Whether a person is acting (the reviewer's own merge, a human's cancel).

        By its `kind`, not its name: an agent called `human:x` is an agent, and is held to the
        rules the agents are."""
        return self.kind == "human"

    @property
    def is_reviewer(self) -> bool:
        """Whether this is the reviewer agent (only it, or a human, reaches `done`)."""
        return self.kind == "agent" and self.name == "reviewer"

    @property
    def who(self) -> str:
        """How it is written in a note: an agent's name, or `human:<name>`."""
        return self.name or "udl"


class Feature(_Model):
    """A goal with a spec: the top tier (udl's 26.3, or an imported 22)."""

    project: Name
    """The project it belongs to."""
    key: Key
    """Its key within the project (`F-<n>`, or an imported key like `22`)."""
    title: Title
    """What it is, in a line."""
    spec: Spec = ""
    """Its spec, as markdown."""
    status: FeatureStatus = "planned"
    """Where it is: its own setting, or what its tasks make it (orch.records)."""
    order: int = 0
    """Where it sits among its project's features."""


PullStateWord = Literal["open", "closed", "merged"]
"""Where a pull is (GitHub's own word: a merged pull is never closed)."""
PullChecksWord = Literal["pass", "fail", "pending", "none"]
"""How a pull's checks are, in one word."""
PullReviewWord = Literal["approved", "changes", "pending", "none"]
"""How a pull's review is, in one word."""


class PullReader(Protocol):
    """What refreshing a pull needs of a service: udl's own `github_get_pull` operation.

    It is udl's `UDLService.call` (an operation's name and its request, as plain data), so the
    hub's service is a PullReader and so is a test's fake of one."""

    async def call(self, name: str, request: BaseModel | Mapping[str, object]) -> BaseModel:
        """Run the operation called `name` with `request`."""
        ...


class PullState(_Model):
    """What a pull link keeps: where a pull is, its head, its checks and its review.

    `state` is GitHub's own word (open, closed or merged — merged is never closed), `checks` and
    `review` one word each for the pull, so a link says at a glance where its pull is. Each is a
    `Literal`: a word outside them is refused when the link is stored, not kept as a string that
    reads as nothing."""

    state: PullStateWord = "open"
    """Where the pull is: open, closed or merged."""
    head_sha: str = ""
    """The commit at its head."""
    checks: PullChecksWord = "none"
    """How its checks are: pass, fail, pending or none."""
    review: PullReviewWord = "none"
    """How its review is: approved, changes, pending or none."""


class Link(_Model):
    """A typed pointer from a feature or a task to something else (26.4); a question's and an
    action's, with 28.x.

    `url` is the canonical form (a `udl:` URL for an internal kind, so a task's link is
    `udl:task/udl/26.2`), and `state` is what is known about it — for a pull its `state`,
    `head_sha`, `checks` and `review`."""

    owner_kind: OwnerKind
    """What it hangs on."""
    owner: str
    """What it hangs on, by key (the task's key, `Q-7`)."""
    link_kind: LinkKind
    """What it points at (not `kind`: a record's `kind` is the name of its kind)."""
    url: URLText
    """The canonical form of the URL it points at."""
    title: Text = ""
    """What it is called, when it has been seen."""
    state: PullState = Field(default_factory=PullState)
    """What is known about it (for a pull: its state, head sha, checks and review)."""
    added_by: Text = ""
    """Who added it (an agent, `human:<name>`, or `scan`)."""


class Task(_Model):
    """One piece of a feature that one agent (or one human) does, usually one PR."""

    project: Name
    """The project it belongs to."""
    feature: Key
    """The feature it is part of (its key: a task always links back)."""
    key: Key
    """Its key within the project (`<feature key>.<n>`, or the key it was given)."""
    title: Title
    """What it is, in a line."""
    spec: Spec = ""
    """Its spec, as markdown (its card, when it was imported)."""
    task_kind: TaskKind = "build"
    """What kind of work it is: a build, a person's, or a review's. (Not `kind`: a record's
    `kind` is the name of its kind, and a field may not shadow it.)"""
    state: State = OPEN
    """Where it is (the Concepts table); set only by move() (orch.transitions)."""
    needs: list[str] = Field(default_factory=list)
    """The tasks that must be done first, by key."""
    repo: str | None = None
    """The repo it is done in (a repo of its project), or None."""
    assignee: str | None = None
    """An agent's name, or `human:<name>`; None while it is nobody's."""
    position: int | None = None
    """Its place in its assignee's queue."""
    hold: str | None = None
    """Why it is held (built now, merged later), or None."""
    blocked_on: str | None = None
    """The question's key or free text it waits on, while it is blocked."""
    branch: str | None = None
    """The branch it is done on (its repo's branch pattern), once it has one."""
    worktree: str | None = None
    """The worktree it is done in, once the dispatcher has made one."""
    updated_by: str = ""
    """Who last moved it (a role, or an agent's name)."""
    size: Size | None = None
    """How big it is (None: not sized yet)."""

    @computed_field
    @property
    def points(self) -> int | None:
        """What its size is worth (None while it has no size)."""
        return POINTS[self.size] if self.size is not None else None


class Sized(Protocol):
    """What a total needs of a task: how big it is, and where it is (a shape's or a record's)."""

    @property
    def size(self) -> Size | None:
        """How big it is (None: not sized yet)."""
        ...

    @property
    def state(self) -> str:
        """Where it is (the Concepts table)."""
        ...


class Totals(_Model):
    """What a feature's or a project's tasks come to, in points (26.8): the tasks nobody is
    going to do (cancelled) are not counted, and the ones without a size are counted instead."""

    points: NonNegativeInt = 0
    """The points of every sized task."""
    points_merged: NonNegativeInt = 0
    """The points of the tasks that are merged or done."""
    points_ready: NonNegativeInt = 0
    """The points of the tasks whose PRs are green, waiting to be merged."""
    unsized: NonNegativeInt = 0
    """How many of its tasks have no size yet."""

    @classmethod
    def of(cls, tasks: Iterable[Sized]) -> Self:
        """`tasks` counted: what is done, what is ready, and what has no size."""
        points = merged = ready = unsized = 0
        for one in tasks:
            if one.state == CANCELLED:
                continue
            if one.size is None:
                unsized += 1
                continue
            points += POINTS[one.size]
            if one.state in (MERGED, DONE):
                merged += POINTS[one.size]
            elif one.state == READY:
                ready += POINTS[one.size]
        return cls(points=points, points_merged=merged, points_ready=ready, unsized=unsized)


# ---------------------------------------------------------------------------------------------
# Human actions (28.2)
# ---------------------------------------------------------------------------------------------


class Action(_Model):
    """Something only a person can do, with a result line (human-comms.md's "To action").

    `when` is `now`, `after` a task (it becomes `now` when that task merges), or `later`; `task`
    is the task it is about, when it is about one, and the result of finishing it is written
    there as a note."""

    key: Key
    """Its key within the project (`A-<n>`)."""
    project: Name
    """The project it belongs to."""
    title: Title
    """What is to be done, in a line."""
    text: Text = ""
    """What it says, as markdown (what to do, and why)."""
    when: ActionWhen = ActionWhen.NOW
    """When it is due: now, after a task, or later."""
    after: Key | None = None
    """The task it waits for, while `when` is `after` (it becomes `now` when that one merges)."""
    task: Key | None = None
    """The task it is about, when it is about one (the result is written there)."""
    assignee: str | None = None
    """The person it is for (`human:<name>`), or None for whoever picks it up."""
    state: ActionState = ActionState.OPEN
    """Where it is: open, done or dropped."""
    result: str | None = None
    """What came of it: the result of finishing it, or why it was dropped."""


class SetActionRequest(_Model):
    """Adding something only a person can do (28.2)."""

    title: Title
    """What is to be done, in a line."""
    text: Text = ""
    """What it says, as markdown."""
    when: ActionWhen = ActionWhen.NOW
    """When it is due: now, after a task, or later."""
    after: Key | None = None
    """The task it waits for (only while `when` is `after`)."""
    task: Key | None = None
    """The task it is about, when it is about one."""
    assignee: str | None = None
    """The person it is for (`human:<name>`)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class ListActionsRequest(_Model):
    """Asking what is to be done, in the order it is due."""

    state: ActionState | None = None
    """Only the actions in this state (None: the open ones)."""
    when: ActionWhen | None = None
    """Only the actions due at this time (None: now, after and later)."""
    assignee: str | None = None
    """Only the actions of this person (`human:<name>`)."""
    project: Name | None = None
    """The project they are of (None: the only one, while udl has one)."""


class FinishActionRequest(_Model):
    """Finishing something only a person could do, with what came of it."""

    key: Key
    """The action to finish (`A-<n>`)."""
    result: str
    """What came of it (written as a note on the task it is about)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class DropActionRequest(_Model):
    """Dropping something that is not going to be done, with the reason."""

    key: Key
    """The action to drop (`A-<n>`)."""
    reason: str = ""
    """Why it is dropped."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class Actions(_Model):
    """What is to be done, in the order it is due (28.2)."""

    project: Name
    """The project they are of."""
    actions: list[Action] = Field(default_factory=list[Action])
    """The actions, now first, then after, then later (empty: nothing to do)."""


# ---------------------------------------------------------------------------------------------
# Questions (28.1)
# ---------------------------------------------------------------------------------------------


class QuestionState(StrEnum):
    """Where a question is: waiting for an answer, answered, or closed without one."""

    OPEN = "open"
    ANSWERED = "answered"
    CLOSED = "closed"


class Question(_Model):
    """Something an agent cannot answer itself (28.1): what it asked, of whom, and what came of
    it.

    `to` is an agent's name (its manager, when that manager may answer) or `humans`; `blocking`
    says whether the task it was asked in waits for the answer. Its thread is its `Message`s."""

    key: Key
    """Its key within the project (`Q-<n>`)."""
    project: Name
    """The project it belongs to."""
    asked_by: str = ""
    """Who asked it: an agent's name, or `human:<name>`."""
    to: str = "humans"
    """Who it is asked of: an agent's name (its manager), or `humans`."""
    title: Title
    """What it is, in a line."""
    text: Text = ""
    """What it says, as markdown."""
    options: list[str] = Field(default_factory=list[str])
    """The answers it offers, when it offers any."""
    blocking: bool = False
    """Whether the task it was asked in waits for the answer."""
    task: Key | None = None
    """The task it was asked in (a blocking one moves it to `blocked`)."""
    state: QuestionState = QuestionState.OPEN
    """Where it is: open, answered or closed."""
    answer: str | None = None
    """The answer, once there is one."""
    answered_by: str | None = None
    """Who answered it: an agent's name, or `human:<name>`."""
    outcome: str | None = None
    """What was done about it, when it is closed without an answer."""
    escalated: bool = False
    """Whether it was passed up from the agent it was asked of."""


class Message(_Model):
    """One thing said on a question (28.1): the question's own text first, then follow-ups."""

    text: str
    """What it says, as markdown."""
    author: str = ""
    """Who said it: an agent's name, `human:<name>`, or `udl`."""
    at: datetime
    """When it was said."""


class Questions(_Model):
    """Questions, oldest first (28.1)."""

    project: Name
    """The project they are of."""
    questions: list[Question] = Field(default_factory=list[Question])
    """The questions (empty: none match)."""


class AQuestion(_Model):
    """One question with everything about it: its thread, and what it names (28.1)."""

    question: Question
    """The question itself."""
    thread: list[Message] = Field(default_factory=list[Message])
    """What was said on it, oldest first (its own text first)."""
    tasks: list[Key] = Field(default_factory=list[Key])
    """The tasks it names (each one the answer is written on)."""
    features: list[Key] = Field(default_factory=list[Key])
    """The features it names (each one the answer is written on)."""


class AskRequest(_Model):
    """Asking something only the manager or a person can answer (28.1)."""

    task: Key
    """The task it is asked in."""
    title: Title
    """What it is, in a line."""
    text: Text = ""
    """What it says, as markdown (what it needs to know, and why it cannot go on)."""
    options: list[str] = Field(default_factory=list[str])
    """The answers it offers, when it offers any."""
    blocking: bool = False
    """Whether the task waits for the answer."""
    tasks: list[Key] = Field(default_factory=list[Key])
    """The other tasks it is about (the answer is written on each)."""
    features: list[Key] = Field(default_factory=list[Key])
    """The features it is about (the answer is written on each)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class BlockRequest(_Model):
    """Saying that only a person can do this (28.1): `orch_ask`, blocking."""

    task: Key
    """The task that cannot go on."""
    reason: str
    """What is needed, and why the agent cannot do it itself."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class AnswerRequest(_Model):
    """Answering a question (28.1): the answer is written on every task and feature it names."""

    key: Key
    """The question to answer (`Q-<n>`)."""
    answer: str
    """The answer."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class ReplyRequest(_Model):
    """A follow-up on a question, from either side (28.1)."""

    key: Key
    """The question to reply on (`Q-<n>`)."""
    text: str
    """What it says, as markdown."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class EscalateRequest(_Model):
    """Passing a question up, to the asker's manager's manager or to the humans (28.1)."""

    key: Key
    """The question to pass up (`Q-<n>`)."""
    why: str = ""
    """Why it is passed up (written into its thread)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class CloseQuestionRequest(_Model):
    """Closing a question nobody answered, with what was done about it (28.1)."""

    key: Key
    """The question to close (`Q-<n>`)."""
    outcome: str
    """What was done about it instead."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class ListQuestionsRequest(_Model):
    """Asking which questions are waiting, and of whom (28.1)."""

    state: QuestionState | None = QuestionState.OPEN
    """Only the questions in this state (None: every state)."""
    to: str | None = None
    """Only the questions asked of this: an agent's name, or `humans`."""
    task: Key | None = None
    """Only the questions asked in this task."""
    project: Name | None = None
    """The project they are of (None: the only one, while udl has one)."""


class GetQuestionRequest(_Model):
    """Reading one question with its thread (28.1)."""

    key: Key
    """The question to read (`Q-<n>`)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


# ---------------------------------------------------------------------------------------------
# The queue's operations (27.3)
# ---------------------------------------------------------------------------------------------


class QueueEntry(_Model):
    """A task on a queue, as the operations answer with it: what a board shows, and nothing that
    is not needed to show it."""

    key: Key
    """Its key within the project (`27.3`)."""
    feature: Key
    """The feature it is part of (its key)."""
    title: Title
    """What it is, in a line."""
    state: State
    """Where it is (a queued task is on a queue; the one an agent is on is in progress)."""
    position: int | None = None
    """Its place in its queue (0 is the next one)."""
    assignee: str | None = None
    """An agent's name, or `human:<name>`; None while it is nobody's."""
    repo: str | None = None
    """The repo it is done in, or None."""


class PushRequest(_Model):
    """Push a task's work (27.5): its branch to origin, then its pull request."""

    key: Key
    """The task whose work it is."""
    title: Title | None = None
    """The pull's title (None: the task's own)."""
    body: Text | None = None
    """What the pull says, as markdown (the task's key is added to it)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class GateRun(_Model):
    """The gate a push ran: how it ended, and the end of what it printed."""

    exit_code: int
    """Its exit code (0: it passed, which is the only way a push goes on)."""
    output: str
    """The last of its output (the end, which is where a failure says why)."""
    seconds: float
    """How long it took, in seconds."""


class PushResult(_Model):
    """What a push did: the branch it pushed, the gate it passed, and the pull it opened (or
    updated)."""

    project: Name
    """The project the task is of."""
    key: Key
    """The task whose work it is."""
    branch: BranchName
    """The branch it pushed."""
    gate: GateRun
    """The gate it passed first."""
    pull: str
    """The pull request's URL."""


class TaskQueue(_Model):
    """One agent's queue: the tasks it is on, in the order it works them."""

    project: Name
    """The project the tasks are of."""
    agent: str
    """Whose queue it is: an agent's name, or `human:<name>`."""
    tasks: list[QueueEntry] = Field(default_factory=list[QueueEntry])
    """Its tasks, in the order they will be worked (empty: it has nothing to do)."""


class SuggestRequest(_Model):
    """What is suggested next: a project's tasks, for one agent or for whoever is idle (27.4)."""

    project: Name | None = None
    """The project (None: the only one, as every task operation allows)."""
    agent: Name | None = None
    """Whose tasks to suggest (None: each task is paired with the idle agent it suits best)."""
    limit: PositiveInt = 10
    """How many to suggest at most."""


class Suggestion(_Model):
    """A task suggested next, with the agent it suits and why (27.4)."""

    task: Key
    """The task's key."""
    title: Title
    """What it is, in a line."""
    feature: Key
    """The feature it is part of (its key)."""
    agent: Name
    """The agent it suits best."""
    score: int
    """How well it suits it: higher comes first."""
    reasons: list[str] = Field(default_factory=list[str])
    """Why it was suggested, in the order they were counted."""
    stack_on: Key | None = None
    """The task whose branch it stacks on (its need is `ready`, not merged yet)."""


class Suggestions(_Model):
    """What is suggested next, best first (27.4)."""

    project: Name
    """The project they are of."""
    suggestions: list[Suggestion] = Field(default_factory=list[Suggestion])
    """The tasks suggested, best first (empty: nothing is ready to start)."""


class MyTaskNote(_Model):
    """A note on the task an agent is on, as `orch_my_task` answers with it (26.5)."""

    note_kind: NoteKind
    """What kind of note it is; `system` is what udl itself writes."""
    text: str
    """What it says, as markdown."""
    author: str
    """Who wrote it: an agent's name, `human:<name>`, or `udl`."""
    at: AwareDatetime
    """When it was written."""


class MyTaskResponse(_Model):
    """What an agent is on: the task, what it is for, and what it is told before it starts.

    Everything an agent's session is given at its start (27.7): the task and its place, its
    feature and the feature's spec, the task's own spec, its notes, and the project's rules.
    Its links are 26.4's, and are added when they are built."""

    task: QueueEntry | None = None
    """The task it is in progress on, or the first on its queue; None when there is none."""
    feature_spec: Spec = ""
    """The spec of the feature the task is part of (empty when there is no task)."""
    spec: Spec = ""
    """The task's own spec."""
    rules: Spec = ""
    """The project's rules: the ground rules every agent on it is given."""
    notes: list[MyTaskNote] = Field(default_factory=list[MyTaskNote])
    """The task's notes, oldest first."""


class AssignTaskRequest(_Model):
    """Assigning a task to an agent, at the end of its queue or at a position in it."""

    key: Key
    """The task to assign (`27.3`)."""
    agent: str
    """Who takes it: an agent's name, or `human:<name>` for a person's task."""
    position: int | None = None
    """Where in the queue it goes (None: its end)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class ReorderQueueRequest(_Model):
    """Setting an agent's queue to exactly these keys, in this order."""

    agent: str
    """Whose queue it is: an agent's name, or `human:<name>`."""
    keys: list[Key]
    """Its tasks, in the order they will be worked: every one on the queue, each once."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class UnassignTaskRequest(_Model):
    """Taking a task out of its agent's queue, back to open."""

    key: Key
    """The task to take out (`27.3`)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class QueueRequest(_Model):
    """Asking what an agent's queue is."""

    agent: str
    """Whose queue it is: an agent's name, or `human:<name>`."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class MyTaskRequest(QueueRequest):
    """Asking what an agent is to work next."""


# ---------------------------------------------------------------------------------------------
# Readiness (27.6)
# ---------------------------------------------------------------------------------------------


class ReviewStatus(StrEnum):
    """Where one of the review bot's items is: open, fixed, partly fixed, not fixed, or a new
    issue it found this round."""

    OPEN = "open"
    FIXED = "fixed"
    PARTIALLY_FIXED = "partially_fixed"
    NOT_FIXED = "not_fixed"
    NEW_ISSUE = "new_issue"


class ReviewItem(_Model):
    """One item of the review bot's comment: what it says, and where it is."""

    number: str = ""
    """Its number in the comment (`1`, or empty for a row without one)."""
    text: str
    """What the item is, as the bot wrote it (its status word left out)."""
    status: ReviewStatus = ReviewStatus.OPEN
    """Where it is; anything but `fixed` keeps the pull from being ready."""


class Assessment(StrEnum):
    """What the review bot thinks of the pull as a whole."""

    APPROVE = "approve"
    """It says the pull is clear."""
    REQUEST_CHANGES = "request_changes"
    """It asks for changes."""
    UNKNOWN = "unknown"
    """It wrote no verdict, or one udl does not know (which never overrides an open item)."""


class ReviewVerdict(_Model):
    """A review bot's comment, read: its items, its assessment, and the commit it reviewed."""

    items: list[ReviewItem] = Field(default_factory=list[ReviewItem])
    """Its items, in the order it wrote them."""
    assessment: Assessment = Assessment.UNKNOWN
    """What it thinks of the pull as a whole."""
    reviewed_sha: str | None = None
    """The commit it says it reviewed (a follow-up names one; a first review does not)."""


PendingKind = Literal["checks", "mergeable", "bot"]
"""What a readiness is only waiting on: GitHub's checks, GitHub working out whether the pull
merges, or the review bot (27.10)."""


class Readiness(_Model):
    """Whether a pull may be merged for a task (27.6): ready or not, why not, and what the
    review bot still lists."""

    ready: bool
    """Whether it may be merged now."""
    held: bool = False
    """Whether it is held (`task.hold`) and otherwise ready: built now, merged later."""
    blockers: list[str] = Field(default_factory=list[str])
    """Why it may not be merged, in the order a person would look at them (empty: it may)."""
    open_items: list[ReviewItem] = Field(default_factory=list[ReviewItem])
    """The review bot's items it still lists as open (empty: nothing)."""
    waited: float = 0.0
    """How long the answer waited for GitHub's checks and the review bot, in seconds (0: it did
    not: `orch_pr_status` without `wait`, 27.5)."""
    pending: list[str] = Field(default_factory=list[str])
    """The blockers that are only a matter of time (the checks still running, the review bot not
    done with this head, GitHub not knowing yet): what a wait can change, and the rest of
    `blockers` cannot (27.5)."""
    pending_kinds: list[PendingKind] = Field(default_factory=list[PendingKind])
    """What those are, for a caller that acts on them rather than reads them (27.10's `Wait`:
    `checks` and `mergeable` are GitHub's, `bot` is the review bot's)."""


class ReviewRequest(_Model):
    """A manager's verdict on a report's pull (27.8)."""

    key: Key
    """The task whose pull it is."""
    verdict: ManagerVerdict
    """`approve`, or `changes`."""
    reasons: str = ""
    """Why, as markdown: what it checked, or the changes it wants."""
    head: str | None = None
    """The commit it read (None: the pull's head as udl last read it)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class Reviewed(_Model):
    """A review, as `orch_review_task` answers with it."""

    project: Name
    """The project it is in."""
    task: Key
    """The task whose pull it is about."""
    head: str
    """The commit it is about."""
    verdict: ManagerVerdict
    """What the manager says."""
    reasons: str = ""
    """Why."""
    by: str = ""
    """The manager's name."""
    at: AwareDatetime
    """When it said so."""


class ReadinessRequest(_Model):
    """Asking whether a task's pull may be merged (27.6)."""

    key: Key
    """The task whose pull is asked about (`27.6`)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class PrStatusRequest(_Model):
    """Asking whether a task's pull is ready, and holding until it is (27.5)."""

    key: Key
    """The task whose pull is asked about."""
    wait: bool = False
    """Whether to hold until GitHub's checks and the review bot have finished with this head
    (an answer that is not ready for another reason comes at once: waiting would not change
    it)."""
    minutes: PositiveInt | None = None
    """How long the wait may take, in minutes (None: orch's `wait_minutes`)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class ReadyRequest(_Model):
    """Marking a task's pull ready: the task, and the note to write with it (27.6)."""

    key: Key
    """The task whose pull is ready (`27.6`)."""
    note: Spec = ""
    """What to write as the task's `progress` note (empty: a line saying it is ready)."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class MergeRow(_Model):
    """One task in a merge list (28.3): its pull, where it is, and what it waits on."""

    project: Name
    """The project it is in."""
    task: Key
    """The task's key."""
    feature: Key
    """The feature it belongs to."""
    title: Title
    """What it is, in a line."""
    repo: str
    """Its repo, in full (`owner/name`)."""
    number: int
    """Its pull's number."""
    url: str
    """Its pull's page."""
    sha: str
    """The commit at its pull's head."""
    state: State
    """Where the task is."""
    needs: list[Key] = Field(default_factory=list[Key])
    """The tasks that must be merged first, by key."""
    blockers: list[str] = Field(default_factory=list[str])
    """Why it may not be merged yet (empty: it may)."""
    hold: bool = False
    """Whether it is held: built now, merged later."""
    since: AwareDatetime | None = None
    """When it reached where it is: ready, or merged."""
    followed: list[Key] = Field(default_factory=list[Key])
    """The tasks that needed it (what followed a merge: they rebase and push again)."""


class AgentRole(StrEnum):
    """What an agent does: implements tasks, manages agents, or reviews their work."""

    IMPLEMENTER = "implementer"
    MANAGER = "manager"
    REVIEWER = "reviewer"


class AgentStatus(StrEnum):
    """What an agent is doing now (derived from its session: never stored)."""

    IDLE = "idle"  # nothing running
    WORKING = "working"  # its session is starting, or running
    WAITING = "waiting"  # it answered, or its session failed and a person is being asked
    LIMITED = "limited"  # its harness's quota is spent, until its session says when
    OFFLINE = "offline"  # its host did not answer (27.7)
    PAUSED = "paused"  # a person paused it: nothing is dispatched to it


class MergeList(_Model):
    """What there is to merge (28.3), as a person reads it: the ready ones in merge order (grouped
    by repo), the ones still coming (with what they wait on), the held ones, and what merged
    lately."""

    ready: list[MergeRow] = Field(default_factory=list[MergeRow])
    """Ready to merge, grouped by repo, each after what it needs."""
    coming: list[MergeRow] = Field(default_factory=list[MergeRow])
    """With an open pull that is not ready yet, each with its blockers."""
    held: list[MergeRow] = Field(default_factory=list[MergeRow])
    """Held (built now, merged later), each with its hold as its blocker."""
    done: list[MergeRow] = Field(default_factory=list[MergeRow])
    """Merged in the last 14 days, with what followed each."""


class AgentLine(_Model):
    """What an agent is doing, in a line (28.4's inbox strip): who it is, what it is, and what it
    is on — derived from its session, never stored."""

    name: Name
    """Its name."""
    title: Title
    """How it is shown."""
    role: AgentRole
    """What it does: implements, manages, or reviews."""
    state: AgentStatus
    """What it is doing now, from its session."""
    task: Key | None = None
    """The task it is on, when it is on one (None: it has none, or its manager has no task)."""
    since: AwareDatetime | None = None
    """When its session last changed (when it started, or answered): None when it has none."""
    line: str = ""
    """It, in a line: `deepseek-a — implementing 27.10`, or `claude — idle` (what the inbox
    strip shows; the app writes it, so the API and MCP answer it too)."""


def agent_line(name: str, role: AgentRole, state: AgentStatus, task: str | None) -> str:
    """An agent in a line (28.4's inbox strip): `deepseek-a — implementing 27.10`, or
    `claude — idle`."""
    what = {
        AgentRole.IMPLEMENTER: "implementing",
        AgentRole.REVIEWER: "reviewing",
        AgentRole.MANAGER: "managing",
    }
    if task is not None and state is AgentStatus.WORKING:
        return f"{name} — {what[role]} {task}"
    return f"{name} — {state.value}"


class InboxRequest(_Model):
    """Asking for the inbox: everything that waits on a person (28.4)."""

    project: Name | None = None
    """The project it is of (None: the only one, while udl has one)."""


class Inbox(_Model):
    """The three lists a person reads today in human-comms.md, in one answer: what is ready to
    merge, what is being asked, and what only a person can do — with what every agent is doing
    beside them (28.4). It reads: nothing changes."""

    project: Name
    """The project it is of."""
    merge: MergeList
    """Ready to merge (28.3's own list: ready, coming, held, done)."""
    questions: list[Question] = Field(default_factory=list[Question])
    """The open questions, the blocking ones first, each newest first."""
    actions: list[Action] = Field(default_factory=list[Action])
    """What only a person can do: now, then after, then later."""
    agents: list[AgentLine] = Field(default_factory=list[AgentLine])
    """What every agent is doing, by name."""
    updated_at: AwareDatetime
    """When this was read (the dispatcher and the watcher's events change it, 28.5)."""


class MergeListRequest(_Model):
    """Asking what there is to merge (28.3)."""

    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class MergeRequest(_Model):
    """Merging a task's pull (28.3): the task, and what it must be confirmed as."""

    key: Key
    """The task whose pull is merged."""
    confirm: str
    """`owner/repo#number`, as `github_merge_pull` asks: the merge says which pull it is."""
    sha: str
    """The head the caller saw (`orch_merge_list`'s row): the pull is merged only at that head,
    and one that moved since is refused rather than merged blind."""
    project: Name | None = None
    """The project it is in (None: the only one, while udl has one)."""


class Merged(_Model):
    """A pull that was merged (28.3): the task, its pull and the commit merged."""

    task: Key
    """The task's key."""
    url: str
    """Its pull's page."""
    sha: str
    """The commit that was merged."""
    state: State
    """Where the task is now: merged, or done at once when nothing follows the base."""


class PromotionList(_Model):
    """What is merged but not done (28.3): the tasks a promotion would mark done, per repo."""

    project: Name
    """The project."""
    promotion: list[str] = Field(default_factory=list[str])
    """Its branches after the base, in order (empty: nothing to promote)."""
    rows: list[MergeRow] = Field(default_factory=list[MergeRow])
    """The tasks that are merged and not done, grouped by repo."""


class PromotionRequest(_Model):
    """Asking what a promotion would mark done (28.3)."""

    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class PromoteRequest(_Model):
    """Marking tasks done after a promotion's pull merged (28.3)."""

    tasks: list[Key]
    """The tasks the promotion carried."""
    note: Spec = ""
    """What to write as each task's note (empty: a line saying it was promoted)."""
    project: Name | None = None
    """The project they are in (None: the only one, while udl has one)."""


class Promoted(_Model):
    """The tasks a promotion marked done (28.3)."""

    tasks: list[Key] = Field(default_factory=list[Key])
    """The tasks that are done now."""


# ---------------------------------------------------------------------------------------------
# The catalogue's operations (26.6)
# ---------------------------------------------------------------------------------------------


def as_shape[S: BaseModel](shape: type[S], record: BaseModel) -> S:
    """`record` (an orch.records one) as `shape`: the fields the shape has, from the record's.

    The records carry the id and the times; a shape is what an operation answers, and it holds
    only what it says."""
    return shape.model_validate({name: getattr(record, name) for name in shape.model_fields})


class NoteInfo(_Model):
    """A note as an operation answers it (the record is orch.records.Note)."""

    note_kind: NoteKind
    """What kind of note it is."""
    text: Spec
    """What it says, as markdown."""
    author: Text = "udl"
    """Who wrote it: an agent, `human:<name>`, or `udl`."""
    at: AwareDatetime
    """When it was written."""


class ListProjectsRequest(_Model):
    """Asking for every project (there is nothing to say: the answer is all of them)."""


class Projects(_Model):
    """Every project udl knows."""

    projects: list[Project] = Field(default_factory=list[Project])
    """The projects, by name."""


class GetProjectRequest(_Model):
    """Asking for one project and its repos."""

    name: Name
    """The project's name."""


class ProjectInfo(_Model):
    """A project, with its repos."""

    project: Project
    """The project itself."""
    repos: list[Repo] = Field(default_factory=list[Repo])
    """Its repos, by name."""


class SetProjectRequest(_Model):
    """Creating or editing a project, by its name: what is not given is kept.

    A new project needs a title; the rest it has by default."""

    name: Name
    """The project's name (what it is found, and set, by)."""
    title: Title | None = None
    """How it is shown (needed when it is a new project)."""
    description: str | None = None
    """What it is, in a line or two."""
    merge: MergePolicy | None = None
    """Who merges a ready PR (O-D3)."""
    promotion: Promotion | None = None
    """The branches after the repo's base, in order (e.g. ["rc", "main"])."""
    review_bot: str | None = None
    """The review bot's login, whose verdict a ready PR waits on (e.g. claude[bot])."""
    rules: str | None = None
    """The ground rules, as markdown, given to every agent on this project."""


class DeleteProjectRequest(_Model):
    """Deleting a project, its repos, its features and its tasks."""

    name: Name
    """The project's name."""


class SetRepoRequest(_Model):
    """Creating or editing a repo of a project, by its name: what is not given is kept.

    A new repo needs a GitHub repository and the account udl reaches it through."""

    project: Name | None = None
    """Its project's name (None: the only one, while udl has one)."""
    name: Name
    """What the repo is called within the project."""
    github: GitHubSlug | None = None
    """Its GitHub repository: owner/repo (needed when it is a new repo)."""
    account: GitHubAccount | None = None
    """The github account udl reaches it through (needed when it is a new repo)."""
    base_branch: BranchName | None = None
    """The branch PRs go into."""
    gate: Gate | None = None
    """The argv that is its gate (e.g. ["make", "check"]), run before a push."""
    checkouts: dict[HostName, str] | None = None
    """Host name → the main checkout's path there."""
    branch_pattern: CheckedBranchPattern | None = None
    """How its task branches are named: {type}/{key}-{slug} (23.1)."""


class DeleteRepoRequest(_Model):
    """Deleting a repo of a project."""

    project: Name | None = None
    """Its project's name (None: the only one, while udl has one)."""
    name: Name
    """The repo's name."""


class ListFeaturesRequest(_Model):
    """Asking for a project's features (all of them, when none is named)."""

    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class Features(_Model):
    """A project's features, in their order."""

    project: str
    """The project they are of."""
    features: list[Feature] = Field(default_factory=list[Feature])
    """The features themselves."""
    totals: Totals = Field(default_factory=Totals)
    """The project's points: what its features' tasks come to (26.8)."""


class GetFeatureRequest(_Model):
    """Asking for one feature: its tasks, its links and its notes."""

    key: Key
    """The feature's key."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class FeatureInfo(_Model):
    """A feature, with what hangs on it."""

    feature: Feature
    """The feature itself."""
    tasks: list[Task] = Field(default_factory=list[Task])
    """Its tasks, by key."""
    links: list[Link] = Field(default_factory=list[Link])
    """What points at it."""
    notes: list[NoteInfo] = Field(default_factory=list[NoteInfo])
    """Its notes, oldest first."""
    totals: Totals = Field(default_factory=Totals)
    """The feature's points: what its tasks come to (26.8)."""


class SetFeatureRequest(_Model):
    """Creating or editing a feature, by its key: what is not given is kept.

    A new feature needs a title."""

    project: Name | None = None
    """The project (None: the only one, while udl has one)."""
    key: Key
    """Its key within the project (`F-<n>`, or an imported key like `22`)."""
    title: Title | None = None
    """What it is, in a line (needed when it is a new feature)."""
    spec: Spec | None = None
    """Its spec, as markdown."""
    status: FeatureStatus | None = None
    """Where it is: its own setting, or what its tasks make it."""
    order: int | None = None
    """Where it sits among its project's features."""


class ListTasksRequest(_Model):
    """Asking for a project's tasks, with what to narrow them by."""

    project: Name | None = None
    """The project (None: the only one, while udl has one)."""
    feature: Key | None = None
    """Only the tasks of this feature."""
    state: State | None = None
    """Only the tasks in this state."""
    assignee: str | None = None
    """Only the tasks of this agent (or `human:<name>`)."""
    text: str | None = None
    """Only the tasks whose title or spec holds this text."""


class Tasks(_Model):
    """A project's tasks, by key."""

    project: str
    """The project they are of."""
    tasks: list[Task] = Field(default_factory=list[Task])
    """The tasks themselves."""


class RateInfo(_Model):
    """What a point costs in hours, and which rate said so: the model's own, its project's, or
    an hour a point (nothing has been learned yet)."""

    hours: float
    """The hours a point."""
    source: Literal["model", "project", "default"]
    """Which rate answered."""


class ModelRow(_Model):
    """One model's work, as the progress page shows it."""

    model: str
    """The model (an agent's, as its runner was given it)."""
    runner: str
    """The runner that runs it (an agent's own, the first of them)."""
    tasks: int
    """How many merged, sized tasks it is learned from."""
    hours_per_point: RateInfo
    """What a point costs it."""
    hours_per_size: dict[str, float] = Field(default_factory=dict[str, float])
    """What each size came to, in hours (the median of the tasks of that size)."""


class SeriesPoint(_Model):
    """One day of the burn-up: the scope's points at its end, and the points merged in it."""

    day: date
    """The day."""
    points: int
    """The scope's points (the tasks made by then and not cancelled by then, at their size now)."""
    merged: int
    """The points merged in it."""


class ForecastInfo(_Model):
    """When the work is done: the last agent's own day, and the spread's range around it."""

    likely: AwareDatetime
    """When the last agent finishes, at its own rate."""
    early: AwareDatetime
    """The earliest the work could be done (the spread's 25th percentile)."""
    late: AwareDatetime
    """The latest (its 75th)."""
    unsized: NonNegativeInt = 0
    """The tasks left out of it because nobody sized them."""


class FeatureProgress(_Model):
    """One feature's points, and when it is forecast to be done."""

    key: Key
    """The feature's key."""
    title: Title
    """What it is called."""
    points: int
    """Its points, merged and not."""
    merged: int
    """The points merged."""
    forecast: ForecastInfo | None = None
    """When what is left of it is done (None: nothing sized is left)."""


class RecentTask(_Model):
    """A merged, sized task: what it was estimated at, and what it took."""

    key: Key
    """Its key."""
    title: Title
    """What it is called."""
    size: Size
    """How big it was."""
    estimate: timedelta
    """What its size was expected to take, at the rate it is learned at."""
    actual: timedelta
    """How long it really took (its working time)."""


class Progress(_Model):
    """A project's progress (27.10): its burn-up, its pace, its forecast, its features, its
    models and the tasks merged lately."""

    project: Name
    """The project it is of."""
    series: list[SeriesPoint] = Field(default_factory=list[SeriesPoint])
    """The burn-up, a day at a time."""
    pace: float = 0.0
    """The points merged a day over the last seven."""
    forecast: ForecastInfo
    """When the open, sized work is done."""
    features: list[FeatureProgress] = Field(default_factory=list[FeatureProgress])
    """Each feature's points and its own forecast."""
    models: list[ModelRow] = Field(default_factory=list[ModelRow])
    """What a point costs each model."""
    recent: list[RecentTask] = Field(default_factory=list[RecentTask])
    """The last 20 merged, sized tasks."""
    estimate_ratio: float = 0.0
    """The median of actual ÷ estimated over them (how far the estimates are off)."""


class ProgressRequest(_Model):
    """Asking for a project's progress."""

    project: Name
    """The project."""
    since: AwareDatetime | None = None
    """The day the series starts (None: thirty days before now)."""


class Estimate(_Model):
    """One task's estimate (27.10): its size, what that is expected to take, and the time worked
    on it so far."""

    task: Task
    """The task."""
    size: Size | None = None
    """How big it is (None: not sized, so not estimated)."""
    estimate: timedelta | None = None
    """What its size is expected to take, at its model's rate (None: unsized)."""
    worked: timedelta
    """The time worked on it so far (its history, less its waits)."""


class EstimateRequest(_Model):
    """Asking for one task's estimate."""

    project: Name
    """The task's project."""
    task: Key
    """The task's key."""


class GetTaskRequest(_Model):
    """Asking for one task: its feature, its links, its notes and its questions."""

    key: Key
    """The task's key."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class TaskInfo(_Model):
    """A task, with what hangs on it."""

    task: Task
    """The task itself."""
    feature: Feature | None = None
    """The feature it is part of."""
    links: list[Link] = Field(default_factory=list[Link])
    """What points at it."""
    notes: list[NoteInfo] = Field(default_factory=list[NoteInfo])
    """Its notes, oldest first."""
    questions: list[JsonObject] = Field(default_factory=list[JsonObject])
    """Its open questions (28.1's record: none yet, so this is empty)."""


class SetTaskRequest(_Model):
    """Creating or editing a task, by its key: what is not given is kept.

    A new task needs its feature and a title; without a key it is the feature's next one."""

    project: Name | None = None
    """The project (None: the only one, while udl has one)."""
    key: Key | None = None
    """Its key within the project (`<feature key>.<n>`), or none to take the next one."""
    feature: Key | None = None
    """The feature it is part of (needed when it is a new task)."""
    title: Title | None = None
    """What it is, in a line (needed when it is a new task)."""
    spec: Spec | None = None
    """Its spec, as markdown."""
    needs: list[Key] | None = None
    """The tasks that must be done first, by key."""
    repo: str | None = None
    """The repo it is done in (a repo of its project)."""
    task_kind: TaskKind | None = None
    """What kind of work it is: a build, a person's, or a review's."""
    hold: str | None = None
    """Why it is held (built now, merged later)."""
    size: Size | None = None
    """How big it is: S, M, L or XL (an agent sets it only with the `plan` power, 26.8)."""


class MoveTaskRequest(_Model):
    """Moving a task to another state (the table is orch.transitions')."""

    key: Key
    """The task's key."""
    to: State
    """The state it is moving to."""
    note: Text = ""
    """What it waits on, or why it was cancelled or moved."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class Split(_Model):
    """One of the tasks a split makes."""

    title: Title
    """What it is, in a line."""
    spec: Spec = ""
    """Its spec, as markdown."""


class SplitTaskRequest(_Model):
    """Splitting a task into several (the first keeps its key and its history)."""

    key: Key
    """The task's key."""
    into: list[Split] = Field(min_length=2)
    """The tasks it becomes, in order (two at least: one piece is a retitle, not a split)."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class AddLinkRequest(_Model):
    """Linking something to a feature or a task (a question and an action come with 28.x)."""

    owner_kind: OwnerKind
    """What it hangs on."""
    owner: Key
    """What it hangs on, by key."""
    link_kind: LinkKind
    """What it points at."""
    url: URLText
    """The URL (canonicalised as it is stored)."""
    title: Text = ""
    """What it is called, when it is known."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class RemoveLinkRequest(_Model):
    """Unlinking something from what it hangs on."""

    owner_kind: OwnerKind
    """What it hangs on."""
    owner: Key
    """What it hangs on, by key."""
    url: URLText
    """The URL, as it was stored (canonicalised again)."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class FindLinksRequest(_Model):
    """Asking what hangs on a URL (the other way round: the tasks a PR belongs to)."""

    url: str
    """The URL, however it was written."""


class Links(_Model):
    """What hangs on a URL."""

    url: str
    """The canonical URL it was asked for."""
    links: list[Link] = Field(default_factory=list[Link])
    """What points at it, by owner."""


class UnplacedLine(_Model):
    """A line of a plan file the importer could not place (26.7)."""

    source: Text
    """The file it is in."""
    line: PositiveInt
    """Its line number, from 1."""
    text: Text
    """The line itself."""
    why: Text
    """What the importer could not read of it."""


class ImportPlanRequest(_Model):
    """Reading plan files into udl (26.7)."""

    files: list[Text] = Field(min_length=1)
    """The plan files, by path."""
    dry_run: bool = False
    """Whether to report what it would do, and write nothing."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""


class ImportReport(_Model):
    """What an import did, or would do."""

    created: list[Key] = Field(default_factory=list)
    """The features and tasks it made, by key."""
    updated: list[Key] = Field(default_factory=list)
    """The ones it changed."""
    unchanged: list[Key] = Field(default_factory=list)
    """The ones the plan says what udl already has."""
    kept: list[Text] = Field(default_factory=list)
    """The tasks whose state udl has moved on from what the plan says, kept as udl has them."""
    unplaced: list[UnplacedLine] = Field(default_factory=list[UnplacedLine])
    """Every line it could not place, with its line number."""


class AddNoteRequest(_Model):
    """Writing a note on a feature or a task (appended: a correction is another note)."""

    owner_kind: OwnerKind
    """What it hangs on."""
    owner: Key
    """What it hangs on: a task's or a feature's key."""
    text: Spec
    """The note, as markdown."""
    note_kind: NoteKind = NoteKind.PROGRESS
    """What kind of note it is (not `system`: that is udl's own)."""
    project: Name | None = None
    """The project (None: the only one, while udl has one)."""
