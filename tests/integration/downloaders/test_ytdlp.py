"""
----------------
- test_ytdlp.py -
----------------

The yt-dlp downloader against the fixture site: the pages its generic extractor handles, offline
matching, filename templates, and options and headers reaching yt-dlp
"""

# System Imports
import logging
from collections.abc import Iterator
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr
from pytest_httpserver import HTTPServer

# Local Imports
from support.fixture_site import FixtureSite, Page
from udl.errors import ErrorCode, ExtractionFailed
from udl.plugins.downloaders.base import (
    Cookie,
    DownloadContext,
    DownloadProgress,
    MediaKind,
    ProbeContext,
)
from udl.plugins.downloaders.ytdlp import YtDlpDownloader, YtDlpOptions, progress_from_status


@pytest.fixture
def ip_server() -> Iterator[HTTPServer]:
    """A server reached by IP address rather than host name."""
    server = HTTPServer(host="127.0.0.1")
    server.start()
    yield server
    server.clear()
    server.stop()


@pytest.mark.parametrize(
    ("page", "ok"),
    [
        (Page.DIRECT_FILE, True),
        (Page.VIDEO, True),  # its generic extractor finds HTML5 <video> tags
        (Page.PLAYER, True),
        (Page.EMBED, False),  # an iframe it does not know
        (Page.JS, False),  # needs a browser
        (Page.NO_MEDIA, False),
    ],
)
async def test_it_handles_direct_files_and_video_tags(
    fixture_site: FixtureSite, page: Page, ok: bool
) -> None:
    result = await YtDlpDownloader().probe(ProbeContext(url=fixture_site.url(page)))

    assert result.ok is ok
    if not ok:
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSUPPORTED_URL


async def test_it_downloads_the_video_on_a_page(fixture_site: FixtureSite, tmp_path: Path) -> None:
    ctx = DownloadContext(url=fixture_site.url(Page.VIDEO), output_dir=tmp_path)

    result = await YtDlpDownloader().download(ctx)

    assert [file.read_bytes() for file in result.files] == [fixture_site.content(Page.DIRECT_FILE)]
    assert result.title


async def test_a_probe_finds_the_media_url(fixture_site: FixtureSite) -> None:
    result = await YtDlpDownloader().probe(ProbeContext(url=fixture_site.url(Page.VIDEO)))

    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]


async def test_the_generic_extractor_gets_low_confidence(fixture_site: FixtureSite) -> None:
    result = await YtDlpDownloader().probe(ProbeContext(url=fixture_site.url(Page.DIRECT_FILE)))

    assert 0 < result.confidence < 0.5


def test_a_site_extractor_matches_offline() -> None:
    match = YtDlpDownloader().match("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    assert match is not None
    assert match.detail == "Youtube"
    assert match.confidence >= 0.8


def test_the_generic_extractor_is_never_an_offline_match(fixture_site: FixtureSite) -> None:
    assert YtDlpDownloader().match(fixture_site.url(Page.DIRECT_FILE)) is None


@pytest.mark.parametrize(
    ("template", "name"),
    [
        ("{title}.{ext}", "sample.mp4"),
        ("clips/{title}.{ext}", "clips/sample.mp4"),
        ("100% {title}.{ext}", "100% sample.mp4"),  # a literal % is not a yt-dlp field
        ("{title} [{no_such_field}].{ext}", "sample [NA].mp4"),
    ],
)
async def test_the_filename_template_names_the_file(
    fixture_site: FixtureSite, tmp_path: Path, template: str, name: str
) -> None:
    ctx = DownloadContext(
        url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path, filename_template=template
    )

    result = await YtDlpDownloader().download(ctx)

    assert result.files == [tmp_path / name]


async def test_options_reach_yt_dlp(fixture_site: FixtureSite, tmp_path: Path) -> None:
    downloader = YtDlpDownloader.configure({"format": "udl-no-such-format"})
    ctx = DownloadContext(url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path)

    with pytest.raises(ExtractionFailed, match="format is not available"):
        await downloader.download(ctx)


async def test_raw_params_reach_yt_dlp(fixture_site: FixtureSite, tmp_path: Path) -> None:
    downloader = YtDlpDownloader(YtDlpOptions(params={"outtmpl": {"default": "raw.%(ext)s"}}))
    ctx = DownloadContext(url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path)

    result = await downloader.download(ctx)

    assert result.files == [tmp_path / "raw.mp4"]


async def test_headers_are_sent(
    httpserver: HTTPServer, fixture_site: FixtureSite, tmp_path: Path
) -> None:
    sample = fixture_site.content(Page.DIRECT_FILE)
    httpserver.expect_request(
        "/clip.mp4", headers={"Referer": "https://ref.example/"}
    ).respond_with_data(sample, content_type="video/mp4")
    ctx = DownloadContext(
        url=httpserver.url_for("/clip.mp4"),
        output_dir=tmp_path,
        headers={"Referer": "https://ref.example/"},
    )

    result = await YtDlpDownloader().download(ctx)

    assert [file.read_bytes() for file in result.files] == [sample]


async def test_a_page_with_two_videos_is_a_playlist_of_both(fixture_site: FixtureSite) -> None:
    result = await YtDlpDownloader().probe(ProbeContext(url=fixture_site.url(Page.VIDEOS)))

    assert [media.url for media in result.media] == [
        fixture_site.url(Page.DIRECT_FILE),
        fixture_site.url(Page.OTHER_FILE),
    ]
    assert {media.kind for media in result.media} == {MediaKind.FILE}


async def test_downloading_a_playlist_gets_every_video(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    ctx = DownloadContext(url=fixture_site.url(Page.VIDEOS), output_dir=tmp_path)

    result = await YtDlpDownloader().download(ctx)

    assert len(result.files) == 2
    assert {file.read_bytes() for file in result.files} == {fixture_site.content(Page.DIRECT_FILE)}


async def test_a_run_that_downloads_nothing_has_failed(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    downloader = YtDlpDownloader(YtDlpOptions(params={"skip_download": True}))
    ctx = DownloadContext(url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path)

    with pytest.raises(ExtractionFailed):
        await downloader.download(ctx)


async def test_yt_dlp_warnings_go_to_udls_log(
    fixture_site: FixtureSite, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="udl"):
        await YtDlpDownloader().probe(ProbeContext(url=fixture_site.url(Page.VIDEO)))

    assert any(
        record.name == "udl.plugins.downloaders.ytdlp"
        and record.levelno == logging.INFO
        and "generic" in record.getMessage()
        for record in caplog.records
    )


async def test_cookies_are_sent_to_a_site_reached_by_ip(
    ip_server: HTTPServer, fixture_site: FixtureSite, tmp_path: Path
) -> None:
    sample = fixture_site.content(Page.DIRECT_FILE)
    ip_server.expect_request("/clip.mp4", headers={"Cookie": "token=abc"}).respond_with_data(
        sample, content_type="video/mp4"
    )
    cookie = Cookie(name="token", value=SecretStr("abc"), domain="127.0.0.1")
    ctx = DownloadContext(url=ip_server.url_for("/clip.mp4"), output_dir=tmp_path, cookies=[cookie])

    result = await YtDlpDownloader().download(ctx)

    assert [file.read_bytes() for file in result.files] == [sample]


@pytest.mark.parametrize(
    ("status", "progress"),
    [
        (
            {
                "status": "downloading",
                "downloaded_bytes": 10,
                "total_bytes": 100,
                "speed": 5.5,
                "eta": 16,
                "filename": "/out/a.mp4",
            },
            DownloadProgress(
                downloaded_bytes=10, total_bytes=100, speed=5.5, eta=16, file=Path("/out/a.mp4")
            ),
        ),
        (
            {"status": "downloading", "downloaded_bytes": 10, "total_bytes_estimate": 99.6},
            DownloadProgress(downloaded_bytes=10, total_bytes=99),
        ),
        (
            {"status": "downloading", "downloaded_bytes": None, "speed": None, "eta": -1},
            DownloadProgress(downloaded_bytes=0),
        ),
        (
            {"status": "finished", "downloaded_bytes": 100, "total_bytes": 100},
            DownloadProgress(downloaded_bytes=100, total_bytes=100),
        ),
        ({"status": "error"}, None),
    ],
)
def test_progress_hook_statuses_become_progress(
    status: dict[str, object], progress: DownloadProgress | None
) -> None:
    assert progress_from_status(status) == progress
