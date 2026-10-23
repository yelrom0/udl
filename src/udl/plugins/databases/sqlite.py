"""
-------------
- sqlite.py -
-------------

The default database: one SQLite file
"""

# System Imports
import asyncio
from pathlib import Path
from typing import Any

# Package Imports
from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Local Imports
from udl.config import DBConnectionConfig, expand_path
from udl.errors import ConfigError
from udl.plugins.requirements import Requirement

from .sqlalchemy import SQLAlchemyDatabase

_DRIVER = "sqlite+aiosqlite"


def _no_implicit_transactions(dbapi_connection: Any, _: Any) -> None:
    dbapi_connection.isolation_level = None


def _begin(conn: Connection) -> None:
    conn.exec_driver_sql("BEGIN")


class SQLitePlugin(SQLAlchemyDatabase):
    """One SQLite file; by default udl.db in the user data dir (see udl.config).

    `~` and `$VARS` in the file's path are expanded, and a sync driver (`sqlite://`) is swapped for
    the async one. `sqlite://` alone is an in-memory database.
    """

    name = "sqlite"
    requires = (Requirement.module("aiosqlite", hint="Install aiosqlite: `uv add aiosqlite`."),)

    def __init__(self, connection: DBConnectionConfig) -> None:
        super().__init__(connection)
        if self.url.get_backend_name() != "sqlite":
            raise ConfigError(
                f"The database connection {connection.name!r} is not a SQLite URL: "
                f"{self.url.render_as_string()}"
            )
        database = self.url.database
        self._file = None if database in (None, "", ":memory:") else expand_path(Path(database))
        self.url = self.url.set(
            drivername=_DRIVER, database=str(self._file) if self._file else database
        )

    def _create_engine(self) -> AsyncEngine:
        if self._file is None:
            engine = create_async_engine(self.url)
        else:
            # One connection, shared in turn: SQLite allows one writer at a time, and concurrent
            # transactions on separate connections can fail with "database is locked".
            engine = create_async_engine(self.url, pool_size=1, max_overflow=0)
        # The sqlite3 module only begins a transaction before INSERT/UPDATE/DELETE, so schema
        # changes would be committed as they run. Let SQLAlchemy begin every transaction instead,
        # so a migration that fails leaves nothing behind. (The recipe from SQLAlchemy's docs:
        # "Serializable isolation / Savepoints / Transactional DDL".)
        event.listen(engine.sync_engine, "connect", _no_implicit_transactions)
        event.listen(engine.sync_engine, "begin", _begin)
        return engine

    async def connect(self) -> None:
        # SQLite creates the database file, but not the directory it lives in.
        if self._file is not None:
            await asyncio.to_thread(self._file.parent.mkdir, parents=True, exist_ok=True)
        await super().connect()
