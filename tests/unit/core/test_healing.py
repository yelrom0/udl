"""
-------------------
- test_healing.py -
-------------------

Self-healing: a stored strategy that fails heal_threshold times in a row is disabled, the URL is
re-probed and the new winner is stored. A manual strategy is never disabled, only warned about.
"""

# System Imports
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.engine import StaticCookies, databases, registry, script, settings
from udl.config import Settings
from udl.core.executor import Execution, Executor, JobSpec, RetryPolicy
from udl.core.models import Attempt
from udl.core.resolver import Resolver, applicable
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import Site, Strategy, StrategySource
from udl.plugins.downloaders.base import CancelToken

PAGE = "https://example.com/watch/1"


def strategy(tool: str, **fields: Any) -> Strategy:
    return Strategy.model_validate(
        {"site_key": "example.com", "tool": tool, "source": StrategySource.PROBE, **fields}
    )


def broken_scraper(tmp_path: Path, **fields: Any) -> Settings:
    """The scraper fails on the page; http downloads it."""
    return settings(
        tmp_path,
        tools={"scraper": script(errors={PAGE: "extraction_failed"}), "http": script({PAGE: "x"})},
        **fields,
    )


async def execute(config: Settings, db: DBManager, attempts: list[Attempt]) -> Execution:
    toolbox = Toolbox(config, registry())
    executor = Executor(
        config, Resolver(config, toolbox), toolbox, cookies=StaticCookies(), retry=RetryPolicy()
    )
    return await executor.run(
        JobSpec(url=URL.parse(PAGE)), db=db, cancel=CancelToken(), on_attempt=attempts.append
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DBManager]:
    async with databases(settings(tmp_path), registry()) as manager:
        await manager.upsert_site(Site(key="example.com"))
        yield manager


async def by_tool(db: DBManager) -> dict[str, Strategy]:
    return {s.tool: s for s in await db.strategies_for("example.com")}


async def test_a_strategy_that_keeps_failing_is_disabled_and_replaced(
    tmp_path: Path, db: DBManager
) -> None:
    await db.upsert_strategy(strategy("scraper", success_count=20, consecutive_failures=2))
    attempts: list[Attempt] = []

    execution = await execute(broken_scraper(tmp_path, heal_threshold=3), db, attempts)

    strategies = await by_tool(db)
    assert strategies["scraper"].enabled is False
    assert strategies["scraper"].consecutive_failures == 3
    assert execution.strategy_id == strategies["http"].id
    assert applicable(strategies.values(), URL.parse(PAGE)) == [strategies["http"]]
    # The re-probe does not try the broken strategy's tool again.
    assert [a.tool for a in attempts] == ["scraper", "http"]


async def test_a_strategy_below_the_threshold_stays_enabled(tmp_path: Path, db: DBManager) -> None:
    await db.upsert_strategy(strategy("scraper", consecutive_failures=1))

    await execute(broken_scraper(tmp_path, heal_threshold=3), db, [])

    scraper = (await by_tool(db))["scraper"]
    assert (scraper.enabled, scraper.consecutive_failures) == (True, 2)


async def test_the_threshold_comes_from_the_settings(tmp_path: Path, db: DBManager) -> None:
    await db.upsert_strategy(strategy("scraper", success_count=5))

    await execute(broken_scraper(tmp_path, heal_threshold=1), db, [])

    assert (await by_tool(db))["scraper"].enabled is False


async def test_a_manual_strategy_is_never_disabled_only_warned_about(
    tmp_path: Path, db: DBManager, caplog: pytest.LogCaptureFixture
) -> None:
    await db.upsert_strategy(
        strategy("scraper", source=StrategySource.MANUAL, consecutive_failures=9)
    )

    with caplog.at_level(logging.WARNING):
        execution = await execute(broken_scraper(tmp_path, heal_threshold=3), db, [])

    strategies = await by_tool(db)
    assert strategies["scraper"].enabled is True
    assert strategies["scraper"].consecutive_failures == 10
    assert execution.strategy_id == strategies["http"].id
    assert "scraper" in caplog.text
    assert "10 times in a row" in caplog.text


@pytest.mark.parametrize("source", [StrategySource.PROBE, StrategySource.IMPORT])
async def test_learned_and_imported_strategies_heal(
    source: StrategySource, tmp_path: Path, db: DBManager
) -> None:
    await db.upsert_strategy(strategy("scraper", source=source, consecutive_failures=2))

    await execute(broken_scraper(tmp_path, heal_threshold=3), db, [])

    assert (await by_tool(db))["scraper"].enabled is False
