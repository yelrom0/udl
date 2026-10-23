"""
-------------
- chains.py -
-------------

Hypothesis strategies for tag chains: CSS selectors, regexes and JSON paths with the characters
that make writing them out hard (quotes, >>, |, spaces), and valid chains made of them
"""

# System Imports
import re
import string
from typing import Any

# Package Imports
from hypothesis import assume
from hypothesis import strategies as st
from pydantic import ValidationError

# Local Imports
from udl.chains.models import Pick, TagChain

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
def chains(draw: st.DrawFn) -> TagChain:
    """Valid tag chains (static, or browser when a step needs it)."""
    raw = draw(st.lists(_raw_steps(), min_size=1, max_size=6))
    try:
        return TagChain.model_validate({"steps": raw})
    except ValidationError:
        assume(False)
        raise
