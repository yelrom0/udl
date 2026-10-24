"""
--------------
- content.py -
--------------

A fetched page (FetchedPage: what a rung of the ladder got, its body as bytes) and what it reads
as (PageText.render()): its main content as Markdown, or as text, or its HTML, with links and
images made absolute; or only what a CSS selector matches. JSON, XML and plain text are given as
they are; anything else (a PDF, an image) is not converted, only described.

The main content is trafilatura's (the article, without the menus, sidebars and footers around
it); the whole page, or what `select` matches, is converted whole, by markdownify. Both stay
behind PageText, so either can be replaced. A page's charset is its Content-Type's, else its
<meta>'s, else a guess.
"""

# System Imports
import codecs
import re
from enum import StrEnum
from typing import Final
from urllib.parse import urljoin

# Package Imports
import charset_normalizer
import markdownify
import trafilatura
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt
from selectolax.lexbor import LexborHTMLParser, LexborNode, SelectolaxError
from trafilatura.settings import use_config

# Local Imports
from udl.errors import ConfigError

# The headers a page keeps (the others are the server's business).
KEPT_HEADERS: Final = ("content-type", "content-language", "last-modified", "etag")
_HTML_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})
_AS_IS_TYPES: Final = frozenset({"application/json", "application/xml", "application/javascript"})
_META_CHARSET: Final = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([\w.:-]+)""", re.IGNORECASE)
_META_SNIFF: Final = 4096  # (where a <meta> charset must be, as browsers look for it)
_DROPPED: Final = "script, style, noscript, template"
_CELLS: Final = frozenset({"td", "th"})
# trafilatura gives up on what it finds too short to be an article: a fragment is what it is.
_TRAFILATURA: Final = use_config()
_TRAFILATURA.set("DEFAULT", "MIN_EXTRACTED_SIZE", "1")
_TRAFILATURA.set("DEFAULT", "MIN_OUTPUT_SIZE", "1")
_TRAFILATURA.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")  # (its timeout uses signals: no threads)


class PageFormat(StrEnum):
    """What a page is given as."""

    MARKDOWN = "markdown"  # its main content (or the whole page, or what select matches)
    TEXT = "text"  # the same, as plain text
    HTML = "html"  # its HTML, as served or as the browser rendered it (or what select matches)
    RAW = "raw"  # as it was served, unconverted (the CLI's --out keeps its bytes)


class FetchedPage(BaseModel):
    """A page a rung of the ladder got."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str  # the URL asked for
    final_url: str  # where it was, after redirects
    status: int
    content_type: str | None = None  # its media type, e.g. text/html
    charset: str | None = None  # the charset it says it is in, if it says
    title: str | None = None
    headers: dict[str, str] = Field(default_factory=dict[str, str])  # KEPT_HEADERS', lower case
    via: str = "static"  # the rung that got it
    body: bytes = b""
    truncated: bool = False  # its body was cut at [fetch] max_page

    @property
    def is_html(self) -> bool:
        return self.content_type in _HTML_TYPES

    @property
    def is_text(self) -> bool:
        """Whether it is text of some kind (HTML, JSON, XML, plain text...), not a file."""
        kind = self.content_type or ""
        return (
            kind in _HTML_TYPES
            or kind in _AS_IS_TYPES
            or kind.startswith("text/")
            or kind.endswith(("+json", "+xml"))
        )

    def text(self) -> str:
        """Its body, decoded (by its charset, its <meta>'s, else a guess)."""
        return decode(self.body, self.charset, html=self.is_html)[0]


class RenderedPage(BaseModel):
    """A page as it reads in a format."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: PageFormat
    content: str | None  # None for what is not converted (a PDF, an image)
    title: str | None
    media_type: str | None
    size: NonNegativeInt  # its body's bytes
    note: str | None = None  # why it is not converted, or what select found


# ---------------------------------------------------------------------------------------------
# Charsets
# ---------------------------------------------------------------------------------------------


def media_type(content_type: str | None) -> tuple[str | None, str | None]:
    """A Content-Type header as its media type (lower case) and its charset, if it says one."""
    if not content_type:
        return None, None
    kind, *parameters = content_type.split(";")
    charset = None
    for parameter in parameters:
        name, _, value = parameter.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip("\"'")
    return kind.strip().lower() or None, charset


def _known(charset: str | None) -> str | None:
    if charset is None:
        return None
    try:
        return codecs.lookup(charset).name
    except LookupError:
        return None


def decode(body: bytes, charset: str | None, *, html: bool) -> tuple[str, str]:
    """`body` as text, and the charset it was read as: `charset` (a Content-Type's), else (for
    HTML) its <meta>'s, else UTF-8 when it is that, else windows-1252 for HTML (as browsers read
    a page that says nothing) and charset_normalizer's guess for other text."""
    found = _known(charset)
    if found is None and html and (meta := _META_CHARSET.search(body[:_META_SNIFF])):
        found = _known(meta.group(1).decode("ascii", "replace"))
    if found is None:
        try:
            return body.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            if html:
                found = "cp1252"
            else:
                guess = charset_normalizer.from_bytes(body).best()
                found = _known(guess.encoding) if guess is not None else None
    found = found or "utf-8"
    return body.decode(found, "replace"), found


def html_title(html: str) -> str | None:
    """A page's <title>, if it has one."""
    node = LexborHTMLParser(html).css_first("title")
    title = " ".join(node.text().split()) if node is not None else ""
    return title or None


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------


class PageText:
    """What a page reads as, in each format (the converters stay behind it)."""

    @classmethod
    def render(
        cls,
        page: FetchedPage,
        format: PageFormat,  # noqa: A002 - (as the requests name it)
        *,
        main_only: bool = True,
        select: str | None = None,
    ) -> RenderedPage:
        """`page` in `format`: its main content (unless `main_only` is false: the whole page),
        or only what the CSS selector `select` matches, converted whole. Raises ConfigError for
        a selector that is not one."""
        shown = RenderedPage(
            format=format,
            content=None,
            title=page.title,
            media_type=page.content_type,
            size=len(page.body),
        )
        if not page.is_text:
            what = page.content_type or "a file of an unknown type"
            return shown.model_copy(
                update={"note": f"{what}, {len(page.body)} bytes: not converted to text"}
            )
        text = page.text()
        if format is PageFormat.RAW or not page.is_html:
            return shown.model_copy(update={"content": text})
        title = page.title or html_title(text)
        parsed = LexborHTMLParser(text)
        base = _base(parsed, page.final_url)
        if select is not None:
            found = _selected(parsed, select)
            note = None if found else f"Nothing on the page matches {select!r}"
            content = cls._convert(found, format, base)
            return shown.model_copy(update={"content": content, "title": title, "note": note})
        if format is PageFormat.HTML:
            return shown.model_copy(update={"content": text, "title": title})
        content = _main(_framed(parsed, text), format, base) if main_only else None
        if content is None:  # (nothing found that reads as an article: the whole page)
            body = parsed.body
            content = cls._convert([body] if body is not None else [], format, base)
        return shown.model_copy(update={"content": content, "title": title})

    @staticmethod
    def _convert(nodes: list[LexborNode], kind: PageFormat, base: str) -> str:
        """`nodes` (each converted whole) as `kind`, a blank line between them."""
        for node in nodes:
            for dropped in node.css(_DROPPED):
                dropped.decompose()
            _absolute(node, base)
        if kind is PageFormat.HTML:
            return "\n".join(node.html or "" for node in nodes)
        # (A table's cell alone is what is in it: as a cell, it would be a row's piece.)
        parts = [
            _markdown((node.inner_html if node.tag in _CELLS else node.html) or "")
            for node in nodes
        ]
        if kind is PageFormat.TEXT:
            parts = [_plain(part) for part in parts]
        return "\n\n".join(part for part in parts if part)


def _base(parsed: LexborHTMLParser, url: str) -> str:
    """What the page's links are relative to: its <base href>, else its URL."""
    node = parsed.css_first("base[href]")
    href = node.attributes.get("href") if node is not None else None
    return urljoin(url, href) if href else url


def _selected(parsed: LexborHTMLParser, select: str) -> list[LexborNode]:
    try:
        return parsed.css(select)
    except SelectolaxError as e:
        raise ConfigError(f"{select!r} is not a CSS selector: {e}") from e


def _absolute(node: LexborNode, base: str) -> None:
    """Make the links and images in (and of) `node` absolute against `base`."""
    for found in [node, *node.css("[href], [src]")]:
        for name in ("href", "src"):
            value = found.attributes.get(name)
            if value and not value.startswith(("#", "data:", "javascript:")):
                found.attrs[name] = urljoin(base, value)


def _framed(parsed: LexborHTMLParser, html: str) -> str:
    """The page, its body in a <main> when it has no main content of its own marked (a <main>,
    an <article>, role=main): trafilatura finds a page's headings only in such a frame (its menus
    and footers it leaves out anyway)."""
    body = parsed.body
    if body is None or parsed.css_first("main, article, [role=main]") is not None:
        return html
    head = parsed.head.html if parsed.head is not None else ""
    return f"<html>{head}<body><main>{body.inner_html or ''}</main></body></html>"


def _main(html: str, kind: PageFormat, base: str) -> str | None:
    """The page's main content (trafilatura's), or None when it finds none."""
    found = trafilatura.extract(
        html,
        url=base,
        output_format="markdown" if kind is PageFormat.MARKDOWN else "txt",
        include_links=kind is PageFormat.MARKDOWN,
        include_images=kind is PageFormat.MARKDOWN,
        include_formatting=kind is PageFormat.MARKDOWN,
        include_tables=True,
        include_comments=False,
        favor_recall=True,
        config=_TRAFILATURA,
    )
    return _tidy(found) if found else None


def _markdown(html: str) -> str:
    return _tidy(markdownify.markdownify(html, heading_style="ATX", bullets="-"))


_MARKDOWN_SYNTAX: Final = (
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),  # images: their alt text
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),  # links: their text
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),  # headings
    (re.compile(r"(\*\*|__)(.+?)\1"), r"\2"),  # strong
    (re.compile(r"(?<![\w*])([*_])(?!\s)(.+?)(?<!\s)\1(?![\w*])"), r"\2"),  # emphasis
    (re.compile(r"^```.*$\n?", re.MULTILINE), ""),  # code fences
)


def _plain(markdown: str) -> str:
    """Markdown as plain text."""
    for pattern, replacement in _MARKDOWN_SYNTAX:
        markdown = pattern.sub(replacement, markdown)
    return _tidy(markdown)


def _tidy(text: str) -> str:
    """`text` without trailing spaces or runs of blank lines, a blank line around code blocks."""
    lines = [line.rstrip() for line in text.strip().splitlines()]
    tidied: list[str] = []
    fence = False
    for line in lines:
        opens = line.startswith("```")
        if opens and not fence and tidied and tidied[-1]:
            tidied.append("")
        if not line and tidied and not tidied[-1]:
            continue
        tidied.append(line)
        if opens:
            if fence:
                tidied.append("")
            fence = not fence
    return "\n".join(tidied).strip()


# ---------------------------------------------------------------------------------------------
# A JavaScript shell
# ---------------------------------------------------------------------------------------------

_NEEDS_JS: Final = re.compile(
    r"(enable|turn on|requires?|needs?|without) javascript|javascript (is )?(required|disabled|"
    r"needed)|doesn't work properly without javascript",
    re.IGNORECASE,
)
# Where a single-page app's scripts put it (React's, Vue's, Next's, Nuxt's, Angular's...).
_APP_ROOT: Final = "#root, #app, #__next, #__nuxt, [ng-app], app-root"
_NOTICE_WORDS: Final = 80  # (an "enable JavaScript" notice on a page of fewer words)


def is_javascript_shell(page: FetchedPage) -> bool:
    """Whether an HTML page is a shell its scripts fill in (so it reads only in a browser): it
    has scripts, and no words at all, or an app's root with nothing in it, or an "enable
    JavaScript" notice and few words besides."""
    if not page.is_html:
        return False
    parsed = LexborHTMLParser(page.text())
    body = parsed.body
    if not parsed.css_first("script") or body is None:
        return False
    notice = " ".join(node.text() for node in parsed.css("noscript"))
    for dropped in body.css(_DROPPED):
        dropped.decompose()
    words = body.text(separator=" ").split()
    if not words or any(not node.text().strip() for node in body.css(_APP_ROOT)):
        return True
    return (
        len(words) < _NOTICE_WORDS and _NEEDS_JS.search(f"{notice} {' '.join(words)}") is not None
    )
