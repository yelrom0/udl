"""
------------------
- test_browser.py -
------------------

The browser downloader (headless Chromium) against the fixture site and pages scripted per test:
media that scripts add, what the page requests, handoffs, timeouts and cancelling, and the
persistent profile that keeps logins
"""

# System Imports
import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

# Local Imports
from support.browser import needs_chromium
from support.downloaders import Lookup
from support.fixture_site import COOKIE_NAME, FixtureSite, Page
from udl.config import data_dir
from udl.errors import Cancelled, ErrorCode
from udl.plugins.downloaders.base import CancelToken, DownloadContext, ProbeContext
from udl.plugins.downloaders.browser import (
    BrowserDownloader,
    BrowserOptions,
    chromium,
    profile_cookies,
)


@pytest.fixture
def options() -> BrowserOptions:
    """Chromium with a new profile each time, waiting briefly for media."""
    needs_chromium()
    return BrowserOptions(profile_dir=None, wait=0.5)


@pytest.fixture
def browser(options: BrowserOptions) -> BrowserDownloader:
    return BrowserDownloader(options)


@pytest.fixture
def slow_server() -> Iterator[HTTPServer]:
    """A server whose /slow.html takes 10 seconds (on a thread of its own, so other requests
    are not held up)."""
    server = HTTPServer(threaded=True)

    def slow(_: Request) -> Response:
        time.sleep(10)
        return Response("<p>at last</p>", mimetype="text/html")

    server.expect_request("/slow.html").respond_with_handler(slow)
    server.start()
    yield server
    server.clear()
    server.stop()


def page(body: str) -> str:
    return f"<!doctype html><html><head><title>Scripted</title></head><body>{body}</body></html>"


# ---------------------------------------------------------------------------------------------
# What it finds
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page_", "ok"),
    [
        (Page.JS, True),
        (Page.VIDEO, True),
        (Page.EMBED, True),
        (Page.DIRECT_FILE, True),  # Chromium plays it
        (Page.NO_MEDIA, False),
        (Page.GALLERY, False),  # images are not media
    ],
)
async def test_it_handles_pages_whose_media_a_browser_loads(
    browser: BrowserDownloader, fixture_site: FixtureSite, page_: Page, ok: bool
) -> None:
    result = await browser.probe(ProbeContext(url=fixture_site.url(page_)))

    assert result.ok is ok
    if not ok:
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSUPPORTED_URL


async def test_a_probe_finds_the_media_a_script_added(
    browser: BrowserDownloader, fixture_site: FixtureSite
) -> None:
    url = fixture_site.url(Page.JS)

    result = await browser.probe(ProbeContext(url=url))

    assert [media.url for media in result.media] == [fixture_site.url(Page.DIRECT_FILE)]
    assert result.title == "A page rendered by JavaScript"
    assert result.handoff is not None
    assert (result.handoff.tool, result.handoff.url) == ("http", fixture_site.url(Page.DIRECT_FILE))
    assert result.handoff.headers["Referer"] == url
    assert "Chrome" in result.handoff.headers["User-Agent"]


async def test_it_downloads_the_media_on_a_page(
    browser: BrowserDownloader, fixture_site: FixtureSite, tmp_path: Path
) -> None:
    ctx = DownloadContext(url=fixture_site.url(Page.JS), output_dir=tmp_path)

    result = await browser.download(ctx)

    assert result.files == [tmp_path / "sample.mp4"]
    assert result.files[0].read_bytes() == fixture_site.content(Page.DIRECT_FILE)
    assert (result.tool, result.handed_off_to) == ("browser", "http")
    assert result.title == "A page rendered by JavaScript"


async def test_media_in_an_iframe_is_handed_off_from_the_iframes_page(
    browser: BrowserDownloader, fixture_site: FixtureSite
) -> None:
    result = await browser.probe(ProbeContext(url=fixture_site.url(Page.EMBED)))

    assert result.handoff is not None
    assert result.handoff.url == fixture_site.url(Page.DIRECT_FILE)
    assert result.handoff.headers["Referer"] == fixture_site.url(Page.PLAYER)


async def test_a_stream_playlist_is_handed_to_yt_dlp(
    browser: BrowserDownloader, httpserver: HTTPServer, tmp_path: Path
) -> None:
    httpserver.expect_request("/page.html").respond_with_data(
        page('<video src="/live/index.m3u8"></video>'), content_type="text/html"
    )
    httpserver.expect_request("/live/index.m3u8").respond_with_data(
        "#EXTM3U\n", content_type="application/vnd.apple.mpegurl"
    )
    lookup = Lookup({httpserver.url_for("/live/index.m3u8"): b"stream"})
    ctx = DownloadContext(
        url=httpserver.url_for("/page.html"), output_dir=tmp_path, downloader_for=lookup
    )

    result = await browser.download(ctx)

    assert lookup.asked == ["yt-dlp"]
    assert result.handed_off_to == "fake"


async def test_stream_segments_are_not_media(
    browser: BrowserDownloader, httpserver: HTTPServer
) -> None:
    httpserver.expect_request("/page.html").respond_with_data(
        page("<script>fetch('/seg1.ts'); fetch('/seg2.m4s');</script>"), content_type="text/html"
    )
    httpserver.expect_request("/seg1.ts").respond_with_data(b"ts", content_type="video/mp2t")
    httpserver.expect_request("/seg2.m4s").respond_with_data(b"m4s", content_type="video/mp4")

    result = await browser.probe(ProbeContext(url=httpserver.url_for("/page.html")))

    assert result.ok is False


async def test_a_file_download_is_left_to_http(
    browser: BrowserDownloader, httpserver: HTTPServer
) -> None:
    httpserver.expect_request("/report.zip").respond_with_data(
        b"PK", content_type="application/zip", headers={"Content-Disposition": "attachment"}
    )

    result = await browser.probe(ProbeContext(url=httpserver.url_for("/report.zip")))

    assert result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED_URL
    assert result.error.hint is not None
    assert "http" in result.error.hint


async def test_headers_are_sent(browser: BrowserDownloader, httpserver: HTTPServer) -> None:
    httpserver.expect_request("/page.html", headers={"X-Udl-Test": "yes"}).respond_with_data(
        page('<video src="/clip.mp4"></video>'), content_type="text/html"
    )
    httpserver.expect_request("/clip.mp4").respond_with_data(b"clip", content_type="video/mp4")
    ctx = ProbeContext(url=httpserver.url_for("/page.html"), headers={"X-Udl-Test": "yes"})

    result = await browser.probe(ctx)

    assert result.ok is True


async def test_the_user_agent_can_be_chosen(httpserver: HTTPServer) -> None:
    needs_chromium()
    httpserver.expect_request("/page.html", headers={"User-Agent": "udl-test"}).respond_with_data(
        page('<video src="/clip.mp4"></video>'), content_type="text/html"
    )
    httpserver.expect_request("/clip.mp4").respond_with_data(b"clip", content_type="video/mp4")
    browser = BrowserDownloader(BrowserOptions(profile_dir=None, wait=0.5, user_agent="udl-test"))

    result = await browser.probe(ProbeContext(url=httpserver.url_for("/page.html")))

    assert result.handoff is not None
    assert result.handoff.headers["User-Agent"] == "udl-test"


# ---------------------------------------------------------------------------------------------
# Timeouts and cancelling
# ---------------------------------------------------------------------------------------------


async def test_a_page_that_does_not_load_in_time_is_a_network_error(
    slow_server: HTTPServer,
) -> None:
    needs_chromium()
    browser = BrowserDownloader(BrowserOptions(profile_dir=None, timeout=1))

    result = await browser.probe(ProbeContext(url=slow_server.url_for("/slow.html")))

    assert result.error is not None
    assert result.error.code is ErrorCode.NETWORK_ERROR


async def test_cancelling_stops_a_page_that_is_loading(
    browser: BrowserDownloader, slow_server: HTTPServer
) -> None:
    token = CancelToken()
    task = asyncio.create_task(
        browser.probe(ProbeContext(url=slow_server.url_for("/slow.html"), cancel=token))
    )
    await asyncio.sleep(1)  # Chromium is up and waiting for the page

    token.cancel()
    start = time.monotonic()

    with pytest.raises(Cancelled):
        await task
    assert time.monotonic() - start < 3


# ---------------------------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------------------------


async def test_a_login_is_kept_in_the_profile(fixture_site: FixtureSite, tmp_path: Path) -> None:
    needs_chromium()
    browser = BrowserDownloader(BrowserOptions(profile_dir=tmp_path / "profile", wait=0.5))
    await browser.probe(ProbeContext(url=fixture_site.url(Page.LOGIN)))  # sets the cookie

    result = await browser.probe(ProbeContext(url=fixture_site.url(Page.PRIVATE)))

    assert result.error is None
    assert result.handoff is not None
    assert COOKIE_NAME in {cookie.name for cookie in result.handoff.cookies}


async def test_a_session_cookie_is_kept_in_the_profile_when_the_browser_closes(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    # (Chromium forgets session cookies when it closes, unless it is to continue where it left
    # off: udl's profile does, so a login with a session cookie lasts.)
    needs_chromium()
    profile = tmp_path / "profile"
    httpserver.expect_request("/login").respond_with_data(
        "<p>in</p>", content_type="text/html", headers={"Set-Cookie": "sid=1; Path=/"}
    )

    async with chromium(profile_dir=profile, headless=True, cancel=CancelToken()) as context:
        page = await context.new_page()
        await page.goto(httpserver.url_for("/login"))

    [kept] = await profile_cookies(profile)
    assert kept.name == "sid"
    assert kept.expires is not None
    assert kept.expires - datetime.now(UTC) > timedelta(days=29)


async def test_without_a_profile_nothing_is_kept(
    browser: BrowserDownloader, fixture_site: FixtureSite
) -> None:
    await browser.probe(ProbeContext(url=fixture_site.url(Page.LOGIN)))

    result = await browser.probe(ProbeContext(url=fixture_site.url(Page.PRIVATE)))

    assert result.error is not None
    assert result.error.code is ErrorCode.AUTH_REQUIRED


async def test_probes_sharing_a_profile_take_turns(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    needs_chromium()
    browser = BrowserDownloader(BrowserOptions(profile_dir=tmp_path / "profile", wait=0.5))
    urls = [fixture_site.url(Page.JS), fixture_site.url(Page.VIDEO)]

    results = await asyncio.gather(*(browser.probe(ProbeContext(url=url)) for url in urls))

    assert [result.error for result in results] == [None, None]


def test_by_default_the_profile_is_udls_own() -> None:
    assert BrowserOptions().profile_dir == data_dir() / "browser-profile"


async def test_a_profile_chromium_cannot_use_is_a_download_error(
    fixture_site: FixtureSite, tmp_path: Path
) -> None:
    needs_chromium()
    profile = tmp_path / "profile"
    profile.write_text("a file, not a folder")
    browser = BrowserDownloader(BrowserOptions(profile_dir=profile))

    result = await browser.probe(ProbeContext(url=fixture_site.url(Page.JS)))

    assert result.error is not None
    assert result.error.code is ErrorCode.DOWNLOAD_ERROR
    assert "Chromium" in result.error.message


async def test_without_chromium_it_is_unavailable(
    fixture_site: FixtureSite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))  # no browsers there
    browser = BrowserDownloader(BrowserOptions(profile_dir=None))

    result = await browser.probe(ProbeContext(url=fixture_site.url(Page.JS)))

    assert result.error is not None
    assert result.error.code is ErrorCode.TOOL_UNAVAILABLE
    assert result.error.hint is not None
    assert "playwright install chromium" in result.error.hint
