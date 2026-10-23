"""
---------------
- postgres.py -
---------------

PostgreSQL, through psycopg (udl's `postgres` extra): a database several machines can share
"""

# System Imports
from typing import ClassVar

# Package Imports
from sqlalchemy import Connection, func, select

# Local Imports
from udl.config import DBConnectionConfig
from udl.errors import ConfigError
from udl.plugins.requirements import Requirement

from .sqlalchemy import SQLAlchemyDatabase

_DRIVER = "postgresql+psycopg"
# The backend names of PostgreSQL URLs ("postgres://" is libpq's spelling, which SQLAlchemy no
# longer takes).
_BACKENDS = ("postgresql", "postgres")
# The advisory lock udls hold while they migrate a database: any number will do, as long as
# every udl uses the same one ("udl.sch" in ASCII).
_SCHEMA_LOCK = 0x75_64_6C_2E_73_63_68


class PostgresPlugin(SQLAlchemyDatabase):
    """A PostgreSQL database, e.g. `postgresql://udl@nas/udl`.

    Whichever driver the URL names, udl uses psycopg's async one. udl's tables go in the
    database's default schema; to keep them apart, give udl's user a schema of its own (or add
    `?options=-csearch_path%3Dudl` to the URL).
    """

    name = "postgres"
    requires = (
        Requirement.module(
            "psycopg",
            hint="Install udl's postgres extra: `uv sync --extra postgres` in udl's project, "
            "or `uv tool install 'udl[postgres]'`.",
        ),
    )

    # How long to wait for the server, in seconds, unless the URL says (connect_timeout):
    # libpq waits for as long as the system does, which can be minutes.
    connect_timeout: ClassVar[float] = 10

    def __init__(self, connection: DBConnectionConfig) -> None:
        super().__init__(connection)
        if self.url.get_backend_name() not in _BACKENDS:
            raise ConfigError(
                f"The database connection {connection.name!r} is not a PostgreSQL URL: "
                f"{self.url.render_as_string()}"
            )
        self.url = self.url.set(drivername=_DRIVER)
        if "connect_timeout" not in self.url.query:
            timeout = str(round(self.connect_timeout))
            self.url = self.url.update_query_dict({"connect_timeout": timeout})

    def _lock_schema(self, conn: Connection) -> None:
        conn.execute(select(func.pg_advisory_xact_lock(_SCHEMA_LOCK)))
