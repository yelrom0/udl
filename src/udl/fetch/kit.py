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
from collections import Counter
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
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
from udl.errors import DoesNotExist, ToolUnavailable
from udl.fetch.content import FetchedPage, PageFormat, PageText, RenderedPage
from udl.fetch.cookies import BrowserThenProfile, CookieStore, SessionJar
from udl.fetch.ladder import Fetcher, FetchSites, HttpStatic, Tab
from udl.fetch.models import (
    BatchStatus,
    ChallengeList,
    FetchBatch,
    FetchBatchInfo,
    FetchBatchList,
    FetchBatchRequest,
    FetchBatchSummary,
    FetchedItemInfo,
    FetchedPageInfo,
    FetchedPageRequest,
    FetchItem,
    FetchRequest,
    ItemStatus,
    ListFetchesRequest,
    Passed,
    RetryFetchRequest,
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
        cookies: CookieSource,  # (the user's browser's: udl's profile's are read here)
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
        self.jar = SessionJar(
            BrowserThenProfile(cookies, self.profile),
            self.profile,
            writes_through=lambda: self.browser.open,
        )
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

    # -- the operations' work --------------------------------------------------------------

    async def fetch_pages(self, request: FetchRequest, *, asks: bool) -> FetchBatchInfo:
        """Fetch a request's URLs, waiting for them as it says (`asks`: whether a window may
        open for the person, which an automation never asks for)."""
        batch = await self.queue.submit(
            request.urls,
            request.options(),
            person=request.person,
            label=request.label,
            asks=asks,
        )
        if request.wait:
            batch = await self.queue.wait(batch.id, within=timedelta(seconds=request.timeout))
        return await self.info(batch, max_chars=request.max_chars)

    async def list_fetches(self, request: ListFetchesRequest) -> FetchBatchList:
        where = {"status": request.status} if request.status is not None else {}
        query = RecordQuery(
            where=where, since=request.since, limit=request.limit, offset=request.offset
        )
        found = [
            await self.queue.batch(batch.id)
            for batch in await self.db.list_records(FetchBatch, query)
        ]
        return FetchBatchList(fetches=[_summary(batch) for batch in found])

    async def get_fetch(self, request: FetchBatchRequest) -> FetchBatchInfo:
        return await self.info(await self.queue.batch(request.id), max_chars=request.max_chars)

    async def cancel_fetch(self, request: FetchBatchRequest) -> FetchBatchInfo:
        return await self.info(await self.queue.cancel(request.id), max_chars=request.max_chars)

    async def retry_fetch(self, request: RetryFetchRequest) -> FetchBatchInfo:
        batch = await self.queue.retry(request.id, items=request.items, via=request.via)
        return await self.info(batch, max_chars=0)

    async def list_challenges(self) -> ChallengeList:
        return ChallengeList(challenges=await self.waiting())

    async def get_fetched_page(self, request: FetchedPageRequest) -> FetchedPageInfo:
        item = await self.db.get_record(FetchItem, request.id)
        if item is None:
            raise DoesNotExist(
                f"No fetched page {request.id}",
                hint="Each URL of a fetch has an id: get_fetch (`udl fetch show`) gives them.",
            )
        options = (await self.queue.batch(item.batch_id)).options
        shown_as = request.format or options.format
        rendered = await self.rendered(
            item,
            shown_as,
            main_only=options.main_only if request.main_only is None else request.main_only,
            select=request.select if request.select is not None else options.select,
        )
        content = rendered.content if rendered is not None else None
        end = request.offset + request.max_chars
        return FetchedPageInfo(
            id=item.id,
            batch=item.batch_id,
            url=item.url,
            status=item.status,
            via=item.via,
            title=item.title,
            final_url=item.final_url,
            http_status=item.http_status,
            content_type=item.content_type,
            charset=item.charset,
            size=item.size,
            truncated=item.truncated,
            error=item.error,
            note=_note(item, rendered),
            attempts=item.attempts,
            format=shown_as,
            content=content[request.offset : end] if content is not None else None,
            offset=request.offset,
            length=len(content) if content is not None else None,
            next_offset=end if content is not None and len(content) > end else None,
        )

    async def rendered(
        self,
        item: FetchItem,
        shown_as: PageFormat,
        *,
        main_only: bool,
        select: str | None,
    ) -> RenderedPage | None:
        """A URL's page, as `shown_as` says, from what was kept of it (None when it has none)."""
        body = await self.body(item)
        if body is None:
            return None
        page = FetchedPage(
            url=item.url,
            final_url=item.final_url or item.url,
            status=item.http_status or 200,
            content_type=item.content_type,
            charset=item.charset,
            title=item.title,
            headers=item.headers,
            via=item.via.value if item.via is not None else "static",
            body=body,
            truncated=item.truncated,
        )
        return PageText.render(page, shown_as, main_only=main_only, select=select)

    async def body(self, item: FetchItem) -> bytes | None:
        """The body kept of a URL's page, as it was read (None when it has none)."""
        if item.status is not ItemStatus.SUCCEEDED or item.body is None:
            return None
        try:
            return await self.store.read(item.body)
        except OSError:  # (deleted meanwhile)
            return None

    async def info(self, batch: FetchBatch, *, max_chars: int) -> FetchBatchInfo:
        """A batch as an answer: each URL with its page (at most `max_chars` of it, and at most
        [fetch] max_answer characters of pages in all)."""
        items = await self.queue.items(batch.id)
        options = batch.options
        budget = self.settings.max_answer
        shown: list[FetchedItemInfo] = []
        for item in items:
            info = _item(item)
            rendered = (
                await self.rendered(
                    item, options.format, main_only=options.main_only, select=options.select
                )
                if max_chars
                else None
            )
            if rendered is not None and rendered.content is not None:
                content = rendered.content[:max_chars]
                if len(content) > budget:
                    info = info.model_copy(update={"left_out": True})
                else:
                    budget -= len(content)
                    cut = len(rendered.content) > max_chars
                    info = info.model_copy(
                        update={"content": content, "next_offset": max_chars if cut else None}
                    )
            if rendered is not None and rendered.note and info.note is None:
                info = info.model_copy(update={"note": rendered.note})
            shown.append(info)
        return FetchBatchInfo(
            id=batch.id,
            label=batch.label,
            status=batch.status,
            person=batch.person,
            format=options.format,
            counts=batch.counts,
            created_at=batch.created_at,
            finished_at=batch.finished_at,
            runner=batch.runner,
            items=shown,
            hint=_hint(batch, items),
        )

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

    async def solve(
        self,
        sites: Sequence[str] | None = None,
        *,
        told: Callable[[str], None] | None = None,
    ) -> SolveReport:
        """Show the person the checks that wait now (or only `sites`'), in one window here,
        whichever udl's fetches wait for them, telling `told` of each site passed. Raises
        ToolUnavailable when no window can open."""
        waiting = await self._waiting_items(sites)
        if not waiting:
            return SolveReport(passed=[], waiting=[])
        if (problem := self.asker.unavailable()) is not None:
            raise ToolUnavailable(
                f"udl cannot show you the checks here: {problem}",
                tool="browser",
                hint="Run `udl fetch solve` in a desktop session on udl's machine, where its "
                "browser can open a window.",
            )
        shown = [
            WaitingSite(site=site, url=items[0].url, item=items[0].id, kind=items[0].challenge)
            for site, items in waiting.items()
        ]

        async def passed(waiting: WaitingSite, page: FetchedPage) -> None:
            await self.queue.passed(waiting, page)
            if told is not None:
                told(waiting.site)

        done = await self.asker.ask(shown, passed)
        still = await self._waiting_items(sites)
        return SolveReport(passed=done, waiting=list(still))

    async def close(self) -> None:
        """Stop the fetches this udl runs (they are left interrupted), and close its browser."""
        await self.queue.shutdown()
        await self.browser.close()


def _item(item: FetchItem) -> FetchedItemInfo:
    return FetchedItemInfo(
        id=item.id,
        url=item.url,
        status=item.status,
        via=item.via,
        title=item.title,
        final_url=item.final_url,
        http_status=item.http_status,
        content_type=item.content_type,
        size=item.size,
        challenge=item.challenge,
        error=item.error,
        note=item.note,
    )


def _summary(batch: FetchBatch) -> FetchBatchSummary:
    return FetchBatchSummary(
        id=batch.id,
        label=batch.label,
        status=batch.status,
        counts=batch.counts,
        created_at=batch.created_at,
        finished_at=batch.finished_at,
    )


def _note(item: FetchItem, rendered: RenderedPage | None) -> str | None:
    if item.note is not None:
        return item.note
    return rendered.note if rendered is not None else None


def _pages(count: int) -> str:
    return "1 page" if count == 1 else f"{count} pages"


def _hint(batch: FetchBatch, items: list[FetchItem]) -> str | None:
    """What to do about a batch's URLs that are not done."""
    waiting = [item for item in items if item.status is ItemStatus.CHALLENGE]
    if waiting:
        sites = Counter(item.site for item in waiting)
        kinds = sorted({str(item.challenge) for item in waiting if item.challenge})
        check = f"a {' or '.join(kinds)} check" if kinds else "an anti-bot check"
        where = " and ".join(f"{_pages(n)} on {site}" for site, n in sites.items())
        verb = "waits" if len(waiting) == 1 else "wait"
        windowed = batch.person.value == "ask" and not any(item.note for item in waiting)
        how = (
            f"{verb} for the user to pass {check} in udl's browser window"
            if windowed
            else f"{verb} for someone to pass {check} (`udl fetch solve`, solve_challenges, on "
            "udl's machine)"
        )
        return f"{where[0].upper()}{where[1:]} {how}. Call get_fetch with this id for them later."
    if any(item.status in (ItemStatus.QUEUED, ItemStatus.RUNNING) for item in items):
        return "Some pages are still being read: call get_fetch with this id for them later."
    if batch.status is BatchStatus.INTERRUPTED:
        return "The udl that ran this fetch stopped before it ended: retry_fetch runs the rest."
    return None
