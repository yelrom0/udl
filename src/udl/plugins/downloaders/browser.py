"""
--------------
- browser.py -
--------------

The browser downloader: pages whose media only shows up in a real browser (added by scripts,
behind a login or a challenge). It loads the page in Chromium (Playwright), records the media and
stream playlists the page requests (in any frame), reads its media elements' sources, and hands
the best find off, a file to http and a stream playlist to yt-dlp, with the browser's cookies,
its User-Agent and the Referer the browser sent.

Chromium runs on udl's persistent profile (where `udl browser login` keeps logins) unless the
profile_dir option is None. One Chromium at a time uses a profile: others wait their turn.
"""

# System Imports
import asyncio
import contextlib
import threading
from collections.abc import AsyncGenerator, Awaitable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Final

# Package Imports
from playwright.async_api import BrowserContext, Page, Response, async_playwright
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
from udl.config import ExpandedPath, browser_profile_dir
from udl.errors import DownloadError, ToolUnavailable, UnsupportedURL
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
    Requirement,
    cancellable,
)
from udl.plugins.downloaders.errors import chromium_error, http_status_error
from udl.plugins.downloaders.media import Find, stream_kind, unique, url_name

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


class BrowserOptions(DownloaderOptions):
    """[tools.browser] in the config."""

    headless: bool = True
    # The persistent profile, which keeps logins; None: a new, empty profile each time.
    profile_dir: ExpandedPath | None = Field(default_factory=browser_profile_dir)
    user_agent: str | None = None  # None: Chromium's own
    timeout: PositiveFloat = 30.0  # seconds for a page to load
    wait: NonNegativeFloat = 2.0  # seconds to wait, once it has, for it to request media


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
        visit = await self._visit(ctx)
        result = await self.hand_off_best(visit.handoffs(), ctx)
        return result.model_copy(update={"title": visit.title or result.title})

    async def _visit(self, ctx: ProbeContext) -> _Visit:
        """Load ctx.url and see what media it has. Raises UnsupportedURL when it has none."""
        options = self.options

        async def visit() -> _Visit:
            async with chromium(
                profile_dir=options.profile_dir,
                headless=options.headless,
                user_agent=options.user_agent,
                cancel=ctx.cancel,
            ) as context:
                await add_cookies(context, ctx.cookies)
                await context.set_extra_http_headers(ctx.headers)
                page = await context.new_page()
                requested: list[Find] = []
                found = asyncio.Event()

                def on_response(response: Response) -> None:
                    if find := _requested(response, ctx.url):
                        requested.append(find)
                        found.set()

                page.on("response", on_response)
                try:
                    response = await page.goto(ctx.url, timeout=options.timeout * 1000)
                except PlaywrightError as e:
                    raise chromium_error(e.message) from e
                status = response.status if response else 200
                retry_after = response.headers.get("retry-after") if response else None
                if error := http_status_error(status, ctx.url, tool=TOOL, retry_after=retry_after):
                    raise error
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(found.wait(), timeout=options.wait)
                streams = [find for find in requested if find.media.kind is not MediaKind.FILE]
                files = [find for find in requested if find.media.kind is MediaKind.FILE]
                finds = unique([*streams, *await _element_finds(page), *files])
                urls = [ctx.url, *(find.media.url for find in finds)]
                return _Visit(
                    title=(await page.title()).strip() or None,
                    finds=finds,
                    cookies=from_playwright(await context.cookies(urls)),
                    user_agent=await page.evaluate("() => navigator.userAgent"),
                )

        result = await cancellable(visit(), ctx.cancel)
        if not result.finds:
            raise UnsupportedURL(f"Found no media on {ctx.url}", tool=TOOL)
        return result
