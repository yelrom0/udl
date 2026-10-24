"""
-----------------------
- test_antibot_site.py -
-----------------------

The anti-bot fixture site (tests/support/antibot.py) behaves as the sites it stands for do, to a
plain client (httpx) and to a browser (Chromium), and FakePerson gets past its check
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator

# Package Imports
import httpx
import pytest
from playwright.async_api import BrowserContext, Page, async_playwright

# Local Imports
from support.antibot import (
    AUTO_CLEARANCE,
    CHALLENGE_TITLE,
    CLEARANCE,
    HOSTS,
    JS_COOKIE,
    SESSION,
    AntiBotSite,
    FakePerson,
    Manner,
)
from support.browser import needs_chromium

PLAIN_AGENT = "udl-test/1.0"


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(headers={"User-Agent": PLAIN_AGENT}, timeout=10) as client:
        yield client


@pytest.fixture
async def browser() -> AsyncIterator[BrowserContext]:
    needs_chromium()
    async with async_playwright() as playwright:
        launched = await playwright.chromium.launch()
        context = await launched.new_context()
        yield context
        await launched.close()


def with_cookies(**cookies: str) -> dict[str, str]:
    """The Cookie header that sends `cookies`."""
    return {"Cookie": "; ".join(f"{name}={value}" for name, value in cookies.items())}


async def settled(page: Page, title: str) -> None:
    """Wait for `page` to show a page titled `title` (it may reload on its way there)."""
    async with asyncio.timeout(10):
        while await _title(page) != title:  # noqa: ASYNC110 - (a page has no event for it)
            await asyncio.sleep(0.05)


async def _title(page: Page) -> str:
    try:
        return await page.title()
    except Exception:  # noqa: BLE001 - (it is navigating)
        return ""


# ---------------------------------------------------------------------------------------------
# A plain client
# ---------------------------------------------------------------------------------------------


async def test_a_page_that_needs_javascript_is_a_500_to_a_plain_client(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    url = antibot.url("/plain-500/one")

    refused = await http.get(url)
    given = await http.get(url, headers=with_cookies(**{JS_COOKIE: "1"}))

    assert refused.status_code == 500
    assert given.status_code == 200
    assert "<title>Plain one</title>" in given.text


async def test_a_flaky_page_fails_a_few_times_then_answers(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    antibot.flaky = 2
    url = antibot.url("/flaky/x")

    statuses = [(await http.get(url)).status_code for _ in range(3)]

    assert statuses == [500, 500, 200]


async def test_the_check_turns_a_plain_client_away_with_cloudflares_markers(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    response = await http.get(antibot.url("/checked/page"))

    assert response.status_code == 403
    assert response.headers["cf-mitigated"] == "challenge"
    assert response.headers["server"] == "cloudflare"
    assert f"<title>{CHALLENGE_TITLE}</title>" in response.text
    assert "/cdn-cgi/challenge-platform/" in response.text


async def test_a_clearance_works_only_with_the_user_agent_it_was_given_to(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    url = antibot.url("/checked/page")
    token = antibot.cleared("Some Browser/1.0")

    other = await http.get(url, headers=with_cookies(**{CLEARANCE: token}))
    same = await http.get(
        url, headers={"User-Agent": "Some Browser/1.0", **with_cookies(**{CLEARANCE: token})}
    )

    assert other.status_code == 403
    assert same.status_code == 200
    assert "The page behind the check: page." in same.text


async def test_a_block_has_nothing_to_pass(antibot: AntiBotSite, http: httpx.AsyncClient) -> None:
    response = await http.get(antibot.url("/blocked"))

    assert response.status_code == 403
    assert "Access Denied" in response.text
    assert "Reference #" in response.text


async def test_slowing_down_says_when_to_come_back(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    antibot.slow_down, antibot.retry_after = 1, 3
    url = antibot.url("/slow-down/x")

    first = await http.get(url)
    then = await http.get(url)

    assert (first.status_code, first.headers["retry-after"]) == (429, "3")
    assert then.status_code == 200


async def test_a_page_built_by_javascript_is_an_empty_shell(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    response = await http.get(antibot.url("/built-by-js"))

    assert response.status_code == 200
    assert '<div id="app"></div>' in response.text
    assert "enable JavaScript" in response.text


async def test_members_pages_need_a_session(antibot: AntiBotSite, http: httpx.AsyncClient) -> None:
    url = antibot.url("/members/home")
    session = antibot.issue_session()

    refused = await http.get(url)
    given = await http.get(url, headers=with_cookies(**{SESSION: session}))

    assert refused.status_code == 401
    assert given.status_code == 200
    assert "For members only: home." in given.text


async def test_a_rotating_session_replaces_the_one_it_was_sent(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    antibot.rotate_sessions = True
    url = antibot.url("/members/home")
    old = antibot.issue_session()

    given = await http.get(url, headers=with_cookies(**{SESSION: old}))
    fresh = given.cookies[SESSION]
    http.cookies.clear()
    again = await http.get(url, headers=with_cookies(**{SESSION: old}))
    with_fresh = await http.get(url, headers=with_cookies(**{SESSION: fresh}))

    assert given.status_code == 200
    assert fresh != old
    assert again.status_code == 401
    assert with_fresh.status_code == 200


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/docs", "text/html; charset=utf-8"),
        ("/article", "text/html; charset=utf-8"),
        ("/data.json", "application/json"),
        ("/notes.txt", "text/plain; charset=utf-8"),
        ("/report.pdf", "application/pdf"),
        ("/cp1252", "text/html; charset=windows-1252"),
        ("/cp1252-meta", "text/html"),
    ],
)
async def test_the_content_pages_are_served_as_they_are(
    antibot: AntiBotSite, http: httpx.AsyncClient, path: str, content_type: str
) -> None:
    response = await http.get(antibot.url(path))

    assert response.status_code == 200
    assert response.headers["content-type"] == content_type
    if not content_type.startswith(("text/html", "application/json")):
        assert response.content == antibot.content(path)


async def test_a_page_in_windows_1252_is_in_windows_1252(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    response = await http.get(antibot.url("/cp1252-meta"))

    assert '<meta charset="windows-1252">' in response.content.decode("cp1252")
    assert "Caf\u00e9 \u2013 la carte".encode("cp1252") in response.content


async def test_each_host_is_a_site_with_cookies_of_its_own(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    session = antibot.issue_session()
    a, b = (antibot.url("/members/x", host) for host in HOSTS[:2])

    for_a = await http.get(a, headers=with_cookies(**{SESSION: session}))
    await http.get(b)

    assert for_a.status_code == 200
    assert [(visit.host, visit.status) for visit in antibot.visits("/members/x")] == [
        ("a.localhost", 200),
        ("b.localhost", 401),
    ]


async def test_the_site_records_what_each_client_sent(
    antibot: AntiBotSite, http: httpx.AsyncClient
) -> None:
    await http.get(antibot.url("/docs"), headers=with_cookies(x="1"))

    [visit] = antibot.visits("/docs")

    assert (visit.host, visit.status, visit.user_agent) == ("a.localhost", 200, PLAIN_AGENT)
    assert visit.cookies == {"x": "1"}
    assert visit.js is False


# ---------------------------------------------------------------------------------------------
# A browser
# ---------------------------------------------------------------------------------------------


async def test_a_browser_runs_the_script_and_gets_the_page(
    antibot: AntiBotSite, browser: BrowserContext
) -> None:
    page = await browser.new_page()

    await page.goto(antibot.url("/plain-500/one"))
    await settled(page, "Plain one")

    visits = antibot.visits("/plain-500/one")
    assert [visit.status for visit in visits] == [500, 200]
    # (The 500's own report is cut short by its reload.)
    assert visits[-1].cookies == {JS_COOKIE: "1"}
    async with asyncio.timeout(5):
        while not antibot.visits("/plain-500/one")[-1].js:  # noqa: ASYNC110
            await asyncio.sleep(0.05)


async def test_a_page_built_by_javascript_is_built_in_a_browser(
    antibot: AntiBotSite, browser: BrowserContext
) -> None:
    page = await browser.new_page()

    await page.goto(antibot.url("/built-by-js"))
    await settled(page, "Built")

    assert "Built by JavaScript" in await page.content()


async def test_a_person_gets_past_the_check(antibot: AntiBotSite, browser: BrowserContext) -> None:
    page = await browser.new_page()
    person = FakePerson()
    url = antibot.url("/checked/page")

    await page.goto(url)
    assert await page.title() == CHALLENGE_TITLE
    await person.attend(page)
    await settled(page, "Checked page")

    assert person.attended == [url]
    cookie = next(c for c in await browser.cookies() if c.get("name") == CLEARANCE)
    agent = await page.evaluate("() => navigator.userAgent")
    assert antibot.visits("/checked/page")[-1].user_agent == agent
    # (The clearance is bound to the browser's User-Agent.)
    assert cookie.get("httpOnly") is True


async def test_a_person_who_ignores_the_check_leaves_it(
    antibot: AntiBotSite, browser: BrowserContext
) -> None:
    page = await browser.new_page()
    await page.goto(antibot.url("/checked/page"))

    await FakePerson(Manner.IGNORES).attend(page)
    await asyncio.sleep(0.3)

    assert await page.title() == CHALLENGE_TITLE


async def test_a_person_who_closes_the_window_closes_it(
    antibot: AntiBotSite, browser: BrowserContext
) -> None:
    page = await browser.new_page()
    await page.goto(antibot.url("/checked/page"))
    closed = asyncio.Event()
    browser.on("close", lambda _: closed.set())

    await FakePerson(Manner.CLOSES).attend(page)

    await asyncio.wait_for(closed.wait(), timeout=5)


async def test_a_check_that_clears_itself_clears_itself_in_a_browser(
    antibot: AntiBotSite, browser: BrowserContext
) -> None:
    page = await browser.new_page()

    await page.goto(antibot.url("/auto-checked/page"))
    await settled(page, "Auto-checked page")

    assert any(c.get("name") == AUTO_CLEARANCE for c in await browser.cookies())


async def test_a_browser_is_turned_away_by_the_block_too(
    antibot: AntiBotSite, browser: BrowserContext
) -> None:
    page = await browser.new_page()

    response = await page.goto(antibot.url("/blocked"))

    assert response is not None
    assert response.status == 403
    assert await page.title() == "Access Denied"
