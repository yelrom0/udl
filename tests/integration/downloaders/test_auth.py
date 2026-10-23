"""
---------------
- test_auth.py -
---------------

Signing in with udl's browser profile against the fixture site (its /login page signs in):
browser_login() keeps a login in the profile, and the profile's cookies let a download that
needed a login succeed
"""

# System Imports
import asyncio
import os
import signal
import time
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.browser import needs_chromium
from support.fixture_site import COOKIE_NAME, FixtureSite, Page
from udl.errors import AuthRequired, ErrorCode, NetworkError
from udl.plugins.downloaders.auth import AuthCookies, browser_login, profile_cookies
from udl.plugins.downloaders.base import DownloadContext, ProbeContext
from udl.plugins.downloaders.browser import BrowserDownloader, BrowserOptions
from udl.plugins.downloaders.http import HttpDownloader


@pytest.fixture
def profile(tmp_path: Path) -> Path:
    needs_chromium()
    return tmp_path / "profile"


async def sign_in(site: FixtureSite, profile: Path) -> None:
    """Sign in to the fixture site in the profile, as a user would with `udl browser login`.
    (Opening its login page signs in, and an awaitable that is done at once stands for the user
    saying they have finished.)"""
    await browser_login(
        site.url(Page.LOGIN), profile_dir=profile, headless=True, until=asyncio.sleep(0)
    )


async def test_signing_in_keeps_the_login_in_the_profile(
    fixture_site: FixtureSite, profile: Path
) -> None:
    await sign_in(fixture_site, profile)

    kept = await profile_cookies(fixture_site.url(Page.PRIVATE), profile_dir=profile)
    assert COOKIE_NAME in {cookie.name for cookie in kept}


async def test_a_download_that_needed_a_login_works_with_the_profiles_cookies(
    fixture_site: FixtureSite, profile: Path, tmp_path: Path
) -> None:
    url = fixture_site.url(Page.PRIVATE_FILE)
    with pytest.raises(AuthRequired):
        await HttpDownloader().download(DownloadContext(url=url, output_dir=tmp_path))
    await sign_in(fixture_site, profile)

    cookies = await AuthCookies(profile=profile).for_url(url)
    result = await HttpDownloader().download(
        DownloadContext(url=url, output_dir=tmp_path, cookies=cookies)
    )

    assert [file.read_bytes() for file in result.files] == [fixture_site.content(Page.DIRECT_FILE)]


async def test_the_browser_downloader_uses_the_login(
    fixture_site: FixtureSite, profile: Path
) -> None:
    browser = BrowserDownloader(BrowserOptions(profile_dir=profile, wait=0.5))
    before = await browser.probe(ProbeContext(url=fixture_site.url(Page.PRIVATE)))
    await sign_in(fixture_site, profile)

    after = await browser.probe(ProbeContext(url=fixture_site.url(Page.PRIVATE)))

    assert before.error is not None
    assert before.error.code is ErrorCode.AUTH_REQUIRED
    assert after.error is None


@pytest.mark.parametrize("seconds", [0, 1])
async def test_signing_in_ends_when_the_user_says_so(
    fixture_site: FixtureSite, profile: Path, seconds: float
) -> None:
    cookies = await browser_login(
        fixture_site.url(Page.LOGIN),
        profile_dir=profile,
        headless=True,
        until=asyncio.sleep(seconds),
    )

    assert COOKIE_NAME in {cookie.name for cookie in cookies}


async def test_signing_in_ends_when_the_browser_is_closed(
    fixture_site: FixtureSite, profile: Path
) -> None:
    task = asyncio.create_task(
        browser_login(fixture_site.url(Page.LOGIN), profile_dir=profile, headless=True)
    )
    [pid] = await asyncio.to_thread(chromium_started_by_this_process)
    await asyncio.sleep(1.5)  # the login page signs in

    os.kill(pid, signal.SIGTERM)  # as when the user closes its window

    cookies = await asyncio.wait_for(task, timeout=10)
    assert COOKIE_NAME in {cookie.name for cookie in cookies}


def parent_of(pid: int) -> int:
    return int(Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[1])


def chromium_started_by_this_process() -> list[int]:
    """The ids of the Chromium browser processes this process started (through Playwright's
    driver, so its grandchildren), once there are some."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found: list[int] = []
        for proc in Path("/proc").iterdir():
            try:
                args = (proc / "cmdline").read_bytes().split(b"\0")
                if parent_of(parent_of(int(proc.name))) == os.getpid() and not any(
                    arg.startswith(b"--type=")
                    for arg in args  # Chromium's helper processes
                ):
                    found.append(int(proc.name))
            except OSError, ValueError, IndexError:  # not a process, or one that has gone
                continue
        if found:
            return found
        time.sleep(0.05)
    raise TimeoutError("Chromium never started")


async def test_signing_in_to_a_site_that_cannot_be_reached_is_a_network_error(
    profile: Path,
) -> None:
    with pytest.raises(NetworkError):
        await browser_login("http://127.0.0.1:1/login", profile_dir=profile, headless=True)


async def test_an_unused_profile_has_no_cookies(tmp_path: Path) -> None:
    # No Chromium is started for a profile that does not exist.
    assert await profile_cookies("https://example.com/", profile_dir=tmp_path / "none") == []
