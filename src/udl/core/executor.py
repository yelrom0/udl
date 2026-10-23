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

What it remembers (unless the request says not to), through DBManager:

- the probe's winner becomes a site-wide strategy (source `probe`), or, when the site has one for
  that tool already, its success is counted there (and the strategy is enabled again);
- a stored strategy counts its success, or its failure when the failure is the strategy's fault
  (DownloadError.counts_against_strategy); one stored only in a database udl does not write to is
  copied into one it does;
- a strategy that needed the site's cookies is marked requires_auth, so it is signed in from the
  start next time.

The request's tool and a site plugin's strategy are not remembered: they are chosen, not learned.
A database that cannot be written is logged and never fails the download.

Self-healing: a stored strategy that fails `heal_threshold` times in a row is disabled, and the
rest of the candidates (the probe chain, without the broken strategy's tool and options) re-probe
the URL; the winner is remembered as above. A manual strategy is never disabled, only warned
about: the user chose it.
"""

# System Imports
import asyncio
import logging
from collections.abc import Callable
from contextlib import aclosing
from pathlib import Path
from typing import Protocol
from uuid import UUID

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
from udl.db.models import Outcome, Site, Strategy, StrategySource, counted, utcnow
from udl.errors import (
    AuthRequired,
    Cancelled,
    DatabaseError,
    DownloadError,
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


async def site_cookies(source: CookieSource, url: str) -> list[Cookie]:
    """The cookies that sign in to url's site. A source that fails gives none (signing in is a
    bonus, never a crash), with a warning."""
    try:
        return await source.for_url(url)
    except Exception:  # (logged with its traceback)
        log.warning("Could not read the cookies for %s", url, exc_info=True)
        return []


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
    record: bool = True  # remember what worked (and what failed)


class Execution(BaseModel):
    """A download that worked: what it wrote, and how."""

    model_config = ConfigDict(frozen=True)

    candidate: Candidate  # the one that worked
    result: DownloadResult
    signed_in: bool  # whether it took the site's cookies
    output_dir: Path
    attempts: list[Attempt]  # every try, the successful one last
    strategy_id: UUID | None = None  # the stored strategy it used or learned


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
        self.tried: set[tuple[str, str, str]] = set()
        self.needs_auth = False  # the site wanted a login
        self._cookies: list[Cookie] | None = None  # None until read
        self._site_stored = False

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
            await self._failed(candidate, outcome)
            return outcome
        result, signed_in = outcome
        return Execution(
            candidate=candidate,
            result=result,
            signed_in=signed_in,
            output_dir=self.output_dir,
            attempts=list(self.attempts),
            strategy_id=await self._worked(candidate, signed_in),
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
        downloader = self.x.toolbox.get(candidate.tool, candidate.options, chain=candidate.chain)
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

    # -- remembering ---------------------------------------------------------------------------

    async def _worked(self, candidate: Candidate, signed_in: bool) -> UUID | None:
        """Remember that `candidate` worked. Returns the stored strategy it used or learned."""
        used = candidate.strategy
        if not self.job.record:
            return used.id if used else None
        try:
            if used is not None:
                strategy = await self._count(used, Outcome(ok=True))
                if signed_in and not strategy.requires_auth:
                    strategy = await self.db.upsert_strategy(
                        strategy.model_copy(update={"requires_auth": True})
                    )
                return strategy.id
            if candidate.source is CandidateSource.PROBE:
                return (await self._learn(candidate, signed_in)).id
        except DatabaseError as e:
            log.warning("Could not remember how %s was downloaded: %s", self.url, e)
            return used.id if used else None
        return None

    async def _failed(self, candidate: Candidate, error: UDLError) -> None:
        """Count a failure against the stored strategy it came from, if it is the strategy's
        fault."""
        used = candidate.strategy
        if (
            used is None
            or not self.job.record
            or not (isinstance(error, DownloadError) and error.counts_against_strategy)
        ):
            return
        try:
            await self._heal(await self._count(used, Outcome(ok=False, error=error.to_info())))
        except DatabaseError as e:
            log.warning("Could not count %s's failure on %s: %s", candidate.tool, self.url, e)

    async def _heal(self, strategy: Strategy) -> None:
        """Disable a strategy that has failed heal_threshold times in a row (the candidates
        after it re-probe the URL), unless the user chose it."""
        failures = strategy.consecutive_failures
        if failures < self.x.settings.heal_threshold or not strategy.enabled:
            return
        if strategy.source is StrategySource.MANUAL:
            log.warning(
                "The %s strategy you set for %s has failed %d times in a row; udl keeps it "
                "because you chose it (change it with `udl sites set`)",
                strategy.tool,
                strategy.site_key,
                failures,
            )
            return
        log.info(
            "Disabling the %s strategy for %s after %d failures in a row; re-probing",
            strategy.tool,
            strategy.site_key,
            failures,
        )
        await self.db.upsert_strategy(strategy.model_copy(update={"enabled": False}))

    async def _count(self, strategy: Strategy, outcome: Outcome) -> Strategy:
        """Count an outcome on a stored strategy. Returns it updated."""
        updated = await self.db.record_outcome(strategy.id, outcome)
        if updated is None:  # stored only where udl does not write: keep a copy where it does
            await self._store_site()
            updated = await self.db.upsert_strategy(counted(strategy, outcome))
        return updated

    async def _learn(self, candidate: Candidate, signed_in: bool) -> Strategy:
        """Remember the probe's winner as a site-wide strategy (or count its success on the
        site's strategy for that tool, enabling it again)."""
        await self._store_site()
        key = self.url.site_key
        same = next(
            (
                strategy
                for strategy in await self.db.strategies_for(key)
                if strategy.url_pattern is None
                and strategy.tool == candidate.tool
                and strategy.options == candidate.options
                and strategy.chain is None
            ),
            None,
        )
        if same is None:
            return await self.db.upsert_strategy(
                Strategy(
                    site_key=key,
                    tool=candidate.tool,
                    options=candidate.options,
                    requires_auth=signed_in,
                    source=StrategySource.PROBE,
                    success_count=1,
                    last_success_at=utcnow(),
                )
            )
        strategy = await self._count(same, Outcome(ok=True))
        if not strategy.enabled or (signed_in and not strategy.requires_auth):
            strategy = await self.db.upsert_strategy(
                strategy.model_copy(
                    update={"enabled": True, "requires_auth": strategy.requires_auth or signed_in}
                )
            )
        return strategy

    async def _store_site(self) -> None:
        """Make sure the databases udl writes to have the site (its strategies need it), keeping
        what is known about it."""
        if not self._site_stored:
            key = self.url.site_key
            await self.db.upsert_site(await self.db.get_site(key) or Site(key=key))
            self._site_stored = True

    # -- helpers ---------------------------------------------------------------------------------

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
            self._cookies = await site_cookies(self.x.cookies, self.url.value)
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
