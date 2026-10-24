"""
----------
- kit.py -
----------

FetchKit: what a service fetches pages with (its `fetches`): the queue, the ladder, the cookies
of udl's browser profile, and udl's browser itself, which is made the first time it is wanted
(it needs Playwright, udl's browser extra), so a udl that never reads a page in the browser
never imports it.
"""

# System Imports
import os
import secrets
import socket
from collections.abc import AsyncGenerator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Local Imports
from udl.automations.bus import EventBus
from udl.clock import Clock
from udl.config import FetchSettings, Settings
from udl.core.executor import CookieSource
from udl.db.manager import DBManager
from udl.errors import ToolUnavailable
from udl.fetch.cookies import CookieStore, SessionJar
from udl.fetch.ladder import Fetcher, FetchSites, HttpStatic, Tab
from udl.fetch.queue import FetchQueue, PageStore
from udl.plugins.downloaders.base import CancelToken, Cookie

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

    from udl.fetch.browser import BrowserLane, ProfileCookies

_EXTRA_HINT = (
    "Install udl's browser extra (`uv sync --extra browser`), then Chromium "
    "(`uv run playwright install chromium`)."
)


def _browser_module() -> Any:
    """udl.fetch.browser, imported only now: it needs Playwright."""
    try:
        import udl.fetch.browser as browser
    except ImportError as e:
        raise ToolUnavailable(
            "Reading pages in a browser needs Playwright", tool="browser", hint=_EXTRA_HINT
        ) from e
    return browser


class LazyBrowser:
    """udl's browser for the fetcher (udl.fetch.browser's BrowserLane, as udl.fetch.ladder's
    Tabs), and its profile's cookies (as udl.fetch.cookies' Profile), made when first wanted."""

    def __init__(self, profile_dir: Path, settings: FetchSettings, clock: Clock) -> None:
        self.profile_dir = profile_dir
        self.settings = settings
        self.clock = clock
        self._lane: BrowserLane | None = None
        self._profile: ProfileCookies | None = None

    def lane(self) -> BrowserLane:
        if self._lane is not None:
            return self._lane
        browser = _browser_module()
        lane: BrowserLane = browser.BrowserLane(
            self.profile_dir, settings=self.settings, clock=self.clock
        )
        self._lane = lane
        return lane

    def context(self) -> BrowserContext | None:
        """The browser's context, while it is open (the profile is read through it then)."""
        return self._lane.context if self._lane is not None else None

    @property
    def open(self) -> bool:
        return self._lane is not None and self._lane.open

    @property
    def user_agent(self) -> str | None:
        return self._lane.user_agent if self._lane is not None else None

    def tab(
        self, *, headers: Mapping[str, str], cancel: CancelToken
    ) -> AbstractAsyncContextManager[Tab]:
        return self._tab(headers, cancel)

    @asynccontextmanager
    async def _tab(self, headers: Mapping[str, str], cancel: CancelToken) -> AsyncGenerator[Tab]:
        async with self.lane().tab(headers=headers, cancel=cancel) as tab:
            yield tab

    def profile(self) -> ProfileCookies:
        if self._profile is not None:
            return self._profile
        browser = _browser_module()
        profile: ProfileCookies = browser.ProfileCookies(
            self.profile_dir, open_context=self.context
        )
        self._profile = profile
        return profile

    async def close(self) -> None:
        if self._lane is not None:
            await self._lane.close()


class LazyProfile:
    """The profile's cookies (udl.fetch.cookies.Profile, and AuthCookies' reader), through
    LazyBrowser."""

    def __init__(self, browser: LazyBrowser) -> None:
        self._browser = browser

    async def cookies(self) -> list[Cookie]:
        return await self._browser.profile().cookies()

    async def cookies_for(self, url: str) -> list[Cookie]:
        return await self._browser.profile().cookies_for(url)

    async def add(self, cookies: list[Cookie]) -> None:
        await self._browser.profile().add(cookies)

    async def remove(self, cookies: list[Cookie]) -> None:
        await self._browser.profile().remove(cookies)

    async def clear(self, site: str) -> int:
        return await self._browser.profile().clear(site)


def runner_name() -> str:
    """This udl process's name, for the batches it runs: host:process:random."""
    return f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"


class FetchKit:
    """The page fetcher of a service (see the module's docs)."""

    def __init__(
        self,
        settings: Settings,
        *,
        db: DBManager,
        bus: EventBus,
        clock: Clock,
        cookies: CookieSource,
        runner: str | None = None,
    ) -> None:
        fetch = settings.fetch
        self.settings = fetch
        self.sites = FetchSites(db, clock)
        self.browser = LazyBrowser(settings.browser.profile_dir, fetch, clock)
        self.profile = LazyProfile(self.browser)
        self.jar = SessionJar(cookies, self.profile, writes_through=lambda: self.browser.open)
        self.fetcher = Fetcher(
            fetch,
            static=HttpStatic(fetch),
            jar=self.jar,
            tabs=self.browser,
            sites=self.sites,
            clock=clock,
        )
        self.store = PageStore(fetch.store_dir)
        self.queue = FetchQueue(
            db,
            self.fetcher,
            bus,
            clock,
            fetch,
            runner=runner or runner_name(),
            store=self.store,
        )
        self.cookies = CookieStore(self.profile, self.sites)

    async def close(self) -> None:
        """Stop the fetches this udl runs (they are left interrupted), and close its browser."""
        await self.queue.shutdown()
        await self.browser.close()
