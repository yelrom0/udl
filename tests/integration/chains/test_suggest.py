"""
-------------------
- test_suggest.py -
-------------------

Suggesting chains for the fixture site's pages, as the scraper reads them and as the browser
renders them
"""

# System Imports

# Package Imports
import pytest
from pytest_httpserver import HTTPServer

# Local Imports
from support.browser import needs_chromium
from support.fixture_site import FixtureSite, Page
from udl.chains.models import FetchMode
from udl.chains.suggest import ChainSuggestion
from udl.errors import UnsupportedURL
from udl.plugins.downloaders.base import ProbeContext
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
