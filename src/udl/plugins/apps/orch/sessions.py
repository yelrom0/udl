"""
--------------------
- sessions.py -
--------------------

What orch publishes while an agent's session runs (27.7): that a session started, each thing its
runner said as it said it, that it failed, and that its harness is out of quota.

These are live events (what `GET /orch/sessions/{id}/stream` carries, 27.9) and what the
transcript is made of: the SessionEvent record is the record, and these are the same things as
they happen, so a person watching sees the turn while it runs rather than after it.

Text that came from outside — a review bot's comment, a CI log — is data in these events, never
an instruction: a session that reads one is told so by its own prompt (the card's last bullet).
"""

# System Imports
from typing import Final

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict

# Local Imports
from udl.automations.events import EventType

__all__ = [
    "AGENT_LIMITED",
    "SESSION_ENDED",
    "SESSION_EVENT",
    "SESSION_FAILED",
    "SESSION_STARTED",
    "AgentLimited",
    "SessionEnded",
    "SessionFailed",
    "SessionSaid",
    "SessionStarted",
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class SessionStarted(_Model):
    """A session an agent is in started (27.7)."""

    agent: str
    """The agent it is."""
    session: str
    """The session (its id)."""
    task: str
    """The task it was started on."""
    host: str = "local"
    """The host its harness runs on."""
    runner: str = ""
    """The runner it runs."""


class SessionSaid(_Model):
    """One thing a session's runner said, as it said it (27.7)."""

    agent: str
    """The agent whose session it is."""
    session: str
    """The session (its id)."""
    task: str = ""
    """The task it is on."""
    turn: int = 0
    """The turn it happened in."""
    kind: str = ""
    """Which of the runner's events it is (`text_delta`, `tool_started`, …)."""


class SessionFailed(_Model):
    """A session whose harness failed, after its retries (27.7)."""

    agent: str
    """The agent whose session it is."""
    session: str
    """The session (its id)."""
    task: str = ""
    """The task it was on."""
    why: str = ""
    """What went wrong, as udl could see it (never a secret)."""


class SessionEnded(_Model):
    """A session that ended: its task moved on, or udl stopped it (27.7)."""

    agent: str
    """The agent whose session it is."""
    session: str
    """The session (its id)."""
    task: str = ""
    """The task it was on."""
    state: str = ""
    """What it ended as (`ended`, `failed`, `limited`)."""


class AgentLimited(_Model):
    """An agent whose harness is out of quota: nothing is dispatched to it until `until`
    (27.7)."""

    agent: str
    """The agent it is."""
    session: str
    """The session that found it out (its id)."""
    until: AwareDatetime | None = None
    """When its quota is back, when the harness says."""


SESSION_STARTED: Final = EventType(
    name="orch.session.started",
    data=SessionStarted,
    summary="An agent's session started on a task",
    example=SessionStarted(agent="deepseek-b", session="0b9a1f8e", task="27.7", runner="opencode"),
    subject=lambda event: f"{event.agent} started on {event.task}",
    source="orch",
)
"""What orch publishes when a session starts (27.7)."""

SESSION_EVENT: Final = EventType(
    name="orch.session.event",
    data=SessionSaid,
    summary="One thing a session's runner said, as it said it",
    example=SessionSaid(
        agent="deepseek-b", session="0b9a1f8e", task="27.7", turn=2, kind="text_delta"
    ),
    subject=lambda event: f"{event.agent}: {event.kind}",
    source="orch",
)
"""What orch publishes for each thing a runner says (27.7's live stream)."""

SESSION_ENDED: Final = EventType(
    name="orch.session.ended",
    data=SessionEnded,
    summary="An agent's session ended",
    example=SessionEnded(agent="deepseek-b", session="0b9a1f8e", task="27.7", state="ended"),
    subject=lambda event: f"{event.agent}'s session ended ({event.state})",
    source="orch",
)
"""What orch publishes when a session ends (27.7)."""

SESSION_FAILED: Final = EventType(
    name="orch.session.failed",
    data=SessionFailed,
    summary="An agent's session failed, and its task waits for a person",
    example=SessionFailed(agent="deepseek-b", session="0b9a1f8e", task="27.7", why="its harness"),
    subject=lambda event: f"{event.agent}'s session failed: {event.why}",
    source="orch",
)
"""What orch publishes when a session's harness failed for good (27.7)."""

AGENT_LIMITED: Final = EventType(
    name="orch.agent.limited",
    data=AgentLimited,
    summary="An agent's harness is out of quota",
    example=AgentLimited(agent="deepseek-b", session="0b9a1f8e", until=None),
    subject=lambda event: f"{event.agent} is out of quota",
    source="orch",
)
"""What orch publishes when an agent's harness is out of quota (27.7)."""
