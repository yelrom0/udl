"""
----------------
- shorthand.py -
----------------

A tag chain's one-line shorthand, for the command line and AI agents:

    iframe#player@src >> video > source[type*=mp4]@src

Steps are separated by `>>`. A select step is a CSS selector, then what to take from the elements
it selects: `@attr` (an attribute, e.g. `@src`) or `::text`. The other steps are a keyword and
its argument: `regex:PATTERN`, `json:PATH`, `wait_for:SELECTOR`, `click:SELECTOR` and
`network:PATTERN`, and `follow` alone. An argument may be quoted ("..." or '...', in which a
backslash before the quote or before another backslash is the only escape), and must be when it
starts with a quote, has `>>` or `|` in it, or spaces around it. A step that finds values may end
with `| all` or `| best` (`| first` is the default).

A step that reads the document (select, wait_for, click, network) after a step that found values
follows them first, so `follow` is only written before a regex or json step, which then works on
the document it opened. Reading what format_steps() wrote gives the same chain.
"""

# System Imports
import re
from collections.abc import Sequence
from typing import Final

# Local Imports
from udl.chains.models import (
    FollowStep,
    JsonStep,
    NetworkStep,
    Pick,
    RegexStep,
    SelectStep,
    Step,
)

type RawStep = dict[str, str | bool]
"""A step as a mapping, e.g. {"select": "video", "attr": "src"}; TagChain validates it."""

_SEPARATOR: Final = ">>"
_TEXT: Final = "::text"
_QUOTES: Final = "\"'"
_BRACKETS: Final = {"[": "]", "(": ")"}
# The keywords of the steps written as `keyword:argument`, and whether their argument is CSS.
_KEYWORDS: Final = {
    "regex": False,
    "json": False,
    "wait_for": True,
    "click": True,
    "network": False,
}
_PICKS: Final = frozenset(pick.value for pick in Pick)
_ATTR: Final = re.compile(r"[A-Za-z_:][\w:.-]*")
_WORD: Final = re.compile(r"\w*")
_TRAILING_PICK: Final = re.compile(r"\s\|\s*(\w+)\s*\Z")


class ShorthandError(ValueError):
    """A shorthand that cannot be read: the step and the place (an index into the text) where
    the problem is, and what it is."""

    def __init__(self, problem: str, *, step: int, position: int) -> None:
        super().__init__(f"step {step}, at character {position + 1}: {problem}")
        self.step = step
        self.position = position


# ---------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------


class _Reader:
    """Reads a shorthand's steps, left to right."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.at = 0
        self.step = 1

    def error(self, problem: str, at: int | None = None) -> ShorthandError:
        return ShorthandError(problem, step=self.step, position=self.at if at is None else at)

    def done(self) -> bool:
        return self.at >= len(self.text)

    def looking_at(self, text: str) -> bool:
        return self.text.startswith(text, self.at)

    def skip_spaces(self) -> None:
        while not self.done() and self.text[self.at].isspace():
            self.at += 1

    def steps(self) -> list[RawStep]:
        steps: list[RawStep] = []
        while True:
            self.skip_spaces()
            steps.append(self.read_step())
            self.skip_spaces()
            if self.done():
                return steps
            if not self.looking_at(_SEPARATOR):
                raise self.error("expected >> (between steps) or the end of the chain")
            self.at += len(_SEPARATOR)
            self.step += 1

    def read_step(self) -> RawStep:
        if self.done() or self.looking_at(_SEPARATOR):
            raise self.error("the step is empty")
        for keyword, css in _KEYWORDS.items():
            if self.looking_at(f"{keyword}:"):
                self.at += len(keyword) + 1
                return self.keyword_step(keyword, css=css)
        after = self.at + len("follow")
        follow = self.text[after : after + 1]
        if self.looking_at("follow") and (not follow or follow.isspace() or follow in ">|"):
            self.at = after
            self.skip_spaces()
            if not (self.done() or self.looking_at(_SEPARATOR)):
                raise self.error("follow takes nothing after it")
            return {"follow": True}
        return self.select_step()

    def select_step(self) -> RawStep:
        start = self.at
        selector = self.css(extract=True)
        if self.looking_at("@"):
            self.at += 1
            if not (attr := _ATTR.match(self.text, self.at)):
                raise self.error("name an attribute after @, e.g. @src")
            self.at = attr.end()
            step: RawStep = {"select": selector, "attr": attr.group()}
        elif self.looking_at(_TEXT):
            self.at += len(_TEXT)
            step = {"select": selector, "text": True}
        else:
            raise self.error(
                "end the select step with @attr (an attribute, e.g. @src) or ::text (the "
                "elements' text)",
                at=start,
            )
        if not selector:
            raise self.error("a CSS selector comes before the @attr or ::text", at=start)
        if pick := self.pick():
            step["pick"] = pick
        return step

    def keyword_step(self, keyword: str, *, css: bool) -> RawStep:
        start = self.at
        pick: str | None = None
        if css:
            argument = self.css(extract=False)
            if self.looking_at("|"):
                raise self.error(f"{keyword} keeps no values, so nothing comes after it")
        elif not self.done() and self.text[self.at] in _QUOTES:
            argument = self.quoted()
            pick = self.pick()
        else:
            end = self.text.find(_SEPARATOR, self.at)
            end = len(self.text) if end < 0 else end
            argument = self.text[self.at : end]
            if trailing := _TRAILING_PICK.search(argument):
                pick = self.picked(trailing.group(1), self.at + trailing.start(1))
                argument = argument[: trailing.start()]
            argument = argument.strip()
            self.at = end
        if not argument:
            raise self.error(f"{keyword} needs its argument", at=start)
        step: RawStep = {keyword: argument}
        if pick is not None:
            step["pick"] = pick
        return step

    def css(self, *, extract: bool) -> str:
        """A CSS selector, read up to a `>>` (or a `|` after a space, or with `extract`, the
        `@attr` or `::text` that ends a select step) outside its quotes and brackets."""
        start = self.at
        opened: list[tuple[str, int]] = []  # the brackets open, and where
        while not self.done():
            char = self.text[self.at]
            if char in _QUOTES:
                self.at = self.quoted_end(char)
                continue
            if char == "\\":  # (an escaped character, e.g. #a\:b)
                self.at += 2
                continue
            if char in _BRACKETS:
                opened.append((_BRACKETS[char], self.at))
            elif opened and char == opened[-1][0]:
                opened.pop()
            elif not opened and (
                self.looking_at(_SEPARATOR)
                or (char == "|" and self.text[self.at - 1 : self.at].isspace())
                or (extract and (char == "@" or self.looking_at(_TEXT)))
            ):
                break
            self.at += 1
        if opened:
            closer, at = opened[0]
            opener = next(o for o, c in _BRACKETS.items() if c == closer)
            raise self.error(f"this {opener} is never closed", at=at)
        self.at = min(self.at, len(self.text))
        return self.text[start : self.at].strip()

    def quoted_end(self, quote: str) -> int:
        """Where the quoted text starting here ends (after its closing quote)."""
        at = self.at + 1
        while at < len(self.text):
            if self.text[at] == "\\":
                at += 2
                continue
            if self.text[at] == quote:
                return at + 1
            at += 1
        raise self.error("this quote is never closed")

    def quoted(self) -> str:
        """A quoted argument, unquoted."""
        quote = self.text[self.at]
        end = self.quoted_end(quote)
        inside = self.text[self.at + 1 : end - 1]
        self.at = end
        return re.sub(rf"\\([\\{quote}])", r"\1", inside)

    def pick(self) -> str | None:
        """The `| pick` after a step, if it has one."""
        before = self.at
        self.skip_spaces()
        if not self.looking_at("|"):
            self.at = before
            return None
        self.at += 1
        self.skip_spaces()
        start = self.at
        word = _WORD.match(self.text, start)
        self.at = word.end() if word else start
        return self.picked(self.text[start : self.at], start)

    def picked(self, word: str, at: int) -> str:
        if word not in _PICKS:
            raise self.error("after |, say which values to keep: first, all or best", at=at)
        return word


def parse_steps(text: str) -> list[RawStep]:
    """The steps a shorthand writes, as step mappings (TagChain validates them, and writes out
    the follows they imply). Raises ShorthandError."""
    return _Reader(text).steps()


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------


def _argument(value: str) -> str:
    """A keyword step's argument: bare when it can be, else quoted."""
    bare = (
        value
        and value == value.strip()
        and value[0] not in _QUOTES
        and _SEPARATOR not in value
        and "|" not in value
    )
    if bare:
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _with_pick(text: str, pick: Pick) -> str:
    return text if pick is Pick.FIRST else f"{text} | {pick}"


def format_step(step: Step) -> str:
    """One step in the shorthand."""
    if isinstance(step, FollowStep):
        return "follow"
    if isinstance(step, SelectStep):
        text = f"{step.select}{_TEXT if step.text else f'@{step.attr}'}"
    else:
        argument = str(step.model_dump(by_alias=True)[step.kind])
        text = f"{step.kind}:{argument if _KEYWORDS[step.kind] else _argument(argument)}"
    if isinstance(step, SelectStep | RegexStep | JsonStep | NetworkStep):
        return _with_pick(text, step.pick)
    return text


def format_steps(steps: Sequence[Step]) -> str:
    """A chain's steps in the shorthand. A follow that the next step implies is left out."""
    written = [
        format_step(step)
        for step, after in zip(steps, [*steps[1:], None], strict=True)
        if not (isinstance(step, FollowStep) and after is not None and after.reads_document)
    ]
    return f" {_SEPARATOR} ".join(written)
