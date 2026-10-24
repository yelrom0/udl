"""
--------------
- cookies.py -
--------------

Cookies, both ways. They come into udl's browser profile, where fetches, downloads and `udl
browser login` all find them, from what the user gives (parse_cookies(): a Netscape cookies.txt,
a browser extension's JSON export, Playwright's storage state, or a Cookie header for a site)
or from their own browser, with the User-Agent they belong to (a clearance only works with the
User-Agent it was issued to). A cookie's value is never shown: listing gives names, domains,
paths, expiry and flags.

The cookies rung of the ladder sends a site's cookies from a SessionJar: the user's browser's and
the profile's, read once per site, and what sites set or delete on them is written back to the
profile (through the browser lane when it is open, else before the browser next uses the
profile, and when a batch ends), so a session a site rotates on a plain request stays valid in
the browser, and the other way round.
"""

# System Imports
import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Final, Protocol, cast

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, NonNegativeInt, SecretStr

# Local Imports
from udl.core.executor import CookieSource
from udl.core.urls import site_key
from udl.errors import ConfigError
from udl.fetch.ladder import FetchSites
from udl.plugins.downloaders.auth import BrowserCookieSource, cookies_from_browser
from udl.plugins.downloaders.base import Cookie

log = logging.getLogger(__name__)

_NETSCAPE_HEADERS: Final = ("# netscape http cookie file", "# http cookie file")
_HTTP_ONLY: Final = "#HttpOnly_"
_FORMS: Final = (
    "udl reads cookies as a Netscape cookies.txt (as browser extensions and yt-dlp write it), a "
    "JSON export of a browser extension (an array of cookies), Playwright's storage state, or a "
    "Cookie header (name=value; name2=value2, with the site it is for)"
)

type CookieKey = tuple[str, str, str]
"""A cookie's name, domain (without its leading dot) and path: what tells it from others."""


def key_of(cookie: Cookie) -> CookieKey:
    return (cookie.name, cookie.domain.lstrip(".").lower(), cookie.path)


def _exactly(cookie: Cookie) -> CookieKey:
    """A cookie's name, domain as it is spelt (a browser keeps .example.com and example.com
    apart) and path."""
    return (cookie.name, cookie.domain, cookie.path)


# ---------------------------------------------------------------------------------------------
# Reading what the user gives
# ---------------------------------------------------------------------------------------------


def parse_cookies(text: str, *, site: str | None = None) -> list[Cookie]:
    """The cookies in `text`, told by how it starts: a Netscape cookies.txt, a JSON export (an
    array, as browser extensions export cookies), Playwright's storage state, or a Cookie
    header (which needs `site`, and gives session cookies for its domain). Raises ConfigError."""
    stripped = text.lstrip("﻿").strip()
    if not stripped:
        raise ConfigError("There are no cookies in it", hint=_FORMS)
    if stripped[0] in "[{":
        return _from_json(stripped)
    lines = [line for line in stripped.splitlines() if line.strip()]
    if lines[0].lower().startswith(_NETSCAPE_HEADERS) or any(
        len(line.split("\t")) in (6, 7) for line in lines if not _comment(line)
    ):
        return _from_cookies_txt(lines)
    return _from_header(stripped, site)


def _comment(line: str) -> bool:
    return line.startswith("#") and not line.startswith(_HTTP_ONLY)


def _from_cookies_txt(lines: list[str]) -> list[Cookie]:
    cookies: list[Cookie] = []
    for number, line in enumerate(lines, 1):
        if _comment(line):
            continue
        http_only = line.startswith(_HTTP_ONLY)
        fields = line.removeprefix(_HTTP_ONLY).rstrip("\r").split("\t")
        if len(fields) == 6:  # (an empty value, its tab trimmed)
            fields.append("")
        if len(fields) != 7:
            raise ConfigError(
                f"Line {number} of the cookies.txt is not a cookie: it has {len(fields)} fields "
                "separated by tabs, not 7",
                hint=_FORMS,
            )
        domain, subdomains, path, secure, expires, name, value = fields
        if subdomains.upper() == "TRUE" and not domain.startswith("."):
            domain = f".{domain}"
        cookies.append(
            _cookie(
                name=name,
                value=value,
                domain=domain,
                path=path or "/",
                secure=secure.upper() == "TRUE",
                http_only=http_only,
                expires=_when(expires),
                where=f"line {number} of the cookies.txt",
            )
        )
    return cookies


def _from_json(text: str) -> list[Cookie]:
    try:
        found: object = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(f"The cookies are not JSON: {e}", hint=_FORMS) from e
    if isinstance(found, dict):  # Playwright's storage state: {"cookies": [...], "origins": [...]}
        found = cast(dict[str, Any], found).get("cookies")
    if not isinstance(found, list):
        raise ConfigError("The JSON holds no list of cookies", hint=_FORMS)
    return [_from_object(item, n) for n, item in enumerate(cast(list[object], found), 1)]


def _from_object(item: object, number: int) -> Cookie:
    where = f"cookie {number} of the JSON"
    if not isinstance(item, dict):
        raise ConfigError(f"{where} is not an object", hint=_FORMS)
    found = cast(dict[str, Any], item)
    domain = str(found.get("domain") or "")
    if found.get("hostOnly") is True:
        domain = domain.lstrip(".")
    elif found.get("hostOnly") is False and domain and not domain.startswith("."):
        domain = f".{domain}"
    expires = found.get("expirationDate", found.get("expires"))
    if found.get("session") is True:
        expires = None
    return _cookie(
        name=str(found.get("name") or ""),
        value=str(found.get("value") or ""),
        domain=domain,
        path=str(found.get("path") or "/"),
        secure=found.get("secure") is True,
        http_only=found.get("httpOnly") is True,
        expires=_when(expires),
        where=where,
    )


def _from_header(text: str, site: str | None) -> list[Cookie]:
    header = text.removeprefix("Cookie:").removeprefix("cookie:").strip()
    pairs = [pair.strip() for pair in header.split(";") if pair.strip()]
    if not pairs or not all("=" in pair and not pair.startswith("=") for pair in pairs):
        raise ConfigError("udl cannot tell what these cookies are", hint=_FORMS)
    if site is None:
        raise ConfigError(
            "A Cookie header does not say which site it is for",
            hint="Name the site (`--site example.com`; import_cookies' site).",
        )
    domain = f".{site_key(site)}"
    return [
        _cookie(name=name.strip(), value=value.strip(), domain=domain, where="the Cookie header")
        for name, _, value in (pair.partition("=") for pair in pairs)
    ]


def _when(value: object) -> AwareDatetime | None:
    """An expiry as cookies.txt and JSON exports write it (seconds since 1970; 0 or -1, or
    nothing: a session cookie)."""
    if value in (None, "", 0, "0") or isinstance(value, bool):
        return None
    try:
        seconds = float(cast(str | float, value))
    except TypeError, ValueError:
        return None
    return None if seconds <= 0 else datetime.fromtimestamp(seconds, UTC)


def _cookie(
    *,
    name: str,
    value: str,
    domain: str,
    where: str,
    path: str = "/",
    secure: bool = False,
    http_only: bool = False,
    expires: AwareDatetime | None = None,
) -> Cookie:
    if not name or not domain.strip("."):
        raise ConfigError(f"{where} has no name or no domain", hint=_FORMS)
    return Cookie(
        name=name,
        value=SecretStr(value),
        domain=domain.lower(),
        path=path,
        secure=secure,
        http_only=http_only,
        expires=expires,
    )


def to_cookies_txt(cookies: Iterable[Cookie]) -> str:
    """`cookies` as a Netscape cookies.txt (which parse_cookies() reads back)."""
    lines = ["# Netscape HTTP Cookie File"]
    for cookie in cookies:
        prefix = _HTTP_ONLY if cookie.http_only else ""
        subdomains = "TRUE" if cookie.domain.startswith(".") else "FALSE"
        expires = str(int(cookie.expires.timestamp())) if cookie.expires else "0"
        fields = [
            f"{prefix}{cookie.domain}",
            subdomains,
            cookie.path,
            "TRUE" if cookie.secure else "FALSE",
            expires,
            cookie.name,
            cookie.value.get_secret_value(),
        ]
        lines.append("\t".join(fields))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------
# What is shown of cookies
# ---------------------------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class CookieInfo(_Frozen):
    """A cookie, as udl shows one: everything but its value."""

    name: str
    """Its name."""
    domain: str
    """Its domain (a leading dot: its subdomains get it too)."""
    path: str
    """The path it is sent under."""
    expires: AwareDatetime | None
    """When it expires (none: when the browser closes, a session cookie)."""
    secure: bool
    """Whether it is sent over https only."""
    http_only: bool
    """Whether a page's scripts cannot read it."""

    @classmethod
    def of(cls, cookie: Cookie) -> CookieInfo:
        return cls(
            name=cookie.name,
            domain=cookie.domain,
            path=cookie.path,
            expires=cookie.expires,
            secure=cookie.secure,
            http_only=cookie.http_only,
        )


def by_site(cookies: Iterable[Cookie]) -> dict[str, list[Cookie]]:
    """`cookies` by their sites (site keys), each site's by name."""
    sites: dict[str, list[Cookie]] = {}
    for cookie in cookies:
        try:
            key = site_key(cookie.domain.lstrip("."))
        except ValueError:
            key = cookie.domain.lstrip(".")
        sites.setdefault(key, []).append(cookie)
    return {
        site: sorted(found, key=lambda c: (c.name, c.domain))
        for site, found in sorted(sites.items())
    }


def of_site(cookies: Iterable[Cookie], site: str) -> list[Cookie]:
    """The cookies of a site (its site key): those of its domain and its subdomains."""
    return by_site(cookies).get(site_key(site), [])


# ---------------------------------------------------------------------------------------------
# udl's browser profile, and the cookies rung's jar
# ---------------------------------------------------------------------------------------------


class Profile(Protocol):
    """The cookies of udl's browser profile (udl.fetch.browser.ProfileCookies)."""

    async def cookies(self) -> list[Cookie]:
        """Every cookie it has."""
        ...

    async def add(self, cookies: list[Cookie]) -> None:
        """Add `cookies` (replacing those of the same name, domain and path)."""
        ...

    async def remove(self, cookies: list[Cookie]) -> None:
        """Remove the cookies of these names, domains and paths."""
        ...

    async def clear(self, site: str) -> int:
        """Remove every cookie of `site` (and its subdomains). Returns how many there were."""
        ...


class SessionJar:
    """The cookies the cookies rung sends (the user's browser's and udl's profile's, from
    `source`, read once per site), and what it keeps of what sites set: written back to
    `profile` when flushed, or at once while `writes_through()` says the profile is open (the
    browser lane's)."""

    def __init__(
        self,
        source: CookieSource,
        profile: Profile | None,
        *,
        writes_through: Callable[[], bool] = lambda: False,
    ) -> None:
        self._source = source
        self._profile = profile
        self._writes_through = writes_through
        self._sites: dict[str, dict[CookieKey, Cookie]] = {}
        self._added: dict[CookieKey, Cookie] = {}  # (by their exact domains)
        self._removed: dict[CookieKey, Cookie] = {}

    async def cookies_for(self, url: str) -> list[Cookie]:
        site = site_key(url)
        if site not in self._sites:
            found = await self._source.for_url(url)
            self._sites[site] = {key_of(cookie): cookie for cookie in found}
        return [cookie for cookie in self._sites[site].values() if cookie.matches(url)]

    async def keep(self, url: str, sent: list[Cookie], now: list[Cookie]) -> None:
        before = {key_of(cookie): cookie for cookie in sent}
        after = {key_of(cookie): cookie for cookie in now}
        site = self._sites.setdefault(site_key(url), {})
        for key, cookie in after.items():
            old = before.get(key)
            if old is not None and old.value == cookie.value and old.domain == cookie.domain:
                continue
            if old is not None and old.domain != cookie.domain:  # (spelt otherwise: replaced)
                self._removed[_exactly(old)] = old
            site[key] = cookie
            self._added[_exactly(cookie)] = cookie
            self._removed.pop(_exactly(cookie), None)
        for key, cookie in before.items():
            if key not in after:
                site.pop(key, None)
                self._removed[_exactly(cookie)] = cookie
                self._added.pop(_exactly(cookie), None)
        if self._writes_through():
            await self.flush()

    async def flush(self) -> None:
        """Write what sites set and deleted to the profile."""
        if self._profile is None or not (self._added or self._removed):
            return
        added, removed = list(self._added.values()), list(self._removed.values())
        self._added.clear()
        self._removed.clear()
        if removed:
            await self._profile.remove(removed)
        if added:
            await self._profile.add(added)
        log.debug(
            "Wrote %d cookies sites set, and %d they deleted, to the profile",
            len(added),
            len(removed),
        )

    def forget(self, site: str | None = None) -> None:
        """Read `site`'s cookies (or every site's) afresh next time (the browser may have
        changed them)."""
        if site is None:
            self._sites.clear()
        else:
            self._sites.pop(site_key(site), None)


# ---------------------------------------------------------------------------------------------
# Importing, listing and deleting them
# ---------------------------------------------------------------------------------------------


class ImportedCookies(_Frozen):
    """What import_cookies put in udl's browser profile (never the cookies' values)."""

    sites: dict[str, NonNegativeInt]
    """How many cookies of each site it imported."""
    user_agent: bool
    """Whether a User-Agent was kept with them (plain requests send it with them)."""


class SiteCookies(_Frozen):
    """A site's cookies in udl's browser profile (never their values)."""

    site: str
    """The site (its key)."""
    user_agent: str | None = None
    """The User-Agent imported with them, which udl's plain requests send with them."""
    cookies: list[CookieInfo]
    """Its cookies, by name."""


type BrowserReader = Callable[[BrowserCookieSource, str], Awaitable[list[Cookie]]]
"""Reads a site's cookies (for a URL of it) from the user's browser."""


class CookieStore:
    """The cookies of udl's browser profile, as import_cookies, list_cookies and delete_cookies
    see them, with the User-Agent each site's were imported with (FetchSite)."""

    def __init__(
        self,
        profile: Profile,
        sites: FetchSites,
        *,
        read_browser: BrowserReader = cookies_from_browser,
    ) -> None:
        self._profile = profile
        self._sites = sites
        self._read_browser = read_browser

    async def import_cookies(
        self,
        text: str | None,
        *,
        site: str | None = None,
        from_browser: str | None = None,
        user_agent: str | None = None,
    ) -> ImportedCookies:
        """Add cookies to the profile: those in `text` (any form parse_cookies() reads; only
        `site`'s, when it is named), or `site`'s from the user's browser (`from_browser`, e.g.
        "firefox" or "chrome:Profile 1"). `user_agent` is kept for each of their sites. Raises
        ConfigError."""
        if (text is None) == (from_browser is None):
            raise ConfigError(
                "Import cookies from a text or from a browser (one of them)",
                hint="Give the cookies (a cookies.txt, a JSON export, a Cookie header), or a "
                "browser to copy a site's from (`--from-browser firefox`, with `--site`).",
            )
        if from_browser is not None:
            if site is None:
                raise ConfigError(
                    "Copying cookies from a browser needs the site they are for",
                    hint="Name it (`--site example.com`).",
                )
            source = BrowserCookieSource.parse(from_browser)
            cookies = await self._read_browser(source, f"https://{site_key(site)}/")
        else:
            cookies = parse_cookies(text or "", site=site)
            if site is not None:
                cookies = of_site(cookies, site)
        if not cookies:
            where = f" of {site_key(site)}" if site is not None else ""
            raise ConfigError(f"There are no cookies{where} to import")
        await self._profile.add(cookies)
        grouped = by_site(cookies)
        if user_agent:
            for found in grouped:
                await self._sites.update(found, user_agent=user_agent)
        log.info("Imported cookies for %s", ", ".join(grouped))
        return ImportedCookies(
            sites={found: len(of) for found, of in grouped.items()}, user_agent=bool(user_agent)
        )

    async def list_cookies(self, site: str | None = None) -> list[SiteCookies]:
        """Each site's cookies (or only `site`'s), never their values."""
        grouped = by_site(await self._profile.cookies())
        if site is not None:
            grouped = {key: found for key, found in grouped.items() if key == site_key(site)}
        listed: list[SiteCookies] = []
        for key, found in grouped.items():
            known = await self._sites.get(key)
            listed.append(
                SiteCookies(
                    site=key,
                    user_agent=known.user_agent if known is not None else None,
                    cookies=[CookieInfo.of(cookie) for cookie in found],
                )
            )
        return listed

    async def delete_cookies(self, site: str) -> bool:
        """Delete a site's cookies (its subdomains' too) and the User-Agent imported with them.
        Returns whether there was anything to delete."""
        key = site_key(site)
        removed = await self._profile.clear(key)
        known = await self._sites.get(key)
        if known is not None and known.user_agent is not None:
            await self._sites.update(key, user_agent=None)
            return True
        return removed > 0
