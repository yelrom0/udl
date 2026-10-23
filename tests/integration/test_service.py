"""
-------------------
- test_service.py -
-------------------

The resolution engine end to end: UDLService with udl's own downloaders against the local
fixture site. It works out which tool downloads each page, remembers it for the site, signs in
when a page wants a login, and stops when a page is gone.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.browser import needs_chromium
from support.downloads import session_cookie
from support.engine import StaticCookies, connection
from support.fixture_site import FixtureSite, Page
from udl.config import BrowserSettings, DBSettings, Settings
from udl.core.jobs import InstanceLock
from udl.core.models import DownloadRequest, JobInfo, JobRequest, ProbeRequest, SiteRequest
from udl.core.service import UDLService
from udl.db.models import JobStatus
from udl.errors import DoesNotExist, ErrorCode

# The chain without the browser, for pages that do not need it (Chromium is slow to start).
WITHOUT_BROWSER = ["http", "yt-dlp", "gallery-dl", "scraper"]

type Make = Callable[..., Awaitable[UDLService]]


@pytest.fixture
async def make(tmp_path: Path) -> AsyncIterator[Make]:
    """Makes started services with udl's own plugins, on an in-memory database."""
    services: list[UDLService] = []

    async def make(
        *, probe_order: list[str] | None = None, cookies: StaticCookies | None = None
    ) -> UDLService:
        config = Settings(
            download_dir=tmp_path / "downloads",
            probe_order=probe_order or WITHOUT_BROWSER,
            db=DBSettings(connections=[connection(primary=True)]),
            browser=BrowserSettings(profile_dir=tmp_path / "profile"),
        )
        service = UDLService(config, cookies=cookies, lock=InstanceLock(tmp_path / "udl.lock"))
        await service.start()
        services.append(service)
        return service

    yield make
    for service in services:
        await service.close()


async def download(service: UDLService, url: str) -> JobInfo:
    [job] = (await service.download(DownloadRequest(urls=[url]))).jobs
    return job


def tried(job: JobInfo) -> list[tuple[str, ErrorCode | None]]:
    return [(a.tool, a.error.code if a.error else None) for a in job.attempts]


async def test_a_direct_file_is_downloaded_by_http_and_remembered(
    fixture_site: FixtureSite, make: Make
) -> None:
    service = await make()

    job = await download(service, fixture_site.url(Page.DIRECT_FILE))

    assert job.status is JobStatus.FINISHED, job.error
    assert (job.site_key, job.tool) == ("localhost", "http")
    assert [p.read_bytes() for p in job.files] == [fixture_site.content(Page.DIRECT_FILE)]
    [strategy] = (await service.get_site(SiteRequest(site="localhost"))).strategies
    assert (strategy.tool, strategy.source, strategy.success_count) == ("http", "probe", 1)


async def test_a_video_page_is_downloaded_by_the_first_tool_that_can(
    fixture_site: FixtureSite, make: Make
) -> None:
    service = await make()

    job = await download(service, fixture_site.url(Page.VIDEO))

    assert job.status is JobStatus.FINISHED, job.error
    # http sees a web page, not a file; yt-dlp's generic extractor finds the <video>.
    assert tried(job) == [("http", ErrorCode.UNSUPPORTED_URL), ("yt-dlp", None)]
    assert [p.read_bytes() for p in job.files] == [fixture_site.content(Page.DIRECT_FILE)]


async def test_the_next_download_from_the_site_uses_what_worked(
    fixture_site: FixtureSite, make: Make
) -> None:
    service = await make()
    await download(service, fixture_site.url(Page.VIDEO))

    job = await download(service, fixture_site.url(Page.PLAYER))

    assert job.status is JobStatus.FINISHED, job.error
    assert [(a.tool, a.source) for a in job.attempts] == [("yt-dlp", "stored")]
    [strategy] = (await service.get_site(SiteRequest(site="localhost"))).strategies
    assert strategy.success_count == 2


async def test_a_page_that_wants_a_login_is_downloaded_with_the_site_s_cookies(
    fixture_site: FixtureSite, make: Make
) -> None:
    service = await make(cookies=StaticCookies([session_cookie(fixture_site)]))

    job = await download(service, fixture_site.url(Page.PRIVATE))

    assert job.status is JobStatus.FINISHED, job.error
    assert [(a.tool, a.signed_in, a.error and a.error.code) for a in job.attempts] == [
        ("http", False, ErrorCode.AUTH_REQUIRED),
        ("http", True, ErrorCode.UNSUPPORTED_URL),  # signed in, it is a page
        ("yt-dlp", True, None),
    ]
    assert [p.read_bytes() for p in job.files] == [fixture_site.content(Page.PRIVATE_FILE)]
    [strategy] = (await service.get_site(SiteRequest(site="localhost"))).strategies
    assert (strategy.tool, strategy.requires_auth) == ("yt-dlp", True)


async def test_a_page_whose_media_needs_javascript_is_downloaded_in_the_browser(
    fixture_site: FixtureSite, make: Make
) -> None:
    needs_chromium()
    service = await make(probe_order=[*WITHOUT_BROWSER, "browser"])

    job = await download(service, fixture_site.url(Page.JS))

    assert job.status is JobStatus.FINISHED, job.error
    assert [a.tool for a in job.attempts] == [*WITHOUT_BROWSER, "browser"]
    assert (job.tool, job.handed_off_to) == ("browser", "http")
    assert [p.read_bytes() for p in job.files] == [fixture_site.content(Page.DIRECT_FILE)]


async def test_a_page_without_media_finds_no_strategy(
    fixture_site: FixtureSite, make: Make
) -> None:
    service = await make()

    job = await download(service, fixture_site.url(Page.NO_MEDIA))

    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert job.error.code is ErrorCode.NO_STRATEGY_FOUND
    assert [a.tool for a in job.attempts] == WITHOUT_BROWSER
    with pytest.raises(DoesNotExist):  # (nothing worked, so there is nothing to remember)
        await service.get_site(SiteRequest(site="localhost"))


async def test_a_page_that_is_gone_stops_at_once(fixture_site: FixtureSite, make: Make) -> None:
    service = await make()

    job = await download(service, fixture_site.url("/no/such/page.mp4"))

    assert job.status is JobStatus.FAILED
    assert tried(job) == [("http", ErrorCode.NOT_FOUND)]


async def test_probing_reports_what_each_tool_sees(fixture_site: FixtureSite, make: Make) -> None:
    service = await make()

    report = await service.probe(ProbeRequest(url=fixture_site.url(Page.VIDEO), all_tools=True))

    assert [(r.tool, r.ok) for r in report.results] == [
        ("http", False),
        ("yt-dlp", True),
        ("gallery-dl", False),
        ("scraper", True),
    ]
    scraper = report.results[-1]
    assert [m.url for m in scraper.media] == [fixture_site.url(Page.DIRECT_FILE)]


async def test_a_slow_download_can_be_cancelled(fixture_site: FixtureSite, make: Make) -> None:
    service = await make()
    batch = await service.download(
        DownloadRequest(urls=[fixture_site.url(Page.SLOW_FILE)], wait=False)
    )
    await asyncio.sleep(0.5)

    [job] = (await service.cancel_download(JobRequest(id=batch.id))).downloads

    assert job.status is JobStatus.CANCELLED
    assert [a.error.code for a in job.attempts if a.error] == [ErrorCode.CANCELLED]
