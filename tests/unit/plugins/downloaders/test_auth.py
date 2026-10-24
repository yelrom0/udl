"""
---------------
- test_auth.py -
---------------

Cookies that sign in to a site: read from the user's browser (cookies_from_browser, here a fake
Firefox profile), and from udl's browser profile, combined for a URL
"""

# System Imports
import logging
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from udl.config import Settings
from udl.errors import ConfigError, ToolUnavailable
from udl.plugins.downloaders import auth
from udl.plugins.downloaders.auth import (
    AuthCookies,
    BrowserCookieSource,
    browser_login,
    cookies_from_browser,
    profile_cookies,
)
from udl.plugins.downloaders.base import Cookie

URL = "https://www.example.com/watch/1"
EXPIRES = datetime(2999, 1, 1, tzinfo=UTC)

# (host, name, value, path, isSecure) rows of a Firefox cookie database.
FIREFOX_COOKIES = [
    (".example.com", "session", "s3cret", "/", 1),  # the site and its subdomains
    ("www.example.com", "prefs", "dark", "/", 0),  # this host only
    ("media.www.example.com", "cdn", "c", "/videos", 0),  # a subdomain of the URL's host
    ("example.org", "other", "x", "/", 0),  # another site
    ("notexample.com", "lookalike", "y", "/", 0),
]


def firefox_profile(folder: Path, rows: list[tuple[str, str, str, str, int]]) -> Path:
    """A Firefox profile folder whose cookie database has these cookies (expiring in 2999)."""
    folder.mkdir(parents=True)
    with sqlite3.connect(folder / "cookies.sqlite") as db:
        db.execute("PRAGMA user_version = 17")  # Firefox 142+: expiry in milliseconds
        db.execute(
            "CREATE TABLE moz_cookies (id INTEGER PRIMARY KEY, originAttributes TEXT, name TEXT,"
            " value TEXT, host TEXT, path TEXT, expiry INTEGER, isSecure INTEGER)"
        )
        db.executemany(
            "INSERT INTO moz_cookies (originAttributes, name, value, host, path, expiry, isSecure)"
            " VALUES ('', ?, ?, ?, ?, ?, ?)",
            [
                (name, value, host, path, int(EXPIRES.timestamp() * 1000), secure)
                for host, name, value, path, secure in rows
            ],
        )
    db.close()
    return folder


# ---------------------------------------------------------------------------------------------
# cookies_from_browser
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "parsed"),
    [
        ("firefox", ("firefox", None, None, None)),
        (" Firefox ", ("firefox", None, None, None)),
        ("chrome:Profile 1", ("chrome", None, "Profile 1", None)),
        ("chromium+gnomekeyring:Default", ("chromium", "GNOMEKEYRING", "Default", None)),
        ("firefox::Personal", ("firefox", None, None, "Personal")),
        ("firefox:~/ff/profile::none", ("firefox", None, "~/ff/profile", "none")),
    ],
)
def test_a_browser_is_named_as_yt_dlp_names_it(
    spec: str, parsed: tuple[str, str | None, str | None, str | None]
) -> None:
    source = BrowserCookieSource.parse(spec)

    assert (source.browser, source.keyring, source.profile, source.container) == parsed


@pytest.mark.parametrize("spec", ["netscape", "chrome+nokeyring", "", ":Default", "+basictext"])
def test_a_browser_udl_cannot_read_is_a_config_error(spec: str) -> None:
    with pytest.raises(ConfigError, match="cookies_from_browser") as caught:
        BrowserCookieSource.parse(spec)

    assert caught.value.hint is not None


async def test_cookies_from_the_browser_are_the_ones_for_the_urls_site(tmp_path: Path) -> None:
    profile = firefox_profile(tmp_path / "firefox", FIREFOX_COOKIES)

    cookies = await cookies_from_browser(BrowserCookieSource.parse(f"firefox:{profile}"), URL)

    assert {cookie.name for cookie in cookies} == {"session", "prefs", "cdn"}
    [session] = [cookie for cookie in cookies if cookie.name == "session"]
    assert session == Cookie(
        name="session",
        value=SecretStr("s3cret"),
        domain=".example.com",
        secure=True,
        expires=EXPIRES,
    )


async def test_a_browser_without_cookies_where_it_was_said_is_a_config_error(
    tmp_path: Path,
) -> None:
    source = BrowserCookieSource.parse(f"firefox:{tmp_path / 'no-profile-here'}")

    with pytest.raises(ConfigError, match="firefox"):
        await cookies_from_browser(source, URL)


# ---------------------------------------------------------------------------------------------
# AuthCookies: every source, for a URL
# ---------------------------------------------------------------------------------------------


def make_cookie(name: str, value: str, domain: str = ".example.com") -> Cookie:
    return Cookie(name=name, value=SecretStr(value), domain=domain)


async def test_auth_cookies_come_from_the_browser_and_udls_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    firefox = firefox_profile(tmp_path / "firefox", FIREFOX_COOKIES)
    profile = tmp_path / "udl-profile"

    async def profile_cookies(url: str, *, profile_dir: Path) -> list[Cookie]:
        assert (url, profile_dir) == (URL, profile)
        return [make_cookie("session", "from-udl"), make_cookie("udl_only", "1")]

    monkeypatch.setattr(auth, "profile_cookies", profile_cookies)
    sources = AuthCookies(browser=BrowserCookieSource.parse(f"firefox:{firefox}"), profile=profile)

    cookies = {
        cookie.name: cookie.value.get_secret_value() for cookie in await sources.for_url(URL)
    }

    # udl's profile is where `udl browser login` signs in: its cookies win.
    assert cookies == {"session": "from-udl", "prefs": "dark", "cdn": "c", "udl_only": "1"}


async def test_a_source_that_fails_is_skipped_with_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def no_chromium(url: str, *, profile_dir: Path) -> list[Cookie]:
        raise ToolUnavailable("Chromium is not installed")

    monkeypatch.setattr(auth, "profile_cookies", no_chromium)
    browser = BrowserCookieSource.parse(f"firefox:{tmp_path / 'missing'}")
    sources = AuthCookies(browser=browser, profile=tmp_path / "udl-profile")

    with caplog.at_level(logging.WARNING, logger="udl"):
        cookies = await sources.for_url(URL)

    assert cookies == []
    assert "firefox" in caplog.text
    assert "Chromium is not installed" in caplog.text


async def test_without_playwright_udls_profile_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Playwright comes with udl's browser extra; udl works without it.
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    monkeypatch.delitem(sys.modules, "udl.plugins.downloaders.browser", raising=False)
    profile = tmp_path / "profile"
    profile.mkdir()

    with pytest.raises(ToolUnavailable, match="Playwright") as caught:
        await profile_cookies(URL, profile_dir=profile)
    with pytest.raises(ToolUnavailable):
        await browser_login(URL, profile_dir=profile)

    assert caught.value.hint is not None
    assert "--extra browser" in caught.value.hint


async def test_without_sources_there_are_no_auth_cookies() -> None:
    assert await AuthCookies().for_url(URL) == []


def test_auth_cookies_come_from_the_settings(tmp_path: Path) -> None:
    settings = Settings(cookies_from_browser="chrome:Profile 1")

    sources = AuthCookies.from_settings(settings)

    assert sources.browser == BrowserCookieSource.parse("chrome:Profile 1")
    assert sources.profile == settings.browser.profile_dir


def test_without_cookies_from_browser_only_udls_profile_is_used() -> None:
    sources = AuthCookies.from_settings(Settings())

    assert sources.browser is None


def test_an_unreadable_cookies_from_browser_setting_is_a_config_error() -> None:
    with pytest.raises(ConfigError):
        AuthCookies.from_settings(Settings(cookies_from_browser="netscape"))
