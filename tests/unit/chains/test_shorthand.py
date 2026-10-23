"""
---------------------
- test_shorthand.py -
---------------------

A tag chain's one-line shorthand: what each form reads as, errors that say where the problem is,
and (hypothesis) that writing a chain and reading it back gives the same chain
"""

# System Imports
import contextlib

# Package Imports
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

# Local Imports
from support.chains import chains
from udl.chains.models import (
    ClickStep,
    FetchMode,
    FollowStep,
    JsonStep,
    NetworkStep,
    Pick,
    RegexStep,
    SelectStep,
    Step,
    TagChain,
    WaitForStep,
)
from udl.chains.shorthand import (
    ShorthandError,
    command_arguments,
    format_step,
    format_steps,
    parse_steps,
)

FOLLOW = FollowStep(follow=True)


def steps(text: str) -> list[Step]:
    return TagChain.model_validate({"steps": text}).steps


# ---------------------------------------------------------------------------------------------
# Reading the shorthand
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("video@src", [SelectStep(select="video", attr="src")]),
        (
            "iframe#player@src >> video > source[type*=mp4]@src",
            [
                SelectStep(select="iframe#player", attr="src"),
                FOLLOW,
                SelectStep(select="video > source[type*=mp4]", attr="src"),
            ],
        ),
        (
            "script#data::text >> json:video.sources.*.url | best",
            [
                SelectStep(select="script#data", text=True),
                JsonStep(json="video.sources.*.url", pick=Pick.BEST),
            ],
        ),
        (
            r"""script::text | all >> regex:"file: ?\"([^\"]+\.m3u8)\"" """,
            [
                SelectStep(select="script", text=True, pick=Pick.ALL),
                RegexStep(regex=r'file: ?"([^"]+\.m3u8)"'),
            ],
        ),
        (
            "a.api@href >> follow >> json:url",
            [SelectStep(select="a.api", attr="href"), FOLLOW, JsonStep(json="url")],
        ),
        (
            r"click:button.play >> wait_for:video[src] >> network:\.m3u8 | all",
            [
                ClickStep(click="button.play"),
                WaitForStep(wait_for="video[src]"),
                NetworkStep(network=r"\.m3u8", pick=Pick.ALL),
            ],
        ),
        # >>, @ and ::text inside a quoted attribute value are the selector's.
        (
            'a[title="a >> b @ c ::text"]@data-video-url',
            [SelectStep(select='a[title="a >> b @ c ::text"]', attr="data-video-url")],
        ),
        (
            r"regex:'src=\'([^\']+)\''",
            [RegexStep(regex="src='([^']+)'")],
        ),
        # A bare argument runs to the next >>; a trailing `| pick` is not part of it.
        (
            r"""regex:src="([^"]+)" | best >> video@src""",
            [RegexStep(regex='src="([^"]+)"', pick=Pick.BEST), FOLLOW],
        ),
        (
            "  video   source @src |  best  ",
            [SelectStep(select="video   source", attr="src", pick=Pick.BEST)],
        ),
        ("follow@src", [SelectStep(select="follow", attr="src")]),
        (r"div#a\:b@src", [SelectStep(select=r"div#a\:b", attr="src")]),
        (r"regex:a|b", [RegexStep(regex="a|b")]),
    ],
)
def test_reading_the_shorthand(text: str, expected: list[Step]) -> None:
    found = steps(text)

    assert found[: len(expected)] == expected


def test_a_shorthand_with_browser_steps_makes_a_browser_chain() -> None:
    assert TagChain.model_validate("click:button >> video@src").fetch is FetchMode.BROWSER
    assert TagChain.model_validate("video@src").fetch is FetchMode.STATIC


def test_steps_read_as_step_mappings() -> None:
    assert parse_steps("iframe@src >> script::text | all >> json:a.b") == [
        {"select": "iframe", "attr": "src"},
        {"select": "script", "text": True, "pick": "all"},
        {"json": "a.b"},
    ]


# ---------------------------------------------------------------------------------------------
# Errors say where the problem is
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "step", "at", "problem"),
    [
        ("", 1, 1, "the step is empty"),
        ("video@src >> ", 2, 14, "the step is empty"),
        ("video@src >> >> a@href", 2, 14, "the step is empty"),
        ("video source", 1, 1, "end the select step with @attr"),
        ("iframe@src >> video source", 2, 15, "end the select step with @attr"),
        ("@src", 1, 1, "a CSS selector comes before"),
        ("video@", 1, 7, "name an attribute after @"),
        ("video@src extra", 1, 11, "expected >> (between steps) or the end"),
        ("video@src | most", 1, 13, "first, all or best"),
        ('regex:"unterminated', 1, 7, "this quote is never closed"),
        ('video[title="x]@src', 1, 13, "this quote is never closed"),
        ("video[src@src", 1, 6, "this [ is never closed"),
        ('regex:"a" b', 1, 11, "expected >> (between steps) or the end"),
        ("follow | all", 1, 8, "follow takes nothing after it"),
        ("json: >> a@href", 1, 6, "json needs its argument"),
        ("click:button | all", 1, 14, "click keeps no values"),
    ],
)
def test_an_error_says_where_it_is(text: str, step: int, at: int, problem: str) -> None:
    with pytest.raises(ShorthandError) as error:
        parse_steps(text)

    assert (error.value.step, error.value.position + 1) == (step, at)
    assert problem in str(error.value)
    assert str(error.value).startswith(f"step {step}, at character {at}: ")


def test_a_chain_s_shorthand_error_is_a_validation_error_of_its_steps() -> None:
    with pytest.raises(ValidationError) as error:
        TagChain.model_validate({"steps": "video source"})

    [problem] = error.value.errors()
    assert problem["loc"] == ("steps",)
    assert "end the select step with @attr" in problem["msg"]


def test_a_step_s_own_problems_are_its_validation_errors() -> None:
    with pytest.raises(ValidationError) as error:
        TagChain.model_validate("iframe@src >> video[]@src")

    assert [problem["loc"][:2] for problem in error.value.errors()] == [("steps", 1)]


@given(st.text())
def test_any_text_reads_as_steps_or_a_shorthand_error(text: str) -> None:
    with contextlib.suppress(ShorthandError):
        parse_steps(text)


# ---------------------------------------------------------------------------------------------
# Writing the shorthand
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "written"),
    [
        (SelectStep(select="video source", attr="src"), "video source@src"),
        (SelectStep(select="h1", text=True, pick=Pick.ALL), "h1::text | all"),
        (FOLLOW, "follow"),
        (RegexStep(regex=r"\.m3u8"), r"regex:\.m3u8"),
        (RegexStep(regex="a|b", pick=Pick.BEST), 'regex:"a|b" | best'),
        (RegexStep(regex=r'"(\d+)"'), r'regex:"\"(\\d+)\""'),
        (JsonStep(json="a.b"), "json:a.b"),
        (JsonStep(json=" a.b"), 'json:" a.b"'),
        (WaitForStep(wait_for="video"), "wait_for:video"),
        (ClickStep(click="button.play"), "click:button.play"),
        (NetworkStep(network="a >> b"), 'network:"a >> b"'),
    ],
)
def test_writing_a_step(step: Step, written: str) -> None:
    assert format_step(step) == written


def test_a_follow_before_a_step_that_reads_the_document_goes_unwritten() -> None:
    chain = TagChain.model_validate("iframe@src >> a.api@href >> follow >> json:url")

    assert format_steps(chain.steps) == "iframe@src >> a.api@href >> follow >> json:url"


@pytest.mark.parametrize(
    ("chain", "arguments"),
    [
        ({"steps": "video@src"}, "video@src"),
        ({"steps": "video source@src", "fetch": "browser"}, "'video source@src' --browser"),
        ({"steps": "click:button >> video@src"}, "'click:button >> video@src'"),
        (
            {"steps": "a@href", "handoff": "yt-dlp", "headers": {"Referer": "{url}", "X": "1"}},
            "a@href --handoff yt-dlp -H 'Referer: {url}' -H 'X: 1'",
        ),
    ],
)
def test_a_chain_as_the_chain_commands_take_it(chain: dict[str, object], arguments: str) -> None:
    assert command_arguments(TagChain.model_validate(chain)) == arguments


# ---------------------------------------------------------------------------------------------
# Round trips (hypothesis)
# ---------------------------------------------------------------------------------------------


@given(chains())
def test_a_chain_written_and_read_back_is_the_same_chain(chain: TagChain) -> None:
    written = format_steps(chain.steps)

    assert TagChain.model_validate({"steps": written, "fetch": chain.fetch}) == chain
