"""
--------------------
- test_resolver.py -
--------------------

Resolver: the candidates a URL is downloaded with, in order. The request's tool beats a site
plugin's strategy, which beats the stored strategies, which beat the probe chain.
"""

# System Imports
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import (
    TOOLS,
    FakeSitePlugin,
    connection,
    databases,
    registry,
    settings,
)
from udl.config import ReadPolicy, Settings
from udl.core.resolver import Candidate, CandidateSource, Resolver, applicable, strategy_rank
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import Site, Strategy, StrategySource
from udl.errors import DatabaseError

WATCH = URL.parse("https://example.com/watch/1")
SHORT = URL.parse("https://example.com/shorts/2")


def strategy(tool: str, **fields: object) -> Strategy:
    return Strategy.model_validate(
        {"site_key": "example.com", "tool": tool, "source": StrategySource.PROBE, **fields}
    )


@pytest.fixture
def config(tmp_path: Path) -> Settings:
    return settings(tmp_path)


@pytest.fixture
async def db(config: Settings) -> AsyncIterator[DBManager]:
    async with databases(config, registry()) as manager:
        await manager.upsert_site(Site(key="example.com"))
        yield manager


def resolver(config: Settings, *plugins: FakeSitePlugin) -> Resolver:
    return Resolver(config, Toolbox(config, registry()), plugins)


async def candidates(
    resolver: Resolver, url: URL, db: DBManager, *, tool: str | None = None, reprobe: bool = False
) -> list[Candidate]:
    return [c async for c in resolver.candidates(url, db=db, tool=tool, reprobe=reprobe)]


def tools(found: list[Candidate]) -> list[tuple[str, CandidateSource]]:
    return [(candidate.tool, candidate.source) for candidate in found]


def probing(*names: str) -> list[tuple[str, CandidateSource]]:
    return [(name, CandidateSource.PROBE) for name in names]


# ---------------------------------------------------------------------------------------------
# Precedence
# ---------------------------------------------------------------------------------------------


async def test_with_nothing_known_the_probe_chain_is_all_there_is(
    config: Settings, db: DBManager
) -> None:
    assert tools(await candidates(resolver(config), WATCH, db)) == probing(*TOOLS)


async def test_a_stored_strategy_beats_the_probe_chain(config: Settings, db: DBManager) -> None:
    stored = await db.upsert_strategy(strategy("scraper", options={"confidence": 0.5}))

    found = await candidates(resolver(config), WATCH, db)

    assert found[0] == Candidate(
        tool="scraper",
        source=CandidateSource.STORED,
        options={"confidence": 0.5},
        strategy=stored,
    )
    assert tools(found[1:]) == probing(*TOOLS)


async def test_a_site_plugin_s_strategy_beats_the_stored_strategies(
    config: Settings, db: DBManager
) -> None:
    await db.upsert_strategy(strategy("scraper"))
    plugin = FakeSitePlugin(
        "example.com",
        strategy=strategy("gallery-dl", source=StrategySource.PLUGIN, requires_auth=True),
    )

    found = await candidates(resolver(config, plugin), WATCH, db)

    assert found[0] == Candidate(
        tool="gallery-dl", source=CandidateSource.PLUGIN, requires_auth=True
    )
    assert found[1].source is CandidateSource.STORED


async def test_the_request_s_tool_beats_everything_and_is_all_that_is_tried(
    config: Settings, db: DBManager
) -> None:
    await db.upsert_strategy(strategy("scraper"))
    plugin = FakeSitePlugin("example.com", strategy=strategy("gallery-dl"))

    found = await candidates(resolver(config, plugin), WATCH, db, tool="http")

    assert found == [Candidate(tool="http", source=CandidateSource.REQUEST)]


async def test_a_site_plugin_for_another_site_or_without_a_strategy_is_skipped(
    config: Settings, db: DBManager
) -> None:
    other = FakeSitePlugin("other.com", strategy=strategy("gallery-dl"))
    no_strategy = FakeSitePlugin("example.com")

    found = await candidates(resolver(config, other, no_strategy), WATCH, db)

    assert tools(found) == probing(*TOOLS)


async def test_the_first_site_plugin_that_matches_is_the_site_s_plugin(config: Settings) -> None:
    first = FakeSitePlugin("example.com", strategy=strategy("http"))
    second = FakeSitePlugin("example.com", strategy=strategy("scraper"))

    assert resolver(config, FakeSitePlugin("o.com"), first, second).site_plugin(WATCH) is first
    assert resolver(config, second).site_plugin(URL.parse("https://o.com/")) is None


async def test_reprobing_skips_the_stored_strategies(config: Settings, db: DBManager) -> None:
    await db.upsert_strategy(strategy("scraper"))

    found = await candidates(resolver(config), WATCH, db, reprobe=True)

    assert tools(found) == probing(*TOOLS)


async def test_a_tool_is_not_offered_twice_with_the_same_options(
    config: Settings, db: DBManager
) -> None:
    await db.upsert_strategy(strategy("scraper"))
    await db.upsert_strategy(strategy("http", options={"confidence": 0.1}, success_count=1))

    found = await candidates(resolver(config), WATCH, db)

    assert tools(found) == [
        ("http", CandidateSource.STORED),
        ("scraper", CandidateSource.STORED),
        *probing("http", "yt-dlp", "gallery-dl", "browser"),
    ]


async def test_stored_strategies_come_from_every_database(tmp_path: Path) -> None:
    config = settings(
        tmp_path,
        connections=[connection("local", primary=True), connection("shared", priority=10)],
        read_policy=ReadPolicy.MERGE,
    )
    async with databases(config, registry()) as db:
        shared = db.database("shared")
        await shared.upsert_site(Site(key="example.com"))
        await shared.upsert_strategy(strategy("scraper"))

        found = await candidates(resolver(config), WATCH, db)

    assert tools(found)[0] == ("scraper", CandidateSource.STORED)


async def test_when_the_stored_strategies_cannot_be_read_the_probe_chain_is_tried(
    config: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    class Unreadable(DBManager):
        async def strategies_for(self, site_key: str) -> list[Strategy]:
            raise DatabaseError("the database is gone")

    broken = Unreadable(config.db, registry())

    found = await candidates(resolver(config), WATCH, broken)

    assert tools(found) == probing(*TOOLS)
    assert "the database is gone" in caplog.text


async def test_tools_in_probe_order_that_are_not_installed_are_skipped_with_a_warning(
    tmp_path: Path, db: DBManager, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(tmp_path, probe_order=["http", "no-such-tool", "scraper"])

    with caplog.at_level(logging.WARNING):
        found = await candidates(resolver(config), WATCH, db)

    assert tools(found) == probing("http", "scraper")
    assert "no-such-tool" in caplog.text


# ---------------------------------------------------------------------------------------------
# Which stored strategies apply, in what order
# ---------------------------------------------------------------------------------------------


def test_disabled_strategies_do_not_apply() -> None:
    on, off = strategy("http"), strategy("scraper", enabled=False)

    assert applicable([on, off], WATCH) == [on]


def test_a_strategy_applies_when_its_pattern_matches_the_url_s_path() -> None:
    shorts = strategy("yt-dlp", url_pattern="^/shorts/")
    site_wide = strategy("http")

    assert applicable([shorts, site_wide], SHORT) == [shorts, site_wide]
    assert applicable([shorts, site_wide], WATCH) == [site_wide]


def test_a_pattern_is_searched_for_anywhere_in_the_path() -> None:
    assert applicable([strategy("yt-dlp", url_pattern="shorts")], SHORT)


def test_a_matching_pattern_beats_a_site_wide_strategy() -> None:
    site_wide = strategy("http", priority=-5, success_count=100)
    shorts = strategy("yt-dlp", url_pattern="^/shorts/", priority=5)

    assert applicable([site_wide, shorts], SHORT) == [shorts, site_wide]


def test_then_a_lower_priority_number_goes_first() -> None:
    later = strategy("http", priority=1, source=StrategySource.MANUAL)
    sooner = strategy("scraper", priority=0)

    assert applicable([later, sooner], WATCH) == [sooner, later]


def test_then_a_manual_strategy_beats_a_learned_one() -> None:
    learned = strategy("http", success_count=50)
    manual = strategy("scraper", source=StrategySource.MANUAL)

    assert applicable([learned, manual], WATCH) == [manual, learned]


def test_then_one_that_failed_last_time_goes_after_one_that_did_not() -> None:
    failing = strategy("http", success_count=50, failure_count=1, consecutive_failures=1)
    fine = strategy("scraper", success_count=1)

    assert applicable([failing, fine], WATCH) == [fine, failing]


def test_then_the_higher_success_rate_goes_first() -> None:
    mixed = strategy("http", success_count=5, failure_count=10)
    reliable = strategy("scraper", success_count=5)
    untried = strategy("yt-dlp")

    assert applicable([untried, mixed, reliable], WATCH) == [reliable, untried, mixed]


def test_the_order_is_total() -> None:
    a = strategy("http", id=uuid.UUID(int=1))
    b = strategy("http", id=uuid.UUID(int=2))

    assert strategy_rank(a) < strategy_rank(b)
    assert applicable([b, a], WATCH) == [a, b]
