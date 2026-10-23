"""
-----------
- http.py -
-----------

The http downloader: URLs that are themselves files (e.g. https://example.com/clip.mp4).

A probe asks the server what the URL is: a HEAD request, or a GET of its first byte when HEAD is
refused. Anything but a web page (or a stream playlist, which yt-dlp downloads) is a file. A
download streams into `<name>.part` beside its final name and renames it once it is complete; a
`.part` left by an earlier try (cut short or cancelled) is resumed with a Range request.

It talks to servers with httpx, or with curl or wget when the `backend` option picks one of them.
"""

# System Imports
import asyncio
import http.cookiejar
import os
import re
import tempfile
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from email.message import Message
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import ClassVar, Final
from urllib.parse import urljoin

# Package Imports
import httpx
from pydantic import BaseModel, ConfigDict, NonNegativeInt, PositiveFloat, TypeAdapter

# Local Imports
from udl.errors import ExtractionFailed, NetworkError, UnsupportedURL
from udl.plugins.downloaders.base import (
    CancelToken,
    Confidence,
    Cookie,
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadProgress,
    DownloadResult,
    MatchResult,
    MediaCandidate,
    ProbeContext,
    ProbeResult,
    cancellable,
    render_template,
)
from udl.plugins.downloaders.errors import curl_error, http_status_error, httpx_error, wget_error
from udl.plugins.downloaders.media import (
    MEDIA_KINDS,
    extension_for,
    media_type,
    stream_kind,
    url_name,
)
from udl.plugins.downloaders.process import run_process
from udl.plugins.requirements import Requirement

TOOL: Final[str] = "http"
_DEFAULT_TEMPLATE: Final[str] = "{title}.{ext}"  # the name the server gives the file

# How sure udl is that a URL is a file: offline, from its extension; probed, from its type.
_MATCH: Final[float] = 0.6
_MEDIA: Final[float] = 0.9  # a media type (video/audio/image), or a download (an attachment)
_OTHER: Final[float] = 0.6  # some other file, e.g. application/octet-stream or a PDF
_UNTYPED: Final[float] = 0.4  # the server did not say

PAGE_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})
_EXTENSION: Final = re.compile(r"\.[A-Za-z0-9]{1,8}")
# Statuses a server may answer HEAD with although GET works (403: e.g. a URL signed for GET).
_HEAD_REFUSED: Final = frozenset({403, 405, 501})
_CURL_CANNOT_RESUME: Final[int] = 33  # the server ignores Range


class HttpBackend(StrEnum):
    HTTPX = "httpx"  # in-process
    CURL = "curl"
    WGET = "wget"


class HttpOptions(DownloaderOptions):
    """[tools.http] in the config."""

    backend: HttpBackend = HttpBackend.HTTPX
    timeout: PositiveFloat = 30.0  # seconds to connect, or to wait for more data
    retries: NonNegativeInt = 3  # times to retry a failed connection


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True)


class _Request(_Model):
    """A request to make: its URL, headers and cookies."""

    url: str
    headers: dict[str, str]
    cookies: list[Cookie]


class _Response(_Model):
    """A server's answer (the last one, after redirects)."""

    status: int
    url: str  # the final URL
    headers: dict[str, str]  # names in lower case


class _File(_Model):
    """A file, as a server describes it."""

    url: str
    title: str  # its name without the extension
    ext: str  # e.g. "mp4"
    content_type: str | None
    size: NonNegativeInt | None  # None when the server did not say
    confidence: Confidence


def _request(ctx: ProbeContext) -> _Request:
    # The file as it is stored: a compressed transfer would not match its Content-Length.
    headers = {
        name: value for name, value in ctx.headers.items() if name.lower() != "accept-encoding"
    }
    return _Request(
        url=ctx.url, headers={**headers, "Accept-Encoding": "identity"}, cookies=ctx.cookies
    )


def _disposition(value: str | None) -> tuple[bool, str | None]:
    """A Content-Disposition header: whether it is an attachment, and the file name it gives."""
    if not value:
        return False, None
    message = Message()
    message["Content-Disposition"] = value
    return message.get_content_disposition() == "attachment", message.get_filename()


def _size(response: _Response) -> int | None:
    headers = response.headers
    if headers.get("content-encoding", "identity") != "identity":
        return None  # the length of the compressed transfer
    if response.status == 206:  # the first byte: "bytes 0-0/<size>"
        _, _, total = headers.get("content-range", "").rpartition("/")
        return int(total) if total.isdigit() else None
    length = headers.get("content-length", "")
    return int(length) if length.isdigit() else None


def _file(response: _Response) -> _File:
    """The file a successful response describes. Raises UnsupportedURL for a web page or a
    stream playlist."""
    url = response.url
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    attachment, filename = _disposition(response.headers.get("content-disposition"))
    if stream_kind(url, content_type):
        raise UnsupportedURL(
            f"{url} is a stream playlist", tool=TOOL, hint="yt-dlp downloads streams: try it."
        )
    if content_type in PAGE_TYPES and not attachment:
        raise UnsupportedURL(
            f"{url} is a web page, not a file",
            tool=TOOL,
            hint="The scraper or browser downloader may find media on it.",
        )
    name = PurePosixPath((filename or url_name(url)).replace("\\", "/")).name or "download"
    title, ext = name, None
    if _EXTENSION.fullmatch(suffix := PurePosixPath(name).suffix):
        title, ext = name.removesuffix(suffix), suffix
    ext = ext or (content_type and extension_for(content_type)) or ".bin"
    if attachment or content_type.partition("/")[0] in MEDIA_KINDS:
        confidence = _MEDIA
    elif content_type:
        confidence = _OTHER
    else:
        confidence = _MATCH if media_type(url) else _UNTYPED
    return _File(
        url=url,
        title=title,
        ext=ext.removeprefix("."),
        content_type=content_type or None,
        size=_size(response),
        confidence=confidence,
    )


def _cookie_file(cookies: list[Cookie], folder: Path) -> Path:
    """The cookies as a Netscape cookies.txt file in `folder`, for curl and wget."""
    lines = ["# Netscape HTTP Cookie File"]
    for cookie in cookies:
        expires = int(cookie.expires.timestamp()) if cookie.expires else 0
        secure = "TRUE" if cookie.secure else "FALSE"
        value = cookie.value.get_secret_value()
        lines.append(
            f"{cookie.domain}\tTRUE\t{cookie.path}\t{secure}\t{expires}\t{cookie.name}\t{value}"
        )
    path = folder / "cookies.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def _part_size(part: Path) -> int:
    return part.stat().st_size if part.is_file() else 0


# ---------------------------------------------------------------------------------------------
# Backends: what talks to the server
# ---------------------------------------------------------------------------------------------

type _Report = Callable[[int], None]
"""Told how many bytes the .part file holds, as a download goes."""


class _Backend(ABC):
    requires: ClassVar[tuple[Requirement, ...]]

    def __init__(self, options: HttpOptions) -> None:
        self.options = options

    @abstractmethod
    async def head(self, request: _Request, *, first_byte: bool, cancel: CancelToken) -> _Response:
        """The answer to a HEAD request, or (first_byte) to a GET of the first byte, of which
        only the headers are read."""

    @abstractmethod
    async def fetch(
        self, request: _Request, part: Path, *, offset: int, report: _Report, cancel: CancelToken
    ) -> None:
        """Write the URL's content into `part`. When `offset` (the bytes `part` already holds)
        is not 0, append the rest if the server can send it, else start again. Raises the
        DownloadError a failure means."""


@asynccontextmanager
async def http_client(
    *,
    headers: Mapping[str, str],
    cookies: list[Cookie],
    seconds: float,
    retries: int,
    tool: str,
) -> AsyncGenerator[httpx.AsyncClient]:
    """An httpx client that sends `headers`, and `cookies` to their sites, follows redirects,
    waits `seconds` to connect or for more data, and retries a failed connection `retries`
    times. httpx's errors are raised as the DownloadErrors they mean (from `tool`)."""
    jar = http.cookiejar.CookieJar()
    for cookie in cookies:
        jar.set_cookie(cookie.to_http_cookie())
    try:
        async with httpx.AsyncClient(
            headers=dict(headers),
            cookies=jar,
            follow_redirects=True,
            timeout=seconds,
            transport=httpx.AsyncHTTPTransport(retries=retries),
        ) as client:
            yield client
    except httpx.HTTPError as e:
        raise httpx_error(e, tool=tool) from e


class _Httpx(_Backend):
    requires = (Requirement.module("httpx", hint="Install httpx: `uv add httpx`."),)

    def _client(self, request: _Request) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        return http_client(
            headers=request.headers,
            cookies=request.cookies,
            seconds=self.options.timeout,
            retries=self.options.retries,
            tool=TOOL,
        )

    async def head(self, request: _Request, *, first_byte: bool, cancel: CancelToken) -> _Response:
        async def ask() -> _Response:
            async with self._client(request) as client:
                if first_byte:
                    range_ = {"Range": "bytes=0-0"}
                    async with client.stream("GET", request.url, headers=range_) as response:
                        pass  # its headers are all that is wanted
                else:
                    response = await client.head(request.url)
            return _Response(
                status=response.status_code,
                url=str(response.url),
                headers={name.lower(): value for name, value in response.headers.items()},
            )

        return await cancellable(ask(), cancel)

    async def fetch(
        self, request: _Request, part: Path, *, offset: int, report: _Report, cancel: CancelToken
    ) -> None:
        async def get() -> None:
            headers = {"Range": f"bytes={offset}-"} if offset else {}
            async with (
                self._client(request) as client,
                client.stream("GET", request.url, headers=headers) as response,
            ):
                status = response.status_code
                if error := http_status_error(
                    status, request.url, tool=TOOL, retry_after=response.headers.get("Retry-After")
                ):
                    raise error
                resumed = status == 206  # else the server sends it all
                done = offset if resumed else 0
                file = await asyncio.to_thread(part.open, "ab" if resumed else "wb")
                try:
                    async for chunk in response.aiter_bytes():
                        await asyncio.to_thread(file.write, chunk)
                        done += len(chunk)
                        report(done)
                finally:
                    await asyncio.to_thread(file.close)

        await cancellable(get(), cancel)


class _CurlInfo(BaseModel):
    """The part of curl's --write-out %{json} that udl reads."""

    response_code: int
    url_effective: str


_CURL_HEADERS: Final = TypeAdapter(dict[str, list[str]])


class _Curl(_Backend):
    requires = (Requirement.binary("curl", hint="Install curl, e.g. `sudo pacman -S curl`."),)

    def _args(self, request: _Request, folder: Path) -> list[str]:
        timeout = self.options.timeout
        args = ["curl", "-q", "--silent", "--show-error", "--location"]  # -q: no ~/.curlrc
        args += ["--compressed"]  # (decodes what a server compresses although asked not to)
        args += ["--connect-timeout", str(timeout), "--retry", str(self.options.retries)]
        # Give up when nothing arrives for `timeout` seconds.
        args += ["--speed-limit", "1", "--speed-time", str(max(int(timeout), 1))]
        for name, value in request.headers.items():
            args += ["--header", f"{name}: {value}"]
        if request.cookies:
            args += ["--cookie", str(_cookie_file(request.cookies, folder))]
        return args

    async def head(self, request: _Request, *, first_byte: bool, cancel: CancelToken) -> _Response:
        with tempfile.TemporaryDirectory(prefix="udl-http-") as folder:
            args = [*self._args(request, Path(folder)), "--head", "--output", os.devnull]
            if first_byte:  # a GET, of which curl reads only the headers
                args += ["--request", "GET", "--range", "0-0"]
            args += ["--write-out", "%{json}\n%{header_json}", request.url]
            run = await run_process(args, cancel=cancel)
        if run.returncode:
            raise curl_error(run.returncode, run.stderr)
        info, _, headers = run.stdout.partition("\n")
        response = _CurlInfo.model_validate_json(info)
        return _Response(
            status=response.response_code,
            url=response.url_effective,
            headers={
                name.lower(): values[-1]
                for name, values in _CURL_HEADERS.validate_json(headers).items()
                if values
            },
        )

    async def fetch(
        self, request: _Request, part: Path, *, offset: int, report: _Report, cancel: CancelToken
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="udl-http-") as folder:
            args = [*self._args(request, Path(folder)), "--fail", "--output", str(part)]

            async def run(*resume: str) -> int:
                ran = await run_process(
                    [*args, *resume, request.url],
                    cancel=cancel,
                    on_tick=lambda: report(_part_size(part)),
                )
                if ran.returncode and ran.returncode != _CURL_CANNOT_RESUME:
                    raise curl_error(ran.returncode, ran.stderr)
                return ran.returncode

            if offset and await run("--continue-at", "-") == 0:
                return
            await asyncio.to_thread(part.unlink, missing_ok=True)
            await run()


def _wget_response(url: str, output: str) -> _Response | None:
    """The last response in wget's --server-response output (following its redirects from
    `url`), or None when there is none."""
    responses: list[tuple[int, dict[str, str]]] = []
    for line in output.splitlines():
        if status := re.match(r"  HTTP/\S+ (\d{3})", line):
            responses.append((int(status.group(1)), {}))
        elif responses and line.startswith("  ") and ":" in line:
            name, _, value = line.strip().partition(":")
            responses[-1][1][name.lower()] = value.strip()
    if not responses:
        return None
    for _, headers in responses[:-1]:
        url = urljoin(url, headers.get("location", ""))
    status, headers = responses[-1]
    return _Response(status=status, url=url, headers=headers)


class _Wget(_Backend):
    requires = (Requirement.binary("wget", hint="Install wget, e.g. `sudo pacman -S wget`."),)

    def _args(self, request: _Request, folder: Path) -> list[str]:
        # No ~/.wgetrc, and no ~/.wget-hsts written.
        args = ["wget", "--no-config", "--no-hsts", "--no-verbose", "--compression=auto"]
        args += [f"--timeout={self.options.timeout}", f"--tries={self.options.retries + 1}"]
        for name, value in request.headers.items():
            args += ["--header", f"{name}: {value}"]
        if request.cookies:
            args += [f"--load-cookies={_cookie_file(request.cookies, folder)}"]
        return args

    async def head(self, request: _Request, *, first_byte: bool, cancel: CancelToken) -> _Response:
        with tempfile.TemporaryDirectory(prefix="udl-http-") as folder:
            args = [*self._args(request, Path(folder)), "--server-response"]
            if first_byte:  # wget has no GET that stops at the headers
                args += ["--header", "Range: bytes=0-0", f"--output-document={os.devnull}"]
            else:
                args += ["--spider"]
            run = await run_process([*args, request.url], cancel=cancel)
        response = _wget_response(request.url, run.stderr)
        # A failed request has no response, or ends in a redirect or success.
        if response is None or (run.returncode and response.status < 400):
            raise wget_error(run.returncode, run.stderr)
        return response

    async def fetch(
        self, request: _Request, part: Path, *, offset: int, report: _Report, cancel: CancelToken
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="udl-http-") as folder:
            args = [*self._args(request, Path(folder)), f"--output-document={part}"]
            if offset:
                args += ["--continue"]  # (it starts again if the server ignores Range)
            run = await run_process(
                [*args, request.url], cancel=cancel, on_tick=lambda: report(_part_size(part))
            )
        if run.returncode:
            if await asyncio.to_thread(_part_size, part) == 0:
                await asyncio.to_thread(part.unlink, missing_ok=True)  # wget makes it at once
            raise wget_error(run.returncode, run.stderr)


_BACKENDS: Final[dict[HttpBackend, type[_Backend]]] = {
    HttpBackend.HTTPX: _Httpx,
    HttpBackend.CURL: _Curl,
    HttpBackend.WGET: _Wget,
}


# ---------------------------------------------------------------------------------------------
# The downloader
# ---------------------------------------------------------------------------------------------


def _kept(path: Path, size: int | None) -> bool:
    """Whether `path` is this file, downloaded before (as far as its size tells)."""
    return path.is_file() and (size is None or path.stat().st_size == size)


def _resumable(part: Path, size: int | None) -> int:
    """How many bytes of the file `part` holds, ready to write (its folder exists). A part that
    cannot be the start of the file is removed."""
    part.parent.mkdir(parents=True, exist_ok=True)
    have = _part_size(part)
    if have and (size is None or have > size):
        part.unlink()
        return 0
    return have


def _finish(part: Path, path: Path, size: int | None) -> None:
    """Rename a complete `part` to `path`."""
    have = _part_size(part)
    if not have:
        part.unlink(missing_ok=True)
        raise ExtractionFailed(f"The server sent an empty file for {path.name}", tool=TOOL)
    if size is not None and have != size:  # cut short (or the file changed): try again
        raise NetworkError(f"Got {have} bytes of {path.name}; the server said {size}", tool=TOOL)
    part.replace(path)


class HttpDownloader(Downloader[HttpOptions]):
    """Downloads URLs that are files rather than web pages, and resumes downloads cut short."""

    name = TOOL
    options_model = HttpOptions

    def __init__(self, options: HttpOptions | None = None) -> None:
        super().__init__(options)
        self._backend = _BACKENDS[self.options.backend](self.options)

    def requirements(self) -> tuple[Requirement, ...]:
        return self._backend.requires

    def match(self, url: str) -> MatchResult | None:
        if media_type(url) is None:
            return None
        return MatchResult(confidence=_MATCH, detail=PurePosixPath(url_name(url)).suffix.lower())

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        file = await self._file(ctx)
        return ProbeResult(
            tool=TOOL,
            ok=True,
            confidence=file.confidence,
            title=file.title,
            media=[
                MediaCandidate(
                    url=file.url,
                    content_type=file.content_type,
                    size_bytes=file.size,
                    title=file.title,
                    found_by=TOOL,
                )
            ],
        )

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        file = await self._file(ctx)
        name = render_template(
            ctx.filename_template or _DEFAULT_TEMPLATE, {"title": file.title, "ext": file.ext}
        )
        path = ctx.output_dir / name
        if not await asyncio.to_thread(_kept, path, file.size):
            part = path.with_name(f"{path.name}.part")
            offset = await asyncio.to_thread(_resumable, part, file.size)
            if file.size is None or offset < file.size:

                def report(done: int) -> None:
                    ctx.progress(
                        DownloadProgress(downloaded_bytes=done, total_bytes=file.size, file=part)
                    )

                await self._backend.fetch(
                    _request(ctx), part, offset=offset, report=report, cancel=ctx.cancel
                )
            await asyncio.to_thread(_finish, part, path, file.size)
        size = (await asyncio.to_thread(path.stat)).st_size
        ctx.progress(DownloadProgress(downloaded_bytes=size, total_bytes=size, file=path))
        return DownloadResult(tool=TOOL, url=ctx.url, files=[path], title=file.title)

    async def _file(self, ctx: ProbeContext) -> _File:
        """The file at ctx.url. Raises the error the server's answer means."""
        request = _request(ctx)
        response = await self._backend.head(request, first_byte=False, cancel=ctx.cancel)
        if response.status in _HEAD_REFUSED:
            response = await self._backend.head(request, first_byte=True, cancel=ctx.cancel)
        if error := http_status_error(
            response.status,
            response.url,
            tool=TOOL,
            retry_after=response.headers.get("retry-after"),
        ):
            raise error
        return _file(response)
