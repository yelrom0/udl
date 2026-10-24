"""
----------------
- test_lane.py -
----------------

The fetcher's browser lane (udl.fetch.browser.BrowserLane): one Chromium on udl's profile for
every tab, at most browser_tabs at once, closed after browser_idle without one so the profile is
free for others, and opened again when a tab is wanted
"""

# System Imports
import asyncio
import time
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.antibot import AntiBotSite
from support.browser import needs_chromium
from udl.clock import SystemClock
from udl.config import FetchSettings
from udl.fetch.browser import BrowserLane, ProfileCookies
from udl.plugins.downloaders.base import CancelToken
from udl.plugins.downloaders.browser import chromium


def lane_on(profile: Path, **fields: object) -> BrowserLane:
    needs_chromium()
    settings = FetchSettings.model_validate({"timeout": "15s", **fields})
    return BrowserLane(profile, settings=settings, clock=SystemClock())


async def test_tabs_share_one_browser(antibot: AntiBotSite, tmp_path: Path) -> None:
    lane = lane_on(tmp_path / "profile")
    try:
        async with (
            lane.tab(headers={}, cancel=CancelToken()) as first,
            lane.tab(headers={}, cancel=CancelToken()) as second,
        ):
            await first.goto(antibot.url("/docs"), wait_for=None)
            await second.goto(antibot.url("/article"), wait_for=None)
            assert lane.context is not None
            assert len(lane.context.pages) >= 2
            assert lane.launches == 1
    finally:
        await lane.close()


async def test_no_more_than_browser_tabs_are_open_at_once(tmp_path: Path) -> None:
    lane = lane_on(tmp_path / "profile", browser_tabs=2)
    most = 0
    open_now = 0

    async def use() -> None:
        nonlocal most, open_now
        async with lane.tab(headers={}, cancel=CancelToken()):
            open_now += 1
            most = max(most, open_now)
            await asyncio.sleep(0.3)
            open_now -= 1

    try:
        await asyncio.gather(*(use() for _ in range(5)))
    finally:
        await lane.close()

    assert most == 2


async def test_an_idle_browser_closes_and_frees_the_profile(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    lane = lane_on(profile, browser_idle="300ms")

    async with lane.tab(headers={}, cancel=CancelToken()):
        assert lane.open
    await asyncio.sleep(1)

    assert not lane.open
    started = time.monotonic()
    async with chromium(profile_dir=profile, headless=True, cancel=CancelToken()):
        assert time.monotonic() - started < 10  # (no wait for the lane to let go)


async def test_a_closed_browser_opens_again_when_a_tab_is_wanted(tmp_path: Path) -> None:
    lane = lane_on(tmp_path / "profile", browser_idle="100ms")
    try:
        async with lane.tab(headers={}, cancel=CancelToken()):
            pass
        await asyncio.sleep(0.5)
        async with lane.tab(headers={}, cancel=CancelToken()):
            assert lane.open
    finally:
        await lane.close()

    assert lane.launches == 2


async def test_the_browsers_user_agent_is_known_once_it_has_opened(tmp_path: Path) -> None:
    lane = lane_on(tmp_path / "profile")
    assert lane.user_agent is None
    try:
        async with lane.tab(headers={}, cancel=CancelToken()):
            pass
    finally:
        await lane.close()

    assert lane.user_agent is not None
    assert "Chrome/" in lane.user_agent
    assert "HeadlessChrome" not in lane.user_agent


async def test_the_profiles_cookies_are_read_through_the_open_browser(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    lane = lane_on(profile)
    cookies = ProfileCookies(profile, open_context=lambda: lane.context)
    try:
        async with lane.tab(headers={}, cancel=CancelToken()):
            # (A Chromium of their own would wait for the lane to let go of the profile.)
            async with asyncio.timeout(5):
                assert await cookies.cookies() == []
    finally:
        await lane.close()


async def test_a_browser_that_cannot_start_says_why(tmp_path: Path) -> None:
    (tmp_path / "profile").write_text("a file, not a folder")
    lane = lane_on(tmp_path / "profile")

    with pytest.raises(Exception, match="Chromium"):
        async with lane.tab(headers={}, cancel=CancelToken()):
            pass
    await lane.close()


async def test_closing_a_lane_that_never_opened_does_nothing(tmp_path: Path) -> None:
    lane = lane_on(tmp_path / "profile", browser_idle=timedelta(seconds=1))

    await lane.close()

    assert not lane.open
