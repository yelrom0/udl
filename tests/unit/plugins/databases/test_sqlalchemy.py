"""
----------------------
- test_sqlalchemy.py -
----------------------

Regression tests for SQLAlchemyDatabase (they replace the old test.py script)
"""

# System Imports
from pathlib import Path

# Local Imports
from udl.plugins.databases.sqlalchemy import SQLAlchemyDatabase


def test_instances_with_different_urls_coexist(tmp_path: Path) -> None:
    first = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'first.db'}")
    second = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'second.db'}")

    assert first is not second
    assert first.engine.url.database == str(tmp_path / "first.db")
    assert second.engine.url.database == str(tmp_path / "second.db")
