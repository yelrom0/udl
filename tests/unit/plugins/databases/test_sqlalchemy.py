"""
----------------------
- test_sqlalchemy.py -
----------------------

Regression tests for SQLAlchemyDatabase (they replace the old test.py script)
"""

# System Imports
import uuid
from collections.abc import Iterator
from pathlib import Path

# Package Imports
import pytest
from sqlalchemy import inspect as sa_inspect
from sqlmodel import Field, SQLModel

# Local Imports
from udl.plugins.databases.sqlalchemy import SQLAlchemyDatabase


class SampleA(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    msg: str = "SampleA"


class SampleB(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    msg: str = "SampleB"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[SQLAlchemyDatabase]:
    database = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'test.db'}")
    database.connect()
    yield database
    database.close()


def _messages(db: SQLAlchemyDatabase, model: type[SampleA | SampleB]) -> list[str]:
    return sorted(row.msg for row in db.list_rows(model))


def test_instances_with_different_urls_coexist(tmp_path: Path) -> None:
    first = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'first.db'}")
    second = SQLAlchemyDatabase(f"sqlite:///{tmp_path / 'second.db'}")

    assert first is not second
    assert first.engine.url.database == str(tmp_path / "first.db")
    assert second.engine.url.database == str(tmp_path / "second.db")


def test_connect_creates_tables_for_registered_models(db: SQLAlchemyDatabase) -> None:
    tables = sa_inspect(db.engine).get_table_names()

    assert {"samplea", "sampleb"} <= set(tables)


def test_connect_is_idempotent(db: SQLAlchemyDatabase) -> None:
    db.add(SampleA(msg="kept"))

    db.connect()

    assert _messages(db, SampleA) == ["kept"]


def test_add_inserts_a_row(db: SQLAlchemyDatabase) -> None:
    db.add(SampleA(msg="hello"))

    assert _messages(db, SampleA) == ["hello"]


def test_add_inserts_every_model_in_a_list(db: SQLAlchemyDatabase) -> None:
    db.add([SampleA(msg="a1"), SampleB(msg="b1"), SampleA(msg="a2")])

    assert _messages(db, SampleA) == ["a1", "a2"]
    assert _messages(db, SampleB) == ["b1"]


def test_add_without_commit_is_not_persisted(db: SQLAlchemyDatabase) -> None:
    db.add(SampleA(), commit_session=False)

    assert db.list_rows(SampleA) == []


def test_add_into_a_callers_session_is_committed_by_the_caller(db: SQLAlchemyDatabase) -> None:
    with db.session() as s:
        db.add(SampleA(msg="batched"), s, commit_session=False)
        assert db.list_rows(SampleA) == []
        s.commit()

    assert _messages(db, SampleA) == ["batched"]


def test_added_models_stay_usable_after_add_returns(db: SQLAlchemyDatabase) -> None:
    model = SampleA(msg="still here")

    db.add(model)

    assert model.msg == "still here"
