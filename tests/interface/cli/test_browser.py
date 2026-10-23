"""
-------------------
- test_browser.py -
-------------------

udl browser login: sign in to a site in udl's browser profile (the browser itself is stood in
for here; tests/integration/downloaders/test_auth.py runs the real one)
"""

# System Imports
import asyncio
import io
import os
import sys
from collections.abc import Awaitable
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.engine import cookie
from udl.core.urls import URL
from udl.errors import ToolUnavailable
from udl.interfaces.cli.commands import browser
from udl.plugins.downloaders.base import Cookie


class FakeLogin:
    """Stands in for browser_login: remembers how it was called and gives `cookies`."""

    def __init__(self, cookies: list[Cookie] | None = None, error: Exception | None = None) -> None:
        self.cookies = cookies or []
        self.error = error
        self.calls: list[tuple[str, Path, bool]] = []

    async def __call__(
        self,
        url: str,
        *,
        profile_dir: Path,
        headless: bool = False,
        until: Awaitable[object] | None = None,
    ) -> list[Cookie]:
        self.calls.append((url, profile_dir, until is not None))
        if self.error is not None:
            raise self.error
        return self.cookies


def test_login_opens_the_page_on_udl_s_profile(
    cli: CLI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = FakeLogin([cookie(), cookie("theme")])
    monkeypatch.setattr(browser, "browser_login", login)
    config = tmp_path / "config.toml"
    config.write_text(f'[browser]\nprofile_dir = "{tmp_path / "profile"}"\n')

    result = cli("--config", config, "browser", "login", "www.example.com/login")

    assert result.exit_code == 0, result.output
    # (Not on a terminal, so Enter is not waited for: closing the browser ends it.)
    assert login.calls == [("https://www.example.com/login", tmp_path / "profile", False)]
    assert "Sign in to example.com" in result.stderr
    assert "2 cookies for example.com" in result.stdout


def test_login_to_something_that_is_not_a_url_is_a_usage_error(
    cli: CLI, monkeypatch: pytest.MonkeyPatch
) -> None:
    login = FakeLogin()
    monkeypatch.setattr(browser, "browser_login", login)

    result = cli("browser", "login", "ftp://example.com")

    assert result.exit_code == 2
    assert login.calls == []


def test_login_without_a_browser_says_what_to_install(
    cli: CLI, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = ToolUnavailable("Chromium is not installed", hint="Run `playwright install`")
    monkeypatch.setattr(browser, "browser_login", FakeLogin(error=missing))

    result = cli("browser", "login", "example.com")

    assert result.exit_code == 1
    assert "Chromium is not installed" in result.stderr
    assert "playwright install" in result.stderr


async def test_enter_on_a_terminal_ends_the_login(monkeypatch: pytest.MonkeyPatch) -> None:
    leader, follower = os.openpty()
    with os.fdopen(follower, "r") as terminal:
        monkeypatch.setattr(sys, "stdin", terminal)

        async with browser.enter_pressed() as pressed:
            assert pressed is not None
            await asyncio.sleep(0.01)
            assert not pressed.done()
            os.write(leader, b"\n")
            await asyncio.wait_for(pressed, 1)
    os.close(leader)


async def test_a_login_that_ends_first_stops_waiting_for_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    leader, follower = os.openpty()
    with os.fdopen(follower, "r") as terminal:
        monkeypatch.setattr(sys, "stdin", terminal)
        login = FakeLogin([cookie()])
        monkeypatch.setattr(browser, "browser_login", login)

        cookies = await browser.login_on(URL.parse("example.com"), Path("profile"))
        os.write(leader, b"\n")  # (too late: nothing reads it)
        await asyncio.sleep(0.01)

    os.close(leader)
    assert cookies == [cookie()]
    assert login.calls == [("https://example.com/", Path("profile"), True)]


@pytest.mark.parametrize("stdin", [io.StringIO("\n"), None])
async def test_enter_is_not_waited_for_without_a_terminal(
    monkeypatch: pytest.MonkeyPatch, stdin: io.StringIO | None
) -> None:
    monkeypatch.setattr(sys, "stdin", stdin)

    async with browser.enter_pressed() as pressed:
        assert pressed is None
