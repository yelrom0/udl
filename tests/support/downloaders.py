"""
------------------
- downloaders.py -
------------------

FakeDownloader: a downloader that needs no network or tools. What each URL gives (media or an
error) is scripted in its options (and what it gives when it follows a tag chain), so tests of the
code that picks and runs downloaders can say exactly what happens. It passes the downloader
contract suite, like any real downloader.
"""

# System Imports
import asyncio
from typing import Any

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, PositiveInt

# Local Imports
from udl.chains.models import TagChain
from udl.errors import (
    AuthRequired,
    DownloadError,
    ErrorCode,
    ErrorInfo,
    UnsupportedURL,
    error_from_info,
)
from udl.plugins.downloaders.base import (
    Confidence,
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadProgress,
    DownloadResult,
    MatchResult,
    MediaCandidate,
    ProbeContext,
    ProbeResult,
    cookie_header,
)


class FakeMedia(BaseModel):
    """What one URL downloads to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: bytes = b"fake media"
    filename: str = "media.mp4"
    title: str | None = None
    cookie: str | None = None  # "name=value": without this cookie, AuthRequired
    delay: NonNegativeFloat = 0.0  # seconds to wait before each chunk


class FakeOptions(DownloaderOptions):
    media: dict[str, FakeMedia] = Field(default_factory=dict[str, FakeMedia])
    chain: TagChain | None = None  # a tag chain to follow: then `chained` is the media
    chained: dict[str, FakeMedia] = Field(default_factory=dict[str, FakeMedia])
    errors: dict[str, ErrorCode] = Field(default_factory=dict[str, ErrorCode])  # url -> error
    matches: dict[str, Confidence] = Field(default_factory=dict[str, Confidence])  # offline
    confidence: Confidence = 0.8  # of a successful probe
    chunk_size: PositiveInt = 64 * 1024


class FakeDownloader(Downloader[FakeOptions]):
    """Media and errors come from its options (`chained` when it has a chain). Any other URL is
    unsupported."""

    name = "fake"
    options_model = FakeOptions

    def match(self, url: str) -> MatchResult | None:
        confidence = self.options.matches.get(url)
        return None if confidence is None else MatchResult(confidence=confidence)

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        media = self._media_for(ctx)
        return ProbeResult(
            tool=self.name,
            ok=True,
            confidence=self.options.confidence,
            title=media.title,
            media=[MediaCandidate(url=ctx.url, size_bytes=len(media.content), title=media.title)],
        )

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        media = self._media_for(ctx)
        path = ctx.output_dir / media.filename
        total = len(media.content)
        with path.open("wb") as file:
            for start in range(0, total, self.options.chunk_size):
                if media.delay:
                    await asyncio.sleep(media.delay)
                ctx.cancel.check()
                file.write(media.content[start : start + self.options.chunk_size])
                done = min(start + self.options.chunk_size, total)
                ctx.progress(DownloadProgress(downloaded_bytes=done, total_bytes=total, file=path))
        return DownloadResult(tool=self.name, url=ctx.url, files=[path], title=media.title)

    def _media_for(self, ctx: ProbeContext) -> FakeMedia:
        if code := self.options.errors.get(ctx.url):
            error = error_from_info(ErrorInfo(code=code, message=f"{code} for {ctx.url}"))
            assert isinstance(error, DownloadError), f"{code} is not a download error"
            raise error
        scripted = self.options.chained if self.options.chain else self.options.media
        media = scripted.get(ctx.url)
        if media is None:
            raise UnsupportedURL(f"Nothing scripted for {ctx.url}")
        if media.cookie and media.cookie not in (cookie_header(ctx.cookies, ctx.url) or ""):
            raise AuthRequired(f"{ctx.url} needs the {media.cookie.split('=')[0]} cookie")
        return media


class Lookup:
    """A DownloadContext's downloader_for: a FakeDownloader for every handoff, whatever tool it
    names, with `media` scripted (and `errors`). It remembers the tools asked for."""

    def __init__(self, media: dict[str, bytes], errors: dict[str, ErrorCode] | None = None) -> None:
        self.options = FakeOptions(
            media={url: FakeMedia(content=data) for url, data in media.items()},
            errors=errors or {},
        )
        self.asked: list[str] = []

    def __call__(self, name: str) -> Downloader[Any]:
        self.asked.append(name)
        return FakeDownloader(self.options)
