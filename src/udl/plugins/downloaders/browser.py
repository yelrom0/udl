"""
--------------
- browser.py -
--------------

The browser downloader: pages whose media only shows up in a real browser (added by scripts,
behind a login or a challenge). It loads the page in Chromium (Playwright), records the media and
stream playlists the page requests (in any frame), reads its media elements' sources, and hands
the best find off, a file to http and a stream playlist to yt-dlp, with the browser's cookies,
its User-Agent and the Referer the browser sent.

Given a site's tag chain (its `chain` option), it follows the chain to the media instead: each
page it opens is a tab of its own, which can wait for elements, click them and report the
requests it has made. The finds are handed off in the same way.

Chromium runs on udl's persistent profile (where `udl browser login` keeps logins) unless the
profile_dir option is None. One Chromium at a time uses a profile: others wait their turn.
"""

# System Imports
import asyncio
import contextlib
import logging
import re
import stat
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final

# Package Imports
from playwright.async_api import BrowserContext, Page, Request, Response, async_playwright
from playwright.async_api import Cookie as PlaywrightCookie
from playwright.async_api import Error as PlaywrightError
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    PositiveFloat,
    SecretStr,
    TypeAdapter,
)

# Local Imports
from udl.chains.executor import (
    ChainRun,
    Document,
    Reader,
    StaticDocument,
    chain_handoffs,
    chain_headers,
    probe_result,
    run_chain,
)
from udl.chains.models import FetchMode, StepTrace, TagChain
from udl.chains.suggest import ChainSuggestion, suggest
from udl.config import ExpandedPath, browser_profile_dir
from udl.errors import ConfigError, DownloadError, ExtractionFailed, ToolUnavailable, UnsupportedURL
from udl.plugins.downloaders.base import (
    CancelToken,
    Cookie,
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadResult,
    Handoff,
    MediaCandidate,
    MediaKind,
    ProbeContext,
    ProbeResult,
    cancellable,
)
from udl.plugins.downloaders.errors import chromium_error, http_status_error
from udl.plugins.downloaders.http import PAGE_TYPES
from udl.plugins.downloaders.media import Find, stream_kind, unique, url_name
from udl.plugins.requirements import Requirement

log = logging.getLogger(__name__)

TOOL: Final[str] = "browser"

# How sure the browser is of a find: what the page requested or plays is media.
_STREAM: Final[float] = 0.8  # a stream playlist it requested
_ELEMENT: Final[float] = 0.8  # a <video> / <audio> source
_REQUESTED: Final[float] = 0.7  # some other media it requested

# Pieces of a stream, which the stream's playlist (if any) is the way to download.
_SEGMENT_TYPES: Final = frozenset({"video/mp2t", "video/iso.segment"})
_SEGMENT_EXTENSIONS: Final = (".ts", ".m4s")

# The http(s) sources of a frame's media elements, as [tag, url] pairs.
_ELEMENT_SOURCES: Final = """() => [...document.querySelectorAll("video, audio")].flatMap(
    (element) => [element.currentSrc, element.src, ...[...element.querySelectorAll("source")]
        .map((source) => source.src)]
        .filter((src) => /^https?:/.test(src))
        .map((src) => [element.tagName.toLowerCase(), src]))"""
_SOURCES: Final = TypeAdapter(list[tuple[str, str]])
# How often a login in progress takes a copy of the browser's cookies.
_LOGIN_POLL: Final[float] = 0.5
_USER_AGENT: Final = "() => navigator.userAgent"
# A document served as one of these is read as it was served (e.g. an API's JSON), not rendered.
_TEXT_TYPES: Final = ("text/", "application/json", "application/javascript", "+json", "+xml")


class BrowserOptions(DownloaderOptions):
    """[tools.browser] in the config."""

    headless: bool = True
    # The persistent profile, which keeps logins; None: a new, empty profile each time.
    profile_dir: ExpandedPath | None = Field(default_factory=browser_profile_dir)
    user_agent: str | None = None  # None: Chromium's own
    timeout: PositiveFloat = 30.0  # seconds for a page to load
    wait: NonNegativeFloat = 2.0  # seconds to wait, once it has, for it to request media
    # The site's tag chain (`udl chain set`), followed to the media instead of looking for it.
    chain: TagChain | None = None


class _PlaywrightCookie(BaseModel):
    """A cookie as Playwright reports it."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    value: str
    domain: str
    path: str = "/"
    expires: float = -1  # -1: a session cookie
    http_only: bool = Field(default=False, alias="httpOnly")
    secure: bool = False

    def cookie(self) -> Cookie:
        return Cookie(
            name=self.name,
            value=SecretStr(self.value),
            domain=self.domain,
            path=self.path,
            secure=self.secure,
            http_only=self.http_only,
            expires=None if self.expires < 0 else datetime.fromtimestamp(self.expires, UTC),
        )


def from_playwright(cookies: list[PlaywrightCookie]) -> list[Cookie]:
    """Playwright's cookies as udl's."""
    return [_PlaywrightCookie.model_validate(cookie).cookie() for cookie in cookies]


async def add_cookies(context: BrowserContext, cookies: list[Cookie]) -> None:
    """Give a browser context udl's cookies."""
    if cookies:
        await context.add_cookies(
            [
                {
                    "name": cookie.name,
                    "value": cookie.value.get_secret_value(),
                    "domain": cookie.domain,
                    "path": cookie.path,
                    "secure": cookie.secure,
                    "httpOnly": cookie.http_only,
                    "expires": cookie.expires.timestamp() if cookie.expires else -1,
                }
                for cookie in cookies
            ]
        )


# ---------------------------------------------------------------------------------------------
# Chromium, on a profile
# ---------------------------------------------------------------------------------------------

_profiles_lock: Final = threading.Lock()
_profiles: Final[dict[Path, threading.Lock]] = {}


def _lock_for(profile: Path) -> threading.Lock:
    with _profiles_lock:
        return _profiles.setdefault(profile.absolute(), threading.Lock())


@asynccontextmanager
async def _profile(path: Path, cancel: CancelToken) -> AsyncGenerator[None]:
    """Hold the profile at `path`, waiting while another Chromium of udl's uses it."""
    lock = _lock_for(path)
    while not lock.acquire(blocking=False):
        cancel.check()
        await asyncio.sleep(0.05)
    try:
        yield
    finally:
        lock.release()


def _launch_error(error: PlaywrightError) -> DownloadError:
    if "Executable doesn't exist" in error.message:
        return ToolUnavailable(
            "Chromium is not installed",
            tool=TOOL,
            hint="Install it: `uv run playwright install chromium`.",
        )
    return DownloadError(f"Chromium could not start: {error.message}", tool=TOOL)


def _check_private(profile_dir: Path) -> None:
    """Warn when other users can get into the profile, which holds the user's logins."""
    with contextlib.suppress(FileNotFoundError):  # (Chromium makes it, for its owner alone)
        if stat.S_IMODE(profile_dir.stat().st_mode) & 0o077:
            log.warning(
                "Other users of this machine can get into the browser profile %s, which holds "
                "your logins: `chmod 700 %s` keeps them out",
                profile_dir,
                profile_dir,
            )


@asynccontextmanager
async def chromium(
    *,
    profile_dir: Path | None,
    headless: bool,
    user_agent: str | None = None,
    cancel: CancelToken,
) -> AsyncGenerator[BrowserContext]:
    """A Chromium browser context: on the persistent profile in `profile_dir` (holding it), or,
    when that is None, on a new, empty profile. Raises ToolUnavailable when Chromium is not
    installed."""
    # Headless, nothing is drawn on a screen, so no GPU: its drivers would write shader caches
    # to the user's cache dir.
    args = ["--disable-gpu", "--disable-software-rasterizer"] if headless else []
    async with AsyncExitStack() as stack:
        if profile_dir is not None:
            await asyncio.to_thread(_check_private, profile_dir)
            await stack.enter_async_context(_profile(profile_dir, cancel))
        playwright = await stack.enter_async_context(async_playwright())
        try:
            if profile_dir is None:
                browser = await playwright.chromium.launch(headless=headless, args=args)
                stack.push_async_callback(browser.close)
                context = await browser.new_context(user_agent=user_agent)
            else:
                context = await playwright.chromium.launch_persistent_context(
                    profile_dir, headless=headless, args=args, user_agent=user_agent
                )
        except PlaywrightError as e:
            raise _launch_error(e) from e
        stack.push_async_callback(context.close)
        yield context


async def profile_cookies(profile_dir: Path) -> list[Cookie]:
    """Every cookie in the persistent profile at `profile_dir`."""
    async with chromium(profile_dir=profile_dir, headless=True, cancel=CancelToken()) as context:
        return from_playwright(await context.cookies())


async def login(
    url: str, *, profile_dir: Path, headless: bool, until: Awaitable[object] | None
) -> list[Cookie]:
    """Open `url` in Chromium on the profile at `profile_dir`, and wait until the browser is
    closed, or `until` is done. Returns every cookie the profile has by then."""
    cookies: list[Cookie] = []
    async with chromium(
        profile_dir=profile_dir, headless=headless, cancel=CancelToken()
    ) as context:
        closed = asyncio.Event()
        context.on("close", lambda _: closed.set())
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(url)  # (a page that says "sign in" is what the user expects here)
        except PlaywrightError as e:
            raise chromium_error(e.message) from e
        waits: list[asyncio.Task[object]] = [asyncio.ensure_future(closed.wait())]
        if until is not None:
            waits.append(asyncio.ensure_future(until))
        try:
            while True:
                # A copy as it goes: once the browser has closed, they cannot be asked for.
                with contextlib.suppress(PlaywrightError):
                    cookies = from_playwright(await context.cookies())
                done, _ = await asyncio.wait(
                    waits, timeout=_LOGIN_POLL, return_when=asyncio.FIRST_COMPLETED
                )
                if done:
                    break
        finally:
            for wait in waits:
                wait.cancel()
    return cookies


# ---------------------------------------------------------------------------------------------
# Finding media
# ---------------------------------------------------------------------------------------------


def _requested(response: Response, page_url: str) -> Find | None:
    """What a response the page received is, if it is media."""
    url = response.url
    content_type = response.headers.get("content-type", "")
    media_type = content_type.partition(";")[0].strip().lower()
    kind = stream_kind(url, content_type)
    is_media = response.request.resource_type == "media" or media_type.startswith(
        ("video/", "audio/")
    )
    is_segment = media_type in _SEGMENT_TYPES or url_name(url).lower().endswith(_SEGMENT_EXTENSIONS)
    if not (200 <= response.status < 300 and (kind or (is_media and not is_segment))):
        return None
    return Find(
        media=MediaCandidate(
            url=url,
            kind=kind or MediaKind.FILE,
            content_type=content_type or None,
            found_by="network",
        ),
        confidence=_STREAM if kind else _REQUESTED,
        # The Referer the browser sent with it (e.g. the page of the iframe that played it).
        referer=response.request.headers.get("referer") or page_url,
    )


async def _element_finds(page: Page) -> list[Find]:
    """The sources of the media elements in every frame of the page."""
    finds: list[Find] = []
    for frame in page.frames:
        with contextlib.suppress(PlaywrightError):  # (a frame that went away)
            for tag, src in _SOURCES.validate_python(await frame.evaluate(_ELEMENT_SOURCES)):
                media = MediaCandidate(
                    url=src, kind=stream_kind(src) or MediaKind.FILE, found_by=f"<{tag}>"
                )
                finds.append(Find(media=media, confidence=_ELEMENT, referer=frame.url))
    return finds


class _Visit(BaseModel):
    """What a page had: its title, its media (best first), the cookies for it and its media,
    and the browser's User-Agent."""

    model_config = ConfigDict(frozen=True)

    title: str | None
    finds: list[Find]
    cookies: list[Cookie]
    user_agent: str

    def handoffs(self) -> list[Handoff]:
        headers = {"User-Agent": self.user_agent}
        return [find.handoff(cookies=self.cookies, headers=headers) for find in self.finds]


# ---------------------------------------------------------------------------------------------
# Following tag chains
# ---------------------------------------------------------------------------------------------


def _served_as_text(content_type: str) -> bool:
    """Whether a document of this type is read as it was served (e.g. an API's JSON), rather
    than as the page Chromium made of it."""
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type not in PAGE_TYPES and (
        media_type.startswith(_TEXT_TYPES) or media_type.endswith(_TEXT_TYPES)
    )


class _ChainPage(Document):
    """A page a chain is on: a tab of its own, which records the requests it makes."""

    def __init__(self, page: Page, *, timeout: float) -> None:
        self._page = page
        self._timeout = timeout
        self._response: Response | None = None
        self._requests: list[tuple[str, str]] = []  # (URL, Referer)
        self._requested = asyncio.Event()
        page.on("request", self._on_request)

    def _on_request(self, request: Request) -> None:
        self._requests.append((request.url, request.headers.get("referer") or self._page.url))
        self._requested.set()

    @property
    def url(self) -> str:
        return self._page.url

    async def load(self, url: str, *, referer: str | None) -> None:
        try:
            self._response = await self._page.goto(
                url, referer=referer, timeout=self._timeout * 1000
            )
        except PlaywrightError as e:
            raise chromium_error(e.message) from e
        if self._response is not None and (
            error := http_status_error(
                self._response.status,
                url,
                tool=TOOL,
                retry_after=self._response.headers.get("retry-after"),
            )
        ):
            raise error

    async def text(self) -> str:
        response = self._response
        if response is not None and _served_as_text(response.headers.get("content-type", "")):
            return await response.text()
        return await self._page.content()

    async def wait_for(self, selector: str) -> None:
        try:
            await self._page.wait_for_selector(
                selector, state="attached", timeout=self._timeout * 1000
            )
        except PlaywrightError as e:
            raise ExtractionFailed(
                f"No element matching {selector!r} came on {self.url}", tool=TOOL
            ) from e

    async def click(self, selector: str) -> None:
        try:
            await self._page.click(selector, timeout=self._timeout * 1000)
        except PlaywrightError as e:
            problem = e.message.splitlines()[0]
            raise ExtractionFailed(
                f"Could not click {selector!r} on {self.url}: {problem}", tool=TOOL
            ) from e

    async def requested(self, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(self._timeout):
                while True:
                    self._requested.clear()
                    if found := self._matching(pattern):
                        return found
                    await self._requested.wait()
        return []

    def _matching(self, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
        return [(url, referer) for url, referer in self._requests if pattern.search(url)]

    async def user_agent(self) -> str:
        return await self._page.evaluate(_USER_AGENT)


class _ChainReader(Reader):
    """Opens a chain's documents in Chromium, each in a tab of its own."""

    def __init__(self, context: BrowserContext, *, timeout: float, referer: bool) -> None:
        self._context = context
        self._timeout = timeout
        self._referer = referer  # send the Referer (else the chain sends its own)
        self._pages: list[_ChainPage] = []

    async def open(self, url: str, *, referer: str | None) -> Document:
        page = _ChainPage(await self._context.new_page(), timeout=self._timeout)
        self._pages.append(page)
        await page.load(url, referer=referer if self._referer else None)
        return page

    async def user_agent(self) -> str:
        """The browser's User-Agent (once a page is open)."""
        return await self._pages[0].user_agent()


# ---------------------------------------------------------------------------------------------
# The downloader
# ---------------------------------------------------------------------------------------------


class BrowserDownloader(Downloader[BrowserOptions]):
    """Loads a page in Chromium to find its media, and has http or yt-dlp download it."""

    name = TOOL
    options_model = BrowserOptions
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.module(
            "playwright",
            hint="Install udl's browser extra (`uv sync --extra browser`), then Chromium "
            "(`uv run playwright install chromium`).",
        ),
    )

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        if (chain := self.options.chain) is not None:
            return probe_result(TOOL, *await self._follow(chain, ctx))
        visit = await self._visit(ctx)
        return ProbeResult(
            tool=TOOL,
            ok=True,
            confidence=visit.finds[0].confidence,
            title=visit.title,
            media=[find.media for find in visit.finds],
            handoff=visit.handoffs()[0],
        )

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        if (chain := self.options.chain) is not None:
            run, handoffs = await self._follow(chain, ctx)
            hand_off = self.hand_off_all if run.every else self.hand_off_best
            result = await hand_off(handoffs, ctx)
            return result.model_copy(update={"title": run.title or result.title})
        visit = await self._visit(ctx)
        result = await self.hand_off_best(visit.handoffs(), ctx)
        return result.model_copy(update={"title": visit.title or result.title})

    async def run_chain(
        self, ctx: ProbeContext, *, trace: list[StepTrace] | None = None
    ) -> ChainRun:
        """Follow the chain in the options from ctx.url (see udl.chains.executor.ChainRunner).
        Raises ConfigError when there is none."""
        if self.options.chain is None:
            raise ConfigError(f"The {TOOL} downloader has no tag chain to follow")
        run, _ = await self._follow(self.options.chain, ctx, trace)
        return run

    async def _follow(
        self, chain: TagChain, ctx: ProbeContext, trace: list[StepTrace] | None = None
    ) -> tuple[ChainRun, list[Handoff]]:
        """Where `chain` leads from ctx.url, and the downloads of its finds (with the browser's
        cookies and User-Agent)."""
        headers = {**ctx.headers, **chain_headers(chain, ctx.url)}

        async def follow(context: BrowserContext) -> tuple[ChainRun, list[Handoff]]:
            referer = not any(name.lower() == "referer" for name in headers)
            reader = _ChainReader(context, timeout=self.options.timeout, referer=referer)
            run = await run_chain(chain, ctx.url, reader, trace=trace)
            urls = [ctx.url, *(find.media.url for find in run.finds)]
            cookies = from_playwright(await context.cookies(urls))
            agent = {"User-Agent": await reader.user_agent()}
            return run, chain_handoffs(chain, run, url=ctx.url, cookies=cookies, headers=agent)

        return await self._in_chromium(ctx.model_copy(update={"headers": headers}), follow)

    async def suggest_chains(self, ctx: ProbeContext) -> list[ChainSuggestion]:
        """Chains to the media on ctx.url's page as Chromium renders it, its frames' pages and
        what it requests (see udl.chains.suggest)."""

        async def read(context: BrowserContext) -> tuple[dict[str, Document], list[str]]:
            page, requested = await self._load(context, ctx)
            pages: dict[str, Document] = {ctx.url: StaticDocument(page.url, await page.content())}
            for frame in page.frames[1:]:
                with contextlib.suppress(PlaywrightError):  # (a frame that went away)
                    pages[frame.url] = StaticDocument(frame.url, await frame.content())
            return pages, [find.media.url for find in requested]

        pages, requested = await self._in_chromium(ctx, read)
        return await suggest(ctx.url, pages, fetch=FetchMode.BROWSER, requested=requested)

    async def _in_chromium[T](
        self, ctx: ProbeContext, work: Callable[[BrowserContext], Awaitable[T]]
    ) -> T:
        """Do `work` in Chromium, with ctx's cookies and headers (stopped when ctx.cancel is)."""
        options = self.options

        async def run() -> T:
            async with chromium(
                profile_dir=options.profile_dir,
                headless=options.headless,
                user_agent=options.user_agent,
                cancel=ctx.cancel,
            ) as context:
                await add_cookies(context, ctx.cookies)
                await context.set_extra_http_headers(ctx.headers)
                return await work(context)

        return await cancellable(run(), ctx.cancel)

    async def _load(self, context: BrowserContext, ctx: ProbeContext) -> tuple[Page, list[Find]]:
        """A page with ctx.url loaded, and the media it requested (waiting a little for some)."""
        page = await context.new_page()
        requested: list[Find] = []
        found = asyncio.Event()

        def on_response(response: Response) -> None:
            if find := _requested(response, ctx.url):
                requested.append(find)
                found.set()

        page.on("response", on_response)
        try:
            response = await page.goto(ctx.url, timeout=self.options.timeout * 1000)
        except PlaywrightError as e:
            raise chromium_error(e.message) from e
        status = response.status if response else 200
        retry_after = response.headers.get("retry-after") if response else None
        if error := http_status_error(status, ctx.url, tool=TOOL, retry_after=retry_after):
            raise error
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(found.wait(), timeout=self.options.wait)
        return page, requested

    async def _visit(self, ctx: ProbeContext) -> _Visit:
        """Load ctx.url and see what media it has. Raises UnsupportedURL when it has none."""

        async def visit(context: BrowserContext) -> _Visit:
            page, requested = await self._load(context, ctx)
            streams = [find for find in requested if find.media.kind is not MediaKind.FILE]
            files = [find for find in requested if find.media.kind is MediaKind.FILE]
            finds = unique([*streams, *await _element_finds(page), *files])
            urls = [ctx.url, *(find.media.url for find in finds)]
            return _Visit(
                title=(await page.title()).strip() or None,
                finds=finds,
                cookies=from_playwright(await context.cookies(urls)),
                user_agent=await page.evaluate(_USER_AGENT),
            )

        result = await self._in_chromium(ctx, visit)
        if not result.finds:
            raise UnsupportedURL(f"Found no media on {ctx.url}", tool=TOOL)
        return result
