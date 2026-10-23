"""
-----------
- base.py -
-----------

Downloader: the interface every downloader plugin implements, and the models it works with.

A plugin implements `_probe` and `_download`. The public probe() and download() wrap them with
the checks every downloader must pass (tests/contract/test_downloader_contract.py): a missing
requirement is ToolUnavailable, probe() reports failures as a result instead of raising,
download() raises only UDLErrors and succeeds only with real files inside `output_dir`, and a
cancelled token stops both.
"""

# System Imports
import asyncio
import functools
import http.cookiejar
import logging
import string
import threading
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Self, cast
from urllib.parse import urlsplit

# Package Imports
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeFloat,
    NonNegativeInt,
    SecretStr,
    ValidationError,
    model_validator,
)

# Local Imports
from udl.db.models import utcnow
from udl.errors import (
    Cancelled,
    ConfigError,
    DownloadError,
    ErrorInfo,
    ExtractionFailed,
    NotFound,
    ToolUnavailable,
    UDLError,
    UnsupportedURL,
)
from udl.plugins.requirements import Requirement, unmet

if TYPE_CHECKING:
    from udl.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

# The failures of a find's own (its media, or its downloader is missing): the next find is tried.
_MEDIA_FAILURES: tuple[type[DownloadError], ...] = (
    UnsupportedURL,
    ExtractionFailed,
    NotFound,
    ToolUnavailable,
)


def _check_http_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{url!r} is not an absolute http(s) URL")
    return url


type HttpURL = Annotated[str, AfterValidator(_check_http_url)]
"""An absolute http:// or https:// URL."""

type Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
"""How sure a downloader is that it can download a URL: 0 (not at all) to 1 (certain)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DownloaderOptions(_Frozen):
    """The base of every downloader's options model ([tools.<name>] in the config, and a
    strategy's `options`). Unknown keys are rejected, so a typo is an error, not ignored."""


# ---------------------------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------------------------


class Cookie(_Frozen):
    """A browser cookie. Its value is a secret: it is hidden when the cookie is printed or
    serialised (e.g. in an API response)."""

    name: str
    value: SecretStr
    domain: str  # "example.com" or ".example.com": either way, subdomains get it too
    path: str = "/"
    secure: bool = False  # https only
    http_only: bool = False
    expires: AwareDatetime | None = None  # None: a session cookie

    def matches(self, url: str) -> bool:
        """Whether a browser would send this cookie with a request for `url`."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        domain = self.domain.lower().lstrip(".")
        if not (host == domain or host.endswith(f".{domain}")):
            return False
        path = parts.path or "/"
        prefix = self.path.rstrip("/")
        if not (path == prefix or path.startswith(f"{prefix}/") or self.path == "/"):
            return False
        if self.secure and parts.scheme.lower() != "https":
            return False
        return self.expires is None or self.expires > utcnow()

    def to_http_cookie(self) -> http.cookiejar.Cookie:
        """This cookie for a standard library cookie jar (yt-dlp's, or a requests session's)."""
        domain = self.domain
        if "." not in domain.strip("."):
            # The cookie jar matches a dotless host (e.g. localhost) as "localhost.local".
            domain = f"{domain.strip('.')}.local"
        return http.cookiejar.Cookie(
            version=0,
            name=self.name,
            value=self.value.get_secret_value(),
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=self.path,
            path_specified=True,
            secure=self.secure,
            expires=int(self.expires.timestamp()) if self.expires else None,
            discard=self.expires is None,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": ""} if self.http_only else {},
        )

    @classmethod
    def from_http_cookie(cls, cookie: http.cookiejar.Cookie) -> Self:
        """A cookie from a standard library cookie jar (e.g. one a server set)."""
        domain = cookie.domain
        if "." not in (bare := domain.removesuffix(".local")):
            domain = bare  # the jar's name for a dotless host (see to_http_cookie)
        return cls(
            name=cookie.name,
            value=SecretStr(cookie.value or ""),
            domain=domain,
            path=cookie.path or "/",
            secure=cookie.secure,
            http_only=cookie.has_nonstandard_attr("HttpOnly"),
            expires=None if cookie.expires is None else datetime.fromtimestamp(cookie.expires, UTC),
        )


def cookie_header(cookies: list[Cookie], url: str) -> str | None:
    """The Cookie header to send with a request for `url`, or None when no cookie matches."""
    pairs = [f"{c.name}={c.value.get_secret_value()}" for c in cookies if c.matches(url)]
    return "; ".join(pairs) or None


def merge_cookies(first: list[Cookie], then: list[Cookie]) -> list[Cookie]:
    """Both lists; a cookie in `then` replaces one in `first` with the same name, domain, path."""
    merged = {(c.name, c.domain.lstrip("."), c.path): c for c in [*first, *then]}
    return list(merged.values())


# ---------------------------------------------------------------------------------------------
# Cancellation and progress
# ---------------------------------------------------------------------------------------------


class CancelToken:
    """Asks a download (or probe) to stop. Safe to use from any thread: threaded tools check it
    in their progress hooks."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        """Raise Cancelled if the token has been cancelled."""
        if self.cancelled:
            raise Cancelled("The download was cancelled")


class DownloadProgress(_Frozen):
    """How far a download has got."""

    downloaded_bytes: NonNegativeInt = 0
    total_bytes: NonNegativeInt | None = None  # None while unknown
    speed: NonNegativeFloat | None = None  # bytes per second
    eta: NonNegativeFloat | None = None  # seconds
    file: Path | None = None  # the file being written

    @property
    def fraction(self) -> float | None:
        """The share downloaded (0 to 1), when the total is known."""
        if not self.total_bytes:
            return None
        return self.downloaded_bytes / self.total_bytes


type ProgressSink = Callable[[DownloadProgress], None]
"""Receives progress. Downloaders call it on the event loop's thread (see threadsafe())."""


def _ignore_progress(_: DownloadProgress) -> None:
    pass


def threadsafe(sink: ProgressSink) -> ProgressSink:
    """A sink that worker threads may call: each report is passed to `sink` on the event loop's
    thread. Call it from the event loop."""
    loop = asyncio.get_running_loop()

    def report(progress: DownloadProgress) -> None:
        loop.call_soon_threadsafe(sink, progress)

    return report


async def run_blocking[T](call: Callable[[], T], *, cancel: CancelToken) -> T:
    """Run a blocking call (e.g. a sync library) in a worker thread.

    If the awaiting task is cancelled, `cancel` is cancelled too, and this waits for the call to
    notice and stop before re-raising, so the thread never outlives the task (and keeps writing).
    """
    worker = asyncio.ensure_future(asyncio.to_thread(call))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel.cancel()
        await asyncio.wait([worker])
        raise


async def cancellable[T](
    awaitable: Awaitable[T], cancel: CancelToken, *, interval: float = 0.1
) -> T:
    """Await `awaitable`, stopping it (and raising Cancelled) within `interval` seconds of
    `cancel` being cancelled, even if it is stuck waiting (e.g. on a stalled server). If the
    awaiting task is cancelled, `cancel` is cancelled too."""
    task = asyncio.ensure_future(awaitable)
    try:
        while not cancel.cancelled:
            if (await asyncio.wait([task], timeout=interval))[0]:
                return task.result()
        task.cancel()  # (before its first step, if it never started)
        await asyncio.wait([task])
    except asyncio.CancelledError:
        cancel.cancel()
        task.cancel()
        await asyncio.wait([task])
        raise
    raise Cancelled("The download was cancelled")


# ---------------------------------------------------------------------------------------------
# Probe and download models
# ---------------------------------------------------------------------------------------------


class MatchResult(_Frozen):
    """An offline guess (no network) that a downloader handles a URL."""

    confidence: Confidence
    detail: str | None = None  # e.g. which extractor matched


class MediaKind(StrEnum):
    FILE = "file"  # a plain file: http can download it
    HLS = "hls"  # an .m3u8 playlist: yt-dlp can download it
    DASH = "dash"  # an .mpd manifest: yt-dlp can download it


class MediaCandidate(_Frozen):
    """A piece of media a probe found."""

    url: HttpURL
    kind: MediaKind = MediaKind.FILE
    content_type: str | None = None
    size_bytes: NonNegativeInt | None = None
    title: str | None = None
    found_by: str | None = None  # how it was found, e.g. "<video>", "og:video", "network"


class Handoff(_Frozen):
    """Media found by one downloader (e.g. scraper, browser) for another one to download."""

    tool: str  # the downloader to hand off to, e.g. "http" or "yt-dlp"
    url: HttpURL
    headers: dict[str, str] = Field(default_factory=dict[str, str])  # e.g. Referer
    cookies: list[Cookie] = Field(default_factory=list[Cookie])


class ProbeResult(_Frozen):
    """What a downloader found out about a URL without downloading it."""

    tool: str
    ok: bool
    confidence: Confidence = 0.0
    title: str | None = None
    media: list[MediaCandidate] = Field(default_factory=list[MediaCandidate])
    handoff: Handoff | None = None  # the media's download needs another downloader
    error: ErrorInfo | None = None  # why not, when not ok

    @model_validator(mode="after")
    def _error_when_not_ok(self) -> Self:
        if self.ok and self.error is not None:
            raise ValueError("a successful probe result has no error")
        if not self.ok and self.error is None:
            raise ValueError("a failed probe result needs its error")
        return self

    @classmethod
    def failed(cls, tool: str, error: UDLError) -> Self:
        info = error.to_info()
        if info.tool is None:
            info = info.model_copy(update={"tool": tool})
        return cls(tool=tool, ok=False, error=info)


class DownloadResult(_Frozen):
    """What a download wrote."""

    tool: str
    url: HttpURL
    files: list[Path]  # absolute paths, inside the download's output_dir
    size_bytes: NonNegativeInt = 0  # the files' total size (download() measures them)
    title: str | None = None
    handed_off_to: str | None = None  # the downloader that downloaded the media, if not `tool`


# ---------------------------------------------------------------------------------------------
# Filename templates, e.g. "{site}/{title}.{ext}"
# ---------------------------------------------------------------------------------------------


def template_parts(template: str) -> list[tuple[str, str | None]]:
    """A filename template as (literal text, field name) pairs; text after the last field has
    the name None. Fields are plain names: `{title}`, not `{title:>10}` or `{0}`. `{{` and `}}`
    are a literal brace. Raises ValueError."""
    parts: list[tuple[str, str | None]] = []
    text = ""
    for literal, field, spec, conversion in string.Formatter().parse(template):
        text += literal
        if field is None:
            continue
        if not field.isidentifier() or spec or conversion is not None:
            raise ValueError(
                f"{{{field}}} in {template!r} is not a plain field name like {{title}}"
            )
        parts.append((text, field))
        text = ""
    if text:
        parts.append((text, None))
    return parts


def _check_template(template: str) -> str:
    if not template:
        raise ValueError("the filename template is empty")
    skeleton = PurePosixPath(
        "".join(text + ("x" if field else "") for text, field in template_parts(template))
    )
    if skeleton.is_absolute() or ".." in skeleton.parts:
        raise ValueError(f"the filename template {template!r} must stay inside the output dir")
    return template


type FilenameTemplate = Annotated[str, AfterValidator(_check_template)]
"""A filename template, e.g. "{site}/{title}.{ext}": relative, with plain fields, never leaving
the output dir."""


def _safe_field(value: str | None) -> str:
    """A field's value as one path segment: it can never add a folder or leave one."""
    if not value:
        return "NA"
    value = value.replace("/", "_").replace("\0", "_")
    return "_" if value in (".", "..") else value


def render_template(template: str, fields: Mapping[str, str]) -> str:
    """The relative path a filename template gives with these field values (missing ones are
    "NA"). Only the template's own `/` make folders."""
    return "".join(
        text + ("" if field is None else _safe_field(fields.get(field)))
        for text, field in template_parts(template)
    )


def _escaped(text: str) -> str:
    return text.replace("{", "{{").replace("}", "}}")


def fill_fields(template: str, fields: Mapping[str, str]) -> str:
    """A filename template with some of its fields filled in (as render_template would fill
    them) and the others left for the tool, e.g. `{site}` before a downloader sees it."""

    def part(text: str, field: str | None) -> str:
        if field is None:
            return _escaped(text)
        if field in fields:
            return _escaped(text) + _escaped(_safe_field(fields[field]))
        return f"{_escaped(text)}{{{field}}}"

    return "".join(part(text, field) for text, field in template_parts(template))


class _Context(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class ProbeContext(_Context):
    """What a probe works with."""

    url: HttpURL
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    cookies: list[Cookie] = Field(default_factory=list[Cookie])
    cancel: CancelToken = Field(default_factory=CancelToken)


type DownloaderLookup = Callable[[str], Downloader[Any]]
"""Gives the downloader to use by name, e.g. for a handoff."""


@functools.cache
def _installed() -> PluginRegistry:
    """The installed plugins (entry points only), found once."""
    # Imported here because the registry imports this module.
    from udl.plugins.registry import PluginRegistry

    return PluginRegistry.discover()


def default_downloader_for(name: str) -> Downloader[Any]:
    """The installed downloader called `name`, with its default options."""
    from udl.plugins.registry import DOWNLOADERS

    return _installed().get(DOWNLOADERS, name)()


class DownloadContext(ProbeContext):
    """What a download works with."""

    output_dir: Path  # where the files go
    # e.g. "{title}.{ext}"; None leaves the naming to the tool. Each tool translates it.
    filename_template: FilenameTemplate | None = None
    progress: ProgressSink = _ignore_progress
    downloader_for: DownloaderLookup = default_downloader_for


# ---------------------------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------------------------


class Downloader[O: DownloaderOptions = DownloaderOptions](ABC):
    """A tool that can download some URLs, e.g. yt-dlp.

    Subclasses set `name` (what strategies and the config use to pick it) and `options_model`
    (the model of O), declare what they need installed in `requires`, and implement `_probe`
    and `_download`. A downloader holds its options, so one instance is one configured tool.
    """

    name: ClassVar[str]
    options_model: ClassVar[type[DownloaderOptions]] = DownloaderOptions
    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(self, options: O | None = None) -> None:
        if options is None:
            options = cast(O, self.options_model())
        elif not isinstance(options, self.options_model):
            raise TypeError(f"{type(self).__name__} needs {self.options_model.__name__} options")
        self._options: O = options

    @property
    def options(self) -> O:
        return self._options

    @classmethod
    def configure(cls, *layers: Mapping[str, JsonValue]) -> Self:
        """A downloader with options from `layers` (e.g. the [tools.<name>] config table, then a
        strategy's options); later layers override earlier ones. Raises ConfigError."""
        merged: dict[str, JsonValue] = {}
        for layer in layers:
            merged.update(layer)
        try:
            options = cls.options_model.model_validate(merged)
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, err['loc'])) or 'options'}: {err['msg']}" for err in e.errors()
            )
            raise ConfigError(
                f"Invalid options for the {cls.name} downloader: {problems}",
                hint=f"Check [tools.{cls.name}] in the config, and the strategy's options.",
            ) from e
        return cls(cast(O, options))

    # -- availability ------------------------------------------------------------------------

    def requirements(self) -> tuple[Requirement, ...]:
        """What this downloader needs installed (with its options: e.g. a backend binary)."""
        return self.requires

    def missing(self) -> list[Requirement]:
        return unmet(self.requirements())

    def available(self) -> bool:
        """Whether everything this downloader needs is installed."""
        return not self.missing()

    def _check_available(self) -> None:
        if missing := self.missing():
            needs = ", ".join(f"the {r.name} {r.kind}" for r in missing)
            hints = " ".join(r.hint for r in missing if r.hint)
            raise ToolUnavailable(f"{self.name} needs {needs}", tool=self.name, hint=hints or None)

    # -- the interface -----------------------------------------------------------------------

    def match(self, url: str) -> MatchResult | None:
        """Whether this downloader looks like it handles `url`, judged offline (no network).
        None when it cannot tell; the default."""
        return None

    async def probe(self, ctx: ProbeContext) -> ProbeResult:
        """Find out (over the network, but writing no files) whether this downloader can
        download ctx.url. Failures are reported in the result; only Cancelled is raised."""
        ctx.cancel.check()
        try:
            self._check_available()
            return await self._probe(ctx)
        except Cancelled:
            raise
        except UDLError as e:
            return ProbeResult.failed(self.name, e)
        except Exception as e:
            log.debug("%s failed to probe %s", self.name, ctx.url, exc_info=True)
            return ProbeResult.failed(self.name, self._unexpected(e))

    async def download(self, ctx: DownloadContext) -> DownloadResult:
        """Download ctx.url into ctx.output_dir (created if needed).

        Raises a DownloadError (e.g. AuthRequired) when it fails, or Cancelled. It succeeds only
        when the files exist, are not empty and are inside output_dir.
        """
        ctx.cancel.check()
        output_dir = ctx.output_dir.absolute()
        try:
            self._check_available()
            await asyncio.to_thread(output_dir.mkdir, parents=True, exist_ok=True)
            result = await self._download(ctx.model_copy(update={"output_dir": output_dir}))
            return await asyncio.to_thread(self._checked, result, output_dir)
        except DownloadError as e:
            if e.tool is None:
                e.tool = self.name
            raise
        except UDLError:
            raise
        except Exception as e:
            log.debug("%s failed to download %s", self.name, ctx.url, exc_info=True)
            raise self._unexpected(e) from e

    @abstractmethod
    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        """The plugin's probe. Raise a DownloadError (e.g. UnsupportedURL) when it fails."""

    @abstractmethod
    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        """The plugin's download. ctx.output_dir exists and is absolute. Raise a DownloadError
        when it fails, and Cancelled soon after ctx.cancel is cancelled."""

    # -- helpers for plugins -----------------------------------------------------------------

    async def hand_off(self, handoff: Handoff, ctx: DownloadContext) -> DownloadResult:
        """Download media this downloader found with the downloader the handoff names."""
        downloader = ctx.downloader_for(handoff.tool)
        child = ctx.model_copy(
            update={
                "url": handoff.url,
                "headers": {**ctx.headers, **handoff.headers},
                "cookies": merge_cookies(ctx.cookies, handoff.cookies),
            }
        )
        result = await downloader.download(child)
        return result.model_copy(
            update={"tool": self.name, "url": ctx.url, "handed_off_to": downloader.name}
        )

    async def hand_off_best(
        self, handoffs: Sequence[Handoff], ctx: DownloadContext, *, tries: int = 5
    ) -> DownloadResult:
        """Download the first of `handoffs` (best first) that works. When one fails because of
        its media (unsupported, not found, not media) or its downloader is missing, the next is
        tried, `tries` at most;
        another failure (e.g. AuthRequired) is raised at once, so other media (e.g. a trailer)
        never stands in for media that needs a login. When none works, the first failure is
        raised."""
        failures: list[DownloadError] = []
        for handoff in handoffs[:tries]:
            try:
                return await self.hand_off(handoff, ctx)
            except _MEDIA_FAILURES as e:
                log.info("Could not download %s (%s); trying the next find", handoff.url, e)
                failures.append(e)
        raise failures[0]

    async def hand_off_all(
        self, handoffs: Sequence[Handoff], ctx: DownloadContext
    ) -> DownloadResult:
        """Download every one of `handoffs` (e.g. a gallery's), each with the downloader it
        names. One that fails because of its media (unsupported, not found, not media) or
        whose downloader is missing is skipped; another failure (e.g. AuthRequired) is raised
        at once. When none works, the first failure is raised."""
        results: list[DownloadResult] = []
        failures: list[DownloadError] = []
        for handoff in handoffs:
            try:
                results.append(await self.hand_off(handoff, ctx))
            except _MEDIA_FAILURES as e:
                log.info("Could not download %s (%s); skipping it", handoff.url, e)
                failures.append(e)
        if not results:
            raise failures[0]
        return results[0].model_copy(
            update={
                "files": [file for result in results for file in result.files],
                "size_bytes": sum(result.size_bytes for result in results),
            }
        )

    def _unexpected(self, error: Exception) -> DownloadError:
        return DownloadError(f"{type(error).__name__}: {error}", tool=self.name)

    def _checked(self, result: DownloadResult, output_dir: Path) -> DownloadResult:
        """`result` with its files made absolute and measured, if they are real and in place."""
        if not result.files:
            raise ExtractionFailed(f"{self.name} downloaded no files", tool=self.name)
        files: list[Path] = []
        size = 0
        for file in result.files:
            path = (output_dir / file).absolute()
            if not path.is_relative_to(output_dir):
                raise ExtractionFailed(
                    f"{self.name} wrote {path}, outside the output dir {output_dir}",
                    tool=self.name,
                )
            if not path.is_file():
                raise ExtractionFailed(
                    f"{self.name} reported a missing file {path}", tool=self.name
                )
            if (file_size := path.stat().st_size) == 0:
                raise ExtractionFailed(f"{self.name} wrote an empty file {path}", tool=self.name)
            files.append(path)
            size += file_size
        return result.model_copy(update={"files": files, "size_bytes": size})


DownloadContext.model_rebuild()
