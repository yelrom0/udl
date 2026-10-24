"""
------------------
- test_ladder.py -
------------------

The ladder (udl.fetch.ladder.Fetcher), each rule with fake rungs on a FakeClock: when a page is
tried again, when the ladder climbs and when it stops, how long it waits, what it remembers of a
site, and what each try was
"""

# System Imports
import asyncio
import http.cookiejar
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

# Package Imports
import pytest
from pydantic import SecretStr, ValidationError

# Local Imports
from support.clock import T0, FakeClock
from support.engine import databases, registry, settings
from udl.config import FetchSettings
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.errors import Cancelled, DownloadError, ErrorCode, NetworkError
from udl.fetch.challenges import ChallengeKind
from udl.fetch.ladder import (
    BROWSER_HEADERS,
    Fetcher,
    FetchOptions,
    FetchOutcome,
    FetchSite,
    FetchSites,
    Snapshot,
    Via,
)
from udl.plugins.downloaders.base import CancelToken, Cookie

PAGE_URL = "https://a.example/page"
SITE = "a.example"
UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/153.0.0.0 Safari/537.36"


def page(title: str = "A page", status: int = 200, **headers: str) -> Snapshot:
    words = " ".join(["Words of the page itself, as a person reads them."] * 5)
    body = f"<html><head><title>{title}</title></head><body><p>{words}</p></body></html>"
    return Snapshot(
        url=PAGE_URL,
        status=status,
        headers={"content-type": "text/html; charset=utf-8", **headers},
        body=body.encode(),
    )


def status(code: int, **headers: str) -> Snapshot:
    return page(f"HTTP {code}", code, **headers)


CHECK = Snapshot(
    url=PAGE_URL,
    status=403,
    headers={"cf-mitigated": "challenge", "content-type": "text/html"},
    body=b"<title>Just a moment...</title><button>I am human</button>",
)
BLOCK = Snapshot(
    url=PAGE_URL,
    status=403,
    headers={"content-type": "text/html", "server": "AkamaiGHost"},
    body=b"<title>Access Denied</title><p>Access Denied. Reference #18.4c1e.17613.2a9</p>",
)
SHELL = Snapshot(
    url=PAGE_URL,
    status=200,
    headers={"content-type": "text/html"},
    body=b'<title>App</title><div id="app"></div><script src="/app.js"></script>',
)

type Answer = Snapshot | DownloadError


class FakeStatic:
    """The static rungs' requests: each URL's answers in turn (the last one again and again),
    and what each request sent, when."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.answers: dict[str, list[Answer]] = {}
        self.asked: list[tuple[datetime, dict[str, str], dict[str, str]]] = []
        self.sets: dict[str, str] = {}  # cookies each answer sets

    def script(self, *answers: Answer, url: str = PAGE_URL) -> None:
        self.answers[url] = list(answers)

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        jar: http.cookiejar.CookieJar,
        cancel: CancelToken,
    ) -> Snapshot:
        self.asked.append((self.clock.now(), dict(headers), {c.name: c.value or "" for c in jar}))
        for name, value in self.sets.items():
            jar.set_cookie(Cookie(name=name, value=SecretStr(value), domain=SITE).to_http_cookie())
        answers = self.answers.get(url) or [status(404)]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(answer, DownloadError):
            raise answer
        return answer


class FakeTab:
    def __init__(self, tabs: FakeTabs) -> None:
        self.tabs = tabs
        self.shown: list[Answer] = []

    def _next(self) -> Snapshot:
        answer = self.shown.pop(0) if len(self.shown) > 1 else self.shown[0]
        if isinstance(answer, DownloadError):
            raise answer
        return answer

    async def goto(self, url: str, *, wait_for: str | None) -> Snapshot:
        self.tabs.visits.append((url, wait_for))
        loads = self.tabs.loads.get(url) or [[status(404)]]
        self.shown = list(loads.pop(0) if len(loads) > 1 else loads[0])
        return self._next()

    async def look(self) -> Snapshot:
        self.tabs.looks += 1
        return self._next()


class FakeTabs:
    """The browser rung's tabs: each URL's loads in turn (a load: what the page shows when it
    has loaded, then each time it is looked at again)."""

    def __init__(self) -> None:
        self.user_agent: str | None = None
        self.loads: dict[str, list[list[Answer]]] = {}
        self.visits: list[tuple[str, str | None]] = []
        self.headers: list[dict[str, str]] = []
        self.looks = 0

    def script(self, *loads: list[Answer], url: str = PAGE_URL) -> None:
        self.loads[url] = list(loads)

    @asynccontextmanager
    async def tab(
        self, *, headers: Mapping[str, str], cancel: CancelToken
    ) -> AsyncGenerator[FakeTab]:
        self.headers.append(dict(headers))
        yield FakeTab(self)


class FakeJar:
    def __init__(self) -> None:
        self.cookies: list[Cookie] = []
        self.kept: list[tuple[str, list[str], list[str]]] = []

    async def cookies_for(self, url: str) -> list[Cookie]:
        return [cookie for cookie in self.cookies if cookie.matches(url)]

    async def keep(self, url: str, sent: list[Cookie], now: list[Cookie]) -> None:
        self.kept.append((url, sorted(c.name for c in sent), sorted(c.name for c in now)))


class Ladder:
    """A Fetcher on the fakes, and a way to run a fetch while moving the clock on."""

    def __init__(self, db: DBManager, **fields: object) -> None:
        self.clock = FakeClock()
        self.settings = FetchSettings.model_validate(fields)
        self.static = FakeStatic(self.clock)
        self.tabs = FakeTabs()
        self.jar = FakeJar()
        self.sites = FetchSites(db, self.clock)
        self.fetcher = Fetcher(
            self.settings,
            static=self.static,
            jar=self.jar,
            tabs=self.tabs,
            sites=self.sites,
            clock=self.clock,
        )

    async def fetch(
        self, url: str = PAGE_URL, *, over: timedelta = timedelta(minutes=10), **options: object
    ) -> FetchOutcome:
        task = asyncio.create_task(
            self.fetcher.fetch(
                URL.parse(url), FetchOptions.model_validate(options), cancel=CancelToken()
            )
        )
        await self.clock.advance(over)
        return await task

    async def site(self) -> FetchSite | None:
        return await self.sites.get(SITE)


@pytest.fixture
async def ladder(tmp_path_factory: pytest.TempPathFactory) -> AsyncGenerator[Ladder]:
    config = settings(tmp_path_factory.mktemp("ladder"))
    async with databases(config, registry()) as db:
        yield Ladder(db)


def rungs(outcome: FetchOutcome) -> list[str]:
    return [attempt.rung for attempt in outcome.attempts]


def cookie(name: str = "session") -> Cookie:
    return Cookie(name=name, value=SecretStr("v"), domain=SITE)


# ---------------------------------------------------------------------------------------------
# Climbing
# ---------------------------------------------------------------------------------------------


async def test_a_page_a_plain_request_gets_is_the_answer(ladder: Ladder) -> None:
    ladder.static.script(page("Hello"))

    outcome = await ladder.fetch()

    assert outcome.page is not None
    assert (outcome.via, outcome.page.title, outcome.page.via) == (Via.STATIC, "Hello", "static")
    assert rungs(outcome) == ["static"]
    assert outcome.error is None


async def test_a_plain_request_sends_a_browsers_headers(ladder: Ladder) -> None:
    ladder.static.script(page())

    await ladder.fetch(headers={"X-Extra": "1"})

    _, headers, cookies = ladder.static.asked[0]
    assert headers == {**BROWSER_HEADERS, "User-Agent": headers["User-Agent"], "X-Extra": "1"}
    assert "Chrome/" in headers["User-Agent"]
    assert cookies == {}


async def test_the_browsers_own_user_agent_is_sent_once_it_is_known(ladder: Ladder) -> None:
    ladder.tabs.user_agent = UA
    ladder.static.script(page())

    await ladder.fetch()

    assert ladder.static.asked[0][1]["User-Agent"] == UA


async def test_a_5xx_is_tried_once_more_a_second_later_then_the_next_rung(ladder: Ladder) -> None:
    ladder.jar.cookies = [cookie()]
    ladder.static.script(status(500), status(502), status(503), status(500))
    ladder.tabs.script([page("In the browser")])

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "static", "cookies", "cookies", "browser"]
    assert [when - T0 for when, _, _ in ladder.static.asked] == [
        timedelta(0),
        timedelta(seconds=1),
        timedelta(seconds=1),
        timedelta(seconds=2),
    ]
    assert outcome.via is Via.BROWSER
    assert [attempt.status for attempt in outcome.attempts] == [500, 502, 503, 500, 200]


async def test_a_dropped_connection_is_tried_once_more_too(ladder: Ladder) -> None:
    ladder.static.script(NetworkError("Connection reset"), page())

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "static"]
    assert outcome.via is Via.STATIC
    assert outcome.attempts[0].error is not None
    assert outcome.attempts[0].error.code is ErrorCode.NETWORK_ERROR
    assert outcome.attempts[0].error.tool == "static"


async def test_a_login_wall_goes_to_the_next_rung_at_once(ladder: Ladder) -> None:
    ladder.jar.cookies = [cookie()]
    ladder.static.script(status(401), page("Signed in"))

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "cookies"]
    assert [when for when, _, _ in ladder.static.asked] == [T0, T0]
    assert ladder.static.asked[1][2] == {"session": "v"}
    assert outcome.via is Via.COOKIES


async def test_the_cookies_rung_is_skipped_for_a_site_without_cookies(ladder: Ladder) -> None:
    ladder.static.script(status(403))
    ladder.tabs.script([page()])

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "browser"]


async def test_the_cookies_rung_keeps_what_the_site_set(ladder: Ladder) -> None:
    ladder.jar.cookies = [cookie()]
    ladder.static.script(status(401), page())
    ladder.static.sets = {"rotated": "2"}

    await ladder.fetch()

    assert ladder.jar.kept == [(PAGE_URL, ["session"], ["rotated", "session"])]


async def test_a_404_is_the_end(ladder: Ladder) -> None:
    ladder.static.script(status(404))

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static"]
    assert outcome.page is None
    assert outcome.error is not None
    assert outcome.error.code is ErrorCode.NOT_FOUND
    assert ladder.tabs.visits == []


async def test_a_429_waits_its_retry_after_and_tries_the_same_rung(ladder: Ladder) -> None:
    ladder.static.script(status(429, **{"retry-after": "30"}), page())

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "static"]
    assert ladder.static.asked[1][0] - T0 == timedelta(seconds=30)
    assert outcome.via is Via.STATIC


async def test_a_429_waits_no_longer_than_max_retry_after(ladder: Ladder) -> None:
    ladder.static.script(status(429, **{"retry-after": "3600"}), page())

    await ladder.fetch()

    assert ladder.static.asked[1][0] - T0 == timedelta(minutes=2)


async def test_a_site_that_keeps_limiting_is_given_up_on_after_two_waits(ladder: Ladder) -> None:
    ladder.static.script(status(429, **{"retry-after": "5"}))

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "static", "static"]
    assert outcome.error is not None
    assert outcome.error.code is ErrorCode.RATE_LIMITED
    assert ladder.tabs.visits == []


async def test_a_javascript_shell_goes_straight_to_the_browser(ladder: Ladder) -> None:
    ladder.jar.cookies = [cookie()]
    ladder.static.script(SHELL)
    ladder.tabs.script([page("Built")])

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "browser"]
    assert outcome.attempts[0].note == "a JavaScript shell"
    assert outcome.page is not None
    assert outcome.page.title == "Built"


async def test_a_javascript_shell_in_the_browser_is_the_page(ladder: Ladder) -> None:
    ladder.tabs.script([SHELL])

    outcome = await ladder.fetch(via="browser")

    assert outcome.via is Via.BROWSER


# ---------------------------------------------------------------------------------------------
# Checks and blocks
# ---------------------------------------------------------------------------------------------


async def test_a_check_goes_to_the_next_rung_at_once(ladder: Ladder) -> None:
    ladder.jar.cookies = [cookie()]
    ladder.static.script(CHECK, page())

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "cookies"]
    first = outcome.attempts[0]
    assert (first.challenge, first.passable, first.status) == (ChallengeKind.CLOUDFLARE, True, 403)
    assert first.evidence


async def test_a_check_that_clears_itself_in_the_browser_needs_no_person(ladder: Ladder) -> None:
    ladder.static.script(CHECK)
    ladder.tabs.script([CHECK, CHECK, page("Cleared")])

    outcome = await ladder.fetch()

    assert outcome.page is not None
    assert (outcome.via, outcome.page.title) == (Via.BROWSER, "Cleared")
    assert outcome.challenge is None
    assert ladder.tabs.looks == 2


async def test_a_check_that_does_not_clear_within_its_grace_waits_for_a_person(
    ladder: Ladder,
) -> None:
    ladder.static.script(CHECK)
    ladder.tabs.script([CHECK])

    outcome = await ladder.fetch()

    assert outcome.page is None
    assert outcome.error is None
    assert outcome.challenge is not None
    assert (outcome.challenge.kind, outcome.challenge.passable) == (ChallengeKind.CLOUDFLARE, True)
    # (Looked at every half a second for its 15 s of grace.)
    assert ladder.tabs.looks == 30


async def test_a_block_in_the_browser_is_auth_required_saying_what_may_help(ladder: Ladder) -> None:
    ladder.static.script(BLOCK)
    ladder.tabs.script([BLOCK])

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "browser"]
    assert outcome.attempts[0].passable is False
    assert outcome.challenge is None
    assert outcome.error is not None
    assert outcome.error.code is ErrorCode.AUTH_REQUIRED
    assert outcome.error.hint is not None
    assert "cookies" in outcome.error.hint
    assert ladder.tabs.looks == 0  # (a block does not clear itself)


async def test_the_browser_failing_twice_is_the_end(ladder: Ladder) -> None:
    ladder.tabs.script([NetworkError("net::ERR_CONNECTION_RESET")])

    outcome = await ladder.fetch(via="browser")

    assert rungs(outcome) == ["browser", "browser"]
    assert outcome.error is not None
    assert outcome.error.code is ErrorCode.NETWORK_ERROR


async def test_the_browser_gets_the_requests_headers_and_waits_for_its_selector(
    ladder: Ladder,
) -> None:
    ladder.tabs.script([page()])

    await ladder.fetch(via="browser", wait_for="#ready", headers={"X-Extra": "1"})

    assert ladder.tabs.visits == [(PAGE_URL, "#ready")]
    assert ladder.tabs.headers == [{"X-Extra": "1"}]


async def test_a_check_met_is_remembered_for_its_site(ladder: Ladder) -> None:
    ladder.static.script(CHECK)
    ladder.tabs.script([CHECK, page()])

    await ladder.fetch()

    known = await ladder.site()
    assert known is not None
    assert (known.last_challenge, known.challenged_at) == (
        ChallengeKind.CLOUDFLARE,
        T0 + timedelta(milliseconds=500),
    )


# ---------------------------------------------------------------------------------------------
# Remembering where to start
# ---------------------------------------------------------------------------------------------


async def test_the_rung_that_worked_is_where_the_next_fetch_starts(ladder: Ladder) -> None:
    ladder.static.script(status(403))
    ladder.tabs.script([page()])
    await ladder.fetch()

    again = await ladder.fetch()

    assert rungs(again) == ["browser"]
    known = await ladder.site()
    assert known is not None
    assert (known.start_at, known.learned_at) == (Via.BROWSER, T0)


async def test_the_bottom_rung_is_tried_again_after_relearn_after(ladder: Ladder) -> None:
    ladder.static.script(status(403))
    ladder.tabs.script([page()])
    await ladder.fetch()
    ladder.static.script(page())  # (the site stopped blocking)
    await ladder.clock.advance(timedelta(days=7))

    again = await ladder.fetch()

    assert rungs(again) == ["static"]
    known = await ladder.site()
    assert known is not None
    assert (known.start_at, known.learned_at) == (Via.STATIC, T0 + timedelta(days=7, minutes=10))


async def test_relearning_starts_the_clock_again_when_the_same_rung_works(ladder: Ladder) -> None:
    ladder.static.script(status(403))
    ladder.tabs.script([page()])
    await ladder.fetch()
    await ladder.clock.advance(timedelta(days=8))

    again = await ladder.fetch()

    assert rungs(again) == ["static", "browser"]
    known = await ladder.site()
    assert known is not None
    assert known.learned_at == T0 + timedelta(days=8, minutes=10)


async def test_a_rung_the_caller_names_is_where_it_starts_whatever_was_learned(
    ladder: Ladder,
) -> None:
    ladder.static.script(status(403), page())
    ladder.tabs.script([page()])
    await ladder.fetch()  # (learns the browser)

    named = await ladder.fetch(via="static")

    assert rungs(named) == ["static"]
    known = await ladder.site()
    assert known is not None
    assert known.start_at is Via.BROWSER  # (what the caller chose teaches nothing)


async def test_a_named_rung_that_climbs_teaches_where_the_page_came(ladder: Ladder) -> None:
    ladder.static.script(status(403))
    ladder.tabs.script([page()])

    await ladder.fetch(via="static")

    known = await ladder.site()
    assert known is not None
    assert known.start_at is Via.BROWSER


async def test_a_user_agent_imported_with_cookies_is_sent_with_them(ladder: Ladder) -> None:
    await ladder.sites.update(SITE, user_agent="Their Browser/1.0")
    ladder.static.script(status(403), page())

    outcome = await ladder.fetch()

    assert rungs(outcome) == ["static", "cookies"]
    assert [headers["User-Agent"] for _, headers, _ in ladder.static.asked][
        1
    ] == "Their Browser/1.0"


# ---------------------------------------------------------------------------------------------
# The options
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("header", ["Cookie", "authorization", "Proxy-Authorization"])
def test_a_fetch_never_sends_secrets_in_its_headers(header: str) -> None:
    with pytest.raises(ValidationError, match="import_cookies"):
        FetchOptions(headers={header: "x"})


def test_a_fetch_does_not_start_with_a_person() -> None:
    with pytest.raises(ValidationError, match="not person"):
        FetchOptions(via=Via.PERSON)


async def test_a_cancelled_fetch_stops(ladder: Ladder) -> None:
    cancel = CancelToken()
    cancel.cancel()

    with pytest.raises(Cancelled):
        await ladder.fetcher.fetch(URL.parse(PAGE_URL), FetchOptions(), cancel=cancel)
