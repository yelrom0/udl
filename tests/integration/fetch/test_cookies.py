"""
-------------------
- test_cookies.py -
-------------------

Cookies, both ways, in a real browser profile (udl.fetch.cookies, udl.fetch.browser): imported
cookies get past the anti-bot site's login wall plainly and in the browser, what the site sets on
a plain request is what the browser sends next, and two udl processes take turns with the
profile
"""

# System Imports
import logging
import subprocess
import sys
import time
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.antibot import SESSION, AntiBotSite
from support.browser import needs_chromium
from support.engine import databases, registry, settings
from udl.clock import SystemClock
from udl.config import FetchSettings
from udl.core.urls import URL
from udl.fetch.browser import ChromiumTabs, ProfileCookies
from udl.fetch.cookies import CookieStore, SessionJar
from udl.fetch.ladder import Fetcher, FetchOptions, FetchOutcome, FetchSites, HttpStatic, Via
from udl.plugins.downloaders.auth import AuthCookies
from udl.plugins.downloaders.base import CancelToken
from udl.plugins.downloaders.browser import chromium, profile_lock

_HOLD = """
import fcntl, sys, time
held = open(sys.argv[1], "a")
fcntl.flock(held, fcntl.LOCK_EX)
print("held", flush=True)
time.sleep(float(sys.argv[2]))
"""


class Kit:
    """A Fetcher whose cookies rung and browser use one browser profile, and its cookies."""

    def __init__(self, profile_dir: Path, sites: FetchSites) -> None:
        clock = SystemClock()
        fetch = FetchSettings(timeout=timedelta(seconds=15), challenge_grace=timedelta(seconds=2))
        self.profile = ProfileCookies(profile_dir)
        self.store = CookieStore(self.profile, sites)
        self.jar = SessionJar(AuthCookies(profile=profile_dir, reader=self.profile), self.profile)
        self.fetcher = Fetcher(
            fetch,
            static=HttpStatic(fetch),
            jar=self.jar,
            tabs=ChromiumTabs(profile_dir=profile_dir, timeout=fetch.timeout, clock=clock),
            sites=sites,
            clock=clock,
        )

    async def fetch(self, url: str, **options: object) -> FetchOutcome:
        return await self.fetcher.fetch(
            URL.parse(url), FetchOptions.model_validate(options), cancel=CancelToken()
        )


@pytest.fixture
async def kit(tmp_path: Path) -> AsyncGenerator[Kit]:
    needs_chromium()
    async with databases(settings(tmp_path), registry()) as db:
        yield Kit(tmp_path / "profile", FetchSites(db, SystemClock()))


async def test_imported_cookies_get_past_a_login_wall_plainly_and_in_the_browser(
    kit: Kit, antibot: AntiBotSite
) -> None:
    await kit.store.import_cookies(
        f"Cookie: {SESSION}={antibot.issue_session()}", site="a.localhost"
    )

    plainly = await kit.fetch(antibot.url("/members/one"))
    in_the_browser = await kit.fetch(antibot.url("/members/two"), via="browser")

    assert plainly.via is Via.COOKIES
    assert in_the_browser.via is Via.BROWSER
    assert [visit.status for visit in antibot.visits("/members/two")] == [200]


async def test_a_session_the_site_rotates_on_a_plain_request_is_the_one_the_browser_sends_next(
    kit: Kit, antibot: AntiBotSite
) -> None:
    antibot.rotate_sessions = True
    first = antibot.issue_session()
    await kit.store.import_cookies(f"{SESSION}={first}", site="a.localhost")

    plainly = await kit.fetch(antibot.url("/members/one"))
    in_the_browser = await kit.fetch(antibot.url("/members/two"), via="browser")

    assert (plainly.via, in_the_browser.via) == (Via.COOKIES, Via.BROWSER)
    rotated = antibot.visits("/members/one")[-1]
    sent = antibot.visits("/members/two")[-1]
    assert rotated.cookies[SESSION] == first
    assert sent.cookies[SESSION] != first
    assert sent.status == 200


async def test_what_the_browser_sets_is_what_plain_requests_send_next(
    kit: Kit, antibot: AntiBotSite
) -> None:
    await kit.fetch(antibot.url("/plain-500/one"))  # (its script sets a cookie in the browser)

    plainly = await kit.fetch(antibot.url("/plain-500/two"), via="cookies")

    assert plainly.via is Via.COOKIES
    assert antibot.visits("/plain-500/two")[-1].cookies == {"js_ok": "1"}


async def test_listing_cookies_shows_each_sites_without_their_values(
    kit: Kit, antibot: AntiBotSite
) -> None:
    secret = antibot.issue_session()
    await kit.store.import_cookies(f"{SESSION}={secret}", site="a.localhost", user_agent="UA/1")

    [listed] = await kit.store.list_cookies()

    assert (listed.site, listed.user_agent, [c.name for c in listed.cookies]) == (
        "a.localhost",
        "UA/1",
        [SESSION],
    )
    assert secret not in listed.model_dump_json()


async def test_deleting_a_sites_cookies_leaves_the_others(kit: Kit) -> None:
    await kit.store.import_cookies("a=1", site="a.localhost")
    await kit.store.import_cookies("b=2", site="b.localhost")

    assert await kit.store.delete_cookies("a.localhost") is True

    assert [listed.site for listed in await kit.store.list_cookies()] == ["b.localhost"]


async def test_a_profile_never_used_has_no_cookies_and_starts_no_browser(tmp_path: Path) -> None:
    profile = ProfileCookies(tmp_path / "never")

    assert await profile.cookies() == []
    assert await profile.clear("a.localhost") == 0
    assert not (tmp_path / "never").exists()


async def test_two_processes_take_turns_with_the_profile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    needs_chromium()
    profile_dir = tmp_path / "profile"
    with subprocess.Popen(  # noqa: S603, ASYNC220 - this Python, running fixed code
        [sys.executable, "-c", _HOLD, str(profile_lock(profile_dir)), "1.5"],
        stdout=subprocess.PIPE,
        text=True,
    ) as holder:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()

        with caplog.at_level(logging.WARNING):
            async with chromium(profile_dir=profile_dir, headless=True, cancel=CancelToken()):
                waited = time.monotonic() - started

    assert waited >= 1.0
    assert "another udl" in caplog.text.lower()
