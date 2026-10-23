"""
---------------------
- test_recording.py -
---------------------

What the executor remembers: the probe's winner becomes the site's strategy, stored strategies
count their successes and failures (only the failures that are the strategy's fault), and a
strategy that needed the site's cookies is marked requires_auth. Nothing is remembered when the
request says not to, and a database that cannot be written never fails a download.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.downloaders import FakeMedia
from support.engine import (
    FakeSitePlugin,
    StaticCookies,
    connection,
    cookie,
    databases,
    registry,
    script,
    settings,
)
from udl.chains.models import TagChain
from udl.config import ReadPolicy, Settings
from udl.core.executor import Execution, Executor, JobSpec, RetryPolicy
from udl.core.resolver import Resolver
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import Site, Strategy, StrategySource
from udl.errors import DatabaseError, ErrorCode, UDLError
from udl.plugins.downloaders.base import CancelToken

PAGE = "https://example.com/watch/1"
CHAIN = TagChain.model_validate("iframe#player@src >> video source@src")
SIGNED_IN = FakeMedia(content=b"members only", cookie="session=signed-in")


def strategy(tool: str, **fields: Any) -> Strategy:
    return Strategy.model_validate(
        {"site_key": "example.com", "tool": tool, "source": StrategySource.PROBE, **fields}
    )


async def execute(
    config: Settings,
    db: DBManager,
    *,
    cookies: StaticCookies | None = None,
    site_plugins: tuple[FakeSitePlugin, ...] = (),
    **spec: Any,
) -> Execution:
    toolbox = Toolbox(config, registry())
    executor = Executor(
        config,
        Resolver(config, toolbox, site_plugins),
        toolbox,
        cookies=cookies or StaticCookies(),
        retry=RetryPolicy(retries=0),
    )
    return await executor.run(JobSpec(url=URL.parse(PAGE), **spec), db=db, cancel=CancelToken())


async def fails(config: Settings, db: DBManager, **spec: Any) -> UDLError:
    with pytest.raises(UDLError) as caught:
        await execute(config, db, **spec)
    return caught.value


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DBManager]:
    async with databases(settings(tmp_path), registry()) as manager:
        yield manager


async def stored(db: DBManager, *strategies: Strategy) -> list[Strategy]:
    await db.upsert_site(Site(key="example.com"))
    return [await db.upsert_strategy(s) for s in strategies]


# ---------------------------------------------------------------------------------------------
# Learning from the probe
# ---------------------------------------------------------------------------------------------


async def test_the_probe_s_winner_becomes_the_site_s_strategy(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(tmp_path, tools={"yt-dlp": script({PAGE: "video"})})

    execution = await execute(config, db)

    assert await db.get_site("example.com") is not None
    [learned] = await db.strategies_for("example.com")
    assert learned.id == execution.strategy_id
    assert (learned.tool, learned.source, learned.url_pattern) == ("yt-dlp", "probe", None)
    assert (learned.success_count, learned.failure_count) == (1, 0)
    assert learned.last_success_at is not None
    assert learned.requires_auth is False
    assert learned.enabled is True


async def test_a_probe_winner_that_needed_the_site_s_cookies_requires_auth(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(tmp_path, tools={"http": script({PAGE: SIGNED_IN})})

    await execute(config, db, cookies=StaticCookies([cookie()]))

    [learned] = await db.strategies_for("example.com")
    assert (learned.tool, learned.requires_auth) == ("http", True)


async def test_winning_with_a_tool_the_site_already_has_counts_on_its_strategy(
    tmp_path: Path, db: DBManager
) -> None:
    [existing] = await stored(db, strategy("http", success_count=4))
    config = settings(tmp_path, tools={"http": script({PAGE: "file"})})

    execution = await execute(config, db, reprobe=True)

    [counted] = await db.strategies_for("example.com")
    assert counted.id == existing.id == execution.strategy_id
    assert counted.success_count == 5


async def test_a_disabled_strategy_whose_tool_wins_again_is_enabled_again(
    tmp_path: Path, db: DBManager
) -> None:
    [existing] = await stored(db, strategy("http", enabled=False, consecutive_failures=3))
    config = settings(tmp_path, tools={"http": script({PAGE: SIGNED_IN})})

    await execute(config, db, cookies=StaticCookies([cookie()]))

    [revived] = await db.strategies_for("example.com")
    assert revived.id == existing.id
    assert (revived.enabled, revived.consecutive_failures, revived.requires_auth) == (
        True,
        0,
        True,
    )


async def test_a_probe_strategy_for_another_tool_is_a_new_strategy(
    tmp_path: Path, db: DBManager
) -> None:
    await stored(db, strategy("scraper", url_pattern="^/shorts/"))
    config = settings(tmp_path, tools={"scraper": script({PAGE: "page"})})

    await execute(config, db)

    strategies = await db.strategies_for("example.com")
    assert {(s.tool, s.url_pattern) for s in strategies} == {
        ("scraper", None),
        ("scraper", "^/shorts/"),
    }


async def test_failed_probe_candidates_are_not_stored(tmp_path: Path, db: DBManager) -> None:
    await fails(settings(tmp_path), db)

    assert await db.strategies_for("example.com") == []


async def test_the_site_s_settings_are_kept_when_its_first_strategy_is_learned(
    tmp_path: Path, db: DBManager
) -> None:
    await db.upsert_site(Site(key="example.com", name="Example", notes="mine"))
    config = settings(tmp_path, tools={"http": script({PAGE: "file"})})

    await execute(config, db)

    site = await db.get_site("example.com")
    assert site is not None
    assert (site.name, site.notes) == ("Example", "mine")


async def test_a_probe_winner_is_learned_apart_from_the_site_s_chain_for_that_tool(
    tmp_path: Path, db: DBManager
) -> None:
    [chained] = await stored(
        db, strategy("scraper", chain=CHAIN, source=StrategySource.MANUAL, enabled=False)
    )
    config = settings(tmp_path, tools={"scraper": script({PAGE: "page"})})

    execution = await execute(config, db)

    learned = [s for s in await db.strategies_for("example.com") if s.id != chained.id]
    assert [(s.tool, s.chain, s.id) for s in learned] == [("scraper", None, execution.strategy_id)]
    assert (await db.strategies_for("example.com")).count(chained) == 1


# ---------------------------------------------------------------------------------------------
# Counting on stored strategies
# ---------------------------------------------------------------------------------------------


async def test_a_stored_chain_is_followed_and_counts_its_success(
    tmp_path: Path, db: DBManager
) -> None:
    [chained] = await stored(db, strategy("scraper", chain=CHAIN, source=StrategySource.MANUAL))
    config = settings(tmp_path, tools={"scraper": script(chained={PAGE: "found by the chain"})})

    execution = await execute(config, db)

    assert execution.strategy_id == chained.id
    assert execution.result.files[0].read_bytes() == b"found by the chain"
    [counted] = await db.strategies_for("example.com")
    assert (counted.id, counted.success_count, counted.chain) == (chained.id, 1, CHAIN)


async def test_a_stored_strategy_that_works_counts_a_success(tmp_path: Path, db: DBManager) -> None:
    [scraper] = await stored(db, strategy("scraper", failure_count=1, consecutive_failures=1))
    config = settings(tmp_path, tools={"scraper": script({PAGE: "page"})})

    execution = await execute(config, db)

    [counted] = await db.strategies_for("example.com")
    assert execution.strategy_id == scraper.id
    assert (counted.success_count, counted.failure_count, counted.consecutive_failures) == (
        1,
        1,
        0,
    )


async def test_a_stored_strategy_that_fails_counts_a_failure(tmp_path: Path, db: DBManager) -> None:
    [scraper] = await stored(db, strategy("scraper", success_count=3))
    config = settings(
        tmp_path,
        tools={"scraper": script(errors={PAGE: "extraction_failed"}), "http": script({PAGE: "x"})},
    )

    execution = await execute(config, db)

    strategies = {s.tool: s for s in await db.strategies_for("example.com")}
    failed = strategies["scraper"]
    assert (failed.failure_count, failed.consecutive_failures) == (1, 1)
    assert failed.last_error is not None
    assert failed.last_error.code is ErrorCode.EXTRACTION_FAILED
    assert execution.strategy_id == strategies["http"].id != scraper.id


@pytest.mark.parametrize(
    "error", ["network_error", "rate_limited", "tool_unavailable", "not_found"]
)
async def test_failures_that_are_not_the_strategy_s_fault_are_not_counted(
    error: str, tmp_path: Path, db: DBManager
) -> None:
    await stored(db, strategy("scraper"))
    config = settings(tmp_path, probe_order=[], tools={"scraper": script(errors={PAGE: error})})

    await fails(config, db)

    [scraper] = await db.strategies_for("example.com")
    assert (scraper.failure_count, scraper.consecutive_failures) == (0, 0)


async def test_invalid_options_are_not_counted_against_a_strategy(
    tmp_path: Path, db: DBManager
) -> None:
    await stored(db, strategy("scraper", options={"no_such_option": 1}))

    await fails(settings(tmp_path, probe_order=[]), db)

    [scraper] = await db.strategies_for("example.com")
    assert scraper.failure_count == 0


async def test_a_stored_strategy_that_needed_the_site_s_cookies_now_requires_auth(
    tmp_path: Path, db: DBManager
) -> None:
    await stored(db, strategy("scraper", success_count=2))
    config = settings(tmp_path, tools={"scraper": script({PAGE: SIGNED_IN})})

    await execute(config, db, cookies=StaticCookies([cookie()]))

    [scraper] = await db.strategies_for("example.com")
    assert (scraper.requires_auth, scraper.success_count, scraper.failure_count) == (True, 3, 0)


async def test_a_strategy_in_a_database_udl_does_not_write_is_copied_where_it_does(
    tmp_path: Path,
) -> None:
    config = settings(
        tmp_path,
        connections=[
            connection("local", primary=True),
            connection("shared", priority=10, read_only=True),
        ],
        read_policy=ReadPolicy.MERGE,
        tools={"scraper": script({PAGE: "page"})},
    )
    async with databases(config, registry()) as db:
        shared = db.database("shared")
        await shared.upsert_site(Site(key="example.com", name="Example"))
        original = await shared.upsert_strategy(strategy("scraper", success_count=7))

        await execute(config, db)

        local = db.database("local")
        [copy] = await local.strategies_for("example.com")
        assert copy.id == original.id
        assert copy.success_count == 8
        assert await shared.strategies_for("example.com") == [original]
        site = await local.get_site("example.com")
        assert site is not None
        assert site.name == "Example"


async def test_a_shared_strategy_s_failure_is_counted_in_a_copy_beside_the_winner(
    tmp_path: Path,
) -> None:
    config = settings(
        tmp_path,
        connections=[
            connection("local", primary=True),
            connection("shared", priority=10, read_only=True),
        ],
        read_policy=ReadPolicy.MERGE,
        tools={"scraper": script(errors={PAGE: "extraction_failed"}), "http": script({PAGE: "x"})},
    )
    async with databases(config, registry()) as db:
        shared = db.database("shared")
        await shared.upsert_site(Site(key="example.com"))
        await shared.upsert_strategy(strategy("scraper"))

        await execute(config, db)

        local = {s.tool: s for s in await db.database("local").strategies_for("example.com")}
        assert local["scraper"].consecutive_failures == 1
        assert local["http"].success_count == 1


# ---------------------------------------------------------------------------------------------
# When nothing is remembered
# ---------------------------------------------------------------------------------------------


async def test_nothing_is_remembered_when_the_request_says_not_to(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(tmp_path, tools={"yt-dlp": script({PAGE: "video"})})

    execution = await execute(config, db, record=False)

    assert execution.strategy_id is None
    assert await db.get_site("example.com") is None


async def test_a_stored_strategy_counts_nothing_when_the_request_says_not_to(
    tmp_path: Path, db: DBManager
) -> None:
    [scraper] = await stored(db, strategy("scraper"))
    config = settings(tmp_path, tools={"scraper": script({PAGE: SIGNED_IN})})

    execution = await execute(config, db, record=False, cookies=StaticCookies([cookie()]))

    assert execution.strategy_id == scraper.id
    assert await db.strategies_for("example.com") == [scraper]


async def test_a_requested_tool_is_not_remembered(tmp_path: Path, db: DBManager) -> None:
    config = settings(tmp_path, tools={"http": script({PAGE: "file"})})

    execution = await execute(config, db, tool="http")

    assert execution.strategy_id is None
    assert await db.strategies_for("example.com") == []


async def test_a_site_plugin_s_strategy_is_not_remembered(tmp_path: Path, db: DBManager) -> None:
    config = settings(tmp_path, tools={"http": script({PAGE: "file"})})
    plugin = FakeSitePlugin("example.com", strategy=strategy("http", source="plugin"))

    execution = await execute(config, db, site_plugins=(plugin,))

    assert execution.strategy_id is None
    assert await db.strategies_for("example.com") == []


async def test_a_database_that_cannot_be_written_never_fails_a_download(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(tmp_path, tools={"http": script({PAGE: "file"})})

    class ReadOnly(DBManager):
        async def upsert_site(self, site: Site) -> Site:
            raise DatabaseError("the disk is full")

    async with ReadOnly(config.db, registry()) as db:
        execution = await execute(config, db)

    assert execution.result.files
    assert execution.strategy_id is None
    assert "the disk is full" in caplog.text


async def test_a_failure_that_cannot_be_counted_is_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(
        tmp_path,
        tools={"scraper": script(errors={PAGE: "extraction_failed"}), "http": script({PAGE: "x"})},
    )

    class Uncountable(DBManager):
        async def record_outcome(self, strategy_id: Any, outcome: Any) -> Strategy | None:
            raise DatabaseError("the disk is full")

    async with Uncountable(config.db, registry()) as db:
        await stored(db, strategy("scraper"))
        execution = await execute(config, db)

    assert execution.candidate.tool == "http"
    assert "the disk is full" in caplog.text
