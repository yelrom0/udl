"""
-------------
- ladder.py -
-------------

The ladder: how the Fetcher reads a page, climbing only as far as it has to.

    static ──► cookies ──► browser ──► (a person: udl.fetch.queue, udl.fetch.window)

- static: a plain request (udl's StaticClient), with a browser's headers and the User-Agent of
  udl's browser, and no cookies;
- cookies: the same, with the site's cookies (the user's browser's, then udl's profile's, where
  imported ones are too) and the User-Agent they were imported with, if one was (SiteJar). It
  is skipped for a site with neither;
- browser: a tab of udl's browser (Tabs), on its profile: it waits for the page to load, then
  for `wait_for` or for its network to go quiet, and the page is the DOM it rendered. A check
  gets `challenge_grace` to clear itself (many JavaScript checks do, in a browser) before it
  needs a person.

After each try: a page is the answer, unless it is a JavaScript shell, which goes straight to the
browser. A NetworkError (a 5xx, a timeout, a dropped connection) is tried once more on the same
rung a second later, then the next rung; AuthRequired (401, 403), a check or a block go to the
next rung at once; RateLimited waits its Retry-After (at most max_retry_after) and tries the
same rung, twice at most; NotFound is the end. In the browser, a block is AuthRequired (with what
may help), and a check a person can pass is the outcome's `challenge`.

The rung that worked is remembered per site (FetchSite): `via: auto` starts there next time,
until relearn_after has passed since it was learned (then at static again, so a site that
stopped blocking is noticed). A rung the caller names is where it starts, whatever was learned.
"""

# System Imports
import http.cookiejar
import logging
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Final, Protocol, Self

# Package Imports
import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeFloat, field_validator

# Local Imports
from udl.clock import Clock
from udl.config import FetchSettings
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import utcnow
from udl.db.records import Record, RecordQuery, RecordScope, SyncRule
from udl.errors import (
    AuthRequired,
    DownloadError,
    ErrorInfo,
    ExtractionFailed,
    NetworkError,
    NotFound,
    RateLimited,
)
from udl.fetch.challenges import ChallengeKind, Detection, detect
from udl.fetch.content import (
    KEPT_HEADERS,
    FetchedPage,
    PageFormat,
    decode,
    html_title,
    is_javascript_shell,
    media_type,
)
from udl.http import USER_AGENT
from udl.plugins.downloaders.base import CancelToken, Cookie, cancellable
from udl.plugins.downloaders.errors import http_status_error, httpx_error

log = logging.getLogger(__name__)

RETRY_AFTER: Final = timedelta(seconds=1)
"""How long a rung waits before trying again after a NetworkError."""
RATE_TRIES: Final = 2
"""How many times a rung waits out a 429 before giving up."""
LOOK_EVERY: Final = timedelta(milliseconds=500)
"""How often the browser looks at a check that may clear itself."""

# What a browser sends with a page it navigates to (the static rungs send them too).
BROWSER_HEADERS: Final = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,"
    "*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
_SECRET_HEADERS: Final = frozenset({"cookie", "authorization", "proxy-authorization"})
_BLOCKED_HINT: Final = (
    "The site refuses udl outright (there is no check to pass). Import your own browser's "
    "cookies for it (`udl browser cookies import`), or try from another network."
)


class Via(StrEnum):
    """A rung of the ladder (or where to start: auto, the rung that worked last)."""

    AUTO = "auto"
    STATIC = "static"
    COOKIES = "cookies"
    BROWSER = "browser"
    PERSON = "person"  # (only what a page came by: a person passed its check)


RUNGS: Final = (Via.STATIC, Via.COOKIES, Via.BROWSER)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class FetchOptions(_Frozen):
    """How to fetch a request's URLs, and how their pages read."""

    via: Via = Via.AUTO
    """The rung to start on: auto (where the site's pages last worked), static, cookies or
    browser."""
    format: PageFormat = PageFormat.MARKDOWN
    """What the pages are given as: markdown, text, html (as served, or as the browser rendered
    it) or raw."""
    main_only: bool = True
    """Only a page's main content (not its menus, sidebars and footers)."""
    select: str | None = None
    """Only what this CSS selector matches on a page, each match converted whole."""
    wait_for: str | None = None
    """In the browser, wait for an element matching this CSS selector before reading the page."""
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    """More request headers (never Cookie or Authorization: import_cookies adds cookies)."""

    @field_validator("via")
    @classmethod
    def _a_rung_to_start_on(cls, via: Via) -> Via:
        if via is Via.PERSON:
            raise ValueError("a fetch starts at auto, static, cookies or browser, not person")
        return via

    @field_validator("headers")
    @classmethod
    def _no_secrets(cls, headers: dict[str, str]) -> dict[str, str]:
        secret = sorted(name for name in headers if name.lower() in _SECRET_HEADERS)
        if secret:
            raise ValueError(
                f"{', '.join(secret)} cannot be sent with a fetch (a batch keeps its headers): "
                "import the cookies with import_cookies (`udl browser cookies import`) instead"
            )
        return headers


class FetchAttempt(_Frozen):
    """One try on a rung of the ladder."""

    rung: str
    """static, cookies, browser or person."""
    status: int | None = None
    """The HTTP status it got, if it got an answer."""
    error: ErrorInfo | None = None
    """What went wrong, if it did."""
    challenge: ChallengeKind | None = None
    """The check it met, if it met one."""
    passable: bool | None = None
    """Whether a person can pass that check (else it is a block)."""
    evidence: list[str] = Field(default_factory=list[str])
    """What told the check (its markers)."""
    note: str | None = None
    """What else it found (e.g. a JavaScript shell)."""
    seconds: NonNegativeFloat = 0.0
    """How long it took."""


class FetchOutcome(BaseModel):
    """What the ladder came to for a URL: its page, its error, or a check that waits for a
    person; and each try."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    page: FetchedPage | None = None
    via: Via | None = None  # the rung that got the page
    error: ErrorInfo | None = None
    challenge: Detection | None = None  # a check a person can pass, met in the browser
    attempts: list[FetchAttempt] = Field(default_factory=list[FetchAttempt])


# ---------------------------------------------------------------------------------------------
# What the ladder remembers of a site
# ---------------------------------------------------------------------------------------------


class FetchSite(Record):
    """What the fetcher knows of a site: the rung that last worked (where `via: auto` starts),
    the User-Agent its cookies were imported with, and its checks."""

    kind = "fetch_site"
    indexed = ("site",)
    unique = (("site",),)
    scope = RecordScope.RUNTIME
    sync = SyncRule.NEVER

    site: str
    start_at: Via | None = None
    learned_at: AwareDatetime | None = None
    user_agent: str | None = None
    last_challenge: ChallengeKind | None = None
    challenged_at: AwareDatetime | None = None
    cleared_at: AwareDatetime | None = None

    @classmethod
    def example(cls) -> Self:
        return cls(
            site="example.com",
            start_at=Via.BROWSER,
            learned_at=utcnow(),
            last_challenge=ChallengeKind.CLOUDFLARE,
        )


class FetchSites:
    """The FetchSite records (on the clock's time)."""

    def __init__(self, db: DBManager, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def get(self, site: str) -> FetchSite | None:
        found = await self._db.list_records(FetchSite, RecordQuery(where={"site": site}, limit=1))
        return found[0] if found else None

    async def update(self, site: str, **changes: Any) -> FetchSite:
        """Change what is known of `site` (made, if nothing was)."""
        known = await self.get(site) or FetchSite(site=site)
        return await self._db.upsert_record(known.model_copy(update=changes))

    def now(self) -> AwareDatetime:
        return self._clock.now()


# ---------------------------------------------------------------------------------------------
# The rungs' tools
# ---------------------------------------------------------------------------------------------


class Snapshot(BaseModel):
    """A page as a rung saw it: where it ended up, its status, its headers (lower case; the
    Set-Cookie lines a line each) and its body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str
    status: int
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    body: bytes = b""
    truncated: bool = False  # cut at max_page
    missing: str | None = None  # the wait_for that never came


class StaticClient(Protocol):
    """What makes the static rungs' plain requests."""

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        jar: http.cookiejar.CookieJar,
        cancel: CancelToken,
    ) -> Snapshot:
        """GET `url` with `headers` and the cookies in `jar` (which keeps what the site sets),
        following redirects. Raises NetworkError (and the like) when no answer comes."""
        ...


class Tab(Protocol):
    """A tab of udl's browser."""

    async def goto(self, url: str, *, wait_for: str | None) -> Snapshot:
        """Load `url`, then wait for `wait_for` (its Snapshot says when it never came), or for
        the network to go quiet. Raises NetworkError (and the like) when it does not load."""
        ...

    async def look(self) -> Snapshot:
        """The page as it is now (it may have changed by itself: a check that cleared)."""
        ...


class Tabs(Protocol):
    """Where the browser rung gets its tabs: udl's browser on its profile."""

    @property
    def user_agent(self) -> str | None:
        """The browser's User-Agent, once it is known."""
        ...

    def tab(
        self, *, headers: Mapping[str, str], cancel: CancelToken
    ) -> AbstractAsyncContextManager[Tab]:
        """A tab that sends `headers` too, for the time a block takes."""
        ...


class SiteJar(Protocol):
    """The cookies the cookies rung sends: the site's, from the user's browser and udl's
    profile; and what it keeps of what sites set."""

    async def cookies_for(self, url: str) -> list[Cookie]:
        """The cookies of url's site."""
        ...

    async def keep(self, url: str, sent: list[Cookie], now: list[Cookie]) -> None:
        """Keep what changed between the cookies sent for `url` and those after its answer."""
        ...


class HttpStatic:
    """The static rungs' requests, over httpx: at most `max_page` of each body is read."""

    def __init__(self, settings: FetchSettings) -> None:
        self._settings = settings

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        jar: http.cookiejar.CookieJar,
        cancel: CancelToken,
    ) -> Snapshot:
        return await cancellable(self._get(url, headers, jar), cancel)

    async def _get(
        self, url: str, headers: Mapping[str, str], jar: http.cookiejar.CookieJar
    ) -> Snapshot:
        limit = self._settings.max_page
        try:
            async with (
                httpx.AsyncClient(
                    headers=dict(headers),
                    cookies=jar,  # (shared: what the site sets is kept in it)
                    follow_redirects=True,
                    timeout=self._settings.timeout.total_seconds(),
                ) as client,
                client.stream("GET", url) as response,
            ):
                body = bytearray()
                truncated = False
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > limit:
                        del body[limit:]
                        truncated = True
                        break
                return Snapshot(
                    url=str(response.url),
                    status=response.status_code,
                    headers={
                        name: "\n".join(response.headers.get_list(name))
                        for name in response.headers
                    },
                    body=bytes(body),
                    truncated=truncated,
                )
        except httpx.HTTPError as e:
            raise httpx_error(e, tool="static") from e


# ---------------------------------------------------------------------------------------------
# The Fetcher
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Page:
    page: FetchedPage


@dataclass(frozen=True)
class _Check:
    detection: Detection
    status: int


@dataclass(frozen=True)
class _Failed:
    error: DownloadError
    status: int | None = None  # (an HTTP error's)


type _Seen = _Page | _Check | _Failed


@dataclass(frozen=True)
class _Climb:
    """Go on up (to the browser, from a JavaScript shell), having failed with `error`."""

    error: DownloadError
    to: Via | None = None


@dataclass(frozen=True)
class _End:
    """The ladder's end: a page, a check for a person, or an error."""

    page: FetchedPage | None = None
    challenge: Detection | None = None
    error: DownloadError | None = None


class Fetcher:
    """Reads pages up the ladder (see the module's docs)."""

    def __init__(
        self,
        settings: FetchSettings,
        *,
        static: StaticClient,
        jar: SiteJar,
        tabs: Tabs,
        sites: FetchSites,
        clock: Clock,
    ) -> None:
        self.settings = settings
        self.static = static
        self.jar = jar
        self.tabs = tabs
        self.sites = sites
        self.clock = clock

    async def fetch(self, url: URL, options: FetchOptions, *, cancel: CancelToken) -> FetchOutcome:
        """Read `url`'s page, climbing the ladder as far as it has to. Raises Cancelled."""
        known = await self.sites.get(url.site_key)
        start, relearning = self._start(known, options.via)
        attempts: list[FetchAttempt] = []
        at = RUNGS.index(start)
        error: DownloadError | None = None
        end: _End | None = None
        while end is None and at < len(RUNGS):
            rung = RUNGS[at]
            if rung is Via.COOKIES and not await self._has_cookies(url, known):
                at += 1
                continue
            step = await self._on(rung, url, options, known, attempts, cancel)
            if isinstance(step, _End):
                end = step
            else:
                error = step.error
                at = RUNGS.index(step.to) if step.to is not None else at + 1
        end = end or _End(error=error)
        via = Via(end.page.via) if end.page is not None else None
        await self._remember(url.site_key, known, attempts, via, start, options.via, relearning)
        return FetchOutcome(
            url=url.value,
            page=end.page,
            via=via,
            error=end.error.to_info() if end.error is not None else None,
            challenge=end.challenge,
            attempts=attempts,
        )

    async def _remember(
        self,
        site: str,
        known: FetchSite | None,
        attempts: list[FetchAttempt],
        via: Via | None,
        start: Via,
        asked: Via,
        relearning: bool,
    ) -> None:
        """Keep the last check the site showed, and the rung its page came by: where `auto`
        starts next. (A rung the caller named teaches nothing unless the page came higher.)"""
        changes: dict[str, Any] = {}
        checks = [attempt.challenge for attempt in attempts if attempt.challenge is not None]
        if checks:
            changes |= {"last_challenge": checks[-1], "challenged_at": self.clock.now()}
        taught = via is not None and (asked is Via.AUTO or via is not start)
        if taught and (relearning or known is None or known.start_at is not via):
            changes |= {"start_at": via, "learned_at": self.clock.now()}
        if changes:
            await self.sites.update(site, **changes)

    def _start(self, known: FetchSite | None, via: Via) -> tuple[Via, bool]:
        """The rung to start on, and whether it is the bottom one again (relearning)."""
        if via is not Via.AUTO:
            return via, False
        if known is None or known.start_at is None or known.learned_at is None:
            return Via.STATIC, False
        if self.clock.now() - known.learned_at >= self.settings.relearn_after:
            return Via.STATIC, True
        return known.start_at, False

    async def _has_cookies(self, url: URL, known: FetchSite | None) -> bool:
        if known is not None and known.user_agent:
            return True
        return bool(await self.jar.cookies_for(url.value))

    async def _on(
        self,
        rung: Via,
        url: URL,
        options: FetchOptions,
        known: FetchSite | None,
        attempts: list[FetchAttempt],
        cancel: CancelToken,
    ) -> _Climb | _End:
        """Try `rung` (again, as its rules say) until it gives an answer or gives up."""
        retried = False
        waited = 0
        while True:
            cancel.check()
            started = self.clock.now()
            seen = await self._try(rung, url, options, known, cancel)
            took = (self.clock.now() - started).total_seconds()
            attempts.append(_attempt(rung, seen, took))
            match seen:
                case _Page(page=page):
                    if rung is not Via.BROWSER and is_javascript_shell(page):
                        attempts[-1] = attempts[-1].model_copy(
                            update={"note": "a JavaScript shell"}
                        )
                        shell = ExtractionFailed(f"{url.value} is built by JavaScript", tool=rung)
                        return _Climb(shell, to=Via.BROWSER)
                    return _End(page=page)
                case _Check(detection=found):
                    if rung is not Via.BROWSER:
                        return _Climb(_challenge_error(url, found, rung))
                    if found.passable:
                        return _End(challenge=found)
                    return _End(error=_blocked(url, found))
                case _Failed(error=NotFound() as error):
                    return _End(error=error)
                case _Failed(error=NetworkError()) if not retried:
                    retried = True
                    await self.clock.sleep(RETRY_AFTER)
                case _Failed(error=RateLimited() as error) if waited < RATE_TRIES:
                    waited += 1
                    await self.clock.sleep(self._retry_after(error))
                case _Failed(error=RateLimited() as error):
                    return _End(error=error)
                case _Failed(error=error):
                    return _Climb(error)

    def _retry_after(self, error: RateLimited) -> timedelta:
        wait = timedelta(seconds=error.retry_after if error.retry_after is not None else 1.0)
        return min(wait, self.settings.max_retry_after)

    async def _try(
        self,
        rung: Via,
        url: URL,
        options: FetchOptions,
        known: FetchSite | None,
        cancel: CancelToken,
    ) -> _Seen:
        """What one try on `rung` saw: the page, a check, or an error."""
        try:
            if rung is Via.BROWSER:
                return await self._browser(url, options, cancel)
            user_agent = self.tabs.user_agent or USER_AGENT
            jar = http.cookiejar.CookieJar()
            sent: list[Cookie] = []
            if rung is Via.COOKIES:
                sent = await self.jar.cookies_for(url.value)
                for cookie in sent:
                    jar.set_cookie(cookie.to_http_cookie())
                if known is not None and known.user_agent:
                    user_agent = known.user_agent
            headers = {**BROWSER_HEADERS, "User-Agent": user_agent, **options.headers}
            try:
                seen = await self.static.get(url.value, headers=headers, jar=jar, cancel=cancel)
            finally:
                if rung is Via.COOKIES:
                    await self.jar.keep(url.value, sent, [Cookie.from_http_cookie(c) for c in jar])
            return _read(url, seen, rung)
        except DownloadError as e:
            if e.tool is None:
                e.tool = rung.value
            return _Failed(e)

    async def _browser(self, url: URL, options: FetchOptions, cancel: CancelToken) -> _Seen:
        async with self.tabs.tab(headers=options.headers, cancel=cancel) as tab:
            seen = _read(url, await tab.goto(url.value, wait_for=options.wait_for), Via.BROWSER)
            deadline = self.clock.now() + self.settings.challenge_grace
            # (A check that a script in it passes: many do, in a browser.)
            while isinstance(seen, _Check) and seen.detection.passable:
                if self.clock.now() >= deadline:
                    break
                cancel.check()
                await self.clock.sleep(LOOK_EVERY)
                seen = _read(url, await tab.look(), Via.BROWSER)
            return seen


def _read(url: URL, seen: Snapshot, rung: Via) -> _Seen:
    """What a rung's Snapshot is: a page, a check (or block), or an HTTP error."""
    kind, charset = media_type(seen.headers.get("content-type"))
    page = FetchedPage(
        url=url.value,
        final_url=seen.url,
        status=seen.status,
        content_type=kind,
        charset=charset,
        headers={name: seen.headers[name] for name in KEPT_HEADERS if name in seen.headers},
        via=rung.value,
        body=seen.body,
        truncated=seen.truncated,
    )
    text = decode(seen.body, charset, html=page.is_html)[0] if page.is_text else ""
    if found := detect(seen.status, seen.headers, text, seen.url):
        return _Check(found, seen.status)
    error = http_status_error(
        seen.status, url.value, tool=rung.value, retry_after=seen.headers.get("retry-after")
    )
    if error is not None:
        return _Failed(error, seen.status)
    if seen.missing is not None:
        missing = f"No element matching {seen.missing!r} came on {seen.url}"
        return _Failed(ExtractionFailed(missing, tool=rung.value), seen.status)
    title = html_title(text) if page.is_html else None
    return _Page(page.model_copy(update={"title": title}))


def _attempt(rung: Via, seen: _Seen, seconds: float) -> FetchAttempt:
    match seen:
        case _Page(page=page):
            return FetchAttempt(rung=rung, status=page.status, seconds=seconds)
        case _Check(detection=found, status=code):
            return FetchAttempt(
                rung=rung,
                status=code,
                challenge=found.kind,
                passable=found.passable,
                evidence=found.evidence,
                seconds=seconds,
            )
        case _Failed(error=error, status=code):
            return FetchAttempt(rung=rung, status=code, error=error.to_info(), seconds=seconds)


def _challenge_error(url: URL, found: Detection, rung: Via) -> AuthRequired:
    what = "check" if found.passable else "block"
    return AuthRequired(f"{url.value} answered with a {found.kind} {what}", tool=rung.value)


def _blocked(url: URL, found: Detection) -> AuthRequired:
    return AuthRequired(
        f"{url.site_key} blocks udl ({found.kind}): there is no check to pass",
        tool=Via.BROWSER.value,
        hint=_BLOCKED_HINT,
    )
