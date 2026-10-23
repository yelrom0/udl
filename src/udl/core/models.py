"""
-------------
- models.py -
-------------

The models the resolution engine and the service hand out: what was tried on a URL, jobs and
their events, and each operation's request and response
"""

# System Imports
from enum import StrEnum
from pathlib import Path
from typing import Self
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt

# Local Imports
from udl.db.models import DownloadRecord, JobStatus
from udl.errors import ErrorInfo
from udl.plugins.downloaders.base import DownloadProgress


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CandidateSource(StrEnum):
    """Why a downloader was tried on a URL."""

    REQUEST = "request"  # the request named it
    PLUGIN = "plugin"  # the site's plugin chose it
    STORED = "stored"  # a stored strategy for the site
    PROBE = "probe"  # the probe chain (or the browser, after a site wanted a login)


class Attempt(_Frozen):
    """One try of a downloader on a URL."""

    tool: str
    source: CandidateSource
    signed_in: bool = False  # whether it sent the site's cookies
    error: ErrorInfo | None = None  # why it failed; None when it worked


# ---------------------------------------------------------------------------------------------
# Jobs: one URL's download each, in a batch per request
# ---------------------------------------------------------------------------------------------


class JobInfo(_Frozen):
    """A download job: where it is, and what came of it. Saved as a DownloadRecord."""

    id: UUID
    batch_id: UUID | None = None  # None for a download from the history, not run by this udl
    url: str
    site_key: str
    status: JobStatus
    tool: str | None = None  # the tool that downloaded it
    handed_off_to: str | None = None  # the tool that tool handed the media to, if any
    strategy_id: UUID | None = None  # the stored strategy it used or learned
    output_dir: Path | None = None
    files: list[Path] = Field(default_factory=list[Path])
    size_bytes: NonNegativeInt = 0
    title: str | None = None
    progress: DownloadProgress | None = None  # the latest, while it runs
    attempts: list[Attempt] = Field(default_factory=list[Attempt])
    error: ErrorInfo | None = None
    started_at: AwareDatetime  # when it was queued, then when it started
    finished_at: AwareDatetime | None = None

    @classmethod
    def from_record(cls, record: DownloadRecord) -> Self:
        """A download from the history."""
        return cls.model_validate(record.model_dump())

    def record(self) -> DownloadRecord:
        """This job as the download history keeps it."""
        return DownloadRecord.model_validate(
            self.model_dump(include=set(DownloadRecord.model_fields))
        )


class JobEventType(StrEnum):
    """What happened to a job. A job's events come in this order: queued, started, any number of
    progress, then one of finished, failed or cancelled (a job cancelled while queued never
    starts)."""

    QUEUED = "queued"
    STARTED = "started"
    PROGRESS = "progress"
    FINISHED = "finished"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def ends(self) -> bool:
        """Whether this is a job's last event."""
        return self in (JobEventType.FINISHED, JobEventType.FAILED, JobEventType.CANCELLED)


class JobEvent(_Frozen):
    """Something that happened to a job, and the job as it is after it."""

    type: JobEventType
    at: AwareDatetime
    job: JobInfo
    tool: str | None = None  # (progress) the tool downloading


class DownloadBatch(_Frozen):
    """The jobs of one download request."""

    id: UUID
    jobs: list[JobInfo]
