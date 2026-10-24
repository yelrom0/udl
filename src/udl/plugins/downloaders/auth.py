"""
-----------
- auth.py -
-----------

Signing in to sites. A download that needs a login (AuthRequired) is retried with the site's
cookies (AuthCookies), from

- the user's own browser: the cookies_from_browser setting, e.g. "firefox" or "chrome:Profile 1",
  read with yt-dlp's cookie readers (the browser may be running), and
- udl's browser profile, which keeps the logins made with `udl browser login` (browser_login()).
  The browser downloader runs on that profile too. It needs Playwright (udl's browser extra),
  which is only imported when the profile is used.
"""

# System Imports
import asyncio
import logging
import re
from collections.abc import Awaitable, Iterable
from pathlib import Path
from typing import Final, Protocol, Self
from urllib.parse import urlsplit

# Package Imports
from pydantic import BaseModel, ConfigDict
from yt_dlp.cookies import (
    SUPPORTED_BROWSERS,
    SUPPORTED_KEYRINGS,
    _LinuxKeyring,  # pyright: ignore[reportPrivateUsage] - what its readers take
    extract_cookies_from_browser,
)

# Local Imports
from udl.config import Settings
from udl.db.models import utcnow
from udl.errors import ConfigError, ToolUnavailable, UDLError
from udl.plugins.downloaders.base import Cookie, merge_cookies
from udl.plugins.downloaders.ytdlp import YtDlpLogger

log = logging.getLogger(__name__)

# yt-dlp's --cookies-from-browser syntax: BROWSER[+KEYRING][:PROFILE][::CONTAINER]
_SPEC: Final = re.compile(
    r"(?P<browser>[^+:]+)(?:\s*\+\s*(?P<keyring>[^:]+))?"
    r"(?:\s*:\s*(?!:)(?P<profile>.+?))?(?:\s*::\s*(?P<container>.+))?"
)
_SPEC_HINT: Final[str] = (
    "cookies_from_browser is BROWSER[+KEYRING][:PROFILE][::CONTAINER] (as yt-dlp's "
    f"--cookies-from-browser), with one of these browsers: {', '.join(sorted(SUPPORTED_BROWSERS))}"
)


def for_site(cookies: Iterable[Cookie], url: str) -> list[Cookie]:
    """The unexpired cookies of url's site: those its host gets, and those of hosts under it
    (e.g. cdn.<host>)."""
    host = (urlsplit(url).hostname or "").lower()
    now = utcnow()

    def of_site(cookie: Cookie) -> bool:
        domain = cookie.domain.lower().lstrip(".")
        related = host == domain or host.endswith(f".{domain}") or domain.endswith(f".{host}")
        return related and (cookie.expires is None or cookie.expires > now)

    return [cookie for cookie in cookies if of_site(cookie)]


# ---------------------------------------------------------------------------------------------
# The user's browser
# ---------------------------------------------------------------------------------------------


class BrowserCookieSource(BaseModel):
    """A browser to read cookies from (the cookies_from_browser setting)."""

    model_config = ConfigDict(frozen=True)

    browser: str  # e.g. "firefox", "chrome"
    keyring: str | None = None  # where a Chromium-based browser keeps its key, e.g. "GNOMEKEYRING"
    profile: str | None = None  # a profile's name or folder; None: the one used last
    container: str | None = None  # a Firefox container's name ("none": outside containers)

    @classmethod
    def parse(cls, spec: str) -> Self:
        """A source in yt-dlp's syntax, e.g. "chromium+gnomekeyring:Default". Raises
        ConfigError."""
        match = _SPEC.fullmatch(spec.strip())
        if match is None:
            raise ConfigError(f"cookies_from_browser = {spec!r} names no browser", hint=_SPEC_HINT)
        browser = match["browser"].strip().lower()
        if browser not in SUPPORTED_BROWSERS:
            raise ConfigError(
                f"udl cannot read {browser}'s cookies (cookies_from_browser)", hint=_SPEC_HINT
            )
        keyring: str | None = match["keyring"]
        if keyring is not None and (keyring := keyring.strip().upper()) not in SUPPORTED_KEYRINGS:
            raise ConfigError(
                f"cookies_from_browser names an unknown keyring, {keyring}",
                hint=f"The keyrings are: {', '.join(sorted(SUPPORTED_KEYRINGS))}.",
            )
        return cls(
            browser=browser, keyring=keyring, profile=match["profile"], container=match["container"]
        )


def _read_browser(source: BrowserCookieSource) -> list[Cookie]:
    try:
        jar = extract_cookies_from_browser(
            source.browser,
            str(Path(source.profile).expanduser()) if source.profile else None,
            YtDlpLogger(),
            keyring=_LinuxKeyring[source.keyring] if source.keyring else None,
            container=source.container,
        )
    except Exception as e:
        raise ConfigError(
            f"Could not read {source.browser}'s cookies: {e}",
            hint="Check cookies_from_browser in the config.",
        ) from e
    return [Cookie.from_http_cookie(cookie) for cookie in jar]


async def cookies_from_browser(source: BrowserCookieSource, url: str) -> list[Cookie]:
    """The cookies `source`'s browser has for url's site. Raises ConfigError when they cannot
    be read (e.g. the browser has no profile there)."""
    return for_site(await asyncio.to_thread(_read_browser, source), url)


# ---------------------------------------------------------------------------------------------
# udl's browser profile
# ---------------------------------------------------------------------------------------------


def _browser():
    """The browser downloader's module, imported only now: it needs Playwright, which comes
    with udl's browser extra. (Not annotated: pyright types the module as itself.)"""
    try:
        import udl.plugins.downloaders.browser as browser
    except ImportError as e:
        raise ToolUnavailable(
            "udl's browser profile needs Playwright",
            tool="browser",
            hint="Install udl's browser extra (`uv sync --extra browser`), then Chromium "
            "(`uv run playwright install chromium`).",
        ) from e
    return browser


async def profile_cookies(url: str, *, profile_dir: Path) -> list[Cookie]:
    """The cookies udl's browser profile has for url's site (none, without starting Chromium,
    when the profile was never used)."""
    if not await asyncio.to_thread(profile_dir.is_dir):
        return []
    return for_site(await _browser().profile_cookies(profile_dir), url)


async def browser_login(
    url: str,
    *,
    profile_dir: Path,
    headless: bool = False,
    until: Awaitable[object] | None = None,
) -> list[Cookie]:
    """Open `url` in Chromium on udl's profile for the user to sign in, and wait until they close
    the browser, or `until` is done (e.g. they pressed Enter). The profile keeps the login.
    Returns the cookies it has for url's site by then."""
    cookies = await _browser().login(url, profile_dir=profile_dir, headless=headless, until=until)
    return for_site(cookies, url)


# ---------------------------------------------------------------------------------------------
# Every source, for a URL
# ---------------------------------------------------------------------------------------------


class ProfileReader(Protocol):
    """Reads udl's browser profile's cookies (the page fetcher's does it through its browser
    while that is open: udl.fetch.browser.ProfileCookies)."""

    async def cookies_for(self, url: str) -> list[Cookie]: ...


class AuthCookies:
    """The cookies that sign in to a site: the user's browser's (if `browser`), then udl's
    browser profile's (if `profile`), which win, since `udl browser login` signs in there. The
    profile is read by `reader` when there is one, else by a Chromium of its own."""

    def __init__(
        self,
        *,
        browser: BrowserCookieSource | None = None,
        profile: Path | None = None,
        reader: ProfileReader | None = None,
    ) -> None:
        self.browser = browser
        self.profile = profile
        self.reader = reader

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """The sources the settings name. Raises ConfigError for a cookies_from_browser that
        udl cannot read."""
        spec = settings.cookies_from_browser
        browser = BrowserCookieSource.parse(spec) if spec else None
        return cls(browser=browser, profile=settings.browser.profile_dir)

    def browsers_only(self) -> Self:
        """These sources but udl's profile: the user's browser's cookies alone."""
        return type(self)(browser=self.browser)

    async def for_url(self, url: str) -> list[Cookie]:
        """The cookies of url's site. A source that fails is skipped, with a warning."""
        cookies: list[Cookie] = []
        if self.browser is not None:
            try:
                cookies = await cookies_from_browser(self.browser, url)
            except UDLError as e:
                log.warning("Could not use the browser's cookies: %s", e)
        if self.profile is not None:
            try:
                found = (
                    await self.reader.cookies_for(url)
                    if self.reader is not None
                    else await profile_cookies(url, profile_dir=self.profile)
                )
                cookies = merge_cookies(cookies, found)
            except UDLError as e:
                log.warning("Could not use udl's browser profile: %s", e)
        return cookies
