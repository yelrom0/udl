"""
--------------------
- test_traffic.py -
--------------------

A tab's network as udl counts it (udl.fetch.browser.Traffic): what is in flight, when it last
moved, and that a new document drops what the old one left in flight. Playwright doesn't always
say a request ended when the page navigated away from it (a check's `fetch()` of its clearance,
then `location.reload()`), and a request counted for ever kept the window from ever seeing the
check passed
"""

# System Imports
from dataclasses import dataclass
from datetime import timedelta

# Local Imports
from support.clock import FakeClock
from udl.fetch.browser import Traffic

QUIET = timedelta(seconds=2)


@dataclass(frozen=True, eq=False)
class FakeRequest:
    method: str
    url: str
    navigation: bool = False

    def is_navigation_request(self) -> bool:
        return self.navigation


CLEAR = FakeRequest("POST", "http://a.localhost/checked/__clear")
RELOAD = FakeRequest("GET", "http://a.localhost/checked/1", navigation=True)


async def test_a_request_that_ended_is_quiet_once_the_delay_has_gone() -> None:
    clock = FakeClock()
    traffic = Traffic(clock)

    traffic.started(CLEAR)
    traffic.ended(CLEAR)
    await clock.advance(timedelta(seconds=1))
    assert not traffic.quiet_for(QUIET)

    await clock.advance(timedelta(seconds=1))
    assert traffic.quiet_for(QUIET)


async def test_a_request_in_flight_is_never_quiet() -> None:
    clock = FakeClock()
    traffic = Traffic(clock)

    traffic.started(CLEAR)
    await clock.advance(timedelta(minutes=2))

    assert not traffic.quiet_for(QUIET)
    assert traffic.inflight() == ["POST http://a.localhost/checked/__clear"]


async def test_a_new_document_drops_what_the_old_one_left_in_flight() -> None:
    # (The CI run that left b.localhost: its clearance's POST was never said to end, the page
    # reloaded, and two minutes later the window still counted it.)
    clock = FakeClock()
    traffic = Traffic(clock)

    traffic.started(CLEAR)
    traffic.started(RELOAD)
    traffic.navigated()
    traffic.ended(RELOAD)
    await clock.advance(QUIET)

    assert traffic.inflight() == []
    assert traffic.quiet_for(QUIET)


async def test_the_navigation_itself_is_kept_until_it_ends() -> None:
    # (A new document's own request is still loading when it commits: it counts until it ends.)
    clock = FakeClock()
    traffic = Traffic(clock)

    traffic.started(RELOAD)
    traffic.navigated()
    await clock.advance(QUIET)

    assert traffic.inflight() == ["GET http://a.localhost/checked/1"]
    assert not traffic.quiet_for(QUIET)


async def test_a_navigation_is_movement() -> None:
    clock = FakeClock()
    traffic = Traffic(clock)
    await clock.advance(timedelta(minutes=1))

    traffic.navigated()

    assert not traffic.quiet_for(QUIET)


async def test_what_is_in_flight_is_named_sorted() -> None:
    traffic = Traffic(FakeClock())
    for request in (RELOAD, CLEAR, FakeRequest("GET", "http://a.localhost/a.css")):
        traffic.started(request)

    assert traffic.inflight() == [
        "GET http://a.localhost/a.css",
        "GET http://a.localhost/checked/1",
        "POST http://a.localhost/checked/__clear",
    ]
