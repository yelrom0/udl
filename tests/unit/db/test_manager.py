"""
-------------------
- test_manager.py -
-------------------

DBManager: several database connections used as one (read policy, write policy, failures)
"""

# System Imports
import logging
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

# Package Imports
import pytest

# Local Imports
from udl.config import DBConnectionConfig, DBSettings, ReadPolicy, WritePolicy
from udl.db.manager import DBManager
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    Outcome,
    Site,
    SiteQuery,
    Strategy,
    StrategySource,
    strategy_order,
)
from udl.errors import DatabaseError, DoesNotExist, PluginError
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.databases.memory import MemoryDatabase
from udl.plugins.registry import DATABASES, PluginRegistry

T0 = datetime(2026, 1, 2, tzinfo=UTC)


class BrokenDatabase(MemoryDatabase):
    """A memory database that fails on purpose. Its URL lists what fails, e.g. `broken://read`."""

    name = "broken"

    def __init__(self, connection: DBConnectionConfig) -> None:
        super().__init__(connection)
        self.failing = set(connection.url.removeprefix("broken://").split(","))

    def _fail(self, operation: str) -> None:
        if operation in self.failing:
            raise ConnectionError(f"{self.connection.name} is unreachable")

    async def connect(self) -> None:
        self._fail("connect")
        await super().connect()

    async def get_site(self, key: str) -> Site | None:
        self._fail("read")
        return await super().get_site(key)

    async def strategies_for(self, site_key: str) -> list[Strategy]:
        self._fail("read")
        return await super().strategies_for(site_key)

    async def upsert_site(self, site: Site) -> Site:
        self._fail("write")
        return await super().upsert_site(site)


@pytest.fixture
def registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(DATABASES, MemoryDatabase)
    registry.register(DATABASES, BrokenDatabase)
    return registry


def conn(
    name: str, priority: int = 0, url: str = "memory://", **fields: object
) -> DBConnectionConfig:
    plugin = "broken" if url.startswith("broken://") else "memory"
    return DBConnectionConfig.model_validate(
        {"name": name, "plugin": plugin, "url": url, "priority": priority, **fields}
    )


def make_manager(
    registry: PluginRegistry,
    *connections: DBConnectionConfig,
    read_policy: ReadPolicy = ReadPolicy.FIRST,
    write_policy: WritePolicy | list[str] = WritePolicy.PRIMARY,
) -> DBManager:
    settings = DBSettings(
        connections=list(connections), read_policy=read_policy, write_policy=write_policy
    )
    return DBManager(settings, registry)


def make_strategy(site_key: str = "example.com", **fields: object) -> Strategy:
    return Strategy.model_validate(
        {"site_key": site_key, "tool": "yt-dlp", "source": StrategySource.PROBE, **fields}
    )


async def store(db: DatabasePlugin, *strategies: Strategy) -> list[Strategy]:
    """Store each strategy (and its site) straight into one connection's database."""
    for strategy in strategies:
        if await db.get_site(strategy.site_key) is None:
            await db.upsert_site(Site(key=strategy.site_key))
        await db.upsert_strategy(strategy)
    return list(strategies)


async def site_keys(db: DatabasePlugin) -> list[str]:
    return [site.key for site in await db.list_sites(SiteQuery())]


# ---------------------------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------------------------


def test_connections_are_built_from_the_settings_in_priority_order(
    registry: PluginRegistry,
) -> None:
    shared, local, other = conn("shared", priority=10), conn("local"), conn("other")

    manager = make_manager(registry, shared, local, other)

    assert manager.connections == [local, other, shared]  # config order breaks ties
    database = manager.database("shared")
    assert isinstance(database, MemoryDatabase)
    assert database.connection == shared


def test_an_unknown_plugin_raises_plugin_error(registry: PluginRegistry) -> None:
    with pytest.raises(PluginError, match="'postgres'"):
        make_manager(registry, conn("shared", plugin="postgres"))


def test_an_unknown_connection_name_raises_database_error(registry: PluginRegistry) -> None:
    manager = make_manager(registry, conn("local"))

    with pytest.raises(DatabaseError, match=re.escape("No database connection named 'nope'")):
        manager.database("nope")


async def test_connect_and_close_open_and_close_every_connection(
    registry: PluginRegistry,
) -> None:
    manager = make_manager(registry, conn("local"), conn("shared"))
    databases = [manager.database("local"), manager.database("shared")]

    async with manager:
        assert [await db.health() for db in databases] == [True, True]

    assert [await db.health() for db in databases] == [False, False]


# ---------------------------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("policy", list(ReadPolicy))
async def test_a_site_comes_from_the_highest_priority_connection_that_has_it(
    registry: PluginRegistry, policy: ReadPolicy
) -> None:
    async with make_manager(
        registry,
        conn("shared", priority=10),
        conn("local"),
        conn("empty", priority=-1),
        read_policy=policy,
    ) as manager:
        await manager.database("shared").upsert_site(Site(key="example.com", name="shared"))
        await manager.database("shared").upsert_site(Site(key="only-shared.com"))
        await manager.database("local").upsert_site(Site(key="example.com", name="local"))

        found = await manager.get_site("example.com")
        only_shared = await manager.get_site("only-shared.com")

        assert found is not None
        assert found.name == "local"
        assert only_shared is not None
        assert await manager.get_site("unknown.com") is None


async def test_first_takes_the_strategies_of_the_highest_priority_connection_with_any(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry, conn("local"), conn("shared", priority=10), read_policy=ReadPolicy.FIRST
    ) as manager:
        local, shared = manager.database("local"), manager.database("shared")
        mine = await store(local, make_strategy(tool="http"))
        await store(shared, make_strategy(tool="yt-dlp"), make_strategy(tool="scraper"))
        only_theirs = await store(shared, make_strategy("other.com"))

        assert await manager.strategies_for("example.com") == mine
        assert await manager.strategies_for("other.com") == only_theirs
        assert await manager.strategies_for("unknown.com") == []


async def test_merge_combines_strategies_keeping_the_highest_priority_copy_of_each(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry, conn("local"), conn("shared", priority=10), read_policy=ReadPolicy.MERGE
    ) as manager:
        [local_ytdlp] = await store(manager.database("local"), make_strategy(priority=5))
        shared = await store(
            manager.database("shared"),
            make_strategy(priority=0),  # the same (site, pattern, tool) as local's: hidden
            make_strategy(url_pattern="^/shorts/"),
            make_strategy(tool="gallery-dl", success_count=4),
        )

        merged = await manager.strategies_for("example.com")

        assert merged == sorted([local_ytdlp, *shared[1:]], key=strategy_order)


@pytest.mark.parametrize("policy", list(ReadPolicy))
async def test_sites_are_listed_from_every_connection(
    registry: PluginRegistry, policy: ReadPolicy
) -> None:
    async with make_manager(
        registry, conn("local"), conn("shared", priority=10), read_policy=policy
    ) as manager:
        for key, name in [("b.com", "local"), ("d.com", "local")]:
            await manager.database("local").upsert_site(Site(key=key, name=name))
        for key in ["a.com", "b.com", "c.com", "e.com"]:
            await manager.database("shared").upsert_site(Site(key=key, name="shared"))

        everything = await manager.list_sites(SiteQuery())
        page = await manager.list_sites(SiteQuery(limit=2, offset=1))
        found = await manager.list_sites(SiteQuery(search="local"))

        assert [(s.key, s.name) for s in everything] == [
            ("a.com", "shared"),
            ("b.com", "local"),
            ("c.com", "shared"),
            ("d.com", "local"),
            ("e.com", "shared"),
        ]
        assert [s.key for s in page] == ["b.com", "c.com"]
        assert [s.key for s in found] == ["b.com", "d.com"]


@pytest.mark.parametrize("policy", list(ReadPolicy))
async def test_downloads_are_listed_from_every_connection(
    registry: PluginRegistry, policy: ReadPolicy
) -> None:
    def download(seconds: int, **fields: object) -> DownloadRecord:
        return DownloadRecord.model_validate(
            {
                "url": "https://example.com/v",
                "site_key": "example.com",
                "started_at": T0 + timedelta(seconds=seconds),
                **fields,
            }
        )

    async with make_manager(
        registry, conn("local"), conn("shared", priority=10), read_policy=policy
    ) as manager:
        local, shared = manager.database("local"), manager.database("shared")
        copied = download(2)
        records = [download(4), copied, download(3, site_key="other.com"), download(1)]
        for record in records[:2]:
            await local.add_download(record)
        for record in records[1:]:
            await shared.add_download(record)

        everything = await manager.list_downloads(DownloadQuery())
        page = await manager.list_downloads(DownloadQuery(limit=2, offset=1))
        filtered = await manager.list_downloads(DownloadQuery(site_key="example.com"))

        newest_first = sorted(records, key=lambda r: r.started_at, reverse=True)
        assert everything == newest_first
        assert page == newest_first[1:3]
        assert filtered == [r for r in newest_first if r.site_key == "example.com"]


async def test_a_download_comes_from_the_highest_priority_connection_that_has_it(
    registry: PluginRegistry,
) -> None:
    async with make_manager(registry, conn("local"), conn("shared", priority=10)) as manager:
        record = DownloadRecord(url="https://example.com/v", site_key="example.com")
        await manager.database("shared").add_download(record)
        await manager.database("local").add_download(record.model_copy(update={"tool": "http"}))
        only_shared = DownloadRecord(url="https://example.com/w", site_key="example.com")
        await manager.database("shared").add_download(only_shared)

        found = await manager.get_download(record.id)

        assert found is not None
        assert found.tool == "http"
        assert await manager.get_download(only_shared.id) == only_shared
        assert await manager.get_download(uuid4()) is None


# ---------------------------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------------------------


async def test_the_primary_policy_writes_only_to_the_primary(registry: PluginRegistry) -> None:
    async with make_manager(
        registry, conn("other", priority=-1), conn("local", primary=True)
    ) as manager:
        await manager.upsert_site(Site(key="example.com"))

        assert await site_keys(manager.database("local")) == ["example.com"]
        assert await site_keys(manager.database("other")) == []


async def test_without_a_primary_the_highest_priority_writable_connection_is_used(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry,
        conn("readonly", priority=-1, read_only=True),
        conn("second", priority=1),
        conn("first"),
    ) as manager:
        await manager.upsert_site(Site(key="example.com"))

        assert await site_keys(manager.database("first")) == ["example.com"]
        assert await site_keys(manager.database("second")) == []
        assert await site_keys(manager.database("readonly")) == []


async def test_the_all_policy_writes_to_every_connection_except_read_only_ones(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry,
        conn("local", primary=True),
        conn("backup", priority=5),
        conn("shared", priority=10, read_only=True),
        write_policy=WritePolicy.ALL,
    ) as manager:
        await manager.upsert_site(Site(key="example.com"))

        assert await site_keys(manager.database("local")) == ["example.com"]
        assert await site_keys(manager.database("backup")) == ["example.com"]
        assert await site_keys(manager.database("shared")) == []


async def test_a_list_write_policy_writes_to_the_named_connections(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry,
        conn("local", primary=True),
        conn("backup", priority=5),
        conn("shared", priority=10),
        write_policy=["backup", "shared"],
    ) as manager:
        await manager.upsert_site(Site(key="example.com"))

        assert await site_keys(manager.database("local")) == []
        assert await site_keys(manager.database("backup")) == ["example.com"]
        assert await site_keys(manager.database("shared")) == ["example.com"]


async def test_writes_return_the_primary_connections_answer(registry: PluginRegistry) -> None:
    async with make_manager(
        registry,
        conn("backup", priority=-1),
        conn("local", primary=True),
        write_policy=WritePolicy.ALL,
    ) as manager:
        known = await manager.database("local").upsert_site(Site(key="example.com"))

        returned = await manager.upsert_site(Site(key="example.com", name="Example"))
        [backup] = await manager.database("backup").list_sites(SiteQuery())

        assert returned.id == known.id
        assert backup.id != known.id


async def test_every_kind_of_write_reaches_every_write_connection(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry, conn("local", primary=True), conn("backup"), write_policy=WritePolicy.ALL
    ) as manager:
        databases = [manager.database("local"), manager.database("backup")]
        await manager.upsert_site(Site(key="gone.com"))
        await manager.upsert_site(Site(key="example.com"))
        strategy = await manager.upsert_strategy(make_strategy())
        updated = await manager.record_outcome(strategy.id, Outcome(ok=True, at=T0))
        record = DownloadRecord(url="https://example.com/v", site_key="example.com")
        await manager.add_download(record)
        finished = record.model_copy(update={"finished_at": T0})
        await manager.update_download(finished)
        deleted = await manager.delete_site("gone.com")

        assert updated is not None
        assert (updated.success_count, deleted) == (1, True)
        for db in databases:
            assert await site_keys(db) == ["example.com"]
            assert await db.strategies_for("example.com") == [updated]
            assert await db.list_downloads(DownloadQuery()) == [finished]


async def test_writing_with_no_writable_connection_raises_database_error(
    registry: PluginRegistry,
) -> None:
    async with make_manager(registry, conn("shared", read_only=True)) as manager:
        with pytest.raises(DatabaseError, match="No database connection to write to"):
            await manager.upsert_site(Site(key="example.com"))


async def test_record_outcome_for_a_strategy_the_primary_lacks_returns_none(
    registry: PluginRegistry,
) -> None:
    async with make_manager(registry, conn("local")) as manager:
        assert await manager.record_outcome(UUID(int=1), Outcome(ok=True)) is None


# ---------------------------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------------------------


async def test_a_failing_secondary_read_is_skipped_with_a_warning(
    registry: PluginRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    async with make_manager(
        registry,
        conn("local"),
        conn("shared", priority=10, url="broken://read"),
        read_policy=ReadPolicy.MERGE,
    ) as manager:
        mine = await store(manager.database("local"), make_strategy())

        assert await manager.strategies_for("example.com") == mine

    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "'shared'" in warning.getMessage()
    assert "shared is unreachable" in warning.getMessage()


async def test_a_failing_primary_read_raises_database_error(registry: PluginRegistry) -> None:
    async with make_manager(registry, conn("local", url="broken://read")) as manager:
        with pytest.raises(DatabaseError, match="'local' database failed: local is unreachable"):
            await manager.get_site("example.com")


async def test_a_failing_secondary_write_is_skipped_with_a_warning(
    registry: PluginRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    async with make_manager(
        registry,
        conn("local", primary=True),
        conn("backup", url="broken://write"),
        write_policy=WritePolicy.ALL,
    ) as manager:
        site = await manager.upsert_site(Site(key="example.com"))

        assert await manager.database("local").get_site("example.com") == site
        assert "'backup'" in caplog.text
        assert "backup is unreachable" in caplog.text


async def test_a_failing_primary_write_raises_and_nothing_else_is_written(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry,
        conn("local", primary=True, url="broken://write"),
        conn("backup", priority=-1),
        write_policy=WritePolicy.ALL,
    ) as manager:
        with pytest.raises(DatabaseError, match="'local' database failed"):
            await manager.upsert_site(Site(key="example.com"))

        assert await site_keys(manager.database("backup")) == []


async def test_a_secondary_that_cannot_connect_is_left_out_with_a_warning(
    registry: PluginRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    async with make_manager(
        registry,
        conn("local", primary=True),
        conn("shared", priority=-1, url="broken://connect"),
        write_policy=WritePolicy.ALL,
    ) as manager:
        site = await manager.upsert_site(Site(key="example.com"))

        assert await manager.get_site("example.com") == site
        assert await manager.list_sites(SiteQuery()) == [site]
        assert "'shared'" in caplog.text


async def test_a_primary_that_cannot_connect_raises_database_error(
    registry: PluginRegistry,
) -> None:
    manager = make_manager(registry, conn("local", url="broken://connect"))

    with pytest.raises(DatabaseError, match="'local' database failed: local is unreachable"):
        await manager.connect()


async def test_using_the_databases_before_connecting_raises_database_error(
    registry: PluginRegistry,
) -> None:
    manager = make_manager(registry, conn("local"))

    with pytest.raises(DatabaseError, match="not connected"):
        await manager.get_site("example.com")
    with pytest.raises(DatabaseError, match="not connected"):
        await manager.upsert_site(Site(key="example.com"))


async def test_a_database_error_from_the_primary_is_raised_unchanged(
    registry: PluginRegistry,
) -> None:
    record = DownloadRecord(url="https://example.com/v", site_key="example.com")
    async with make_manager(registry, conn("local")) as manager:
        await manager.add_download(record)

        with pytest.raises(DatabaseError, match=f"^Download {record.id} is already stored$"):
            await manager.add_download(record)


# ---------------------------------------------------------------------------------------------
# A view of some of the connections, and what is known about them
# ---------------------------------------------------------------------------------------------


async def test_the_primary_and_which_connections_are_connected(registry: PluginRegistry) -> None:
    manager = make_manager(
        registry, conn("local", primary=True), conn("shared", url="broken://connect", priority=1)
    )
    assert manager.primary == "local"
    assert not manager.is_connected("local")

    async with manager:
        assert manager.is_connected("local")
        assert not manager.is_connected("shared")

    assert not manager.is_connected("local")


async def test_a_view_reads_only_from_the_connections_it_names(registry: PluginRegistry) -> None:
    async with make_manager(
        registry, conn("local", primary=True), conn("shared", priority=10)
    ) as manager:
        await store(manager.database("local"), make_strategy(tool="http"))
        await store(manager.database("shared"), make_strategy(tool="scraper"))

        view = manager.only(["shared"])

        assert [s.tool for s in await view.strategies_for("example.com")] == ["scraper"]
        assert [c.name for c in view.connections] == ["shared"]
        assert view.primary == "shared"


async def test_a_view_writes_to_the_writable_connections_it_names_whatever_the_policy(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry,
        conn("local", primary=True),
        conn("shared", priority=10),
        conn("archive", priority=20, read_only=True),
    ) as manager:
        view = manager.only(["shared", "archive"])

        await view.upsert_site(Site(key="example.com"))

        assert await site_keys(manager.database("shared")) == ["example.com"]
        assert await site_keys(manager.database("local")) == []
        assert await site_keys(manager.database("archive")) == []


async def test_a_view_keeps_the_primary_when_it_names_it(registry: PluginRegistry) -> None:
    async with make_manager(
        registry, conn("other", priority=-1), conn("local", primary=True)
    ) as manager:
        view = manager.only(["other", "local"])

        await view.upsert_site(Site(key="example.com"))

        assert view.primary == "local"
        assert await site_keys(manager.database("local")) == ["example.com"]
        assert await site_keys(manager.database("other")) == ["example.com"]


async def test_a_view_of_read_only_connections_cannot_be_written(
    registry: PluginRegistry,
) -> None:
    async with make_manager(
        registry, conn("local", primary=True), conn("archive", read_only=True, priority=1)
    ) as manager:
        view = manager.only(["archive"])

        with pytest.raises(DatabaseError, match="No database connection to write to"):
            await view.upsert_site(Site(key="example.com"))


@pytest.mark.parametrize("names", [["nope"], ["local", "nope"], []])
def test_a_view_must_name_known_connections(registry: PluginRegistry, names: list[str]) -> None:
    manager = make_manager(registry, conn("local", primary=True))

    # (The request's fault, not a database's: what it names does not exist.)
    with pytest.raises(DoesNotExist, match="connection"):
        manager.only(names)
