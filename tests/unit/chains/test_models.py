"""
------------------
- test_models.py -
------------------

Tag chains as data: the step types (told apart by their key), what each step needs, the order
steps may come in, the follows the model writes out, and what needs the browser
"""

# System Imports
from typing import Any

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from udl.chains.models import (
    ClickStep,
    FetchMode,
    FollowStep,
    JsonStep,
    NetworkStep,
    Pick,
    RegexStep,
    SelectStep,
    SiteChain,
    TagChain,
    WaitForStep,
)

FOLLOW = FollowStep(follow=True)


def chain(*steps: dict[str, Any], **fields: Any) -> TagChain:
    return TagChain.model_validate({"steps": list(steps), **fields})


def problems(error: pytest.ExceptionInfo[ValidationError]) -> str:
    return "; ".join(str(problem["msg"]) for problem in error.value.errors())


# ---------------------------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "step"),
    [
        (
            {"select": "video > source", "attr": "src"},
            SelectStep(select="video > source", attr="src"),
        ),
        ({"select": "h1", "text": True}, SelectStep(select="h1", text=True)),
        ({"regex": r"file: '([^']+)'"}, RegexStep(regex=r"file: '([^']+)'")),
        ({"json": "video.sources.*.url"}, JsonStep(json="video.sources.*.url")),
        ({"wait_for": "video"}, WaitForStep(wait_for="video")),
        ({"click": "button.play"}, ClickStep(click="button.play")),
        ({"network": r"\.m3u8", "pick": "all"}, NetworkStep(network=r"\.m3u8", pick=Pick.ALL)),
    ],
)
def test_a_step_is_told_apart_by_its_key(raw: dict[str, Any], step: object) -> None:
    # (wait_for and click find nothing, so a select ends their chains.)
    found = chain(raw, {"select": "video", "attr": "src"}, fetch="browser")

    assert found.steps[0] == step


def test_steps_dump_as_they_are_written() -> None:
    dumped = chain(
        {"select": "iframe", "attr": "src"},
        {"select": "script#data", "text": True},
        {"json": "video.url", "pick": "best"},
    ).model_dump(mode="json", exclude_defaults=True)

    assert dumped["steps"] == [
        {"select": "iframe", "attr": "src"},
        {"follow": True},
        {"select": "script#data", "text": True},
        {"json": "video.url", "pick": "best"},
    ]
    assert TagChain.model_validate(dumped).model_dump(mode="json", exclude_defaults=True) == dumped


def test_an_unknown_step_is_rejected() -> None:
    with pytest.raises(ValidationError) as error:
        chain({"scroll": "down"})

    assert "select, follow, regex, json, wait_for, click or network" in problems(error)


@pytest.mark.parametrize("given", [42, {"steps": 42}, {"steps": [42]}])
def test_what_is_not_a_chain_is_rejected(given: object) -> None:
    with pytest.raises(ValidationError):
        TagChain.model_validate(given)


def test_a_step_is_one_thing() -> None:
    with pytest.raises(ValidationError, match="regex"):
        chain({"select": "video", "attr": "src", "regex": "x"})


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"select": "video"}, "an attribute (attr) or the text"),
        ({"select": "video", "attr": "src", "text": True}, "not both"),
        ({"select": "video[", "attr": "src"}, "not a CSS selector"),
        ({"select": "  ", "attr": "src"}, "at least 1 character"),
        ({"select": "video", "attr": "s rc"}, "should match pattern"),
        ({"regex": "(unclosed"}, "not a valid regular expression"),
        ({"json": "video..url"}, "empty part"),
        ({"json": ""}, "empty part"),
        ({"follow": False}, "Input should be True"),
    ],
)
def test_what_a_step_needs(raw: dict[str, Any], message: str) -> None:
    steps = [{"select": "a", "attr": "href"}, raw] if "follow" in raw else [raw]
    if "follow" in raw:
        steps.append({"select": "video", "attr": "src"})

    with pytest.raises(ValidationError) as error:
        chain(*steps)

    assert message in problems(error)


def test_selectors_lose_the_spaces_around_them() -> None:
    assert chain({"select": "  video  source ", "attr": "src"}).steps == [
        SelectStep(select="video  source", attr="src")
    ]


# ---------------------------------------------------------------------------------------------
# The order of the steps
# ---------------------------------------------------------------------------------------------


def test_a_step_that_reads_the_document_after_values_follows_them() -> None:
    found = chain(
        {"select": "iframe#player", "attr": "src"},
        {"select": "video source", "attr": "src", "pick": "best"},
    )

    assert found.steps == [
        SelectStep(select="iframe#player", attr="src"),
        FOLLOW,
        SelectStep(select="video source", attr="src", pick=Pick.BEST),
    ]


def test_a_follow_already_written_is_kept_once() -> None:
    written = chain(
        {"select": "a.api", "attr": "href"},
        {"follow": True},
        {"select": "video", "attr": "src"},
    )

    assert (
        written.steps
        == chain({"select": "a.api", "attr": "href"}, {"select": "video", "attr": "src"}).steps
    )


def test_value_steps_work_on_the_values_before_them() -> None:
    steps = chain(
        {"select": "script#data", "text": True},
        {"json": "player.config"},
        {"regex": r'"file":"([^"]+)"'},
    ).steps

    assert FOLLOW not in steps


def test_a_follow_before_a_value_step_opens_a_document_for_it() -> None:
    steps = chain({"select": "a.api", "attr": "href"}, {"follow": True}, {"json": "url"}).steps

    assert steps == [SelectStep(select="a.api", attr="href"), FOLLOW, JsonStep(json="url")]


def test_browser_steps_on_the_page_need_no_follow() -> None:
    steps = chain(
        {"click": "button.play"}, {"wait_for": "video"}, {"select": "video", "attr": "src"}
    ).steps

    assert FOLLOW not in steps


@pytest.mark.parametrize(
    "steps",
    [
        [{"follow": True}, {"select": "video", "attr": "src"}],
        [{"click": "button"}, {"follow": True}, {"select": "video", "attr": "src"}],
        [{"select": "a", "attr": "href"}, {"follow": True}, {"follow": True}, {"json": "url"}],
    ],
)
def test_a_follow_needs_a_url_from_the_step_before(steps: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError) as error:
        chain(*steps, fetch="browser")

    assert "follow opens a URL that the step before it found" in problems(error)


@pytest.mark.parametrize(
    "steps",
    [
        [{"select": "a", "attr": "href"}, {"follow": True}],
        [{"click": "button.play"}],
        [{"wait_for": "video"}],
    ],
)
def test_the_last_step_finds_the_media(steps: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError) as error:
        chain(*steps, fetch="browser")

    assert "the last step must find the media's URLs" in problems(error)


def test_a_chain_has_steps() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        chain()


# ---------------------------------------------------------------------------------------------
# Static and browser chains
# ---------------------------------------------------------------------------------------------


def test_a_chain_is_static_by_default() -> None:
    found = chain({"select": "video", "attr": "src"})

    assert found.fetch is FetchMode.STATIC
    assert (found.headers, found.handoff) == ({}, None)


@pytest.mark.parametrize("step", [{"click": "button"}, {"wait_for": "video"}, {"network": "mp4"}])
def test_a_chain_with_browser_steps_runs_in_the_browser(step: dict[str, Any]) -> None:
    steps = [step] if "network" in step else [step, {"select": "video", "attr": "src"}]

    assert chain(*steps).fetch is FetchMode.BROWSER


@pytest.mark.parametrize("step", [{"click": "button"}, {"wait_for": "video"}, {"network": "mp4"}])
def test_browser_steps_are_rejected_in_a_static_chain(step: dict[str, Any]) -> None:
    steps = [step] if "network" in step else [step, {"select": "video", "attr": "src"}]

    with pytest.raises(ValidationError) as error:
        chain(*steps, fetch="static")

    assert "need the browser" in problems(error)
    assert next(iter(step)) in problems(error)


def test_the_scraper_runs_a_static_chain_and_the_browser_a_browser_chain() -> None:
    assert chain({"select": "video", "attr": "src"}).tool == "scraper"
    assert chain({"select": "video", "attr": "src"}, fetch="browser").tool == "browser"


def test_a_static_chain_can_run_in_the_browser() -> None:
    assert chain({"select": "video", "attr": "src"}, fetch="browser").fetch is FetchMode.BROWSER


def test_headers_and_the_handoff() -> None:
    found = chain(
        {"select": "video", "attr": "src"},
        headers={"Referer": "{url}"},
        handoff="yt-dlp",
    )

    assert found.headers == {"Referer": "{url}"}
    assert found.handoff == "yt-dlp"


def test_chains_are_frozen() -> None:
    found = chain({"select": "video", "attr": "src"})

    with pytest.raises(ValidationError):
        found.fetch = FetchMode.BROWSER


# ---------------------------------------------------------------------------------------------
# A site's chain, as chain files hold it
# ---------------------------------------------------------------------------------------------


def test_a_site_chain_names_its_site_by_key() -> None:
    found = SiteChain.model_validate(
        {"site": "https://www.example.co.uk/watch/1", "steps": [{"select": "video", "attr": "src"}]}
    )

    assert found.site == "example.co.uk"
    assert (found.pattern, found.priority, found.requires_auth) == (None, 0, False)
    assert found.chain() == chain({"select": "video", "attr": "src"})


def test_a_site_chain_s_pattern_is_a_regex() -> None:
    with pytest.raises(ValidationError, match="not a valid regular expression"):
        SiteChain.model_validate(
            {"site": "example.com", "pattern": "(", "steps": [{"select": "video", "attr": "src"}]}
        )
