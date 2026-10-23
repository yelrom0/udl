"""
-------------
- models.py -
-------------

Tag chains as data. A chain is the steps from a web page to its media, for a site no downloader
handles on its own. It is stored on one of the site's strategies (whose tool is the scraper, or
the browser for a chain that needs one), so it is remembered, synced, imported and exported like
any strategy.

Each step reads the document the chain is on, or the values the step before it found:

- select: CSS-select elements, and take an attribute of each (attr) or its text;
- follow: open the URL the step before found, and go on on that document;
- regex: the matches of a regex in the values (its first group, if it has groups);
- json: parse the values as JSON, and take a dotted path in them;
- wait_for, click (browser only): wait for an element, or click one (e.g. a play button);
- network (browser only): the URLs the page has requested that match a regex.

A step is told apart by its key (`{"select": "video", "attr": "src"}`). A step that reads the
document (select, wait_for, click, network) right after one that found values follows them first:
`iframe#player@src >> video source@src` opens the iframe's page, then selects in it. The model
writes that follow out, so each chain has one form. The steps that find values keep the first of
them (the default), all of them or the best.
"""

# System Imports
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, Self, cast

# Package Imports
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    field_validator,
    model_validator,
)
from selectolax.lexbor import LexborHTMLParser, SelectolaxError

# Local Imports
from udl.core.urls import SiteKey


def _check_regex(pattern: str) -> str:
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"{pattern!r} is not a valid regular expression: {e}") from e
    return pattern


# A valid regular expression. (A plain alias, not a `type` statement: SQLModel reads the fields'
# types to make the SQL plugins' columns, and does not see through `type` aliases.)
Regex = Annotated[str, AfterValidator(_check_regex)]


def _check_css(selector: str) -> str:
    try:
        LexborHTMLParser("").css(selector)
    except SelectolaxError:
        raise ValueError(f"{selector!r} is not a CSS selector udl can use") from None
    return selector


type CssSelector = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1), AfterValidator(_check_css)
]
"""A CSS selector, e.g. `video#player > source[type="video/mp4"]`."""

type AttrName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_:][\w:.-]*$")]
"""An HTML attribute's name, e.g. `src` or `data-video-url`."""


def _check_path(path: str) -> str:
    if any(part == "" for part in path.split(".")):
        raise ValueError(f"the JSON path {path!r} has an empty part (its parts are split by dots)")
    return path


type JsonPath = Annotated[str, AfterValidator(_check_path)]
"""A dotted path into JSON, e.g. `video.sources.0.url`: object keys, list indices, and `*` for
every item of a list (or every value of an object)."""


class Pick(StrEnum):
    """Which of the values a step finds it keeps."""

    FIRST = "first"  # the first (in the document's order)
    ALL = "all"  # every one (a final step's are all downloaded)
    BEST = "best"  # the one that names the highest resolution (e.g. 1080p), else the first


class FetchMode(StrEnum):
    """How a chain reads its pages."""

    STATIC = "static"  # the HTML as served (the scraper)
    BROWSER = "browser"  # rendered in Chromium (the browser), which can click and see requests


# ---------------------------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------------------------


class _Step(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ClassVar[str]  # its key, e.g. "select"
    reads_document: ClassVar[bool] = False  # it works on the document, not on values
    finds_values: ClassVar[bool] = False  # it finds values (URLs, text) for the next step
    browser_only: ClassVar[bool] = False


class SelectStep(_Step):
    """Select elements with a CSS selector, and take an attribute of each (attr) or its text."""

    kind = "select"
    reads_document = True
    finds_values = True

    select: CssSelector
    attr: AttrName | None = None
    text: bool = False
    pick: Pick = Pick.FIRST

    @model_validator(mode="after")
    def _takes_one_thing(self) -> Self:
        if self.attr is not None and self.text:
            raise ValueError("take an attribute (attr) or the text (text: true), not both")
        if self.attr is None and not self.text:
            raise ValueError(
                "say what to take from the elements: an attribute (attr) or the text (text: true)"
            )
        return self


class FollowStep(_Step):
    """Open the URL the step before found (relative to its document), and go on on that
    document."""

    kind = "follow"

    follow: Literal[True]


class RegexStep(_Step):
    """The matches of a regex in each value: its first group, if it has groups, else the whole
    match."""

    kind = "regex"
    finds_values = True

    regex: Regex
    pick: Pick = Pick.FIRST


class JsonStep(_Step):
    """Parse each value as JSON, and take a dotted path in it."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )

    kind = "json"
    finds_values = True

    # (Named `json` by its alias: a field called json would shadow BaseModel.json.)
    path: JsonPath = Field(alias="json")
    pick: Pick = Pick.FIRST


class WaitForStep(_Step):
    """Wait for an element to be on the page (browser only)."""

    kind = "wait_for"
    reads_document = True
    browser_only = True

    wait_for: CssSelector


class ClickStep(_Step):
    """Click an element, e.g. a play button (browser only)."""

    kind = "click"
    reads_document = True
    browser_only = True

    click: CssSelector


class NetworkStep(_Step):
    """The URLs the page has requested (any of its frames) that a regex is found in, waiting for
    one if there is none yet (browser only)."""

    kind = "network"
    reads_document = True
    finds_values = True
    browser_only = True

    network: Regex
    pick: Pick = Pick.FIRST


_STEPS: tuple[type[_Step], ...] = (
    SelectStep,
    FollowStep,
    RegexStep,
    JsonStep,
    WaitForStep,
    ClickStep,
    NetworkStep,
)
_KINDS = tuple(step.kind for step in _STEPS)


def _kind(value: Any) -> str | None:
    """Which step `value` is: a step model's kind, or the first step key a mapping has."""
    if isinstance(value, _Step):
        return value.kind
    if isinstance(value, Mapping):
        return next((kind for kind in _KINDS if kind in value), None)
    return None


type Step = Annotated[
    Annotated[SelectStep, Tag("select")]
    | Annotated[FollowStep, Tag("follow")]
    | Annotated[RegexStep, Tag("regex")]
    | Annotated[JsonStep, Tag("json")]
    | Annotated[WaitForStep, Tag("wait_for")]
    | Annotated[ClickStep, Tag("click")]
    | Annotated[NetworkStep, Tag("network")],
    Discriminator(
        _kind,
        custom_error_type="unknown_step",
        custom_error_message="a step is one of select, follow, regex, json, wait_for, click or "
        "network, named by its key (e.g. {'select': 'video', 'attr': 'src'})",
    ),
]
"""One step of a chain."""


def _parsed(shorthand: str) -> list[dict[str, str | bool]]:
    # (Imported here because the shorthand module imports this one.)
    from udl.chains.shorthand import parse_steps

    return parse_steps(shorthand)


def _needs_browser(steps: Any) -> bool:
    """Whether steps (as given to a chain: a list, or the shorthand) have one that needs the
    browser. Steps that are not valid have none (their validation says what is wrong)."""
    if isinstance(steps, str):
        try:
            steps = _parsed(steps)
        except ValueError:
            return False
    if not isinstance(steps, list):
        return False
    browser = {step.kind for step in _STEPS if step.browser_only}
    return any(_kind(step) in browser for step in cast(list[object], steps))


def _with_follows(steps: list[Step]) -> list[Step]:
    """The steps, checked, with the follow written out before each step that reads the document
    after values."""
    found: list[Step] = []
    values = False  # whether the step before found values (else the chain is on a document)
    for number, step in enumerate(steps, start=1):
        if isinstance(step, FollowStep) and not values:
            raise ValueError(
                f"step {number}: follow opens a URL that the step before it found (a select, "
                "regex, json or network step)"
            )
        if step.reads_document and values:
            found.append(FollowStep(follow=True))
        found.append(step)
        values = step.finds_values
    if not values:
        raise ValueError(
            "the last step must find the media's URLs: end with a select, regex, json or "
            "network step"
        )
    return found


# ---------------------------------------------------------------------------------------------
# Chains
# ---------------------------------------------------------------------------------------------


class TagChain(BaseModel):
    """The steps from a web page to its media, and how its pages are fetched.

    The steps can be given as the chain's one-line shorthand (see udl.chains.shorthand), and so
    can the whole chain. A chain with steps that need the browser runs in it, unless `fetch`
    says otherwise (and is refused).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    fetch: FetchMode = FetchMode.STATIC
    """How its pages are read: static (their HTML as served) or browser (rendered in Chromium,
    which click, wait_for and network steps need). By default static, unless a step needs the
    browser."""
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    """Headers to send with every request, the media's download too. `{url}` in a value is the
    page's URL, e.g. {"Referer": "{url}"}."""
    steps: Annotated[list[Step], Field(min_length=1)]
    """The steps, in order: e.g. [{"select": "iframe#player", "attr": "src"}, {"select": "video
    source", "attr": "src"}], or the same in the one-line shorthand,
    "iframe#player@src >> video source@src"."""
    handoff: Annotated[str, Field(min_length=1)] | None = None
    """The downloader that downloads the media; by default http for a file, and yt-dlp for a
    stream playlist (HLS, DASH)."""

    @model_validator(mode="before")
    @classmethod
    def _from_shorthand(cls, data: Any) -> Any:
        """A whole chain can be given as its shorthand; without `fetch`, a chain whose steps
        need the browser runs in it."""
        if isinstance(data, str):
            data = {"steps": data}
        if not isinstance(data, Mapping):
            return data  # (validation says what is wrong with it)
        fields = cast(Mapping[str, Any], data)
        if "fetch" not in fields and _needs_browser(fields.get("steps")):
            return {**fields, "fetch": FetchMode.BROWSER}
        return fields

    @field_validator("steps", mode="before")
    @classmethod
    def _parse_steps(cls, steps: Any) -> Any:
        return _parsed(steps) if isinstance(steps, str) else steps

    @field_validator("steps", mode="after")
    @classmethod
    def _check_steps(cls, steps: list[Step]) -> list[Step]:
        return _with_follows(steps)

    @model_validator(mode="after")
    def _check_fetch(self) -> Self:
        browser_steps = [step.kind for step in self.steps if step.browser_only]
        if browser_steps and self.fetch is FetchMode.STATIC:
            raise ValueError(
                f"{', '.join(dict.fromkeys(browser_steps))} steps need the browser, but the "
                "chain is static (fetch: static)"
            )
        return self

    @property
    def tool(self) -> str:
        """The downloader that runs the chain: the scraper, or the browser."""
        return "browser" if self.fetch is FetchMode.BROWSER else "scraper"


class SiteChain(TagChain):
    """A site's chain, as a chain file (YAML) holds it: the chain, and the site (and the part of
    it) that it is for."""

    site: SiteKey
    """The site: its key (e.g. example.com), a host or a URL on it."""
    pattern: Regex | None = None
    """Use the chain only for the site's URLs whose path this regex is found in (e.g.
    "^/watch/"); by default for the whole site."""
    priority: int = 0
    """Lower is tried first, among the site's strategies."""
    requires_auth: bool = False
    """Send the site's cookies from the first try."""

    def chain(self) -> TagChain:
        """The chain alone."""
        return TagChain.model_validate(self.model_dump(include=set(TagChain.model_fields)))
