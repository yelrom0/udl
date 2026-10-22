"""
------------
- ytdlp.py -
------------

The yt-dlp downloader: yt-dlp's Python API, run in a worker thread. A new YoutubeDL is made for
every probe and download; cancelling raises from its progress hook.
"""

# System Imports
import functools
import http.cookiejar
import logging
import urllib.request
import weakref
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final, TypedDict, cast

# Package Imports
import yt_dlp
from pydantic import Field, JsonValue, NonNegativeInt, PositiveInt
from yt_dlp.extractor import gen_extractor_classes
from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.networking import Response
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import DownloadCancelled
from yt_dlp.utils import DownloadError as YtDlpDownloadError

# Local Imports
from udl.errors import Cancelled, DownloadError
from udl.plugins.downloaders.base import (
    CancelToken,
    Cookie,
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadProgress,
    DownloadResult,
    MatchResult,
    MediaCandidate,
    MediaKind,
    ProbeContext,
    ProbeResult,
    ProgressSink,
    Requirement,
    run_blocking,
    template_parts,
    threadsafe,
)
from udl.plugins.downloaders.errors import ytdlp_error

if TYPE_CHECKING:
    from yt_dlp import _YoutubeDLOptions  # pyright: ignore[reportPrivateUsage]

log = logging.getLogger(__name__)

# How sure a probe is: a site's own extractor knows the site; the generic one is guessing.
_SITE_EXTRACTOR: Final[float] = 0.9
_GENERIC_EXTRACTOR: Final[float] = 0.3
_DEFAULT_OUTTMPL: Final[str] = "%(title)s [%(id)s].%(ext)s"


class YtDlpOptions(DownloaderOptions):
    """[tools.yt-dlp] in the config."""

    format: str | None = None  # yt-dlp's format selector, e.g. "bv*+ba/b"; None: its default
    noplaylist: bool = False  # for a video in a playlist, download just the video
    retries: NonNegativeInt = 3
    socket_timeout: PositiveInt = 30
    # Anything else, passed to YoutubeDL as-is (see `yt_dlp.YoutubeDL`'s docstring); it wins
    # over everything udl sets.
    params: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


# The parts of yt-dlp's info dict that udl reads.
class _RequestedDownload(TypedDict, total=False):
    filepath: str


class _Info(TypedDict, total=False):
    id: str
    title: str
    ext: str
    url: str
    webpage_url: str
    protocol: str
    filesize: int | None
    filesize_approx: int | None
    entries: list[_Info | None]
    requested_downloads: list[_RequestedDownload]


class _Logger:
    """Sends yt-dlp's output to udl's log: its warnings as info, the rest as debug (its errors
    are raised as well)."""

    def __init__(self, ydl: yt_dlp.YoutubeDL | None = None) -> None:
        pass

    def debug(self, message: str) -> None:
        log.debug("%s", message)

    def warning(self, message: str, *, once: bool = False, only_once: bool = False) -> None:
        log.info("%s", message)

    info = error = stdout = stderr = debug


class _YoutubeDL(yt_dlp.YoutubeDL):
    """A YoutubeDL that closes every response it opened when it closes. A download cancelled
    from a progress hook leaves its response (and socket) open otherwise."""

    def __init__(self, params: _YoutubeDLOptions) -> None:
        super().__init__(params)
        self._responses = weakref.WeakSet[Response]()

    # (yt-dlp's own Request at run time; the stubs say urllib's)
    def urlopen(self, req: urllib.request.Request | str) -> Response:
        response = super().urlopen(req)
        self._responses.add(response)
        return response

    def close(self) -> None:
        for response in list(self._responses):
            response.close()
        super().close()


@functools.cache
def _site_extractors() -> list[type[InfoExtractor]]:
    """yt-dlp's extractors, except the generic one (it "handles" every URL)."""
    return [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]


def outtmpl(template: str | None) -> str:
    """A udl filename template (e.g. "{title}.{ext}") as a yt-dlp output template."""
    if template is None:
        return _DEFAULT_OUTTMPL
    return "".join(
        text.replace("%", "%%") + (f"%({field})s" if field else "")
        for text, field in template_parts(template)
    )


def _jar_cookie(cookie: Cookie) -> http.cookiejar.Cookie:
    domain = cookie.domain
    if "." not in domain.strip("."):
        # The cookie jar matches a dotless host (e.g. localhost) as "localhost.local".
        domain = f"{domain.strip('.')}.local"
    return http.cookiejar.Cookie(
        version=0,
        name=cookie.name,
        value=cookie.value.get_secret_value(),
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path=cookie.path,
        path_specified=True,
        secure=cookie.secure,
        expires=int(cookie.expires.timestamp()) if cookie.expires else None,
        discard=cookie.expires is None,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""} if cookie.http_only else {},
    )


def _error(e: YtDlpDownloadError) -> DownloadError:
    cause = e.exc_info[1] if e.exc_info else None
    if isinstance(cause, HTTPError):
        retry_after = cause.response.headers.get("Retry-After")
        cause.close()  # yt-dlp leaves the response (and its socket) open
        return ytdlp_error(str(e), status=cause.status, retry_after=retry_after)
    return ytdlp_error(str(e))


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and value >= 0 else None


def progress_from_status(status: Mapping[str, Any]) -> DownloadProgress | None:
    """What a yt-dlp progress hook was given, as progress (None when it is not progress)."""
    if status.get("status") not in ("downloading", "finished"):
        return None
    total = status.get("total_bytes") or status.get("total_bytes_estimate")
    filename = status.get("filename")
    return DownloadProgress(
        downloaded_bytes=int(_number(status.get("downloaded_bytes")) or 0),
        total_bytes=int(total) if (total := _number(total)) is not None else None,
        speed=_number(status.get("speed")),
        eta=_number(status.get("eta")),
        file=Path(filename) if isinstance(filename, str) else None,
    )


def _http_url(*urls: str | None) -> str | None:
    return next((url for url in urls if url and url.startswith(("http://", "https://"))), None)


# yt-dlp's download protocols that are not plain files.
_KINDS: Final[dict[str, MediaKind]] = {
    "m3u8": MediaKind.HLS,
    "m3u8_native": MediaKind.HLS,
    "http_dash_segments": MediaKind.DASH,
}


def _entries(info: _Info) -> list[_Info]:
    """The videos in a result: a playlist's entries, or the result itself."""
    if "entries" not in info:
        return [info]
    return [video for entry in info["entries"] if entry for video in _entries(entry)]


class YtDlpDownloader(Downloader[YtDlpOptions]):
    """yt-dlp: video sites (its 1,800 extractors), plus direct media files and HTML5 <video>
    tags through its generic extractor."""

    name = "yt-dlp"
    options_model = YtDlpOptions
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.module("yt_dlp", hint="Install yt-dlp: `uv add yt-dlp`."),
    )

    def match(self, url: str) -> MatchResult | None:
        for extractor in _site_extractors():
            if extractor.suitable(url):
                return MatchResult(confidence=_SITE_EXTRACTOR, detail=extractor.ie_key())
        return None

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        info = await run_blocking(lambda: self._extract(ctx, None), cancel=ctx.cancel)
        media = [
            MediaCandidate(
                url=_http_url(video.get("url"), video.get("webpage_url")) or ctx.url,
                kind=_KINDS.get(video.get("protocol", ""), MediaKind.FILE),
                size_bytes=video.get("filesize") or video.get("filesize_approx"),
                title=video.get("title"),
                found_by="yt-dlp",
            )
            for video in _entries(info)
        ]
        return ProbeResult(
            tool=self.name,
            ok=True,
            confidence=_SITE_EXTRACTOR if self.match(ctx.url) else _GENERIC_EXTRACTOR,
            title=info.get("title"),
            media=media,
        )

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        report = threadsafe(ctx.progress)
        info = await run_blocking(lambda: self._extract(ctx, report), cancel=ctx.cancel)
        files = [
            Path(download["filepath"])
            for video in _entries(info)
            for download in video.get("requested_downloads", [])
            if "filepath" in download
        ]
        return DownloadResult(tool=self.name, url=ctx.url, files=files, title=info.get("title"))

    def _extract(self, ctx: ProbeContext, report: ProgressSink | None) -> _Info:
        """Run yt-dlp (in a worker thread). It downloads when `report` is given."""
        params = self._params(ctx, report)
        try:
            with _YoutubeDL(params) as ydl:
                for cookie in ctx.cookies:
                    ydl.cookiejar.set_cookie(_jar_cookie(cookie))
                info = ydl.extract_info(ctx.url, download=report is not None)
        except DownloadCancelled as e:
            raise Cancelled("The download was cancelled") from e
        except YtDlpDownloadError as e:
            ctx.cancel.check()
            raise _error(e) from e
        return cast(_Info, info)

    def _params(self, ctx: ProbeContext, report: ProgressSink | None) -> _YoutubeDLOptions:
        options = self.options
        params: _YoutubeDLOptions = {
            "logger": _Logger(),
            "quiet": True,
            "noprogress": True,
            "color": {"stdout": "never", "stderr": "never"},
            "cachedir": False,  # pyright: ignore[reportAssignmentType] - False turns the cache off
            "noplaylist": options.noplaylist,
            "retries": options.retries,
            "socket_timeout": options.socket_timeout,
        }
        if ctx.headers:
            params["http_headers"] = dict(ctx.headers)
        if options.format:
            params["format"] = options.format
        if isinstance(ctx, DownloadContext) and report is not None:
            output_dir = str(ctx.output_dir)
            params["paths"] = {"home": output_dir, "temp": output_dir}
            params["outtmpl"] = {"default": outtmpl(ctx.filename_template)}
            params["progress_hooks"] = [_hook(report, ctx.cancel)]
        else:
            params["extract_flat"] = "in_playlist"  # a probe lists a playlist's videos only
        params.update(cast("_YoutubeDLOptions", options.params))
        return params


def _hook(report: ProgressSink, cancel: CancelToken) -> Callable[[Mapping[str, Any]], None]:
    """A progress hook: reports progress, and stops the download once `cancel` is cancelled."""

    def hook(status: Mapping[str, Any]) -> None:
        if cancel.cancelled:
            raise DownloadCancelled()
        # yt-dlp only reports "downloading" and "finished", which are both progress.
        if progress := progress_from_status(status):  # pragma: no branch
            report(progress)

    return hook
