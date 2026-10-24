"""
-------------------
- test_cookies.py -
-------------------

Cookies, both ways (udl.fetch.cookies): each form they come in, a cookies.txt round trip, values
never shown, and the jar that sends a site's cookies and keeps what the site sets
"""

# System Imports
import json
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

# Package Imports
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.engine import StaticCookies, databases, registry, settings
from udl.db.manager import DBManager
from udl.errors import ConfigError
from udl.fetch.cookies import (
    CookieInfo,
    CookieStore,
    ImportedCookies,
    SessionJar,
    by_site,
    key_of,
    of_site,
    parse_cookies,
    to_cookies_txt,
)
from udl.fetch.ladder import FetchSites
from udl.plugins.downloaders.auth import BrowserCookieSource
from udl.plugins.downloaders.base import Cookie

CANARY = "canary-3f9a7c"  # (a value no answer, log line or repr may show)
SOON = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)

COOKIES_TXT = f"""# Netscape HTTP Cookie File
# https://curl.se/docs/http-cookies.html

.example.com\tTRUE\t/\tTRUE\t{int(SOON.timestamp())}\tsession\t{CANARY}
#HttpOnly_shop.example.com\tFALSE\t/cart\tFALSE\t0\tcart\t42
other.example\tFALSE\t/\tFALSE\t0\tempty
"""


def names(cookies: list[Cookie]) -> list[tuple[str, str, str]]:
    return [(c.name, c.domain, c.value.get_secret_value()) for c in cookies]


# ---------------------------------------------------------------------------------------------
# The forms
# ---------------------------------------------------------------------------------------------


def test_a_netscape_cookies_txt() -> None:
    cookies = parse_cookies(COOKIES_TXT)

    assert names(cookies) == [
        ("session", ".example.com", CANARY),
        ("cart", "shop.example.com", "42"),
        ("empty", "other.example", ""),
    ]
    session, cart, _ = cookies
    assert (session.secure, session.http_only, session.expires, session.path) == (
        True,
        False,
        SOON,
        "/",
    )
    assert (cart.secure, cart.http_only, cart.expires, cart.path) == (False, True, None, "/cart")


def test_a_cookies_txt_without_its_header_line() -> None:
    cookies = parse_cookies("example.com\tFALSE\t/\tFALSE\t0\ta\t1\n")

    assert names(cookies) == [("a", "example.com", "1")]


def test_a_cookies_txt_with_subdomains_but_no_dot_gets_one() -> None:
    [cookie] = parse_cookies("example.com\tTRUE\t/\tFALSE\t0\ta\t1\n")

    assert cookie.domain == ".example.com"


def test_a_line_of_a_cookies_txt_that_is_not_a_cookie_is_refused() -> None:
    with pytest.raises(ConfigError, match="Line 2"):
        parse_cookies("# Netscape HTTP Cookie File\nexample.com\tTRUE\t/\n")


def test_a_browser_extensions_json_export() -> None:
    exported = [
        {
            "domain": ".example.com",
            "expirationDate": SOON.timestamp(),
            "hostOnly": False,
            "httpOnly": True,
            "name": "session",
            "path": "/",
            "sameSite": "lax",
            "secure": True,
            "session": False,
            "value": CANARY,
        },
        {
            "domain": "shop.example.com",
            "hostOnly": True,
            "name": "cart",
            "value": "1",
            "session": True,
        },
        {"domain": "example.org", "hostOnly": False, "name": "wide", "value": "2"},
    ]

    cookies = parse_cookies(json.dumps(exported))

    assert names(cookies) == [
        ("session", ".example.com", CANARY),
        ("cart", "shop.example.com", "1"),
        ("wide", ".example.org", "2"),
    ]
    assert (cookies[0].http_only, cookies[0].secure, cookies[0].expires) == (True, True, SOON)
    assert cookies[1].expires is None


def test_playwrights_storage_state() -> None:
    state = {
        "cookies": [
            {
                "name": "session",
                "value": CANARY,
                "domain": "a.localhost",
                "path": "/",
                "expires": -1,
                "httpOnly": True,
                "secure": False,
                "sameSite": "Lax",
            }
        ],
        "origins": [{"origin": "http://a.localhost", "localStorage": list[object]()}],
    }

    [cookie] = parse_cookies(json.dumps(state))

    assert (cookie.name, cookie.domain, cookie.expires, cookie.http_only) == (
        "session",
        "a.localhost",
        None,
        True,
    )


@pytest.mark.parametrize(
    "header", [f"Cookie: session={CANARY}; theme=dark", f"session={CANARY}; theme=dark"]
)
def test_a_cookie_header_for_a_site(header: str) -> None:
    cookies = parse_cookies(header, site="www.example.com")

    assert names(cookies) == [
        ("session", ".example.com", CANARY),
        ("theme", ".example.com", "dark"),
    ]
    assert all(cookie.expires is None for cookie in cookies)


def test_a_cookie_header_needs_its_site() -> None:
    with pytest.raises(ConfigError, match="which site"):
        parse_cookies("a=1; b=2")


@pytest.mark.parametrize(
    "text",
    ["", "   ", "just some words", "[1, 2]", '{"no": "cookies"}', "{not json", '[{"value": "x"}]'],
)
def test_what_is_not_cookies_is_refused_naming_the_forms_udl_reads(text: str) -> None:
    with pytest.raises(ConfigError) as refused:
        parse_cookies(text, site="example.com")

    assert refused.value.hint is not None
    assert "cookies.txt" in refused.value.hint


_names = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126, exclude_characters="=;#\t,"),
    min_size=1,
    max_size=12,
)
_values = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126, exclude_characters=";\t"),
    max_size=20,
)
_domains = st.from_regex(r"\.?[a-z][a-z0-9-]{0,8}\.(com|org|example)", fullmatch=True)


@given(
    st.lists(
        st.builds(
            Cookie,
            name=_names,
            value=_values.map(SecretStr),
            domain=_domains,
            path=st.sampled_from(["/", "/a", "/a/b"]),
            secure=st.booleans(),
            http_only=st.booleans(),
            expires=st.one_of(
                st.none(),
                st.integers(min_value=1_000_000_000, max_value=4_000_000_000).map(
                    lambda s: datetime.fromtimestamp(s, UTC)
                ),
            ),
        ),
        min_size=1,
        max_size=5,
    )
)
def test_a_cookies_txt_round_trips(cookies: list[Cookie]) -> None:
    read = parse_cookies(to_cookies_txt(cookies))

    assert [
        (c.name, c.value.get_secret_value(), c.domain, c.path, c.secure, c.http_only, c.expires)
        for c in read
    ] == [
        (c.name, c.value.get_secret_value(), c.domain, c.path, c.secure, c.http_only, c.expires)
        for c in cookies
    ]


# ---------------------------------------------------------------------------------------------
# Never shown
# ---------------------------------------------------------------------------------------------


def test_a_cookies_value_is_never_in_its_repr_or_its_json() -> None:
    [cookie] = parse_cookies(f"Cookie: session={CANARY}", site="example.com")

    assert CANARY not in repr(cookie)
    assert CANARY not in cookie.model_dump_json()
    assert CANARY not in CookieInfo.of(cookie).model_dump_json()
    assert "value" not in CookieInfo.model_fields


def test_a_cookies_value_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    parse_cookies(COOKIES_TXT)
    with pytest.raises(ConfigError) as refused:
        parse_cookies(f"# Netscape HTTP Cookie File\nexample.com\tTRUE\t{CANARY}\n")

    assert CANARY not in caplog.text
    assert CANARY not in refused.value.message


# ---------------------------------------------------------------------------------------------
# By site
# ---------------------------------------------------------------------------------------------


def test_cookies_are_grouped_by_site() -> None:
    cookies = parse_cookies(COOKIES_TXT)

    grouped = by_site(cookies)

    assert {site: [c.name for c in found] for site, found in grouped.items()} == {
        "example.com": ["cart", "session"],
        "other.example": ["empty"],
    }
    assert [c.name for c in of_site(cookies, "https://shop.example.com/x")] == ["cart", "session"]
    assert of_site(cookies, "nothing.example") == []


# ---------------------------------------------------------------------------------------------
# The jar
# ---------------------------------------------------------------------------------------------


class MemoryProfile:
    def __init__(self, cookies: list[Cookie] | None = None) -> None:
        self.stored = {key_of(c): c for c in cookies or []}
        self.writes = 0

    async def cookies(self) -> list[Cookie]:
        return list(self.stored.values())

    async def add(self, cookies: list[Cookie]) -> None:
        self.writes += 1
        self.stored |= {key_of(c): c for c in cookies}

    async def remove(self, cookies: list[Cookie]) -> None:
        self.writes += 1
        for cookie in cookies:
            self.stored.pop(key_of(cookie), None)

    async def clear(self, site: str) -> int:
        found = of_site(self.stored.values(), site)
        for cookie in found:
            self.stored.pop(key_of(cookie))
        return len(found)


def cookie(name: str, value: str, domain: str = "example.com") -> Cookie:
    return Cookie(name=name, value=SecretStr(value), domain=domain)


URL = "https://example.com/page"


async def test_the_jar_reads_a_sites_cookies_once() -> None:
    source = StaticCookies([cookie("a", "1"), cookie("b", "2", "other.org")])
    jar = SessionJar(source, MemoryProfile())

    first = await jar.cookies_for(URL)
    again = await jar.cookies_for("https://example.com/other")

    assert [c.name for c in first] == [c.name for c in again] == ["a"]
    assert source.asked == 1


async def test_what_a_site_sets_is_sent_next_and_written_to_the_profile_when_flushed() -> None:
    profile = MemoryProfile()
    jar = SessionJar(StaticCookies([cookie("session", "old")]), profile)
    sent = await jar.cookies_for(URL)

    await jar.keep(URL, sent, [cookie("session", "new"), cookie("seen", "1")])

    assert {c.name: c.value.get_secret_value() for c in await jar.cookies_for(URL)} == {
        "session": "new",
        "seen": "1",
    }
    assert profile.stored == {}
    await jar.flush()
    assert {c.name: c.value.get_secret_value() for c in profile.stored.values()} == {
        "session": "new",
        "seen": "1",
    }


async def test_what_a_site_deletes_is_deleted_from_the_profile() -> None:
    profile = MemoryProfile([cookie("session", "old")])
    jar = SessionJar(StaticCookies([cookie("session", "old")]), profile)
    sent = await jar.cookies_for(URL)

    await jar.keep(URL, sent, [])
    await jar.flush()

    assert await jar.cookies_for(URL) == []
    assert profile.stored == {}


async def test_nothing_changed_writes_nothing() -> None:
    profile = MemoryProfile()
    jar = SessionJar(StaticCookies([cookie("session", "same")]), profile)
    sent = await jar.cookies_for(URL)

    await jar.keep(URL, sent, sent)
    await jar.flush()

    assert profile.writes == 0


async def test_while_the_profile_is_open_changes_are_written_at_once() -> None:
    profile = MemoryProfile()
    jar = SessionJar(StaticCookies(), profile, writes_through=lambda: True)

    await jar.keep(URL, [], [cookie("session", "new")])

    assert [c.name for c in profile.stored.values()] == ["session"]


async def test_a_cookie_set_again_under_another_spelling_of_its_domain_replaces_it() -> None:
    profile = MemoryProfile()
    imported = cookie("session", "old", ".example.com")
    jar = SessionJar(StaticCookies([imported]), profile)
    sent = await jar.cookies_for(URL)

    await jar.keep(URL, sent, [cookie("session", "new", "example.com")])
    await jar.flush()

    assert [(c.domain, c.value.get_secret_value()) for c in await jar.cookies_for(URL)] == [
        ("example.com", "new")
    ]


async def test_forgetting_a_site_reads_its_cookies_again() -> None:
    source = StaticCookies([cookie("a", "1")])
    jar = SessionJar(source, None)
    await jar.cookies_for(URL)

    jar.forget("example.com")
    await jar.cookies_for(URL)
    jar.forget()
    await jar.cookies_for(URL)

    assert source.asked == 3


async def test_a_jar_without_a_profile_keeps_changes_to_itself() -> None:
    jar = SessionJar(StaticCookies(), None)

    await jar.keep(URL, [], [cookie("session", "new")])
    await jar.flush()

    assert [c.name for c in await jar.cookies_for(URL)] == ["session"]


# ---------------------------------------------------------------------------------------------
# Importing, listing and deleting
# ---------------------------------------------------------------------------------------------


class Store:
    def __init__(self, db: DBManager, browser: list[Cookie] | None = None) -> None:
        self.profile = MemoryProfile()
        self.sites = FetchSites(db, FakeClock())
        self.asked: list[tuple[str, str]] = []
        self.browser = browser or []
        self.store = CookieStore(self.profile, self.sites, read_browser=self._read)

    async def _read(self, source: BrowserCookieSource, url: str) -> list[Cookie]:
        self.asked.append((source.browser, url))
        return [cookie for cookie in self.browser if cookie.matches(url)]


@pytest.fixture
async def store(tmp_path_factory: pytest.TempPathFactory) -> AsyncGenerator[Store]:
    config = settings(tmp_path_factory.mktemp("cookies"))
    async with databases(config, registry()) as db:
        yield Store(db, [cookie("from-firefox", "1", ".example.com")])


async def test_imported_cookies_go_into_the_profile(store: Store) -> None:
    imported = await store.store.import_cookies(COOKIES_TXT)

    assert imported == ImportedCookies(
        sites={"example.com": 2, "other.example": 1}, user_agent=False
    )
    assert sorted(c.name for c in await store.profile.cookies()) == ["cart", "empty", "session"]
    assert CANARY not in imported.model_dump_json()


async def test_only_the_named_sites_cookies_are_imported(store: Store) -> None:
    imported = await store.store.import_cookies(COOKIES_TXT, site="other.example")

    assert imported.sites == {"other.example": 1}


async def test_a_user_agent_is_kept_for_each_site_imported(store: Store) -> None:
    await store.store.import_cookies(COOKIES_TXT, user_agent="Their Browser/1.0")

    for site in ("example.com", "other.example"):
        known = await store.sites.get(site)
        assert known is not None
        assert known.user_agent == "Their Browser/1.0"


async def test_a_sites_cookies_can_be_copied_from_the_users_browser(store: Store) -> None:
    imported = await store.store.import_cookies(
        None, site="www.example.com", from_browser="firefox"
    )

    assert imported.sites == {"example.com": 1}
    assert store.asked == [("firefox", "https://example.com/")]
    assert [c.name for c in await store.profile.cookies()] == ["from-firefox"]


@pytest.mark.parametrize(
    ("text", "fields", "problem"),
    [
        (None, {}, "one of them"),
        ("a=1", {"from_browser": "firefox", "site": "example.com"}, "one of them"),
        (None, {"from_browser": "firefox"}, "the site"),
        (None, {"from_browser": "netscape-navigator", "site": "example.com"}, "cannot read"),
        (COOKIES_TXT, {"site": "nothing.example"}, "no cookies of nothing.example"),
    ],
)
async def test_what_cannot_be_imported_says_why(
    store: Store, text: str | None, fields: dict[str, str], problem: str
) -> None:
    with pytest.raises(ConfigError, match=problem):
        await store.store.import_cookies(text, **fields)


async def test_cookies_are_listed_by_site_without_their_values(store: Store) -> None:
    await store.store.import_cookies(COOKIES_TXT, site="example.com", user_agent="UA/1")

    [listed] = await store.store.list_cookies()

    assert (listed.site, listed.user_agent) == ("example.com", "UA/1")
    assert [(c.name, c.domain, c.path) for c in listed.cookies] == [
        ("cart", "shop.example.com", "/cart"),
        ("session", ".example.com", "/"),
    ]
    assert CANARY not in listed.model_dump_json()
    assert await store.store.list_cookies("other.example") == []


async def test_deleting_a_sites_cookies_leaves_the_others(store: Store) -> None:
    await store.store.import_cookies(COOKIES_TXT, user_agent="UA/1")

    assert await store.store.delete_cookies("https://shop.example.com/") is True

    assert [c.name for c in await store.profile.cookies()] == ["empty"]
    known = await store.sites.get("example.com")
    assert known is not None
    assert known.user_agent is None
    assert await store.store.delete_cookies("example.com") is False
