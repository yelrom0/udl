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
from typing import Annotated, Self
from uuid import UUID

# Package Imports
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    PositiveInt,
    SecretStr,
    field_validator,
)

# Local Imports
from udl.config import PersonMode
from udl.db.models import utcnow
from udl.db.records import Record, RecordScope, SyncRule
from udl.errors import ErrorInfo
from udl.fetch.challenges import ChallengeKind
from udl.fetch.content import FetchedPage, PageFormat
from udl.fetch.cookies import SiteCookies
from udl.fetch.ladder import FetchAttempt, FetchOptions, Via, no_secret_headers, start_rung


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


# ---------------------------------------------------------------------------------------------
# The operations' requests and answers
# ---------------------------------------------------------------------------------------------

_PAGE = 50  # (what a list gives, unless asked for more)
_MOST = 1000  # (what a list gives at most)
CHARS = 20_000
"""Characters of each page an answer gives, unless asked for others."""


class FetchRequest(_Shown):
    """URLs to read the pages of, in one batch."""

    urls: Annotated[list[str], Field(min_length=1)]
    """The URLs (several at once: they are fetched together, their sites taking turns)."""
    via: Via = Via.AUTO
    """Where to start: auto (where the site's pages last worked), static (a plain request),
    cookies (with the site's cookies) or browser (udl's browser)."""
    format: PageFormat = PageFormat.MARKDOWN
    """What the pages are given as: markdown (each page's main content), text, html (as served,
    or as the browser rendered it) or raw (as served)."""
    main_only: bool = True
    """Only each page's main content, without its menus, sidebars and footers."""
    select: str | None = None
    """Only what this CSS selector matches on each page (e.g. `table.prices`), each match
    converted whole."""
    wait_for: str | None = None
    """In the browser, wait for an element matching this CSS selector before reading a page."""
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    """More request headers to send (never Cookie or Authorization: import_cookies adds cookies)."""
    person: PersonMode | None = None
    """At an anti-bot check udl cannot pass: ask (open a window for the user to pass it), wait
    (until someone runs `udl fetch solve`) or fail. By default [fetch] person."""
    label: str | None = None
    """A name for the batch, to find it again by."""
    wait: bool = True
    """Wait for the pages (at most `timeout`); else answer at once, while they are fetched."""
    timeout: Annotated[float, Field(gt=0, le=3600)] = 90.0
    """Seconds to wait for the pages; get_fetch gives those that come later."""
    max_chars: Annotated[int, Field(ge=0)] = CHARS
    """Characters of each page in the answer, from its start; get_fetched_page gives the rest."""

    @field_validator("via")
    @classmethod
    def _a_rung_to_start_on(cls, via: Via) -> Via:
        return start_rung(via)

    @field_validator("headers")
    @classmethod
    def _no_secrets(cls, headers: dict[str, str]) -> dict[str, str]:
        return no_secret_headers(headers)

    def options(self) -> FetchOptions:
        return FetchOptions(
            via=self.via,
            format=self.format,
            main_only=self.main_only,
            select=self.select,
            wait_for=self.wait_for,
            headers=self.headers,
        )


class FetchedItemInfo(_Shown):
    """A URL of a fetch: where it is, and its page."""

    id: UUID
    """The URL's id (get_fetched_page takes it)."""
    url: str
    """The URL."""
    status: ItemStatus
    """queued, running, challenge (waits for a person to pass its site's anti-bot check),
    succeeded, failed, cancelled or interrupted."""
    via: Via | None = None
    """How its page was read: static, cookies, browser, or person (passed by the user)."""
    title: str | None = None
    """Its page's title."""
    final_url: str | None = None
    """Where its page was, after redirects."""
    http_status: int | None = None
    """Its page's HTTP status."""
    content_type: str | None = None
    """Its page's media type."""
    size: NonNegativeInt | None = None
    """Its page's size, in bytes."""
    challenge: ChallengeKind | None = None
    """The anti-bot check it met, if it met one."""
    error: ErrorInfo | None = None
    """Why it failed."""
    note: str | None = None
    """More about it (e.g. why no window opened)."""
    content: str | None = None
    """Its page in the fetch's format, from the start (at most max_chars). None until it is
    read, for a file (a PDF, an image: not converted), or when left out of this answer."""
    next_offset: NonNegativeInt | None = None
    """Where its content goes on, when it was cut (get_fetched_page's offset)."""
    left_out: bool = False
    """Whether its content was left out of this answer, which holds at most [fetch] max_answer
    characters of pages (get_fetched_page gives it)."""


class FetchBatchInfo(_Shown):
    """A fetch (a batch of URLs), each URL's status and page."""

    id: UUID
    """The fetch's id (get_fetch, cancel_fetch, retry_fetch take it)."""
    label: str | None
    """Its label."""
    status: BatchStatus
    """queued, running, waiting (all that is left waits for a person to pass an anti-bot
    check), finished, cancelled or interrupted (the udl that ran it stopped: retry_fetch)."""
    person: PersonMode
    """What it does at an anti-bot check udl cannot pass: ask, wait or fail."""
    format: PageFormat
    """What its pages are given as."""
    counts: dict[ItemStatus, NonNegativeInt]
    """How many of its URLs are in each status."""
    created_at: AwareDatetime
    """When it was asked for."""
    finished_at: AwareDatetime | None
    """When it ended."""
    runner: str | None
    """The udl that runs it."""
    items: list[FetchedItemInfo]
    """Its URLs, in order."""
    hint: str | None = None
    """What to do about the URLs that are not done."""


class FetchBatchSummary(_Shown):
    """A fetch, without its URLs."""

    id: UUID
    """The fetch's id."""
    label: str | None
    """Its label."""
    status: BatchStatus
    """Where it is."""
    counts: dict[ItemStatus, NonNegativeInt]
    """How many of its URLs are in each status."""
    created_at: AwareDatetime
    """When it was asked for."""
    finished_at: AwareDatetime | None
    """When it ended."""


class FetchBatchList(_Shown):
    fetches: list[FetchBatchSummary]
    """The fetches, newest first."""


class ListFetchesRequest(_Shown):
    """Which fetches to list."""

    status: BatchStatus | None = None
    """Only those with this status."""
    since: AwareDatetime | None = None
    """Only those asked for at this time or later."""
    limit: Annotated[PositiveInt, Field(le=_MOST)] = _PAGE
    """At most this many (newest first)."""
    offset: NonNegativeInt = 0
    """Skip this many first."""


class FetchBatchRequest(_Shown):
    """A fetch."""

    id: UUID
    """The fetch's id (fetch_pages gave it)."""
    max_chars: Annotated[int, Field(ge=0)] = CHARS
    """Characters of each page in the answer, from its start; get_fetched_page gives the rest."""


class FetchedPageRequest(_Shown):
    """A page a fetch read, or part of it."""

    id: UUID
    """The URL's id (each URL of fetch_pages' and get_fetch's answers has one)."""
    format: PageFormat | None = None
    """What to give it as (by default, its fetch's format): markdown, text, html or raw."""
    main_only: bool | None = None
    """Only its main content (by default, as its fetch said)."""
    select: str | None = None
    """Only what this CSS selector matches on it (by default, its fetch's select)."""
    offset: NonNegativeInt = 0
    """Where to start, in characters (an answer's next_offset)."""
    max_chars: Annotated[int, Field(ge=0)] = CHARS
    """Characters to give, at most."""


class FetchedPageInfo(_Shown):
    """A page a fetch read: what was fetched, how, and its content (from `offset`)."""

    id: UUID
    """The URL's id."""
    batch: UUID
    """Its fetch's id."""
    url: str
    """The URL."""
    status: ItemStatus
    """Where it is."""
    via: Via | None
    """How its page was read."""
    title: str | None
    """Its page's title."""
    final_url: str | None
    """Where its page was, after redirects."""
    http_status: int | None
    """Its page's HTTP status."""
    content_type: str | None
    """Its page's media type."""
    charset: str | None
    """The charset its page said it was in."""
    size: NonNegativeInt | None
    """Its page's size, in bytes."""
    truncated: bool
    """Whether its page was cut at [fetch] max_page."""
    error: ErrorInfo | None
    """Why it failed."""
    note: str | None
    """More about it."""
    attempts: list[FetchAttempt]
    """Each try, rung by rung: its status, the check it met, what went wrong."""
    format: PageFormat
    """What the content is given as."""
    content: str | None
    """The page in `format`, from `offset` (None: it has no page, or it is a file)."""
    offset: NonNegativeInt
    """Where the content starts, in characters."""
    length: NonNegativeInt | None
    """The whole content's length, in characters."""
    next_offset: NonNegativeInt | None
    """Where the content goes on, when it was cut."""


class RetryFetchRequest(_Shown):
    """A fetch to try again."""

    id: UUID
    """The fetch's id."""
    items: list[UUID] | None = None
    """Only these of its URLs (by id); by default, every one that failed, was cancelled or was
    interrupted."""
    via: Via | None = None
    """Start them at this rung (static, cookies or browser) instead."""

    @field_validator("via")
    @classmethod
    def _a_rung_to_start_on(cls, via: Via | None) -> Via | None:
        return start_rung(via) if via is not None else None


class ListChallengesRequest(_Shown):
    """The anti-bot checks that wait for a person."""


class ChallengeList(_Shown):
    challenges: list[WaitingChallenge]
    """Each site that waits for a person to pass its check, by site."""


class SolveChallengesRequest(_Shown):
    """Which waiting checks to show the person."""

    sites: list[str] | None = None
    """Only these sites' (by default, every site that waits)."""


class ImportCookiesRequest(_Shown):
    """Cookies to put in udl's browser profile."""

    cookies: SecretStr | None = None
    """The cookies: a Netscape cookies.txt's text, a JSON export (a browser extension's, or
    Playwright's storage state), or a Cookie header (name=value; name2=value2, with `site`)."""
    site: str | None = None
    """The site they are for: only its cookies are imported (a Cookie header and from_browser
    need it)."""
    from_browser: str | None = None
    """Copy the site's cookies from the user's own browser instead (e.g. firefox, or
    chrome:Profile 1, as cookies_from_browser names one)."""
    user_agent: str | None = None
    """The User-Agent of the browser they came from: udl's plain requests send it with them (a
    clearance works only with the User-Agent it was given to)."""


class ListCookiesRequest(_Shown):
    """Whose cookies to list."""

    site: str | None = None
    """Only this site's (by default, every site's)."""


class CookieList(_Shown):
    sites: list[SiteCookies]
    """Each site's cookies in udl's browser profile, by site (never their values)."""


class CookiesRequest(_Shown):
    """A site's cookies."""

    site: str
    """The site (a host, a URL or its key)."""
