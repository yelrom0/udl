"""
--------------
- scraper.py -
--------------

The scraper downloader: web pages with media in their HTML (as served: no JavaScript runs, which
is the browser downloader's job). It looks, in this order, for

- <video> and <audio> tags (and their <source>s),
- og:video and og:audio meta tags,
- JSON-LD contentUrls,
- links to media files,
- media and stream playlist URLs in scripts,

and, when a page has none of them, on the pages of its iframes (e.g. an embedded player). It
downloads by handing a find off: a file to http, a stream playlist (HLS / DASH) to yt-dlp. When a
find turns out not to be downloadable, the next one is tried.
"""

# System Imports
import logging
import re
from collections.abc import Iterator
from typing import ClassVar, Final, Self
from urllib.parse import urljoin

# Package Imports
import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    TypeAdapter,
    ValidationError,
)
from selectolax.lexbor import LexborHTMLParser

# Local Imports
from udl.errors import (
    DownloadError,
    ExtractionFailed,
    NotFound,
    ToolUnavailable,
    UnsupportedURL,
)
from udl.plugins.downloaders.base import (
    Confidence,
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
from udl.plugins.downloaders.errors import http_status_error
from udl.plugins.downloaders.http import PAGE_TYPES, http_client
from udl.plugins.downloaders.media import media_type, stream_kind

log = logging.getLogger(__name__)

TOOL: Final[str] = "scraper"

# How sure the scraper is of a find, by where it was found.
_TAG: Final[float] = 0.8  # <video>, <audio>
_META: Final[float] = 0.7  # og:video, JSON-LD
_LINK: Final[float] = 0.5  # <a href>
_SCRIPT: Final[float] = 0.4  # a URL in a script
_IFRAME: Final[float] = 0.1  # less, when found on an iframe's page

# Failures of one find that say nothing about the others: try the next one (at most _TRIES).
_NEXT_FIND: Final = (UnsupportedURL, ExtractionFailed, NotFound, ToolUnavailable)
_TRIES: Final[int] = 5

_URL_IN_SCRIPT: Final = re.compile(r"""https?://[^\s"'<>]+""")
_JSON: Final = TypeAdapter[JsonValue](JsonValue)


class ScraperOptions(DownloaderOptions):
    """[tools.scraper] in the config."""

    timeout: PositiveFloat = 30.0  # seconds to connect, or to wait for more data
    retries: NonNegativeInt = 3  # times to retry a failed connection
    max_iframes: NonNegativeInt = 3  # iframes searched when a page has no media itself
    max_page_bytes: PositiveInt = 10 * 1024 * 1024  # what is read of a page, at most


class _Find(BaseModel):
    """Media found on a page."""

    model_config = ConfigDict(frozen=True)

    media: MediaCandidate
    confidence: Confidence
    referer: str  # the page it is on

    def in_iframe(self) -> Self:
        return self.model_copy(
            update={
                "media": self.media.model_copy(
                    update={"found_by": f"{self.media.found_by} in <iframe>"}
                ),
                "confidence": max(self.confidence - _IFRAME, 0.0),
            }
        )


class _Scraped(BaseModel):
    """What a page has: its title, its media (best first) and the cookies its site set."""

    model_config = ConfigDict(frozen=True)

    title: str | None
    finds: list[_Find]
    cookies: list[Cookie]

    def handoff(self, find: _Find) -> Handoff:
        tool = "http" if find.media.kind is MediaKind.FILE else "yt-dlp"
        return Handoff(
            tool=tool, url=find.media.url, headers={"Referer": find.referer}, cookies=self.cookies
        )


# ---------------------------------------------------------------------------------------------
# Finding media in a page
# ---------------------------------------------------------------------------------------------


class _Finder:
    """Collects a page's media, in the order it is found."""

    def __init__(self, tree: LexborHTMLParser, url: str) -> None:
        self.tree = tree
        self.referer = url
        base = tree.css_first("base[href]")
        self.base = urljoin(url, (base and base.attributes.get("href")) or "")
        self.finds: list[_Find] = []

    def add(
        self, src: str | None, found_by: str, confidence: float, content_type: str | None = None
    ) -> None:
        if not src:
            return
        url = urljoin(self.base, src.strip())
        if url == self.referer:  # (what e.g. src="http://" comes to)
            return
        try:
            media = MediaCandidate(
                url=url,
                kind=stream_kind(url, content_type) or MediaKind.FILE,
                content_type=content_type or None,
                found_by=found_by,
            )
        except ValidationError:  # not an http(s) URL, e.g. blob: or data:
            return
        self.finds.append(_Find(media=media, confidence=confidence, referer=self.referer))

    def add_if_media(self, url: str, found_by: str, confidence: float) -> None:
        """Add `url` if its extension is a video's, an audio file's or a stream playlist's."""
        absolute = urljoin(self.base, url)
        kind = media_type(absolute) or ""
        if kind.startswith(("video/", "audio/")) or stream_kind(absolute):
            self.add(absolute, found_by, confidence)

    def find(self) -> list[_Find]:
        for tag in ("video", "audio"):
            for node in self.tree.css(f"{tag}[src], {tag} source[src]"):
                self.add(node.attributes.get("src"), f"<{tag}>", _TAG, node.attributes.get("type"))
        for medium in ("video", "audio"):
            for url in self._open_graph(medium):
                self.add(url, f"og:{medium}", _META)
        for node in self.tree.css('script[type="application/ld+json"]'):
            try:
                data = _JSON.validate_json(node.text())
            except ValidationError:
                continue
            for url in _content_urls(data):
                self.add(url, "json-ld", _META)
        for node in self.tree.css("a[href]"):
            self.add_if_media(node.attributes.get("href") or "", "<a>", _LINK)
        for node in self.tree.css("script"):
            if node.attributes.get("src") is None:
                for match in _URL_IN_SCRIPT.finditer(node.text().replace("\\/", "/")):
                    self.add_if_media(match.group().rstrip(").,;"), "<script>", _SCRIPT)
        return self.finds

    def _open_graph(self, medium: str) -> list[str]:
        """og:<medium> URLs, except those whose og:<medium>:type says they are a web page (an
        embedded player)."""
        urls: list[tuple[str, str]] = []  # (url, its type)
        for node in self.tree.css(f'meta[property^="og:{medium}"]'):
            prop, content = node.attributes.get("property"), node.attributes.get("content") or ""
            if prop in (f"og:{medium}", f"og:{medium}:url", f"og:{medium}:secure_url"):
                urls.append((content, ""))
            elif prop == f"og:{medium}:type" and urls:
                urls[-1] = (urls[-1][0], content)
        return [url for url, kind in urls if kind.partition(";")[0].strip() not in PAGE_TYPES]

    def title(self) -> str | None:
        og_title = self.tree.css_first('meta[property="og:title"]')
        title = og_title.attributes.get("content") if og_title else None
        if not title and (node := self.tree.css_first("title")):
            title = node.text()
        return (title.strip() or None) if title else None

    def iframes(self) -> list[str]:
        sources = (node.attributes.get("src") for node in self.tree.css("iframe[src]"))
        urls = (urljoin(self.base, src.strip()) for src in sources if src)
        return [url for url in urls if url.startswith(("http://", "https://"))]


def _content_urls(data: JsonValue) -> Iterator[str]:
    """Every contentUrl in a JSON-LD document."""
    if isinstance(data, list):
        for item in data:
            yield from _content_urls(item)
    elif isinstance(data, dict):
        if isinstance(url := data.get("contentUrl"), str):
            yield url
        for value in data.values():
            yield from _content_urls(value)


def _unique(finds: list[_Find]) -> list[_Find]:
    """`finds` with each URL once (where it was found first)."""
    seen: set[str] = set()
    unique: list[_Find] = []
    for find in finds:
        if find.media.url not in seen:
            seen.add(find.media.url)
            unique.append(find)
    return unique


# ---------------------------------------------------------------------------------------------
# The downloader
# ---------------------------------------------------------------------------------------------


class ScraperDownloader(Downloader[ScraperOptions]):
    """Finds the media in a web page's HTML, and has http or yt-dlp download it."""

    name = TOOL
    options_model = ScraperOptions
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.module("selectolax", hint="Install selectolax: `uv add selectolax`."),
        Requirement.module("httpx", hint="Install httpx: `uv add httpx`."),
    )

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        page = await self._scrape(ctx)
        return ProbeResult(
            tool=TOOL,
            ok=True,
            confidence=page.finds[0].confidence,
            title=page.title,
            media=[find.media for find in page.finds],
            handoff=page.handoff(page.finds[0]),
        )

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        page = await self._scrape(ctx)
        failures: list[DownloadError] = []
        for find in page.finds[:_TRIES]:
            try:
                result = await self.hand_off(page.handoff(find), ctx)
            except _NEXT_FIND as e:
                log.info("Could not download %s (%s); trying the next find", find.media.url, e)
                failures.append(e)
                continue
            return result.model_copy(update={"title": page.title or result.title})
        raise failures[0]

    async def _scrape(self, ctx: ProbeContext) -> _Scraped:
        """The media on ctx.url's page (on its iframes' pages if none is on the page itself).
        Raises UnsupportedURL when there is none."""

        async def scrape() -> _Scraped:
            async with http_client(
                headers=ctx.headers,
                cookies=ctx.cookies,
                seconds=self.options.timeout,
                retries=self.options.retries,
                tool=TOOL,
            ) as client:
                page = await self._page(client, ctx.url)
                finds = page.find()
                for url in [] if finds else page.iframes()[: self.options.max_iframes]:
                    try:
                        frame = await self._page(client, url)
                    except DownloadError as e:
                        log.debug("Skipped the iframe %s: %s", url, e)
                        continue
                    finds += [find.in_iframe() for find in frame.find()]
                cookies = [Cookie.from_http_cookie(cookie) for cookie in client.cookies.jar]
            if not finds:
                raise UnsupportedURL(
                    f"Found no media on {ctx.url}",
                    tool=TOOL,
                    hint="Its media may need JavaScript: try the browser downloader.",
                )
            return _Scraped(title=page.title(), finds=_unique(finds), cookies=cookies)

        return await cancellable(scrape(), ctx.cancel)

    async def _page(self, client: httpx.AsyncClient, url: str) -> _Finder:
        """The page at `url`, parsed. Raises UnsupportedURL when it is not a web page."""
        async with client.stream("GET", url) as response:
            status = response.status_code
            if error := http_status_error(
                status, url, tool=TOOL, retry_after=response.headers.get("Retry-After")
            ):
                raise error
            content_type = response.headers.get("Content-Type", "").partition(";")[0]
            if content_type.strip().lower() not in PAGE_TYPES:
                raise UnsupportedURL(
                    f"{url} is not a web page ({content_type or 'no type'})",
                    tool=TOOL,
                    hint="If it is a file, http downloads it.",
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) >= self.options.max_page_bytes:
                    break
            html = bytes(body[: self.options.max_page_bytes]).decode(
                response.charset_encoding or "utf-8", errors="replace"
            )
            return _Finder(LexborHTMLParser(html), str(response.url))
