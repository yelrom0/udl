"""
--------------
- browser.py -
--------------

The browser rung's tabs: pages of Chromium (Playwright) on udl's profile, where the user's logins
and the clearances of checks they passed are. A tab loads its page, waits for an element (or
for its network to go quiet, and for any reload the page makes of itself), and reads the DOM it
rendered; what is not HTML (JSON, a file) is read as it was served, through the browser's own
cookies.

ChromiumTabs opens a Chromium for each tab. (The queue's browser lane keeps one open for its
batches: udl.fetch.queue.) Playwright comes with udl's browser extra, so this module is only
imported when a page is fetched in the browser.
"""

# System Imports
import contextlib
import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Final

# Package Imports
from playwright.async_api import BrowserContext, Page, Request, Response
from playwright.async_api import Error as PlaywrightError

# Local Imports
from udl.clock import Clock
from udl.fetch.content import media_type
from udl.fetch.ladder import Snapshot
from udl.plugins.downloaders.base import CancelToken
from udl.plugins.downloaders.browser import chromium
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
