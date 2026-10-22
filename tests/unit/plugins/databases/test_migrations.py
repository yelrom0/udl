"""
----------------------
- test_migrations.py -
----------------------

Schema versioning in SQL plugins: the schema_version table, and migrate() bringing a database
made by an older udl up to date (run here through the sqlite plugin)
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
from udl.config import DBConnectionConfig
from udl.db.models import Site
from udl.errors import DatabaseError
from udl.plugins.databases.sqlite import SQLitePlugin

ran: list[str] = []


def add_rating(conn: Connection) -> None:
    ran.append("add_rating")
    conn.exec_driver_sql("ALTER TABLE site ADD COLUMN rating INTEGER")


def add_rating_then_fail(conn: Connection) -> None:
    add_rating(conn)
    raise RuntimeError("disk on fire")


class NextVersion(SQLitePlugin):
    """The sqlite plugin of a later udl, with one more migration."""

    migrations = (add_rating,)


class BrokenNextVersion(SQLitePlugin):
    migrations = (add_rating_then_fail,)


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


def site_columns(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as raw:
        return [row[1] for row in raw.execute("PRAGMA table_info(site)").fetchall()]


async def test_a_new_database_is_stamped_with_the_current_schema_version(path: Path) -> None:
    async with SQLitePlugin(connection(path)) as db:
        assert db.schema_version == 1

    assert stored_version(path) == 1


async def test_a_new_database_gets_the_current_tables_without_migrating(path: Path) -> None:
    async with NextVersion(connection(path)) as db:
        assert db.schema_version == 2

    assert stored_version(path) == 2
    assert ran == []


async def test_an_older_database_is_migrated_and_keeps_its_data(path: Path) -> None:
    async with SQLitePlugin(connection(path)) as old:
        site = await old.upsert_site(Site(key="example.com"))

    async with NextVersion(connection(path)) as new:
        assert await new.get_site("example.com") == site

    assert ran == ["add_rating"]
    assert stored_version(path) == 2
    assert "rating" in site_columns(path)


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
        match=re.escape("Could not migrate the 'local' database to schema version 2"),
    ) as excinfo:
        await broken.connect()
    await broken.close()

    assert "disk on fire" in str(excinfo.value)
    assert stored_version(path) == 1
    assert "rating" not in site_columns(path)
    async with SQLitePlugin(connection(path)) as old:
        assert await old.get_site("example.com") == site


async def test_a_database_from_a_newer_udl_is_refused(path: Path) -> None:
    async with NextVersion(connection(path)):
        pass
    old = SQLitePlugin(connection(path))

    with pytest.raises(DatabaseError, match="schema version 2") as excinfo:
        await old.connect()
    await old.close()

    assert excinfo.value.hint is not None
    assert "upgrade udl" in excinfo.value.hint.lower()
    assert stored_version(path) == 2
