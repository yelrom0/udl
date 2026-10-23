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

Given a site's tag chain (its `chain` option), it follows the chain to the media instead, reading
the pages over HTTP as they are served.
"""

# System Imports
import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from typing import ClassVar, Final
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
    field_validator,
)
from selectolax.lexbor import LexborHTMLParser

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
from udl.errors import ConfigError, DownloadError, UnsupportedURL
from udl.plugins.downloaders.base import (
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
from udl.plugins.downloaders.errors import http_status_error
from udl.plugins.downloaders.http import PAGE_TYPES, USER_AGENT, http_client
from udl.plugins.downloaders.media import Find, media_type, page_title, stream_kind, unique
from udl.plugins.requirements import Requirement

log = logging.getLogger(__name__)

TOOL: Final[str] = "scraper"

# How sure the scraper is of a find, by where it was found.
_TAG: Final[float] = 0.8  # <video>, <audio>
_META: Final[float] = 0.7  # og:video, JSON-LD
_LINK: Final[float] = 0.5  # <a href>
_SCRIPT: Final[float] = 0.4  # a URL in a script
_IFRAME: Final[float] = 0.1  # less, when found on an iframe's page

_URL_IN_SCRIPT: Final = re.compile(r"""https?://[^\s"'<>]+""")
_JSON: Final = TypeAdapter[JsonValue](JsonValue)


class ScraperOptions(DownloaderOptions):
    """[tools.scraper] in the config."""

    timeout: PositiveFloat = 30.0  # seconds to connect, or to wait for more data
    retries: NonNegativeInt = 3  # times to retry a failed connection
    max_iframes: NonNegativeInt = 3  # iframes searched when a page has no media itself
    max_page_bytes: PositiveInt = 10 * 1024 * 1024  # what is read of a page, at most
    user_agent: str = USER_AGENT  # sent unless the download has one (e.g. the browser's)
    # The site's tag chain (`udl chain set`), followed to the media instead of looking for it.
    chain: TagChain | None = None

    @field_validator("chain")
    @classmethod
    def _static(cls, chain: TagChain | None) -> TagChain | None:
        if chain is not None and chain.fetch is FetchMode.BROWSER:
            raise ValueError("a browser chain runs in the browser downloader, not the scraper")
        return chain


class _Scraped(BaseModel):
    """What a page has: its title, its media (best first) and the cookies its site set."""

    model_config = ConfigDict(frozen=True)

    title: str | None
    finds: list[Find]
    cookies: list[Cookie]


def _in_iframe(find: Find) -> Find:
    media = find.media.model_copy(update={"found_by": f"{find.media.found_by} in <iframe>"})
    return find.model_copy(
        update={"media": media, "confidence": max(find.confidence - _IFRAME, 0.0)}
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
        self.finds: list[Find] = []

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
        self.finds.append(Find(media=media, confidence=confidence, referer=self.referer))

    def add_if_media(self, url: str, found_by: str, confidence: float) -> None:
        """Add `url` if its extension is a video's, an audio file's or a stream playlist's."""
        absolute = urljoin(self.base, url)
        kind = media_type(absolute) or ""
        if kind.startswith(("video/", "audio/")) or stream_kind(absolute):
            self.add(absolute, found_by, confidence)

    def find(self) -> list[Find]:
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
        return page_title(self.tree)

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


async def _body(response: httpx.Response, max_bytes: int) -> str:
    """A response's body as text: its first `max_bytes` at most."""
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) >= max_bytes:
            break
    return bytes(body[:max_bytes]).decode(response.charset_encoding or "utf-8", errors="replace")


class _ChainReader(Reader):
    """Opens a chain's documents over HTTP, as they are served."""

    def __init__(self, client: httpx.AsyncClient, max_bytes: int) -> None:
        self._client = client
        self._max_bytes = max_bytes

    async def open(self, url: str, *, referer: str | None) -> Document:
        # (The chain's own Referer, if it has one, is sent instead.)
        headers = {"Referer": referer} if referer and "Referer" not in self._client.headers else {}
        async with self._client.stream("GET", url, headers=headers) as response:
            status = response.status_code
            if error := http_status_error(
                status, url, tool=TOOL, retry_after=response.headers.get("Retry-After")
            ):
                raise error
            return StaticDocument(str(response.url), await _body(response, self._max_bytes))


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
        if (chain := self.options.chain) is not None:
            return probe_result(TOOL, *await self._follow(chain, ctx))
        page = await self._scrape(ctx)
        return ProbeResult(
            tool=TOOL,
            ok=True,
            confidence=page.finds[0].confidence,
            title=page.title,
            media=[find.media for find in page.finds],
            handoff=page.finds[0].handoff(cookies=page.cookies),
        )

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        if (chain := self.options.chain) is not None:
            run, handoffs = await self._follow(chain, ctx)
            hand_off = self.hand_off_all if run.every else self.hand_off_best
            result = await hand_off(handoffs, ctx)
            return result.model_copy(update={"title": run.title or result.title})
        page = await self._scrape(ctx)
        handoffs = [find.handoff(cookies=page.cookies) for find in page.finds]
        result = await self.hand_off_best(handoffs, ctx)
        return result.model_copy(update={"title": page.title or result.title})

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
        """Where `chain` leads from ctx.url, and the downloads of its finds (with the cookies
        the site set on the way)."""

        async def follow() -> tuple[ChainRun, list[Handoff]]:
            async with self._client(ctx, chain_headers(chain, ctx.url)) as client:
                reader = _ChainReader(client, self.options.max_page_bytes)
                run = await run_chain(chain, ctx.url, reader, trace=trace)
                cookies = [Cookie.from_http_cookie(cookie) for cookie in client.cookies.jar]
            return run, chain_handoffs(chain, run, url=ctx.url, cookies=cookies)

        return await cancellable(follow(), ctx.cancel)

    async def suggest_chains(self, ctx: ProbeContext) -> list[ChainSuggestion]:
        """Chains to the media on ctx.url's page as it is served, and its iframes' pages (see
        udl.chains.suggest). Raises UnsupportedURL when it is not a web page."""

        async def read() -> dict[str, Document]:
            async with self._client(ctx) as client:
                url, html = await self._fetch(client, ctx.url)
                pages: dict[str, Document] = {ctx.url: StaticDocument(url, html)}
                frames = _Finder(LexborHTMLParser(html), url).iframes()
                for frame in frames[: self.options.max_iframes]:
                    try:
                        pages[frame] = StaticDocument(*await self._fetch(client, frame))
                    except DownloadError as e:
                        log.debug("Skipped the iframe %s: %s", frame, e)
            return pages

        pages = await cancellable(read(), ctx.cancel)
        return await suggest(ctx.url, pages, fetch=FetchMode.STATIC)

    def _client(
        self, ctx: ProbeContext, headers: Mapping[str, str] | None = None
    ) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        return http_client(
            headers={**ctx.headers, **(headers or {})},
            user_agent=self.options.user_agent,
            cookies=ctx.cookies,
            seconds=self.options.timeout,
            retries=self.options.retries,
            tool=TOOL,
        )

    async def _scrape(self, ctx: ProbeContext) -> _Scraped:
        """The media on ctx.url's page (on its iframes' pages if none is on the page itself).
        Raises UnsupportedURL when there is none."""

        async def scrape() -> _Scraped:
            async with self._client(ctx) as client:
                page = await self._page(client, ctx.url)
                finds = page.find()
                for url in [] if finds else page.iframes()[: self.options.max_iframes]:
                    try:
                        frame = await self._page(client, url)
                    except DownloadError as e:
                        log.debug("Skipped the iframe %s: %s", url, e)
                        continue
                    finds += [_in_iframe(find) for find in frame.find()]
                cookies = [Cookie.from_http_cookie(cookie) for cookie in client.cookies.jar]
            if not finds:
                raise UnsupportedURL(
                    f"Found no media on {ctx.url}",
                    tool=TOOL,
                    hint="Its media may need JavaScript: try the browser downloader.",
                )
            return _Scraped(title=page.title(), finds=unique(finds), cookies=cookies)

        return await cancellable(scrape(), ctx.cancel)

    async def _page(self, client: httpx.AsyncClient, url: str) -> _Finder:
        """The page at `url`, parsed. Raises UnsupportedURL when it is not a web page."""
        url, html = await self._fetch(client, url)
        return _Finder(LexborHTMLParser(html), url)

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> tuple[str, str]:
        """The web page at `url`: where it is (after redirects), and its HTML. Raises
        UnsupportedURL when it is not a web page."""
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
            return str(response.url), await _body(response, self.options.max_page_bytes)
