"""
------------------------
- test_fixture_site.py -
------------------------

The local fixture site that the downloader integration tests run against
"""

# System Imports
import json
import time
from urllib.parse import urljoin

# Package Imports
import httpx
import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from selectolax.lexbor import LexborHTMLParser

# Local Imports
from support.fixture_site import COOKIE_NAME, COOKIE_VALUE, SLOW_SIZE, FixtureSite, Page


def src_of(html: str, selector: str) -> str:
    """The `src` of the first element matching `selector`."""
    node = LexborHTMLParser(html).css_first(selector)
    assert node is not None, f"no {selector!r} in page"
    src = node.attributes.get("src")
    assert src, f"{selector!r} has no src"
    return src


def test_the_direct_file_is_a_real_mp4(fixture_site: FixtureSite) -> None:
    response = httpx.get(fixture_site.url(Page.DIRECT_FILE))

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == fixture_site.content(Page.DIRECT_FILE)
    assert response.content[4:8] == b"ftyp"


def test_the_direct_file_supports_range_requests(fixture_site: FixtureSite) -> None:
    full = fixture_site.content(Page.DIRECT_FILE)

    response = httpx.get(fixture_site.url(Page.DIRECT_FILE), headers={"Range": "bytes=100-"})

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 100-{len(full) - 1}/{len(full)}"
    assert response.content == full[100:]


def test_head_reports_type_and_size_without_a_body(fixture_site: FixtureSite) -> None:
    response = httpx.head(fixture_site.url(Page.DIRECT_FILE))

    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"
    assert int(response.headers["content-length"]) == len(fixture_site.content(Page.DIRECT_FILE))
    assert response.content == b""


def test_the_video_page_points_at_the_direct_file(fixture_site: FixtureSite) -> None:
    html = httpx.get(fixture_site.url(Page.VIDEO)).text

    assert src_of(html, "video > source[type='video/mp4']") == Page.DIRECT_FILE


def test_the_videos_page_has_two_videos(fixture_site: FixtureSite) -> None:
    html = httpx.get(fixture_site.url(Page.VIDEOS)).text

    sources = [node.attributes.get("src") for node in LexborHTMLParser(html).css("video > source")]

    assert sources == [Page.DIRECT_FILE, Page.OTHER_FILE]
    assert httpx.get(fixture_site.url(Page.OTHER_FILE)).content == fixture_site.content(
        Page.DIRECT_FILE
    )


def test_the_gallery_has_an_image_and_a_missing_one(fixture_site: FixtureSite) -> None:
    html = httpx.get(fixture_site.url(Page.GALLERY)).text

    sources = [node.attributes.get("src") for node in LexborHTMLParser(html).css("img")]

    assert sources == [Page.IMAGE, "/media/missing.png"]
    image = httpx.get(fixture_site.url(Page.IMAGE))
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert httpx.get(fixture_site.url("/media/missing.png")).status_code == 404


def test_the_embed_page_leads_through_its_iframe_to_the_file(fixture_site: FixtureSite) -> None:
    embed_url = fixture_site.url(Page.EMBED)

    # Both hops use relative URLs, resolved against the document they appear in.
    player_url = urljoin(embed_url, src_of(httpx.get(embed_url).text, "iframe#player"))
    file_url = urljoin(player_url, src_of(httpx.get(player_url).text, "video > source"))

    assert player_url == fixture_site.url(Page.PLAYER)
    assert file_url == fixture_site.url(Page.DIRECT_FILE)


def test_the_page_without_media_has_none(fixture_site: FixtureSite) -> None:
    response = httpx.get(fixture_site.url(Page.NO_MEDIA))

    assert response.headers["content-type"].startswith("text/html")
    assert LexborHTMLParser(response.text).css("video, audio, source, iframe, a, script") == []
    assert ".mp4" not in response.text


def test_the_slow_file_trickles_out(fixture_site: FixtureSite) -> None:
    start = time.monotonic()
    with httpx.stream("GET", fixture_site.url(Page.SLOW_FILE)) as response:
        first = next(response.iter_raw())
        elapsed = time.monotonic() - start

    assert response.headers["content-type"] == "video/mp4"
    assert int(response.headers["content-length"]) == SLOW_SIZE
    assert first.startswith(fixture_site.content(Page.DIRECT_FILE)[:16])  # an MP4's header
    assert len(first) < SLOW_SIZE / 50  # the rest takes seconds
    assert elapsed < 1


def test_head_on_the_slow_file_has_its_size(fixture_site: FixtureSite) -> None:
    response = httpx.head(fixture_site.url(Page.SLOW_FILE))

    assert response.headers["content-type"] == "video/mp4"
    assert int(response.headers["content-length"]) == SLOW_SIZE


def test_the_slow_page_points_at_the_slow_file(fixture_site: FixtureSite) -> None:
    html = httpx.get(fixture_site.url(Page.SLOW)).text

    assert src_of(html, "video > source") == Page.SLOW_FILE


def test_the_json_page_has_its_video_only_in_json(fixture_site: FixtureSite) -> None:
    html = httpx.get(fixture_site.url(Page.JSON)).text
    data = LexborHTMLParser(html).css_first('script#data[type="application/json"]')

    assert data is not None
    assert json.loads(data.text())["video"]["src"] == Page.DIRECT_FILE
    assert "<video" not in html
    assert "http" not in html.lower()


def test_the_js_page_has_no_media_in_its_static_html(fixture_site: FixtureSite) -> None:
    html = httpx.get(fixture_site.url(Page.JS)).text

    assert LexborHTMLParser(html).css("video, audio, source, iframe") == []
    assert ".mp4" not in html


def test_the_js_page_builds_its_video_in_a_browser(fixture_site: FixtureSite) -> None:
    url = fixture_site.url(Page.JS)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as e:
            if "Executable doesn't exist" in e.message:
                pytest.skip("Chromium is not installed: uv run playwright install chromium")
            raise
        try:
            page = browser.new_page()
            page.goto(url)
            src = page.locator("video").get_attribute("src")
        finally:
            browser.close()

    assert src is not None
    assert urljoin(url, src) == fixture_site.url(Page.DIRECT_FILE)


@pytest.mark.parametrize("page", [Page.PRIVATE, Page.PRIVATE_FILE])
def test_private_pages_are_401_without_the_session_cookie(
    fixture_site: FixtureSite, page: Page
) -> None:
    assert httpx.get(fixture_site.url(page)).status_code == 401


def test_the_session_cookie_unlocks_the_private_video(fixture_site: FixtureSite) -> None:
    page_url = fixture_site.url(Page.PRIVATE)

    with httpx.Client(cookies={COOKIE_NAME: COOKIE_VALUE}) as client:
        page = client.get(page_url)
        file = client.get(urljoin(page_url, src_of(page.text, "video > source")))

    assert page.status_code == 200
    assert file.status_code == 200
    assert file.content == fixture_site.content(Page.PRIVATE_FILE)


def test_signing_in_sets_the_session_cookie(fixture_site: FixtureSite) -> None:
    with httpx.Client() as client:
        response = client.get(fixture_site.url(Page.LOGIN))

        assert client.cookies.get(COOKIE_NAME) == COOKIE_VALUE
        assert client.get(fixture_site.url(Page.PRIVATE)).status_code == 200
    # It outlives the browser session (a "remember me" login), so a browser profile keeps it.
    assert "max-age=" in response.headers["set-cookie"].lower()


def test_unknown_paths_are_not_found(fixture_site: FixtureSite) -> None:
    assert httpx.get(fixture_site.url("/gone.mp4")).status_code == 404
