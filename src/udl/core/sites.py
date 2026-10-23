"""
------------
- sites.py -
------------

Site plugins in the resolution engine: which ones udl uses, and how it calls them (SiteHooks).

A URL's site plugin is the first plugin that matches it, lowest priority first. Where its hooks
come in (request args beat the plugin, the plugin beats what udl has learned):

1. transform_urls: when a download is asked for, the URL becomes the URLs the plugin gives (e.g.
   a short link's usual form, or a list's items), duplicates dropped. Each is a job of its own,
   with its own site plugin; the URLs it gives are not transformed again.
2. output_dir: the request's output dir, else the plugin's, else the site's, else the config's.
3. strategy: the first candidate after the request's tool (which, when given, is the only one),
   so it is tried before the stored strategies and the probe chain. It is never remembered.
4. prepare: before each try, whichever downloader makes it (the request's tool too).
5. post_process: once a download has worked (and has been remembered), on its files.

A plugin that only advises (matches, transform_urls, strategy, output_dir) and fails, or gives
something that is not what the hook gives, is logged (a bug with its traceback) and udl goes on
as if the plugin had nothing to say: a broken plugin never stops a download. prepare and
post_process take part in the download: an error they raise on purpose (a UDLError, e.g.
AuthRequired) is the download's, and anything else is a PluginFailed naming the plugin, which
ends the download.
"""

# System Imports
import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from types import UnionType
from typing import cast

# Local Imports
from udl.core.urls import URL
from udl.db.models import Strategy
from udl.errors import PluginFailed, UDLError
from udl.plugins.downloaders.base import DownloadContext, DownloadResult
from udl.plugins.registry import SITES, PluginRegistry
from udl.plugins.sites.base import SitePlugin

log = logging.getLogger(__name__)

_FIX = "It is a bug in the plugin: fix it, or remove it (`udl plugins list` shows where it is)."


def load_site_plugins(registry: PluginRegistry) -> list[SitePlugin]:
    """An instance of each site plugin in `registry`, by name. One that cannot start is left
    out, with a warning."""
    plugins: list[SitePlugin] = []
    for info in registry.plugins(SITES):
        try:
            plugins.append(registry.get(SITES, info.name)())
        except Exception:  # (logged with its traceback)
            log.warning(
                "Could not start the %s site plugin (%s)", info.name, info.origin, exc_info=True
            )
    return plugins


class SiteHooks:
    """A site plugin, called the way the engine calls it (see the module docs)."""

    def __init__(self, plugin: SitePlugin) -> None:
        self.plugin = plugin

    @property
    def name(self) -> str:
        return self.plugin.name

    # -- hooks that advise ---------------------------------------------------------------------

    def matches(self, url: URL) -> bool:
        return self._advice("match", url, lambda: self.plugin.matches(url), bool, False)

    async def transform_urls(self, url: URL) -> list[URL]:
        """The URLs to download for `url`: the plugin's, normalised, or `url` itself."""
        try:
            found = [URL.parse(each.value) for each in await self.plugin.transform_urls(url)]
        except UDLError as e:
            log.warning(
                "The %s site plugin could not transform %s, so it is downloaded as it is: %s",
                self.name,
                url,
                e,
            )
            return [url]
        except Exception:  # (logged with its traceback)
            log.warning(
                "The %s site plugin failed to transform %s, so it is downloaded as it is",
                self.name,
                url,
                exc_info=True,
            )
            return [url]
        if not found:
            log.warning(
                "The %s site plugin gave no URLs for %s, so it is downloaded as it is",
                self.name,
                url,
            )
            return [url]
        return found

    def strategy(self, url: URL) -> Strategy | None:
        return self._advice(
            "choose a strategy for", url, lambda: self.plugin.strategy(url), Strategy | None, None
        )

    def output_dir(self, url: URL) -> Path | None:
        return self._advice(
            "choose the output dir for", url, lambda: self.plugin.output_dir(url), Path | None, None
        )

    def _advice[T](
        self, doing: str, url: URL, call: Callable[[], object], kind: type | UnionType, default: T
    ) -> T:
        try:
            advice = call()
        except Exception:  # (logged with its traceback)
            log.warning(
                "The %s site plugin failed to %s %s, so udl goes on without it",
                self.name,
                doing,
                url,
                exc_info=True,
            )
            return default
        if not isinstance(advice, kind):
            log.warning(
                "The %s site plugin gave %r when asked to %s %s, so udl goes on without it",
                self.name,
                advice,
                doing,
                url,
            )
            return default
        return cast(T, advice)

    # -- hooks that take part in the download --------------------------------------------------

    async def prepare(self, ctx: DownloadContext) -> DownloadContext:
        """ctx with the headers and cookies the plugin gives it. Raises the UDLError the plugin
        raises, or PluginFailed."""
        try:
            prepared = cast(object, await self.plugin.prepare(ctx))  # (a plugin may get it wrong)
        except UDLError:
            raise
        except Exception as e:
            raise self._failed("prepare the download of", ctx.url, e) from e
        if not isinstance(prepared, DownloadContext):
            raise self._wrong("prepare", prepared, DownloadContext)
        return ctx.model_copy(update={"headers": prepared.headers, "cookies": prepared.cookies})

    async def post_process(self, result: DownloadResult) -> DownloadResult:
        """The result the plugin gives for `result`, with its files measured (they must exist).
        Raises the UDLError the plugin raises, or PluginFailed."""
        try:
            processed = cast(object, await self.plugin.post_process(result))
        except UDLError:
            raise
        except Exception as e:
            raise self._failed("post-process", result.url, e) from e
        if not isinstance(processed, DownloadResult):
            raise self._wrong("post_process", processed, DownloadResult)
        return await asyncio.to_thread(self._measured, processed)

    def _measured(self, result: DownloadResult) -> DownloadResult:
        size = 0
        for file in result.files:
            if not (file.is_absolute() and file.is_file()):
                raise PluginFailed(
                    f"The {self.name} site plugin's post_process gave {file}, which is not a file "
                    "(it gives the files' absolute paths)",
                    hint=_FIX,
                )
            size += file.stat().st_size
        return result.model_copy(update={"size_bytes": size})

    def _failed(self, doing: str, url: str, error: Exception) -> PluginFailed:
        log.warning("The %s site plugin failed to %s %s", self.name, doing, url, exc_info=True)
        return PluginFailed(
            f"The {self.name} site plugin failed to {doing} {url}: {type(error).__name__}: {error}",
            hint=_FIX,
        )

    def _wrong(self, hook: str, gave: object, kind: type) -> PluginFailed:
        return PluginFailed(
            f"The {self.name} site plugin's {hook} gave {type(gave).__name__}, not a "
            f"{kind.__name__}",
            hint=_FIX,
        )
