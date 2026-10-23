"""
----------------
- gallerydl.py -
----------------

The gallery-dl downloader, in one of two modes:

- in-process (the default): gallery-dl's extractors and DownloadJob in a worker thread. gallery-dl
  is untyped and its config is global, so its modules are used as untyped values here and
  nowhere else, and one job (or probe) runs at a time, each with a fresh config. If its Python API
  fails (e.g. a gallery-dl upgrade changed it), the command line is used instead.
- subprocess: gallery-dl's command line, one process per probe or download.
"""

# System Imports
import asyncio
import http.cookiejar
import importlib
import json
import logging
import sys
import tempfile
import threading
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Any, ClassVar, Final

# Package Imports
import httpx
from pydantic import Field, JsonValue, NonNegativeInt, PositiveFloat

# Local Imports
from udl.errors import Cancelled, ExtractionFailed, NetworkError, UDLError, UnsupportedURL
from udl.plugins.downloaders.base import (
    CancelToken,
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadProgress,
    DownloadResult,
    MatchResult,
    MediaCandidate,
    ProbeContext,
    ProbeResult,
    ProgressSink,
    cookie_header,
    run_blocking,
    template_parts,
    threadsafe,
)
from udl.plugins.downloaders.errors import gallerydl_error, http_status_error, httpx_error
from udl.plugins.downloaders.http import USER_AGENT, with_user_agent
from udl.plugins.downloaders.process import run_process
from udl.plugins.requirements import Requirement

log = logging.getLogger(__name__)

# gallery-dl has no type information: its modules are untyped values.
_config: ModuleType = importlib.import_module("gallery_dl.config")
_exception: ModuleType = importlib.import_module("gallery_dl.exception")
_extractor: ModuleType = importlib.import_module("gallery_dl.extractor")
_job: ModuleType = importlib.import_module("gallery_dl.job")
_message: ModuleType = importlib.import_module("gallery_dl.extractor.message")

TOOL: Final[str] = "gallery-dl"
# How sure a match is: a site's own extractor knows the site; direct links are any media URL.
_SITE_EXTRACTOR: Final[float] = 0.85
_DIRECT_LINK: Final[float] = 0.4
# A HEAD request that fails with these only means the server does not do HEAD.
_HEAD_UNSUPPORTED: Final[frozenset[int]] = frozenset({405, 501})
# udl's template fields that gallery-dl names differently, or not always.
_FIELDS: Final[dict[str, str]] = {"title": "title|filename", "ext": "extension"}
# The ids of the messages in `gallery-dl -j` output that name a file or a child gallery.
_URL_MESSAGES: Final[frozenset[int]] = frozenset({3, 6})  # Message.Url, Message.Queue
_ERROR_MESSAGE: Final[int] = -1  # what stopped the extractor

# gallery-dl's config is global: one in-process job at a time.
_lock: Final = threading.Lock()


class GalleryDlMode(StrEnum):
    IN_PROCESS = "in-process"  # its Python API (its command line if that fails)
    SUBPROCESS = "subprocess"  # its command line


class GalleryDlOptions(DownloaderOptions):
    """[tools.gallery-dl] in the config."""

    mode: GalleryDlMode = GalleryDlMode.IN_PROCESS
    retries: NonNegativeInt = 3
    timeout: PositiveFloat = 30.0
    # gallery-dl config (as in its config.json), applied over what udl sets, e.g.
    # {"extractor": {"twitter": {"videos": false}}}.
    config: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


def gallerydl_format(template: str) -> tuple[list[str], str]:
    """A udl filename template as gallery-dl's directory (one format per folder) and filename."""
    formatted = "".join(
        text.replace("{", "{{").replace("}", "}}")
        + ("" if field is None else f"{{{_FIELDS.get(field, field)}}}")
        for text, field in template_parts(template)
    )
    *directory, filename = formatted.split("/")
    return directory, filename


def _merged(base: Mapping[str, JsonValue], over: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """`base` with `over` on top, merging tables into tables."""
    merged = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(current := merged.get(key), dict):
            merged[key] = _merged(current, value)
        else:
            merged[key] = value
    return merged


def _settings(options: GalleryDlOptions, ctx: ProbeContext) -> dict[str, JsonValue]:
    """The gallery-dl config for a probe or download (as in its config.json)."""
    extractor: dict[str, JsonValue] = {
        "retries": options.retries,
        "timeout": options.timeout,
        "keywords-default": "NA",
        "directory": [],  # files go straight into output_dir
    }
    settings: dict[str, JsonValue] = {
        "extractor": extractor,
        "downloader": {"progress": 0.0, "part": True},  # progress from the start; resumable
    }
    if isinstance(ctx, DownloadContext):
        settings["base-directory"] = str(ctx.output_dir)
        if ctx.filename_template:
            directory, filename = gallerydl_format(ctx.filename_template)
            extractor.update({"directory": list[JsonValue](directory), "filename": filename})
    return _merged(settings, options.config)


def _size_if_reachable(status: int, url: str, headers: Mapping[str, str]) -> int | None:
    """A HEAD response's file size, raising the error its status means (if it is one)."""
    error = http_status_error(status, url, tool=TOOL, retry_after=headers.get("Retry-After"))
    if error is not None and status not in _HEAD_UNSUPPORTED:
        raise error
    length = headers.get("Content-Length")
    return int(length) if length and length.isdigit() else None


def _probe_result(downloader: GalleryDlDownloader, ctx: ProbeContext, item: Any) -> ProbeResult:
    """A probe's result, from the first item (URL, metadata) the extractor found."""
    url, metadata, size = item
    found = metadata.get("title") or metadata.get("filename")
    title = str(found) if found else None
    match = downloader.match(ctx.url)
    return ProbeResult(
        tool=TOOL,
        ok=True,
        confidence=match.confidence if match else _SITE_EXTRACTOR,
        title=title,
        # Not e.g. "ytdl:" URLs, which gallery-dl hands to yt-dlp.
        media=[MediaCandidate(url=url, size_bytes=size, title=title, found_by=TOOL)]
        if _is_web(url)
        else [],
    )


def _is_web(url: str) -> bool:
    return url.startswith(("http://", "https://"))


# ---------------------------------------------------------------------------------------------
# In-process: gallery-dl's Python API
# ---------------------------------------------------------------------------------------------


class _Log:
    """gallery-dl's logger adapter, for udl: a job's log goes to udl's log (its warnings and
    errors as info) instead of to the root logger, and its warnings and errors are kept, to
    explain a failure."""

    def __init__(self, logger: logging.Logger, messages: list[str]) -> None:
        self.logger = logger
        self.messages = messages

    def _text(self, msg: object, args: tuple[object, ...]) -> str:
        return str(msg) % args if args else str(msg)

    def traceback(self, exc: BaseException) -> None:
        log.debug("%s: %s", self.logger.name, exc, exc_info=exc)

    def debug(self, msg: object, *args: object, **_: object) -> None:
        log.debug("%s: %s", self.logger.name, self._text(msg, args))

    info = debug

    def warning(self, msg: object, *args: object, **_: object) -> None:
        text = self._text(msg, args)
        self.messages.append(text)
        log.info("%s: %s", self.logger.name, text)

    error = warning


class _Output:
    """Where a job reports the files it writes: udl collects them, reports progress and stops
    the job once the download is cancelled."""

    def __init__(self, report: ProgressSink, cancel: CancelToken) -> None:
        self.report = report
        self.cancel = cancel
        self.files: list[Path] = []

    def _check(self) -> None:
        if self.cancel.cancelled:
            # gallery-dl closes the response and ends the job for this one.
            raise _exception.TerminateExtraction()

    def start(self, path: str) -> None:
        self._check()

    def skip(self, path: str) -> None:
        self._check()
        if (file := Path(path)).is_file():  # already downloaded (else skipped, e.g. too small)
            self.files.append(file)

    def success(self, path: str) -> None:
        file = Path(path)
        self.files.append(file)
        size = file.stat().st_size
        self.report(DownloadProgress(downloaded_bytes=size, total_bytes=size, file=file))

    def progress(self, bytes_total: object, bytes_downloaded: int, bytes_per_second: int) -> None:
        self._check()
        total = int(bytes_total) if isinstance(bytes_total, int | str) and bytes_total else None
        self.report(
            DownloadProgress(
                downloaded_bytes=bytes_downloaded, total_bytes=total, speed=bytes_per_second
            )
        )


def _set_config(path: tuple[str, ...], values: Mapping[str, JsonValue]) -> None:
    """Merge `values` into gallery-dl's config at `path` (tables into tables)."""
    for key, value in values.items():
        if isinstance(value, dict):
            _set_config((*path, key), value)
        else:
            _config.set(path, key, value)


@contextmanager
def _gallery_dl(settings: Mapping[str, JsonValue], cancel: CancelToken) -> Generator[None]:
    """Hold gallery-dl (waiting for any other job to finish), configured with `settings`."""
    while not _lock.acquire(timeout=0.1):
        cancel.check()
    try:
        cancel.check()
        _config.clear()
        _set_config((), settings)
        yield
    finally:
        _config.clear()
        _lock.release()


def _extractor_for(ctx: ProbeContext) -> Any:
    """gallery-dl's extractor for ctx.url, ready to use with ctx's headers and cookies."""
    extractor = _extractor.find(ctx.url)
    if extractor is None:
        raise UnsupportedURL(f"gallery-dl has no extractor for {ctx.url}", tool=TOOL)
    extractor.initialize()
    extractor.session.headers.update(ctx.headers)
    for cookie in ctx.cookies:
        extractor.cookies.set_cookie(cookie.to_http_cookie())
    return extractor


def _first_item(extractor: Any, timeout: float) -> tuple[str, dict[str, Any], int | None]:
    """The first file (or child gallery) the extractor finds: its URL, metadata and size. The
    file must be there (HEAD)."""
    try:
        for message in extractor:
            if message[0] in (_message.Message.Url, _message.Message.Queue):
                url: str = message[1]
                return url, message[2], _head(extractor, url, timeout) if _is_web(url) else None
    except _exception.GalleryDLException as e:
        raise gallerydl_error(e.code, [f"{type(e).__name__}: {e}"]) from e
    raise ExtractionFailed(f"gallery-dl found nothing at {extractor.url}", tool=TOOL)


def _head(extractor: Any, url: str, timeout: float) -> int | None:
    try:
        with extractor.session.head(url, allow_redirects=True, timeout=timeout) as response:
            return _size_if_reachable(response.status_code, url, response.headers)
    except OSError as e:  # requests' errors are OSErrors
        raise NetworkError(f"{type(e).__name__}: {e}", tool=TOOL) from e


def _run_job(extractor: Any, output: _Output, messages: list[str]) -> int:
    """Run a gallery-dl DownloadJob; returns its exit status (0 when all went well)."""
    job = _job.DownloadJob(extractor)

    def wrap(logger: logging.Logger) -> _Log:
        return _Log(logger, messages)

    job._wrap_logger = wrap
    job.log = wrap(job.log.logger)
    extractor.log = wrap(extractor.log.logger)
    job.out = output
    try:
        return int(job.run())
    except _exception.TerminateExtraction:
        return 1


# ---------------------------------------------------------------------------------------------
# Subprocess: gallery-dl's command line
# ---------------------------------------------------------------------------------------------


def _cli_args(ctx: ProbeContext, settings: Mapping[str, JsonValue], folder: Path) -> list[str]:
    """The gallery-dl command line for ctx, with its config (and cookies) written to `folder`."""
    cli_settings = _merged(
        settings,
        {
            "extractor": {"headers": dict[str, JsonValue](ctx.headers)},
            "output": {"mode": "pipe", "log": "[{name}][{levelname}] {message}"},
        },
    )
    config_file = folder / "config.json"
    config_file.write_text(json.dumps(cli_settings))
    args = [sys.executable, "-m", "gallery_dl", "--config-ignore", "--no-input"]
    args += ["-c", str(config_file)]
    if ctx.cookies:
        jar = http.cookiejar.MozillaCookieJar(folder / "cookies.txt")
        for cookie in ctx.cookies:
            jar.set_cookie(cookie.to_http_cookie())
        jar.save(ignore_discard=True, ignore_expires=True)
        args += ["-C", str(jar.filename)]
    return args


async def _cli_head(ctx: ProbeContext, url: str, options: GalleryDlOptions) -> int | None:
    headers = with_user_agent(ctx.headers, USER_AGENT)
    if cookies := cookie_header(ctx.cookies, url):
        headers["Cookie"] = cookies
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=options.timeout) as client:
            response = await client.head(url, headers=headers)
    except httpx.HTTPError as e:
        raise httpx_error(e, tool=TOOL) from e
    return _size_if_reachable(response.status_code, url, response.headers)


class _NewFiles:
    """The size of what a download has written so far: the files in its output dir that were
    not there before it started."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.before = set(self._files())

    def _files(self) -> list[Path]:
        return [path for path in self.folder.rglob("*") if path.is_file()]

    def size(self) -> int:
        return sum(path.stat().st_size for path in self._files() if path not in self.before)


# ---------------------------------------------------------------------------------------------
# The downloader
# ---------------------------------------------------------------------------------------------


class GalleryDlDownloader(Downloader[GalleryDlOptions]):
    """gallery-dl: image and gallery sites (and direct links to media files)."""

    name = TOOL
    options_model = GalleryDlOptions
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.module("gallery_dl", hint="Install gallery-dl: `uv add gallery-dl`."),
    )

    def match(self, url: str) -> MatchResult | None:
        extractor = _extractor.find(url)
        if extractor is None:
            return None
        category: str = extractor.category
        confidence = _DIRECT_LINK if category == "directlink" else _SITE_EXTRACTOR
        return MatchResult(confidence=confidence, detail=f"{category}:{extractor.subcategory}")

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        if self.options.mode is GalleryDlMode.IN_PROCESS:
            try:
                return await run_blocking(lambda: self._probe_in_process(ctx), cancel=ctx.cancel)
            except UDLError:
                raise
            except Exception as e:  # noqa: BLE001 - its API failed: use its command line
                _api_failed(e)
        return await self._probe_cli(ctx)

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        if self.options.mode is GalleryDlMode.IN_PROCESS:
            report = threadsafe(ctx.progress)
            try:
                return await run_blocking(
                    lambda: self._download_in_process(ctx, report), cancel=ctx.cancel
                )
            except UDLError:
                raise
            except Exception as e:  # noqa: BLE001 - its API failed: use its command line
                _api_failed(e)
        return await self._download_cli(ctx)

    # -- in-process --------------------------------------------------------------------------

    def _probe_in_process(self, ctx: ProbeContext) -> ProbeResult:
        with _gallery_dl(_settings(self.options, ctx), ctx.cancel):
            extractor = _extractor_for(ctx)
            try:
                item = _first_item(extractor, self.options.timeout)
            finally:
                extractor.session.close()
        return _probe_result(self, ctx, item)

    def _download_in_process(self, ctx: DownloadContext, report: ProgressSink) -> DownloadResult:
        messages: list[str] = []
        output = _Output(report, ctx.cancel)
        with _gallery_dl(_settings(self.options, ctx), ctx.cancel):
            extractor = _extractor_for(ctx)
            try:
                status = _run_job(extractor, output, messages)
            finally:
                extractor.session.close()
        if ctx.cancel.cancelled:
            raise Cancelled("The download was cancelled")
        return self._result(ctx, status, output.files, messages)

    # -- command line ------------------------------------------------------------------------

    async def _probe_cli(self, ctx: ProbeContext) -> ProbeResult:
        with tempfile.TemporaryDirectory(prefix="udl-gallery-dl-") as folder:
            args = _cli_args(ctx, _settings(self.options, ctx), Path(folder))
            run = await run_process([*args, "--dump-json", ctx.url], cancel=ctx.cancel)
        if run.returncode:
            raise gallerydl_error(run.returncode, run.stderr.splitlines())
        messages: list[Any] = json.loads(run.stdout or "[]")
        for message in messages:
            if message[0] == _ERROR_MESSAGE:
                error = f"{message[1]['error']}: {message[1]['message']}"
                raise gallerydl_error(run.returncode, [error])
            if message[0] in _URL_MESSAGES:
                url: str = message[1]
                size = await _cli_head(ctx, url, self.options) if _is_web(url) else None
                return _probe_result(self, ctx, (url, message[2], size))
        raise ExtractionFailed(f"gallery-dl found nothing at {ctx.url}", tool=TOOL)

    async def _download_cli(self, ctx: DownloadContext) -> DownloadResult:
        written = await asyncio.to_thread(_NewFiles, ctx.output_dir)

        def tick() -> None:
            ctx.progress(DownloadProgress(downloaded_bytes=written.size()))

        with tempfile.TemporaryDirectory(prefix="udl-gallery-dl-") as folder:
            args = _cli_args(ctx, _settings(self.options, ctx), Path(folder))
            run = await run_process([*args, ctx.url], cancel=ctx.cancel, on_tick=tick)
        # Its output is the files it downloaded, and ("# " first) the ones it already had.
        paths = [Path(line.removeprefix("# ")) for line in run.stdout.splitlines() if line]
        files = [path for path in paths if path.is_file()]
        if files:
            size = sum(file.stat().st_size for file in files)
            ctx.progress(DownloadProgress(downloaded_bytes=size, total_bytes=size))
        return self._result(ctx, run.returncode, files, run.stderr.splitlines())

    def _result(
        self, ctx: DownloadContext, status: int, files: list[Path], messages: list[str]
    ) -> DownloadResult:
        if not files and (status or messages):
            raise gallerydl_error(status, messages)
        if status:
            log.warning("gallery-dl could not download everything from %s", ctx.url)
        return DownloadResult(tool=TOOL, url=ctx.url, files=files)


def _api_failed(error: Exception) -> None:
    log.warning(
        "gallery-dl's Python API failed (%s: %s); using its command line instead",
        type(error).__name__,
        error,
    )
