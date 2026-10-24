"""
-----------------
- challenges.py -
-----------------

Recognising anti-bot checks: the interstitial a page answers with instead of itself (Cloudflare's
"Just a moment...", DataDome's, PerimeterX's Press & Hold...), a CAPTCHA widget on a page that
holds little else, or a block with nothing to pass (Akamai's "Access Denied").

MARKERS is a table, so a new vendor is a row: each marker says where it looks (a header, the
status, the title, the markup, the URL, a cookie the page sets, or the few words of a page with
little else), what it matches, and whether it is enough alone; those of a kind that are not
must all match together (Cloudflare serves every page with `server: cloudflare`, but a 403 or
503 of its, titled "Just a moment...", is its check). A widget marker counts only on a page that
holds little else, or with a 4xx, so a sign-in form with a CAPTCHA in it is no check. detect()
reads the first 512 KiB of a page, and only its markup (its title, scripts, iframes, forms,
ids and classes), not its text, so an article about CAPTCHAs is not one.
"""

# System Imports
import functools
import re
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Final

# Package Imports
from pydantic import BaseModel, ConfigDict
from selectolax.lexbor import LexborHTMLParser

READ: Final = 512 * 1024
"""How much of a page detect() reads."""
LITTLE: Final = 80
"""A page with fewer words than this holds little else (than a check)."""

# Attributes whose values are the markup's (what a script or widget is, and where it comes from).
_ATTRIBUTES: Final = ("id", "class", "src", "href", "action", "name", "data-sitekey", "data-ray")


class ChallengeKind(StrEnum):
    """Whose check a page is."""

    CLOUDFLARE = "cloudflare"
    TURNSTILE = "turnstile"
    HCAPTCHA = "hcaptcha"
    RECAPTCHA = "recaptcha"
    DATADOME = "datadome"
    PERIMETERX = "perimeterx"
    AWS_WAF = "aws_waf"
    IMPERVA = "imperva"
    SUCURI = "sucuri"
    AKAMAI = "akamai"
    UNKNOWN = "unknown"  # a "verify you are human" page no vendor's markers match


class Where(StrEnum):
    """Where a marker looks."""

    HEADER = "header"  # a response header's value (`name`)
    STATUS = "status"  # the HTTP status, as digits
    TITLE = "title"  # the page's <title>
    MARKUP = "markup"  # scripts, iframes, forms, ids and classes: not the page's text
    URL = "url"  # the URL the page is at
    COOKIE = "cookie"  # the name of a cookie the response sets
    TEXT = "text"  # the words of a page that holds little else


class Marker(BaseModel):
    """A sign of a kind of check."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ChallengeKind
    where: Where
    match: str
    """A regular expression (any case; ^ and $ at each line's ends), searched for."""
    name: str | None = None
    """The header a HEADER marker reads."""
    alone: bool = True
    """Enough alone; else the kind's markers that are not alone must all match together."""
    widget: bool = False
    """It counts only on a page that holds little else, or with a 4xx (a CAPTCHA's widget)."""
    passable: bool = True
    """A check a person can pass, rather than a block with nothing to pass."""

    def describe(self) -> str:
        """What it found, for a URL's attempts and the log."""
        at = f"header {self.name}" if self.where is Where.HEADER else self.where.value
        return f"{self.kind.value}: {at} ~ {self.match}"


def _m(kind: ChallengeKind, where: Where, match: str, **fields: object) -> Marker:
    return Marker.model_validate({"kind": kind, "where": where, "match": match, **fields})


_CF, _TS, _HC, _RC = (
    ChallengeKind.CLOUDFLARE,
    ChallengeKind.TURNSTILE,
    ChallengeKind.HCAPTCHA,
    ChallengeKind.RECAPTCHA,
)
_DD, _PX, _AWS, _IMP, _SUC, _AK = (
    ChallengeKind.DATADOME,
    ChallengeKind.PERIMETERX,
    ChallengeKind.AWS_WAF,
    ChallengeKind.IMPERVA,
    ChallengeKind.SUCURI,
    ChallengeKind.AKAMAI,
)

MARKERS: Final[tuple[Marker, ...]] = (
    # Cloudflare: its documented header on a check's answer; its check page's script (normal
    # pages may load /cdn-cgi/challenge-platform/'s bot detection, never `orchestrate`); a 403
    # or 503 of Cloudflare's titled "Just a moment..."; and its block ("Sorry, you have been
    # blocked").
    _m(_CF, Where.HEADER, r"^challenge$", name="cf-mitigated"),
    _m(_CF, Where.MARKUP, r"/cdn-cgi/challenge-platform/\S*orchestrate"),
    _m(_CF, Where.URL, r"/cdn-cgi/challenge-platform/"),
    _m(_CF, Where.STATUS, r"^(403|503)$", alone=False),
    _m(_CF, Where.HEADER, r"^cloudflare", name="server", alone=False),
    _m(_CF, Where.TITLE, r"^\s*just a moment", alone=False),
    _m(_CF, Where.TITLE, r"attention required! \| cloudflare", passable=False),
    # Turnstile, hCaptcha and reCAPTCHA: their widgets, on a page with little else.
    _m(_TS, Where.MARKUP, r"challenges\.cloudflare\.com/turnstile", widget=True),
    _m(_TS, Where.MARKUP, r"\bcf-turnstile\b", widget=True),
    _m(_HC, Where.MARKUP, r"\bh-captcha\b", widget=True),
    _m(_HC, Where.MARKUP, r"(js\.)?hcaptcha\.com/1/api\.js", widget=True),
    _m(_RC, Where.MARKUP, r"\bg-recaptcha\b", widget=True),
    _m(_RC, Where.MARKUP, r"(google|recaptcha)\.(com|net)/recaptcha/(api|enterprise)", widget=True),
    # DataDome: its check's iframe (normal pages load js.datadome.co, never this), or its header
    # on a 403.
    _m(_DD, Where.MARKUP, r"captcha-delivery\.com"),
    _m(_DD, Where.HEADER, r".", name="x-datadome", alone=False),
    _m(_DD, Where.STATUS, r"^403$", alone=False),
    # PerimeterX (HUMAN): its Press & Hold.
    _m(_PX, Where.MARKUP, r"\bpx-captcha\b"),
    _m(_PX, Where.MARKUP, r"_pxCaptcha"),
    # AWS WAF: its header on a check's answer, or its check page's script.
    _m(_AWS, Where.HEADER, r"^(captcha|challenge)$", name="x-amzn-waf-action"),
    _m(_AWS, Where.MARKUP, r"\bgokuProps\b"),
    # Imperva (Incapsula): its resource, with a 403.
    _m(_IMP, Where.MARKUP, r"_Incapsula_Resource", alone=False),
    _m(_IMP, Where.STATUS, r"^403$", alone=False),
    # Sucuri: its JavaScript check, and its firewall's block.
    _m(_SUC, Where.MARKUP, r"sucuri_cloudproxy_js"),
    _m(_SUC, Where.TITLE, r"sucuri website firewall - access denied", passable=False),
    # Akamai: a 403 "Access Denied" with a Reference # (nothing to pass).
    _m(_AK, Where.STATUS, r"^403$", alone=False, passable=False),
    _m(_AK, Where.TITLE, r"^\s*access denied\s*$", alone=False, passable=False),
    _m(_AK, Where.TEXT, r"reference\s*#\s*\d+\.", alone=False, passable=False),
)
"""How each kind of check is recognised."""

# A "verify you are human" page that no vendor's markers match: the title, or the few words, of
# a page that holds little else.
_UNKNOWN: Final = re.compile(
    r"verify (that )?you('| a)re (a )?human|checking (if the site connection is secure|your "
    r"browser)|are you a (robot|human)|human verification|not a robot|bot (check|protection)",
    re.IGNORECASE,
)


class Detection(BaseModel):
    """A check (or a block) recognised on a page."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    kind: ChallengeKind
    """Whose check it is."""
    passable: bool
    """Whether a person can pass it (else it is a block, with nothing to pass)."""
    evidence: list[str]
    """The markers that matched."""


class _Page:
    """What detect() reads of a page."""

    def __init__(self, status: int, headers: Mapping[str, str], html: str, url: str) -> None:
        self.status = str(status)
        self.headers = {name.lower(): value for name, value in headers.items()}
        self.url = url
        parsed = LexborHTMLParser(html[:READ])
        title = parsed.css_first("title")
        self.title = " ".join(title.text().split()) if title is not None else ""
        self.markup = _markup(parsed)
        self.words = _words(parsed)
        self.cookies = _set_cookies(self.headers.get("set-cookie", ""))

    @property
    def little(self) -> bool:
        """Whether it holds little else than what a check shows."""
        return len(self.words.split()) < LITTLE

    def value(self, marker: Marker) -> str | None:
        """What `marker` looks at, or None when the page has nothing there."""
        match marker.where:
            case Where.HEADER:
                return self.headers.get((marker.name or "").lower())
            case Where.STATUS:
                return self.status
            case Where.TITLE:
                return self.title
            case Where.MARKUP:
                return self.markup
            case Where.URL:
                return self.url
            case Where.COOKIE:
                return "\n".join(self.cookies)
            case Where.TEXT:
                return self.words if self.little else None

    def matches(self, marker: Marker) -> bool:
        if marker.widget and not (self.little or self.status.startswith("4")):
            return False
        value = self.value(marker)
        return value is not None and _pattern(marker.match).search(value) is not None


@functools.cache
def _pattern(match: str) -> re.Pattern[str]:
    return re.compile(match, re.IGNORECASE | re.MULTILINE)  # (cookies are a line each)


def _markup(parsed: LexborHTMLParser) -> str:
    """The page's markup, without its text: its elements' ids, classes and sources, and its
    scripts."""
    parts: list[str] = []
    for node in parsed.css("*"):
        attributes = node.attributes
        parts.extend(f"{name}={attributes[name]}" for name in _ATTRIBUTES if attributes.get(name))
        if node.tag == "script":
            parts.append(node.text(deep=True))
    return "\n".join(parts)


def _words(parsed: LexborHTMLParser) -> str:
    """The page's readable text."""
    body = parsed.body
    if body is None:
        return ""
    for node in body.css("script, style, noscript, template"):
        node.decompose()
    return " ".join(body.text(separator=" ").split())


def _set_cookies(header: str) -> list[str]:
    """The names of the cookies a Set-Cookie header (or several, a line each) sets."""
    names: list[str] = []
    for line in header.splitlines():
        name, equals, _ = line.partition("=")
        if equals:
            names.append(name.strip())
    return names


def detect(
    status: int,
    headers: Mapping[str, str],
    html: str,
    url: str,
    *,
    markers: Sequence[Marker] = MARKERS,
) -> Detection | None:
    """The check (or block) a page is, if it is one (by `markers`, the table's rows)."""
    page = _Page(status, headers, html, url)
    for kind in ChallengeKind:
        rows = [marker for marker in markers if marker.kind is kind]
        alone = [marker for marker in rows if marker.alone and page.matches(marker)]
        if alone:
            return _found(kind, alone)
        together = [marker for marker in rows if not marker.alone]
        if together and all(page.matches(marker) for marker in together):
            return _found(kind, together)
    if not page.little:  # (an article about checks says these words too)
        return None
    for where, value in ((Where.TITLE, page.title), (Where.TEXT, page.words)):
        if found := _UNKNOWN.search(value):
            return Detection(
                kind=ChallengeKind.UNKNOWN,
                passable=True,
                evidence=[f"unknown: {where.value} says {found.group(0)!r}"],
            )
    return None


def _found(kind: ChallengeKind, markers: list[Marker]) -> Detection:
    return Detection(
        kind=kind,
        passable=all(marker.passable for marker in markers),
        evidence=[marker.describe() for marker in markers],
    )
