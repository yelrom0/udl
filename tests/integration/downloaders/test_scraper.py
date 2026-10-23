"""
------------------
- test_scraper.py -
------------------

The scraper downloader against the fixture site and pages scripted per test: where it looks for
media, iframes, what it hands off to which downloader, and trying the next find when one fails
"""

# System Imports
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

# Local Imports
from support.downloaders import Lookup
from support.fixture_site import FixtureSite, Page
from udl.errors import AuthRequired, ErrorCode, NetworkError, NotFound
from udl.plugins.downloaders.base import DownloadContext, MediaKind, ProbeContext, ProbeResult
from udl.plugins.downloaders.http import USER_AGENT
from udl.plugins.downloaders.scraper import ScraperDownloader, ScraperOptions

CDN = "https://cdn.example.com"


def page(body: str, head: str = "") -> str:
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"


async def probe_html(server: HTTPServer, html: str, **options: Any) -> ProbeResult:
    """Probe a page with this HTML (served at /page/index.html)."""
    server.expect_request("/page/index.html").respond_with_data(html, content_type="text/html")
    downloader = ScraperDownloader(ScraperOptions(**options))
    return await downloader.probe(ProbeContext(url=server.url_for("/page/index.html")))


def found(result: ProbeResult) -> list[tuple[str, MediaKind, str | None]]:
    return [(media.url, media.kind, media.found_by) for media in result.media]


# ---------------------------------------------------------------------------------------------
# The fixture site
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page_", "ok"),
    [
        (Page.VIDEO, True),
        (Page.PLAYER, True),
        (Page.EMBED, True),  # through its iframe
        (Page.VIDEOS, True),
        (Page.JS, False),  # the media only exists once its script runs
        (Page.NO_MEDIA, False),
        (Page.GALLERY, False),  # images are not media to the scraper
    ],
)
async def test_it_handles_pages_with_media_in_their_html(
    fixture_site: FixtureSite, page_: Page, ok: bool
) -> None:
    result = await ScraperDownloader().probe(ProbeContext(url=fixture_site.url(page_)))

    assert result.ok is ok
    if not ok:
        assert result.error is not None
        assert result.error.code is ErrorCode.UNSUPPORTED_URL


async def test_a_probe_lists_the_media_and_hands_off_the_first(fixture_site: FixtureSite) -> None:
    url = fixture_site.url(Page.VIDEOS)

    result = await ScraperDownloader().probe(ProbeContext(url=url))

    assert [media.url for media in result.media] == [
        fixture_site.url(Page.DIRECT_FILE),
        fixture_site.url(Page.OTHER_FILE),
    ]
    assert result.title == "A page with two videos"
    assert result.handoff is not None
    assert (result.handoff.tool, result.handoff.url) == ("http", fixture_site.url(Page.DIRECT_FILE))
    assert result.handoff.headers == {"Referer": url}


async def test_a_file_is_not_a_page(fixture_site: FixtureSite) -> None:
    url = fixture_site.url(Page.DIRECT_FILE)

    result = await ScraperDownloader().probe(ProbeContext(url=url))

    assert result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED_URL
    assert result.error.hint is not None
    assert "http" in result.error.hint


async def test_media_in_an_iframe_is_found_on_the_iframes_page(fixture_site: FixtureSite) -> None:
    result = await ScraperDownloader().probe(ProbeContext(url=fixture_site.url(Page.EMBED)))

    [media] = result.media
    assert media.url == fixture_site.url(Page.DIRECT_FILE)
    assert media.found_by == "<video> in <iframe>"
    assert result.handoff is not None
    assert result.handoff.headers == {"Referer": fixture_site.url(Page.PLAYER)}


async def test_iframes_can_be_left_alone(fixture_site: FixtureSite) -> None:
    downloader = ScraperDownloader(ScraperOptions(max_iframes=0))

    result = await downloader.probe(ProbeContext(url=fixture_site.url(Page.EMBED)))

    assert result.ok is False


async def test_it_downloads_the_media_on_a_page(fixture_site: FixtureSite, tmp_path: Path) -> None:
    ctx = DownloadContext(url=fixture_site.url(Page.EMBED), output_dir=tmp_path)

    result = await ScraperDownloader().download(ctx)

    assert result.files == [tmp_path / "sample.mp4"]
    assert result.files[0].read_bytes() == fixture_site.content(Page.DIRECT_FILE)
    assert (result.tool, result.handed_off_to) == ("scraper", "http")
    assert result.title == "A page with an embedded player"


# ---------------------------------------------------------------------------------------------
# Where it looks
# ---------------------------------------------------------------------------------------------

FILE, HLS, DASH = MediaKind.FILE, MediaKind.HLS, MediaKind.DASH


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            page('<audio src="/sounds/song.mp3"></audio>'),
            [("{root}/sounds/song.mp3", FILE, "<audio>")],
        ),
        (
            page('<video><source src="live/index.m3u8" type="application/x-mpegURL"></video>'),
            [("{root}/page/live/index.m3u8", HLS, "<video>")],
        ),
        (
            page('<video><source src="/stream?id=1" type="application/dash+xml"></video>'),
            [("{root}/stream?id=1", DASH, "<video>")],
        ),
        (
            page("", f'<meta property="og:video" content="{CDN}/a.mp4">'),
            [(f"{CDN}/a.mp4", FILE, "og:video")],
        ),
        (
            page("", f'<meta property="og:audio:secure_url" content="{CDN}/a.ogg">'),
            [(f"{CDN}/a.ogg", FILE, "og:audio")],
        ),
        (
            page(
                "",
                f'<meta property="og:video" content="{CDN}/embed/1">'
                '<meta property="og:video:type" content="text/html">'
                f'<meta property="og:video" content="{CDN}/1.mp4">'
                '<meta property="og:video:type" content="video/mp4">',
            ),
            [(f"{CDN}/1.mp4", FILE, "og:video")],  # the player page is not media
        ),
        (
            page(
                '<script type="application/ld+json">'
                f'{{"@type": "VideoObject", "contentUrl": "{CDN}/v.mp4"}}</script>'
            ),
            [(f"{CDN}/v.mp4", FILE, "json-ld")],
        ),
        (
            page(
                '<script type="application/ld+json">{"@graph": [{"@type": "WebPage"}, '
                f'{{"video": {{"contentUrl": "{CDN}/nested.mp4"}}}}]}}</script>'
                '<script type="application/ld+json">{not json</script>'
            ),
            [(f"{CDN}/nested.mp4", FILE, "json-ld")],
        ),
        (
            page('<a href="files/clip.webm">Download</a> <a href="other.html">More</a>'),
            [("{root}/page/files/clip.webm", FILE, "<a>")],
        ),
        (
            page(
                '<script>var player = {"hls": "https:\\/\\/cdn.example.com\\/live.m3u8", '
                f'"poster": "{CDN}/poster.jpg"}};</script>'
            ),
            [(f"{CDN}/live.m3u8", HLS, "<script>")],
        ),
        (
            page(f"<script>jwplayer().setup({{file: '{CDN}/v.mp4'}});</script>"),
            [(f"{CDN}/v.mp4", FILE, "<script>")],
        ),
        (
            page('<video src="clip.mp4"></video>', f'<base href="{CDN}/media/">'),
            [(f"{CDN}/media/clip.mp4", FILE, "<video>")],
        ),
        (
            page(
                f'<a href="{CDN}/b.mp4">b</a> <video src="{CDN}/a.mp4"></video>'
                f'<video src="{CDN}/b.mp4"></video>'
            ),
            # Ranked by where they were found, each found once.
            [(f"{CDN}/a.mp4", FILE, "<video>"), (f"{CDN}/b.mp4", FILE, "<video>")],
        ),
    ],
)
async def test_where_it_finds_media(
    httpserver: HTTPServer, html: str, expected: list[tuple[str, MediaKind, str]]
) -> None:
    root = httpserver.url_for("/").rstrip("/")

    result = await probe_html(httpserver, html)

    assert found(result) == [(url.format(root=root), kind, how) for url, kind, how in expected]


@pytest.mark.parametrize(
    "html",
    [
        page('<video src="blob:https://example.com/1"></video><video src="data:video/mp4,x">'),
        page('<video src="http://"></video>'),
        page(
            '<video src=""></video><script src="/player.js"></script>',
            '<meta property="og:video" content=""><meta property="og:video:width" content="640">',
        ),
        page('<a href="/photo.jpg">a photo</a>'),
        page(f"<script>var poster = '{CDN}/poster.jpg';</script>"),
    ],
)
async def test_what_is_not_media(httpserver: HTTPServer, html: str) -> None:
    result = await probe_html(httpserver, html)

    assert result.error is not None
    assert result.error.code is ErrorCode.UNSUPPORTED_URL


async def test_the_title_is_the_pages(httpserver: HTTPServer) -> None:
    html = page(
        f'<video src="{CDN}/a.mp4"></video>',
        '<title>Plain</title><meta property="og:title" content=" Open Graph ">',
    )

    result = await probe_html(httpserver, html)

    assert result.title == "Open Graph"


async def test_only_the_start_of_a_huge_page_is_read(httpserver: HTTPServer) -> None:
    html = page(" " * 1000 + f'<video src="{CDN}/a.mp4"></video>')

    result = await probe_html(httpserver, html, max_page_bytes=500)

    assert result.ok is False


async def test_an_iframe_that_fails_is_skipped(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/gone.html").respond_with_data("", status=404)
    httpserver.expect_request("/clip.mp4").respond_with_data(b"x", content_type="video/mp4")
    httpserver.expect_request("/player.html").respond_with_data(
        page('<video src="clip.mp4"></video>'), content_type="text/html"
    )

    result = await probe_html(
        httpserver, page('<iframe src="/gone.html"></iframe><iframe src="/player.html"></iframe>')
    )

    assert [media.url for media in result.media] == [httpserver.url_for("/clip.mp4")]


# ---------------------------------------------------------------------------------------------
# Downloading: handoffs
# ---------------------------------------------------------------------------------------------


async def test_a_stream_playlist_is_handed_to_yt_dlp(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    httpserver.expect_request("/page.html").respond_with_data(
        page(f'<video src="{CDN}/live.m3u8"></video>'), content_type="text/html"
    )
    lookup = Lookup({f"{CDN}/live.m3u8": b"stream"})
    ctx = DownloadContext(
        url=httpserver.url_for("/page.html"), output_dir=tmp_path, downloader_for=lookup
    )

    result = await ScraperDownloader().download(ctx)

    assert lookup.asked == ["yt-dlp"]
    assert result.handed_off_to == "fake"


@pytest.fixture
def two_videos(httpserver: HTTPServer) -> str:
    httpserver.expect_request("/page.html").respond_with_data(
        page('<video src="/first.mp4"></video><video src="/second.mp4"></video>'),
        content_type="text/html",
    )
    httpserver.expect_request("/second.mp4").respond_with_data(b"second", content_type="video/mp4")
    return httpserver.url_for("/page.html")


@pytest.mark.parametrize(("status", "content_type"), [(404, "text/plain"), (200, "text/html")])
async def test_when_a_find_cannot_be_downloaded_the_next_one_is(
    httpserver: HTTPServer, two_videos: str, tmp_path: Path, status: int, content_type: str
) -> None:
    httpserver.expect_request("/first.mp4").respond_with_data(
        "<p>not here</p>" if status == 200 else "", status=status, content_type=content_type
    )

    result = await ScraperDownloader().download(
        DownloadContext(url=two_videos, output_dir=tmp_path)
    )

    assert [file.read_bytes() for file in result.files] == [b"second"]


async def test_when_no_find_can_be_downloaded_the_first_failure_is_raised(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    httpserver.expect_request("/page.html").respond_with_data(
        page('<video src="/first.mp4"></video><video src="/second.mp4"></video>'),
        content_type="text/html",
    )
    httpserver.expect_request("/first.mp4").respond_with_data("", status=404)
    httpserver.expect_request("/second.mp4").respond_with_data(
        "<p>a page</p>", content_type="text/html"
    )
    ctx = DownloadContext(url=httpserver.url_for("/page.html"), output_dir=tmp_path)

    with pytest.raises(NotFound) as caught:
        await ScraperDownloader().download(ctx)

    assert caught.value.tool == "http"


async def test_media_that_needs_a_login_is_not_swapped_for_other_media(
    httpserver: HTTPServer, two_videos: str, tmp_path: Path
) -> None:
    # e.g. the film needs a login and the second video is its trailer
    httpserver.expect_request("/first.mp4").respond_with_data("", status=401)

    with pytest.raises(AuthRequired):
        await ScraperDownloader().download(DownloadContext(url=two_videos, output_dir=tmp_path))


async def test_the_media_is_requested_with_the_page_as_referer_and_its_cookies(
    httpserver: HTTPServer, tmp_path: Path
) -> None:
    def page_setting_a_cookie(_: Request) -> Response:
        response = Response(page('<video src="/clip.mp4"></video>'), mimetype="text/html")
        response.set_cookie("token", "abc")
        return response

    page_url = httpserver.url_for("/page.html")
    httpserver.expect_request("/page.html").respond_with_handler(page_setting_a_cookie)
    httpserver.expect_request(
        "/clip.mp4", headers={"Referer": page_url, "Cookie": "token=abc"}
    ).respond_with_data(b"clip", content_type="video/mp4")

    result = await ScraperDownloader().download(DownloadContext(url=page_url, output_dir=tmp_path))

    assert [file.read_bytes() for file in result.files] == [b"clip"]


@pytest.mark.parametrize(
    ("options", "agent"), [({}, USER_AGENT), ({"user_agent": "Configured/2"}, "Configured/2")]
)
async def test_pages_are_read_with_a_browser_s_user_agent_or_the_one_set(
    httpserver: HTTPServer, options: dict[str, Any], agent: str
) -> None:
    # Many sites refuse other User-Agents (e.g. a library's).
    await probe_html(httpserver, page('<iframe src="/frame.html"></iframe>'), **options)

    assert {tuple(r.headers.getlist("User-Agent")) for r, _ in httpserver.log} == {(agent,)}


async def test_an_unreachable_page_is_a_network_error(tmp_path: Path) -> None:
    scraper = ScraperDownloader(ScraperOptions(retries=0))
    url = "http://127.0.0.1:1/page.html"  # nothing listens on port 1

    probed = await scraper.probe(ProbeContext(url=url))

    assert probed.error is not None
    assert probed.error.code is ErrorCode.NETWORK_ERROR
    with pytest.raises(NetworkError):
        await scraper.download(DownloadContext(url=url, output_dir=tmp_path))
