"""
----------
- kit.py -
----------

FetchKit: what a service fetches pages with (its `fetches`): the queue, the ladder, the cookies
of udl's browser profile, the window where the user passes checks, and udl's browser itself,
which is made the first time it is wanted (it needs Playwright, udl's browser extra), so a udl
that never reads a page in the browser never imports it.

A window needs a display ($WAYLAND_DISPLAY or $DISPLAY). Without one, a fetch that would ask
waits instead (its URLs' notes say why), and solving says to run `udl fetch solve` where udl's
browser can show one.
"""

# System Imports
import os
import secrets
import socket
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Local Imports
from udl.automations.bus import EventBus
from udl.clock import Clock
from udl.config import FetchSettings, Settings
from udl.core.executor import CookieSource
from udl.core.urls import site_key
from udl.db.manager import DBManager
from udl.db.records import RecordQuery
from udl.errors import ToolUnavailable
from udl.fetch.cookies import CookieStore, SessionJar
from udl.fetch.ladder import Fetcher, FetchSites, HttpStatic, Tab
from udl.fetch.models import (
    FetchItem,
    ItemStatus,
    Passed,
    SolveReport,
    WaitingChallenge,
    WaitingSite,
)
from udl.fetch.queue import FetchQueue, PageStore
from udl.plugins.downloaders.base import CancelToken, Cookie

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

    from udl.fetch.browser import BrowserLane, ProfileCookies
    from udl.fetch.window import Person, WindowAsker

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


def _window_module() -> Any:
    """udl.fetch.window, imported only now: it needs Playwright."""
    _browser_module()
    import udl.fetch.window as window

    return window


def no_display() -> str | None:
    """Why no window can open here (no display), or None when one can."""
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        return None
    return "there is no display ($DISPLAY or $WAYLAND_DISPLAY) for a window"


class LazyBrowser:
    """udl's browser for the fetcher (udl.fetch.browser's BrowserLane, as udl.fetch.ladder's
    Tabs), and its profile's cookies (as udl.fetch.cookies' Profile), made when first wanted."""

    def __init__(self, profile_dir: Path, settings: FetchSettings, clock: Clock) -> None:
        self.profile_dir = profile_dir
        self.settings = settings
        self.clock = clock
        self._lane: BrowserLane | None = None
        self._profile: ProfileCookies | None = None
        self.window_context: Callable[[], BrowserContext | None] = lambda: None
        """The challenge window's context, while it is open (it has the profile then)."""

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
        """The context of the browser that has the profile open (the fetcher's, or the
        window's), if one has: the profile is read through it then."""
        lane = self._lane.context if self._lane is not None else None
        return lane or self.window_context()

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

    async def pause(self) -> None:
        """Let go of the profile (for the window), taking no tabs meanwhile."""
        await self.lane().pause()

    def resume(self) -> None:
        if self._lane is not None:
            self._lane.resume()

    async def close(self) -> None:
        if self._lane is not None:
            await self._lane.close()


class LazyAsker:
    """Shows the sites that wait to the person (udl.fetch.queue.Asker): in udl.fetch.window's
    WindowAsker, made when first wanted. With a `person` (tests' FakePerson), the window is
    headless and they attend it, wherever udl runs."""

    def __init__(
        self,
        browser: LazyBrowser,
        *,
        person: Person | None = None,
        display: Callable[[], str | None] = no_display,
    ) -> None:
        self._browser = browser
        self._person = person
        self._display = display
        self._asker: WindowAsker | None = None

    def unavailable(self) -> str | None:
        if self._person is not None:
            return None
        return self._display()

    @property
    def context(self) -> BrowserContext | None:
        return self._asker.context if self._asker is not None else None

    def also(self, waiting: WaitingSite) -> bool:
        return self._asker is not None and self._asker.also(waiting)

    async def ask(self, sites: Sequence[WaitingSite], passed: Passed) -> list[str]:
        if self._asker is None:
            window = _window_module()
            browser = self._browser
            asker: WindowAsker = window.WindowAsker(
                browser.profile_dir,
                settings=browser.settings,
                clock=browser.clock,
                lane=browser,
                headless=self._person is not None,
                person=self._person,
            )
            self._asker = asker
        return await self._asker.ask(sites, passed)


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
        person: Person | None = None,
        display: Callable[[], str | None] = no_display,
    ) -> None:
        fetch = settings.fetch
        self.settings = fetch
        self.db = db
        self.sites = FetchSites(db, clock)
        self.browser = LazyBrowser(settings.browser.profile_dir, fetch, clock)
        self.asker = LazyAsker(self.browser, person=person, display=display)
        self.browser.window_context = lambda: self.asker.context
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
            asker=self.asker,
        )
        self.cookies = CookieStore(self.profile, self.sites)

    async def waiting(self, sites: Sequence[str] | None = None) -> list[WaitingChallenge]:
        """The sites whose URLs wait for a person (or only `sites`), in any udl, by site."""
        return [
            WaitingChallenge(
                site=site,
                kind=items[0].challenge,
                urls=[item.url for item in items],
                batches=list(dict.fromkeys(item.batch_id for item in items)),
                since=min(
                    (item.challenged_at for item in items if item.challenged_at), default=None
                ),
            )
            for site, items in (await self._waiting_items(sites)).items()
        ]

    async def _waiting_items(self, sites: Sequence[str] | None) -> dict[str, list[FetchItem]]:
        query = RecordQuery(where={"status": ItemStatus.CHALLENGE})
        wanted = {site_key(site) for site in sites} if sites else None
        grouped: dict[str, list[FetchItem]] = {}
        for item in await self.db.list_records(FetchItem, query):
            if wanted is None or item.site in wanted:
                grouped.setdefault(item.site, []).append(item)
        return {
            site: sorted(
                items,
                key=lambda item: (item.challenged_at or item.created_at, item.position),
            )
            for site, items in sorted(grouped.items())
        }

    async def solve(self, sites: Sequence[str] | None = None) -> SolveReport:
        """Show the person the checks that wait now (or only `sites`'), in one window here,
        whichever udl's fetches wait for them. Raises ToolUnavailable when no window can open."""
        waiting = await self._waiting_items(sites)
        if not waiting:
            return SolveReport(passed=[], waiting=[])
        if (problem := self.asker.unavailable()) is not None:
            raise ToolUnavailable(
                f"udl cannot show you the checks here: {problem}",
                tool="browser",
                hint="Run `udl fetch solve` on udl's machine, where its browser can open a window.",
            )
        shown = [
            WaitingSite(site=site, url=items[0].url, item=items[0].id, kind=items[0].challenge)
            for site, items in waiting.items()
        ]
        passed = await self.asker.ask(shown, self.queue.passed)
        still = await self._waiting_items(sites)
        return SolveReport(passed=passed, waiting=list(still))

    async def close(self) -> None:
        """Stop the fetches this udl runs (they are left interrupted), and close its browser."""
        await self.queue.shutdown()
        await self.browser.close()
