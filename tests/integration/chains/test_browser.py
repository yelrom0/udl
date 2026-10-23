"""
------------------
- test_browser.py -
------------------

The browser (headless Chromium) following tag chains on the fixture site and pages scripted per
test: a page rendered by JavaScript, clicking Play and waiting for the video, capturing the
requests a page makes, following an iframe and a JSON API in pages of their own, and what goes
wrong
"""

# System Imports
import socket
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pytest_httpserver import HTTPServer

# Local Imports
from support.browser import needs_chromium
from support.fixture_site import FixtureSite, Page
from udl.chains.models import StepTrace, TagChain
from udl.errors import ConfigError, ErrorCode
from udl.plugins.downloaders.base import DownloadContext, ProbeContext, ProbeResult
from udl.plugins.downloaders.browser import BrowserDownloader, BrowserOptions


def browser(chain: str | dict[str, Any] | None, **options: Any) -> BrowserDownloader:
    """Chromium with a new profile each time, following `chain` (in the browser)."""
    needs_chromium()
    if isinstance(chain, str):
        chain = {"steps": chain}
    found = TagChain.model_validate({**chain, "fetch": "browser"}) if chain else None
    return BrowserDownloader(BrowserOptions(profile_dir=None, chain=found, **options))


async def probe(chain: str | dict[str, Any], url: str, **options: Any) -> ProbeResult:
    return await browser(chain, **options).probe(ProbeContext(url=url))


async def test_a_chain_reads_the_page_as_its_scripts_left_it(fixture_site: FixtureSite) -> None:
    result = await probe("div#player video@src", fixture_site.url(Page.JS))

    assert result.ok, result.error
    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]
    assert result.title == "A page rendered by JavaScript"


async def test_a_chain_clicks_play_and_waits_for_the_video(fixture_site: FixtureSite) -> None:
    result = await probe("click:#play >> wait_for:video >> video@src", fixture_site.url(Page.CLICK))

    assert result.ok, result.error
    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]


async def test_a_chain_captures_what_the_page_requests(fixture_site: FixtureSite) -> None:
    url = fixture_site.url(Page.CLICK)

    result = await probe(r"click:#play >> network:/media/sample\.mp4", url)

    assert result.ok, result.error
    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]
    assert result.handoff is not None
    assert result.handoff.headers["Referer"] == url
    assert "User-Agent" in result.handoff.headers


async def test_a_request_that_never_comes_finds_nothing(fixture_site: FixtureSite) -> None:
    result = await probe(r"network:\.mp4", fixture_site.url(Page.CLICK), timeout=1)

    assert result.error is not None
    assert result.error.code is ErrorCode.EXTRACTION_FAILED
    assert r"step 1 (network:\.mp4) found nothing" in result.error.message


@pytest.mark.parametrize(
    ("chain", "problem"),
    [
        ("wait_for:video >> video@src", "No element matching 'video' came"),
        ("click:#pause >> video@src", "Could not click '#pause'"),
    ],
)
async def test_an_element_that_never_comes_is_an_extraction_failure(
    fixture_site: FixtureSite, chain: str, problem: str
) -> None:
    result = await probe(chain, fixture_site.url(Page.CLICK), timeout=1)

    assert result.error is not None
    assert (result.error.code, result.error.tool) == (ErrorCode.EXTRACTION_FAILED, "browser")
    assert problem in result.error.message


async def test_a_chain_follows_an_iframe_in_a_page_of_its_own(fixture_site: FixtureSite) -> None:
    result = await probe("iframe#player@src >> video#clip source@src", fixture_site.url(Page.EMBED))

    assert result.ok, result.error
    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]
    assert result.handoff is not None
    assert result.handoff.headers["Referer"] == fixture_site.url(Page.PLAYER)


async def test_a_followed_api_s_json_is_read_as_it_was_served(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/watch").respond_with_data(
        '<a class="api" href="/api/video">video</a>', content_type="text/html"
    )
    httpserver.expect_request("/api/video").respond_with_json({"url": "/media/clip.mp4"})

    result = await probe("a.api@href >> follow >> json:url", httpserver.url_for("/watch"))

    assert result.ok, result.error
    assert [media.url for media in result.media] == [httpserver.url_for("/media/clip.mp4")]


async def test_a_chain_s_headers_go_with_the_page_s_requests(httpserver: HTTPServer) -> None:
    # (A request without the header is not answered.)
    httpserver.expect_request("/watch", headers={"X-Site": "yes"}).respond_with_data(
        '<video src="/clip.mp4"></video>', content_type="text/html"
    )

    result = await probe(
        {"steps": "video@src", "headers": {"X-Site": "yes"}}, httpserver.url_for("/watch")
    )

    assert result.ok, result.error
    assert result.handoff is not None
    assert result.handoff.headers["X-Site"] == "yes"


async def test_a_page_that_cannot_be_reached_is_a_network_error() -> None:
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        port = closed.getsockname()[1]

    result = await probe("video@src", f"http://127.0.0.1:{port}/watch")

    assert result.error is not None
    assert result.error.code is ErrorCode.NETWORK_ERROR


async def test_a_page_that_wants_a_login_says_so(fixture_site: FixtureSite) -> None:
    result = await probe("video source@src", fixture_site.url(Page.PRIVATE))

    assert result.error is not None
    assert result.error.code is ErrorCode.AUTH_REQUIRED


async def test_a_browser_chain_downloads_the_video(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    downloader = browser("click:#play >> wait_for:video >> video@src")
    ctx = DownloadContext(url=fixture_site.url(Page.CLICK), output_dir=tmp_path)

    result = await downloader.download(ctx)

    assert [path.read_bytes() for path in result.files] == [fixture_site.content(Page.DIRECT_FILE)]
    assert (result.tool, result.handed_off_to) == ("browser", "http")
    assert result.title == "A page whose video plays on a click"


async def test_running_a_browser_chain_traces_its_steps(fixture_site: FixtureSite) -> None:
    trace: list[StepTrace] = []
    url = fixture_site.url(Page.CLICK)

    await browser("click:#play >> wait_for:video >> video@src").run_chain(
        ProbeContext(url=url), trace=trace
    )

    assert trace == [
        StepTrace(step="click:#play", found=[url], count=1),
        StepTrace(step="wait_for:video", found=[url], count=1),
        StepTrace(step="video@src", found=["/media/sample.mp4"], count=1),
    ]


async def test_running_a_chain_needs_one(fixture_site: FixtureSite) -> None:
    with pytest.raises(ConfigError, match="no tag chain"):
        await browser(None).run_chain(ProbeContext(url=fixture_site.url(Page.VIDEO)))
