"""
-----------
- urls.py -
-----------

URL normalisation and site keys.

A URL is normalised before udl looks anything up for it or downloads it: the scheme and host are
lower case, default ports and tracking parameters (utm_*, fbclid, …) go, and a URL written
without a scheme gets https. Everything else is kept exactly as written, since a signed URL's
signature covers its query as written and a page that routes in JavaScript may need its fragment.

A URL's site key is its registrable domain (eTLD+1, e.g. `example.co.uk` for
`a.b.example.co.uk`), from the public suffix list snapshot that comes with tldextract (it is
never fetched). Sites on shared hosting (e.g. `someone.github.io`) are sites of their own, and a
host with no registrable domain (localhost, an IP address, an intranet name) is its own key.
"""

# System Imports
import functools
import re
from typing import Annotated, Final, Self
from urllib.parse import unquote_plus, urlsplit, urlunsplit

# Package Imports
import tldextract
from pydantic import AfterValidator, BaseModel, ConfigDict

_DEFAULT_PORTS: Final[dict[str, int]] = {"http": 80, "https": 443}

# Query parameters that only track where a click came from.
TRACKING_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "fbclid",
        "gclid",
        "gclsrc",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "yclid",
        "twclid",
        "ttclid",
        "li_fat_id",
        "igshid",
        "igsh",
        "mc_cid",
        "mc_eid",
        "_ga",
        "_gl",
        "mkt_tok",
        "oly_anon_id",
        "oly_enc_id",
        "vero_id",
        "_hsenc",
        "_hsmi",
        "__hssc",
        "__hstc",
        "__hsfp",
        "srsltid",
    }
)
TRACKING_PREFIXES: Final[tuple[str, ...]] = ("utm_",)

_HAS_SCHEME: Final = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# A host (and port) with no scheme in front, e.g. "example.com/watch" or "localhost:8080".
_BARE_HOST: Final = re.compile(r"[^\s:/?#@]+(?::\d*)?(?:[/?#]|$)")
_BAD_HOST_CHARS: Final = re.compile(r"[\x00-\x20\x7f/?#@\\<>\"{}|^`%\[\]]")
# What follows a fragment's ":~:" is a text fragment (a highlight in a shared link).
_TEXT_FRAGMENT: Final[str] = ":~:"


def _not_a_url(text: str, why: str) -> ValueError:
    return ValueError(f"{text!r} is not an http(s) URL: {why}")


def _with_scheme(text: str) -> str:
    """`text` with https:// in front when it is written without a scheme."""
    if _HAS_SCHEME.match(text):
        return text
    if text.startswith("//"):
        return f"https:{text}"
    if _BARE_HOST.match(text):
        return f"https://{text}"
    return text


def _host(hostname: str, text: str) -> str:
    host = hostname.rstrip(".")
    if not host:
        raise _not_a_url(text, "it has no host")
    if ":" in host:  # an IPv6 address (urlsplit checks those)
        return f"[{host}]"
    if _BAD_HOST_CHARS.search(host):
        raise _not_a_url(text, f"{host!r} is not a host")
    return host


def _is_tracking(pair: str) -> bool:
    key = unquote_plus(pair.partition("=")[0]).lower()
    return key in TRACKING_PARAMS or key.startswith(TRACKING_PREFIXES)


def normalise_url(text: str) -> str:
    """`text` as a normalised http(s) URL (see the module docs). Raises ValueError when it is
    not one."""
    original = text
    text = _with_scheme(text.strip())
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as e:
        raise _not_a_url(original, str(e)) from None
    scheme = parts.scheme.lower()
    if scheme not in _DEFAULT_PORTS:
        raise _not_a_url(original, "udl downloads http and https URLs")
    host = _host(parts.hostname or "", original)

    userinfo, at, _ = parts.netloc.rpartition("@")
    netloc = f"{userinfo}{at}{host}"
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc += f":{port}"
    query = "&".join(pair for pair in parts.query.split("&") if not _is_tracking(pair))
    fragment = parts.fragment.partition(_TEXT_FRAGMENT)[0]
    return urlunsplit((scheme, netloc, parts.path or "/", query, fragment))


@functools.cache
def _extract() -> tldextract.TLDExtract:
    # The bundled suffix list snapshot, never fetched; no cache files are written.
    return tldextract.TLDExtract(
        cache_dir=None, suffix_list_urls=(), include_psl_private_domains=True
    )


def _key_of_host(host: str) -> str:
    return _extract()(host).top_domain_under_public_suffix or host


def site_key(text: str) -> str:
    """The site key of a URL, a host or a site key (see the module docs). Raises ValueError."""
    return _key_of_host(urlsplit(normalise_url(text)).hostname or "")


class URL(BaseModel):
    """A normalised http(s) URL and the site it belongs to."""

    model_config = ConfigDict(frozen=True)

    value: str  # the normalised URL
    site_key: str

    @classmethod
    def parse(cls, text: str) -> Self:
        """`text` normalised. Raises ValueError when it is not an http(s) URL."""
        value = normalise_url(text)
        return cls(value=value, site_key=site_key(value))

    @property
    def host(self) -> str:
        return urlsplit(self.value).hostname or ""

    @property
    def path(self) -> str:
        return urlsplit(self.value).path

    def __str__(self) -> str:
        return self.value


type RequestURL = Annotated[str, AfterValidator(normalise_url)]
"""A URL in a request: normalised, and rejected when it is not an http(s) URL."""

type SiteKey = Annotated[str, AfterValidator(site_key)]
"""A site in a request, given as a site key, a host or a URL: stored as its site key."""
