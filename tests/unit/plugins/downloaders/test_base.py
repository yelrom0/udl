"""
----------------
- test_base.py -
----------------

The Downloader base class: options, requirements, the checks it makes around every plugin's
probe and download, cancellation, threads, cookies and handoffs
"""

# System Imports
import asyncio
import http.cookiejar
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

# Package Imports
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import JsonValue, SecretStr, ValidationError

# Local Imports
from udl.errors import (
    AuthRequired,
    Cancelled,
    ConfigError,
    DownloadError,
    ErrorCode,
    ExtractionFailed,
    PluginError,
    ToolUnavailable,
)
from udl.plugins.downloaders.base import (
    CancelToken,
    Cookie,
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadProgress,
    DownloadResult,
    Handoff,
    MediaCandidate,
    ProbeContext,
    ProbeResult,
    Requirement,
    RequirementKind,
    cookie_header,
    default_downloader_for,
    render_template,
    run_blocking,
    template_parts,
    threadsafe,
)

URL = "https://example.com/watch/1"


class EchoOptions(DownloaderOptions):
    greeting: str = "hello"
    retries: int = 1


class Echo(Downloader[EchoOptions]):
    """Writes the files it is told to (relative to output_dir), or raises what it is told to."""

    name = "echo"
    options_model = EchoOptions

    def __init__(
        self,
        options: EchoOptions | None = None,
        *,
        writes: dict[str, bytes] | None = None,
        report: list[str] | None = None,
        raises: BaseException | None = None,
    ) -> None:
        super().__init__(options)
        self.writes = {"clip.mp4": b"data"} if writes is None else writes
        self.report = report  # absolute paths to return instead of the written files
        self.raises = raises
        self.seen: list[ProbeContext] = []

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        self.seen.append(ctx)
        if self.raises:
            raise self.raises
        return ProbeResult(tool=self.name, ok=True, confidence=0.5, title=self.options.greeting)

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        self.seen.append(ctx)
        if self.raises:
            raise self.raises
        files: list[Path] = []
        for name, data in self.writes.items():
            path = ctx.output_dir / name
            path.write_bytes(data)
            files.append(path)
        reported = [Path(p) for p in self.report] if self.report is not None else files
        return DownloadResult(tool=self.name, url=ctx.url, files=reported, title="Clip")


class NeedsThings(Echo):
    name = "needs-things"
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.binary("udl-no-such-binary", hint="Install it"),
        Requirement.module("json"),
    )


# ---------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------


def test_a_downloader_without_options_uses_the_defaults() -> None:
    assert Echo().options == EchoOptions()


def test_configure_layers_options_later_ones_winning() -> None:
    # e.g. the [tools.echo] config table, then a strategy's options.
    echo = Echo.configure({"greeting": "hi", "retries": 3}, {"retries": 5})

    assert echo.options == EchoOptions(greeting="hi", retries=5)


@pytest.mark.parametrize("options", [{"no_such_option": 1}, {"retries": "many"}])
def test_invalid_options_are_a_config_error_naming_the_tool(options: dict[str, JsonValue]) -> None:
    with pytest.raises(ConfigError, match="echo") as caught:
        Echo.configure(options)

    assert caught.value.hint is not None


def test_options_of_another_downloader_are_rejected() -> None:
    with pytest.raises(TypeError, match="EchoOptions"):
        Echo(DownloaderOptions())  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------------------------
# Requirements
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("requirement", "met"),
    [
        (Requirement.binary("sh"), True),
        (Requirement.binary("udl-no-such-binary"), False),
        (Requirement.module("json"), True),
        (Requirement.module("udl_no_such_module"), False),
        (Requirement.module("udl_no_such_module.sub"), False),
    ],
)
def test_a_requirement_is_met_when_its_binary_or_module_exists(
    requirement: Requirement, met: bool
) -> None:
    assert requirement.met() is met


def test_requirement_kinds() -> None:
    assert Requirement.binary("curl").kind is RequirementKind.BINARY
    assert Requirement.module("yt_dlp").kind is RequirementKind.MODULE


def test_a_downloader_is_available_when_every_requirement_is_met() -> None:
    assert Echo().available() is True
    assert Echo().missing() == []


def test_a_downloader_lists_its_missing_requirements() -> None:
    downloader = NeedsThings()

    assert downloader.available() is False
    assert downloader.missing() == [Requirement.binary("udl-no-such-binary", hint="Install it")]


async def test_probing_with_a_missing_requirement_reports_the_tool_unavailable() -> None:
    downloader = NeedsThings()

    result = await downloader.probe(ProbeContext(url=URL))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code is ErrorCode.TOOL_UNAVAILABLE
    assert result.error.hint == "Install it"
    assert downloader.seen == []


async def test_downloading_with_a_missing_requirement_raises_tool_unavailable(
    tmp_path: Path,
) -> None:
    downloader = NeedsThings()

    with pytest.raises(ToolUnavailable, match="udl-no-such-binary") as caught:
        await downloader.download(DownloadContext(url=URL, output_dir=tmp_path))

    assert caught.value.tool == "needs-things"
    assert downloader.seen == []


# ---------------------------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["ftp://example.com/a.mp4", "example.com/a.mp4", "http://", "not a url", ""]
)
def test_urls_must_be_absolute_http_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        ProbeContext(url=url)


@pytest.mark.parametrize("url", ["http://localhost:8080/a.mp4", "HTTPS://Example.com/"])
def test_http_and_https_urls_are_accepted(url: str) -> None:
    assert ProbeContext(url=url).url == url


def test_a_successful_probe_result_has_no_error() -> None:
    with pytest.raises(ValidationError, match="error"):
        ProbeResult(tool="echo", ok=True, error=AuthRequired("no").to_info())


def test_a_failed_probe_result_needs_its_error() -> None:
    with pytest.raises(ValidationError, match="error"):
        ProbeResult(tool="echo", ok=False)


def test_a_failed_probe_result_is_built_from_the_error() -> None:
    result = ProbeResult.failed("echo", AuthRequired("Sign in", hint="log in"))

    assert result.ok is False
    assert result.confidence == 0.0
    assert result.error is not None
    assert (result.error.code, result.error.tool, result.error.hint) == (
        ErrorCode.AUTH_REQUIRED,
        "echo",
        "log in",
    )


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_confidence_is_between_0_and_1(confidence: float) -> None:
    with pytest.raises(ValidationError):
        ProbeResult(tool="echo", ok=True, confidence=confidence)


def test_cookie_values_are_hidden_when_serialised() -> None:
    cookie = Cookie(name="session", value=SecretStr("s3cret"), domain="example.com")

    assert "s3cret" not in cookie.model_dump_json()
    assert "s3cret" not in repr(cookie)
    assert cookie.value.get_secret_value() == "s3cret"


def test_progress_knows_its_fraction() -> None:
    assert DownloadProgress(downloaded_bytes=25, total_bytes=100).fraction == 0.25
    assert DownloadProgress(downloaded_bytes=25).fraction is None
    assert DownloadProgress(downloaded_bytes=0, total_bytes=0).fraction is None


# ---------------------------------------------------------------------------------------------
# Probing and downloading: the checks around every plugin
# ---------------------------------------------------------------------------------------------


async def test_probe_returns_the_plugins_result() -> None:
    result = await Echo(EchoOptions(greeting="hey")).probe(ProbeContext(url=URL))

    assert result == ProbeResult(tool="echo", ok=True, confidence=0.5, title="hey")


async def test_a_download_error_while_probing_becomes_a_failed_result() -> None:
    result = await Echo(raises=AuthRequired("Sign in")).probe(ProbeContext(url=URL))

    assert result.error is not None
    assert (result.ok, result.error.code, result.error.tool) == (
        False,
        ErrorCode.AUTH_REQUIRED,
        "echo",
    )


async def test_an_unexpected_exception_while_probing_becomes_a_failed_result() -> None:
    result = await Echo(raises=RuntimeError("boom")).probe(ProbeContext(url=URL))

    assert result.error is not None
    assert result.error.code is ErrorCode.DOWNLOAD_ERROR
    assert "boom" in result.error.message


async def test_cancelling_a_probe_raises_cancelled() -> None:
    with pytest.raises(Cancelled):
        await Echo(raises=Cancelled("stop")).probe(ProbeContext(url=URL))


async def test_a_probe_with_a_cancelled_token_never_starts() -> None:
    token = CancelToken()
    token.cancel()
    echo = Echo()

    with pytest.raises(Cancelled):
        await echo.probe(ProbeContext(url=URL, cancel=token))

    assert echo.seen == []


async def test_download_creates_the_output_dir_and_measures_the_files(tmp_path: Path) -> None:
    out = tmp_path / "new" / "dir"
    echo = Echo(writes={"a.mp4": b"12345", "b.jpg": b"678"})

    result = await echo.download(DownloadContext(url=URL, output_dir=out))

    assert result == DownloadResult(
        tool="echo",
        url=URL,
        files=[out / "a.mp4", out / "b.jpg"],
        size_bytes=8,
        title="Clip",
    )


async def test_a_relative_output_dir_is_made_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    echo = Echo()

    result = await echo.download(DownloadContext(url=URL, output_dir=Path("out")))

    assert result.files == [tmp_path / "out" / "clip.mp4"]
    assert isinstance(echo.seen[0], DownloadContext)
    assert echo.seen[0].output_dir == tmp_path / "out"


@pytest.mark.parametrize(
    ("writes", "report", "error"),
    [
        ({}, None, "no files"),
        ({"empty.mp4": b""}, None, "empty"),
        ({"a.mp4": b"x"}, ["{out}/missing.mp4"], "missing"),
        ({"a.mp4": b"x"}, ["{tmp}/elsewhere.mp4"], "outside"),
    ],
)
async def test_a_download_only_succeeds_with_real_files_inside_output_dir(
    tmp_path: Path, writes: dict[str, bytes], report: list[str] | None, error: str
) -> None:
    out = tmp_path / "out"
    (tmp_path / "elsewhere.mp4").write_bytes(b"x")
    reported = None if report is None else [r.format(out=out, tmp=tmp_path) for r in report]

    with pytest.raises(ExtractionFailed, match=error) as caught:
        await Echo(writes=writes, report=reported).download(
            DownloadContext(url=URL, output_dir=out)
        )

    assert caught.value.tool == "echo"


async def test_a_download_error_gets_the_tools_name(tmp_path: Path) -> None:
    with pytest.raises(AuthRequired) as caught:
        await Echo(raises=AuthRequired("Sign in")).download(
            DownloadContext(url=URL, output_dir=tmp_path)
        )

    assert caught.value.tool == "echo"


async def test_a_download_error_keeps_a_tool_name_it_already_has(tmp_path: Path) -> None:
    # e.g. a scraper whose handoff to http failed
    with pytest.raises(AuthRequired) as caught:
        await Echo(raises=AuthRequired("Sign in", tool="http")).download(
            DownloadContext(url=URL, output_dir=tmp_path)
        )

    assert caught.value.tool == "http"


async def test_an_unexpected_exception_while_downloading_is_a_download_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(DownloadError, match="RuntimeError: boom") as caught:
        await Echo(raises=RuntimeError("boom")).download(
            DownloadContext(url=URL, output_dir=tmp_path)
        )

    assert type(caught.value) is DownloadError
    assert caught.value.tool == "echo"
    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_a_download_with_a_cancelled_token_never_starts(tmp_path: Path) -> None:
    token = CancelToken()
    token.cancel()
    echo = Echo()

    with pytest.raises(Cancelled):
        await echo.download(DownloadContext(url=URL, output_dir=tmp_path, cancel=token))

    assert echo.seen == []


# ---------------------------------------------------------------------------------------------
# Cancellation and threads
# ---------------------------------------------------------------------------------------------


def test_a_cancel_token_starts_uncancelled() -> None:
    token = CancelToken()

    token.check()  # does not raise

    assert token.cancelled is False


def test_a_cancelled_token_raises_cancelled_on_check() -> None:
    token = CancelToken()

    token.cancel()

    assert token.cancelled is True
    with pytest.raises(Cancelled):
        token.check()


async def test_run_blocking_runs_the_call_in_another_thread() -> None:
    loop_thread = threading.get_ident()

    worker = await run_blocking(threading.get_ident, cancel=CancelToken())

    assert worker != loop_thread


async def test_cancelling_the_task_cancels_the_token_and_waits_for_the_thread() -> None:
    token = CancelToken()
    started, finished = threading.Event(), threading.Event()

    def work() -> None:
        started.set()
        while not token.cancelled:
            time.sleep(0.01)
        time.sleep(0.05)  # still cleaning up after the token was cancelled
        finished.set()

    task = asyncio.create_task(run_blocking(work, cancel=token))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert token.cancelled
    assert finished.is_set()


async def test_threadsafe_progress_arrives_on_the_event_loop_thread() -> None:
    seen: list[tuple[int, int]] = []
    report = threadsafe(lambda p: seen.append((threading.get_ident(), p.downloaded_bytes)))

    def work() -> None:
        report(DownloadProgress(downloaded_bytes=1))
        report(DownloadProgress(downloaded_bytes=2))

    await run_blocking(work, cancel=CancelToken())
    await asyncio.sleep(0)  # let the loop run the scheduled callbacks

    assert seen == [(threading.get_ident(), 1), (threading.get_ident(), 2)]


# ---------------------------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------------------------


def make_cookie(name: str = "s", **fields: object) -> Cookie:
    return Cookie.model_validate({"name": name, "value": "v", "domain": "example.com", **fields})


@pytest.mark.parametrize(
    ("cookie", "url", "sent"),
    [
        (make_cookie(), "https://example.com/a", True),
        (make_cookie(), "https://www.example.com/a", True),  # subdomains too
        (make_cookie(domain=".example.com"), "https://cdn.example.com/a", True),
        (make_cookie(), "https://notexample.com/a", False),
        (make_cookie(), "https://example.org/a", False),
        (make_cookie(path="/private"), "https://example.com/private/v.mp4", True),
        (make_cookie(path="/private"), "https://example.com/private", True),
        (make_cookie(path="/private"), "https://example.com/privateer", False),
        (make_cookie(path="/private"), "https://example.com/", False),
        (make_cookie(secure=True), "http://example.com/a", False),
        (make_cookie(secure=True), "https://example.com/a", True),
        (make_cookie(domain="localhost"), "http://localhost:8080/a", True),
        (make_cookie(expires=datetime(2000, 1, 1, tzinfo=UTC)), "https://example.com/", False),
        (make_cookie(expires=datetime(2999, 1, 1, tzinfo=UTC)), "https://example.com/", True),
    ],
)
def test_a_cookie_is_sent_to_its_domain_and_path(cookie: Cookie, url: str, sent: bool) -> None:
    assert cookie.matches(url) is sent


@pytest.mark.parametrize(
    ("cookie", "url", "sent"),
    [
        (make_cookie(), "https://example.com/a", True),
        (make_cookie(), "https://www.example.com/a", True),
        (make_cookie(), "https://example.org/a", False),
        (make_cookie(domain="localhost"), "http://localhost:8080/a", True),
        (make_cookie(domain="127.0.0.1"), "http://127.0.0.1:8080/a", True),
        (make_cookie(path="/private"), "https://example.com/a", False),
        (make_cookie(secure=True), "http://example.com/a", False),
        (make_cookie(expires=datetime(2999, 1, 1, tzinfo=UTC)), "https://example.com/", True),
    ],
)
def test_a_cookie_works_in_a_standard_cookie_jar(cookie: Cookie, url: str, sent: bool) -> None:
    jar = http.cookiejar.CookieJar()
    jar.set_cookie(cookie.to_http_cookie())
    request = urllib.request.Request(url)  # noqa: S310 - never opened, only asks the jar

    jar.add_cookie_header(request)

    assert (request.get_header("Cookie") == "s=v") is sent


def test_an_http_only_cookie_stays_http_only() -> None:
    assert make_cookie(http_only=True).to_http_cookie().has_nonstandard_attr("HttpOnly")
    assert not make_cookie().to_http_cookie().has_nonstandard_attr("HttpOnly")


def test_the_cookie_header_has_the_cookies_for_the_url() -> None:
    cookies = [
        make_cookie("a", value="1"),
        make_cookie("b", value="2", domain="other.com"),
        make_cookie("c", value="3"),
    ]

    assert cookie_header(cookies, "https://example.com/") == "a=1; c=3"
    assert cookie_header(cookies, "https://unrelated.com/") is None


# ---------------------------------------------------------------------------------------------
# Handoffs
# ---------------------------------------------------------------------------------------------


class Finder(Echo):
    """Finds media on a page and hands its download off to another downloader."""

    name = "finder"

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        handoff = Handoff(
            tool="echo",
            url="https://cdn.example.com/clip.mp4",
            headers={"Referer": ctx.url},
            cookies=[make_cookie("found")],
        )
        return await self.hand_off(handoff, ctx)


async def test_a_handoff_downloads_with_the_named_downloader(tmp_path: Path) -> None:
    echo = Echo()
    ctx = DownloadContext(
        url=URL,
        output_dir=tmp_path,
        headers={"User-Agent": "udl", "Referer": "https://old.example/"},
        cookies=[make_cookie("mine")],
        downloader_for=lambda name: {"echo": echo}[name],
    )

    result = await Finder().download(ctx)

    assert result.tool == "finder"
    assert result.handed_off_to == "echo"
    assert result.url == URL
    assert result.files == [tmp_path / "clip.mp4"]
    [seen] = echo.seen
    assert isinstance(seen, DownloadContext)
    assert seen.url == "https://cdn.example.com/clip.mp4"
    assert seen.headers == {"User-Agent": "udl", "Referer": URL}
    assert [c.name for c in seen.cookies] == ["mine", "found"]
    assert (seen.output_dir, seen.cancel) == (ctx.output_dir, ctx.cancel)


def test_the_default_lookup_names_an_unknown_downloader() -> None:
    with pytest.raises(PluginError, match="nope"):
        default_downloader_for("nope")


def test_media_candidates_need_a_url() -> None:
    with pytest.raises(ValidationError):
        MediaCandidate(url="clip.mp4")


def test_by_default_a_downloader_cannot_tell_offline_whether_it_handles_a_url() -> None:
    assert Echo().match(URL) is None


# ---------------------------------------------------------------------------------------------
# Filename templates
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "/abs/{title}.{ext}",  # absolute
        "../{title}.{ext}",  # escapes the output dir
        "a/../../{title}.{ext}",
        "{title:>10}.{ext}",  # format specs, conversions and positional fields are not fields
        "{title!r}.{ext}",
        "{0}.{ext}",
        "{}.{ext}",
        "{title.upper}.{ext}",
        "{title",  # unbalanced
        "",
    ],
)
def test_a_bad_filename_template_is_rejected(template: str, tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="filename_template"):
        DownloadContext(url=URL, output_dir=tmp_path, filename_template=template)


def test_a_filename_template_may_have_folders(tmp_path: Path) -> None:
    ctx = DownloadContext(url=URL, output_dir=tmp_path, filename_template="{site}/{title}.{ext}")

    assert ctx.filename_template == "{site}/{title}.{ext}"


def test_a_template_splits_into_literal_text_and_fields() -> None:
    assert template_parts("{title} [{id}].{ext}") == [
        ("", "title"),
        (" [", "id"),
        ("].", "ext"),
    ]
    assert template_parts("100% {{literal}}") == [("100% {literal}", None)]


def test_rendering_fills_in_the_fields() -> None:
    assert render_template(
        "{site}/{title}.{ext}", {"site": "a.com", "title": "Clip", "ext": "mp4"}
    ) == ("a.com/Clip.mp4")


def test_a_missing_field_renders_as_na() -> None:
    assert render_template("{title} [{id}].{ext}", {"title": "Clip", "ext": "mp4"}) == (
        "Clip [NA].mp4"
    )


@pytest.mark.parametrize(
    ("title", "rendered"),
    [
        ("a/b", "a_b.mp4"),
        ("..", "_.mp4"),
        ("../../etc/passwd", ".._.._etc_passwd.mp4"),
        ("nul\0byte", "nul_byte.mp4"),
        ("", "NA.mp4"),
    ],
)
def test_field_values_cannot_add_folders(title: str, rendered: str) -> None:
    assert render_template("{title}.{ext}", {"title": title, "ext": "mp4"}) == rendered


@given(
    st.dictionaries(st.sampled_from(["site", "title", "ext"]), st.text()),
)
def test_a_rendered_template_never_leaves_the_output_dir(fields: dict[str, str]) -> None:
    rendered = Path(render_template("{site}/{title}.{ext}", fields))

    assert not rendered.is_absolute()
    assert ".." not in rendered.parts
    assert len(rendered.parts) == 2
