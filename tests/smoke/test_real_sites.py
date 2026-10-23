"""
---------------------
- test_real_sites.py -
---------------------

udl against real sites, to catch what the local fixture site cannot: a site that changed, a tool
that broke, a site that refuses udl. They talk to the internet, so they never run by default:
`make smoke` (or `uv run pytest -m network`) runs them.

Each page was chosen for being small, public and long-lived. When one of these fails, check
the page in a browser first: a page that moved is fixed here, not in udl.
"""

# System Imports
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

# Package Imports
import pytest

# Local Imports
from support.browser import needs_chromium
from support.engine import connection
from udl.config import BrowserSettings, DBSettings, Settings
from udl.core.jobs import InstanceLock
from udl.core.models import DownloadRequest, JobInfo, ProbeRequest, SiteRequest
from udl.core.service import UDLService
from udl.db.models import JobStatus

FILE: Final = "https://www.python.org/static/img/python-logo.png"  # a direct file
VIDEO_PAGE: Final = "https://www.w3schools.com/html/html5_video.asp"  # <video><source>
VIDEO_FILE: Final = "mov_bbb.mp4"  # the video on VIDEO_PAGE
YOUTUBE: Final = "https://youtu.be/jNQXAC9IVRw?si=udl"  # "Me at the zoo", 19 s, a share link
WIKIMEDIA: Final = "https://commons.wikimedia.org/wiki/File:Example.jpg"  # one image, 4 versions
# What YouTube says to a machine it has seen download a lot.
BOT_CHECK: Final = re.compile(r"confirm you.re not a bot")


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """udl with its own plugins, an in-memory database and the browser in a temporary profile."""
    config = Settings(
        download_dir=tmp_path / "downloads",
        db=DBSettings(connections=[connection(primary=True)]),
        browser=BrowserSettings(profile_dir=tmp_path / "profile"),
    )
    async with UDLService(config, lock=InstanceLock(tmp_path / "udl.lock")) as started:
        yield started


async def download(service: UDLService, url: str, tool: str | None = None) -> JobInfo:
    [job] = (await service.download(DownloadRequest(urls=[url], tool=tool))).jobs
    assert job.status is JobStatus.FINISHED, (job.error, job.attempts)
    return job


async def test_http_downloads_a_file(service: UDLService) -> None:
    job = await download(service, FILE)

    assert job.tool == "http"
    assert [file.name for file in job.files] == ["python-logo.png"]
    assert job.files[0].read_bytes().startswith(b"\x89PNG")


async def test_youtube_links_are_downloaded_once_with_yt_dlp(service: UDLService) -> None:
    jobs = (await service.download(DownloadRequest(urls=[YOUTUBE, YOUTUBE.split("?")[0]]))).jobs

    [job] = jobs  # the youtube site plugin made both links the same video
    if job.error and BOT_CHECK.search(job.error.message):
        pytest.skip(
            "YouTube wants this machine to sign in (its bot check, after many downloads): try "
            "later, or sign in with `udl browser login https://www.youtube.com`"
        )
    assert job.status is JobStatus.FINISHED, (job.error, job.attempts)
    assert (job.url, job.tool) == ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "yt-dlp")
    assert job.size_bytes > 100_000


async def test_gallery_dl_downloads_an_image_and_lists_it_once(service: UDLService) -> None:
    job = await download(service, WIKIMEDIA, tool="gallery-dl")

    [image] = job.files
    assert image.read_bytes().startswith(b"\xff\xd8")  # a JPEG
    assert job.size_bytes == image.stat().st_size


async def test_the_scraper_finds_the_video_on_a_page(service: UDLService) -> None:
    job = await download(service, VIDEO_PAGE, tool="scraper")

    assert [file.name for file in job.files] == [VIDEO_FILE]


async def test_the_browser_finds_the_video_on_a_page(service: UDLService) -> None:
    needs_chromium()

    job = await download(service, VIDEO_PAGE, tool="browser")

    assert [file.name for file in job.files] == [VIDEO_FILE]


async def test_a_new_site_is_probed_downloaded_and_remembered(service: UDLService) -> None:
    probed = await service.probe(ProbeRequest(url=VIDEO_PAGE))
    job = await download(service, VIDEO_PAGE)

    assert any(result.ok for result in probed.results), probed.results
    assert job.files  # (yt-dlp, the first to find it, keeps each of the page's formats)
    [strategy] = (await service.get_site(SiteRequest(site=VIDEO_PAGE))).strategies
    assert (strategy.tool, strategy.success_count) == (job.tool, 1)
