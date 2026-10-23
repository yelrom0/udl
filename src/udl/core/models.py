"""
-------------
- models.py -
-------------

The models the resolution engine and the service hand out: what was tried on a URL, jobs and
their events, and each operation's request and response.

The operations' models are what the API and the MCP server show, so their fields are described
by the docstrings under them (the API's docs, and what an AI agent reads about a tool).
"""

# System Imports
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self
from uuid import UUID

# Package Imports
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveFloat,
    model_validator,
)

# Local Imports
from udl.chains.models import ChainInput, FetchMode, Regex, SiteChain, StepTrace, TagChain
from udl.chains.suggest import ChainSuggestion
from udl.config import Settings
from udl.core.urls import RequestURL, SiteKey
from udl.db.models import DownloadRecord, JobStatus, JsonObject, Site, Strategy
from udl.db.sync import SyncResult
from udl.errors import ErrorInfo
from udl.plugins.downloaders.base import (
    DownloadProgress,
    FilenameTemplate,
    MediaCandidate,
    ProbeResult,
)
from udl.plugins.registry import PluginProblem, PluginSource
from udl.plugins.requirements import Requirement


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


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
    signed_in: bool = False
    """Whether it sent the site's cookies."""
    error: ErrorInfo | None = None
    """Why it failed; None when it worked."""


# ---------------------------------------------------------------------------------------------
# Jobs: one URL's download each, in a batch per request
# ---------------------------------------------------------------------------------------------


class JobInfo(_Frozen):
    """A download job: where it is, and what came of it. Saved as a DownloadRecord."""

    id: UUID
    batch_id: UUID | None = None
    """The download request it is part of; None for a download from the history that this udl
    did not run."""
    url: str
    site_key: str
    status: JobStatus
    tool: str | None = None
    """The downloader that downloaded it."""
    handed_off_to: str | None = None
    """The downloader that `tool` handed the media to, if any."""
    strategy_id: UUID | None = None
    """The stored strategy it used or learned."""
    output_dir: Path | None = None
    files: list[Path] = Field(default_factory=list[Path])
    size_bytes: NonNegativeInt = 0
    title: str | None = None
    progress: DownloadProgress | None = None
    """The latest progress, while it runs."""
    attempts: list[Attempt] = Field(default_factory=list[Attempt])
    """Every try of a downloader, in order."""
    error: ErrorInfo | None = None
    started_at: AwareDatetime
    """When it was queued, then when it started."""
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
    tool: str | None = None
    """(progress) The downloader downloading."""


class DownloadBatch(_Frozen):
    """The jobs of one download request."""

    id: UUID
    """The batch's id: get its jobs' events, or cancel them all, with it."""
    jobs: list[JobInfo]
    """One per URL, in the request's order (a site plugin may have turned a URL into
    several)."""


# ---------------------------------------------------------------------------------------------
# The operations' requests and responses
# ---------------------------------------------------------------------------------------------


class DownloadSpec(_Frozen):
    """What to download, and how."""

    urls: Annotated[list[RequestURL], Field(min_length=1)]
    """The URLs to download (http or https; one written without a scheme gets https://)."""
    output_dir: Path | None = None
    """The folder to download into; by default the site plugin's, the site's, or the config's
    download_dir."""
    tool: str | None = None
    """The only downloader to try (e.g. "yt-dlp" or "http"); by default udl works it out."""
    filename_template: FilenameTemplate | None = None
    """Where each file goes in the folder, e.g. "{site}/{title}.{ext}"; by default the
    config's."""
    reprobe: bool = False
    """Ignore what worked on the site before, and try the downloaders again."""
    record: bool = True
    """Remember what worked for next time, and keep the downloads in the history."""
    databases: Annotated[list[str], Field(min_length=1)] | None = None
    """Use only these database connections (by name), for what is known and what is learned;
    by default every configured one."""


class DownloadRequest(DownloadSpec):
    """Download one or more URLs (`udl <url>...`)."""

    wait: bool = True
    """Return once every download has ended; else at once, with the jobs queued."""
    timeout: PositiveFloat | None = None
    """Seconds to wait at most; the downloads go on after that."""


class JobRequest(_Frozen):
    """A download job, or a batch of them, by id."""

    id: UUID
    """A download job's id (or, to cancel every download of a request, its batch id)."""


class DownloadList(_Frozen):
    downloads: list[JobInfo]


class ProbeRequest(_Frozen):
    """Find out which downloaders can download a URL, without downloading it."""

    url: RequestURL
    """The URL to probe."""
    tool: str | None = None
    """Probe only this downloader."""
    all_tools: bool = False
    """Probe every downloader, not just until one can download the URL."""


class ProbeReport(_Frozen):
    url: str
    """The URL, normalised."""
    site_key: str
    results: list[ProbeResult]
    """What each downloader found, in the order they were probed."""
    signed_in: bool = False
    """Whether the site's cookies were sent (it wanted a login)."""


class SiteRequest(_Frozen):
    """A site, given as its key, a host or a URL."""

    site: SiteKey
    """The site: its key (e.g. example.com), a host (www.example.com) or a URL on it."""


class SiteList(_Frozen):
    sites: list[Site]


class SiteInfo(_Frozen):
    site: Site
    strategies: list[Strategy]
    """In the order the databases keep them, disabled ones too."""


class StrategySpec(_Frozen):
    """A downloader (and its options) to use for a site, or for a part of it."""

    tool: Annotated[str, Field(min_length=1)]
    """The downloader: a downloader plugin's name, e.g. "yt-dlp"."""
    pattern: Regex | None = None
    """Use it only for the site's URLs whose path this regex is found in (e.g. "^/shorts/");
    by default for the whole site."""
    options: JsonObject = Field(default_factory=dict)
    """The downloader's options, as in its [tools.<name>] config table."""
    priority: int = 0
    """Lower is tried first, among the site's strategies."""
    requires_auth: bool = False
    """Send the site's cookies from the first try."""


class SetStrategyRequest(StrategySpec):
    """Tell udl which downloader to use for a site (or a part of it)."""

    site: SiteKey
    """The site: its key (e.g. example.com), a host (www.example.com) or a URL on it."""


class Deleted(_Frozen):
    deleted: bool
    """False when there was nothing to delete."""


# ---------------------------------------------------------------------------------------------
# Tag chains
# ---------------------------------------------------------------------------------------------


class SuggestChainRequest(_Frozen):
    """Suggest tag chains for a page's media."""

    url: RequestURL
    """The page."""
    fetch: FetchMode | None = None
    """How to read the page: static (its HTML as served) or browser (rendered in Chromium, with
    the media it requests). By default static, then the browser when that finds nothing."""
    limit: Annotated[int, Field(ge=1, le=50)] = 10
    """Suggest at most this many."""


class ChainSuggestions(_Frozen):
    url: str
    """The page, normalised."""
    site_key: str
    fetch: FetchMode
    """How the page was read."""
    suggestions: list[ChainSuggestion]
    """Best first: media that answered as media, video before audio, the largest first."""
    signed_in: bool = False
    """Whether the site's cookies were sent (it wanted a login)."""
    error: ErrorInfo | None = None
    """Why the page could not be read, when it could not."""


class ChainTestRequest(_Frozen):
    """Try a tag chain on a page."""

    url: RequestURL
    """The page."""
    chain: ChainInput
    """The tag chain: its one-line shorthand, e.g. "iframe#player@src >> video source@src", or
    a chain (fetch, headers, steps, handoff)."""


class ChainReport(_Frozen):
    url: str
    """The page, normalised."""
    site_key: str
    chain: TagChain
    ok: bool
    """Whether the chain led to media."""
    steps: list[StepTrace]
    """What each step found, as far as the chain got."""
    media: list[MediaCandidate] = Field(default_factory=list[MediaCandidate])
    """The media it led to (with its type and size, when its server said)."""
    title: str | None = None
    """The page's title."""
    signed_in: bool = False
    """Whether the site's cookies were sent (it wanted a login)."""
    error: ErrorInfo | None = None
    """Why the chain did not lead to media, when it did not."""


class ChainSpec(_Frozen):
    """A tag chain to use for a site, or for a part of it."""

    chain: ChainInput
    """The tag chain: its one-line shorthand, e.g. "iframe#player@src >> video source@src", or
    a chain (fetch, headers, steps, handoff)."""
    pattern: Regex | None = None
    """Use it only for the site's URLs whose path this regex is found in (e.g. "^/watch/");
    by default for the whole site."""
    priority: int = 0
    """Lower is tried first, among the site's strategies."""
    requires_auth: bool = False
    """Send the site's cookies from the first try."""


class SetChainRequest(ChainSpec):
    """Save a tag chain for a site (or a part of it)."""

    site: SiteKey
    """The site: its key (e.g. example.com), a host (www.example.com) or a URL on it."""


class ImportChainsRequest(_Frozen):
    """Save the tag chains in a chain file."""

    yaml: str
    """A chain file's text (YAML): a chain a document (`---` between them), each with its site,
    as export_chain gives them."""


class ImportedChains(_Frozen):
    strategies: list[Strategy]
    """The strategies the chains were saved as, in the file's order."""


class ChainFile(_Frozen):
    site: str
    chains: list[SiteChain]
    """The site's chains, in the order the databases keep them (by priority)."""
    yaml: str
    """The same as a chain file (YAML), which import_chains (and `udl chain import`) reads."""


# ---------------------------------------------------------------------------------------------
# udl itself
# ---------------------------------------------------------------------------------------------


class ListDatabasesRequest(_Frozen):
    """List the configured database connections."""


class DatabaseInfo(_Frozen):
    name: str
    plugin: str
    url: str
    """Any password in it is hidden."""
    primary: bool
    priority: int
    read_only: bool
    connected: bool
    healthy: bool


class DatabaseList(_Frozen):
    databases: list[DatabaseInfo]


class SyncDatabasesRequest(_Frozen):
    """Copy what one database connection knows into others."""

    source: str
    """The connection to copy from: its name, as list_databases gives it."""
    targets: Annotated[list[str], Field(min_length=1)]
    """The connections to copy into (not read-only ones)."""
    dry_run: bool = False
    """Only count what would change, and change nothing."""

    @model_validator(mode="after")
    def _check_targets(self) -> Self:
        if self.source in self.targets:
            raise ValueError(f"{self.source!r} cannot be synced into itself")
        if len(set(self.targets)) < len(self.targets):
            raise ValueError("a connection is named more than once in targets")
        return self


class SyncReport(_Frozen):
    source: str
    dry_run: bool
    """Whether the sync only counted what would change."""
    targets: list[SyncResult]
    """What changed (or would) in each connection synced into, in turn."""


class ListPluginsRequest(_Frozen):
    """List the plugins (databases, downloaders, sites) and whether each can run."""


class PluginStatus(_Frozen):
    kind: str
    """The plugin's kind (its entry point group): "udl.databases", "udl.downloaders" or
    "udl.sites"."""
    name: str
    source: PluginSource
    """builtin (udl's own), installed (another package's) or local (a file in a plugin dir)."""
    origin: str
    """Where it is: its module and class, or its file."""
    overrides: str | None = None
    """The origin of the plugin of the same name it replaced, if any."""
    available: bool
    """Whether it can run: what it needs is installed (and it is configured and starts)."""
    missing: list[Requirement] = Field(default_factory=list[Requirement])
    """What to install for it to run."""
    problem: str | None = None
    """Why it cannot run, when it is not something to install."""
    domains: list[str] = Field(default_factory=list[str])
    """(Site plugins) The sites it handles, and their subdomains."""


class PluginList(_Frozen):
    plugins: list[PluginStatus]
    problems: list[PluginProblem]
    """The plugins that could not be loaded."""
    plugin_dirs: list[Path]
    """The folders local plugins are read from: each is a single .py file there."""


class GetConfigRequest(_Frozen):
    """Show the settings udl runs with."""


class ConfigInfo(_Frozen):
    settings: Settings
    """The settings, with secrets hidden."""
