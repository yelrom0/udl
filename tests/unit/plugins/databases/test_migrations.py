"""
----------------------
- test_migrations.py -
----------------------

Schema versioning in SQL plugins: the schema_version table, and migrate() bringing a database
made by an older udl up to date (run here through the sqlite plugin), and udl's own migrations
"""

# System Imports
import re
import sqlite3
from contextlib import closing
from pathlib import Path

# Package Imports
import pytest
from sqlalchemy import Connection

# Local Imports
from udl.chains.models import TagChain
from udl.config import DBConnectionConfig
from udl.db.models import Site, Strategy, StrategySource
from udl.errors import DatabaseError
from udl.plugins.databases.sqlite import SQLitePlugin

ran: list[str] = []


def add_rating(conn: Connection) -> None:
    ran.append("add_rating")
    conn.exec_driver_sql("ALTER TABLE site ADD COLUMN rating INTEGER")


def add_rating_then_fail(conn: Connection) -> None:
    add_rating(conn)
    raise RuntimeError("disk on fire")


CURRENT = SQLitePlugin.migrations
NEXT = len(CURRENT) + 2  # the schema version of a later udl


class NextVersion(SQLitePlugin):
    """The sqlite plugin of a later udl, with one more migration."""

    migrations = (*CURRENT, add_rating)


class BrokenNextVersion(SQLitePlugin):
    migrations = (*CURRENT, add_rating_then_fail)


@pytest.fixture(autouse=True)
def forget_runs() -> None:
    ran.clear()


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "udl.db"


def connection(path: Path) -> DBConnectionConfig:
    return DBConnectionConfig(name="local", plugin="sqlite", url=f"sqlite:///{path}")


def stored_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as raw:
        [(version,)] = raw.execute("SELECT version FROM schema_version").fetchall()
    return version


def columns(path: Path, table: str = "site") -> list[str]:
    with closing(sqlite3.connect(path)) as raw:
        return [row[1] for row in raw.execute(f"PRAGMA table_info({table})").fetchall()]


async def test_a_new_database_is_stamped_with_the_current_schema_version(path: Path) -> None:
    async with SQLitePlugin(connection(path)) as db:
        assert db.schema_version == 2

    assert stored_version(path) == 2


async def test_a_new_database_gets_the_current_tables_without_migrating(path: Path) -> None:
    async with NextVersion(connection(path)) as db:
        assert db.schema_version == NEXT

    assert stored_version(path) == NEXT
    assert ran == []


async def test_an_older_database_is_migrated_and_keeps_its_data(path: Path) -> None:
    async with SQLitePlugin(connection(path)) as old:
        site = await old.upsert_site(Site(key="example.com"))

    async with NextVersion(connection(path)) as new:
        assert await new.get_site("example.com") == site

    assert ran == ["add_rating"]
    assert stored_version(path) == NEXT
    assert "rating" in columns(path)


async def test_each_migration_runs_once(path: Path) -> None:
    async with SQLitePlugin(connection(path)):
        pass

    for _ in range(2):
        async with NextVersion(connection(path)) as db:
            await db.migrate()

    assert ran == ["add_rating"]


async def test_a_failed_migration_leaves_the_database_as_it_was(path: Path) -> None:
    async with SQLitePlugin(connection(path)) as old:
        site = await old.upsert_site(Site(key="example.com"))
    broken = BrokenNextVersion(connection(path))

    with pytest.raises(
        DatabaseError,
        match=re.escape(f"Could not migrate the 'local' database to schema version {NEXT}"),
    ) as excinfo:
        await broken.connect()
    await broken.close()

    assert "disk on fire" in str(excinfo.value)
    assert stored_version(path) == NEXT - 1
    assert "rating" not in columns(path)
    async with SQLitePlugin(connection(path)) as old:
        assert await old.get_site("example.com") == site


async def test_a_database_from_a_newer_udl_is_refused(path: Path) -> None:
    async with NextVersion(connection(path)):
        pass
    old = SQLitePlugin(connection(path))

    with pytest.raises(DatabaseError, match=f"schema version {NEXT}") as excinfo:
        await old.connect()
    await old.close()

    assert excinfo.value.hint is not None
    assert "upgrade udl" in excinfo.value.hint.lower()
    assert stored_version(path) == NEXT


# ---------------------------------------------------------------------------------------------
# udl's migrations
# ---------------------------------------------------------------------------------------------


def as_version_1(path: Path) -> None:
    """Make the database at `path` as udl 0.1 made it (schema version 1): no tag chains."""
    with closing(sqlite3.connect(path)) as raw, raw:
        raw.execute("ALTER TABLE strategy DROP COLUMN chain")
        raw.execute("UPDATE schema_version SET version = 1")


async def test_version_2_adds_strategies_tag_chains(path: Path) -> None:
    async with SQLitePlugin(connection(path)) as db:
        await db.upsert_site(Site(key="example.com"))
        learned = await db.upsert_strategy(
            Strategy(site_key="example.com", tool="yt-dlp", source=StrategySource.PROBE)
        )
    as_version_1(path)
    assert "chain" not in columns(path, "strategy")

    chain = TagChain.model_validate({"steps": [{"select": "video", "attr": "src"}]})
    async with SQLitePlugin(connection(path)) as db:
        assert await db.strategies_for("example.com") == [learned]
        chained = await db.upsert_strategy(
            Strategy(
                site_key="example.com", tool="scraper", chain=chain, source=StrategySource.MANUAL
            )
        )
        assert chained in await db.strategies_for("example.com")

    assert stored_version(path) == 2
    assert "chain" in columns(path, "strategy")
