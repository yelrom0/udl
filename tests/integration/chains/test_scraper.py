"""
------------------
- test_scraper.py -
------------------

The scraper following tag chains on the fixture site (and on pages scripted per test): a direct
<video>, an iframe followed to its <source>, a video in JSON, keeping every find, the chain's
headers and handoff, and the errors that say what went wrong
"""

# System Imports
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pytest_httpserver import HTTPServer

# Local Imports
from support.downloaders import Lookup
from support.fixture_site import COOKIE_NAME, COOKIE_VALUE, FixtureSite, Page
from udl.chains.models import StepTrace, TagChain
from udl.errors import ConfigError, ErrorCode, ExtractionFailed, NotFound
from udl.plugins.downloaders.base import Cookie, DownloadContext, MediaKind, ProbeContext
from udl.plugins.downloaders.scraper import ScraperDownloader, ScraperOptions


def scraper(chain: str | dict[str, Any]) -> ScraperDownloader:
    return ScraperDownloader(ScraperOptions(chain=TagChain.model_validate(chain)))


async def test_a_chain_finds_a_direct_video(fixture_site: FixtureSite) -> None:
    url = fixture_site.url(Page.VIDEO)

    result = await scraper("video > source[type='video/mp4']@src").probe(ProbeContext(url=url))

    assert result.ok, result.error
    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]
    assert (result.media[0].kind, result.media[0].found_by) == (MediaKind.FILE, "tag chain")
    assert result.title == "A page with a video"
    assert result.handoff is not None
    assert (result.handoff.tool, result.handoff.headers) == ("http", {"Referer": url})


async def test_a_chain_follows_an_iframe_to_its_source(fixture_site: FixtureSite) -> None:
    chain = "iframe#player@src >> video#clip source@src"

    result = await scraper(chain).probe(ProbeContext(url=fixture_site.url(Page.EMBED)))

    assert result.ok, result.error
    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]
    assert result.handoff is not None
    assert result.handoff.headers == {"Referer": fixture_site.url(Page.PLAYER)}


async def test_a_chain_downloads_the_video_in_json(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    downloader = scraper("script#data::text >> json:video.src")
    ctx = DownloadContext(url=fixture_site.url(Page.JSON), output_dir=tmp_path)

    result = await downloader.download(ctx)

    assert [path.read_bytes() for path in result.files] == [fixture_site.content(Page.DIRECT_FILE)]
    assert (result.tool, result.handed_off_to) == ("scraper", "http")
    assert result.title == "A page with its video in JSON"


async def test_a_chain_that_keeps_all_downloads_all(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    ctx = DownloadContext(
        url=fixture_site.url(Page.VIDEOS), output_dir=tmp_path, filename_template="{title}.{ext}"
    )

    result = await scraper("video source@src | all").download(ctx)

    assert sorted(path.name for path in result.files) == ["other.mp4", "sample.mp4"]


async def test_a_chain_that_finds_nothing_says_which_step(fixture_site: FixtureSite) -> None:
    downloader = scraper("iframe#player@src >> audio@src")

    result = await downloader.probe(ProbeContext(url=fixture_site.url(Page.EMBED)))

    assert result.error is not None
    assert (result.error.code, result.error.tool) == (ErrorCode.EXTRACTION_FAILED, "scraper")
    assert "step 3 (audio@src) found nothing" in result.error.message
    assert fixture_site.url(Page.PLAYER) in result.error.message


async def test_a_chain_s_page_that_is_not_found_is_not_found(fixture_site: FixtureSite) -> None:
    ctx = DownloadContext(url=fixture_site.url("/gone.html"), output_dir=Path("unused"))

    with pytest.raises(NotFound):
        await scraper("video@src").download(ctx)


async def test_a_chain_on_a_page_that_wants_a_login_signs_in_with_cookies(
    fixture_site: FixtureSite,
) -> None:
    url = fixture_site.url(Page.PRIVATE)
    downloader = scraper("video source@src")
    cookie = Cookie.model_validate(
        {"name": COOKIE_NAME, "value": COOKIE_VALUE, "domain": "localhost"}
    )

    without = await downloader.probe(ProbeContext(url=url))
    signed_in = await downloader.probe(ProbeContext(url=url, cookies=[cookie]))

    assert without.error is not None
    assert without.error.code is ErrorCode.AUTH_REQUIRED
    assert [media.url for media in signed_in.media] == [fixture_site.url(Page.PRIVATE_FILE)]


async def test_a_chain_s_headers_go_with_every_request(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    # (A request without the headers is not answered.)
    page = httpserver.url_for("/watch")
    headers = {"X-Site": "yes", "Referer": page}
    httpserver.expect_request("/watch", headers=headers).respond_with_data(
        '<iframe src="/embed"></iframe>', content_type="text/html"
    )
    httpserver.expect_request("/embed", headers=headers).respond_with_data(
        '<video src="/clip.mp4"></video>', content_type="text/html"
    )
    httpserver.expect_request("/clip.mp4", headers=headers).respond_with_data(
        b"clip", content_type="video/mp4"
    )
    downloader = scraper(
        {"steps": "iframe@src >> video@src", "headers": {"X-Site": "yes", "Referer": "{url}"}}
    )

    result = await downloader.download(DownloadContext(url=page, output_dir=tmp_path))

    assert [path.read_bytes() for path in result.files] == [b"clip"]


async def test_a_chain_s_handoff_names_the_downloader(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    lookup = Lookup({fixture_site.url(Page.DIRECT_FILE): b"video"})
    downloader = scraper({"steps": "video source@src", "handoff": "yt-dlp"})

    await downloader.download(
        DownloadContext(
            url=fixture_site.url(Page.VIDEO), output_dir=tmp_path, downloader_for=lookup
        )
    )

    assert lookup.asked == ["yt-dlp"]


async def test_running_a_chain_traces_its_steps(fixture_site: FixtureSite) -> None:
    trace: list[StepTrace] = []

    run = await scraper("iframe#player@src >> video#clip source@src").run_chain(
        ProbeContext(url=fixture_site.url(Page.EMBED)), trace=trace
    )

    assert [find.media.url for find in run.finds] == [fixture_site.url(Page.DIRECT_FILE)]
    assert trace == [
        StepTrace(step="iframe#player@src", found=["player.html"], count=1),
        StepTrace(step="follow", found=[fixture_site.url(Page.PLAYER)], count=1),
        StepTrace(step="video#clip source@src", found=["media/sample.mp4"], count=1),
    ]


async def test_a_trace_keeps_the_steps_before_a_failure(fixture_site: FixtureSite) -> None:
    trace: list[StepTrace] = []

    with pytest.raises(ExtractionFailed):
        await scraper("iframe#player@src >> audio@src").run_chain(
            ProbeContext(url=fixture_site.url(Page.EMBED)), trace=trace
        )

    assert [step.step for step in trace] == ["iframe#player@src", "follow", "audio@src"]


async def test_running_a_chain_needs_one(fixture_site: FixtureSite) -> None:
    with pytest.raises(ConfigError, match="no tag chain"):
        await ScraperDownloader().run_chain(ProbeContext(url=fixture_site.url(Page.VIDEO)))


async def test_without_a_chain_the_scraper_looks_for_media_itself(
    fixture_site: FixtureSite,
) -> None:
    result = await ScraperDownloader().probe(ProbeContext(url=fixture_site.url(Page.JSON)))

    assert result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED_URL


def test_the_scraper_follows_only_static_chains() -> None:
    with pytest.raises(ValueError, match="browser chain runs in the browser"):
        ScraperOptions(chain=TagChain.model_validate("click:#play >> video@src"))
