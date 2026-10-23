"""
--------------------
- test_executor.py -
--------------------

Running a tag chain on documents read by a fake reader (pages in a dict): what each step finds,
following, picking, relative URLs, what the media is, the trace, and the errors that say which
step found nothing
"""

# System Imports
import re
from collections.abc import Mapping

# Package Imports
import pytest

# Local Imports
from udl.chains.executor import ChainRun, Document, Reader, StaticDocument, run_chain
from udl.chains.models import StepTrace, TagChain
from udl.errors import AuthRequired, ExtractionFailed, NotFound, UnsupportedURL
from udl.plugins.downloaders.base import MediaKind

SITE = "https://example.com"
PAGE = f"{SITE}/watch/1"


def html(body: str, head: str = "") -> str:
    return f"<!doctype html><html><head>{head}</head><body>{body}</body></html>"


class FakeReader(Reader):
    """Opens the pages it has (a URL's text); any other URL is not found. It remembers what it
    opened, and from where."""

    def __init__(self, pages: Mapping[str, str], fails: Mapping[str, Exception] | None = None):
        self.pages = dict(pages)
        self.fails = dict(fails or {})
        self.opened: list[tuple[str, str | None]] = []

    async def open(self, url: str, *, referer: str | None) -> Document:
        self.opened.append((url, referer))
        if url in self.fails:
            raise self.fails[url]
        if url not in self.pages:
            raise NotFound(f"{url} is not found")
        return StaticDocument(url, self.pages[url])


class FakePage(StaticDocument):
    """A browser page: it records clicks and waits, and has requested `requests`."""

    def __init__(self, url: str, text: str, requests: list[tuple[str, str]]) -> None:
        super().__init__(url, text)
        self.requests = requests
        self.done: list[str] = []

    async def wait_for(self, selector: str) -> None:
        self.done.append(f"wait_for {selector}")

    async def click(self, selector: str) -> None:
        self.done.append(f"click {selector}")

    async def requested(self, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
        return [(url, referer) for url, referer in self.requests if pattern.search(url)]


class BrowserReader(FakeReader):
    def __init__(self, page: FakePage) -> None:
        super().__init__({})
        self.page = page

    async def open(self, url: str, *, referer: str | None) -> Document:
        self.opened.append((url, referer))
        return self.page


async def run(chain: str, pages: Mapping[str, str]) -> ChainRun:
    return await run_chain(TagChain.model_validate(chain), PAGE, FakeReader(pages))


def urls(found: ChainRun) -> list[str]:
    return [find.media.url for find in found.finds]


# ---------------------------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------------------------


async def test_select_takes_an_attribute_and_resolves_it_against_the_page() -> None:
    page = html('<video id="clip"><source src="/media/a.mp4" type="video/mp4"></video>')

    found = await run("video#clip source@src", {PAGE: page})

    assert urls(found) == [f"{SITE}/media/a.mp4"]
    [find] = found.finds
    assert (find.media.kind, find.media.found_by, find.referer) == (
        MediaKind.FILE,
        "tag chain",
        PAGE,
    )
    assert found.every is False


async def test_select_takes_text() -> None:
    page = html("<p class='link'>  https://cdn.example.com/a.mp4 </p>")

    assert urls(await run("p.link::text", {PAGE: page})) == ["https://cdn.example.com/a.mp4"]


async def test_relative_urls_resolve_against_the_page_s_base() -> None:
    page = html('<video src="a.mp4"></video>', head='<base href="https://cdn.example.com/v/">')

    assert urls(await run("video@src", {PAGE: page})) == ["https://cdn.example.com/v/a.mp4"]


async def test_elements_without_the_attribute_are_skipped() -> None:
    page = html('<video></video><video src=""></video><video src="b.mp4"></video>')

    assert urls(await run("video@src", {PAGE: page})) == [f"{SITE}/watch/b.mp4"]


@pytest.mark.parametrize(
    ("pick", "expected"),
    [
        ("", ["low.mp4"]),
        (" | first", ["low.mp4"]),
        (" | all", ["low.mp4", "high.mp4", "mid.mp4", "720p.mp4"]),
        (" | best", ["high.mp4"]),
    ],
)
async def test_which_values_a_step_keeps(pick: str, expected: list[str]) -> None:
    page = html(
        '<video><source src="low.mp4" res="360"><source src="high.mp4" label="1080p">'
        '<source src="mid.mp4" data-quality="480"><source src="720p.mp4"></video>'
    )

    found = await run(f"video source@src{pick}", {PAGE: page})

    assert urls(found) == [f"{SITE}/watch/{name}" for name in expected]
    assert found.every is (pick == " | all")


async def test_best_is_the_first_when_nothing_names_a_resolution() -> None:
    page = html('<video><source src="a.mp4"><source src="b.mp4"></video>')

    assert urls(await run("video source@src | best", {PAGE: page})) == [f"{SITE}/watch/a.mp4"]


async def test_best_reads_a_resolution_in_the_value() -> None:
    page = html('<a href="v_480p.mp4">a</a><a href="v_1080p.mp4">b</a><a href="v_720p.mp4">c</a>')

    found = await run("a@href | best", {PAGE: page})

    assert urls(found) == [f"{SITE}/watch/v_1080p.mp4"]


# ---------------------------------------------------------------------------------------------
# follow
# ---------------------------------------------------------------------------------------------


async def test_a_select_after_a_select_opens_its_url() -> None:
    pages = {
        PAGE: html('<iframe id="player" src="/embed/1"></iframe>'),
        f"{SITE}/embed/1": html('<video><source src="clip.mp4"></video>'),
    }
    reader = FakeReader(pages)

    found = await run_chain(
        TagChain.model_validate("iframe#player@src >> video source@src"), PAGE, reader
    )

    assert urls(found) == [f"{SITE}/embed/clip.mp4"]
    assert found.finds[0].referer == f"{SITE}/embed/1"
    assert reader.opened == [(PAGE, None), (f"{SITE}/embed/1", PAGE)]


async def test_follow_then_json_reads_the_opened_document() -> None:
    pages = {
        PAGE: html('<a class="api" href="/api/video/1">x</a>'),
        f"{SITE}/api/video/1": '{"sources": [{"url": "https://cdn.example.com/1.m3u8"}]}',
    }

    found = await run("a.api@href >> follow >> json:sources.0.url", pages)

    assert urls(found) == ["https://cdn.example.com/1.m3u8"]
    assert found.finds[0].media.kind is MediaKind.HLS


async def test_follow_opens_every_value_kept_but_no_more_than_ten() -> None:
    links = "".join(f'<a href="/p/{n}">{n}</a>' for n in range(12))
    pages = {PAGE: html(links)} | {
        f"{SITE}/p/{n}": html(f'<video src="/v/{n}.mp4"></video>') for n in range(12)
    }

    found = await run("a@href | all >> video@src", pages)

    assert urls(found) == [f"{SITE}/v/{n}.mp4" for n in range(10)]


async def test_a_follow_that_is_not_found_is_skipped_when_others_open() -> None:
    pages = {
        PAGE: html('<a href="/gone">a</a><a href="/p/1">b</a>'),
        f"{SITE}/p/1": html('<video src="/v/1.mp4"></video>'),
    }

    assert urls(await run("a@href | all >> video@src", pages)) == [f"{SITE}/v/1.mp4"]


async def test_a_follow_that_fails_alone_is_the_error() -> None:
    with pytest.raises(NotFound, match="/gone"):
        await run("a@href >> video@src", {PAGE: html('<a href="/gone">a</a>')})


async def test_a_follow_that_needs_a_login_stops_the_chain() -> None:
    pages = {
        PAGE: html('<a href="/private">a</a><a href="/p/1">b</a>'),
        f"{SITE}/p/1": html('<video src="/v/1.mp4"></video>'),
    }
    reader = FakeReader(pages, fails={f"{SITE}/private": AuthRequired("Sign in")})

    with pytest.raises(AuthRequired):
        await run_chain(TagChain.model_validate("a@href | all >> video@src"), PAGE, reader)


async def test_values_that_are_not_http_urls_are_not_followed() -> None:
    page = html('<a href="javascript:void(0)">a</a><a href="mailto:x@example.com">b</a>')

    with pytest.raises(ExtractionFailed, match=r"step 2 \(follow\) found nothing"):
        await run("a@href | all >> video@src", {PAGE: page})


# ---------------------------------------------------------------------------------------------
# regex and json
# ---------------------------------------------------------------------------------------------


async def test_regex_takes_its_first_group() -> None:
    page = html("<script>player.setup({file: 'https:\\/\\/cdn.example.com\\/a.m3u8'})</script>")

    found = await run(r"script::text >> regex:file: '([^']+)'", {PAGE: page})

    assert urls(found) == ["https://cdn.example.com/a.m3u8"]


async def test_regex_without_groups_takes_the_whole_match() -> None:
    page = html("<script>var a = 'https://cdn.example.com/a.mp4', b = 'x.mp4';</script>")

    found = await run(r"script::text >> regex:https://\S+?\.mp4 | all", {PAGE: page})

    assert urls(found) == ["https://cdn.example.com/a.mp4"]


async def test_a_regex_first_works_on_the_page_s_html() -> None:
    page = html('<div data-src="https://cdn.example.com/a.mp4?x=1&amp;y=2"></div>')

    found = await run(r'regex:data-src="([^"]+)"', {PAGE: page})

    assert urls(found) == ["https://cdn.example.com/a.mp4?x=1&y=2"]


async def test_json_takes_a_dotted_path() -> None:
    data = '{"video": {"sources": [{"src": "/a.mp4", "res": 360}, {"src": "/b_720p.mp4"}]}}'
    page = html(f'<script id="data" type="application/json">{data}</script>')

    first = await run("script#data::text >> json:video.sources.1.src", {PAGE: page})
    every = await run("script#data::text >> json:video.sources.*.src | all", {PAGE: page})
    best = await run("script#data::text >> json:video.sources.*.src | best", {PAGE: page})

    assert urls(first) == [f"{SITE}/b_720p.mp4"]
    assert urls(every) == [f"{SITE}/a.mp4", f"{SITE}/b_720p.mp4"]
    assert urls(best) == [f"{SITE}/b_720p.mp4"]


async def test_json_values_that_are_not_text_are_json_for_the_next_step() -> None:
    data = '{"player": {"config": {"hls": "https://cdn.example.com/a.m3u8", "n": 3}}}'
    page = html(f"<script type='application/json'>{data}</script>")

    found = await run("script::text >> json:player.config >> json:hls", {PAGE: page})

    assert urls(found) == ["https://cdn.example.com/a.m3u8"]


async def test_json_star_takes_every_value_of_an_object() -> None:
    data = '{"formats": {"hd": "/hd.mp4", "sd": "/sd.mp4"}}'
    page = html(f"<script type='application/json'>{data}</script>")

    found = await run("script::text >> json:formats.* | all", {PAGE: page})

    assert urls(found) == [f"{SITE}/hd.mp4", f"{SITE}/sd.mp4"]


@pytest.mark.parametrize(
    "data", ["not json", '{"video": 3}', '{"video": {"src": null}}', '["x"]', '{"video": []}']
)
async def test_json_that_does_not_have_the_path_finds_nothing(data: str) -> None:
    page = html(f"<script>{data}</script>")

    with pytest.raises(ExtractionFailed, match=r"step 2 \(json:video\.src\) found nothing"):
        await run("script::text >> json:video.src", {PAGE: page})


async def test_a_number_in_json_is_its_text() -> None:
    page = html('<script>{"id": 1234}</script>')

    assert urls(await run("script::text >> json:id", {PAGE: page})) == [f"{SITE}/watch/1234"]


# ---------------------------------------------------------------------------------------------
# What the chain ends on
# ---------------------------------------------------------------------------------------------


async def test_the_finds_are_the_last_step_s_urls_each_once() -> None:
    page = html('<a href="/a.mp4">1</a><a href="/a.mp4">2</a><a href="/b.mp4">3</a>')

    assert urls(await run("a@href | all", {PAGE: page})) == [f"{SITE}/a.mp4", f"{SITE}/b.mp4"]


async def test_a_stream_playlist_is_found_as_one() -> None:
    page = html('<video src="https://cdn.example.com/master.mpd"></video>')

    [find] = (await run("video@src", {PAGE: page})).finds

    assert find.media.kind is MediaKind.DASH


async def test_last_values_that_are_not_urls_fail_the_chain() -> None:
    page = html("<h1>Not a URL</h1>")

    with pytest.raises(ExtractionFailed, match="found no URLs") as error:
        await run("h1::text", {PAGE: page})

    assert "'Not a URL'" in str(error.value)


@pytest.mark.parametrize("value", ["http://[broken", "ftp://example.com/a.mp4", "a b.mp4"])
async def test_values_that_are_no_http_urls_are_not_media(value: str) -> None:
    page = html(f"<p>{value}</p><a href='{value}'>x</a>")

    with pytest.raises(ExtractionFailed, match="found no URLs"):
        await run("a@href", {PAGE: page})


async def test_the_title_is_the_page_s() -> None:
    page = html('<video src="a.mp4"></video>', head='<meta property="og:title" content="A film">')

    assert (await run("video@src", {PAGE: page})).title == "A film"


# ---------------------------------------------------------------------------------------------
# A step that finds nothing
# ---------------------------------------------------------------------------------------------


async def test_a_step_that_finds_nothing_says_which_and_where() -> None:
    pages = {PAGE: html('<iframe src="/embed"></iframe>'), f"{SITE}/embed": html("<p>no</p>")}

    with pytest.raises(ExtractionFailed) as error:
        await run("iframe@src >> video source@src", pages)

    assert str(error.value) == (
        f"The tag chain's step 3 (video source@src) found nothing on {SITE}/embed"
    )
    assert error.value.hint is not None
    assert "udl chain test" in error.value.hint
    assert error.value.counts_against_strategy


async def test_the_start_page_s_own_failure_is_the_error() -> None:
    with pytest.raises(NotFound):
        await run("video@src", {})


# ---------------------------------------------------------------------------------------------
# The trace
# ---------------------------------------------------------------------------------------------


async def test_the_trace_says_what_each_step_found() -> None:
    pages = {
        PAGE: html('<iframe src="/embed"></iframe>'),
        f"{SITE}/embed": html('<video><source src="a.mp4"><source src="b.mp4"></video>'),
    }
    trace: list[StepTrace] = []

    await run_chain(
        TagChain.model_validate("iframe@src >> video source@src | all"),
        PAGE,
        FakeReader(pages),
        trace=trace,
    )

    assert trace == [
        StepTrace(step="iframe@src", found=["/embed"], count=1),
        StepTrace(step="follow", found=[f"{SITE}/embed"], count=1),
        StepTrace(step="video source@src | all", found=["a.mp4", "b.mp4"], count=2),
    ]


async def test_the_trace_shows_ten_values_and_counts_them_all() -> None:
    page = html("".join(f'<a href="/{n}.mp4">{n}</a>' for n in range(15)))
    trace: list[StepTrace] = []

    await run_chain(
        TagChain.model_validate("a@href | all"), PAGE, FakeReader({PAGE: page}), trace=trace
    )

    assert trace[0].count == 15
    assert trace[0].found == [f"/{n}.mp4" for n in range(10)]


async def test_the_trace_holds_the_steps_before_a_failure() -> None:
    trace: list[StepTrace] = []

    with pytest.raises(ExtractionFailed):
        await run_chain(
            TagChain.model_validate("iframe@src >> video@src"),
            PAGE,
            FakeReader({PAGE: html("<p>nothing</p>")}),
            trace=trace,
        )

    assert trace == [StepTrace(step="iframe@src", found=[], count=0)]


# ---------------------------------------------------------------------------------------------
# Browser steps
# ---------------------------------------------------------------------------------------------


async def test_browser_steps_act_on_the_page() -> None:
    page = FakePage(
        PAGE,
        html('<button class="play"></button>'),
        requests=[
            (f"{SITE}/app.js", PAGE),
            ("https://cdn.example.com/a/master.m3u8", f"{SITE}/embed"),
            ("https://cdn.example.com/b/master.m3u8", PAGE),
        ],
    )

    found = await run_chain(
        TagChain.model_validate(r"wait_for:button.play >> click:button.play >> network:\.m3u8"),
        PAGE,
        BrowserReader(page),
    )

    assert page.done == ["wait_for button.play", "click button.play"]
    assert urls(found) == ["https://cdn.example.com/a/master.m3u8"]
    assert found.finds[0].referer == f"{SITE}/embed"


async def test_a_network_step_that_sees_no_request_finds_nothing() -> None:
    page = FakePage(PAGE, html(""), requests=[])

    with pytest.raises(ExtractionFailed, match=r"step 1 \(network:\\\.mp4\) found nothing"):
        await run_chain(TagChain.model_validate(r"network:\.mp4"), PAGE, BrowserReader(page))


@pytest.mark.parametrize("step", ["wait_for:video >> video@src", "click:a >> video@src"])
async def test_a_static_document_cannot_do_browser_steps(step: str) -> None:
    chain = TagChain.model_validate(step)

    with pytest.raises(UnsupportedURL, match="needs the browser"):
        await run_chain(chain, PAGE, FakeReader({PAGE: html("<video></video>")}))


async def test_a_static_document_has_no_requests() -> None:
    chain = TagChain.model_validate(r"network:\.mp4")

    with pytest.raises(UnsupportedURL, match="needs the browser"):
        await run_chain(chain, PAGE, FakeReader({PAGE: html("")}))
