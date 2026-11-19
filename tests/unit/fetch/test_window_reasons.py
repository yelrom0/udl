"""
--------------------------
- test_window_reasons.py -
--------------------------

Why udl's window has not seen a site's check passed (udl.fetch.window.not_passed_because): each
look's reason, on a fake page and tab, and None when the tab shows the site's own page. The
window ends by saying the last one for each site it left, so a run that left a site says why
"""

# System Imports
from dataclasses import dataclass, field
from datetime import timedelta

# Package Imports
import pytest
from playwright.async_api import Error as PlaywrightError

# Local Imports
from udl.fetch.ladder import Snapshot
from udl.fetch.window import not_passed_because

SITE = "a.localhost"
URL = "http://a.localhost:8080/checked/1"
HTML = {"content-type": "text/html; charset=utf-8"}
PAGE = Snapshot(url=URL, status=200, headers=HTML, body=b"<html><title>Checked 1</title></html>")
# (Cloudflare's check, by its header: what a tab shows until the person passes it.)
CHECK = Snapshot(
    url=URL,
    status=403,
    headers={**HTML, "cf-mitigated": "challenge"},
    body=b"<html><title>Just a moment...</title></html>",
)


@dataclass
class FakePage:
    url: str = URL
    closed: bool = False
    state: str = "complete"
    navigating: bool = False

    def is_closed(self) -> bool:
        return self.closed

    async def evaluate(self, expression: str) -> object:
        if self.navigating:
            raise PlaywrightError("Execution context was destroyed")
        return self.state


@dataclass
class FakeTab:
    quiet: bool = True
    busy: list[str] = field(default_factory=list[str])
    shows: Snapshot = PAGE

    def quiet_for(self, delay: timedelta) -> bool:
        return self.quiet

    def inflight(self) -> list[str]:
        return self.busy

    async def look(self) -> Snapshot:
        return self.shows


async def test_the_sites_own_page_has_passed() -> None:
    assert await not_passed_because(FakePage(), FakeTab(), SITE) is None


@pytest.mark.parametrize(
    ("page", "tab", "reason"),
    [
        (FakePage(closed=True), FakeTab(), "its tab was closed"),
        (FakePage(url="about:blank"), FakeTab(), "its tab is on about:blank, not the site"),
        (
            FakePage(url="http://b.localhost:8080/x"),
            FakeTab(),
            "its tab is on http://b.localhost:8080/x, not the site",
        ),
        (
            FakePage(),
            FakeTab(quiet=False, busy=[f"POST {URL}/__js/3", f"GET {URL}"]),
            f"its network is busy (POST {URL}/__js/3, GET {URL})",
        ),
        (FakePage(), FakeTab(quiet=False), "its network is busy (a request ended under 2 s ago)"),
        (FakePage(state="interactive"), FakeTab(), "its page is still loading (interactive)"),
        (FakePage(navigating=True), FakeTab(), "its page is navigating"),
        (FakePage(), FakeTab(shows=CHECK), "its page is still the check (cloudflare)"),
        (
            FakePage(),
            FakeTab(shows=PAGE.model_copy(update={"status": 404})),
            "its page answers 404",
        ),
    ],
)
async def test_a_tab_that_has_not_passed_says_why(
    page: FakePage, tab: FakeTab, reason: str
) -> None:
    assert await not_passed_because(page, tab, SITE) == reason


async def test_at_most_three_busy_requests_are_named() -> None:
    busy = [f"GET {URL}/{n}" for n in range(5)]

    reason = await not_passed_because(FakePage(), FakeTab(quiet=False, busy=busy), SITE)

    assert reason == f"its network is busy ({', '.join(busy[:3])})"
