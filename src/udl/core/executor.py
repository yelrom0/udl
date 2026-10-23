"""
---------------
- executor.py -
---------------

The executor: downloads a URL by trying its candidates (from the resolver) in turn until one
actually downloads it (real, non-empty files inside the output dir: Downloader.download checks).

How it reacts to a failed try (the error taxonomy in planning.md):

- UnsupportedURL, ExtractionFailed, any other DownloadError, a config or plugin problem: the next
  candidate.
- AuthRequired: the same candidate again, with the site's cookies (read once per URL); then the
  browser downloader, if it has not been tried; then the next candidates, all with the cookies.
- RateLimited, NetworkError: the same candidate again after a wait (the Retry-After the site
  asked for, else a backoff that doubles), RetryPolicy.retries times; then the next candidate.
- NotFound: stop. The content is gone, not the strategy.
- Cancelled: stop.

A tool the request named is the only candidate: its own failure is the error, and it gets no
browser fallback. When every candidate fails, the error is the most useful one seen (a login, a
rate limit, a network problem), else NoStrategyFound.
"""

# System Imports
import asyncio
import logging
from collections.abc import Callable
from contextlib import aclosing
from pathlib import Path
from typing import Protocol

# Package Imports
from pydantic import BaseModel, ConfigDict, NonNegativeFloat, NonNegativeInt

# Local Imports
from udl.config import Settings
from udl.core.models import Attempt, CandidateSource
from udl.core.output import filename_template, resolve_output_dir
from udl.core.resolver import Candidate, Resolver
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.errors import (
    AuthRequired,
    Cancelled,
    DatabaseError,
    NetworkError,
    NoStrategyFound,
    NotFound,
    RateLimited,
    UDLError,
)
from udl.plugins.downloaders.base import (
    CancelToken,
    Cookie,
    DownloadContext,
    DownloadProgress,
    DownloadResult,
    FilenameTemplate,
    cancellable,
)

log = logging.getLogger(__name__)

_BROWSER = "browser"


class CookieSource(Protocol):
    """Where the cookies that sign in to a site come from (AuthCookies)."""

    async def for_url(self, url: str) -> list[Cookie]: ...


type ToolProgress = Callable[[str, DownloadProgress], None]
"""Receives a download's progress, with the tool downloading."""


class RetryPolicy(BaseModel):
    """How a tool is retried after a network error or a rate limit."""

    model_config = ConfigDict(frozen=True)

    retries: NonNegativeInt = 2  # tries after the first
    backoff: NonNegativeFloat = 1.0  # seconds before the first retry; it doubles each time
    max_wait: NonNegativeFloat = 60.0  # the longest wait; a longer Retry-After moves on instead


class JobSpec(BaseModel):
    """What to download, and how the request wants it done."""

    model_config = ConfigDict(frozen=True)

    url: URL
    tool: str | None = None  # the only tool to try
    reprobe: bool = False  # ignore the stored strategies
    output_dir: Path | None = None
    filename_template: FilenameTemplate | None = None


class Execution(BaseModel):
    """A download that worked: what it wrote, and how."""

    model_config = ConfigDict(frozen=True)

    candidate: Candidate  # the one that worked
    result: DownloadResult
    signed_in: bool  # whether it took the site's cookies
    output_dir: Path
    attempts: list[Attempt]  # every try, the successful one last


def _ignore(*_: object) -> None:
    pass


class Executor:
    """Downloads URLs, trying their candidates until one works."""

    def __init__(
        self,
        settings: Settings,
        resolver: Resolver,
        toolbox: Toolbox,
        *,
        cookies: CookieSource,
        retry: RetryPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.toolbox = toolbox
        self.cookies = cookies
        self.retry = retry or RetryPolicy()

    async def run(
        self,
        job: JobSpec,
        *,
        db: DBManager,
        cancel: CancelToken,
        progress: ToolProgress = _ignore,
        on_attempt: Callable[[Attempt], None] = _ignore,
    ) -> Execution:
        """Download job.url. Raises the error that stopped it (see the module docs), or
        Cancelled. `on_attempt` hears of every try as it ends."""
        cancel.check()
        return await _Run(self, job, db, cancel, progress, on_attempt).execute()


class _Run:
    """One URL's download: the state its tries share."""

    def __init__(
        self,
        executor: Executor,
        job: JobSpec,
        db: DBManager,
        cancel: CancelToken,
        progress: ToolProgress,
        on_attempt: Callable[[Attempt], None],
    ) -> None:
        self.x = executor
        self.job = job
        self.url = job.url
        self.db = db
        self.cancel = cancel
        self.progress = progress
        self.on_attempt = on_attempt
        self.attempts: list[Attempt] = []
        self.errors: list[UDLError] = []
        self.tried: set[tuple[str, str]] = set()
        self.needs_auth = False  # the site wanted a login
        self._cookies: list[Cookie] | None = None  # None until read

    async def execute(self) -> Execution:
        self.output_dir = await self._output_dir()
        settings = self.x.settings
        self.template = filename_template(
            self.job.filename_template or settings.filename_template, self.url.site_key
        )
        candidates = self.x.resolver.candidates(
            self.url, db=self.db, tool=self.job.tool, reprobe=self.job.reprobe
        )
        async with aclosing(candidates):
            async for candidate in candidates:
                if candidate.key in self.tried:
                    continue
                outcome = await self._candidate(candidate)
                if isinstance(outcome, AuthRequired) and (fallback := self._browser(candidate)):
                    outcome = await self._candidate(fallback)
                if isinstance(outcome, Execution):
                    return outcome
                if isinstance(outcome, NotFound) or candidate.source is CandidateSource.REQUEST:
                    raise outcome
        raise self._failure()

    async def _candidate(self, candidate: Candidate) -> Execution | UDLError:
        """Try a candidate (with its retries). Returns how it went."""
        self.tried.add(candidate.key)
        outcome = await self._try(candidate)
        if isinstance(outcome, UDLError):
            return outcome
        result, signed_in = outcome
        return Execution(
            candidate=candidate,
            result=result,
            signed_in=signed_in,
            output_dir=self.output_dir,
            attempts=list(self.attempts),
        )

    async def _try(self, candidate: Candidate) -> tuple[DownloadResult, bool] | UDLError:
        """Download with a candidate, again with the site's cookies when it wants a login and
        again after a wait on a network error or rate limit. Returns the result and whether it
        was signed in, or the error it ended on."""
        wants_cookies = self.needs_auth or candidate.requires_auth
        retries = 0
        while True:
            cookies = await self._site_cookies() if wants_cookies else []
            try:
                result = await self._download(candidate, cookies)
            except UDLError as e:
                self._attempt(candidate, bool(cookies), e)
                if isinstance(e, Cancelled):
                    raise
                if isinstance(e, AuthRequired):
                    self.needs_auth = True
                    if not wants_cookies:
                        wants_cookies = True
                        if await self._site_cookies():
                            continue
                elif isinstance(e, RateLimited | NetworkError) and retries < self.x.retry.retries:
                    wait = self._wait(e, retries)
                    if wait is not None:
                        retries += 1
                        log.info(
                            "%s failed on %s (%s); retrying in %gs",
                            candidate.tool,
                            self.url,
                            e,
                            wait,
                        )
                        await cancellable(asyncio.sleep(wait), self.cancel)
                        continue
                return e
            self._attempt(candidate, bool(cookies), None)
            return result, bool(cookies)

    async def _download(self, candidate: Candidate, cookies: list[Cookie]) -> DownloadResult:
        downloader = self.x.toolbox.get(candidate.tool, candidate.options)
        tool = candidate.tool

        def progress(update: DownloadProgress) -> None:
            self.progress(tool, update)

        ctx = DownloadContext(
            url=self.url.value,
            cookies=cookies,
            cancel=self.cancel,
            output_dir=self.output_dir,
            filename_template=self.template,
            progress=progress,
            downloader_for=self.x.toolbox.downloader_for,
        )
        return await downloader.download(ctx)

    def _attempt(self, candidate: Candidate, signed_in: bool, error: UDLError | None) -> None:
        attempt = Attempt(
            tool=candidate.tool,
            source=candidate.source,
            signed_in=signed_in,
            error=None if error is None else error.to_info(),
        )
        self.attempts.append(attempt)
        if error is not None and not isinstance(error, Cancelled):
            self.errors.append(error)
            log.info("%s could not download %s: %s", candidate.tool, self.url, error)
        self.on_attempt(attempt)

    def _wait(self, error: UDLError, retries: int) -> float | None:
        """How long to wait before retrying, or None to move on."""
        policy = self.x.retry
        if isinstance(error, RateLimited) and error.retry_after is not None:
            return error.retry_after if error.retry_after <= policy.max_wait else None
        return min(policy.backoff * 2**retries, policy.max_wait)

    def _browser(self, failed: Candidate) -> Candidate | None:
        """The browser, to try after `failed` wanted a login, unless the request named `failed`
        or the browser has been tried."""
        browser = Candidate(tool=_BROWSER, source=CandidateSource.PROBE)
        if (
            failed.source is CandidateSource.REQUEST
            or failed.tool == _BROWSER
            or browser.key in self.tried
            or not self.x.toolbox.has(_BROWSER)
        ):
            return None
        return browser

    async def _site_cookies(self) -> list[Cookie]:
        """The cookies that sign in to the URL's site (read once). A source that fails gives
        none."""
        if self._cookies is None:
            try:
                self._cookies = await self.x.cookies.for_url(self.url.value)
            except Exception:  # signing in is a bonus, never a crash (logged with its traceback)
                log.warning("Could not read the cookies for %s", self.url, exc_info=True)
                self._cookies = []
        return self._cookies

    async def _output_dir(self) -> Path:
        plugin = self.x.resolver.site_plugin(self.url)
        try:
            site = await self.db.get_site(self.url.site_key)
        except DatabaseError as e:
            log.warning("Could not read %s's settings: %s", self.url.site_key, e)
            site = None
        return resolve_output_dir(
            request=self.job.output_dir,
            plugin=plugin.output_dir(self.url) if plugin else None,
            site=site.default_output_dir if site else None,
            default=self.x.settings.download_dir,
        )

    def _failure(self) -> UDLError:
        """The error when every candidate failed."""
        for kind in (AuthRequired, RateLimited, NetworkError):
            if found := [error for error in self.errors if isinstance(error, kind)]:
                return found[-1]
        tools = list(dict.fromkeys(attempt.tool for attempt in self.attempts))
        if not tools:
            return NoStrategyFound(
                f"No downloader could try {self.url}",
                hint="Check probe_order in the config, and `udl plugins list`.",
            )
        return NoStrategyFound(
            f"No downloader could download {self.url} (tried {', '.join(tools)})",
            hint=f"`udl probe {self.url} --all` shows what each downloader found.",
        )
