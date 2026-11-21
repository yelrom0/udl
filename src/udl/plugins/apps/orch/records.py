"""
---------------
- records.py -
---------------

The orchestrator's records: a Project and a Repo (26.2), a Feature and a Task (26.3), the
Counter that hands out their keys, and the agents that do the work (27.1): an Agent, an
AgentSession and a SessionEvent. Links and notes are 26.4's and 26.5's. Each is a udl record
(udl.db.records), so every database plugin stores it the same way, and the record contract
round-trips its example.

A Project is unique by its name; a Repo by its project and its name together (two projects may
each have a repo called udl), and repos are also looked up by GitHub (`yelrom0/udl`), which is
how a webhook's event finds its repo. A Feature is unique by its project and key, a Task by its
project and key (both as the card says).

The fields are typed with the same aliases orch.models uses, so what a shape refuses a record
refuses too — one rule, in one place.

An agent's hierarchy is a forest, and that is checked where an agent is saved: a manager must be
another agent whose role is manager, and no chain of managers may reach itself. A session's
events are its transcript: history, which db sync copies once it happened.

ProjectRecords, RepoRecords, FeatureRecords, TaskRecords, Counters, AgentRecords and
SessionRecords are the typed way to read and write them: an app holds an AppRecords, and these
sit over it, so nothing here knows about a database.
"""

# System Imports
import hashlib
import logging
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Final, Self
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, Field, JsonValue, NonNegativeInt, PositiveInt

# Local Imports
from udl.db.models import JsonObject, utcnow
from udl.db.records import Name, Record, RecordQuery, RecordScope, SyncRule
from udl.errors import (
    ConfigError,
    DatabaseError,
    DoesNotExist,
    DuplicateKey,
    InUse,
    NotAllowed,
)
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import (
    AgentRole,
    AnAgentEvent,
    ManagerPowers,
    SessionState,
)
from udl.plugins.apps.orch.models import (
    CANCELLED,
    DONE,
    OPEN,
    ActionState,
    ActionWhen,
    BranchName,
    CheckedBranchPattern,
    FeatureStatus,
    Gate,
    GitHubAccount,
    GitHubSlug,
    HostName,
    Key,
    LinkKind,
    ManagerVerdict,
    MergePolicy,
    NoteKind,
    OwnerKind,
    Principal,
    Promotion,
    PullReader,
    PullState,
    QuestionState,
    Size,
    Spec,
    State,
    TaskKind,
    Text,
    Title,
    URLText,
)
from udl.plugins.runners.base import SessionRef, TextDelta


class Project(Record):
    """A project: a body of work, its repos (Repo) and its merge policy.

    `promotion` names the branches after a repo's base, in order (["rc", "main"]); a project
    whose base is its last branch has none (udl), and its reviewer's merge is its review.
    `rules` is the markdown every agent on this project is given: the ground rules."""

    kind = "orch_project"
    indexed = ("name",)
    unique = (("name",),)

    name: Name
    title: Title
    description: str = ""
    merge: MergePolicy = MergePolicy.HUMAN
    """Who merges a ready PR (O-D3)."""
    promotion: Promotion = Field(default_factory=list)
    """The branches after the repo's base, in order; empty for udl."""
    review_bot: str | None = None
    """The review bot's login (e.g. claude[bot]), whose verdict a ready PR waits on."""
    rules: str = ""
    """The ground rules, as markdown, given to every agent on this project."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            name="udl",
            title="udl",
            description="What udl is: the local model server and the work it tracks.",
            merge=MergePolicy.HUMAN,
            review_bot="claude[bot]",
            rules="Never push to main.",
        )


class Repo(Record):
    """A repo of a project: its GitHub repository, its account, its base branch, its gate argv,
    its main checkout on each host, and how its task branches are named."""

    kind = "orch_repo"
    indexed = ("project", "name", "github")
    unique = (("project", "name"),)

    project: Name
    name: Name
    github: GitHubSlug
    """owner/repo, as GitHub names it."""
    account: GitHubAccount
    """The github:<name> account udl reaches it through (25.4)."""
    base_branch: BranchName = "main"
    """The branch PRs go into."""
    gate: Gate = Field(default_factory=lambda: ["make", "check"])
    """The argv that is its gate, run before a push (e.g. ["make", "check"])."""
    checkouts: dict[HostName, str] = Field(default_factory=dict)
    """Host name → the main checkout's path there."""
    branch_pattern: CheckedBranchPattern = "{type}/{key}-{slug}"
    """How its task branches are named (23.1)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            name="udl",
            github="yelrom0/udl",
            account="github:yelrom0",
            base_branch="main",
            gate=["make", "check"],
            checkouts={"local": "~/Documents/code/udl"},
            branch_pattern="{type}/{key}-{slug}",
        )


class Feature(Record):
    """A feature: a goal with a spec, the top tier (udl's 26.3, or an imported 22)."""

    kind = "orch_feature"
    indexed = ("project", "key", "order")
    unique = (("project", "key"),)

    project: Name
    """The project it belongs to."""
    key: Key
    """Its key within the project (`F-<n>`, or an imported key like `22`)."""
    title: Title
    """What it is, in a line."""
    spec: Spec = ""
    """Its spec, as markdown."""
    status: FeatureStatus = "planned"
    """Where it is: its own setting, or what its tasks make it (`FeatureRecords` keeps it in
    step with them unless it is `parked`, which is what a person sets it to by hand)."""
    order: int = 0
    """Where it sits among its project's features."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            key="26",
            title="Work tracking: projects, features, tasks, links",
            spec="The plan's Phase 26, as its section reads.",
            status="active",
            order=26,
        )


class Task(Record):
    """A task: one piece of a feature that one agent (or one human) does, usually one PR.

    Its `state` is set only by `orch.transitions.move()`: no code path writes it directly."""

    kind = "orch_task"
    indexed = ("project", "feature", "key", "assignee", "position")
    unique = (("project", "key"),)

    project: Name
    """The project it belongs to."""
    feature: Key
    """The feature it is part of (its key)."""
    key: Key
    """Its key within the project (`<feature key>.<n>`, or the key it was given)."""
    title: Title
    """What it is, in a line."""
    spec: Spec = ""
    """Its spec, as markdown (its card, when it was imported)."""
    task_kind: TaskKind = "build"
    """What kind of work it is: a build, a person's, or a review's. (Not `kind`: `kind` is the
    record's own name.)"""
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
    """The branch it is done on, once it has one."""
    worktree: str | None = None
    """The worktree it is done in, once the dispatcher has made one."""
    updated_by: str = ""
    """Who last moved it (a role, or an agent's name)."""
    size: Size | None = None
    """How big it is (None: not sized yet, which is how a task written before 26.8 reads)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            feature="26",
            key="26.2",
            title="Projects and repos",
            spec="The plan's 26.2 card.",
            state="merged",
            needs=["26.1"],
            repo="udl",
            branch="feat/26.2-projects",
            updated_by="ernie-c",
        )


class Note(Record):
    """A note on a task or a feature (26.5): what was done, a decision, a finding, a review, or
    what udl itself says (a state change, a task moved to another agent's queue).

    Append-only: there is no update, and a correction is another note. Its `owner` is the record
    it is about (a task's or a feature's id), so a note is found by it whatever its key becomes.
    (Its kind is `note_kind`: `kind` is the record's own name.)"""

    kind = "orch_note"
    indexed = ("owner_kind", "owner")

    owner_kind: OwnerKind
    """What it is about: a feature, a task, a question or an action."""
    owner: UUID
    """The id of the record it is about."""
    note_kind: NoteKind = NoteKind.PROGRESS
    """What kind of note it is; `system` is what udl itself writes."""
    text: str
    """What it says, as markdown."""
    author: str = "udl"
    """Who wrote it: an agent's name, `human:<name>`, or `udl`."""
    at: AwareDatetime = Field(default_factory=utcnow)
    """When it was written."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            owner_kind=OwnerKind.TASK,
            owner=UUID("6f1c2f0e-9d4a-4a37-8c1e-0d2b7a5c9e11"),
            note_kind=NoteKind.SYSTEM,
            text="Moved to deepseek-b's queue: ernie-c must stop working on it.",
            author="udl",
        )


class Link(Record):
    """A link: a typed pointer from a feature, task, question or action to something else.

    A link is unique on its project, what it hangs on and its URL: two projects that each have
    a task `1.1` each link the same PR as their own, and a feature and a task with the same key
    never share one. It is looked up by its URL (what finds it) and by its owner (what hangs on
    it)."""

    kind = "orch_link"
    indexed = ("project", "owner_kind", "owner", "url")
    unique = (("project", "owner_kind", "owner", "url"),)

    project: Name
    """The project whose work it is a link to (the owner's project)."""
    owner_kind: OwnerKind
    """What it hangs on."""
    owner: str
    """What it hangs on, by key (the task's key, `Q-7`)."""
    link_kind: LinkKind
    """What it points at (not `kind`: a record's `kind` is the name of its kind)."""
    url: URLText
    """The canonical form of the URL (a `udl:` one for an internal kind)."""
    title: Text = ""
    """What it is called, when it has been seen."""
    state: PullState = Field(default_factory=PullState)
    """What is known about it (for a pull: its state, head sha, checks and review)."""
    added_by: Text = ""
    """Who added it (an agent, `human:<name>`, `scan`, or empty)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            owner_kind=OwnerKind.TASK,
            owner="26.2",
            link_kind="pull",
            url="https://github.com/yelrom0/udl/pull/33",
            title="Projects and repos",
            state=PullState(state="merged", checks="pass"),
            added_by="ernie-c",
        )


class HumanAction(Record):
    """Something only a person can do (28.2), as it is stored: what orch.models.Action is.

    Its key is `A-<n>` of its project's counter, and it is unique by it. `after` is the task it
    waits for (it becomes `now` when that one merges, `orch.actions.become_now`), and `task` is
    the task it is about, whose notes its result is written to."""

    kind = "orch_action"
    indexed = ("project", "key", "state", "when", "assignee", "task")
    unique = (("project", "key"),)

    key: Key
    """Its key within the project (`A-<n>`)."""
    project: Name
    """The project it belongs to."""
    title: Title
    """What is to be done, in a line."""
    text: Text = ""
    """What it says, as markdown."""
    when: ActionWhen = ActionWhen.NOW
    """When it is due: now, after a task, or later."""
    after: Key | None = None
    """The task it waits for, while `when` is `after`."""
    task: Key | None = None
    """The task it is about, when it is about one."""
    assignee: str | None = None
    """The person it is for (`human:<name>`), or None."""
    state: ActionState = ActionState.OPEN
    """Where it is: open, done or dropped."""
    result: str | None = None
    """What came of it: its result, or why it was dropped."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            key="A-1",
            title="Send the OpenRouter key to Liively",
            text="The one from 1Password, for the AI providers.",
            assignee="human:claude",
        )


class Question(Record):
    """Something an agent cannot answer itself (28.1), as it is stored: what orch.models.Question
    is.

    Its key is `Q-<n>` of its project's counter, and it is unique by it. `to` is who it is asked
    of — an agent's name, or `humans` — and `task` is the task it was asked in, which a blocking
    question moves to `blocked` (its `blocked_on` is the question's key) until it is answered or
    closed. Its thread is its `Message`s."""

    kind = "orch_question"
    indexed = ("project", "key", "state", "to", "task")
    unique = (("project", "key"),)

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
    delivered: bool = False
    """Whether its answer has been told to the agent that asked (28.1: an answer is delivered
    into a session once, not on every prompt and not again into the next session)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            key="Q-1",
            asked_by="deepseek-b",
            to="humans",
            title="Which repo is 28.1 in?",
            text="`udl` has two repos, and the card names neither.",
            task="28.1",
            blocking=True,
        )


class Message(Record):
    """One thing said on a question (28.1): the question's own text first, then a follow-up from
    either side.

    It is history — appended, never replaced — and `question` is the question's id, so a thread
    is found by it whatever its key becomes."""

    kind = "orch_message"
    indexed = ("question",)

    question: UUID
    """The id of the question it is said on."""
    text: str
    """What it says, as markdown."""
    author: str = ""
    """Who said it: an agent's name, `human:<name>`, or `udl`."""
    at: AwareDatetime = Field(default_factory=utcnow)
    """When it was said."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            question=UUID("6f1c2f0e-9d4a-4a37-8c1e-0d2b7a5c9e11"),
            text="`udl` has two repos, and the card names neither.",
            author="deepseek-b",
        )


class Counter(Record):
    """One number a counter handed out: the compare-and-set claim of a key (`F-`, `Q-`, `A-`).

    Each claim is a record of its own and its number is part of its unique key, so the
    database's own uniqueness is the lock: two callers that read the same last number both try
    to store the next one, and the one that loses is told (DuplicateKey) and reads again. Two
    callers therefore never get the same number, also between udls on one database."""

    kind = "orch_counter"
    indexed = ("project", "prefix", "last")
    unique = (("project", "prefix", "last"),)

    project: Name
    """The project the numbers belong to."""
    prefix: str
    """What is counted (`F-` for features, `<feature key>.` for a feature's tasks)."""
    last: int = 0
    """The number this claim handed out."""

    @classmethod
    def example(cls) -> Self:
        return cls(project="udl", prefix="F-", last=26)


class Agent(Record):
    """An agent, as it is stored: what orch.agents.Agent is (one shape, one place for its rules).

    It is unique by its name. A manager must be another agent whose role is manager, and a
    manager chain may not reach itself (AgentRecords checks both, since they are facts about the
    others)."""

    kind = "orch_agent"
    indexed = ("name", "role", "manager")
    unique = (("name",),)

    name: Name
    title: Title
    runner: Name
    runner_options: JsonObject = Field(default_factory=dict)
    model: str = ""
    host: HostName = "local"
    role: AgentRole = AgentRole.IMPLEMENTER
    manager: Name | None = None
    manages: ManagerPowers = Field(default_factory=ManagerPowers)
    projects: list[Name] = Field(default_factory=list)
    brief: str = ""
    max_nudges: PositiveInt = 3
    enabled: bool = True
    paused: bool = False
    offline: bool = False
    """Whether its host could not be reached: what a failed `check_host` sets, so no session is
    started there until it answers again (27.7)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            name="deepseek-b",
            title="deepseek-b",
            runner="opencode",
            runner_options={"args": ["--model", "deepseek-v4"]},
            model="deepseek-v4",
            host="local",
            role=AgentRole.IMPLEMENTER,
            manager="claude",
            manages=ManagerPowers(),
            projects=["udl"],
            brief="Build the task on your queue, test-first.",
            max_nudges=3,
            enabled=True,
            paused=False,
        )


class AgentSession(Record):
    """A session an agent is in, as it is stored: one runtime's own (a session belongs to the udl
    that started it, on its host), so it is not synced; its transcript (SessionEvent) is."""

    kind = "orch_agent_session"
    indexed = ("agent", "task", "state")
    scope = RecordScope.RUNTIME
    sync = SyncRule.NEVER

    agent: Name
    project: str = ""
    """The project its task is in (its key is only unique within one)."""
    task: str = ""
    ref: SessionRef
    state: SessionState = SessionState.STARTING
    turns: NonNegativeInt = 0
    nudges: NonNegativeInt = 0
    failures: NonNegativeInt = 0
    """How many turns in a row failed (the dispatcher retries three times, 27.7)."""
    limited_until: AwareDatetime | None = None
    limits: NonNegativeInt = 0
    """How many times its runner limited it (a limited turn is not a turn, so this is what tells
    one limit from the next, 27.7)."""
    tokens_in: NonNegativeInt = 0
    tokens_out: NonNegativeInt = 0
    cost: Decimal | None = None
    last_text: str = ""
    """What the last turn was told, so the same news is not told twice (27.7)."""
    last_error: str = ""
    """Why its last turn failed, as its runner said (what a retry reports, 27.7)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            agent="deepseek-b",
            task="27.1",
            ref=SessionRef(runner="opencode", id="ses_7f3a", cwd="/work/udl", host="local"),
            state=SessionState.RUNNING,
            turns=2,
            nudges=0,
            limited_until=None,
            tokens_in=1200,
            tokens_out=340,
            cost=Decimal("0.03"),
            last_text="Tests first: the record contract covers the three kinds.",
        )


class SessionEvent(Record):
    """One thing that happened in a session, as its runner said it: the transcript.

    It is history (a session's live view and, afterwards, its transcript), kept as long as
    `[apps.orch] session_days` says, and what db sync copies of it is the events that happened."""

    kind = "orch_session_event"
    indexed = ("agent", "session")
    scope = RecordScope.RUNTIME
    sync = SyncRule.ENDED

    agent: Name
    """The agent whose session it happened in."""
    session: UUID
    """The session (AgentSession.id)."""
    turn: NonNegativeInt = 0
    """The turn it happened in (0: before the first one)."""
    event: AnAgentEvent
    """What happened (its `kind` says which of the runner's events it is)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            agent="deepseek-b",
            session=UUID("0b9a1f8e-6a5f-4d1f-9a52-2d0e7f1a3c48"),
            turn=2,
            event=TextDelta(text="The tests fail for the right reason; now the records."),
        )


def hash_token(token: str) -> str:
    """The SHA-256 of a token (hex): what an AgentToken keeps, so the token itself is never
    stored outside udl's secret store."""
    return hashlib.sha256(token.encode()).hexdigest()


class AgentToken(Record):
    """A token an agent calls udl with (27.2), as it is stored: its SHA-256, never the token
    itself (that is in udl's secret store, at apps/orch/agents/<name>/token).

    It is unique by its hash, so a token is found by hashing the one presented, and issuing one
    for an agent replaces the one it had (rotating)."""

    kind = "orch_agent_token"
    indexed = ("agent", "hash")
    unique = (("hash",),)

    agent: Name
    """The agent it belongs to."""
    hash: str
    """The SHA-256 of the token (hex)."""
    # (created_at is the record's own: when it was issued.)
    last_used_at: datetime | None = None
    """When it was last used, if it has been."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            agent="deepseek-a",
            hash=hashlib.sha256(b"udl_agent_example").hexdigest(),
            last_used_at=None,
        )


SCANNED: Final = "scan"
"""What a link's `added_by` says when a save's scan added it (not a person or an agent)."""

log = logging.getLogger(__name__)
"""Where a scan that could not read a text says so (it never breaks the save)."""


async def _scan(
    records: AppRecords, project: str, owner_kind: OwnerKind, owner: str, text: str
) -> None:
    """Store the links a text makes (a GitHub URL, a task key in backticks), as links of
    `project`/`owner_kind`/`owner`, and drop the ones the text no longer has.

    A scan is a save's helper: it never breaks the save (a text with a URL udl cannot parse is
    saved, and the scan says so in the log), so a task's spec is never refused for a link's
    sake."""
    from udl.plugins.apps.orch.links import links_in

    try:
        wanted = {link.url: link for link in links_in(text, project=project)}
    except ValueError as error:  # (a URL that is not one: udl's own parser refusing it)
        log.warning("orch could not scan %s %s: %s", owner_kind, owner, error)
        return
    links = LinkRecords(records)
    for link in await links.for_owner(project, owner_kind, owner):
        if link.added_by == SCANNED and link.url not in wanted:
            await links.delete(link)
    for url, link in wanted.items():
        if any(found.url == url for found in await links.for_owner(project, owner_kind, owner)):
            continue
        await links.add(
            project=project,
            owner_kind=owner_kind,
            owner=owner,
            kind=link.link_kind,
            url=url,
            added_by=SCANNED,
        )


class ProjectRecords:
    """Where orch keeps its projects: over an app's records (udl.db.records), so they are
    stored as any other record is."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the projects are kept in (what a RepoRecords is made over too)."""
        return self._records

    async def require(self, name: str) -> Project:
        """The project called `name`, or DoesNotExist (what saving a repo of it needs)."""
        found = await self.project(name)
        if found is None:
            raise DoesNotExist(f"There is no project called {name!r}")
        return found

    async def save_project(self, project: Project) -> Project:
        """Store `project` (by its id, so storing one again replaces it). Raises DuplicateKey
        for another project with the same name."""
        return await self._records.save(project)

    async def project(self, name: str) -> Project | None:
        """The project called `name`, or None when there is none."""
        found = await self._records.list(Project, RecordQuery(where={"name": name}))
        return found[0] if found else None

    async def projects(self) -> list[Project]:
        """Every project, by name."""
        return sorted(await self._records.list(Project, RecordQuery()), key=lambda p: p.name)

    async def delete_project(self, name: str) -> bool:
        """Delete the project called `name` and everything of it: its repos, features, tasks,
        their notes and the counters its keys came from. Returns whether there was one."""
        project = await self.project(name)
        if project is None:
            return False
        await _delete_actions(self._records, name)
        await _delete_questions(self._records, name)
        for kind in (Repo, Feature, Task, Counter):
            for record in await self._records.list(kind, RecordQuery(where={"project": name})):
                if isinstance(record, Task):
                    await _delete_notes(self._records, "task", record.id)
                    await _delete_links(self._records, record.project, "task", record.key)
                elif isinstance(record, Feature):
                    await _delete_notes(self._records, "feature", record.id)
                    await _delete_links(self._records, record.project, "feature", record.key)
                await self._records.delete(kind, record.id)
        return await self._records.delete(Project, project.id)


class RepoRecords:
    """Where orch keeps a project's repos: looked up by project and name, and by GitHub."""

    def __init__(self, records: AppRecords, projects: ProjectRecords | None = None) -> None:
        self._records = records
        self._projects = projects if projects is not None else ProjectRecords(records)

    async def save_repo(self, repo: Repo) -> Repo:
        """Store `repo` (by its id, so storing one again replaces it). Raises DoesNotExist when
        its project is not there, and DuplicateKey when the project already has a repo of that
        name."""
        await self._projects.require(repo.project)
        return await self._records.save(repo)

    async def repo(self, project: str, name: str) -> Repo | None:
        """The repo called `name` of `project`, or None when there is none."""
        found = await self._records.list(
            Repo, RecordQuery(where={"project": project, "name": name})
        )
        return found[0] if found else None

    async def repos(self, project: str) -> list[Repo]:
        """Every repo of `project`, by name."""
        found = await self._records.list(Repo, RecordQuery(where={"project": project}))
        return sorted(found, key=lambda repo: repo.name)

    async def repos_for(self, github: str) -> list[Repo]:
        """Every repo that is the GitHub repository `github` (owner/repo), by project then name."""
        found = await self._records.list(Repo, RecordQuery(where={"github": github}))
        return sorted(found, key=lambda repo: (repo.project, repo.name))

    async def delete_repo(self, project: str, name: str) -> bool:
        """Delete the repo called `name` of `project`. Returns whether there was one."""
        repo = await self.repo(project, name)
        return repo is not None and await self._records.delete(Repo, repo.id)


class LinkRecords:
    """Where orch keeps its links: looked up by owner (what hangs on them) and by URL (what
    finds them), both ways.

    An owner is a project, what it hangs on (`owner_kind`) and its key together: two projects
    that each have a task `1.1` each link a PR of their own, and `for_owner`/`delete_for` touch
    only the one project's and the one kind's links."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the links are kept in."""
        return self._records

    async def add(
        self,
        *,
        project: str,
        owner_kind: OwnerKind,
        owner: str,
        kind: LinkKind,
        url: str,
        title: str = "",
        state: PullState | None = None,
        added_by: str = "",
    ) -> Link:
        """Store a link to `url` on `project`/`owner_kind`/`owner` (canonicalised first), or
        give the stored one when it is there already (one URL, one link). Raises ValueError when
        the URL is not one the kind can point at."""
        from udl.plugins.apps.orch.links import canonical, is_github

        clean = canonical(url)
        if not clean.strip():
            raise ValueError("A link needs a URL")
        if kind in ("pull", "issue", "commit", "branch", "ci_run") and not is_github(clean):
            raise ValueError(f"A {kind} link needs a GitHub URL (not {url!r})")
        if kind == "url" and not clean.startswith(("http://", "https://", "udl:")):
            raise ValueError(f"A url link needs a URL (not {url!r})")
        for found in await self.by_url(clean):
            if (found.project, found.owner_kind, found.owner) == (project, owner_kind, owner):
                return found
        return await self._records.save(
            Link(
                project=project,
                owner_kind=owner_kind,
                owner=owner,
                link_kind=kind,
                url=clean,
                title=title,
                state=state or PullState(),
                added_by=added_by,
            )
        )

    async def get(self, link_id: UUID) -> Link | None:
        """The link with that id, or None when there is none."""
        return await self._records.get(Link, link_id)

    async def by_url(self, url: str) -> list[Link]:
        """Every link to `url` (canonicalised), by owner."""
        from udl.plugins.apps.orch.links import canonical

        found = await self._records.list(Link, RecordQuery(where={"url": canonical(url)}))
        return sorted(found, key=lambda link: (link.project, link.owner_kind, link.owner))

    async def for_url(self, url: str) -> list[Link]:
        """Every link to `url`, however it was written (canonicalised first)."""
        return await self.by_url(url)

    async def for_owner(self, project: str, owner_kind: OwnerKind, owner: str) -> list[Link]:
        """Every link hanging on `project`/`owner_kind`/`owner`, by URL."""
        found = await self._records.list(
            Link,
            RecordQuery(where={"project": project, "owner_kind": owner_kind.value, "owner": owner}),
        )
        return sorted(found, key=lambda link: link.url)

    async def save(self, link: Link) -> Link:
        """Store `link` as it is (what a refresh does)."""
        return await self._records.save(link)

    async def delete(self, link: Link) -> bool:
        """Delete `link`. Returns whether it was there."""
        return await self._records.delete(Link, link.id)

    async def delete_for(self, project: str, owner_kind: OwnerKind, owner: str) -> int:
        """Delete every link hanging on `project`/`owner_kind`/`owner` (what a deleted task or
        feature does). Returns how many went."""
        gone = await self.for_owner(project, owner_kind, owner)
        for link in gone:
            await self.delete(link)
        return len(gone)

    async def refresh(self, link: Link, *, github: PullReader) -> Link:
        """`link` with its state read again through `github_get_pull`, stored and given back.

        The link is the service's own: it is stored as it is, so what a pull is now is what a
        link says from now on. A link that is not a pull is given back unchanged."""
        from udl.plugins.apps.orch.links import refresh_pull

        refreshed = await refresh_pull(link, github=github)
        if refreshed == link:
            return link
        return await self.save(refreshed)


class FeatureRecords:
    """Where orch keeps its features: looked up by project and key."""

    def __init__(self, records: AppRecords, projects: ProjectRecords | None = None) -> None:
        self._records = records
        self._projects = projects if projects is not None else ProjectRecords(records)

    @property
    def records(self) -> AppRecords:
        """The records the features are kept in (what a TaskRecords is made over too)."""
        return self._records

    async def save_feature(self, feature: Feature) -> Feature:
        """Store `feature` (by its id, so storing one again replaces it). Raises DoesNotExist
        when its project is not there, and DuplicateKey when the project already has a feature
        of that key. Its spec is scanned as it is saved: a GitHub URL or a task key in backticks
        becomes a link of its own (26.4)."""
        await self._projects.require(feature.project)
        saved = await self._records.save(feature)
        await _scan(self._records, feature.project, OwnerKind.FEATURE, feature.key, feature.spec)
        return saved

    async def feature(self, project: str, key: str) -> Feature | None:
        """The feature called `key` of `project`, or None when there is none."""
        found = await self._records.list(
            Feature, RecordQuery(where={"project": project, "key": key})
        )
        return found[0] if found else None

    async def features(self, project: str) -> list[Feature]:
        """Every feature of `project`, in its order and then by key."""
        found = await self._records.list(Feature, RecordQuery(where={"project": project}))
        return sorted(found, key=lambda feature: (feature.order, feature.key))

    async def create_feature(
        self,
        project: str,
        title: str,
        *,
        key: Key | None = None,
        spec: Spec = "",
        status: FeatureStatus = "planned",
        order: int = 0,
    ) -> Feature:
        """A feature of `project` with `key`, or the next `F-` number of its counter, stored.
        Raises DoesNotExist when the project is not there."""
        claimed = (
            key if key is not None else f"F-{await Counters(self._records).claim(project, 'F-')}"
        )
        return await self.save_feature(
            Feature(
                project=project,
                key=claimed,
                title=title,
                spec=spec,
                status=status,
                order=order,
            )
        )

    async def delete_feature(self, project: str, key: str) -> bool:
        """Delete the feature called `key` of `project`, and its tasks with it. Returns whether
        there was one."""
        feature = await self.feature(project, key)
        if feature is None:
            return False
        for task in await TaskRecords(self._records, self._projects).tasks(project, feature=key):
            await _delete_notes(self._records, "task", task.id)
            await _delete_links(self._records, task.project, "task", task.key)
            await _delete_actions(self._records, task.project, task.key)
            await self._records.delete(Task, task.id)
        await _delete_notes(self._records, "feature", feature.id)
        await _delete_links(self._records, feature.project, "feature", feature.key)
        return await self._records.delete(Feature, feature.id)


class TaskRecords:
    """Where orch keeps its tasks: looked up by project, feature, key, state and assignee."""

    def __init__(self, records: AppRecords, projects: ProjectRecords | None = None) -> None:
        self._records = records
        self._projects = projects if projects is not None else ProjectRecords(records)

    @property
    def records(self) -> AppRecords:
        """The records the tasks are kept in."""
        return self._records

    async def save_task(self, task: Task) -> Task:
        """Store `task` (by its id, so storing one again replaces it). Raises DoesNotExist when
        its project is not there, DuplicateKey when the project already has a task of that key,
        and NotAllowed when it would set the task's state: a new task is `open`, and an existing
        one's state changes only through `orch.transitions.move()` (the card's done-when). A
        task's feature must be there: a task always links back to one."""
        await self._projects.require(task.project)
        if (
            await FeatureRecords(self._records, self._projects).feature(task.project, task.feature)
            is None
        ):
            raise DoesNotExist(f"There is no feature {task.feature!r} in {task.project!r}")
        stored = await self._records.get(Task, task.id)
        if stored is not None and stored.state != task.state:
            raise NotAllowed(
                f"{task.key!r} would be saved in {task.state!r}, from {stored.state!r}",
                hint="A task's state changes through move() (orch.transitions), not a save.",
            )
        if stored is None and task.state != OPEN:
            raise NotAllowed(
                f"a new task cannot start in {task.state!r}",
                hint="A task is saved open, and then moved.",
            )
        saved = await self._store(task)
        await _scan(self._records, task.project, OwnerKind.TASK, task.key, task.spec)
        return saved

    async def import_task(self, task: Task) -> Task:
        """Store `task` as a plan file has it, its state included (26.7).

        The one path that writes a state without a move: an import records what a plan says
        happened, and a plan's `[x]` is not a move of what udl is doing (`save_task`'s rule,
        which holds for every caller but this one). Its project and its feature must be there."""
        await self._projects.require(task.project)
        if (
            await FeatureRecords(self._records, self._projects).feature(task.project, task.feature)
            is None
        ):
            raise DoesNotExist(f"There is no feature {task.feature!r} in {task.project!r}")
        saved = await self._store(task)
        await _scan(self._records, task.project, OwnerKind.TASK, task.key, task.spec)
        return saved

    async def save_all(self, tasks: Sequence[Task], *, also: Sequence[Record] = ()) -> list[Task]:
        """Store tasks as one write — all of them, or none — and `also` in the same write (a
        queue's system note, 26.5), keeping each feature's status in step with its tasks.

        What `save_task` checks of a new task is checked for every task: its project and its
        feature are there. Its state is not checked: a batch is a queue's own write, and the
        states in it are the ones `orch.transitions.move()` planned (the table), which
        `save_task`'s guard cannot see. Returns the stored tasks, in order."""
        if not tasks and not also:
            return []
        features = FeatureRecords(self._records, self._projects)
        for task in tasks:
            await self._projects.require(task.project)
            if await features.feature(task.project, task.feature) is None:
                raise DoesNotExist(f"There is no feature {task.feature!r} in {task.project!r}")
            if await self._records.get(Task, task.id) is None and task.state != OPEN:
                raise NotAllowed(
                    f"a new task cannot start in {task.state!r}",
                    hint="A task is saved open, and then moved.",
                )
        followed = await self._follow_all(tasks)
        batch: list[Record] = [*tasks, *followed, *also]
        saved = await self._records.save_all(batch)
        return [one for one in saved[: len(tasks)] if isinstance(one, Task)]

    async def _follow_all(self, tasks: Sequence[Task]) -> list[Feature]:
        """The features whose status the batch changes, ready to be written with it."""
        changed: list[Feature] = []
        for project, key in sorted({(task.project, task.feature) for task in tasks}):
            feature = await FeatureRecords(self._records, self._projects).feature(project, key)
            if feature is None or feature.status == "parked":
                continue
            in_batch = {
                task.id: task for task in tasks if (task.project, task.feature) == (project, key)
            }
            states = {
                in_batch.get(one.id, one).state for one in await self.tasks(project, feature=key)
            }
            status = _status_of(states)
            if status is not None and status != feature.status:
                changed.append(feature.model_copy(update={"status": status}))
        return changed

    async def move(self, task: Task, to: str, *, by: Principal, note: str = "") -> Task:
        """`task` moved to `to` by `by`, stored, and noted: the one path that sets a task's
        state (the table is orch.transitions', so what may move it is one rule in one place), and
        the one that says so in the task's history — a `system` note, `<from> → <to>, by <who>`
        (26.5), so a task's history reads as its card's notes do.

        The task and its note are one batch: a state change never lands without its history
        entry (the paths that plan their own moves — a queue's, a merge's — build the same note
        with `move_note` and write it in their batch)."""
        from udl.plugins.apps.orch.transitions import move

        moved = move(task, to, by=by, note=note)
        saved = await self.save_moved(task, moved, by=by)
        return next((one for one in saved if one.id == moved.id), moved)

    async def save_moved(self, task: Task, moved: Task, *, by: Principal) -> list[Task]:
        """`moved` (what `transitions.move()` made of `task`) stored with the note that says so,
        in one batch: a state change never lands without its history entry.

        This is `move()`'s own write. A path that plans several moves (a queue's reassign is two)
        builds the same note with `move_note()` and writes it in its own batch."""
        return await self.save_all([moved], also=[move_note(task, moved, by)])

    async def _store(self, task: Task) -> Task:
        """Store `task` as it is (a save's and a move's own path) and keep its feature's status
        in step with its tasks."""
        saved = await self._records.save(task)
        await self._follow(saved)
        return saved

    async def _follow(self, task: Task) -> None:
        """Keep `task`'s feature's status in step with its tasks: `active` once one is past
        `open` (and not cancelled), `done` when all of them are done or cancelled, `planned`
        while they are all open. A `parked` feature is what a person set by hand: left alone."""
        feature = await FeatureRecords(self._records, self._projects).feature(
            task.project, task.feature
        )
        if feature is None or feature.status == "parked":
            return
        tasks = await self.tasks(task.project, feature=task.feature)
        status = _status_of({found.state for found in tasks})
        if status is not None and status != feature.status:
            await self._records.save(feature.model_copy(update={"status": status}))

    async def task(self, project: str, key: str) -> Task | None:
        """The task called `key` of `project`, or None when there is none."""
        found = await self._records.list(Task, RecordQuery(where={"project": project, "key": key}))
        return found[0] if found else None

    async def tasks(
        self,
        project: str,
        *,
        feature: str | None = None,
        state: str | None = None,
        assignee: str | None = None,
    ) -> list[Task]:
        """The tasks of `project` that match what is given, by key. (`state` is filtered here:
        it is a state of the table, not a column a database indexes.)"""
        where: dict[str, JsonValue] = {"project": project}
        if feature is not None:
            where["feature"] = feature
        if assignee is not None:
            where["assignee"] = assignee
        found = await self._records.list(Task, RecordQuery(where=where))
        if state is not None:
            found = [task for task in found if task.state == state]
        return sorted(found, key=lambda task: task.key)

    async def create_task(
        self,
        project: str,
        feature: Key,
        title: str,
        *,
        key: Key | None = None,
        spec: Spec = "",
        task_kind: TaskKind = "build",
        repo: str | None = None,
        assignee: str | None = None,
        position: int | None = None,
        hold: str | None = None,
    ) -> Task:
        """A task of `feature` with `key`, or the next `<feature key>.` number of its counter,
        stored. Raises DoesNotExist when the project is not there."""
        claimed = (
            key
            if key is not None
            else f"{feature}.{await Counters(self._records).claim(project, f'{feature}.')}"
        )
        return await self.save_task(
            Task(
                project=project,
                feature=feature,
                key=claimed,
                title=title,
                spec=spec,
                task_kind=task_kind,
                repo=repo,
                assignee=assignee,
                position=position,
                hold=hold,
            )
        )

    async def delete_task(self, project: str, key: str) -> bool:
        """Delete the task called `key` of `project`, and its notes. Returns whether there was
        one."""
        task = await self.task(project, key)
        if task is None:
            return False
        await _delete_notes(self._records, "task", task.id)
        await _delete_links(self._records, task.project, "task", task.key)
        await _delete_actions(self._records, task.project, task.key)
        return await self._records.delete(Task, task.id)


def _status_of(states: set[str]) -> FeatureStatus | None:
    """The status a feature's tasks give it: `done` when all of them are done or cancelled,
    `active` once one is past `open`, `planned` while they are all open. None when it has no
    tasks at all (there is nothing to say)."""
    if not states:
        return None
    if states <= {DONE, CANCELLED}:
        return "done"
    if states - {OPEN, CANCELLED}:
        return "active"
    return "planned"


class Counters:
    """The counters a project's keys come from (`F-`, `Q-`, `A-`, and a feature's tasks).

    A number is claimed by compare-and-set: read the last one handed out, add one, and store it
    as a claim of its own. The database's uniqueness is the lock, so a caller that loses the
    race is told (DuplicateKey) and reads again."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the counters are kept in."""
        return self._records

    async def claim(self, project: str, prefix: str) -> int:
        """The next number for `prefix` in `project` (`F-` → 1, then 2). Raises DoesNotExist
        when the project is not there.

        The claim is a row, and only the last one is kept: once a number is stored the older
        claims of that prefix go, so reading the last number is one row however long a project
        has been counting. Two callers claiming at once still cannot get the same number: both
        store the same `last`, and the database's uniqueness refuses one of them."""
        await ProjectRecords(self._records).require(project)
        for _ in range(10):  # (a caller that loses the race reads again, a few times)
            latest = await self.latest(project, prefix)
            nxt = Counter(
                project=project, prefix=prefix, last=1 if latest is None else latest.last + 1
            )
            try:
                saved = await self._records.save(nxt)
            except DuplicateKey:
                continue  # (someone claimed that number first)
            await self.keep(project, prefix, saved)
            return saved.last
        raise DatabaseError(
            f"Could not claim a {prefix} number in {project!r}: ten tries lost",
            hint="Another caller is claiming numbers as fast as this one: try again.",
        )

    async def latest(self, project: str, prefix: str) -> Counter | None:
        """The last number claimed for `prefix` in `project`, or None when none has been."""
        found = await self.claims(project, prefix)
        return max(found, key=lambda counter: counter.last) if found else None

    async def keep(self, project: str, prefix: str, saved: Counter) -> None:
        """Clear the claims of `prefix` older than `saved` (the one just stored). A claim newer
        than it — one another caller made meanwhile — is never cleared: clearing it would hand
        its number out twice."""
        for older in await self.claims(project, prefix):
            if older.last < saved.last:
                await self._records.delete(Counter, older.id)

    async def claims(self, project: str, prefix: str) -> list[Counter]:
        """The claims kept for `prefix` in `project` (the last one, and any a concurrent
        caller has not cleared yet)."""
        return await self._records.list(
            Counter, RecordQuery(where={"project": project, "prefix": prefix})
        )


class AgentRecords:
    """Where orch keeps its agents: over an app's records, with the hierarchy's rules checked as
    one is saved (a manager must be an agent whose role is manager, and no chain may reach
    itself)."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the agents are kept in (what a SessionRecords is made over too)."""
        return self._records

    @property
    def tokens(self) -> AgentTokenRecords:
        """The tokens its agents call udl with (27.2)."""
        return AgentTokenRecords(self._records)

    async def save_agent(self, agent: Agent) -> Agent:
        """Store `agent` (by its id, so storing one again replaces it), after checking its place
        in the hierarchy. Raises DuplicateKey for another agent with the same name,
        DoesNotExist for a manager that is not there, and ConfigError for a manager that is not
        a manager, a chain that reaches itself, or a manager that has reports and is being
        renamed or is no longer a manager (its reports would be left pointing at nothing)."""
        await self._check_hierarchy(agent)
        await self._check_reports(agent)
        return await self._records.save(agent)

    async def agent(self, name: str) -> Agent | None:
        """The agent called `name`, or None when there is none."""
        found = await self._records.list(Agent, RecordQuery(where={"name": name}))
        return found[0] if found else None

    async def agents(self) -> list[Agent]:
        """Every agent, by name."""
        return sorted(await self._records.list(Agent, RecordQuery()), key=lambda one: one.name)

    async def reports(self, name: str) -> list[Agent]:
        """Every agent whose manager is `name`, by name (empty: it manages nobody)."""
        found = await self._records.list(Agent, RecordQuery(where={"manager": name}))
        return sorted(found, key=lambda one: one.name)

    async def delete_agent(self, name: str) -> bool:
        """Delete the agent called `name`, its sessions and their events, and the tokens it
        called udl with (so they stop working at once, whatever name comes next). Returns whether
        there was one. Raises InUse while it is another agent's manager: deleting it would
        leave its reports pointing at a name that is not there (they are moved, or its
        manager's, first)."""
        agent = await self.agent(name)
        if agent is None:
            return False
        reports = await self.reports(name)
        if reports:
            named = ", ".join(found.name for found in reports)
            raise InUse(f"The agent {name} is the manager of {named}: it cannot be deleted")
        sessions = await self._records.list(AgentSession, RecordQuery(where={"agent": name}))
        for session in sessions:
            for event in await self._records.list(
                SessionEvent, RecordQuery(where={"session": session.id})
            ):
                await self._records.delete(SessionEvent, event.id)
            await self._records.delete(AgentSession, session.id)
        await self.tokens.delete_for(name)
        return await self._records.delete(Agent, agent.id)

    async def _check_hierarchy(self, agent: Agent) -> None:
        """Refuse an agent whose manager is not there, is not a manager, or whose chain of
        managers reaches the agent itself (which is also the case when it manages itself)."""
        seen = [agent.name]
        manager = agent.manager
        while manager is not None:
            # (The cycle is checked before the lookup: coming back to an agent already seen is
            # the chain reaching itself, whatever the stored copy's role was.)
            if manager in seen:
                raise ConfigError(
                    f"{agent.name}'s manager chain reaches itself: {' -> '.join([*seen, manager])}"
                )
            seen.append(manager)
            found = await self.agent(manager)
            if found is None:
                raise DoesNotExist(f"There is no agent called {manager!r}")
            if found.role is not AgentRole.MANAGER:
                raise ConfigError(
                    f"The agent {agent.name} reports to {manager}, which is not a manager "
                    f"(its role is {found.role})"
                )
            manager = found.manager

    async def _check_reports(self, agent: Agent) -> None:
        """Refuse to rename a manager that has reports, or to save it without the manager role:
        either would leave its reports pointing at a name that is not a manager, or at none."""
        stored = await self._records.get(Agent, agent.id)
        if stored is None or stored.role is not AgentRole.MANAGER:
            return
        reports = await self.reports(stored.name)
        if not reports:
            return
        named = ", ".join(found.name for found in reports)
        if agent.name != stored.name:
            raise ConfigError(
                f"The agent {stored.name} has reports ({named}): it cannot be renamed"
            )
        if agent.role is not AgentRole.MANAGER:
            raise ConfigError(
                f"The agent {stored.name} has reports ({named}): it cannot stop being a manager"
            )


def move_note(task: Task, moved: Task, by: Principal) -> Note:
    """The `system` note a state change writes (26.5): `<from> → <to>, by <who>`.

    `who` is the caller's own name — the GitHub watcher's, a person's, an agent's — never
    guessed from the state it moved to, so a merge udl made itself is not the watcher's."""
    return Note(
        owner_kind=OwnerKind.TASK,
        owner=moved.id,
        note_kind=NoteKind.SYSTEM,
        text=f"{task.state} → {moved.state}, by {by.who}.",
        author="udl",
    )


class Review(Record):
    """A manager's verdict on a report's pull, for one head (27.8): what `orch_review_task`
    records, and what readiness asks for when the report's manager has the `review` power.

    It is per head, so a new push needs a new review: the verdict on the commit before it says
    nothing about this one."""

    kind = "orch_review"
    indexed = ("project", "task", "head")

    project: Name
    """The project it is in."""
    task: Key
    """The task whose pull it is about."""
    head: str
    """The commit it is about (the pull's head when the manager read it)."""
    verdict: ManagerVerdict
    """What the manager says: `approve`, or `changes`."""
    reasons: str = ""
    """Why, as markdown (the changes it wants, or what it checked)."""
    by: str = ""
    """The manager's name."""
    at: AwareDatetime = Field(default_factory=utcnow)
    """When it said so."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            task="27.8",
            head="dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            verdict="approve",
            reasons="It does what the card says.",
            by="deepseek-a",
        )


class ReviewRecords:
    """Where orch keeps its managers' reviews (27.8): read by task and head, newest first."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    async def save_review(self, review: Review) -> Review:
        """Keep `review`."""
        return await self._records.save(review)

    async def for_task(self, project: str, task: str) -> list[Review]:
        """Every review of `task`'s pull, newest first."""
        found = await self._records.list(
            Review, RecordQuery(where={"project": project, "task": task})
        )
        return sorted(found, key=lambda one: (one.at, one.created_at), reverse=True)

    async def for_head(self, project: str, task: str, head: str) -> Review | None:
        """The newest review of `head` (None: nobody has looked at this head)."""
        for one in await self.for_task(project, task):
            if one.head == head:
                return one
        return None


class NoteRecords:
    """Where orch keeps its notes: appended, and read by the record they are about, oldest
    first (so a task's history reads top to bottom, 26.5).

    There is no update: a note is never replaced, and a correction is another note. A note's
    `owner` is the record it is about (a task's or a feature's id), so it is found by it
    whatever that record's key becomes, and `author` is who wrote it — an agent, `human:<name>`,
    or `udl` for what udl itself says."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the notes are kept in."""
        return self._records

    async def add(self, note: Note) -> Note:
        """Store `note` (appended: a note is never replaced)."""
        return await self._records.save(note)

    async def get(self, note_id: UUID) -> Note | None:
        """The note with that id, or None when there is none."""
        return await self._records.get(Note, note_id)

    async def for_owner(self, owner_kind: OwnerKind, owner: UUID) -> list[Note]:
        """Every note on the record `owner` of `owner_kind`, oldest first (then by when it was
        stored, so notes written in one tick keep their order)."""
        found = await self._records.list(
            Note, RecordQuery(where={"owner_kind": owner_kind.value, "owner": owner})
        )
        return sorted(found, key=lambda note: (note.at, note.created_at))

    async def system(self, owner_kind: OwnerKind, owner: UUID, text: str) -> Note:
        """Write what udl itself says about `owner_kind`/`owner` (a state change, a move), as a
        `system` note by `udl`."""
        return await self.add(
            Note(
                owner_kind=owner_kind,
                owner=owner,
                note_kind=NoteKind.SYSTEM,
                text=text,
                author="udl",
            )
        )


class QuestionRecords:
    """Where orch keeps its questions and their threads (28.1): read by project, key, state, who
    they are asked of and the task they were asked in.

    A question's thread is its `Message`s, oldest first, and the question's own text is the
    first of them — so what an agent asked, and every follow-up, reads top to bottom in one
    place."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the questions are kept in."""
        return self._records

    async def save_question(self, question: Question) -> Question:
        """Store `question` (by its id, so storing one again replaces it)."""
        return await self._records.save(question)

    async def question(self, project: str, key: str) -> Question | None:
        """The question called `key` of `project`, or None when there is none."""
        found = await self._records.list(
            Question, RecordQuery(where={"project": project, "key": key})
        )
        return found[0] if found else None

    async def questions(
        self,
        project: str,
        *,
        state: QuestionState | str | None = OPEN,
        to: str | None = None,
        task: str | None = None,
    ) -> list[Question]:
        """The questions of `project` that match what is given, oldest first. `state` is None for
        every state."""
        where: dict[str, JsonValue] = {"project": project}
        if to is not None:
            where["to"] = to
        if task is not None:
            where["task"] = task
        found = await self._records.list(Question, RecordQuery(where=where))
        if state is not None:
            found = [question for question in found if question.state == state]
        return sorted(found, key=lambda question: question.created_at)

    async def thread(self, question: Question) -> list[Message]:
        """Everything said on `question`, oldest first (its own text included)."""
        found = await self._records.list(Message, RecordQuery(where={"question": question.id}))
        return sorted(found, key=lambda message: (message.at, message.created_at))

    async def say(self, question: Question, text: str, *, author: str) -> Message:
        """Append `text` to `question`'s thread, said by `author`."""
        return await self._records.save(Message(question=question.id, text=text, author=author))


class AgentTokenRecords:
    """Where orch keeps the tokens its agents call udl with: only their hashes (the token itself
    is in udl's secret store). One token per agent — issuing one replaces the one it had."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    async def issue(self, agent: str, token: str) -> AgentToken:
        """Keep the hash of `token` for `agent`, replacing any it had (so the old one stops
        working at once)."""
        digest = hash_token(token)
        for old in await self.for_agent(agent):
            await self._records.delete(AgentToken, old.id)
        return await self._records.save(AgentToken(agent=agent, hash=digest, created_at=utcnow()))

    async def for_agent(self, agent: str) -> list[AgentToken]:
        """The tokens of `agent` (one, unless one is being rotated)."""
        return sorted(
            await self._records.list(AgentToken, RecordQuery(where={"agent": agent})),
            key=lambda one: one.created_at,
        )

    async def by_hash(self, digest: str) -> AgentToken | None:
        """The token with that hash, or None when there is none."""
        found = await self._records.list(AgentToken, RecordQuery(where={"hash": digest}))
        return found[0] if found else None

    async def touch(self, token: AgentToken) -> AgentToken:
        """Note that `token` was just used."""
        return await self._records.save(token.model_copy(update={"last_used_at": utcnow()}))

    async def delete_for(self, agent: str) -> bool:
        """Forget every token of `agent` (when it is deleted). Returns whether it had one."""
        found = await self.for_agent(agent)
        for token in found:
            await self._records.delete(AgentToken, token.id)
        return bool(found)


class SessionRecords:
    """Where orch keeps its agents' sessions and their transcripts: the newest session first,
    the transcript in the order things happened."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    async def save_session(self, session: AgentSession) -> AgentSession:
        """Store `session` (by its id, so storing one again replaces it)."""
        return await self._records.save(session)

    async def session(self, session_id: UUID) -> AgentSession | None:
        """The session with that id, or None when there is none."""
        return await self._records.get(AgentSession, session_id)

    async def sessions(self, agent: str) -> list[AgentSession]:
        """Every session of `agent`, newest first."""
        found = await self._records.list(AgentSession, RecordQuery(where={"agent": agent}))
        return sorted(found, key=lambda one: one.created_at, reverse=True)

    async def current(self, agent: str) -> AgentSession | None:
        """The session `agent` is in: its newest one that has not ended, or None when it has
        none (so it is idle)."""
        for found in await self.sessions(agent):
            if found.state is not SessionState.ENDED:
                return found
        return None

    async def save_event(self, event: SessionEvent) -> SessionEvent:
        """Store `event`, one thing that happened in its session."""
        return await self._records.save(event)

    async def prune(self, before: datetime) -> int:
        """Delete the session events that happened before `before` (what `[apps.orch]
        session_days` keeps is what the dispatcher passes here, 27.7). Returns how many."""
        return await self._records.prune(SessionEvent, before)

    async def transcript(self, session: UUID) -> list[SessionEvent]:
        """Everything that happened in `session`, in the order it happened (by its turn, then
        by when it was stored)."""
        found = await self._records.list(SessionEvent, RecordQuery(where={"session": session}))
        return sorted(found, key=lambda one: (one.turn, one.created_at))


async def _delete_actions(records: AppRecords, project: str, key: str | None = None) -> None:
    """Delete the human actions about or after the task called `key` in `project` — or, when
    `key` is None, every action of the project (28.2: what deleting a task takes with it, and a
    project's actions go with the project, so its `A-` numbers start over)."""
    for action in await records.list(HumanAction, RecordQuery(where={"project": project})):
        if key is None or key in (action.task, action.after):
            await records.delete(HumanAction, action.id)


async def _delete_questions(records: AppRecords, project: str) -> None:
    """Delete every question of `project`, with its thread and the notes and links on it (what
    deleting a project takes with it, so its `Q-` numbers start over)."""
    for question in await records.list(Question, RecordQuery(where={"project": project})):
        for message in await records.list(Message, RecordQuery(where={"question": question.id})):
            await records.delete(Message, message.id)
        await _delete_notes(records, "question", question.id)
        await _delete_links(records, project, "question", question.key)
        await records.delete(Question, question.id)


async def _delete_notes(records: AppRecords, owner_kind: str, owner: UUID) -> None:
    """Delete every note on the record `owner` of `owner_kind` (what deleting it takes with it)."""
    for note in await records.list(
        Note, RecordQuery(where={"owner_kind": owner_kind, "owner": owner})
    ):
        await records.delete(Note, note.id)


async def _delete_links(records: AppRecords, project: str, owner_kind: str, owner: str) -> None:
    """Delete every link hanging on the record `owner` of `owner_kind` in `project` (what
    deleting it takes with it, so a link never answers for a task that is gone)."""
    await LinkRecords(records).delete_for(project, OwnerKind(owner_kind), owner)
