"""
-------------
- window.py -
-------------

ChallengeWindow: where the user passes an anti-bot check. It is a browser window on udl's profile
(so the clearance a check sets stays there, for udl's later fetches): its first tab is a page udl
writes, listing the sites that wait and ticking each off as it passes, then a tab per site, on its
first waiting URL. A site that comes to wait while the window is open gets a tab of its own.

Each tab is looked at every second: a site has passed when its tab is on the site, loaded, quiet
for two seconds, and is no check any more; its page is then that URL's answer. The window closes
when every site has passed, when the person closes it, or after [fetch] challenge_timeout; the
sites not passed go on waiting.

WindowAsker is what the queue asks to show one: one window at a time, the fetcher's browser
letting go of the profile while the window has it. (Whether one can open at all, with a display,
is udl.fetch.kit's to say.)
"""

# System Imports
import asyncio
import contextlib
import html
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Final, Protocol

# Package Imports
from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

# Local Imports
from udl.clock import Clock, pause
from udl.config import FetchSettings
from udl.core.urls import URL, site_key
from udl.fetch.browser import PlaywrightTab, browser_user_agent
from udl.fetch.challenges import detect
from udl.fetch.ladder import Via, page_of
from udl.fetch.models import Passed, WaitingSite
from udl.plugins.downloaders.base import CancelToken
from udl.plugins.downloaders.browser import chromium

log = logging.getLogger(__name__)

LOOK_EVERY: Final = timedelta(seconds=1)
QUIET: Final = timedelta(seconds=2)
"""How long a site's tab must be quiet, after its check, for it to have passed."""


class Person(Protocol):
    """Someone who looks at a tab (tests' FakePerson; the user looks at the window)."""

    async def attend(self, tab: Page) -> None: ...


@dataclass
class _SiteTab:
    waiting: WaitingSite
    page: Page
    tab: PlaywrightTab
    passed: bool = False


@dataclass
class _Listing:
    sites: dict[str, bool] = field(default_factory=dict[str, bool])  # site: passed

    def html(self) -> str:
        rows = "".join(
            f'<li id="{html.escape(site)}" class="{"passed" if done else "waiting"}">'
            f"{html.escape(site)}: {'passed' if done else 'waits for you'}</li>"
            for site, done in self.sites.items()
        )
        return (
            "<!doctype html><html><head><title>udl: checks waiting for you</title><style>"
            "body{font-family:sans-serif;margin:2em}.passed{color:green}.waiting{color:#a60}"
            "</style></head><body><h1>Sites waiting for you</h1><p>Each tab is a site whose "
            "pages udl could not read past its anti-bot check. Pass the check in its tab: udl "
            "reads the page once it is past it, and goes on by itself.</p>"
            f"<ul>{rows}</ul></body></html>"
        )


class ChallengeWindow:
    """A window where the person passes sites' checks (see the module's docs)."""

    def __init__(
        self,
        profile_dir: Path | None,
        *,
        settings: FetchSettings,
        clock: Clock,
        headless: bool = False,
        person: Person | None = None,
    ) -> None:
        self.profile_dir = profile_dir
        self.settings = settings
        self.clock = clock
        self.headless = headless
        self.person = person
        self._context: BrowserContext | None = None
        self._tabs: dict[str, _SiteTab] = {}
        self._listing = _Listing()
        self._listing_page: Page | None = None
        self._coming: list[WaitingSite] = []
        self._woken = asyncio.Event()
        self._people: set[asyncio.Task[None]] = set()
        self._done = False

    @property
    def context(self) -> BrowserContext | None:
        """The window's context, while it is open."""
        return self._context

    def add(self, waiting: WaitingSite) -> bool:
        """Give a site that came to wait a tab of the window (False: it has closed)."""
        if self._done:
            return False
        if waiting.site not in self._tabs and waiting not in self._coming:
            self._coming.append(waiting)
            self._woken.set()
        return True

    async def show(self, sites: Sequence[WaitingSite], passed: Passed) -> list[str]:
        """Open the window on `sites`, until each has passed, the person closes it, or
        challenge_timeout. Returns the sites passed."""
        done: list[str] = []
        self._coming.extend(site for site in sites if site not in self._coming)
        async with chromium(
            profile_dir=self.profile_dir,
            headless=self.headless,
            user_agent=await browser_user_agent(),
            cancel=CancelToken(),
        ) as context:
            self._context = context
            closed = asyncio.Event()
            context.on("close", lambda _: closed.set())
            try:
                self._listing_page = context.pages[0] if context.pages else await context.new_page()
                deadline = self.clock.now() + self.settings.challenge_timeout
                while not closed.is_set():
                    await self._open_coming()
                    for site_tab in [t for t in self._tabs.values() if not t.passed]:
                        if await self._has_passed(site_tab):
                            await self._read(site_tab, passed)
                            done.append(site_tab.waiting.site)
                    if all(t.passed for t in self._tabs.values()) and not self._coming:
                        break
                    if self.clock.now() >= deadline:
                        log.info("Nobody passed the checks in udl's window in time")
                        break
                    self._woken.clear()
                    await pause(self.clock, LOOK_EVERY, closed, self._woken)
            except PlaywrightError as e:  # (the person closed it while udl was reading)
                log.debug("udl's window closed: %s", e)
            finally:
                self._done = True
                self._context = None
                self._tabs.clear()
                for person in self._people:
                    person.cancel()
        return done

    async def _open_coming(self) -> None:
        coming, self._coming = self._coming, []
        for waiting in coming:
            if waiting.site in self._tabs or self._context is None:
                continue
            self._listing.sites[waiting.site] = False
            page = await self._context.new_page()
            tab = PlaywrightTab(page, timeout=self.settings.timeout, clock=self.clock)
            self._tabs[waiting.site] = _SiteTab(waiting=waiting, page=page, tab=tab)
            with contextlib.suppress(PlaywrightError):  # (a check is what it shows: fine)
                await page.goto(waiting.url, timeout=self.settings.timeout.total_seconds() * 1000)
            if self.person is not None:
                attending = asyncio.create_task(self.person.attend(page))
                self._people.add(attending)
                attending.add_done_callback(self._people.discard)
        await self._show_listing()

    async def _show_listing(self) -> None:
        if self._listing_page is not None:
            with contextlib.suppress(PlaywrightError):
                await self._listing_page.set_content(self._listing.html())

    async def _has_passed(self, site_tab: _SiteTab) -> bool:
        page = site_tab.page
        if page.is_closed():
            return False
        try:
            on_site = site_key(page.url) == site_tab.waiting.site
        except ValueError:  # (about:blank, an error page)
            return False
        if not on_site or not site_tab.tab.quiet_for(QUIET):
            return False
        try:
            if await page.evaluate("() => document.readyState") != "complete":
                return False
            seen = await site_tab.tab.look()
        except PlaywrightError:  # (navigating)
            return False
        text = seen.body.decode("utf-8", "replace")
        return seen.status < 400 and detect(seen.status, seen.headers, text, seen.url) is None

    async def _read(self, site_tab: _SiteTab, passed: Passed) -> None:
        seen = await site_tab.tab.look()
        page = page_of(URL.parse(site_tab.waiting.url), seen, Via.PERSON)
        site_tab.passed = True
        self._listing.sites[site_tab.waiting.site] = True
        await passed(site_tab.waiting, page)
        await self._show_listing()
        log.info("%s was passed: its pages go on", site_tab.waiting.site)


# ---------------------------------------------------------------------------------------------
# Asking
# ---------------------------------------------------------------------------------------------


class Lane(Protocol):
    """The fetcher's browser, which lets go of the profile while the window has it."""

    async def pause(self) -> None: ...

    def resume(self) -> None: ...


class WindowAsker:
    """Shows waiting sites to the person in a ChallengeWindow on udl's profile, one window at a
    time, the fetcher's browser closed meanwhile."""

    def __init__(
        self,
        profile_dir: Path,
        *,
        settings: FetchSettings,
        clock: Clock,
        lane: Lane,
        headless: bool = False,
        person: Person | None = None,
    ) -> None:
        self.profile_dir = profile_dir
        self.settings = settings
        self.clock = clock
        self.lane = lane
        self.headless = headless
        self.person = person
        self._window: ChallengeWindow | None = None
        self._showing = asyncio.Lock()

    @property
    def context(self) -> BrowserContext | None:
        return self._window.context if self._window is not None else None

    def also(self, waiting: WaitingSite) -> bool:
        """Add a site to the window that is open (False when none is)."""
        return self._window is not None and self._window.add(waiting)

    async def ask(self, sites: Sequence[WaitingSite], passed: Passed) -> list[str]:
        """Show `sites` in a window until the person has passed them, closed it, or the time is
        up (in the window open already, if one is). Returns the sites passed."""
        if self._window is not None and all(self._window.add(site) for site in sites):
            return []
        async with self._showing:
            window = ChallengeWindow(
                self.profile_dir,
                settings=self.settings,
                clock=self.clock,
                headless=self.headless,
                person=self.person,
            )
            self._window = window
            await self.lane.pause()
            try:
                return await window.show(sites, passed)
            finally:
                self._window = None
                self.lane.resume()
