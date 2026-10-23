"""
-----------
- base.py -
-----------

SitePlugin: the interface every site plugin implements.

A tag chain or a strategy is data, for how to find a site's media; a site plugin is code, for
behaviour: rewriting a site's URLs (short and mobile links, lists), choosing its downloader,
headers and cookies it needs, its own folder, and work on the files once they are downloaded.

Every hook is optional: SitePlugin itself does nothing, and a plugin overrides the hooks it
needs. tests/contract/test_site_contract.py is the behaviour every plugin must have.
"""

# System Imports
from collections.abc import Iterable
from pathlib import Path
from typing import Any, ClassVar

# Local Imports
from udl.core.urls import URL
from udl.db.models import Strategy
from udl.errors import PluginError
from udl.plugins.downloaders.base import DownloadContext, DownloadResult
from udl.plugins.requirements import Requirement


def on_domains(url: URL, domains: Iterable[str]) -> bool:
    """Whether url's host is one of `domains`, or a subdomain of one."""
    host = url.host
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _check_domain(plugin: type, domain: str) -> None:
    try:
        url = URL.parse(domain)
        written = url.host == domain and url.value == f"https://{domain}/"
    except ValueError:
        written = False
    if not written:
        raise PluginError(
            f"{plugin.__name__}.domains: {domain!r} is not a host",
            hint="Write each domain as a host, in lower case, with no scheme, port or path: "
            "e.g. 'example.com'.",
        )


class SitePlugin:
    """Behaviour for the sites on `domains`.

    Subclasses set `name` (what `plugins list` shows, and what a plugin of the same name in a
    later plugin dir overrides) and `domains`, and override the hooks they need. udl makes one
    instance of each (with no arguments) and asks the first plugin that matches a URL, lowest
    `priority` first (see udl.core.sites for how udl calls them, and what it does when one
    fails).
    """

    name: ClassVar[str]
    # The hosts the plugin handles: a URL on one of them, or on a subdomain of one.
    domains: ClassVar[tuple[str, ...]] = ()
    # Lower is asked first, when several plugins match a URL.
    priority: ClassVar[int] = 0
    # What it needs installed (e.g. a program its post_process runs): udl does without a plugin
    # that is missing something, and `udl plugins list` says what.
    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for domain in cls.domains:
            _check_domain(cls, domain)

    def matches(self, url: URL) -> bool:
        """Whether this plugin handles `url`. By default, whether it is on one of `domains`; a
        plugin may handle fewer of their URLs (e.g. only some paths), never others."""
        return on_domains(url, self.domains)

    async def transform_urls(self, url: URL) -> list[URL]:
        """The URLs to download for `url`, which the user asked for: e.g. a short or mobile
        link's usual form, or the items of a list. Never empty. By default, `url` itself."""
        return [url]

    def strategy(self, url: URL) -> Strategy | None:
        """The downloader to try first on `url` (with its options, or a tag chain), or None to
        leave it to what udl knows about the site. By default None."""
        return None

    async def prepare(self, ctx: DownloadContext) -> DownloadContext:
        """The context to download ctx.url with, whichever downloader tries it: e.g. with headers
        or cookies the site needs. udl takes the headers and cookies from it; what is downloaded,
        and where to, stay as they are. By default, `ctx`."""
        return ctx

    def output_dir(self, url: URL) -> Path | None:
        """The folder url's files go in, unless the request names one; None leaves it to the
        site's settings and the config. By default None."""
        return None

    async def post_process(self, result: DownloadResult) -> DownloadResult:
        """What a download wrote, once the plugin has done its work on the files (e.g. renamed,
        moved or tagged them): its files must exist. By default, `result`."""
        return result
