"""
---------------
- executor.py -
---------------

Running a tag chain: from a page, step by step, to the URLs of its media.

A chain reads its documents through a Reader: the scraper's reads them over HTTP, as they are
served; the browser's opens them in Chromium, which can also wait for elements, click them and
see the requests a page makes. Each step works on what the step before it left: documents (the
page, or the pages a follow opened) or values (what a select, regex, json or network step found):

- a select reads each document's elements (in a browser, as the page is now);
- a follow opens each value as a URL (relative to its document), MAX_FOLLOWS at most;
- regex and json work on each value, or on the document's text when there is none;
- the last step's values are the media: http(s) URLs, relative ones resolved against their
  document (JSON's `\\/` and HTML's `&amp;` undone), each once.

A step that finds nothing ends the run with ExtractionFailed, which names the step. A follow
whose page is not found is skipped when another one opens; any other failure to open a page (a
login, a rate limit...) is the run's error, as is the first page's.
"""

# System Imports
import dataclasses
import html
import logging
import re
import shlex
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from typing import Final, Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

# Package Imports
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from selectolax.lexbor import LexborHTMLParser, LexborNode

# Local Imports
from udl.chains.models import (
    FollowStep,
    JsonStep,
    NetworkStep,
    Pick,
    RegexStep,
    SelectStep,
    Step,
    StepTrace,
    TagChain,
    WaitForStep,
)
from udl.chains.shorthand import command_arguments, format_step
from udl.errors import DownloadError, ExtractionFailed, NotFound, UnsupportedURL
from udl.plugins.downloaders.base import (
    Cookie,
    Handoff,
    MediaCandidate,
    MediaKind,
    ProbeContext,
    ProbeResult,
)
from udl.plugins.downloaders.media import Find, page_title, stream_kind, unique

log = logging.getLogger(__name__)

MAX_FOLLOWS: Final = 10
"""The most documents one follow step opens."""
_TRACED: Final = 10  # values a trace shows of a step
_CONFIDENCE: Final = 0.9  # how sure a chain is of its finds: it was written for the site
_FOUND_BY: Final = "tag chain"

# The attributes (and their data- forms) that may name a media element's resolution.
_RESOLUTION_ATTRS: Final = frozenset({"res", "size", "label", "quality", "height", "title"})
_P_RESOLUTION: Final = re.compile(r"(?<!\d)(\d{3,4})p(?![a-z])", re.IGNORECASE)  # e.g. 720p
_UNICODE_ESCAPE: Final = re.compile(r"\\u([0-9a-fA-F]{4})")
_JSON: Final = TypeAdapter[JsonValue](JsonValue)


# ---------------------------------------------------------------------------------------------
# Documents, and where they come from
# ---------------------------------------------------------------------------------------------


def _needs_browser(step: str) -> UnsupportedURL:
    return UnsupportedURL(
        f"A {step} step needs the browser", hint="Run the chain in the browser (fetch: browser)."
    )


class Document(ABC):
    """A document a chain is on: its page, or a page a follow opened."""

    def __init__(self, url: str) -> None:
        self.url = url  # where it is (after any redirects)

    @abstractmethod
    async def text(self) -> str:
        """Its text: a web page's HTML (in a browser, the page as it is now), else its body."""

    async def wait_for(self, selector: str) -> None:
        """Wait for the page to have an element the selector selects (browser only)."""
        raise _needs_browser("wait_for")

    async def click(self, selector: str) -> None:
        """Click the first element the selector selects (browser only)."""
        raise _needs_browser("click")

    async def requested(self, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
        """The URLs the page has requested that `pattern` is found in, each with the page that
        requested it (its Referer), waiting for one if there is none yet (browser only)."""
        raise _needs_browser("network")


class StaticDocument(Document):
    """A document as it was served."""

    def __init__(self, url: str, text: str) -> None:
        super().__init__(url)
        self._text = text

    async def text(self) -> str:
        return self._text


class Reader(ABC):
    """Opens the documents a chain reads."""

    @abstractmethod
    async def open(self, url: str, *, referer: str | None) -> Document:
        """The document at `url`, opened from `referer` (None for the chain's page). Raises a
        DownloadError (e.g. NotFound, AuthRequired) when it cannot be opened."""


class ChainRun(BaseModel):
    """Where a chain led: the page's title, and its media."""

    model_config = ConfigDict(frozen=True)

    title: str | None
    finds: list[Find]  # in the order they were found
    every: bool  # download every find (the last step keeps all), not just the first that works


@runtime_checkable
class ChainRunner(Protocol):
    """A downloader that follows the tag chain in its options (the scraper, the browser)."""

    async def run_chain(
        self, ctx: ProbeContext, *, trace: list[StepTrace] | None = None
    ) -> ChainRun:
        """Follow the chain from ctx.url, adding what each step found to `trace`. Raises a
        DownloadError (ExtractionFailed when a step finds nothing), or Cancelled."""
        ...


# ---------------------------------------------------------------------------------------------
# What the steps work on
# ---------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Item:
    """A document the chain is on, or a value a step found on one."""

    document: Document
    base: str  # what a relative URL in the value is relative to
    referer: str  # where the value was found: its media is downloaded as if from there
    value: str | None = None  # None: the chain is on the document itself
    hints: tuple[str, ...] = ()  # what may name the value's resolution (its element's attributes)

    def found(self, value: str, hints: tuple[str, ...] = ()) -> _Item:
        return dataclasses.replace(self, value=value, hints=hints)

    def on_document(self) -> _Item:
        return dataclasses.replace(self, value=None, hints=())

    async def text(self) -> str:
        """The value, or the document's text when there is none."""
        return self.value if self.value is not None else await self.document.text()


def _resolution(text: str, *, plain: bool = False) -> int:
    """The resolution a text names: e.g. 720 in "720p" (or in "720", when `plain`)."""
    found = [int(number) for number in _P_RESOLUTION.findall(text)]
    if plain and text.strip().isdigit():
        found.append(int(text))
    return max(found, default=0)


def _quality(item: _Item) -> int:
    return max(
        [_resolution(item.value or ""), *(_resolution(hint, plain=True) for hint in item.hints)]
    )


def _picked(items: list[_Item], pick: Pick) -> list[_Item]:
    if pick is Pick.ALL or not items:
        return items
    if pick is Pick.FIRST:
        return items[:1]
    return [max(items, key=_quality)]  # (the first of the best)


def _url(value: str, base: str) -> str | None:
    """`value` as an absolute http(s) URL (relative to `base`), or None when it is not one."""
    text = value.strip().replace("\\/", "/")
    text = html.unescape(_UNICODE_ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), text))
    if not text or any(char.isspace() for char in text):
        return None
    try:
        url = urljoin(base, text)
        parts = urlsplit(url)
        return url if parts.scheme in ("http", "https") and parts.hostname else None
    except ValueError:  # (e.g. a broken IPv6 address)
        return None


# ---------------------------------------------------------------------------------------------
# The steps
# ---------------------------------------------------------------------------------------------


def _hints(node: LexborNode) -> tuple[str, ...]:
    return tuple(
        value
        for name, value in node.attributes.items()
        if value and name.removeprefix("data-") in _RESOLUTION_ATTRS
    )


async def _select(step: SelectStep, item: _Item) -> list[_Item]:
    page = LexborHTMLParser(await item.document.text())
    base = page.css_first("base[href]")
    href = base.attributes.get("href") if base else None
    on = dataclasses.replace(item, base=urljoin(item.document.url, href or ""))
    found: list[_Item] = []
    for node in page.css(step.select):
        value = node.attributes.get(step.attr) if step.attr else node.text(strip=True)
        if value and value.strip():
            found.append(on.found(value.strip(), _hints(node)))
    return _picked(found, step.pick)


async def _follow(items: list[_Item], reader: Reader) -> list[_Item]:
    targets: dict[str, str] = {}  # URL -> the document it was found on
    for item in items:
        if (url := _url(item.value or "", item.base)) is not None:
            targets.setdefault(url, item.document.url)
    opened: list[_Item] = []
    failures: list[DownloadError] = []
    for url, referer in list(targets.items())[:MAX_FOLLOWS]:
        try:
            document = await reader.open(url, referer=referer)
        except (NotFound, UnsupportedURL, ExtractionFailed) as e:
            log.info("Could not follow %s (%s)", url, e)
            failures.append(e)
            continue
        opened.append(_Item(document, base=document.url, referer=document.url))
    if failures and not opened:
        raise failures[0]
    return opened


async def _regex(step: RegexStep, item: _Item) -> list[_Item]:
    pattern = re.compile(step.regex)
    matches = (
        match.group(1) if pattern.groups else match.group()
        for match in pattern.finditer(await item.text())
    )
    found = [item.found(value.strip()) for value in matches if value and value.strip()]
    return _picked(found, step.pick)


def _walk(data: JsonValue, path: list[str]) -> Iterator[JsonValue]:
    """The values at `path` in `data` (`*` takes every item, or every value)."""
    if not path:
        yield data
        return
    part, rest = path[0], path[1:]
    children: list[JsonValue] = []
    if isinstance(data, dict):
        children = list(data.values()) if part == "*" else [data[part]] if part in data else []
    elif isinstance(data, list):
        if part == "*":
            children = data
        elif part.isdigit() and int(part) < len(data):
            children = [data[int(part)]]
    for child in children:
        yield from _walk(child, rest)


def _json_text(value: JsonValue) -> str | None:
    """A JSON value as a step's value: text as it is, anything else as JSON (for a next json
    step); None for null."""
    if value is None:
        return None
    return value if isinstance(value, str) else _JSON.dump_json(value).decode()


async def _json(step: JsonStep, item: _Item) -> list[_Item]:
    try:
        data = _JSON.validate_json(await item.text())
    except ValidationError:
        return []
    values = (_json_text(value) for value in _walk(data, step.path.split(".")))
    return _picked([item.found(value) for value in values if value], step.pick)


async def _network(step: NetworkStep, item: _Item) -> list[_Item]:
    requested = await item.document.requested(re.compile(step.network))
    found = [
        _Item(item.document, base=url, referer=referer, value=url) for url, referer in requested
    ]
    return _picked(found, step.pick)


async def _step(step: Step, items: list[_Item], reader: Reader) -> list[_Item]:
    """What one step finds on the items the step before it left."""
    if isinstance(step, FollowStep):
        return await _follow(items, reader)
    found: list[_Item] = []
    for item in items:
        if isinstance(step, SelectStep):
            found += await _select(step, item)
        elif isinstance(step, RegexStep):
            found += await _regex(step, item)
        elif isinstance(step, JsonStep):
            found += await _json(step, item)
        elif isinstance(step, NetworkStep):
            found += await _network(step, item)
        elif isinstance(step, WaitForStep):
            await item.document.wait_for(step.wait_for)
            found.append(item.on_document())
        else:  # (a click)
            await item.document.click(step.click)
            found.append(item.on_document())
    return found


# ---------------------------------------------------------------------------------------------
# Running a chain
# ---------------------------------------------------------------------------------------------


def _documents(items: list[_Item]) -> str:
    urls = list(dict.fromkeys(item.document.url for item in items))
    shown = ", ".join(urls[:3])
    return shown + (f" and {len(urls) - 3} more" if len(urls) > 3 else "")


async def run_chain(
    chain: TagChain, url: str, reader: Reader, *, trace: list[StepTrace] | None = None
) -> ChainRun:
    """Follow `chain` from the page at `url`, reading its documents with `reader`. What each
    step found is added to `trace`, as it runs. Raises ExtractionFailed when a step finds
    nothing (or the last one finds no URLs), and the reader's errors."""
    page = await reader.open(url, referer=None)
    title = page_title(LexborHTMLParser(await page.text()))
    items = [_Item(page, base=page.url, referer=page.url)]
    for number, step in enumerate(chain.steps, start=1):
        before, items = items, await _step(step, items, reader)
        if trace is not None:
            found = [item.value or item.document.url for item in items]
            trace.append(StepTrace(step=format_step(step), found=found[:_TRACED], count=len(found)))
        if not items:
            raise ExtractionFailed(
                f"The tag chain's step {number} ({format_step(step)}) found nothing on "
                f"{_documents(before)}",
                hint=_hint(chain, url),
            )
    finds = [find for item in items if (find := _find(item)) is not None]
    if not finds:
        ended = ", ".join(repr(item.value) for item in items[:3])
        raise ExtractionFailed(
            f"The tag chain found no URLs on {url}: its last step found {ended}",
            hint=_hint(chain, url),
        )
    last = chain.steps[-1]
    every = isinstance(last, SelectStep | RegexStep | JsonStep | NetworkStep) and (
        last.pick is Pick.ALL
    )
    return ChainRun(title=title, finds=unique(finds), every=every)


def _find(item: _Item) -> Find | None:
    url = _url(item.value or "", item.base)
    if url is None:
        return None
    media = MediaCandidate(url=url, kind=stream_kind(url) or MediaKind.FILE, found_by=_FOUND_BY)
    return Find(media=media, confidence=_CONFIDENCE, referer=item.referer)


# ---------------------------------------------------------------------------------------------
# What a run leads to
# ---------------------------------------------------------------------------------------------


def chain_headers(chain: TagChain, url: str) -> dict[str, str]:
    """The headers a chain sends, with `{url}` in them the page's URL."""
    return {name: value.replace("{url}", url) for name, value in chain.headers.items()}


def _over(headers: Mapping[str, str], over: Mapping[str, str]) -> dict[str, str]:
    """`headers` with `over`'s, whatever their case."""
    replaced = {name.lower() for name in over}
    kept = {name: value for name, value in headers.items() if name.lower() not in replaced}
    return kept | dict(over)


def chain_handoffs(
    chain: TagChain,
    run: ChainRun,
    *,
    url: str,
    cookies: list[Cookie],
    headers: Mapping[str, str] | None = None,
) -> list[Handoff]:
    """The downloads of a run's finds (each with `headers`, and the Referer of the page it was
    found on), by the chain's handoff, with the chain's headers over those."""
    sent = chain_headers(chain, url)
    handoffs = [
        find.handoff(cookies=cookies, headers=headers, tool=chain.handoff) for find in run.finds
    ]
    return [
        handoff.model_copy(update={"headers": _over(handoff.headers, sent)}) for handoff in handoffs
    ]


def probe_result(tool: str, run: ChainRun, handoffs: list[Handoff]) -> ProbeResult:
    """A downloader's probe result for where a chain led."""
    return ProbeResult(
        tool=tool,
        ok=True,
        confidence=run.finds[0].confidence,
        title=run.title,
        media=[find.media for find in run.finds],
        handoff=handoffs[0],
    )


def _hint(chain: TagChain, url: str) -> str:
    page = shlex.quote(url)
    return (
        f"The site may have changed: `udl chain test {page} {command_arguments(chain)}` shows "
        f"what each step finds, and `udl chain suggest {page}` suggests new chains."
    )
