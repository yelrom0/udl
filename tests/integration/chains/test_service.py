"""
-------------------
- test_service.py -
-------------------

Tag chains end to end: UDLService with udl's own downloaders against the local fixture site. It
suggests the real media first on each page, tests and saves a chain, and downloads a page that no
downloader handles by following it.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.browser import needs_chromium
from support.engine import connection
from support.fixture_site import FixtureSite, Page
from udl.chains.models import FetchMode
from udl.config import BrowserSettings, DBSettings, Settings
from udl.core.jobs import InstanceLock
from udl.core.models import (
    ChainTestRequest,
    DownloadRequest,
    SetChainRequest,
    SuggestChainRequest,
)
from udl.core.service import UDLService
from udl.db.models import JobStatus
from udl.errors import ErrorCode


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A started service with udl's own plugins, on an in-memory database."""
    config = Settings(
        download_dir=tmp_path / "downloads",
        probe_order=["http", "yt-dlp", "gallery-dl", "scraper"],
        db=DBSettings(connections=[connection(primary=True)]),
        browser=BrowserSettings(profile_dir=tmp_path / "profile"),
    )
    async with UDLService(config, lock=InstanceLock(tmp_path / "udl.lock")) as started:
        yield started


@pytest.mark.parametrize(
    ("page", "first"),
    [
        (Page.VIDEO, "video > source@src"),
        (Page.PLAYER, "video#clip > source@src"),
        (Page.EMBED, "iframe#player@src >> video#clip > source@src"),
        (Page.JSON, "script#data::text >> json:video.src"),
        (Page.VIDEOS, "video > source@src"),
    ],
)
async def test_the_real_media_is_suggested_first(
    service: UDLService, fixture_site: FixtureSite, page: Page, first: str
) -> None:
    found = await service.suggest_chain(
        SuggestChainRequest(url=fixture_site.url(page), fetch=FetchMode.STATIC)
    )

    best = found.suggestions[0]
    assert (best.shorthand, best.media.url) == (first, fixture_site.url(Page.DIRECT_FILE))
    assert (best.reachable, best.media.content_type) == (True, "video/mp4")
    assert best.media.size_bytes == len(fixture_site.content(Page.DIRECT_FILE))
    assert all("nth-" not in s.shorthand for s in found.suggestions if page is not Page.VIDEOS)


async def test_a_page_only_a_browser_renders_is_suggested_for_in_the_browser(
    service: UDLService, fixture_site: FixtureSite
) -> None:
    needs_chromium()

    found = await service.suggest_chain(SuggestChainRequest(url=fixture_site.url(Page.JS)))

    assert found.fetch is FetchMode.BROWSER
    best = found.suggestions[0]
    assert (best.shorthand, best.media.url) == (
        "div#player video@src",
        fixture_site.url(Page.DIRECT_FILE),
    )


async def test_a_page_that_is_gone_says_so(service: UDLService, fixture_site: FixtureSite) -> None:
    found = await service.suggest_chain(
        SuggestChainRequest(url=fixture_site.url("/gone.html"), fetch=FetchMode.STATIC)
    )

    assert found.suggestions == []
    assert found.error is not None
    assert (found.error.code, found.error.tool) == (ErrorCode.NOT_FOUND, "scraper")


async def test_a_page_no_downloader_handles_is_downloaded_by_its_chain(
    service: UDLService, fixture_site: FixtureSite
) -> None:
    url = fixture_site.url(Page.JSON)
    before = await service.download(DownloadRequest(urls=[url]))
    [suggestion] = (
        await service.suggest_chain(SuggestChainRequest(url=url, fetch=FetchMode.STATIC))
    ).suggestions

    report = await service.test_chain(ChainTestRequest(url=url, chain=suggestion.chain))
    saved = await service.set_site_chain(SetChainRequest(site=url, chain=suggestion.chain))
    after = await service.download(DownloadRequest(urls=[url]))

    assert before.jobs[0].status is JobStatus.FAILED
    assert report.ok
    assert [step.step for step in report.steps] == ["script#data::text", "json:video.src"]
    assert report.steps[1].found == ["/media/sample.mp4"]
    [job] = after.jobs
    assert (job.status, job.tool, job.handed_off_to, job.strategy_id) == (
        JobStatus.FINISHED,
        "scraper",
        "http",
        saved.id,
    )
    assert [path.read_bytes() for path in job.files] == [fixture_site.content(Page.DIRECT_FILE)]
