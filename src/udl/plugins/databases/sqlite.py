"""
-------------
- sqlite.py -
-------------

Connecting to an embedded SQLite database
"""

# System Imports
from pathlib import Path

# Local Imports
from udl.config import default_db_path

from .sqlalchemy import SQLAlchemyDatabase


class SQLitePlugin(SQLAlchemyDatabase):
    """The default database: one SQLite file, by default udl.db in the user data dir."""

    def __init__(
        self,
        db_url: str | None = None,
        *,
        echo: bool = False,
    ) -> None:
        super().__init__(
            db_url or f"sqlite:///{default_db_path()}",
            echo=echo,
        )

    def connect(self) -> None:
        # SQLite creates the database file, but not the directory it lives in.
        database = self.engine.url.database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        super().connect()
