"""
-----------------------------
- test_database_contract.py -
-----------------------------

The behaviour every database plugin must have. Each test runs once per plugin in CONNECTIONS;
a new plugin starts by adding itself there (and watching this suite fail).
"""

# System Imports
import asyncio
import re
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

# Package Imports
import pytest

# Local Imports
from udl.chains.models import TagChain
from udl.config import DBConnectionConfig
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    JobStatus,
    Outcome,
    Site,
    SiteQuery,
    Strategy,
    StrategySource,
    strategy_order,
    utcnow,
)
from udl.errors import AuthRequired, DatabaseError, NetworkError
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.registry import DATABASES, PluginRegistry

# How to connect each plugin to a fresh, empty database in a temporary directory.
CONNECTIONS: dict[str, Callable[[Path], DBConnectionConfig]] = {
    "memory": lambda _: DBConnectionConfig(name="contract", plugin="memory", url="memory://"),
    "sqlite": lambda tmp: DBConnectionConfig(
        name="contract", plugin="sqlite", url=f"sqlite+aiosqlite:///{tmp / 'udl.db'}"
    ),
}

T0 = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)


@pytest.fixture(params=sorted(CONNECTIONS))
async def db(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[DatabasePlugin]:
    """A connected database of each plugin, found the way udl finds it (by entry point)."""
    connection = CONNECTIONS[request.param](tmp_path)
    plugin = PluginRegistry.discover().get(DATABASES, connection.plugin)
    async with plugin(connection) as database:
        yield database


def make_site(key: str = "example.com", **fields: object) -> Site:
    return Site.model_validate({"key": key, **fields})


def make_strategy(site_key: str = "example.com", **fields: object) -> Strategy:
    return Strategy.model_validate(
        {"site_key": site_key, "tool": "yt-dlp", "source": StrategySource.PROBE, **fields}
    )


def make_download(site_key: str = "example.com", **fields: object) -> DownloadRecord:
    return DownloadRecord.model_validate(
        {"url": f"https://{site_key}/v/{uuid4()}", "site_key": site_key, **fields}
    )


async def stored_strategy(
    db: DatabasePlugin, site_key: str = "example.com", **fields: object
) -> Strategy:
    """Store a site (if needed) and a strategy for it."""
    strategy = make_strategy(site_key, **fields)
    if await db.get_site(strategy.site_key) is None:
        await db.upsert_site(make_site(strategy.site_key))
    return await db.upsert_strategy(strategy)


# ---------------------------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------------------------


async def test_an_unknown_site_is_none(db: DatabasePlugin) -> None:
    assert await db.get_site("example.com") is None


async def test_a_stored_site_round_trips(db: DatabasePlugin) -> None:
    site = make_site(
        name="Example",
        default_output_dir=Path("/media/example"),
        notes="needs a login",
        created_at=T0,
        # Stored as the same instant, whatever the timezone.
        updated_at=T0.astimezone(timezone(timedelta(hours=10))),
    )

    assert await db.upsert_site(site) == site
    assert await db.get_site("example.com") == site


async def test_upserting_a_stored_key_updates_it_keeping_its_id_and_created_at(
    db: DatabasePlugin,
) -> None:
    original = await db.upsert_site(make_site(name="Old", created_at=T0, updated_at=T0))
    before = utcnow()

    updated = await db.upsert_site(
        make_site(name="New", default_output_dir=Path("/media"), notes="moved")
    )

    assert (updated.id, updated.created_at) == (original.id, T0)
    assert (updated.name, updated.default_output_dir, updated.notes) == (
        "New",
        Path("/media"),
        "moved",
    )
    assert updated.updated_at >= before
    assert await db.get_site("example.com") == updated
    assert len(await db.list_sites(SiteQuery())) == 1


async def test_sites_are_listed_by_key(db: DatabasePlugin) -> None:
    for key in ["b.com", "c.com", "a.com"]:
        await db.upsert_site(make_site(key))

    assert [site.key for site in await db.list_sites(SiteQuery())] == ["a.com", "b.com", "c.com"]


@pytest.mark.parametrize(
    ("search", "keys"),
    [
        ("TUBE", ["example.com", "youtube.com"]),  # key or name, any case
        ("a_b", ["a_b.com"]),  # `_` and `%` are not wildcards
        ("%", []),
    ],
)
async def test_site_search_finds_text_in_the_key_or_name(
    db: DatabasePlugin, search: str, keys: list[str]
) -> None:
    await db.upsert_site(make_site("youtube.com"))
    await db.upsert_site(make_site("example.com", name="Tube sites"))
    await db.upsert_site(make_site("vimeo.com"))
    await db.upsert_site(make_site("a_b.com"))
    await db.upsert_site(make_site("axb.com"))

    found = await db.list_sites(SiteQuery(search=search))

    assert [site.key for site in found] == keys


async def test_sites_are_listed_a_page_at_a_time(db: DatabasePlugin) -> None:
    keys = [f"site{n}.com" for n in range(5)]
    for key in keys:
        await db.upsert_site(make_site(key))

    page = await db.list_sites(SiteQuery(limit=2, offset=1))
    rest = await db.list_sites(SiteQuery(offset=3))

    assert [site.key for site in page] == keys[1:3]
    assert [site.key for site in rest] == keys[3:]


async def test_deleting_a_site_removes_it_and_its_strategies(db: DatabasePlugin) -> None:
    await stored_strategy(db, site_key="gone.com")
    kept = await stored_strategy(db, site_key="kept.com")

    assert await db.delete_site("gone.com") is True

    assert await db.get_site("gone.com") is None
    assert await db.strategies_for("gone.com") == []
    assert await db.strategies_for("kept.com") == [kept]


async def test_deleting_an_unknown_site_returns_false(db: DatabasePlugin) -> None:
    assert await db.delete_site("example.com") is False


async def test_deleting_a_site_keeps_its_download_history(db: DatabasePlugin) -> None:
    await db.upsert_site(make_site())
    record = make_download()
    await db.add_download(record)

    await db.delete_site("example.com")

    assert await db.list_downloads(DownloadQuery()) == [record]


# ---------------------------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------------------------


async def test_a_site_without_strategies_has_none(db: DatabasePlugin) -> None:
    await db.upsert_site(make_site())

    assert await db.strategies_for("example.com") == []
    assert await db.strategies_for("unknown.com") == []


async def test_a_stored_strategy_round_trips(db: DatabasePlugin) -> None:
    await db.upsert_site(make_site())
    strategy = make_strategy(
        url_pattern="^/shorts/",
        options={"format": "bv*+ba/b", "retries": 3, "nested": {"list": [1, None, True]}},
        requires_auth=True,
        source=StrategySource.MANUAL,
        priority=-5,
        success_count=7,
        failure_count=2,
        consecutive_failures=1,
        last_success_at=T0,
        last_error=AuthRequired("Sign in", tool="yt-dlp", hint="udl browser login").to_info(),
        enabled=False,
    )

    assert await db.upsert_strategy(strategy) == strategy
    assert await db.strategies_for("example.com") == [strategy]


async def test_a_strategy_s_tag_chain_comes_back_as_a_tag_chain(db: DatabasePlugin) -> None:
    await db.upsert_site(make_site())
    chain = TagChain.model_validate(
        {
            "headers": {"Referer": "{url}"},
            "steps": [
                {"select": "iframe#player", "attr": "src"},
                {"select": "script#data", "text": True},
                {"json": "video.sources.*.url", "pick": "best"},
            ],
            "handoff": "yt-dlp",
        }
    )
    strategy = make_strategy(tool="scraper", chain=chain, source=StrategySource.MANUAL)

    assert await db.upsert_strategy(strategy) == strategy
    [stored] = await db.strategies_for("example.com")
    assert stored.chain == chain
    assert make_strategy().chain is None


async def test_a_strategy_for_a_site_that_is_not_stored_is_rejected(db: DatabasePlugin) -> None:
    with pytest.raises(DatabaseError, match=re.escape("example.com")):
        await db.upsert_strategy(make_strategy())

    assert await db.strategies_for("example.com") == []


async def test_upserting_a_stored_strategy_replaces_it(db: DatabasePlugin) -> None:
    original = await stored_strategy(db, tool="yt-dlp")
    replacement = original.model_copy(update={"tool": "gallery-dl", "options": {"a": 1}})

    assert await db.upsert_strategy(replacement) == replacement
    assert await db.strategies_for("example.com") == [replacement]


async def test_strategies_come_in_strategy_order(db: DatabasePlugin) -> None:
    await db.upsert_site(make_site())
    strategies = [
        make_strategy(priority=priority, success_count=successes)
        for priority, successes in [(1, 0), (0, 3), (0, 9), (-1, 0), (0, 3), (1, 50)]
    ]
    for strategy in strategies:
        await db.upsert_strategy(strategy)

    assert await db.strategies_for("example.com") == sorted(strategies, key=strategy_order)


async def test_a_success_is_counted_and_ends_the_failure_streak(db: DatabasePlugin) -> None:
    error = NetworkError("timed out").to_info()
    strategy = await stored_strategy(
        db, success_count=2, failure_count=4, consecutive_failures=3, last_error=error
    )

    updated = await db.record_outcome(strategy.id, Outcome(ok=True, at=T0))

    expected = strategy.model_copy(
        update={"success_count": 3, "consecutive_failures": 0, "last_success_at": T0}
    )
    assert updated == expected
    assert await db.strategies_for("example.com") == [expected]


async def test_a_failure_is_counted_and_extends_the_failure_streak(db: DatabasePlugin) -> None:
    strategy = await stored_strategy(
        db, success_count=2, failure_count=4, consecutive_failures=1, last_success_at=T0
    )
    error = AuthRequired("Sign in", tool="yt-dlp").to_info()

    updated = await db.record_outcome(strategy.id, Outcome(ok=False, error=error))

    expected = strategy.model_copy(
        update={"failure_count": 5, "consecutive_failures": 2, "last_error": error}
    )
    assert updated == expected
    assert await db.strategies_for("example.com") == [expected]


async def test_an_outcome_for_an_unknown_strategy_returns_none(db: DatabasePlugin) -> None:
    assert await db.record_outcome(uuid4(), Outcome(ok=True)) is None


async def test_concurrent_outcomes_are_all_counted(db: DatabasePlugin) -> None:
    strategy = await stored_strategy(db)

    await asyncio.gather(
        *(db.record_outcome(strategy.id, Outcome(ok=n % 2 == 0)) for n in range(20))
    )

    [counted] = await db.strategies_for("example.com")
    assert (counted.success_count, counted.failure_count) == (10, 10)


# ---------------------------------------------------------------------------------------------
# Download history
# ---------------------------------------------------------------------------------------------


async def test_a_stored_download_round_trips(db: DatabasePlugin) -> None:
    record = make_download(
        strategy_id=uuid4(),
        tool="http",
        status=JobStatus.FAILED,
        output_dir=Path("/media/example"),
        files=[Path("/media/example/a.mp4"), Path("/media/example/b.mp4")],
        size_bytes=123_456_789_012,
        started_at=T0,
        finished_at=T0 + timedelta(minutes=5),
        error=NetworkError("connection reset", tool="http").to_info(),
    )

    await db.add_download(record)

    assert await db.list_downloads(DownloadQuery()) == [record]


async def test_a_download_is_found_by_its_id(db: DatabasePlugin) -> None:
    wanted, other = make_download(), make_download()
    await db.add_download(wanted)
    await db.add_download(other)

    assert await db.get_download(wanted.id) == wanted


async def test_an_unknown_download_is_none(db: DatabasePlugin) -> None:
    await db.add_download(make_download())

    assert await db.get_download(uuid4()) is None


async def test_adding_a_download_twice_is_rejected(db: DatabasePlugin) -> None:
    record = make_download()
    await db.add_download(record)

    with pytest.raises(DatabaseError, match=str(record.id)):
        await db.add_download(record.model_copy(update={"tool": "http"}))

    assert await db.list_downloads(DownloadQuery()) == [record]


async def test_updating_a_download_replaces_it(db: DatabasePlugin) -> None:
    record = make_download()
    await db.add_download(record)
    finished = record.model_copy(
        update={"status": JobStatus.FINISHED, "files": [Path("/media/a.mp4")], "finished_at": T0}
    )

    await db.update_download(finished)

    assert await db.list_downloads(DownloadQuery()) == [finished]


async def test_updating_a_download_that_is_not_stored_is_rejected(db: DatabasePlugin) -> None:
    record = make_download()

    with pytest.raises(DatabaseError, match=str(record.id)):
        await db.update_download(record)

    assert await db.list_downloads(DownloadQuery()) == []


async def test_downloads_are_listed_newest_first(db: DatabasePlugin) -> None:
    records = [make_download(started_at=T0 + timedelta(seconds=n)) for n in (2, 0, 1)]
    for record in records:
        await db.add_download(record)

    listed = await db.list_downloads(DownloadQuery())

    assert listed == sorted(records, key=lambda r: r.started_at, reverse=True)


async def test_downloads_started_at_the_same_time_are_listed_by_id(db: DatabasePlugin) -> None:
    records = [make_download(started_at=T0) for _ in range(5)]
    for record in records:
        await db.add_download(record)

    listed = await db.list_downloads(DownloadQuery())

    assert listed == sorted(records, key=lambda r: r.id, reverse=True)


async def test_downloads_can_be_filtered_by_site_and_status(db: DatabasePlugin) -> None:
    wanted = make_download("a.com", status=JobStatus.FINISHED)
    for record in [
        wanted,
        make_download("a.com", status=JobStatus.FAILED),
        make_download("b.com", status=JobStatus.FINISHED),
    ]:
        await db.add_download(record)

    by_site = await db.list_downloads(DownloadQuery(site_key="a.com"))
    by_status = await db.list_downloads(DownloadQuery(status=JobStatus.FINISHED))
    by_both = await db.list_downloads(DownloadQuery(site_key="a.com", status=JobStatus.FINISHED))

    assert {r.status for r in by_site} == {JobStatus.FINISHED, JobStatus.FAILED}
    assert {r.site_key for r in by_status} == {"a.com", "b.com"}
    assert by_both == [wanted]


async def test_downloads_are_listed_a_page_at_a_time(db: DatabasePlugin) -> None:
    records = [make_download(started_at=T0 - timedelta(seconds=n)) for n in range(5)]
    for record in records:
        await db.add_download(record)

    page = await db.list_downloads(DownloadQuery(limit=2, offset=1))
    rest = await db.list_downloads(DownloadQuery(offset=3))

    assert page == records[1:3]
    assert rest == records[3:]


# ---------------------------------------------------------------------------------------------
# The database itself
# ---------------------------------------------------------------------------------------------


async def test_migrate_is_idempotent_and_keeps_data(db: DatabasePlugin) -> None:
    strategy = await stored_strategy(db)
    record = make_download()
    await db.add_download(record)

    await db.migrate()
    await db.migrate()

    assert await db.strategies_for("example.com") == [strategy]
    assert await db.list_downloads(DownloadQuery()) == [record]


async def test_a_connected_database_is_healthy_until_closed(db: DatabasePlugin) -> None:
    assert await db.health() is True

    await db.close()

    assert await db.health() is False


async def test_changing_a_model_does_not_change_the_stored_one(db: DatabasePlugin) -> None:
    site = make_site(name="Example")
    returned = await db.upsert_site(site)
    strategy = await stored_strategy(db, options={"format": "best"})

    site.name = "Changed"
    returned.name = "Changed"
    strategy.options["format"] = "worst"
    [listed] = await db.strategies_for("example.com")
    listed.options["format"] = "worst"

    stored_site = await db.get_site("example.com")
    assert stored_site is not None
    assert stored_site.name == "Example"
    assert [s.options for s in await db.strategies_for("example.com")] == [{"format": "best"}]


async def test_concurrent_upserts_of_a_new_site_store_it_once(db: DatabasePlugin) -> None:
    results = await asyncio.gather(*(db.upsert_site(make_site(name=str(n))) for n in range(10)))

    [stored] = await db.list_sites(SiteQuery())
    assert {site.id for site in results} == {stored.id}
