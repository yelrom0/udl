"""
-----------
- runs.py -
-----------

Runs: one action running for one event (or for none, when it was asked for). A run is stored
before it starts (queued), then runs, maybe waits for approval (an AI action's tool call), and
ends: succeeded, failed or cancelled; or it was skipped (a throttle, a disabled action), saying
why. The bus queues runs (udl.automations.bus) and the runner runs them (udl.automations.runner).
"""

# System Imports
from enum import StrEnum
from typing import Self
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, JsonValue, NonNegativeInt

# Local Imports
from udl.db.records import Record, RecordScope, SyncRule
from udl.errors import ErrorInfo


class RunStatus(StrEnum):
    """Where a run is."""

    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @property
    def ended(self) -> bool:
        return self in _ENDED


_ENDED = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.SKIPPED})


class ActionRun(Record):
    """One action running for one event. A run of a dispatch is keyed by its event, action,
    subscription and dispatch (a replay is a new dispatch), so dispatching an event again (after
    a crash) queues nothing twice."""

    kind = "run"
    indexed = (
        "action_id",
        "action",
        "event_id",
        "subscription_id",
        "subscription",
        "status",
        "dispatch",
        "runner",
    )
    unique = (("event_id", "action_id", "subscription_id", "dispatch"),)
    scope = RecordScope.RUNTIME
    sync = SyncRule.ENDED

    action_id: UUID
    action: str
    plugin: str
    event_id: UUID | None = None
    event_type: str | None = None
    subscription_id: UUID | None = None
    subscription: str | None = None
    dispatch: UUID | None = None
    status: RunStatus = RunStatus.QUEUED
    attempt: NonNegativeInt = 0
    next_try_at: AwareDatetime | None = None
    depth: NonNegativeInt = 0
    runner: str | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    output: JsonValue = None
    summary: str = ""
    error: ErrorInfo | None = None
    log: str = ""
    skipped_reason: str | None = None

    @classmethod
    def example(cls) -> Self:
        return cls(
            action_id=UUID("00000000-0000-4000-8000-000000000003"),
            action="file-statement",
            plugin="script",
            event_id=UUID("00000000-0000-4000-8000-000000000004"),
            event_type="custom.hello",
            subscription="bank-mail",
            status=RunStatus.SUCCEEDED,
            attempt=1,
            output={"filed": True},
            log="Filing\n",
        )

    def ended(self) -> bool:
        return self.status.ended
