"""
-------------
- models.py -
-------------

The domain models every database plugin stores: Site, Strategy and DownloadRecord.

They are plain (non-table) SQLModels, so any database plugin can use them; SQL plugins map them
onto tables of their own. Records have UUID ids, so they can be copied between databases.
Strategies and downloads refer to their site by its key, which is the same in every database.
"""

# System Imports
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

# Package Imports
import pydantic
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
)
from sqlmodel import Field, SQLModel

# Local Imports
from udl.errors import ErrorInfo

type JsonObject = dict[str, JsonValue]


def utcnow() -> datetime:
    return datetime.now(UTC)


class StrategySource(StrEnum):
    """How a strategy was learned. Self-healing never replaces a `manual` strategy."""

    PROBE = "probe"  # a probe found it and a download proved it works
    MANUAL = "manual"  # the user set it
    PLUGIN = "plugin"  # a site plugin supplied it
    IMPORT = "import"  # imported from a file or another database


class JobStatus(StrEnum):
    """Where a download is in its life."""

    QUEUED = "queued"
    RUNNING = "running"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _check_regex(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{pattern!r} is not a valid regular expression: {e}") from e
    return pattern


class Site(SQLModel):
    """A website, identified by its key: the registrable domain (eTLD+1), e.g. `example.co.uk`."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    key: str = Field(min_length=1)
    name: str | None = None
    default_output_dir: Path | None = None
    notes: str | None = None
    created_at: AwareDatetime = Field(default_factory=utcnow)
    updated_at: AwareDatetime = Field(default_factory=utcnow)


class Strategy(SQLModel):
    """How to download from a site: a tool (downloader plugin) and its options.

    A site can have several, e.g. a different tool for one section of it (`url_pattern`).
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    site_key: str = Field(min_length=1)
    # A regex matched against the URL's path; None means the whole site.
    url_pattern: Annotated[str, AfterValidator(_check_regex)] | None = None
    tool: str = Field(min_length=1)
    # Validated by the tool's own Options model.
    options: JsonObject = Field(default_factory=dict)
    requires_auth: bool = False
    source: StrategySource
    # Lower is tried first.
    priority: int = 0
    success_count: NonNegativeInt = 0
    failure_count: NonNegativeInt = 0
    consecutive_failures: NonNegativeInt = 0
    last_success_at: AwareDatetime | None = None
    last_error: ErrorInfo | None = None
    enabled: bool = True


def strategy_order(strategy: Strategy) -> tuple[int, int, uuid.UUID]:
    """The order databases return a site's strategies in: priority, then most successes.

    The id breaks ties, so every database plugin returns the same order.
    """
    return (strategy.priority, -strategy.success_count, strategy.id)


class DownloadRecord(SQLModel):
    """One download of one URL: the download history."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    url: str = Field(min_length=1)
    site_key: str = Field(min_length=1)
    # The strategy and tool used, once one is chosen.
    strategy_id: uuid.UUID | None = None
    tool: str | None = None
    status: JobStatus = JobStatus.QUEUED
    output_dir: Path | None = None
    files: list[Path] = Field(default_factory=list)
    size_bytes: NonNegativeInt = 0
    started_at: AwareDatetime = Field(default_factory=utcnow)
    finished_at: AwareDatetime | None = None
    error: ErrorInfo | None = None


def download_order(record: DownloadRecord) -> tuple[datetime, uuid.UUID]:
    """The order databases list downloads in, reversed: newest first, then by id (descending)."""
    return (record.started_at, record.id)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Outcome(_Frozen):
    """The result of one attempt to download with a strategy."""

    ok: bool
    error: ErrorInfo | None = None
    at: AwareDatetime = pydantic.Field(default_factory=utcnow)


def counted(strategy: Strategy, outcome: Outcome) -> Strategy:
    """A copy of `strategy` with `outcome` counted (DatabasePlugin.record_outcome's update).

    Success: success_count += 1, consecutive_failures = 0, last_success_at = outcome.at.
    Failure: failure_count += 1, consecutive_failures += 1, last_error = outcome.error.
    """
    if outcome.ok:
        update = {
            "success_count": strategy.success_count + 1,
            "consecutive_failures": 0,
            "last_success_at": outcome.at,
        }
    else:
        update = {
            "failure_count": strategy.failure_count + 1,
            "consecutive_failures": strategy.consecutive_failures + 1,
            "last_error": outcome.error,
        }
    return strategy.model_copy(update=update)


class _Paged(_Frozen):
    limit: PositiveInt | None = None
    offset: NonNegativeInt = 0

    def page[T](self, items: Sequence[T]) -> list[T]:
        """The page of `items` (already filtered and in order) that this query asks for."""
        end = None if self.limit is None else self.offset + self.limit
        return list(items[self.offset : end])


class SiteQuery(_Paged):
    """Which sites to list. Sites are listed by key."""

    # Case-insensitive text to find in the key or name.
    search: str | None = None


class DownloadQuery(_Paged):
    """Which downloads to list. Downloads are listed newest first."""

    site_key: str | None = None
    status: JobStatus | None = None
