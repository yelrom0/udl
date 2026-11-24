"""
-------------
- events.py -
-------------

The events orch publishes (28.5): what happened to its tasks, its questions and its people's
actions — so a subscription can tell a person, or another tool, without polling the API.

Each type is one of `Orch.event_types()`, so `udl events types` lists it with its schema, its
example and the fields a filter names. They are published where the thing happens and once: a
task's move in `TaskRecords.save_all()` (the one write every move goes through, and the same
batch as its history), a question in `orch.questions`, an action in `orch.actions`, and an idle
agent in the dispatcher.

Text in an event is data, never an instruction: a subscription's action renders it, and a
session that reads one is told so by its own prompt.
"""

# System Imports
from typing import Final

# Package Imports
from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.automations.events import EventType

__all__ = [
    "ACTION_NEEDED",
    "AGENT_IDLE",
    "HOST_HEALTHY",
    "HOST_UNHEALTHY",
    "QUESTION_ANSWERED",
    "QUESTION_ASKED",
    "TASK_BLOCKED",
    "TASK_CHANGED",
    "ActionNeeded",
    "AgentIdle",
    "HostHealthy",
    "HostUnhealthy",
    "QuestionAnswered",
    "QuestionAsked",
    "TaskBlocked",
    "TaskChanged",
]


class _Model(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, use_attribute_docstrings=True
    )


class HostIssueSaid(_Model):
    """One issue a host has, as an event carries it (27.13)."""

    host: str
    """The host."""
    kind: str
    """What is wrong (`memory`, `disk`, `btrfs_unallocated` …)."""
    detail: str
    """The numbers, in a line."""


class HostUnhealthy(_Model):
    """A host is in trouble (27.13): what a check found that a person or a tool should know."""

    host: str
    """The host."""
    issues: list[HostIssueSaid] = Field(default_factory=list[HostIssueSaid])
    """Every issue it has now."""


class HostHealthy(_Model):
    """A host is healthy again (27.13): what cleared, so a subscription can say it is over."""

    host: str
    """The host."""
    cleared: list[HostIssueSaid] = Field(default_factory=list[HostIssueSaid])
    """The issues that cleared since the last check."""


class TaskChanged(_Model):
    """A task moved from one state to another (28.5): every move, whoever made it."""

    project: str
    """The project the task is in."""
    task: str
    """The task, by key (`27.6`)."""
    from_: str = Field(alias="from")
    """The state it was in."""
    to: str
    """The state it is in now."""
    by: str = "udl"
    """Who moved it: an agent's name, `human:<name>`, or `udl`."""


class TaskBlocked(_Model):
    """A task is blocked: it waits on a question, or on something a person must do (28.5)."""

    project: str
    """The project the task is in."""
    task: str
    """The task, by key."""
    on: str = ""
    """What it waits on: a question's key, or the note the move gave it."""


class QuestionAsked(_Model):
    """An agent (or a person) asked a question it cannot answer itself (28.1, 28.5)."""

    project: str
    """The project the question is in."""
    question: str
    """The question, by key (`Q-1`)."""
    task: str = ""
    """The task it was asked in, when it was asked in one."""
    blocking: bool = False
    """Whether the task waits for the answer."""
    asked_by: str = ""
    """Who asked it: an agent's name, or `human:<name>`."""
    to: str = "humans"
    """Who it is asked of: an agent's name (its manager), or `humans`."""


class QuestionAnswered(_Model):
    """A question was answered (28.1, 28.5)."""

    project: str
    """The project the question is in."""
    question: str
    """The question, by key."""
    task: str = ""
    """The task it was asked in, when it was asked in one."""
    by: str = ""
    """Who answered it: an agent's name, or `human:<name>`."""
    answer: str = ""
    """What the answer says."""


class ActionNeeded(_Model):
    """Something only a person can do is needed (28.2, 28.5)."""

    project: str
    """The project the action is in."""
    action: str
    """The action, by key (`A-1`)."""
    task: str = ""
    """The task it is about, when it is about one."""
    when: str = "now"
    """When it is due: `now`, `after` a task, or `later`."""
    assignee: str = ""
    """The person it is for (`human:<name>`), when it is for one."""


class AgentIdle(_Model):
    """An agent has nothing on its queue (28.5): its queue is empty and nothing is dispatched to
    it."""

    agent: str
    """The agent it is."""
    project: str = ""
    """A project it works (its own, or the first of every one it may work)."""


class AgentChanged(_Model):
    """An agent was made, edited, paused, resumed or deleted (29.6): what a tree draws, and what a
    page shows of it, is read again."""

    agent: str
    """The agent it is (the one that was deleted, too: its row is what went)."""
    project: str = ""
    """A project it works, when the change named one."""


TASK_CHANGED: Final = EventType(
    name="orch.task.changed",
    data=TaskChanged,
    summary="A task moved from one state to another",
    example=TaskChanged(
        project="udl", task="28.5", **{"from": "queued", "to": "in_progress"}, by="deepseek-b"
    ),
    subject=lambda event: f"Task {event.task} went from {event.from_} to {event.to}",
    source="orch",
)
"""What orch publishes when any task moves (28.5: every move, from every path)."""

TASK_BLOCKED: Final = EventType(
    name="orch.task.blocked",
    data=TaskBlocked,
    summary="A task is blocked, waiting on a question or on a person",
    example=TaskBlocked(project="udl", task="28.5", on="Q-1"),
    subject=lambda event: f"Task {event.task} is blocked on {event.on or 'a person'}",
    source="orch",
)
"""What orch publishes when a task moves to `blocked` (28.5)."""

QUESTION_ASKED: Final = EventType(
    name="orch.question.asked",
    data=QuestionAsked,
    summary="An agent asked a question it cannot answer itself",
    example=QuestionAsked(
        project="udl",
        question="Q-1",
        task="28.5",
        blocking=True,
        asked_by="deepseek-b",
        to="humans",
    ),
    subject=lambda event: (
        f"Question {event.question}: {event.task or 'no task'}"
        + (" (blocking)" if event.blocking else "")
    ),
    source="orch",
)
"""What orch publishes when a question is asked (28.1, 28.5)."""

QUESTION_ANSWERED: Final = EventType(
    name="orch.question.answered",
    data=QuestionAnswered,
    summary="A question was answered",
    example=QuestionAnswered(
        project="udl", question="Q-1", task="28.5", by="human:claude", answer="The first one."
    ),
    subject=lambda event: f"Question {event.question} answered by {event.by}",
    source="orch",
)
"""What orch publishes when a question is answered (28.1, 28.5)."""

ACTION_NEEDED: Final = EventType(
    name="orch.action.needed",
    data=ActionNeeded,
    summary="Something only a person can do is needed",
    example=ActionNeeded(
        project="udl", action="A-1", task="28.5", when="now", assignee="human:claude"
    ),
    subject=lambda event: f"Action {event.action} is {event.when}: {event.assignee or 'anyone'}",
    source="orch",
)
"""What orch publishes when a person's action is planned, or becomes due (28.2, 28.5)."""

AGENT_IDLE: Final = EventType(
    name="orch.agent.idle",
    data=AgentIdle,
    summary="An agent has nothing on its queue",
    example=AgentIdle(agent="deepseek-b", project="udl"),
    subject=lambda event: f"{event.agent} has nothing to do",
    source="orch",
)
"""What orch publishes when an agent's queue is empty and it goes idle (28.5)."""

AGENT_CHANGED: Final = EventType(
    name="orch.agent.changed",
    data=AgentChanged,
    summary="An agent was made, edited, paused, resumed or deleted",
    example=AgentChanged(agent="deepseek-b"),
    subject=lambda event: f"{event.agent} changed",
    source="orch",
)
"""What orch publishes when an agent itself changes (29.6): the tree and its page read again."""
HOST_UNHEALTHY: Final = EventType(
    name="orch.host.unhealthy",
    data=HostUnhealthy,
    summary="A host is in trouble",
    example=HostUnhealthy(
        host="jeff-wiggle",
        issues=[
            HostIssueSaid(
                host="jeff-wiggle",
                kind="btrfs_unallocated",
                detail="/home/yelrom0 has 0 B unallocated",
            )
        ],
    ),
    subject=lambda event: f"{event.host} is in trouble: {len(event.issues)} issue(s)",
    source="orch",
)
"""What orch publishes when a check finds a host in trouble (27.13)."""

HOST_HEALTHY: Final = EventType(
    name="orch.host.healthy",
    data=HostHealthy,
    summary="A host is healthy again",
    example=HostHealthy(
        host="jeff-wiggle",
        cleared=[
            HostIssueSaid(
                host="jeff-wiggle",
                kind="btrfs_unallocated",
                detail="/home/yelrom0 has 0 B unallocated",
            )
        ],
    ),
    subject=lambda event: f"{event.host} is healthy again",
    source="orch",
)
"""What orch publishes when a check finds a host healthy again (27.13)."""
