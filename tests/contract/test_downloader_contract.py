"""
-------------------------------
- test_downloader_contract.py -
-------------------------------

The behaviour every downloader plugin must have. Each test runs once per downloader in CASES,
against the local fixture site; a new downloader starts by adding itself there (and watching
this suite fail).
"""

# System Imports
import asyncio
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

# Package Imports
import pytest

# Local Imports
from support.downloaders import FakeDownloader, FakeMedia, FakeOptions
from support.downloads import session_cookie
from support.fixture_site import COOKIE_NAME, COOKIE_VALUE, FixtureSite, Page
from udl.errors import (
    AuthRequired,
    Cancelled,
    ConfigError,
    ErrorCode,
    NotFound,
    ToolUnavailable,
    UnsupportedURL,
)
from udl.plugins.downloaders.base import (
    CancelToken,
    DownloadContext,
    Downloader,
    DownloadProgress,
    MatchResult,
    ProbeContext,
    ProbeResult,
    Requirement,
)
from udl.plugins.registry import DOWNLOADERS, PluginRegistry

# A cancelled download must stop this soon (the slow file takes far longer to download).
CANCEL_WITHIN: Final[float] = 3.0
MISSING: Final[str] = "/gone.mp4"


@dataclass(frozen=True)
class Case:
    """A downloader, and the fixture site pages it is meant for."""

    downloader: Downloader[Any]
    site: FixtureSite
    supported: Page  # downloads to the sample MP4
    private: Page  # the same behind the login cookie
    slow: Page  # downloads to the slow file

    def url(self, page: str) -> str:
        return self.site.url(page)


def fake(site: FixtureSite) -> Case:
    sample = site.content(Page.DIRECT_FILE)
    options = FakeOptions(
        media={
            site.url(Page.DIRECT_FILE): FakeMedia(content=sample, filename="sample.mp4"),
            site.url(Page.PRIVATE_FILE): FakeMedia(
                content=sample, filename="sample.mp4", cookie=f"{COOKIE_NAME}={COOKIE_VALUE}"
            ),
            site.url(Page.SLOW_FILE): FakeMedia(content=bytes(200_000), delay=0.05),
        },
        errors={site.url(MISSING): ErrorCode.NOT_FOUND},
        chunk_size=1_000,
    )
    return Case(FakeDownloader(options), site, Page.DIRECT_FILE, Page.PRIVATE_FILE, Page.SLOW_FILE)


def installed(name: str) -> Downloader[Any]:
    """A downloader found the way udl finds it (by entry point), with its default options."""
    return PluginRegistry.discover().get(DOWNLOADERS, name)()


def ytdlp(site: FixtureSite) -> Case:
    return Case(installed("yt-dlp"), site, Page.DIRECT_FILE, Page.PRIVATE_FILE, Page.SLOW_FILE)


# How to get each downloader, and the pages it is meant for.
CASES: dict[str, Callable[[FixtureSite], Case]] = {
    "fake": fake,
    "yt-dlp": ytdlp,
}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest, fixture_site: FixtureSite) -> Case:
    return CASES[request.param](fixture_site)


@pytest.fixture
def downloader(case: Case) -> Downloader[Any]:
    return case.downloader


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty current directory, so a test can see whether anything was written to it."""
    path = tmp_path / "cwd"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


def files_under(*dirs: Path) -> set[Path]:
    return {path for directory in dirs for path in directory.rglob("*") if path.is_file()}


# ---------------------------------------------------------------------------------------------
# Options and availability
# ---------------------------------------------------------------------------------------------


def test_its_options_have_defaults_and_reject_unknown_keys(downloader: Downloader[Any]) -> None:
    downloader.options_model()  # every option has a default

    with pytest.raises(ConfigError, match=downloader.name):
        type(downloader).configure({"udl_no_such_option": True})


def test_it_is_available_when_its_requirements_are_installed(downloader: Downloader[Any]) -> None:
    assert downloader.missing() == []
    assert downloader.available() is True


async def test_without_its_requirements_it_is_unavailable(
    downloader: Downloader[Any], case: Case, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never_met(_: Requirement) -> bool:
        return False

    monkeypatch.setattr(Requirement, "met", never_met)
    needs = list(downloader.requirements())

    assert downloader.available() is not bool(needs)
    if needs:
        result = await downloader.probe(ProbeContext(url=case.url(case.supported)))
        assert result.error is not None
        assert result.error.code is ErrorCode.TOOL_UNAVAILABLE
        with pytest.raises(ToolUnavailable):
            await downloader.download(
                DownloadContext(url=case.url(case.supported), output_dir=tmp_path)
            )


def test_match_works_offline(
    downloader: Downloader[Any], case: Case, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*_: object, **__: object) -> None:
        raise AssertionError("match() used the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)

    for page in [case.supported, Page.NO_MEDIA, MISSING]:
        match = downloader.match(case.url(page))
        assert match is None or isinstance(match, MatchResult)


# ---------------------------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------------------------


async def test_probe_finds_the_media(downloader: Downloader[Any], case: Case) -> None:
    result = await downloader.probe(ProbeContext(url=case.url(case.supported)))

    assert result.error is None
    assert result.ok is True
    assert result.tool == downloader.name
    assert result.confidence > 0
    assert ProbeResult.model_validate(result.model_dump()) == result


async def test_probe_never_writes_files(
    downloader: Downloader[Any], case: Case, workdir: Path, isolated_user_dirs: Path
) -> None:
    before = files_under(workdir, isolated_user_dirs)

    for page in [case.supported, case.private, Page.NO_MEDIA, MISSING]:
        await downloader.probe(ProbeContext(url=case.url(page)))

    assert files_under(workdir, isolated_user_dirs) == before


@pytest.mark.parametrize(
    ("page", "code"),
    [
        (Page.NO_MEDIA, ErrorCode.UNSUPPORTED_URL),
        (MISSING, ErrorCode.NOT_FOUND),
    ],
)
async def test_probe_reports_what_went_wrong(
    downloader: Downloader[Any], case: Case, page: str, code: ErrorCode
) -> None:
    result = await downloader.probe(ProbeContext(url=case.url(page)))

    assert result.ok is False
    assert result.error is not None
    assert (result.error.code, result.error.tool) == (code, downloader.name)


async def test_probe_without_the_login_cookie_reports_auth_required(
    downloader: Downloader[Any], case: Case
) -> None:
    result = await downloader.probe(ProbeContext(url=case.url(case.private)))

    assert result.error is not None
    assert result.error.code is ErrorCode.AUTH_REQUIRED


async def test_probe_with_the_login_cookie_finds_the_media(
    downloader: Downloader[Any], case: Case
) -> None:
    ctx = ProbeContext(url=case.url(case.private), cookies=[session_cookie(case.site)])

    result = await downloader.probe(ctx)

    assert result.error is None


# ---------------------------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------------------------


async def test_download_writes_the_media_into_output_dir_only(
    downloader: Downloader[Any],
    case: Case,
    tmp_path: Path,
    workdir: Path,
    isolated_user_dirs: Path,
) -> None:
    out = tmp_path / "out"
    before = files_under(workdir, isolated_user_dirs)

    result = await downloader.download(
        DownloadContext(url=case.url(case.supported), output_dir=out)
    )

    [file] = result.files
    assert file.parent.is_relative_to(out)
    assert file.read_bytes() == case.site.content(Page.DIRECT_FILE)
    assert result.size_bytes == file.stat().st_size
    assert (result.tool, result.url) == (downloader.name, case.url(case.supported))
    assert files_under(out) == {file}
    assert files_under(workdir, isolated_user_dirs) == before


async def test_download_reports_progress_on_the_event_loop_thread(
    downloader: Downloader[Any], case: Case, tmp_path: Path
) -> None:
    reports: list[tuple[int, DownloadProgress]] = []
    ctx = DownloadContext(
        url=case.url(case.supported),
        output_dir=tmp_path,
        progress=lambda p: reports.append((threading.get_ident(), p)),
    )

    result = await downloader.download(ctx)

    assert reports
    assert {thread for thread, _ in reports} == {threading.get_ident()}
    assert reports[-1][1].downloaded_bytes == result.size_bytes


async def test_download_with_the_login_cookie_gets_the_private_media(
    downloader: Downloader[Any], case: Case, tmp_path: Path
) -> None:
    ctx = DownloadContext(
        url=case.url(case.private), output_dir=tmp_path, cookies=[session_cookie(case.site)]
    )

    result = await downloader.download(ctx)

    assert [file.read_bytes() for file in result.files] == [case.site.content(Page.DIRECT_FILE)]


@pytest.mark.parametrize(
    ("page", "error"),
    [
        (Page.NO_MEDIA, UnsupportedURL),
        (MISSING, NotFound),
        ("private", AuthRequired),
    ],
)
async def test_a_failed_download_raises_what_went_wrong_and_leaves_no_file(
    downloader: Downloader[Any], case: Case, tmp_path: Path, page: str, error: type[Exception]
) -> None:
    url = case.url(case.private if page == "private" else page)

    with pytest.raises(error) as caught:
        await downloader.download(DownloadContext(url=url, output_dir=tmp_path))

    assert getattr(caught.value, "tool", None) == downloader.name
    assert files_under(tmp_path) == set()


async def test_cancelling_stops_a_download_within_seconds(
    downloader: Downloader[Any], case: Case, tmp_path: Path
) -> None:
    token = CancelToken()
    started = asyncio.Event()
    ctx = DownloadContext(
        url=case.url(case.slow),
        output_dir=tmp_path,
        cancel=token,
        progress=lambda p: started.set() if p.downloaded_bytes else None,
    )
    task = asyncio.create_task(downloader.download(ctx))
    await asyncio.wait_for(started.wait(), timeout=10)

    token.cancel()
    cancelled_at = time.monotonic()

    with pytest.raises(Cancelled):
        await asyncio.wait_for(task, timeout=CANCEL_WITHIN)
    assert time.monotonic() - cancelled_at < CANCEL_WITHIN
