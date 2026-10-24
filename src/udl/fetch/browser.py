"""
--------------
- browser.py -
--------------

The browser rung's tabs: pages of Chromium (Playwright) on udl's profile, where the user's logins
and the clearances of checks they passed are. A tab loads its page, waits for an element (or
for its network to go quiet, and for any reload the page makes of itself), and reads the DOM it
rendered; what is not HTML (JSON, a file) is read as it was served, through the browser's own
cookies.

The fetcher's tabs come from its BrowserLane: one Chromium on the profile, opened when a tab is
wanted, at most [fetch] browser_tabs tabs at once, and closed after browser_idle without one, so
downloads, `udl browser login`, the challenge window and other udls get the profile back.
(ChromiumTabs opens a Chromium for each tab.) ProfileCookies reads and writes the profile's
cookies, through the browser that has it open, if one does. Playwright comes with udl's browser
extra, so this module is only imported when it is needed.
"""

# System Imports
import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Final

# Package Imports
from playwright.async_api import BrowserContext, Page, Request, Response
from playwright.async_api import Error as PlaywrightError

# Local Imports
from udl.clock import Clock
from udl.config import FetchSettings
from udl.fetch.content import media_type
from udl.fetch.cookies import of_site
from udl.fetch.ladder import Snapshot
from udl.plugins.downloaders.auth import for_site
from udl.plugins.downloaders.base import CancelToken, Cookie
from udl.plugins.downloaders.browser import add_cookies, chromium, from_playwright
from udl.plugins.downloaders.errors import chromium_error

log = logging.getLogger(__name__)

QUIET: Final = timedelta(milliseconds=500)
"""How long a page's network must be quiet for it to have loaded."""
_STEP: Final = timedelta(milliseconds=50)
_HTML: Final = frozenset({"text/html", "application/xhtml+xml"})
_USER_AGENT: Final = "() => navigator.userAgent"


def own_user_agent(agent: str) -> str:
    """The browser's User-Agent as a browser on a screen sends it (headless, Chromium says so)."""
    return agent.replace("HeadlessChrome/", "Chrome/")


class PlaywrightTab:
    """A tab: its page, the document it shows (the main frame's last navigation) and its
    network's comings and goings."""

    def __init__(self, page: Page, *, timeout: timedelta, clock: Clock) -> None:
        self._page = page
        self._timeout = timeout
        self._clock = clock
        self._document: Response | None = None
        self._inflight: set[Request] = set()
        self._active_at = clock.now()
        page.on("request", self._started)
        page.on("requestfinished", self._ended)
        page.on("requestfailed", self._ended)
        page.on("response", self._responded)

    def _started(self, request: Request) -> None:
        self._inflight.add(request)
        self._active_at = self._clock.now()

    def _ended(self, request: Request) -> None:
        self._inflight.discard(request)
        self._active_at = self._clock.now()

    def _responded(self, response: Response) -> None:
        if response.request.is_navigation_request() and response.frame == self._page.main_frame:
            self._document = response

    async def goto(self, url: str, *, wait_for: str | None) -> Snapshot:
        self._document = None
        try:
            response = await self._page.goto(url, timeout=self._timeout.total_seconds() * 1000)
        except PlaywrightError as e:
            if "Download is starting" not in e.message:
                raise chromium_error(e.message) from e
            return await self._as_served(url)  # (a file: as it was served)
        # (A page that reloads itself has a document of its own by now: that one counts.)
        self._document = self._document or response
        kind, _ = media_type(response.headers.get("content-type") if response else None)
        if kind is not None and kind not in _HTML:
            return await self._as_served(url)
        if not await self._settle(wait_for):
            return (await self.look()).model_copy(update={"missing": wait_for})
        return await self.look()

    async def _as_served(self, url: str) -> Snapshot:
        """`url` as it was served, through the browser's cookies (JSON, a file...)."""
        try:
            served = await self._page.context.request.get(
                url, timeout=self._timeout.total_seconds() * 1000
            )
            body = await served.body()
        except PlaywrightError as e:
            raise chromium_error(e.message) from e
        return Snapshot(url=served.url, status=served.status, headers=served.headers, body=body)

    async def _settle(self, wait_for: str | None) -> bool:
        """Wait for `wait_for` (False when it never comes), or for the network to be quiet and
        the page loaded (again, when it reloads itself), within the timeout."""
        if wait_for is not None:
            try:
                await self._page.wait_for_selector(
                    wait_for, state="attached", timeout=self._timeout.total_seconds() * 1000
                )
            except PlaywrightError:
                return False
            return True
        deadline = self._clock.now() + self._timeout
        while self._clock.now() < deadline:
            if not self._inflight and self._clock.now() - self._active_at >= QUIET:
                with contextlib.suppress(PlaywrightError):
                    await self._page.wait_for_load_state("load", timeout=1000)
                    if not self._inflight and self._clock.now() - self._active_at >= QUIET:
                        return True
            await self._clock.sleep(_STEP)
        return True

    async def look(self) -> Snapshot:
        """The page as it is now: the DOM the browser rendered."""
        html = ""
        for _ in range(20):  # (it may be navigating: then its content cannot be read yet)
            try:
                html = await self._page.content()
                break
            except PlaywrightError:
                await self._clock.sleep(_STEP * 2)
        document = self._document
        headers: dict[str, str] = {}
        status = 200
        if document is not None:
            status = document.status
            with contextlib.suppress(PlaywrightError):
                headers = await document.all_headers()
        kind, _ = media_type(headers.get("content-type"))
        # (The DOM is text, whatever charset the page came in.)
        headers["content-type"] = f"{kind or 'text/html'}; charset=utf-8"
        return Snapshot(url=self._page.url, status=status, headers=headers, body=html.encode())

    async def user_agent(self) -> str:
        return own_user_agent(await self._page.evaluate(_USER_AGENT))


class ChromiumTabs:
    """Tabs, each in a Chromium of its own on udl's profile (or a new, empty one)."""

    def __init__(self, *, profile_dir: Path | None, timeout: timedelta, clock: Clock) -> None:
        self._profile_dir = profile_dir
        self._timeout = timeout
        self._clock = clock
        self._user_agent: str | None = None

    @property
    def user_agent(self) -> str | None:
        return self._user_agent

    @asynccontextmanager
    async def tab(
        self, *, headers: Mapping[str, str], cancel: CancelToken
    ) -> AsyncGenerator[PlaywrightTab]:
        async with (
            chromium(profile_dir=self._profile_dir, headless=True, cancel=cancel) as context,
            opened(context, headers, loads_within=self._timeout, clock=self._clock) as tab,
        ):
            if self._user_agent is None:
                self._user_agent = await tab.user_agent()
            yield tab


class BrowserLane:
    """The fetcher's browser (udl.fetch.ladder.Tabs): one Chromium on the profile in
    `profile_dir`, opened when a tab is wanted, at most `browser_tabs` tabs at once, closed after
    `browser_idle` without one. `launches` counts the times it opened."""

    def __init__(self, profile_dir: Path | None, *, settings: FetchSettings, clock: Clock) -> None:
        self.profile_dir = profile_dir
        self.settings = settings
        self.clock = clock
        self.launches = 0
        self._slots = asyncio.Semaphore(settings.browser_tabs)
        self._opening = asyncio.Lock()
        self._context: BrowserContext | None = None
        self._holder: asyncio.Task[None] | None = None
        self._release = asyncio.Event()
        self._active = 0
        self._idle: asyncio.Task[None] | None = None
        self._user_agent: str | None = None

    @property
    def context(self) -> BrowserContext | None:
        """The browser's context, while it is open."""
        return self._context

    @property
    def open(self) -> bool:
        return self._context is not None

    @property
    def user_agent(self) -> str | None:
        return self._user_agent

    @asynccontextmanager
    async def tab(
        self, *, headers: Mapping[str, str], cancel: CancelToken
    ) -> AsyncGenerator[PlaywrightTab]:
        async with self._slots:
            context = await self._opened()
            self._active += 1
            if self._idle is not None:
                self._idle.cancel()
                self._idle = None
            try:
                async with opened(
                    context, headers, loads_within=self.settings.timeout, clock=self.clock
                ) as tab:
                    if self._user_agent is None:
                        self._user_agent = await tab.user_agent()
                    yield tab
            finally:
                self._active -= 1
                if self._active == 0:
                    self._idle = asyncio.create_task(self._close_when_idle())

    async def _opened(self) -> BrowserContext:
        """The browser's context, opening it if it is closed."""
        async with self._opening:
            if self._context is not None:
                return self._context
            ready = asyncio.get_running_loop().create_future()
            self._release = asyncio.Event()
            self._holder = asyncio.create_task(self._hold(ready), name="udl fetch browser")
            # (A tab given up on while it opens leaves it opening, for the next.)
            return await asyncio.shield(ready)

    async def _hold(self, ready: asyncio.Future[BrowserContext]) -> None:
        """Hold the browser open until it is released."""
        try:
            async with chromium(
                profile_dir=self.profile_dir, headless=True, cancel=CancelToken()
            ) as context:
                self.launches += 1
                self._context = context
                ready.set_result(context)
                await self._release.wait()
        except Exception as e:  # noqa: BLE001 - (whatever kept it from opening, for its tabs)
            if not ready.done():
                ready.set_exception(e)
            else:
                log.warning("udl's browser closed with a problem: %s", e)
        finally:
            self._context = None

    async def _close_when_idle(self) -> None:
        await self.clock.sleep(self.settings.browser_idle)
        if self._active == 0:
            self._idle = None
            await self.close()

    async def close(self) -> None:
        """Close the browser (letting go of the profile) once its tabs have closed."""
        if self._idle is not None and self._idle is not asyncio.current_task():
            self._idle.cancel()
        self._idle = None
        holder = self._holder
        if holder is None:
            return
        self._holder = None
        self._release.set()
        await asyncio.wait([holder])
        if not holder.cancelled() and (problem := holder.exception()) is not None:
            log.warning("udl's browser closed with a problem: %s", problem)


@asynccontextmanager
async def opened(
    context: BrowserContext,
    headers: Mapping[str, str],
    *,
    loads_within: timedelta,
    clock: Clock,
) -> AsyncGenerator[PlaywrightTab]:
    """A new tab of `context` (whose pages load within `loads_within`), sending `headers` too,
    closed afterwards."""
    page = await context.new_page()
    try:
        if headers:
            await page.set_extra_http_headers(dict(headers))
        yield PlaywrightTab(page, timeout=loads_within, clock=clock)
    finally:
        with contextlib.suppress(PlaywrightError):
            await page.close()


# ---------------------------------------------------------------------------------------------
# The profile's cookies
# ---------------------------------------------------------------------------------------------


class ProfileCookies:
    """The cookies of udl's browser profile (udl.fetch.cookies.Profile): read and written
    through the context `open_context()` gives (the browser that has the profile open), else
    through a Chromium of their own on it."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        open_context: Callable[[], BrowserContext | None] = lambda: None,
    ) -> None:
        self.profile_dir = profile_dir
        self._open_context = open_context

    @asynccontextmanager
    async def _context(self) -> AsyncGenerator[BrowserContext]:
        if (context := self._open_context()) is not None:
            yield context
            return
        cancel = CancelToken()
        async with chromium(profile_dir=self.profile_dir, headless=True, cancel=cancel) as opened:
            yield opened

    async def _used(self) -> bool:
        """Whether the profile was ever used (else it has no cookies, and no Chromium starts)."""
        return self._open_context() is not None or await asyncio.to_thread(self.profile_dir.is_dir)

    async def cookies(self) -> list[Cookie]:
        if not await self._used():
            return []
        async with self._context() as context:
            return from_playwright(await context.cookies())

    async def cookies_for(self, url: str) -> list[Cookie]:
        """The cookies the profile has for url's site."""
        return for_site(await self.cookies(), url)

    async def add(self, cookies: list[Cookie]) -> None:
        async with self._context() as context:
            await add_cookies(context, cookies)

    async def remove(self, cookies: list[Cookie]) -> None:
        if not cookies or not await self._used():
            return
        async with self._context() as context:
            for cookie in cookies:
                await context.clear_cookies(
                    name=cookie.name, domain=cookie.domain, path=cookie.path
                )

    async def clear(self, site: str) -> int:
        if not await self._used():
            return 0
        async with self._context() as context:
            found = of_site(from_playwright(await context.cookies()), site)
            for cookie in found:
                await context.clear_cookies(
                    name=cookie.name, domain=cookie.domain, path=cookie.path
                )
        return len(found)
