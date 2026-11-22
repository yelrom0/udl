"""
--------------
- agents.py -
--------------

What an agent is (Phase 27): the harness it runs, the model it runs on it, the host it runs on,
its role in the hierarchy, the powers its manager has, the projects it works and the brief it is
given — and the session it is in, with its turns, its nudges, what it cost and what it last said.

An agent's `status` is derived from its session and never stored: paused (its own), offline (its
host did not answer, 27.7's check), limited (its harness's quota is spent until `limited_until`),
working (a session is starting or running), waiting (it answered, or its session failed and a
person is being asked), or idle (it has nothing running).

The hierarchy is a forest: a manager is another agent whose role is manager, and no chain of
managers may reach itself. That is checked where agents are stored (orch.records.AgentRecords),
because it is a fact about the others, not about this one.

These are not records: they are the shapes the orch operations take and answer. orch.records
holds them as records, with the same fields, so a stored agent is the same shape.
"""

# System Imports
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal
from uuid import UUID

# Package Imports
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    TypeAdapter,
)

# Local Imports
from udl.db.models import JsonObject, utcnow
from udl.db.records import Name
from udl.plugins.apps.orch.models import AgentRole, AgentStatus, HostName, QueueEntry, Title
from udl.plugins.runners.base import (
    AgentEvent,
    RunnerFailed,
    RunnerLimited,
    SessionRef,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    Usage,
)

__all__ = [
    "Agent",
    "AgentInfo",
    "AgentRole",
    "AgentSession",
    "AgentStatus",
    "Agents",
    "AnAgentEvent",
    "DeleteAgentRequest",
    "GetAgentRequest",
    "GetSessionRequest",
    "ListAgentsRequest",
    "ManagerPowers",
    "PauseAgentRequest",
    "ResumeAgentRequest",
    "SendRequest",
    "Sent",
    "SessionEventShape",
    "SessionInfo",
    "SessionState",
    "SetAgentRequest",
    "StopSessionRequest",
]

AnAgentEvent = Annotated[
    TextDelta | ToolStarted | ToolFinished | Usage | TurnEnded | RunnerLimited | RunnerFailed,
    Field(discriminator="kind"),
]
"""One thing that happened in a session, as its runner said it (the `kind` says which)."""

_AN_EVENT: Final = TypeAdapter(AnAgentEvent)


def an_event(event: AgentEvent) -> AnAgentEvent:
    """`event` as the transcript holds it: the kinds are the same, so this reads it as one of
    them (`kind` says which)."""
    return _AN_EVENT.validate_python(event)


class SessionState(StrEnum):
    """Where a session is: what its dispatcher last saw of it."""

    STARTING = "starting"
    RUNNING = "running"
    IDLE = "idle"  # it answered a turn and waits for the next
    LIMITED = "limited"  # its harness is out of quota (see limited_until)
    FAILED = "failed"  # its harness failed (a question is raised, 27.7)
    ENDED = "ended"  # it is over


class ManagerPowers(BaseModel):
    """What a manager may do for its reports (O-D10): each power is off until it is given, and
    each switches on operations in its role's list, always within its own subtree (27.8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assign: bool = False
    """Queue suggested tasks to its reports, and reorder their queues."""
    answer: bool = False
    """Answer its reports' questions (else they go up, to the humans)."""
    review: bool = False
    """Its OK is needed before a report's task is ready."""
    plan: bool = False
    """Create and split tasks within the features its reports work on."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Agent(_Model):
    """An agent: the harness it runs, where and how, its role and its place in the hierarchy.

    It reports to the humans unless its `manager` is another agent, and its `projects` are the
    ones it works (empty: all of them)."""

    name: Name
    """What it is called (unique; e.g. deepseek-b)."""
    title: Title
    """How it is shown (e.g. deepseek-b)."""
    runner: Name
    """The runner plugin that starts its sessions (e.g. claude-code)."""
    runner_options: JsonObject = Field(default_factory=dict)
    """What its runner is given (its own options' fields; a secret is a reference)."""
    model: str = ""
    """The model it runs (empty: its runner's default)."""
    host: HostName = "local"
    """The host it runs on (udl.hosts; local is where udl runs)."""
    role: AgentRole = AgentRole.IMPLEMENTER
    """What it does: implements, manages, or reviews."""
    manager: Name | None = None
    """The agent it reports to (None: the humans), whose role must be manager."""
    manages: ManagerPowers = Field(default_factory=ManagerPowers)
    """What it may do for its reports, when it is a manager (O-D10)."""
    projects: list[Name] = Field(default_factory=list)
    """The projects it works (empty: all of them)."""
    brief: str = ""
    """What it is told of itself, as markdown, added to its instructions."""
    max_nudges: PositiveInt = 3
    """How many times it may be nudged towards finishing a task before a person is asked."""
    enabled: bool = True
    """Whether udl starts sessions for it at all."""
    paused: bool = False
    """Whether a person paused it: nothing is dispatched to it until it is resumed."""

    def status(
        self,
        session: AgentSession | None,
        *,
        offline: bool = False,
        now: datetime | None = None,
    ) -> AgentStatus:
        """What it is doing, from its session (`offline` is its host's check, 27.7): paused wins
        over everything, then a host that did not answer, then its session's state."""
        if self.paused:
            return AgentStatus.PAUSED
        if offline:
            return AgentStatus.OFFLINE
        if session is None:
            return AgentStatus.IDLE
        if session.state is SessionState.LIMITED:
            # (Until its quota is back it is limited; once it is, nothing runs until the
            # dispatcher starts it again.)
            until = session.limited_until
            return (
                AgentStatus.LIMITED
                if until is None or until > (now or utcnow())
                else AgentStatus.IDLE
            )
        if session.state in (SessionState.STARTING, SessionState.RUNNING):
            return AgentStatus.WORKING
        if session.state in (SessionState.IDLE, SessionState.FAILED):
            return AgentStatus.WAITING
        return AgentStatus.IDLE  # (ended: it has nothing running)


class AgentSession(_Model):
    """A session an agent is in: the harness's own session (its ref), where it got to, what it
    has used, and what it last said."""

    agent: Name
    """The agent whose session it is."""
    task: str = ""
    """The task it works (a task's key; empty: its manager's standing session)."""
    ref: SessionRef
    """The harness's session: the runner, its id, the folder and the host."""
    state: SessionState = SessionState.STARTING
    """Where it is."""
    turns: NonNegativeInt = 0
    """How many turns it has taken."""
    nudges: NonNegativeInt = 0
    """How many times it has been nudged towards finishing."""
    limited_until: AwareDatetime | None = None
    """When its harness's quota is back, when it said (None: it did not say)."""
    tokens_in: NonNegativeInt = 0
    """The tokens it was given, over its turns."""
    tokens_out: NonNegativeInt = 0
    """The tokens it wrote, over its turns."""
    cost: Decimal | None = None
    """What it has cost, in USD, when its harness says (None: it does not charge per turn)."""
    last_text: str = ""
    """What it last said (the end of its last turn's answer)."""


# ---------------------------------------------------------------------------------------------
# Agents and their sessions (27.9)
# ---------------------------------------------------------------------------------------------


class SessionEventShape(_Model):
    """One thing that happened in a session, as an operation answers it."""

    turn: NonNegativeInt = 0
    """The turn it happened in (0: before the first one)."""
    at: AwareDatetime
    """When it was stored."""
    event: AnAgentEvent
    """What happened (its `kind` says which of the runner's events it is)."""


class SessionInfo(_Model):
    """A session as an operation answers it: where it is, what it has used, and its transcript
    (paged: `events` is the page, `total` is how many there are)."""

    id: UUID
    """Its id (what `orch_get_session` and `orch_stop_session` take)."""
    agent: Name
    """The agent whose session it is."""
    project: str = ""
    """The project its task is in (empty: its manager's standing session)."""
    task: str = ""
    """The task it works (empty: its manager's standing session)."""
    ref: SessionRef
    """The harness's session: the runner, its id, the folder and the host."""
    state: SessionState
    """Where it is."""
    turns: NonNegativeInt = 0
    """How many turns it has taken."""
    nudges: NonNegativeInt = 0
    """How many times it has been nudged."""
    limited_until: AwareDatetime | None = None
    """When its harness's quota is back, when it said."""
    tokens_in: NonNegativeInt = 0
    """The tokens it was given, over its turns."""
    tokens_out: NonNegativeInt = 0
    """The tokens it wrote, over its turns."""
    cost: Decimal | None = None
    """What it has cost, in USD, when its harness says."""
    last_text: str = ""
    """What it last said."""
    created_at: AwareDatetime
    """When it started."""
    events: list[SessionEventShape] = Field(default_factory=list[SessionEventShape])
    """The transcript's page (oldest first)."""
    total: NonNegativeInt = 0
    """How many events its transcript has."""


class AgentInfo(_Model):
    """An agent as an operation answers it: what it is, what it is doing, its session, its queue
    and the agents that report to it."""

    agent: Agent
    """The agent."""
    status: AgentStatus
    """What it is doing now (derived: never stored)."""
    session: SessionInfo | None = None
    """Its current session, when it has one (its transcript's first page)."""
    queue: list[QueueEntry] = Field(default_factory=list[QueueEntry])
    """Its tasks, in the order it will work them."""
    reports: list[Name] = Field(default_factory=list[Name])
    """The agents that report to it."""


class ListAgentsRequest(_Model):
    """Listing the agents."""

    project: Name | None = None
    """Only the agents that work this project (None: all of them)."""


class Agents(_Model):
    """Every agent, each as `AgentInfo` answers one."""

    items: list[AgentInfo] = Field(default_factory=list[AgentInfo])
    """The agents, by name."""


class GetAgentRequest(_Model):
    """One agent, with its queue, its session and its reports."""

    name: Name
    """The agent's name."""


class SetAgentRequest(_Model):
    """Creating or editing an agent: what is not given is kept, and a new one needs a title and
    a runner. `manager` set to `""` clears it (the agent reports to the humans)."""

    name: Name
    """The agent's name (unique)."""
    title: Title | None = None
    """How it is shown."""
    runner: Name | None = None
    """The runner plugin that starts its sessions (e.g. claude-code)."""
    runner_options: JsonObject | None = None
    """What its runner is given (its own options' fields; a secret is a reference)."""
    model: str | None = None
    """The model it runs (empty: its runner's default)."""
    host: HostName | None = None
    """The host it runs on."""
    role: AgentRole | None = None
    """What it does: implements, manages, or reviews."""
    manager: str | None = None
    """The agent it reports to, or `""` for the humans."""
    manages: ManagerPowers | None = None
    """What it may do for its reports, when it is a manager."""
    projects: list[Name] | None = None
    """The projects it works (empty: all of them)."""
    brief: str | None = None
    """What it is told of itself, as markdown."""
    max_nudges: PositiveInt | None = None
    """How many times it may be nudged before a person is asked."""
    enabled: bool | None = None
    """Whether udl starts sessions for it at all."""
    paused: bool | None = None
    """Whether a person paused it."""


class DeleteAgentRequest(_Model):
    """Deleting an agent: refused while it has reports or an unfinished task."""

    name: Name
    """The agent's name."""


class PauseAgentRequest(_Model):
    """Pausing an agent: nothing is dispatched to it until it is resumed."""

    name: Name
    """The agent's name."""


class ResumeAgentRequest(_Model):
    """Resuming an agent: what it has queued is dispatched again."""

    name: Name
    """The agent's name."""


class GetSessionRequest(_Model):
    """A session and a page of its transcript."""

    session: UUID
    """The session's id."""
    offset: NonNegativeInt = 0
    """How many of its events to skip (the transcript's order)."""
    limit: PositiveInt = 50
    """How many to return."""


class StopSessionRequest(_Model):
    """Stopping a session: it ends, and nothing it was doing goes on."""

    session: UUID
    """The session's id."""


class SendRequest(_Model):
    """A message typed into an agent's session (the owner's only).

    `delivery: "turn"` (the only one there is for now) sends it as a turn of its own through the
    agent's runner: the session must be between turns, and the message reaches the agent when it
    is. `"steer"` is refused with a reason — none of udl's harnesses takes input mid-turn, and a
    message kept for the next turn would not be what a person said mid-turn, only a queued one
    (27.11's chats need the same answer for permission prompts)."""

    agent: Name
    """The agent whose session it is."""
    text: str
    """What is said (markdown)."""
    delivery: Literal["turn", "steer"] = "turn"
    """How it is given: as a turn of its own, or steered into the running one."""


class Sent(_Model):
    """What `orch_send` did: the session it went to, and the turn it was."""

    session: UUID
    """The session it went to."""
    turn: int
    """The turn it was (the session's count of turns before it: 0 is its first)."""
