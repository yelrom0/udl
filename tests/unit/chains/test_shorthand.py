"""
---------------------
- test_shorthand.py -
---------------------

A tag chain's one-line shorthand: what each form reads as, errors that say where the problem is,
and (hypothesis) that writing a chain and reading it back gives the same chain
"""

# System Imports
import contextlib
import re
import string
from typing import Any

# Package Imports
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
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
    Step,
    TagChain,
    WaitForStep,
)
from udl.chains.shorthand import ShorthandError, format_step, format_steps, parse_steps

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


# ---------------------------------------------------------------------------------------------
# Round trips (hypothesis)
# ---------------------------------------------------------------------------------------------

_NAMES = st.from_regex(r"\A[a-z][a-z0-9-]{0,6}\Z")
_TAGS = st.sampled_from(["video", "source", "iframe", "div", "a", "script", "audio", "x-player"])
_VALUES = st.text(
    alphabet=string.ascii_letters + string.digits + " >|@:.#-_/()[]'",
    max_size=12,
)


@st.composite
def _compound(draw: st.DrawFn) -> str:
    selector = draw(_TAGS)
    if draw(st.booleans()):
        selector += f"#{draw(_NAMES)}"
    for name in draw(st.lists(_NAMES, max_size=2)):
        selector += f".{name}"
    for name in draw(st.lists(_NAMES, max_size=2)):
        operator = draw(st.sampled_from(["", "=", "*=", "^=", "|="]))
        selector += f'[{name}{operator}"{draw(_VALUES)}"]' if operator else f"[{name}]"
    return selector


@st.composite
def _selectors(draw: st.DrawFn) -> str:
    parts = draw(st.lists(_compound(), min_size=1, max_size=3))
    combinators = [draw(st.sampled_from([" ", " > ", " + ", " ~ "])) for _ in parts[1:]]
    return parts[0] + "".join(c + p for c, p in zip(combinators, parts[1:], strict=True))


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
    except re.error:
        return False
    return True


_PATTERNS = st.one_of(
    st.sampled_from(
        [r"\.m3u8", "a|b", r'"file":"([^"]+)"', "a >> b", " spaced ", "'q'", "x | all"]
    ),
    st.text(min_size=1).filter(_compiles),
)
_PATHS = st.lists(
    st.text(min_size=1).filter(lambda part: "." not in part), min_size=1, max_size=3
).map(".".join)
_PICKS = st.sampled_from(list(Pick))


@st.composite
def _raw_steps(draw: st.DrawFn) -> dict[str, Any]:
    kind = draw(
        st.sampled_from(["attr", "text", "follow", "regex", "json", "wait_for", "click", "network"])
    )
    pick = draw(_PICKS).value
    match kind:
        case "attr":
            return {"select": draw(_selectors()), "attr": draw(_NAMES), "pick": pick}
        case "text":
            return {"select": draw(_selectors()), "text": True, "pick": pick}
        case "follow":
            return {"follow": True}
        case "regex" | "network":
            return {kind: draw(_PATTERNS), "pick": pick}
        case "json":
            return {"json": draw(_PATHS), "pick": pick}
        case _:
            return {kind: draw(_selectors())}


@st.composite
def _chains(draw: st.DrawFn) -> TagChain:
    raw = draw(st.lists(_raw_steps(), min_size=1, max_size=6))
    try:
        return TagChain.model_validate({"steps": raw})
    except ValidationError:
        assume(False)
        raise


@given(_chains())
def test_a_chain_written_and_read_back_is_the_same_chain(chain: TagChain) -> None:
    written = format_steps(chain.steps)

    assert TagChain.model_validate({"steps": written, "fetch": chain.fetch}) == chain
