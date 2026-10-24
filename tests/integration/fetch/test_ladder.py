"""
------------------
- test_ladder.py -
------------------

The ladder (udl.fetch.ladder) against the anti-bot fixture site, with real requests and a real
Chromium: what each kind of site takes, and what is remembered of it
"""

# System Imports
from collections.abc import AsyncGenerator
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.antibot import SESSION, AntiBotSite
from support.browser import needs_chromium
from support.engine import databases, registry, settings
from udl.clock import SystemClock
from udl.config import FetchSettings
from udl.core.urls import URL
from udl.errors import ErrorCode
from udl.fetch.browser import ChromiumTabs
from udl.fetch.challenges import ChallengeKind
from udl.fetch.content import PageFormat, PageText
from udl.fetch.ladder import Fetcher, FetchOptions, FetchOutcome, FetchSites, HttpStatic, Via
from udl.plugins.downloaders.base import CancelToken, Cookie


class Jar:
    """The site's cookies: those given, and what sites set on them."""

    def __init__(self) -> None:
        self.cookies: list[Cookie] = []

    async def cookies_for(self, url: str) -> list[Cookie]:
        return [cookie for cookie in self.cookies if cookie.matches(url)]

    async def keep(self, url: str, sent: list[Cookie], now: list[Cookie]) -> None:
        self.cookies = now


class Ladder:
    def __init__(self, fetcher: Fetcher, jar: Jar) -> None:
        self.fetcher = fetcher
        self.jar = jar

    async def fetch(self, url: str, **options: object) -> FetchOutcome:
        return await self.fetcher.fetch(
            URL.parse(url), FetchOptions.model_validate(options), cancel=CancelToken()
        )


@pytest.fixture
async def ladder(tmp_path: Path) -> AsyncGenerator[Ladder]:
    needs_chromium()
    config = settings(tmp_path)
    fetch = FetchSettings(
        challenge_grace=timedelta(seconds=3), timeout=timedelta(seconds=15), max_page=64 * 1024
    )
    clock = SystemClock()
    jar = Jar()
    async with databases(config, registry()) as db:
        yield Ladder(
            Fetcher(
                fetch,
                static=HttpStatic(fetch),
                jar=jar,
                tabs=ChromiumTabs(
                    profile_dir=tmp_path / "profile", timeout=fetch.timeout, clock=clock
                ),
                sites=FetchSites(db, clock),
                clock=clock,
            ),
            jar,
        )


def rungs(outcome: FetchOutcome) -> list[str]:
    return [attempt.rung for attempt in outcome.attempts]


def markdown(outcome: FetchOutcome) -> str:
    assert outcome.page is not None, outcome.error
    return PageText.render(outcome.page, PageFormat.MARKDOWN).content or ""


async def test_a_page_a_plain_request_gets_is_read_plainly(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/docs"))

    assert (rungs(outcome), outcome.via) == (["static"], Via.STATIC)
    assert outcome.page is not None
    assert outcome.page.title == "Widgets: a guide"
    assert markdown(outcome).startswith("# Widgets: a guide")


async def test_a_page_that_needs_javascript_is_read_in_the_browser_after_a_try_and_a_retry(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/plain-500/one"))

    assert rungs(outcome) == ["static", "static", "browser"]
    assert outcome.via is Via.BROWSER
    assert "# Plain one" in markdown(outcome)
    assert [visit.status for visit in antibot.visits("/plain-500/one")] == [500, 500, 500, 200]


async def test_the_next_fetch_on_that_host_starts_in_the_browser(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    await ladder.fetch(antibot.url("/plain-500/one"))

    again = await ladder.fetch(antibot.url("/plain-500/two"))

    assert rungs(again) == ["browser"]
    assert "# Plain two" in markdown(again)
    other = await ladder.fetch(antibot.url("/docs", "b.localhost"))
    assert rungs(other) == ["static"]  # (another site: nothing learned of it)


async def test_a_flaky_page_is_read_on_its_retry(ladder: Ladder, antibot: AntiBotSite) -> None:
    antibot.flaky = 1

    outcome = await ladder.fetch(antibot.url("/flaky/x"))

    assert (rungs(outcome), outcome.via) == (["static", "static"], Via.STATIC)


async def test_a_429_is_waited_out_on_the_same_rung(ladder: Ladder, antibot: AntiBotSite) -> None:
    antibot.slow_down, antibot.retry_after = 1, 1

    outcome = await ladder.fetch(antibot.url("/slow-down/x"))

    assert (rungs(outcome), outcome.via) == (["static", "static"], Via.STATIC)
    assert outcome.attempts[0].status == 429


async def test_a_check_that_clears_itself_needs_no_person(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/auto-checked/x"))

    assert rungs(outcome) == ["static", "browser"]
    assert outcome.challenge is None
    assert "Cleared by itself." in markdown(outcome)
    assert outcome.attempts[0].challenge is ChallengeKind.CLOUDFLARE


async def test_a_check_that_wants_a_person_waits_for_one(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/checked/x"))

    assert rungs(outcome) == ["static", "browser"]
    assert outcome.page is None
    assert outcome.challenge is not None
    assert (outcome.challenge.kind, outcome.challenge.passable) == (ChallengeKind.CLOUDFLARE, True)


async def test_a_block_is_auth_required_saying_what_may_help(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/blocked"))

    assert rungs(outcome) == ["static", "browser"]
    assert outcome.error is not None
    assert outcome.error.code is ErrorCode.AUTH_REQUIRED
    assert outcome.attempts[0].challenge is ChallengeKind.AKAMAI


async def test_a_page_built_by_javascript_is_read_in_the_browser(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/built-by-js"))

    assert rungs(outcome) == ["static", "browser"]
    assert outcome.attempts[0].note == "a JavaScript shell"
    assert "Built by JavaScript" in markdown(outcome)


async def test_a_page_that_is_gone_is_the_end(ladder: Ladder, antibot: AntiBotSite) -> None:
    outcome = await ladder.fetch(antibot.url("/no-such-page"))

    assert rungs(outcome) == ["static"]
    assert outcome.error is not None
    assert outcome.error.code is ErrorCode.NOT_FOUND


async def test_a_login_wall_is_passed_with_the_sites_cookies(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    session = antibot.issue_session()
    ladder.jar.cookies = [Cookie(name=SESSION, value=SecretStr(session), domain="a.localhost")]

    outcome = await ladder.fetch(antibot.url("/members/home"))

    assert (rungs(outcome), outcome.via) == (["static", "cookies"], Via.COOKIES)
    assert "For members only: home." in markdown(outcome)


async def test_what_the_site_sets_on_the_cookies_rung_is_kept(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    antibot.rotate_sessions = True
    old = antibot.issue_session()
    ladder.jar.cookies = [Cookie(name=SESSION, value=SecretStr(old), domain="a.localhost")]

    await ladder.fetch(antibot.url("/members/one"))
    again = await ladder.fetch(antibot.url("/members/two"))

    assert again.via is Via.COOKIES
    assert [cookie.value.get_secret_value() != old for cookie in ladder.jar.cookies] == [True]


async def test_a_body_is_cut_at_max_page(ladder: Ladder, antibot: AntiBotSite) -> None:
    ladder.fetcher.static = HttpStatic(FetchSettings(max_page=100))

    outcome = await ladder.fetch(antibot.url("/docs"))

    assert outcome.page is not None
    assert (len(outcome.page.body), outcome.page.truncated) == (100, True)


async def test_json_in_the_browser_is_read_as_it_was_served(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/data.json"), via="browser")

    assert outcome.page is not None
    assert outcome.page.content_type == "application/json"
    assert outcome.page.body.startswith(b'{"name":"widget"')


async def test_a_file_in_the_browser_is_read_as_it_was_served(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/report.pdf"), via="browser")

    assert outcome.page is not None
    assert outcome.page.body == antibot.content("/report.pdf")


async def test_the_browser_waits_for_what_it_is_told_to(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    outcome = await ladder.fetch(antibot.url("/built-by-js"), via="browser", wait_for="#app h1")

    assert "Built by JavaScript" in markdown(outcome)


async def test_what_never_comes_is_extraction_failed(ladder: Ladder, antibot: AntiBotSite) -> None:
    ladder.fetcher.tabs = ChromiumTabs(
        profile_dir=None, timeout=timedelta(seconds=1), clock=SystemClock()
    )

    outcome = await ladder.fetch(antibot.url("/docs"), via="browser", wait_for="#never")

    assert outcome.error is not None
    assert outcome.error.code is ErrorCode.EXTRACTION_FAILED
    assert "#never" in outcome.error.message


async def test_the_browsers_user_agent_is_what_plain_requests_send_once_it_is_known(
    ladder: Ladder, antibot: AntiBotSite
) -> None:
    await ladder.fetch(antibot.url("/built-by-js"))

    await ladder.fetch(antibot.url("/docs", "b.localhost"))

    [plain] = antibot.visits("/docs", "b.localhost")
    browser = antibot.visits("/built-by-js")[-1]
    assert plain.user_agent == browser.user_agent.replace("HeadlessChrome/", "Chrome/")
    assert "HeadlessChrome" not in plain.user_agent
