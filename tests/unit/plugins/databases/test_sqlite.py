"""
------------------
- test_sqlite.py -
------------------

Regression tests for SQLitePlugin: where the default database is created
"""

# System Imports
from contextlib import closing
from pathlib import Path

# Package Imports
import platformdirs
import pytest
from sqlmodel import SQLModel

# Local Imports
from udl.plugins.databases.sqlite import SQLitePlugin


def test_default_database_lives_in_the_user_data_dir_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    with closing(SQLitePlugin()) as db:
        db.connect()

    assert (platformdirs.user_data_path("udl") / "udl.db").is_file()
    assert list(cwd.iterdir()) == []


def test_connect_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "udl.db"

    with closing(SQLitePlugin(f"sqlite:///{path}")) as db:
        db.connect()

    assert path.is_file()


def test_in_memory_database_needs_no_directory() -> None:
    with closing(SQLitePlugin("sqlite://")) as db:
        db.connect()

        assert {table.name for table in db.list_tables()} == set(SQLModel.metadata.tables)
