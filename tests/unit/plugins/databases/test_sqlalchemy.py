"""
----------------------
- test_sqlalchemy.py -
----------------------

Regression tests for SQLAlchemyDatabase (they replace the old test.py script)
"""

# System Imports
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

# Package Imports
import pytest
from sqlalchemy import inspect as sa_inspect
from sqlmodel import Field, SQLModel

# Local Imports
from udl.plugins.databases.sqlalchemy import ColumnInfo, SQLAlchemyDatabase


class SampleA(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    msg: str = "SampleA"


class SampleB(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    msg: str = "SampleB"


type MakeDB = Callable[[str], SQLAlchemyDatabase]


@pytest.fixture
def make_db(tmp_path: Path) -> Iterator[MakeDB]:
    """Create (unconnected) databases in tmp_path by file name; all are closed afterwards."""
    created: list[SQLAlchemyDatabase] = []

    def make(name: str) -> SQLAlchemyDatabase:
        created.append(SQLAlchemyDatabase(f"sqlite:///{tmp_path / name}"))
        return created[-1]

    yield make
    for database in created:
        database.close()


@pytest.fixture
def db(make_db: MakeDB) -> SQLAlchemyDatabase:
    database = make_db("test.db")
    database.connect()
    return database


def _messages(db: SQLAlchemyDatabase, model: type[SampleA | SampleB]) -> list[str]:
    return sorted(row.msg for row in db.list_rows(model))


def test_instances_with_different_urls_coexist(tmp_path: Path, make_db: MakeDB) -> None:
    first = make_db("first.db")
    second = make_db("second.db")

    assert first is not second
    assert first.engine.url.database == str(tmp_path / "first.db")
    assert second.engine.url.database == str(tmp_path / "second.db")


def test_instances_with_different_urls_store_data_separately(make_db: MakeDB) -> None:
    first, second = make_db("first.db"), make_db("second.db")
    first.connect()
    second.connect()

    first.add(SampleA(msg="only in first"))

    assert _messages(first, SampleA) == ["only in first"]
    assert second.list_rows(SampleA) == []


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


def test_transaction_commits_when_the_block_succeeds(db: SQLAlchemyDatabase) -> None:
    with db.transaction() as s:
        s.add(SampleA(msg="committed"))

    assert _messages(db, SampleA) == ["committed"]


def test_transaction_rolls_back_and_reraises_when_the_block_fails(db: SQLAlchemyDatabase) -> None:
    def fail_after_writing() -> None:
        with db.transaction() as s:
            s.add(SampleA(msg="rolled back"))
            s.flush()
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        fail_after_writing()

    assert db.list_rows(SampleA) == []


def test_delete_commits_and_returns_the_number_of_rows_removed(db: SQLAlchemyDatabase) -> None:
    db.add([SampleA(msg="a1"), SampleA(msg="a2"), SampleB()])

    removed = db.delete(SampleA)

    assert removed == 2
    assert db.list_rows(SampleA) == []
    assert _messages(db, SampleB) == ["SampleB"]


def test_delete_with_filters_only_removes_matching_rows(db: SQLAlchemyDatabase) -> None:
    db.add([SampleA(msg="a1"), SampleA(msg="a2")])

    removed = db.delete(SampleA, msg="a1")

    assert removed == 1
    assert _messages(db, SampleA) == ["a2"]


def test_delete_in_a_callers_session_is_committed_by_the_caller(db: SQLAlchemyDatabase) -> None:
    db.add(SampleA(msg="a1"))

    with db.session() as s:
        db.delete(SampleA, s)
        assert _messages(db, SampleA) == ["a1"]
        s.commit()

    assert db.list_rows(SampleA) == []


def test_list_tables_returns_the_schema_instead_of_printing_it(
    db: SQLAlchemyDatabase, capsys: pytest.CaptureFixture[str]
) -> None:
    tables = {table.name: table for table in db.list_tables()}

    assert tables["samplea"].primary_key == ["id"]
    assert tables["samplea"].columns[1] == ColumnInfo(name="msg", type="VARCHAR", nullable=False)
    assert capsys.readouterr().out == ""


def test_list_tables_is_empty_before_connect(make_db: MakeDB) -> None:
    assert make_db("empty.db").list_tables() == []


def test_drop_removes_the_table_until_connect_recreates_it(db: SQLAlchemyDatabase) -> None:
    db.add(SampleB())

    db.drop(SampleB)
    after_drop = {table.name for table in db.list_tables()}
    db.connect()

    assert "sampleb" not in after_drop
    assert "samplea" in after_drop
    assert db.list_rows(SampleB) == []
