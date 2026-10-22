"""
--------------------
- test_gallerydl.py -
--------------------

The gallery-dl downloader against the fixture site: offline matching, its direct-link extractor,
filename templates, options and headers reaching gallery-dl, its log and running jobs at once
"""

# System Imports
import asyncio
import importlib
import logging
from collections.abc import Iterator
from pathlib import Path

# Package Imports
import pytest
from pytest_httpserver import HTTPServer

# Local Imports
from support.fixture_site import FixtureSite, Page
from support.gallerydl_extractors import udltest_options
from udl.errors import AuthRequired, Cancelled, ErrorCode, ExtractionFailed
from udl.plugins.downloaders.base import CancelToken, DownloadContext, ProbeContext
from udl.plugins.downloaders.gallerydl import GalleryDlDownloader, GalleryDlMode, GalleryDlOptions


@pytest.fixture(params=list(GalleryDlMode))
def mode(request: pytest.FixtureRequest) -> GalleryDlMode:
    """Each way of running gallery-dl: its Python API, and its command line."""
    return request.param


@pytest.fixture
def udltest(mode: GalleryDlMode) -> Iterator[GalleryDlDownloader]:
    """gallery-dl with an extractor of the tests' own (fixtures/gallery-dl/udltest.py)."""
    with udltest_options(mode) as options:
        yield GalleryDlDownloader(options)


@pytest.mark.parametrize(
    ("page", "ok"),
    [
        (Page.DIRECT_FILE, True),  # its direct-link extractor handles media file URLs
        (Page.VIDEO, False),  # no extractor for the site's pages
        (Page.JS, False),
        (Page.NO_MEDIA, False),
    ],
)
async def test_it_handles_media_file_urls(
    fixture_site: FixtureSite, page: Page, ok: bool, mode: GalleryDlMode
) -> None:
    downloader = GalleryDlDownloader(GalleryDlOptions(mode=mode))

    result = await downloader.probe(ProbeContext(url=fixture_site.url(page)))

    assert result.ok is ok
    if not ok:
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSUPPORTED_URL


async def test_a_probe_finds_the_file_and_its_size(
    fixture_site: FixtureSite, mode: GalleryDlMode
) -> None:
    downloader = GalleryDlDownloader(GalleryDlOptions(mode=mode))

    result = await downloader.probe(ProbeContext(url=fixture_site.url(Page.DIRECT_FILE)))

    [media] = result.media
    assert media.url == fixture_site.url(Page.DIRECT_FILE)
    assert media.size_bytes == len(fixture_site.content(Page.DIRECT_FILE))
    assert result.title == "sample"


def test_a_site_extractor_matches_offline() -> None:
    match = GalleryDlDownloader().match("https://www.pixiv.net/en/artworks/123")

    assert match is not None
    assert match.detail == "pixiv:work"
    assert match.confidence >= 0.8


def test_the_direct_link_extractor_is_a_weak_match(fixture_site: FixtureSite) -> None:
    match = GalleryDlDownloader().match(fixture_site.url(Page.DIRECT_FILE))

    assert match is not None
    assert match.detail is not None
    assert match.detail.startswith("directlink")
    assert match.confidence < 0.5


def test_a_url_no_extractor_handles_is_no_match(fixture_site: FixtureSite) -> None:
    assert GalleryDlDownloader().match(fixture_site.url(Page.VIDEO)) is None


async def test_by_default_files_go_straight_into_output_dir(
    fixture_site: FixtureSite, tmp_path: Path, mode: GalleryDlMode
) -> None:
    ctx = DownloadContext(url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path)

    result = await GalleryDlDownloader(GalleryDlOptions(mode=mode)).download(ctx)

    [file] = result.files
    assert file.parent == tmp_path


@pytest.mark.parametrize(
    ("template", "name"),
    [
        ("{title}.{ext}", "sample.mp4"),  # no title: the file's name
        ("clips/{title}.{ext}", "clips/sample.mp4"),
        ("{{{title}}}.{ext}", "{sample}.mp4"),
        ("{title} [{id}].{ext}", "sample [NA].mp4"),  # a missing field
    ],
)
async def test_the_filename_template_names_the_file(
    fixture_site: FixtureSite, tmp_path: Path, template: str, name: str, mode: GalleryDlMode
) -> None:
    ctx = DownloadContext(
        url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path, filename_template=template
    )

    result = await GalleryDlDownloader(GalleryDlOptions(mode=mode)).download(ctx)

    assert result.files == [tmp_path / name]


async def test_raw_config_reaches_gallery_dl(
    fixture_site: FixtureSite, tmp_path: Path, mode: GalleryDlMode
) -> None:
    # The sample is far smaller than this minimum, so gallery-dl skips it.
    options = GalleryDlOptions(mode=mode, config={"downloader": {"filesize-min": "1M"}})
    ctx = DownloadContext(url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path)

    with pytest.raises(ExtractionFailed, match="smaller than"):
        await GalleryDlDownloader(options).download(ctx)


async def test_headers_are_sent(
    httpserver: HTTPServer, fixture_site: FixtureSite, tmp_path: Path, mode: GalleryDlMode
) -> None:
    sample = fixture_site.content(Page.DIRECT_FILE)
    httpserver.expect_request("/clip.mp4", headers={"X-Udl-Test": "yes"}).respond_with_data(
        sample, content_type="video/mp4"
    )
    ctx = DownloadContext(
        url=httpserver.url_for("/clip.mp4"), output_dir=tmp_path, headers={"X-Udl-Test": "yes"}
    )

    result = await GalleryDlDownloader(GalleryDlOptions(mode=mode)).download(ctx)

    assert [file.read_bytes() for file in result.files] == [sample]


async def test_gallery_dl_logs_into_udls_log_only(
    fixture_site: FixtureSite, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ctx = DownloadContext(url=fixture_site.url(Page.PRIVATE_FILE), output_dir=tmp_path)

    with caplog.at_level(logging.DEBUG), pytest.raises(AuthRequired):
        await GalleryDlDownloader().download(ctx)

    names = {record.name for record in caplog.records if record.levelno >= logging.INFO}
    assert "udl.plugins.downloaders.gallerydl" in names
    assert not {"download", "downloader.http", "directlink"} & names


async def test_jobs_at_the_same_time_all_succeed(
    fixture_site: FixtureSite, tmp_path: Path, mode: GalleryDlMode
) -> None:
    # gallery-dl's config is global, so in-process jobs take turns.
    urls = [fixture_site.url(Page.DIRECT_FILE), fixture_site.url(Page.OTHER_FILE)] * 2
    contexts = [
        DownloadContext(url=url, output_dir=tmp_path / str(n)) for n, url in enumerate(urls)
    ]

    downloader = GalleryDlDownloader(GalleryDlOptions(mode=mode))

    results = await asyncio.gather(*(downloader.download(ctx) for ctx in contexts))

    assert [len(result.files) for result in results] == [1, 1, 1, 1]


async def test_a_partial_download_is_resumed(
    fixture_site: FixtureSite, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sample = fixture_site.content(Page.DIRECT_FILE)
    (tmp_path / "sample.mp4.part").write_bytes(sample[:100])
    ctx = DownloadContext(
        url=fixture_site.url(Page.DIRECT_FILE),
        output_dir=tmp_path,
        filename_template="{title}.{ext}",
    )

    with caplog.at_level(logging.DEBUG, logger="udl"):
        result = await GalleryDlDownloader().download(ctx)

    assert [file.read_bytes() for file in result.files] == [sample]
    assert any("Resuming download at byte 100" in record.getMessage() for record in caplog.records)


async def test_a_file_downloaded_before_is_kept(
    fixture_site: FixtureSite, tmp_path: Path, mode: GalleryDlMode
) -> None:
    downloader = GalleryDlDownloader(GalleryDlOptions(mode=mode))
    ctx = DownloadContext(url=fixture_site.url(Page.DIRECT_FILE), output_dir=tmp_path)
    first = await downloader.download(ctx)

    again = await downloader.download(ctx)

    assert again.files == first.files


async def test_a_job_waiting_for_its_turn_can_be_cancelled(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    first_token, second_token = CancelToken(), CancelToken()
    started = asyncio.Event()
    first = asyncio.create_task(
        GalleryDlDownloader().download(
            DownloadContext(
                url=fixture_site.url(Page.SLOW_FILE),
                output_dir=tmp_path / "first",
                cancel=first_token,
                progress=lambda _: started.set(),
            )
        )
    )
    await started.wait()  # the first job has gallery-dl
    second = asyncio.create_task(
        GalleryDlDownloader().download(
            DownloadContext(
                url=fixture_site.url(Page.DIRECT_FILE),
                output_dir=tmp_path / "second",
                cancel=second_token,
            )
        )
    )
    await asyncio.sleep(0.3)

    second_token.cancel()
    with pytest.raises(Cancelled):
        await asyncio.wait_for(second, timeout=1)
    first_token.cancel()
    with pytest.raises(Cancelled):
        await first


async def test_an_extractor_that_finds_nothing_fails(
    udltest: GalleryDlDownloader, fixture_site: FixtureSite, tmp_path: Path
) -> None:
    url = fixture_site.url("/udltest/empty")

    probed = await udltest.probe(ProbeContext(url=url))

    assert probed.error is not None
    assert probed.error.code is ErrorCode.EXTRACTION_FAILED
    with pytest.raises(ExtractionFailed):
        await udltest.download(DownloadContext(url=url, output_dir=tmp_path))


@pytest.mark.parametrize(
    ("kind", "code"),
    [("error", ErrorCode.NOT_FOUND), ("unreachable", ErrorCode.NETWORK_ERROR)],
)
async def test_a_probe_reports_why_the_first_file_cannot_be_had(
    udltest: GalleryDlDownloader, fixture_site: FixtureSite, kind: str, code: ErrorCode
) -> None:
    result = await udltest.probe(ProbeContext(url=fixture_site.url(f"/udltest/{kind}")))

    assert result.error is not None
    assert result.error.code is code


async def test_a_gallery_with_a_missing_file_gets_the_rest(
    udltest: GalleryDlDownloader,
    fixture_site: FixtureSite,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ctx = DownloadContext(url=fixture_site.url("/udltest/partial"), output_dir=tmp_path)

    with caplog.at_level(logging.WARNING, logger="udl"):
        result = await udltest.download(ctx)

    assert result.files == [tmp_path / "pixel.png"]
    assert "could not download everything" in caplog.text


async def test_media_gallery_dl_hands_to_yt_dlp_is_not_checked(
    udltest: GalleryDlDownloader, fixture_site: FixtureSite
) -> None:
    result = await udltest.probe(ProbeContext(url=fixture_site.url("/udltest/ytdl")))

    assert result.ok is True
    assert result.media == []


async def test_when_its_python_api_breaks_its_command_line_is_used(
    fixture_site: FixtureSite,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # As if a gallery-dl upgrade changed the parts of its API that udl uses.
    def broken(*_: object, **__: object) -> None:
        raise AttributeError("'DownloadJob' object has no attribute 'out'")

    monkeypatch.setattr(importlib.import_module("gallery_dl.job"), "DownloadJob", broken)
    monkeypatch.setattr(
        importlib.import_module("gallery_dl.extractor.common").Extractor, "initialize", broken
    )
    downloader = GalleryDlDownloader()
    url = fixture_site.url(Page.DIRECT_FILE)

    with caplog.at_level(logging.WARNING, logger="udl"):
        probed = await downloader.probe(ProbeContext(url=url))
        result = await downloader.download(DownloadContext(url=url, output_dir=tmp_path))

    assert probed.ok is True
    assert [file.read_bytes() for file in result.files] == [fixture_site.content(Page.DIRECT_FILE)]
    assert caplog.text.count("command line instead") == 2
