"""
------------
- sites.py -
------------

FakeSitePlugin: a site plugin whose hooks do what it is given (the URLs a URL transforms into,
a strategy, an output dir, headers and cookies to add, a folder to move the files to), and which
remembers what it was asked. It passes the site plugin contract suite, like any real plugin.
"""

# System Imports
import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

# Local Imports
from udl.core.urls import URL
from udl.db.models import Strategy
from udl.plugins.downloaders.base import Cookie, DownloadContext, DownloadResult
from udl.plugins.sites.base import SitePlugin


class FakeSitePlugin(SitePlugin):
    """A site plugin for example.com (fake_site() makes one for other domains)."""

    name = "fake"
    domains = ("example.com",)

    def __init__(
        self,
        *,
        strategy: Strategy | None = None,
        output_dir: Path | None = None,
        urls: Mapping[str, Sequence[str]] | None = None,
        headers: Mapping[str, str] | None = None,
        cookies: Sequence[Cookie] = (),
        move_to: Path | None = None,
    ) -> None:
        self._strategy = strategy
        self._output_dir = output_dir
        self._urls = {url: list(found) for url, found in (urls or {}).items()}
        self._headers = dict(headers or {})
        self._cookies = list(cookies)
        self._move_to = move_to
        self.prepared: list[DownloadContext] = []  # the contexts prepare() was given
        self.post_processed: list[DownloadResult] = []  # the results post_process() was given

    async def transform_urls(self, url: URL) -> list[URL]:
        """The URLs `urls` gives for url (else url)."""
        return [URL.parse(found) for found in self._urls.get(url.value, [url.value])]

    def strategy(self, url: URL) -> Strategy | None:
        return self._strategy

    async def prepare(self, ctx: DownloadContext) -> DownloadContext:
        """ctx with `headers` and `cookies` added."""
        self.prepared.append(ctx)
        return ctx.model_copy(
            update={
                "headers": {**ctx.headers, **self._headers},
                "cookies": [*ctx.cookies, *self._cookies],
            }
        )

    def output_dir(self, url: URL) -> Path | None:
        return self._output_dir

    async def post_process(self, result: DownloadResult) -> DownloadResult:
        """The result with its files moved into `move_to` (when given)."""
        self.post_processed.append(result)
        if self._move_to is None:
            return result
        await asyncio.to_thread(self._move_to.mkdir, parents=True, exist_ok=True)
        moved = [file.rename(self._move_to / file.name) for file in result.files]
        return result.model_copy(update={"files": moved})


def fake_site(*on: str, called: str = "fake", first: int = 0, **behaviour: Any) -> FakeSitePlugin:
    """A FakeSitePlugin called `called` for the sites on `on` (by default example.com), with
    priority `first` and FakeSitePlugin's `behaviour`."""
    cls = type(
        f"FakeSite[{called}]",
        (FakeSitePlugin,),
        {"name": called, "domains": on or FakeSitePlugin.domains, "priority": first},
    )
    return cast(type[FakeSitePlugin], cls)(**behaviour)
