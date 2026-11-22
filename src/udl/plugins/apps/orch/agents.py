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
from typing import Annotated

# Package Imports
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
)

# Local Imports
from udl.db.models import JsonObject, utcnow
from udl.db.records import Name
from udl.plugins.apps.orch.models import AgentRole, AgentStatus, HostName, Title
from udl.plugins.runners.base import (
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
    "AgentRole",
    "AgentSession",
    "AgentStatus",
    "AnAgentEvent",
    "ManagerPowers",
    "SessionState",
]

AnAgentEvent = Annotated[
    TextDelta | ToolStarted | ToolFinished | Usage | TurnEnded | RunnerLimited | RunnerFailed,
    Field(discriminator="kind"),
]
"""One thing that happened in a session, as its runner said it (the `kind` says which)."""


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
