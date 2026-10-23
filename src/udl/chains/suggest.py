"""
--------------
- suggest.py -
--------------

Suggesting tag chains for a page: a chain for each piece of media found on it, so that the user
(or an AI agent) only has to pick one.

The scraper (the page as served) or the browser (the page as rendered, with the media it
requested) fetch the page and its iframes' pages; suggest() looks in them for

- <video> and <audio> elements and their <source>s,
- og:video and og:audio meta tags (not those that are a player's web page),
- media URLs in JSON scripts (JSON-LD too),
- links to media files,
- absolute media URLs in scripts (a chain of one regex, by the media's kind),
- media in its iframes' pages (through the iframe), and the media the page requested (a network
  step, by the media's kind),

and runs each chain on the pages it has, so a suggestion's media is what its chain finds. An
element is selected by a selector that finds it first and that other pages of the site are
likely to share: by its id, a data- attribute or its classes, then inside an ancestor that has
one, then by its type, and only then by its place among its siblings (:nth-of-type).

with_preview() adds what the http downloader's probe of a suggestion's media found (the service
probes them), and ranked() orders them: media that answered as media first, video before audio,
the largest first.
"""

# System Imports
import re
from collections.abc import Iterator, Mapping, Sequence
from typing import Final, Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

# Package Imports
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError
from selectolax.lexbor import LexborHTMLParser, LexborNode, SelectolaxError

# Local Imports
from udl.chains.executor import Document, Reader, run_chain
from udl.chains.models import FetchMode, TagChain
from udl.chains.shorthand import RawStep, format_steps
from udl.errors import DownloadError, ErrorCode, NotFound
from udl.plugins.downloaders.base import MediaCandidate, MediaKind, ProbeContext, ProbeResult
from udl.plugins.downloaders.http import PAGE_TYPES
from udl.plugins.downloaders.media import media_type, stream_kind, url_name

_JSON: Final = TypeAdapter[JsonValue](JsonValue)
_IDENT: Final = re.compile(r"-?[_a-zA-Z][\w-]*")
_GENERATED: Final = re.compile(r"\d{4,}")  # e.g. a database id, which other pages do not share
_TYPE_ATTRS: Final = ("type", "property", "name", "itemprop")
_URL_IN_SCRIPT: Final = re.compile(r"""https?://[^\s"'<>]+""")
_JSON_TYPES: Final = ("application/json", "application/ld+json")

# How likely a find is to be the page's media, by how it was found (lower first).
_FOUND_BY: Final[dict[str, int]] = {
    "<video>": 0,
    "<audio>": 0,
    "og:video": 1,
    "og:audio": 1,
    "json-ld": 2,
    "json": 2,
    "<a>": 3,
    "<script>": 4,
    "network": 5,
}


class ChainSuggestion(BaseModel):
    """A tag chain that leads to media on a page."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    chain: TagChain
    shorthand: str
    """Its steps in the one-line shorthand (as `udl chain set` and set_site_chain take them)."""
    media: MediaCandidate
    """The media it leads to (its type and size, once it has been asked for)."""
    found_on: str
    """The page the media is on (the page itself, or one of its iframes)."""
    reachable: bool | None = None
    """Whether the media answered as media (the http downloader probed it); None when that is
    not known."""


@runtime_checkable
class ChainSuggester(Protocol):
    """A downloader that suggests chains for a page (the scraper, the browser)."""

    async def suggest_chains(self, ctx: ProbeContext) -> list[ChainSuggestion]:
        """Chains to the media on ctx.url's page (see suggest()). Raises a DownloadError when
        the page cannot be read, or Cancelled."""
        ...


def _is_media(url: str) -> bool:
    kind = media_type(url) or ""
    return kind.startswith(("video/", "audio/")) or stream_kind(url) is not None


# ---------------------------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------------------------


def _stable(name: str) -> bool:
    return bool(_IDENT.fullmatch(name)) and not _GENERATED.search(name)


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _own(node: LexborNode) -> list[str]:
    """Selectors for an element by what is its own: its id, data- attributes and classes."""
    tag, attrs = node.tag or "", node.attributes
    found: list[str] = []
    if (id_ := attrs.get("id")) and _stable(id_):
        found.append(f"{tag}#{id_}")
    found += [f"{tag}[{name}]" for name in attrs if name.startswith("data-") and _stable(name)]
    classes = [name for name in (attrs.get("class") or "").split() if _stable(name)]
    if classes:
        found.append(tag + "".join(f".{name}" for name in classes))
    if len(classes) > 1:
        found += [f"{tag}.{name}" for name in classes]
    return found


def _typed(node: LexborNode) -> list[str]:
    """Selectors for an element by its tag, then its type-like attributes (a <source>'s type, a
    <meta>'s property...)."""
    tag, attrs = node.tag or "", node.attributes
    return [
        tag,
        *(f"{tag}[{name}={_quoted(value)}]" for name in _TYPE_ATTRS if (value := attrs.get(name))),
    ]


def _ancestors(node: LexborNode) -> Iterator[LexborNode]:
    parent = node.parent
    while parent is not None and parent.is_element_node and parent.tag not in ("html", "body"):
        yield parent
        parent = parent.parent


def _place(node: LexborNode) -> str:
    """An element by its tag and, among its siblings with the same tag, its place."""
    siblings = [] if node.parent is None else list(node.parent.iter())
    same = [sibling.mem_id for sibling in siblings if sibling.tag == node.tag]
    if len(same) < 2:
        return str(node.tag)
    return f"{node.tag}:nth-of-type({same.index(node.mem_id) + 1})"


def selector_for(node: LexborNode, page: LexborHTMLParser) -> str:
    """A selector whose first match on `page` is `node`, as stable as can be (see the module
    docs). A <source> is selected in its media element."""

    def first(selector: str) -> bool:
        try:
            found = page.css_first(selector)
        except SelectolaxError:  # (e.g. an id that is not a CSS identifier)
            return False
        # (Lexbor's == compares elements' markup: two alike elements are equal.)
        return found is not None and found.mem_id == node.mem_id

    parent = node.parent
    if node.tag == "source" and parent is not None and parent.tag in ("video", "audio"):
        within = selector_for(parent, page)
        for part in [*_typed(node), _place(node)]:
            if first(selector := f"{within} > {part}"):
                return selector
    for selector in _own(node):
        if first(selector):
            return selector
    for ancestor in _ancestors(node):
        for anchor in _own(ancestor):
            for own in [*_typed(node), *_own(node)]:
                if first(selector := f"{anchor} {own}"):
                    return selector
    for selector in _typed(node):
        if first(selector):
            return selector
    top, parts = node, [_place(node)]
    for ancestor in _ancestors(node):
        if first(selector := " > ".join(reversed(parts))):
            return selector
        top = ancestor
        parts.append(_place(ancestor))
    selector = " > ".join(reversed(parts))
    if first(selector) or top.parent is None:
        return selector
    return f"{top.parent.tag} > {selector}"


# ---------------------------------------------------------------------------------------------
# Finding media on a page
# ---------------------------------------------------------------------------------------------


class _Spot(BaseModel):
    """Media found on a page, and the steps that find it."""

    model_config = ConfigDict(frozen=True)

    steps: list[RawStep]
    url: str
    found_by: str


def _value(node: LexborNode, attr: str) -> str:
    return (node.attributes.get(attr) or "").strip()


def _json_paths(data: JsonValue, path: tuple[str, ...] = ()) -> Iterator[tuple[str, str]]:
    """The (dotted path, value) of every text in JSON that is a media file's or a stream's URL."""
    if isinstance(data, str):
        if path and _is_media(data):
            yield ".".join(path), data
    elif isinstance(data, dict):
        for key, value in data.items():
            if key and "." not in key and key != "*":
                yield from _json_paths(value, (*path, key))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            yield from _json_paths(value, (*path, str(index)))


def _script_pattern(url: str) -> str:
    """A regex that finds absolute URLs of the same kind (extension) as `url` in a page."""
    extension = re.escape(url_name(url).rpartition(".")[2])
    return rf"""https?:[^\s"'<>]+?\.{extension}(?:\?[^\s"'<>]*)?"""


class _Finder:
    """Finds the media on a page, and the steps to each."""

    def __init__(self, page: LexborHTMLParser, url: str) -> None:
        self.page = page
        base = page.css_first("base[href]")
        self.base = urljoin(url, (base.attributes.get("href") if base else None) or "")

    def spot(self, node: LexborNode, attr: str, url: str, found_by: str) -> _Spot:
        """The media at `url`, the value of an element's attribute."""
        steps: list[RawStep] = [{"select": selector_for(node, self.page), "attr": attr}]
        return _Spot(steps=steps, url=urljoin(self.base, url), found_by=found_by)

    def spots(self) -> Iterator[_Spot]:
        page = self.page
        for node in page.css("video[src], audio[src], video source[src], audio source[src]"):
            media = (node.parent or node) if node.tag == "source" else node
            if src := _value(node, "src"):
                yield self.spot(node, "src", src, f"<{media.tag}>")
        yield from self.open_graph()
        yield from self.json()
        for node in page.css("a[href]"):
            if (href := _value(node, "href")) and _is_media(urljoin(self.base, href)):
                yield self.spot(node, "href", href, "<a>")
        for node in page.css("script:not([src])"):
            if node.attributes.get("type") in _JSON_TYPES:  # (their media is found as JSON)
                continue
            for match in _URL_IN_SCRIPT.finditer(node.text().replace("\\/", "/")):
                if _is_media(url := match.group().rstrip(").,;")):
                    steps: list[RawStep] = [{"regex": _script_pattern(url)}]
                    yield _Spot(steps=steps, url=url, found_by="<script>")

    def open_graph(self) -> Iterator[_Spot]:
        """og:video and og:audio, except those whose og:…:type says they are a web page (a
        player)."""
        found: list[tuple[_Spot, str]] = []  # (the spot, its type)
        for node in self.page.css('meta[property^="og:video"], meta[property^="og:audio"]'):
            prop = node.attributes.get("property") or ""
            medium = prop.removeprefix("og:").partition(":")[0]
            content = _value(node, "content")
            if prop in (f"og:{medium}", f"og:{medium}:url", f"og:{medium}:secure_url"):
                if content:
                    found.append((self.spot(node, "content", content, f"og:{medium}"), ""))
            elif prop == f"og:{medium}:type" and found:
                found[-1] = (found[-1][0], node.attributes.get("content") or "")
        for made, kind in found:
            if kind.partition(";")[0].strip().lower() not in PAGE_TYPES:
                yield made

    def json(self) -> Iterator[_Spot]:
        """Media URLs in JSON scripts (JSON-LD too)."""
        scripts = ", ".join(f'script[type="{kind}"]' for kind in _JSON_TYPES)
        for node in self.page.css(scripts):
            try:
                data = _JSON.validate_json(node.text())
            except ValidationError:
                continue
            ld = node.attributes.get("type") == "application/ld+json"
            selector = selector_for(node, self.page)
            for path, value in _json_paths(data):
                steps: list[RawStep] = [{"select": selector, "text": True}, {"json": path}]
                url = urljoin(self.base, value)
                yield _Spot(steps=steps, url=url, found_by="json-ld" if ld else "json")


def _iframes(page: LexborHTMLParser, url: str) -> Iterator[tuple[RawStep, str]]:
    """The step that selects each iframe's URL, and that URL."""
    base = _Finder(page, url).base
    for node in page.css("iframe[src]"):
        if src := _value(node, "src"):
            yield {"select": selector_for(node, page), "attr": "src"}, urljoin(base, src)


def _requested_pattern(url: str) -> str:
    """A regex that finds requests for media of the same kind (extension) as `url`, or for its
    path when it has no extension."""
    name = url_name(url)
    if "." in name:
        return rf"\.{re.escape(name.rpartition('.')[2])}\b"
    return re.escape(urlsplit(url).path)


# ---------------------------------------------------------------------------------------------
# Suggesting
# ---------------------------------------------------------------------------------------------


class FetchedPages(Reader):
    """Opens pages that were fetched already (by the URLs they were opened at): to check a
    chain on them."""

    def __init__(self, pages: Mapping[str, Document]) -> None:
        self._pages = pages

    async def open(self, url: str, *, referer: str | None) -> Document:
        if url not in self._pages:
            raise NotFound(f"{url} was not fetched")
        return self._pages[url]


async def suggest(
    url: str,
    pages: Mapping[str, Document],
    *,
    fetch: FetchMode,
    requested: Sequence[str] = (),
) -> list[ChainSuggestion]:
    """Chains that lead to the media on the page at `url`: found in `pages` (the page, and its
    iframes' pages, by the URLs they were opened at) and in the media it `requested`. Each chain
    is checked on `pages`, and suggested once (see the module docs)."""
    page = pages[url]
    tree = LexborHTMLParser(await page.text())
    spots = [(spot, page.url) for spot in _Finder(tree, page.url).spots()]
    for step, frame_url in _iframes(tree, page.url):
        if (frame := pages.get(frame_url)) is not None:
            for spot in _Finder(LexborHTMLParser(await frame.text()), frame.url).spots():
                inside = spot.model_copy(
                    update={
                        "steps": [step, *spot.steps],
                        "found_by": f"{spot.found_by} in <iframe>",
                    }
                )
                spots.append((inside, frame.url))
    suggestions: dict[str, ChainSuggestion] = {}
    for spot, found_on in spots:
        if (made := await _checked(spot, found_on, url, pages, fetch)) is not None:
            suggestions.setdefault(made.shorthand, made)
    for media_url in requested:
        steps = [{"network": _requested_pattern(media_url)}]
        chain = TagChain.model_validate({"fetch": FetchMode.BROWSER, "steps": steps})
        made = _suggestion(chain, media_url, "network", url)
        suggestions.setdefault(made.shorthand, made)
    return list(suggestions.values())


def _suggestion(chain: TagChain, url: str, found_by: str, found_on: str) -> ChainSuggestion:
    kind = stream_kind(url) or MediaKind.FILE
    return ChainSuggestion(
        chain=chain,
        shorthand=format_steps(chain.steps),
        media=MediaCandidate(url=url, kind=kind, found_by=found_by),
        found_on=found_on,
    )


async def _checked(
    spot: _Spot, found_on: str, url: str, pages: Mapping[str, Document], fetch: FetchMode
) -> ChainSuggestion | None:
    """The spot's suggestion, with the media its chain finds (None when it finds none)."""
    try:
        chain = TagChain.model_validate({"fetch": fetch, "steps": spot.steps})
        run = await run_chain(chain, url, FetchedPages(pages))
    except ValidationError, DownloadError:
        return None
    first = run.finds[0]
    return _suggestion(chain, first.media.url, spot.found_by, first.referer)


# ---------------------------------------------------------------------------------------------
# What the media's server says, and ranking
# ---------------------------------------------------------------------------------------------


def preview_media(media: MediaCandidate, probe: ProbeResult) -> MediaCandidate:
    """Media with the type and size that the http downloader's probe of it found (if it
    did)."""
    if not probe.ok:
        return media
    found = probe.media[0]
    return media.model_copy(
        update={"content_type": found.content_type, "size_bytes": found.size_bytes}
    )


def with_preview(suggestion: ChainSuggestion, probe: ProbeResult) -> ChainSuggestion:
    """A suggestion with what the http downloader's probe of its media (from the page it is
    on) found: its type and size, and whether it answered as media. A stream playlist, which
    http leaves to yt-dlp, stays unknown."""
    if probe.ok:
        media = preview_media(suggestion.media, probe)
        return suggestion.model_copy(update={"media": media, "reachable": True})
    stream = suggestion.media.kind is not MediaKind.FILE
    unsupported = probe.error is not None and probe.error.code is ErrorCode.UNSUPPORTED_URL
    return suggestion.model_copy(update={"reachable": None if stream and unsupported else False})


def _type_rank(media: MediaCandidate) -> int:
    kind = (media.content_type or media_type(media.url) or "").partition(";")[0].strip().lower()
    if (
        media.kind is not MediaKind.FILE
        or stream_kind(media.url, kind)
        or kind.startswith("video/")
    ):
        return 0
    return 1 if kind.startswith("audio/") else 2


def ranked(suggestions: Sequence[ChainSuggestion]) -> list[ChainSuggestion]:
    """The suggestions, best first: media that answered as media (then media not asked for),
    video (and streams) before audio, the largest, then by how they were found, in order."""

    def rank(suggestion: ChainSuggestion) -> tuple[int, int, float, int]:
        media = suggestion.media
        answered = {True: 0, None: 1, False: 2}[suggestion.reachable]
        size = -media.size_bytes if media.size_bytes is not None else float("inf")
        found_by = (media.found_by or "").partition(" in ")[0]
        return (answered, _type_rank(media), size, _FOUND_BY.get(found_by, len(_FOUND_BY)))

    return sorted(suggestions, key=rank)
