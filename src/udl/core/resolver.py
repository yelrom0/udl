"""
---------------
- resolver.py -
---------------

The resolver: which downloaders to try on a URL, and in what order. Each is a Candidate; the
executor tries them in turn until one downloads the URL.

Precedence (highest first):

1. the request's tool (`--tool`): it is the only candidate;
2. the strategy of the site's plugin (Phase 6), which encodes deliberate behaviour;
3. the site's stored strategies, from every database (DBManager's read policy): those that are
   enabled and whose url_pattern matches the URL's path, best first (see strategy_rank);
4. the probe chain: the tools in `probe_order`, those that match the URL offline (no network:
   a site's yt-dlp or gallery-dl extractor, a media file's extension) first. The others are
   checked over the network by trying them, in that order: by default http (is the URL itself a
   file?), yt-dlp's generic extractor, gallery-dl, the scraper, and the browser, the most
   expensive, last.

A tool is not offered twice with the same options (and tag chain). The probe chain is worked out
only once it is reached, since matching offline asks every extractor of yt-dlp and gallery-dl.
"""

# System Imports
import json
import logging
import re
from collections.abc import AsyncGenerator, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

# Package Imports
from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.chains.models import TagChain
from udl.config import Settings
from udl.core.models import CandidateSource
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import JsonObject, Strategy, StrategySource
from udl.errors import DatabaseError, UDLError
from udl.plugins.downloaders.base import MatchResult

log = logging.getLogger(__name__)


class Candidate(BaseModel):
    """A downloader to try a URL with."""

    model_config = ConfigDict(frozen=True)

    tool: str
    source: CandidateSource
    options: JsonObject = Field(default_factory=dict)  # over the tool's [tools.<name>] table
    chain: TagChain | None = None  # the tag chain the tool follows
    requires_auth: bool = False  # sign in (send the site's cookies) from the start
    strategy: Strategy | None = None  # the stored strategy (source STORED)
    match: MatchResult | None = None  # how it matched the URL offline (source PROBE)

    @property
    def key(self) -> tuple[str, str, str]:
        """What makes two candidates the same: the tool, its options and its chain."""
        chain = self.chain.model_dump_json() if self.chain else ""
        return (self.tool, json.dumps(self.options, sort_keys=True), chain)


class SiteBehaviour(Protocol):
    """What the resolution engine asks of a site plugin (Phase 6's SitePlugin)."""

    def matches(self, url: URL) -> bool:
        """Whether this plugin handles the URL's site."""
        ...

    def strategy(self, url: URL) -> Strategy | None:
        """The strategy to try first on the URL, if the plugin chooses one."""
        ...

    def output_dir(self, url: URL) -> Path | None:
        """Where the URL's download goes, if the plugin says."""
        ...


def strategy_rank(strategy: Strategy) -> tuple[bool, int, bool, int, float, UUID]:
    """The order stored strategies are tried in (lower first): one whose url_pattern matched
    before a site-wide one, then by priority, a manual one before a learned one, one that did
    not fail last time before one that did (fewest failures in a row first), the best success
    rate (counting one success and one failure more, so an untried strategy sits at a half),
    and finally the id, so the order is always the same."""
    rate = (strategy.success_count + 1) / (strategy.success_count + strategy.failure_count + 2)
    return (
        strategy.url_pattern is None,
        strategy.priority,
        strategy.source is not StrategySource.MANUAL,
        strategy.consecutive_failures,
        -rate,
        strategy.id,
    )


def applicable(strategies: Iterable[Strategy], url: URL) -> list[Strategy]:
    """The strategies to try on `url`: the enabled ones whose url_pattern (if any) is found in
    its path, in strategy_rank order."""
    path = url.path
    found = [
        strategy
        for strategy in strategies
        if strategy.enabled
        and (strategy.url_pattern is None or re.search(strategy.url_pattern, path))
    ]
    return sorted(found, key=strategy_rank)


class Resolver:
    """Works out which downloaders to try on a URL."""

    def __init__(
        self, settings: Settings, toolbox: Toolbox, site_plugins: Sequence[SiteBehaviour] = ()
    ) -> None:
        self._toolbox = toolbox
        self._site_plugins = list(site_plugins)
        self._probe_order: list[str] = []
        for name in settings.probe_order:
            if toolbox.has(name):
                self._probe_order.append(name)
            else:
                log.warning("probe_order names %r, which is not an installed downloader", name)

    def site_plugin(self, url: URL) -> SiteBehaviour | None:
        """The site's plugin: the first that matches the URL."""
        return next((plugin for plugin in self._site_plugins if plugin.matches(url)), None)

    async def candidates(
        self, url: URL, *, db: DBManager, tool: str | None = None, reprobe: bool = False
    ) -> AsyncGenerator[Candidate]:
        """The candidates for `url`, best first. `tool` (the request's) is the only one when
        given; `reprobe` skips the stored strategies."""
        if tool is not None:
            yield Candidate(tool=tool, source=CandidateSource.REQUEST)
            return
        stored = [] if reprobe else applicable(await self._stored(url, db), url)
        offered: set[tuple[str, str, str]] = set()
        for candidate in self._in_order(url, stored):
            if candidate.key not in offered:
                offered.add(candidate.key)
                yield candidate

    def probe_chain(self, url: URL) -> list[Candidate]:
        """The probe chain's candidates for `url`: the tools that match it offline, then the
        others, each in probe_order."""
        matched: list[Candidate] = []
        others: list[Candidate] = []
        for name in self._probe_order:
            match = self._match(name, url)
            candidate = Candidate(tool=name, source=CandidateSource.PROBE, match=match)
            (others if match is None else matched).append(candidate)
        return [*matched, *others]

    def _in_order(self, url: URL, stored: list[Strategy]) -> Iterator[Candidate]:
        plugin = self.site_plugin(url)
        if plugin is not None and (chosen := plugin.strategy(url)) is not None:
            yield Candidate(
                tool=chosen.tool,
                source=CandidateSource.PLUGIN,
                options=chosen.options,
                chain=chosen.chain,
                requires_auth=chosen.requires_auth,
            )
        for strategy in stored:
            yield Candidate(
                tool=strategy.tool,
                source=CandidateSource.STORED,
                options=strategy.options,
                chain=strategy.chain,
                requires_auth=strategy.requires_auth,
                strategy=strategy,
            )
        yield from self.probe_chain(url)  # (only worked out once it is reached)

    def _match(self, name: str, url: URL) -> MatchResult | None:
        try:
            return self._toolbox.get(name).match(url.value)
        except UDLError as e:  # its options are invalid: trying it will say so
            log.debug("Could not match %s offline with %s: %s", url, name, e)
        except Exception:  # a plugin's bug must not stop the others (logged with its traceback)
            log.warning("The %s downloader failed to match %s offline", name, url, exc_info=True)
        return None

    async def _stored(self, url: URL, db: DBManager) -> list[Strategy]:
        try:
            return await db.strategies_for(url.site_key)
        except DatabaseError as e:
            log.warning("Could not read the strategies for %s, so probing: %s", url.site_key, e)
            return []
