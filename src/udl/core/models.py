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
from typing import Annotated, Self
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt, PositiveFloat

# Local Imports
from udl.config import Settings
from udl.core.urls import RequestURL, SiteKey
from udl.db.models import DownloadRecord, JobStatus, JsonObject, Regex, Site, Strategy
from udl.errors import ErrorInfo
from udl.plugins.downloaders.base import (
    DownloadProgress,
    FilenameTemplate,
    ProbeResult,
    Requirement,
)
from udl.plugins.registry import PluginProblem, PluginSource


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


# ---------------------------------------------------------------------------------------------
# The operations' requests and responses
# ---------------------------------------------------------------------------------------------


class DownloadRequest(_Frozen):
    """Download one or more URLs (`udl <url>...`)."""

    urls: Annotated[list[RequestURL], Field(min_length=1)]
    output_dir: Path | None = None  # else the site plugin's, the site's, or download_dir
    tool: str | None = None  # the only downloader to try
    filename_template: FilenameTemplate | None = None  # else the config's
    reprobe: bool = False  # ignore the stored strategies, and probe again
    record: bool = True  # remember what worked, and keep the downloads in the history
    databases: Annotated[list[str], Field(min_length=1)] | None = None  # only these connections
    wait: bool = True  # return once every download has ended
    timeout: PositiveFloat | None = None  # seconds to wait, at most (the downloads go on)


class JobRequest(_Frozen):
    """A download job, or a batch of them, by id."""

    id: UUID


class DownloadList(_Frozen):
    downloads: list[JobInfo]


class ProbeRequest(_Frozen):
    """Find out which downloaders can download a URL, without downloading it."""

    url: RequestURL
    tool: str | None = None  # probe only this downloader
    all_tools: bool = False  # probe every downloader, not just until one can


class ProbeReport(_Frozen):
    url: str  # normalised
    site_key: str
    results: list[ProbeResult]  # in the order they were probed
    signed_in: bool = False  # whether the site's cookies were sent (it wanted a login)


class SiteRequest(_Frozen):
    """A site, given as its key, a host or a URL."""

    site: SiteKey


class SiteList(_Frozen):
    sites: list[Site]


class SiteInfo(_Frozen):
    site: Site
    strategies: list[Strategy]  # in the order databases keep them, disabled ones too


class SetStrategyRequest(_Frozen):
    """Tell udl which downloader to use for a site (or a part of it)."""

    site: SiteKey
    tool: Annotated[str, Field(min_length=1)]
    pattern: Regex | None = None  # a regex searched for in the URL's path; None: the whole site
    options: JsonObject = Field(default_factory=dict)  # the downloader's options
    priority: int = 0  # lower is tried first
    requires_auth: bool = False  # send the site's cookies from the start


class Deleted(_Frozen):
    deleted: bool  # False when there was nothing to delete


class ListDatabasesRequest(_Frozen):
    """List the configured database connections."""


class DatabaseInfo(_Frozen):
    name: str
    plugin: str
    url: str  # any password hidden
    primary: bool
    priority: int
    read_only: bool
    connected: bool
    healthy: bool


class DatabaseList(_Frozen):
    databases: list[DatabaseInfo]


class ListPluginsRequest(_Frozen):
    """List the plugins (databases, downloaders) and whether each can run."""


class PluginStatus(_Frozen):
    kind: str  # e.g. "udl.downloaders"
    name: str
    source: PluginSource
    origin: str
    available: bool | None = None  # (downloaders) whether it can run; None for other plugins
    missing: list[Requirement] = Field(default_factory=list[Requirement])  # what to install
    problem: str | None = None  # why it cannot run, when it is not something to install


class PluginList(_Frozen):
    plugins: list[PluginStatus]
    problems: list[PluginProblem]  # plugins that could not be loaded


class GetConfigRequest(_Frozen):
    """Show the settings udl runs with."""


class ConfigInfo(_Frozen):
    settings: Settings  # secrets hidden
