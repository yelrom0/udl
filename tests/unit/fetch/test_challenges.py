"""
----------------------
- test_challenges.py -
----------------------

Recognising anti-bot checks (udl.fetch.challenges): each kind from its markers, on small pages
written for these tests (never a vendor's own), and what is not one: an article that only talks
about CAPTCHAs, a sign-in form with a widget in it, and a page Cloudflare merely serves
"""

# System Imports
from dataclasses import dataclass, field

# Package Imports
import pytest

# Local Imports
from udl.fetch.challenges import (
    LITTLE,
    MARKERS,
    READ,
    ChallengeKind,
    Detection,
    Marker,
    Where,
    detect,
)

URL = "https://shop.example/item/1"


def page(body: str = "", *, title: str = "", head: str = "") -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title>{head}</head><body>{body}</body></html>"
    )


LONG_TEXT = " ".join(["Words of an article that goes on for a while."] * 20)


@dataclass(frozen=True)
class Case:
    html: str
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict[str, str])
    url: str = URL


CHECKS: dict[str, tuple[Case, ChallengeKind, bool]] = {
    "cloudflare's header": (
        Case(page("<p>One moment.</p>"), 403, {"cf-mitigated": "challenge"}),
        ChallengeKind.CLOUDFLARE,
        True,
    ),
    "cloudflare's interstitial": (
        Case(
            page("<div id='challenge-stage'></div>", title="Just a moment..."),
            503,
            {"server": "cloudflare"},
        ),
        ChallengeKind.CLOUDFLARE,
        True,
    ),
    "cloudflare's check script": (
        Case(
            page(
                head='<script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1?ray=1">'
                "</script>"
            ),
            403,
        ),
        ChallengeKind.CLOUDFLARE,
        True,
    ),
    "cloudflare's check url": (
        Case(page("<p>Wait.</p>"), url="https://shop.example/cdn-cgi/challenge-platform/x"),
        ChallengeKind.CLOUDFLARE,
        True,
    ),
    "cloudflare's block": (
        Case(
            page("<h1>Sorry, you have been blocked</h1>", title="Attention Required! | Cloudflare"),
            403,
            {"server": "cloudflare"},
        ),
        ChallengeKind.CLOUDFLARE,
        False,
    ),
    "turnstile": (
        Case(
            page(
                '<div class="cf-turnstile" data-sitekey="0x4AAA"></div>',
                head='<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
            )
        ),
        ChallengeKind.TURNSTILE,
        True,
    ),
    "hcaptcha": (
        Case(page('<form><div class="h-captcha" data-sitekey="10000000"></div></form>')),
        ChallengeKind.HCAPTCHA,
        True,
    ),
    "hcaptcha's script": (
        Case(page(head='<script src="https://js.hcaptcha.com/1/api.js"></script>')),
        ChallengeKind.HCAPTCHA,
        True,
    ),
    "recaptcha": (
        Case(
            page('<form action="/verify"><div class="g-recaptcha" data-sitekey="6Le"></div></form>')
        ),
        ChallengeKind.RECAPTCHA,
        True,
    ),
    "a recaptcha with a 4xx, on a page of words": (
        Case(
            page(f'<p>{LONG_TEXT}</p><div class="g-recaptcha"></div>'),
            429,
        ),
        ChallengeKind.RECAPTCHA,
        True,
    ),
    "recaptcha enterprise's script": (
        Case(page(head='<script src="https://www.google.com/recaptcha/enterprise.js"></script>')),
        ChallengeKind.RECAPTCHA,
        True,
    ),
    "datadome's iframe": (
        Case(
            page('<iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=x"></iframe>'),
            403,
        ),
        ChallengeKind.DATADOME,
        True,
    ),
    "datadome's header on a 403": (
        Case(page("<p>Blocked for now.</p>"), 403, {"x-datadome": "protected"}),
        ChallengeKind.DATADOME,
        True,
    ),
    "perimeterx": (
        Case(
            page('<div id="px-captcha"></div><p>Press &amp; Hold to confirm you are a human</p>'),
            403,
        ),
        ChallengeKind.PERIMETERX,
        True,
    ),
    "perimeterx's script": (
        Case(page("<script>window._pxCaptcha = true;</script>")),
        ChallengeKind.PERIMETERX,
        True,
    ),
    "aws waf's header": (
        Case(page(), 405, {"x-amzn-waf-action": "captcha"}),
        ChallengeKind.AWS_WAF,
        True,
    ),
    "aws waf's check page": (
        Case(page("<script>window.gokuProps = {key: 'x'};</script>"), 202),
        ChallengeKind.AWS_WAF,
        True,
    ),
    "imperva": (
        Case(page('<iframe src="/_Incapsula_Resource?CWUDNSAI=9"></iframe>'), 403),
        ChallengeKind.IMPERVA,
        True,
    ),
    "sucuri's check": (
        Case(page("<script>var sucuri_cloudproxy_js='', S='cz0i';</script>")),
        ChallengeKind.SUCURI,
        True,
    ),
    "sucuri's block": (
        Case(page("<h1>Access Denied</h1>", title="Sucuri WebSite Firewall - Access Denied"), 403),
        ChallengeKind.SUCURI,
        False,
    ),
    "akamai's block": (
        Case(
            page(
                "<h1>Access Denied</h1><p>Reference #18.6f1b3017.1761300000.2c5d</p>",
                title="Access Denied",
            ),
            403,
            {"server": "AkamaiGHost"},
        ),
        ChallengeKind.AKAMAI,
        False,
    ),
    "a verify-you-are-human title": (
        Case(page("<form><button>Continue</button></form>", title="Please verify you are human")),
        ChallengeKind.UNKNOWN,
        True,
    ),
    "a checking-your-browser page": (
        Case(page("<p>Checking your browser before accessing shop.example.</p>")),
        ChallengeKind.UNKNOWN,
        True,
    ),
}


@pytest.mark.parametrize(("case", "kind", "passable"), CHECKS.values(), ids=CHECKS.keys())
def test_each_kind_of_check_is_recognised_by_its_markers(
    case: Case, kind: ChallengeKind, passable: bool
) -> None:
    found = detect(case.status, case.headers, case.html, case.url)

    assert found is not None
    assert (found.kind, found.passable) == (kind, passable)
    assert found.evidence  # (what matched, for the URL's attempts and the log)


def test_every_kind_has_a_test_page() -> None:
    assert {kind for _, kind, _ in CHECKS.values()} == set(ChallengeKind)


def test_every_row_of_the_table_is_recognised_on_a_test_page() -> None:
    matched = {
        evidence
        for case, _, _ in CHECKS.values()
        if (found := detect(case.status, case.headers, case.html, case.url)) is not None
        for evidence in found.evidence
    }

    assert [marker.describe() for marker in MARKERS if marker.describe() not in matched] == []


NOT_CHECKS: dict[str, Case] = {
    "an ordinary page": Case(page(f"<h1>Item</h1><p>{LONG_TEXT}</p>", title="Item 1")),
    "an article about captchas": Case(
        page(
            "<h1>How CAPTCHAs work</h1><p>Sites ask you to verify you are human, with a "
            'g-recaptcha or h-captcha box, or <code>&lt;div class="cf-turnstile"&gt;</code>. '
            f"Cloudflare says Just a moment... while checking your browser. {LONG_TEXT}</p>",
            title="Why sites ask whether you are a robot: verify you are human, explained",
        )
    ),
    "a sign-in form with a widget": Case(
        page(
            f"<h1>Sign in</h1><p>{LONG_TEXT}</p><form action='/login'><input name='email'>"
            '<div class="g-recaptcha" data-sitekey="6Le"></div></form>'
        )
    ),
    "a page cloudflare serves": Case(
        page(f"<h1>Item</h1><p>{LONG_TEXT}</p>", title="Item"), 200, {"server": "cloudflare"}
    ),
    "a 403 cloudflare serves for the site": Case(
        page("<h1>Forbidden</h1>", title="Forbidden"), 403, {"server": "cloudflare"}
    ),
    "cloudflare's bot detection on a normal page": Case(
        page(
            f"<p>{LONG_TEXT}</p>",
            title="Item",
            head='<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>',
        )
    ),
    "datadome's tag on a normal page": Case(
        page(f"<p>{LONG_TEXT}</p>", head='<script src="https://js.datadome.co/tags.js"></script>'),
        200,
        {"x-datadome": "protected"},
    ),
    "a 403 with access denied but no reference": Case(
        page("<h1>Access Denied</h1>", title="Access Denied"), 403
    ),
    "a 404": Case(page("<h1>Not found</h1>", title="Not found"), 404),
}


@pytest.mark.parametrize("case", NOT_CHECKS.values(), ids=NOT_CHECKS.keys())
def test_what_is_no_check_is_not_one(case: Case) -> None:
    assert detect(case.status, case.headers, case.html, case.url) is None


def test_a_check_is_told_by_its_markup_not_its_words() -> None:
    # (The words "g-recaptcha" in text are no widget; the class is.)
    words = page("<p>g-recaptcha px-captcha _Incapsula_Resource sucuri_cloudproxy_js</p>")

    assert detect(403, {}, words, URL) is None


def test_only_the_start_of_a_page_is_read() -> None:
    late = page("x" * READ + '<div class="g-recaptcha"></div>')

    assert detect(200, {}, late, URL) is None


def test_a_widget_counts_on_a_page_of_few_words() -> None:
    few = " ".join(["word"] * (LITTLE - 1))
    many = " ".join(["word"] * LITTLE)

    assert detect(200, {}, page(f'<p>{few}</p><div class="h-captcha"></div>'), URL) is not None
    assert detect(200, {}, page(f'<p>{many}</p><div class="h-captcha"></div>'), URL) is None


def test_headers_are_read_whatever_their_case() -> None:
    found = detect(403, {"CF-Mitigated": "challenge"}, page(), URL)

    assert found is not None
    assert found.kind is ChallengeKind.CLOUDFLARE


def test_a_cookie_marker_reads_the_cookies_the_response_sets() -> None:
    markers = (Marker(kind=ChallengeKind.DATADOME, where=Where.COOKIE, match=r"^datadome$"),)
    headers = {"set-cookie": "a=1\nnot a cookie\ndatadome=x; Path=/"}

    found = detect(403, headers, page(), URL, markers=markers)

    assert found is not None
    assert found.kind is ChallengeKind.DATADOME


def test_markers_of_a_kind_that_are_not_alone_match_together() -> None:
    markers = (
        Marker(kind=ChallengeKind.IMPERVA, where=Where.STATUS, match="^403$", alone=False),
        Marker(kind=ChallengeKind.IMPERVA, where=Where.TITLE, match="^held$", alone=False),
    )

    assert detect(403, {}, page(title="held"), URL, markers=markers) is not None
    assert detect(403, {}, page(title="other"), URL, markers=markers) is None
    assert detect(200, {}, page(title="held"), URL, markers=markers) is None


def test_the_markers_are_a_table() -> None:
    # (A new vendor is a row.)
    assert all(isinstance(marker, Marker) for marker in MARKERS)
    assert all(marker.name is not None for marker in MARKERS if marker.where is Where.HEADER)


def test_a_detection_describes_what_matched() -> None:
    found = detect(403, {"cf-mitigated": "challenge"}, page(), URL)

    assert found == Detection(
        kind=ChallengeKind.CLOUDFLARE,
        passable=True,
        evidence=["cloudflare: header cf-mitigated ~ ^challenge$"],
    )
