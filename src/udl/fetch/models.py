"""
-------------
- models.py -
-------------

The page fetcher's records (FetchBatch: a request's URLs, queued together; FetchItem: one URL's
fetch) and its operations' requests and answers. They are what the API and the MCP server show,
so their fields are described by the docstrings under them (what an AI agent reads about a
tool).
"""

# System Imports
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Self
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt

# Local Imports
from udl.config import PersonMode
from udl.db.models import utcnow
from udl.db.records import Record, RecordScope, SyncRule
from udl.errors import ErrorInfo
from udl.fetch.challenges import ChallengeKind
from udl.fetch.content import FetchedPage
from udl.fetch.ladder import FetchAttempt, FetchOptions, Via


class BatchStatus(StrEnum):
    """Where a batch is."""

    QUEUED = "queued"  # nothing of it has started
    RUNNING = "running"
    WAITING = "waiting"  # all that is left waits for a person to pass a check
    FINISHED = "finished"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # the udl that ran it stopped first: retry_fetch runs the rest

    @property
    def ended(self) -> bool:
        return self in (BatchStatus.FINISHED, BatchStatus.CANCELLED, BatchStatus.INTERRUPTED)


class ItemStatus(StrEnum):
    """Where a URL of a batch is."""

    QUEUED = "queued"
    RUNNING = "running"
    CHALLENGE = "challenge"  # waits for a person to pass its site's check
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def ended(self) -> bool:
        return self in _ENDED


_ENDED = frozenset(
    {ItemStatus.SUCCEEDED, ItemStatus.FAILED, ItemStatus.CANCELLED, ItemStatus.INTERRUPTED}
)


class FetchBatch(Record):
    """A request's URLs, fetched together, in the udl that was asked (its `runner`)."""

    kind = "fetch_batch"
    indexed = ("status", "runner")
    scope = RecordScope.RUNTIME
    sync = SyncRule.NEVER

    label: str | None = None
    options: FetchOptions = Field(default_factory=FetchOptions)
    person: PersonMode = PersonMode.ASK
    status: BatchStatus = BatchStatus.QUEUED
    counts: dict[ItemStatus, NonNegativeInt] = Field(
        default_factory=dict[ItemStatus, NonNegativeInt]
    )
    runner: str | None = None
    finished_at: AwareDatetime | None = None

    @classmethod
    def example(cls) -> Self:
        return cls(
            label="docs",
            options=FetchOptions(via=Via.BROWSER, select="main"),
            status=BatchStatus.WAITING,
            counts={ItemStatus.SUCCEEDED: 2, ItemStatus.CHALLENGE: 1},
            runner="host:1:abc",
        )


class FetchItem(Record):
    """One URL of a batch: where its fetch is, the rung that got its page, each try, and its
    page's body (a file in [fetch] store_dir)."""

    kind = "fetch_item"
    indexed = ("batch_id", "site", "status")
    scope = RecordScope.RUNTIME
    sync = SyncRule.NEVER

    batch_id: UUID
    url: str
    site: str
    position: NonNegativeInt = 0
    status: ItemStatus = ItemStatus.QUEUED
    via: Via | None = None
    attempts: list[FetchAttempt] = Field(default_factory=list[FetchAttempt])
    challenge: ChallengeKind | None = None
    challenged_at: AwareDatetime | None = None
    error: ErrorInfo | None = None
    title: str | None = None
    final_url: str | None = None
    http_status: int | None = None
    content_type: str | None = None
    charset: str | None = None
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    size: NonNegativeInt | None = None
    truncated: bool = False
    body: str | None = None
    note: str | None = None
    finished_at: AwareDatetime | None = None

    @classmethod
    def example(cls) -> Self:
        return cls(
            batch_id=UUID("00000000-0000-4000-8000-000000000005"),
            url="https://example.com/a",
            site="example.com",
            position=1,
            status=ItemStatus.SUCCEEDED,
            via=Via.BROWSER,
            attempts=[FetchAttempt(rung="static", status=403, challenge=ChallengeKind.CLOUDFLARE)],
            title="A",
            final_url="https://example.com/a",
            http_status=200,
            content_type="text/html",
            size=1024,
            body="fetched/00000000-0000-4000-8000-000000000005/x",
            finished_at=utcnow(),
        )


# ---------------------------------------------------------------------------------------------
# Checks that wait for a person
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WaitingSite:
    """A site whose URLs wait for a person to pass its check: the waiting URL its tab opens on
    (`item`, whose page passing it gives), and the check's kind."""

    site: str
    url: str
    item: UUID
    kind: ChallengeKind | None = None


type Passed = Callable[[WaitingSite, FetchedPage], Awaitable[None]]
"""What is done with a site's page once the person passed its check (FetchQueue.passed)."""


class _Shown(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class WaitingChallenge(_Shown):
    """A site whose URLs wait for a person to pass its anti-bot check."""

    site: str
    """The site."""
    kind: ChallengeKind | None
    """Whose check it is (cloudflare, recaptcha...)."""
    urls: list[str]
    """The URLs that wait for it."""
    batches: list[UUID]
    """The fetches they are of."""
    since: AwareDatetime | None
    """Since when they have waited."""


class SolveReport(_Shown):
    """What came of showing the checks to the person."""

    passed: list[str]
    """The sites whose checks were passed: their URLs go on."""
    waiting: list[str]
    """The sites still waiting (the window closed, or the time was up)."""
