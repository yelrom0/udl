"""
------------------
- test_records.py -
------------------

Record kinds: what a kind may be (so every database can store it), and how records are looked
up (RecordQuery)
"""

# System Imports
import re
from datetime import UTC, datetime
from enum import IntEnum
from uuid import UUID

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from support.records import Note, NoteStatus, Scratch, Tally
from udl.db.records import (
    RECORD_KINDS,
    Lease,
    PluginSettings,
    Record,
    RecordQuery,
    RecordScope,
    SyncRule,
    record_kind,
    record_kinds,
)


def test_every_kind_defined_is_known_by_name() -> None:
    kinds = record_kinds()

    assert {Lease, Note, Tally, Scratch} <= set(kinds)
    assert [kind.kind for kind in kinds] == sorted(kind.kind for kind in kinds)


def test_a_record_s_own_fields_leave_out_its_id_and_times() -> None:
    assert Lease.own_fields() == ["name", "holder", "expires_at"]


def test_a_base_for_kinds_is_not_a_kind() -> None:
    class Base(Record):
        shared: str = ""

    class Kind(Base):
        kind = "test_based"

        @classmethod
        def example(cls) -> Kind:
            return cls()

    assert Kind in record_kinds()
    assert Base not in record_kinds()


@pytest.mark.parametrize(
    ("kind", "problem"),
    [
        ("Shouty", "cannot name a kind"),
        ("two words", "cannot name a kind"),
    ],
)
def test_a_kind_is_named_as_a_table_can_be(kind: str, problem: str) -> None:
    with pytest.raises(TypeError, match=problem):
        type("Bad", (Record,), {"kind": kind, "__module__": __name__})


@pytest.mark.parametrize(
    ("fields", "problem"),
    [
        ({"indexed": ("nope",)}, "has no field 'nope'"),
        ({"indexed": ("id",)}, "has no field 'id'"),
        ({"indexed": ("tags",)}, "tags cannot be indexed"),
        ({"indexed": ("level",)}, "level cannot be indexed"),  # (an enum of numbers)
        ({"indexed": ("data",)}, "a field called data"),
        ({"indexed": ("name",), "unique": (("name", "tags"),)}, "not \\(all\\) indexed"),
        ({"indexed": ("name",), "unique": ((),)}, "not \\(all\\) indexed"),
    ],
)
def test_a_kind_s_indexed_fields_are_ones_every_database_can_compare(
    fields: dict[str, object], problem: str
) -> None:
    class Level(IntEnum):
        LOW = 1

    annotations = {"name": str, "tags": list[str], "level": Level, "data": dict[str, str]}
    namespace: dict[str, object] = {
        "__module__": __name__,
        "__annotations__": annotations,
        "kind": "test_bad",
        "name": "",
        "tags": [],
        "level": Level.LOW,
        "data": {},
        **fields,
    }

    with pytest.raises(TypeError, match=problem):
        type("Bad", (Record,), namespace)


def test_another_class_cannot_be_a_kind_that_is_taken() -> None:
    with pytest.raises(TypeError, match=re.escape("Note is the test_note kind")):
        type("Other", (Record,), {"__module__": __name__, "kind": "test_note"})


def test_a_kind_gives_an_example() -> None:
    with pytest.raises(TypeError, match="example"):
        type("Exampleless", (Record,), {"__module__": __name__, "kind": "test_exampleless"})


def test_kinds_are_known_by_name() -> None:
    assert record_kind("lease") is Lease
    assert RECORD_KINDS["plugin_settings"] is PluginSettings
    with pytest.raises(KeyError, match="nope"):
        record_kind("nope")


def test_a_kind_s_scope_and_sync_rule_say_whose_it_is() -> None:
    assert (PluginSettings.scope, PluginSettings.sync) == (RecordScope.CONFIG, SyncRule.ALWAYS)
    assert (Lease.scope, Lease.sync) == (RecordScope.RUNTIME, SyncRule.NEVER)
    assert (Note.scope, Tally.sync, Scratch.sync) == (
        RecordScope.CONFIG,
        SyncRule.ENDED,
        SyncRule.NEVER,
    )


# ---------------------------------------------------------------------------------------------
# Looking records up
# ---------------------------------------------------------------------------------------------


def test_a_query_s_values_are_read_as_the_fields_types() -> None:
    where = Note.where(
        {
            "status": "done",
            "owner": "00000000-0000-0000-0000-000000000001",
            "due": "2026-01-02T03:04:05+02:00",
            "count": "7",
            "ref": None,
        }
    )

    assert where == {
        "status": NoteStatus.DONE,
        "owner": UUID(int=1),
        "due": datetime(2026, 1, 2, 1, 4, 5, tzinfo=UTC),
        "count": 7,
        "ref": None,
    }


def test_records_are_looked_up_by_indexed_fields_only() -> None:
    with pytest.raises(ValueError, match="test_note records are not looked up by 'body'"):
        Note.where({"body": {}})
    with pytest.raises(ValueError, match="only by their id"):
        Scratch.where({"note": "x"})


def test_a_value_a_field_cannot_have_is_refused() -> None:
    with pytest.raises(ValidationError):
        Note.where({"count": "many"})


def test_a_query_gives_its_page() -> None:
    items = list(range(10))

    assert RecordQuery().page(items) == items
    assert RecordQuery(limit=3, offset=2).page(items) == [2, 3, 4]
    assert RecordQuery(offset=8).page(items) == [8, 9]


def test_a_query_from_the_start_covers_its_page() -> None:
    query = RecordQuery(where={"topic": "x"}, limit=3, offset=2)

    assert query.from_start() == RecordQuery(where={"topic": "x"}, limit=5)
    assert RecordQuery(offset=4).from_start() == RecordQuery()


def test_a_record_has_ended_unless_its_kind_says_otherwise() -> None:
    assert Tally(name="t").ended()
    assert not Tally(name="t", open=True).ended()
    assert Note(topic="t", slug="s").ended()
