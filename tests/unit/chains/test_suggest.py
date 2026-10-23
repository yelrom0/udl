"""
-------------------
- test_suggest.py -
-------------------

Suggesting tag chains from a page (and its iframes' pages, and what it requested): the media
each kind of find leads to, stable selectors, checking each chain finds its media, and ranking
"""

# System Imports
from collections.abc import Mapping, Sequence

# Package Imports
import pytest
from selectolax.lexbor import LexborHTMLParser

# Local Imports
from udl.chains.executor import StaticDocument
from udl.chains.models import FetchMode
from udl.chains.suggest import (
    ChainSuggestion,
    FetchedPages,
    ranked,
    selector_for,
    suggest,
    with_preview,
)
from udl.errors import NotFound, UnsupportedURL
from udl.plugins.downloaders.base import MediaCandidate, MediaKind, ProbeResult

SITE = "https://example.com"
PAGE = f"{SITE}/watch/1"


def html(body: str, head: str = "") -> str:
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"


async def suggested(
    pages: Mapping[str, str],
    *,
    requested: Sequence[str] = (),
    fetch: FetchMode = FetchMode.STATIC,
) -> list[ChainSuggestion]:
    documents = {url: StaticDocument(url, text) for url, text in pages.items()}
    return await suggest(PAGE, documents, fetch=fetch, requested=requested)


def found(suggestions: list[ChainSuggestion]) -> list[tuple[str, str, str | None]]:
    return [(s.shorthand, s.media.url, s.media.found_by) for s in suggestions]


# ---------------------------------------------------------------------------------------------
# What each find leads to
# ---------------------------------------------------------------------------------------------


async def test_a_video_s_source() -> None:
    page = html('<video id="clip"><source src="/v.mp4" type="video/mp4"></video>')

    [suggestion] = await suggested({PAGE: page})

    assert found([suggestion]) == [("video#clip > source@src", f"{SITE}/v.mp4", "<video>")]
    assert suggestion.chain.fetch is FetchMode.STATIC
    assert (suggestion.found_on, suggestion.reachable) == (PAGE, None)
    assert suggestion.media.kind is MediaKind.FILE


async def test_media_elements_own_sources() -> None:
    page = html('<audio class="track" src="a.mp3"></audio><video src="//cdn.example.com/v.m3u8">')

    assert found(await suggested({PAGE: page})) == [
        ("audio.track@src", f"{SITE}/watch/a.mp3", "<audio>"),
        ("video@src", "https://cdn.example.com/v.m3u8", "<video>"),
    ]


async def test_open_graph_media_but_not_a_player_page() -> None:
    page = html(
        "",
        head='<meta charset="utf-8">'
        '<meta property="og:video" content="https://cdn.example.com/v.mp4">'
        '<meta property="og:video:url" content="https://example.com/embed/1">'
        '<meta property="og:video:type" content="text/html">'
        '<meta property="og:audio:secure_url" content="https://cdn.example.com/a.mp3">',
    )

    assert found(await suggested({PAGE: page})) == [
        ('meta[property="og:video"]@content', "https://cdn.example.com/v.mp4", "og:video"),
        (
            'meta[property="og:audio:secure_url"]@content',
            "https://cdn.example.com/a.mp3",
            "og:audio",
        ),
    ]


async def test_media_urls_in_json() -> None:
    data = '{"video": {"title": "A", "sources": [{"src": "/hd.mp4"}, {"src": "/sd.mp4"}]}}'
    ld = '{"@type": "VideoObject", "contentUrl": "https://cdn.example.com/v.mp4"}'
    page = html(
        f'<script id="data" type="application/json">{data}</script>'
        f'<script type="application/ld+json">{ld}</script>'
    )

    assert found(await suggested({PAGE: page})) == [
        ("script#data::text >> json:video.sources.0.src", f"{SITE}/hd.mp4", "json"),
        ("script#data::text >> json:video.sources.1.src", f"{SITE}/sd.mp4", "json"),
        (
            'script[type="application/ld+json"]::text >> json:contentUrl',
            "https://cdn.example.com/v.mp4",
            "json-ld",
        ),
    ]


async def test_media_links() -> None:
    page = html('<a href="/about">About</a><a class="download" href="/files/v.mp4">Get it</a>')

    assert found(await suggested({PAGE: page})) == [
        ("a.download@href", f"{SITE}/files/v.mp4", "<a>")
    ]


async def test_media_urls_in_scripts_are_found_by_their_kind() -> None:
    script = (
        "<script>var hls = 'https:\\/\\/cdn.example.com\\/live\\/master.m3u8?token=1';</script>"
    )

    [suggestion] = await suggested({PAGE: html(script)})

    assert suggestion.shorthand.startswith("regex:")
    assert suggestion.media.url == "https://cdn.example.com/live/master.m3u8?token=1"
    assert (suggestion.media.kind, suggestion.media.found_by) == (MediaKind.HLS, "<script>")


async def test_what_is_not_media_or_empty_is_not_suggested() -> None:
    page = html(
        '<video src=""></video><video src="  "></video><a href="">x</a><iframe src=""></iframe>'
        "<script>var page = 'https://example.com/about';</script>"
        '<script type="application/json">{not json</script>'
        '<script type="application/json">{"a.b": "/x.mp4", "*": "/y.mp4", "n": [1, true, null]}'
        "</script>",
        head='<meta property="og:video:width" content="640">'
        '<meta property="og:video" content="">'
        '<meta property="og:video:type" content="video/mp4">',
    )

    assert await suggested({PAGE: page}) == []


async def test_relative_urls_are_relative_to_the_page_s_base() -> None:
    pages = {
        PAGE: html(
            '<a href="v.mp4">v</a><iframe src="embed"></iframe>',
            head='<base href="https://cdn.example.com/x/">',
        ),
        "https://cdn.example.com/x/embed": html('<video src="clip.mp4"></video>'),
    }

    assert [s.media.url for s in await suggested(pages)] == [
        "https://cdn.example.com/x/v.mp4",
        "https://cdn.example.com/x/clip.mp4",
    ]


async def test_markup_that_no_selector_can_find_is_left_out() -> None:
    # (A tag with a colon cannot be written in CSS without escaping it: its selector fails, so
    # its chain is not suggested; the media elsewhere still is.)
    page = html(
        '<x:box><video><source src="/a.mp4"></video></x:box>'
        '<x:box><video><source src="/b.mp4"></video></x:box>'
    )

    assert [s.media.url for s in await suggested({PAGE: page})] == [f"{SITE}/a.mp4"]


async def test_fetched_pages_open_only_what_was_fetched() -> None:
    pages = FetchedPages({PAGE: StaticDocument(PAGE, html(""))})

    assert (await pages.open(PAGE, referer=None)).url == PAGE
    with pytest.raises(NotFound, match="not fetched"):
        await pages.open(f"{SITE}/other", referer=PAGE)


async def test_a_page_without_media_has_no_suggestions() -> None:
    page = html('<p>Nothing</p><img src="/logo.png"><a href="/next">Next</a>')

    assert await suggested({PAGE: page}) == []


# ---------------------------------------------------------------------------------------------
# Iframes and requests
# ---------------------------------------------------------------------------------------------


async def test_media_in_an_iframe_s_page_is_reached_through_the_iframe() -> None:
    pages = {
        PAGE: html('<iframe id="player" src="/embed/1"></iframe><iframe src="/ad"></iframe>'),
        f"{SITE}/embed/1": html('<video><source src="clip.mp4"></video>'),
    }

    [suggestion] = await suggested(pages)

    assert found([suggestion]) == [
        ("iframe#player@src >> video > source@src", f"{SITE}/embed/clip.mp4", "<video> in <iframe>")
    ]
    assert suggestion.found_on == f"{SITE}/embed/1"


async def test_what_the_page_requested_is_captured_by_its_kind() -> None:
    requested = [
        "https://cdn.example.com/a/master.m3u8",
        "https://cdn.example.com/b/master.m3u8",
        "https://cdn.example.com/stream?id=7",
    ]

    suggestions = await suggested({PAGE: html("")}, requested=requested, fetch=FetchMode.BROWSER)

    assert found(suggestions) == [
        (r"network:\.m3u8\b", requested[0], "network"),
        (r"network:/stream", requested[2], "network"),
    ]
    assert all(s.chain.fetch is FetchMode.BROWSER for s in suggestions)


async def test_a_rendered_page_s_chains_run_in_the_browser() -> None:
    [suggestion] = await suggested(
        {PAGE: html('<div id="player"><video src="/v.mp4"></video></div>')},
        fetch=FetchMode.BROWSER,
    )

    assert (suggestion.shorthand, suggestion.chain.fetch) == (
        "div#player video@src",
        FetchMode.BROWSER,
    )


async def test_each_chain_is_suggested_once() -> None:
    page = html(
        "<script>a = 'https://cdn.example.com/1.mp4'; b = 'https://cdn.example.com/2.mp4'</script>"
    )

    suggestions = await suggested({PAGE: page})

    assert [s.media.url for s in suggestions] == ["https://cdn.example.com/1.mp4"]


# ---------------------------------------------------------------------------------------------
# Stable selectors
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "target", "selector"),
    [
        ('<video id="clip"></video>', "video", "video#clip"),
        ('<video id="v123456"></video><video></video>', "video", "video"),
        ('<div><video data-player="x"></video></div>', "video", "video[data-player]"),
        ('<video class="vjs-tech main"></video>', "video", "video.vjs-tech.main"),
        ('<video class="a"></video><video class="a b"></video>', "video.b", "video.a.b"),
        ('<div id="p"><video></video></div><video></video>', "div video", "div#p video"),
        ("<video></video><video></video>", "video:nth-of-type(2)", "video:nth-of-type(2)"),
        (
            "<div><p></p><video></video></div><div><video></video></div>",
            "div:nth-of-type(2) video",
            "div:nth-of-type(2) > video",
        ),
        ('<video id="v"></video><video id="v" class="x"></video>', "video.x", "video.x"),
        (
            '<div id="p"><video></video><video></video></div>',
            "video:nth-of-type(2)",
            "video:nth-of-type(2)",
        ),
        (
            "<section><div><video></video></div><div><video></video></div></section>",
            "div:nth-of-type(2) video",
            "div:nth-of-type(2) > video",
        ),
        (
            "<section><div><video></video></div></section><div><video></video></div>",
            "body > div video",
            "body > div > video",
        ),
        (
            '<video><source type="video/webm"><source type="video/mp4"></video>',
            "[type='video/mp4']",
            'video > source[type="video/mp4"]',
        ),
        (
            '<source type="video/webm"><source type="video/mp4">',
            "[type='video/mp4']",
            'source[type="video/mp4"]',
        ),
    ],
)
def test_a_selector_finds_its_element_first(body: str, target: str, selector: str) -> None:
    page = LexborHTMLParser(html(body))
    node = page.css_first(target)
    assert node is not None

    assert selector_for(node, page) == selector
    first = page.css_first(selector)
    assert first is not None
    assert first.mem_id == node.mem_id


# ---------------------------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------------------------


def suggestion(
    name: str,
    *,
    reachable: bool | None = True,
    content_type: str | None = "video/mp4",
    size: int | None = None,
    found_by: str = "<video>",
) -> ChainSuggestion:
    chain = f"a.{name}@href"
    return ChainSuggestion.model_validate(
        {
            "chain": chain,
            "shorthand": chain,
            "media": MediaCandidate(
                url=f"{SITE}/{name}",
                content_type=content_type,
                size_bytes=size,
                found_by=found_by,
            ),
            "found_on": PAGE,
            "reachable": reachable,
        }
    )


def test_ranking_puts_media_that_answered_first_then_video_then_the_largest() -> None:
    suggestions = [
        suggestion("gone", reachable=False, size=10**9),
        suggestion("unknown", reachable=None),
        suggestion("audio", content_type="audio/mpeg", size=10**8),
        suggestion("small", size=10),
        suggestion("big", size=10**6),
        suggestion("stream", content_type="application/vnd.apple.mpegurl", size=100),
        suggestion("sized-later", size=None),
        suggestion("link", size=10**6, found_by="<a>"),
    ]

    assert [s.media.url.rpartition("/")[2] for s in ranked(suggestions)] == [
        "big",
        "link",
        "stream",
        "small",
        "sized-later",
        "audio",
        "unknown",
        "gone",
    ]


# ---------------------------------------------------------------------------------------------
# What the media's server says
# ---------------------------------------------------------------------------------------------


def test_a_probe_that_found_the_media_gives_its_type_and_size() -> None:
    probe = ProbeResult(
        tool="http",
        ok=True,
        media=[MediaCandidate(url=f"{SITE}/a", content_type="video/mp4", size_bytes=1234)],
    )

    previewed = with_preview(suggestion("a", reachable=None, content_type=None), probe)

    assert previewed.reachable is True
    assert (previewed.media.content_type, previewed.media.size_bytes) == ("video/mp4", 1234)
    assert previewed.media.url == f"{SITE}/a"


@pytest.mark.parametrize(
    ("kind", "error", "reachable"),
    [
        (MediaKind.FILE, NotFound("gone"), False),
        (MediaKind.FILE, UnsupportedURL("a web page"), False),
        (MediaKind.HLS, UnsupportedURL("a stream playlist: yt-dlp downloads it"), None),
        (MediaKind.HLS, NotFound("gone"), False),
    ],
)
def test_a_probe_that_failed_says_whether_the_media_answered(
    kind: MediaKind, error: Exception, reachable: bool | None
) -> None:
    assert isinstance(error, NotFound | UnsupportedURL)
    found = suggestion("a", reachable=None)
    found = found.model_copy(update={"media": found.media.model_copy(update={"kind": kind})})

    previewed = with_preview(found, ProbeResult.failed("http", error))

    assert previewed.reachable is reachable
