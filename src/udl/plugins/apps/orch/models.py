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
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Final, Literal, Protocol, get_args

# Package Imports
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
)

# Local Imports
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
    "QUEUED",
    "READY",
    "REF_MARKS",
    "ROLE",
    "STATE",
    "STATE_MARK",
    "Action",
    "ActionState",
    "ActionWhen",
    "Actions",
    "AssignTaskRequest",
    "BranchName",
    "BranchPattern",
    "DropActionRequest",
    "Feature",
    "FeatureStatus",
    "FinishActionRequest",
    "Gate",
    "GateArg",
    "GateRun",
    "GitHubAccount",
    "GitHubSlug",
    "HostName",
    "Key",
    "Link",
    "LinkKind",
    "ListActionsRequest",
    "MergePolicy",
    "MyTaskNote",
    "MyTaskRequest",
    "MyTaskResponse",
    "NoteKind",
    "OwnerKind",
    "PrStatusRequest",
    "Principal",
    "Project",
    "Promotion",
    "PullState",
    "PushRequest",
    "PushResult",
    "QueueEntry",
    "QueueRequest",
    "ReorderQueueRequest",
    "Repo",
    "Role",
    "SetActionRequest",
    "Spec",
    "State",
    "SuggestRequest",
    "Suggestion",
    "Suggestions",
    "Task",
    "TaskKind",
    "TaskQueue",
    "Title",
    "URLText",
    "UnassignTaskRequest",
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

Spec = Annotated[str, StringConstraints(max_length=100_000)]
"""A task's or a feature's spec, as markdown."""

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
    """What a note (or a link) is about: a feature, a task, a question or an action (26.4-26.5)."""

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
    """A typed pointer from a feature, task, question or action to something else (26.4).

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
