"""
-----------
- http.py -
-----------

udl's HTTP client (httpx), for every kind of plugin that talks to servers (downloaders, the
webhook action, integrations): it sends a browser's User-Agent unless told another, a site's
cookies, follows redirects, and raises httpx's errors as the UDLErrors they mean.

An integration is given an HttpClientFactory, which makes its clients (api_client(), unless a
test gives clients of its fake servers).
"""

# System Imports
import http.cookiejar
import importlib.metadata
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final, Protocol

# Package Imports
import httpx

# Local Imports
from udl.plugins.downloaders.base import Cookie
from udl.plugins.downloaders.errors import httpx_error

# The User-Agent udl's own requests send (unless a download has one): a browser's, since many
# sites refuse others (a library's, curl's, wget's), as yt-dlp and gallery-dl know.
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)


def with_user_agent(headers: Mapping[str, str], user_agent: str) -> dict[str, str]:
    """`headers`, with `user_agent` as the User-Agent unless they have one."""
    if any(name.lower() == "user-agent" for name in headers):
        return dict(headers)
    return {"User-Agent": user_agent, **headers}


@asynccontextmanager
async def http_client(
    *,
    headers: Mapping[str, str],
    cookies: Sequence[Cookie] = (),
    seconds: float,
    retries: int,
    tool: str,
    user_agent: str = USER_AGENT,
) -> AsyncGenerator[httpx.AsyncClient]:
    """An httpx client that sends `headers` (with `user_agent`, unless they have a User-Agent),
    and `cookies` to their sites, follows redirects, waits `seconds` to connect or for more
    data, and retries a failed connection `retries` times. httpx's errors are raised as the
    DownloadErrors they mean (from `tool`: NetworkError, for one)."""
    jar = http.cookiejar.CookieJar()
    for cookie in cookies:
        jar.set_cookie(cookie.to_http_cookie())
    try:
        async with httpx.AsyncClient(
            headers=with_user_agent(headers, user_agent),
            cookies=jar,
            follow_redirects=True,
            timeout=seconds,
            transport=httpx.AsyncHTTPTransport(retries=retries),
        ) as client:
            yield client
    except httpx.HTTPError as e:
        raise httpx_error(e, tool=tool) from e


class HttpClientFactory(Protocol):
    """What makes the HTTP clients an integration (or OAuth) talks to its service with."""

    def __call__(self, *, tool: str) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        """A client, for the time a block takes; httpx's errors are raised as the UDLErrors
        they mean, from `tool` (the integration's name)."""
        ...


def api_client(*, tool: str) -> AbstractAsyncContextManager[httpx.AsyncClient]:
    """udl's client of a service's API (an integration's, an OAuth provider's): as
    http_client(), saying it is udl."""
    version = importlib.metadata.version("udl")
    return http_client(headers={}, seconds=30, retries=2, tool=tool, user_agent=f"udl/{version}")
