"""
-------------------
- test_suggest.py -
-------------------

Suggesting chains for the fixture site's pages (as the scraper reads them, and as the browser
renders them), and asking the media's server what each suggestion leads to
"""

# System Imports
import socket

# Package Imports
import pytest
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

# Local Imports
from support.browser import needs_chromium
from support.fixture_site import FixtureSite, Page
from udl.chains.models import FetchMode
from udl.chains.suggest import ChainSuggestion, previewed
from udl.errors import UnsupportedURL
from udl.plugins.downloaders.base import MediaCandidate, ProbeContext
from udl.plugins.downloaders.browser import BrowserDownloader, BrowserOptions
from udl.plugins.downloaders.scraper import ScraperDownloader


def shorthands(suggestions: list[ChainSuggestion]) -> list[tuple[str, str]]:
    return [(s.shorthand, s.media.url) for s in suggestions]


# ---------------------------------------------------------------------------------------------
# The scraper
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "expected"),
    [
        (Page.VIDEO, [("video > source@src", Page.DIRECT_FILE)]),
        (Page.PLAYER, [("video#clip > source@src", Page.DIRECT_FILE)]),
        (Page.EMBED, [("iframe#player@src >> video#clip > source@src", Page.DIRECT_FILE)]),
        (Page.JSON, [("script#data::text >> json:video.src", Page.DIRECT_FILE)]),
        (
            Page.VIDEOS,
            [
                ("video > source@src", Page.DIRECT_FILE),
                ("video:nth-of-type(2) > source@src", Page.OTHER_FILE),
            ],
        ),
        (Page.NO_MEDIA, []),
        (Page.JS, []),
    ],
)
async def test_the_scraper_suggests_chains_for_the_page_as_served(
    fixture_site: FixtureSite, page: Page, expected: list[tuple[str, Page]]
) -> None:
    suggestions = await ScraperDownloader().suggest_chains(ProbeContext(url=fixture_site.url(page)))

    assert shorthands(suggestions) == [(chain, fixture_site.url(to)) for chain, to in expected]
    assert all(s.chain.fetch is FetchMode.STATIC for s in suggestions)


async def test_a_file_is_not_a_page_to_suggest_chains_for(fixture_site: FixtureSite) -> None:
    with pytest.raises(UnsupportedURL, match="not a web page"):
        await ScraperDownloader().suggest_chains(
            ProbeContext(url=fixture_site.url(Page.DIRECT_FILE))
        )


async def test_an_iframe_that_cannot_be_read_is_skipped(httpserver: HTTPServer) -> None:
    httpserver.expect_request("/watch").respond_with_data(
        '<iframe src="/gone"></iframe><video src="/v.mp4"></video>', content_type="text/html"
    )

    suggestions = await ScraperDownloader().suggest_chains(
        ProbeContext(url=httpserver.url_for("/watch"))
    )

    assert shorthands(suggestions) == [("video@src", httpserver.url_for("/v.mp4"))]


# ---------------------------------------------------------------------------------------------
# The browser
# ---------------------------------------------------------------------------------------------


async def test_the_browser_suggests_chains_for_the_page_as_rendered(
    fixture_site: FixtureSite,
) -> None:
    needs_chromium()
    browser = BrowserDownloader(BrowserOptions(profile_dir=None, wait=1))

    suggestions = await browser.suggest_chains(ProbeContext(url=fixture_site.url(Page.JS)))

    assert shorthands(suggestions)[0] == (
        "div#player video@src",
        fixture_site.url(Page.DIRECT_FILE),
    )
    assert all(s.chain.fetch is FetchMode.BROWSER for s in suggestions)
    assert {s.media.found_by for s in suggestions} <= {"<video>", "network"}


async def test_the_browser_suggests_chains_through_frames(fixture_site: FixtureSite) -> None:
    needs_chromium()
    browser = BrowserDownloader(BrowserOptions(profile_dir=None, wait=0.5))

    suggestions = await browser.suggest_chains(ProbeContext(url=fixture_site.url(Page.EMBED)))

    assert shorthands(suggestions)[0] == (
        "iframe#player@src >> video#clip > source@src",
        fixture_site.url(Page.DIRECT_FILE),
    )


# ---------------------------------------------------------------------------------------------
# Asking for the media
# ---------------------------------------------------------------------------------------------


def suggestion(url: str, found_on: str = "https://example.com/watch") -> ChainSuggestion:
    return ChainSuggestion.model_validate(
        {
            "chain": "video@src",
            "shorthand": "video@src",
            "media": MediaCandidate(url=url),
            "found_on": found_on,
        }
    )


async def test_asking_for_media_gives_its_type_and_size(fixture_site: FixtureSite) -> None:
    [asked] = await previewed([suggestion(fixture_site.url(Page.DIRECT_FILE))], cookies=[])

    assert asked.reachable is True
    assert asked.media.content_type == "video/mp4"
    assert asked.media.size_bytes == len(fixture_site.content(Page.DIRECT_FILE))


async def test_media_that_is_gone_or_a_page_did_not_answer_as_media(
    fixture_site: FixtureSite,
) -> None:
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        unreachable = f"http://127.0.0.1:{closed.getsockname()[1]}/v.mp4"

    asked = await previewed(
        [
            suggestion(fixture_site.url("/gone.mp4")),
            suggestion(fixture_site.url(Page.VIDEO)),
            suggestion(unreachable),
        ],
        cookies=[],
    )

    assert [s.reachable for s in asked] == [False, False, False]


async def test_a_server_that_answers_only_get_is_asked_for_a_byte(httpserver: HTTPServer) -> None:
    def answer(request: Request) -> Response:
        if request.method == "HEAD":
            return Response(status=405)
        assert request.headers["Range"] == "bytes=0-0"
        assert request.headers["Referer"] == "https://example.com/watch"
        headers = {"Content-Range": "bytes 0-0/12345"}
        return Response(b"x", status=206, mimetype="video/mp4", headers=headers)

    httpserver.expect_request("/v.mp4").respond_with_handler(answer)

    [asked] = await previewed([suggestion(httpserver.url_for("/v.mp4"))], cookies=[])

    assert (asked.reachable, asked.media.size_bytes) == (True, 12345)
