"""
----------------
- test_urls.py -
----------------

URL normalisation and site keys (the registrable domain a URL belongs to)
"""

# Package Imports
import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError

# Local Imports
from udl.core.urls import URL, RequestURL, SiteKey, normalise_url, site_key

# ---------------------------------------------------------------------------------------------
# Strategies: URLs built from parts
# ---------------------------------------------------------------------------------------------

_LABEL_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"

# Subdomain labels: letters, digits and inner hyphens.
labels = st.from_regex(r"[a-z0-9]([a-z0-9-]{0,10}[a-z0-9])?", fullmatch=True)
# A registrable label that is no public suffix itself.
domains = st.text(_LABEL_CHARS, min_size=1, max_size=10).map(lambda label: f"udl{label}")
suffixes = st.sampled_from(["com", "org", "de", "co.uk", "com.au", "github.io"])
segments = st.text(
    st.characters(codec="utf-8", exclude_characters="/?#", exclude_categories=["Cc", "Zs"]),
    max_size=8,
)
params = st.tuples(
    st.text(_LABEL_CHARS + "_", min_size=1, max_size=8), st.text(_LABEL_CHARS + "%~", max_size=8)
)
tracking = st.sampled_from(["utm_source", "utm_campaign", "UTM_Medium", "fbclid", "gclid"])


@st.composite
def urls(draw: st.DrawFn) -> str:
    scheme = draw(st.sampled_from(["http", "https", "HTTP", "Https"]))
    host = ".".join([*draw(st.lists(labels, max_size=3)), draw(domains), draw(suffixes)])
    if draw(st.booleans()):
        host = host.upper()
    port = draw(st.sampled_from(["", ":80", ":443", ":8080"]))
    path = "/".join(["", *draw(st.lists(segments, max_size=4))])
    pairs = [f"{k}={v}" for k, v in draw(st.lists(params, max_size=3))]
    pairs += [f"{k}=x" for k in draw(st.lists(tracking, max_size=2))]
    query = "?" + "&".join(draw(st.permutations(pairs))) if pairs else ""
    fragment = draw(st.sampled_from(["", "#", "#top", "#:~:text=hello", "#t=10:~:text=a"]))
    return f"{scheme}://{host}{port}{path}{query}{fragment}"


# ---------------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------------


@given(urls())
def test_normalising_is_idempotent(url: str) -> None:
    once = normalise_url(url)

    assert normalise_url(once) == once


@given(st.text(max_size=40))
@example("http://[::1")
@example("https://a.com:99999/")
def test_any_text_is_rejected_or_normalises_to_a_fixed_point(text: str) -> None:
    try:
        once = normalise_url(text)
    except ValueError:
        return

    assert normalise_url(once) == once


@pytest.mark.parametrize(
    ("given_url", "normalised"),
    [
        # The scheme and host are lower case; the path keeps its case.
        ("HTTPS://Example.COM/Watch/Abc", "https://example.com/Watch/Abc"),
        # Default ports go, others stay.
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        ("http://example.com:443/a", "http://example.com:443/a"),
        # An empty path is the root.
        ("https://example.com", "https://example.com/"),
        ("https://example.com?v=1", "https://example.com/?v=1"),
        # A trailing dot on the host goes.
        ("https://example.com./a", "https://example.com/a"),
        # www. and m. stay: the site may not answer without them.
        ("https://www.example.com/a", "https://www.example.com/a"),
        ("https://m.example.com/a", "https://m.example.com/a"),
        # No scheme means https.
        ("example.com/watch?v=1", "https://example.com/watch?v=1"),
        ("//example.com/a", "https://example.com/a"),
        ("localhost:8080/a", "https://localhost:8080/a"),
        # Surrounding whitespace goes.
        ("  https://example.com/a \n", "https://example.com/a"),
        # IPv6 hosts keep their brackets.
        ("http://[::1]:8080/a", "http://[::1]:8080/a"),
        # A user name and password are kept as they are.
        ("https://User:Pw@Example.com/a", "https://User:Pw@example.com/a"),
    ],
)
def test_normalising(given_url: str, normalised: str) -> None:
    assert normalise_url(given_url) == normalised


@pytest.mark.parametrize(
    ("given_url", "normalised"),
    [
        (
            "https://example.com/a?v=1&utm_source=x&utm_medium=y&t=10",
            "https://example.com/a?v=1&t=10",
        ),
        ("https://example.com/a?fbclid=abc", "https://example.com/a"),
        ("https://example.com/a?gclid=1&UTM_Campaign=2&igshid=3", "https://example.com/a"),
        # Everything else is kept exactly as it was written, in its order (a signed URL's
        # signature covers the query as written).
        (
            "https://cdn.example.com/a.mp4?Expires=1&Signature=a%2Fb~c&Key-Pair-Id=K",
            "https://cdn.example.com/a.mp4?Expires=1&Signature=a%2Fb~c&Key-Pair-Id=K",
        ),
        ("https://example.com/a?b=2&a=1&&c", "https://example.com/a?b=2&a=1&&c"),
        ("https://example.com/a?q=a+b%20c", "https://example.com/a?q=a+b%20c"),
    ],
)
def test_tracking_parameters_are_removed(given_url: str, normalised: str) -> None:
    assert normalise_url(given_url) == normalised


@pytest.mark.parametrize(
    ("given_url", "normalised"),
    [
        ("https://example.com/a#", "https://example.com/a"),
        # Fragments are kept: a page that routes in JavaScript may need it.
        ("https://example.com/#/watch/1", "https://example.com/#/watch/1"),
        # A text fragment (a highlight in a shared link) is not part of the page.
        ("https://example.com/a#:~:text=hello", "https://example.com/a"),
        ("https://example.com/a#t=10:~:text=hello", "https://example.com/a#t=10"),
    ],
)
def test_fragments(given_url: str, normalised: str) -> None:
    assert normalise_url(given_url) == normalised


@given(urls())
def test_normalised_urls_keep_no_tracking_parameters(url: str) -> None:
    query = normalise_url(url).partition("?")[2].partition("#")[0]
    keys = [pair.partition("=")[0].lower() for pair in query.split("&") if pair]

    assert not [key for key in keys if key.startswith("utm_") or key in ("fbclid", "gclid")]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "ftp://example.com/a",
        "mailto:someone@example.com",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://",
        "https:///path",
        "https://exa mple.com/",
        "https://example.com:99999/",
        "https://example.com:port/",
        "http://[::1",
        "https://./a",
    ],
)
def test_text_that_is_not_an_http_url_is_rejected(text: str) -> None:
    with pytest.raises(ValueError, match="URL"):
        normalise_url(text)


# ---------------------------------------------------------------------------------------------
# Site keys
# ---------------------------------------------------------------------------------------------


@given(st.lists(labels, max_size=3), domains, suffixes, urls())
def test_the_site_key_is_the_registrable_domain(
    subdomains: list[str], domain: str, suffix: str, other: str
) -> None:
    host = ".".join([*subdomains, domain, suffix])
    path_and_query = other.split("/", 3)[3] if other.count("/") >= 3 else ""

    assert site_key(f"https://{host}/{path_and_query}") == f"{domain}.{suffix}"


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("https://a.b.example.co.uk/watch", "example.co.uk"),
        ("https://www.example.com/", "example.com"),
        ("https://m.example.com/", "example.com"),
        ("https://WWW.Example.COM./", "example.com"),
        # A site key, a host or a URL without a scheme.
        ("example.com", "example.com"),
        ("www.example.com", "example.com"),
        ("m.youtube.com/watch?v=1", "youtube.com"),
        # Sites on shared hosting are sites of their own.
        ("https://someone.github.io/page", "someone.github.io"),
        # Hosts with no registrable domain are their own key (the port is not part of it).
        ("http://localhost:8080/a", "localhost"),
        ("http://127.0.0.1:8080/a", "127.0.0.1"),
        ("http://[::1]:8080/a", "::1"),
        ("https://intranet.corp/a", "intranet.corp"),
        ("https://github.io/", "github.io"),
    ],
)
def test_site_keys(text: str, key: str) -> None:
    assert site_key(text) == key


@given(urls())
def test_www_and_m_hosts_share_the_site_key(url: str) -> None:
    scheme, _, rest = normalise_url(url).partition("://")

    assert site_key(f"{scheme}://www.{rest}") == site_key(url)
    assert site_key(f"{scheme}://m.{rest}") == site_key(url)


def test_a_site_key_of_text_that_is_not_a_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="URL"):
        site_key("ftp://example.com/")


# ---------------------------------------------------------------------------------------------
# URL
# ---------------------------------------------------------------------------------------------


def test_a_parsed_url_is_normalised_and_knows_its_site() -> None:
    url = URL.parse("HTTPS://WWW.Example.co.uk/Watch?v=1&utm_source=x")

    assert url.value == "https://www.example.co.uk/Watch?v=1"
    assert str(url) == url.value
    assert url.site_key == "example.co.uk"
    assert url.host == "www.example.co.uk"
    assert url.path == "/Watch"


def test_parsing_text_that_is_not_a_url_raises_value_error() -> None:
    with pytest.raises(ValueError, match="URL"):
        URL.parse("not a url at all")


class _Request(BaseModel):
    url: RequestURL
    site: SiteKey


def test_request_models_normalise_urls_and_site_keys() -> None:
    request = _Request(url="Example.com/a?fbclid=1", site="https://www.example.com/x")

    assert request.url == "https://example.com/a"
    assert request.site == "example.com"


def test_request_models_reject_text_that_is_not_a_url() -> None:
    with pytest.raises(ValidationError, match="URL"):
        _Request(url="ftp://example.com/", site="example.com")
