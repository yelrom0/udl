"""
----------------------
- test_sqlalchemy.py -
----------------------

SQLAlchemyDatabase: what SQL plugins add to the database contract (tests/contract), including the
regression tests for the Phase 0 bugs (the per-class singleton, writes that were never committed)
"""

# System Imports
import asyncio
import re
import sqlite3
import threading
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

# Package Imports
import pytest
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError

# Local Imports
from udl.config import DBConnectionConfig
from udl.db.models import DownloadQuery, DownloadRecord, Site, SiteQuery
from udl.db.records import Lease, Record, RecordQuery, record_kinds
from udl.errors import ConfigError, DatabaseError
from udl.plugins.databases.sqlalchemy import SQLAlchemyDatabase

type MakeDB = Callable[[str], SQLAlchemyDatabase]


def connection(url: str, name: str = "test") -> DBConnectionConfig:
    return DBConnectionConfig(name=name, plugin="sqlalchemy", url=url)


@pytest.fixture
def make_db(tmp_path: Path) -> MakeDB:
    """Create (unconnected) databases in tmp_path, by file name."""

    def make(name: str) -> SQLAlchemyDatabase:
        return SQLAlchemyDatabase(connection(f"sqlite+aiosqlite:///{tmp_path / name}"))

    return make


def aiosqlite_workers() -> set[threading.Thread]:
    """The aiosqlite connections' worker threads running now (a thread's default name ends with
    its target's)."""
    return {t for t in threading.enumerate() if t.name.endswith("(_connection_worker_thread)")}


async def workers_ended(before: set[threading.Thread]) -> None:
    """Wait for the aiosqlite workers started since `before` to end, while this test's loop is
    still open.

    A connect that fails leaves its worker to stop on its own: aiosqlite (0.22.1) queues the stop
    with a future on this loop and never awaits it, and the worker's last act is a callback on that
    loop. If the test has ended first, the loop is closed, the worker raises "Event loop is closed",
    and the warning fails whichever test runs next on the worker (CI's four workers on four cores
    did, and so does pinning this module to one core).

    A worker that has already ended is not listed, so finding none is no failure here; that the
    workers are found by their name at all is test_an_open_connections_worker_is_found_by_name's."""
    for worker in aiosqlite_workers() - before:
        await asyncio.to_thread(worker.join, 10)
        assert not worker.is_alive(), f"{worker.name} did not end"


def table_names(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as raw:
        rows = raw.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {name for (name,) in rows}


async def test_instances_with_different_urls_store_data_separately(make_db: MakeDB) -> None:
    async with make_db("first.db") as first, make_db("second.db") as second:
        await first.upsert_site(Site(key="first.com"))

        assert [site.key for site in await first.list_sites(SiteQuery())] == ["first.com"]
        assert await second.list_sites(SiteQuery()) == []


async def test_writes_are_committed(make_db: MakeDB) -> None:
    site, record = (
        Site(key="example.com"),
        DownloadRecord(url="https://example.com/v", site_key="example.com"),
    )
    async with make_db("udl.db") as db:
        await db.upsert_site(site)
        await db.add_download(record)

    async with make_db("udl.db") as reopened:
        assert await reopened.get_site("example.com") == site
        assert await reopened.list_downloads(DownloadQuery()) == [record]


async def test_connect_creates_udls_tables_and_no_others(tmp_path: Path, make_db: MakeDB) -> None:
    async with make_db("udl.db"):
        pass

    assert table_names(tmp_path / "udl.db") == {
        "schema_version",
        "site",
        "strategy",
        "download_record",
        *(f"record_{kind.kind}" for kind in record_kinds()),  # (a table for each kind of record)
    }


async def test_connect_and_close_can_be_repeated(make_db: MakeDB) -> None:
    db = make_db("udl.db")

    await db.connect()
    await db.connect()
    await db.close()
    await db.close()
    async with db:
        assert await db.health() is True


async def test_using_a_database_before_connecting_raises_database_error(make_db: MakeDB) -> None:
    db = make_db("udl.db")

    with pytest.raises(DatabaseError, match=re.escape("'test' database is not connected")):
        await db.get_site("example.com")


async def test_a_database_that_cannot_be_opened_raises_database_error(tmp_path: Path) -> None:
    db = SQLAlchemyDatabase(
        connection(f"sqlite+aiosqlite:///{tmp_path / 'missing' / 'udl.db'}", name="broken")
    )
    before = aiosqlite_workers()

    try:
        with pytest.raises(DatabaseError, match="'broken' database") as excinfo:
            await db.connect()

        assert "unable to open database file" in str(excinfo.value)
        assert await db.health() is False
        await db.close()
    finally:
        # (Also when an assertion above fails: a worker left behind would fail the next test.)
        await workers_ended(before)  # (each failed open left one: see workers_ended)


async def test_an_open_connections_worker_is_found_by_name(make_db: MakeDB) -> None:
    """(workers_ended finds aiosqlite's workers by their thread's name: if a later aiosqlite names
    them otherwise, this fails, rather than the wait quietly finding nothing.)"""
    before = aiosqlite_workers()

    async with make_db("udl.db"):
        assert aiosqlite_workers() - before


async def test_database_errors_are_raised_as_database_error(
    tmp_path: Path, make_db: MakeDB
) -> None:
    async with make_db("udl.db") as db:
        with closing(sqlite3.connect(tmp_path / "udl.db")) as raw:
            raw.execute("DROP TABLE site")

        with pytest.raises(DatabaseError, match=r"'test' database failed.*no such table: site"):
            await db.get_site("example.com")


def test_an_invalid_url_raises_config_error_naming_the_connection() -> None:
    with pytest.raises(ConfigError, match=re.escape("'test'")):
        SQLAlchemyDatabase(connection("not a url"))


async def test_a_database_error_does_not_show_the_statement_or_its_values(
    make_db: MakeDB, tmp_path: Path
) -> None:
    # Its message reaches API clients; the values could be anything stored (e.g. options).
    async with make_db("udl.db"):
        pass
    with closing(sqlite3.connect(tmp_path / "udl.db")) as raw, raw:
        raw.execute("ALTER TABLE site DROP COLUMN notes")

    async with make_db("udl.db") as db:
        with pytest.raises(DatabaseError, match=r"'test' database failed: .*notes") as caught:
            await db.upsert_site(Site(key="hunter2.example"))

    assert "hunter2" not in caught.value.message
    assert "SELECT" not in caught.value.message


async def test_a_records_table_that_fails_is_a_database_error(
    tmp_path: Path, make_db: MakeDB
) -> None:
    async with make_db("udl.db") as db:
        with closing(sqlite3.connect(tmp_path / "udl.db")) as raw:
            raw.execute("DROP TABLE record_lease")

        with pytest.raises(DatabaseError, match="no such table"):
            await db.list_records(Lease, RecordQuery())


async def test_a_records_table_that_cannot_be_made_is_a_database_error(
    make_db: MakeDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(conn: Connection) -> None:
        raise SQLAlchemyError("disk full")

    async with make_db("udl.db") as db:

        class Unmade(Record):  # (a kind the database did not know when it connected)
            kind = "test_unmade"

            @classmethod
            def example(cls) -> Unmade:
                return cls()

        monkeypatch.setattr(db, "_lock_schema", full)

        with pytest.raises(DatabaseError, match="disk full"):
            await db.list_records(Unmade, RecordQuery())
