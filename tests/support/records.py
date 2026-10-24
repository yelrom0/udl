"""
--------------
- records.py -
--------------

Record kinds that exist only in the tests, with a field of every type a record kind can be
queried on, so the record contract suite can try them in every database plugin
"""

# System Imports
from enum import StrEnum
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Local Imports
from udl.db.models import JsonObject
from udl.db.records import Record, RecordScope, SyncRule


class NoteStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class Detail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    words: list[str] = Field(default_factory=list[str])
    weight: float = 0.0


class Note(Record):
    """A record of every kind of field."""

    kind = "test_note"
    indexed = ("topic", "slug", "status", "count", "flag", "owner", "due", "ref")
    unique = (("topic", "slug"), ("ref",))

    topic: str
    slug: str
    status: NoteStatus = NoteStatus.OPEN
    count: int = 0
    flag: bool = False
    owner: UUID | None = None
    due: AwareDatetime | None = None
    ref: str | None = None
    body: JsonObject = Field(default_factory=dict)
    detail: Detail | None = None

    @classmethod
    def example(cls) -> Note:
        return cls(topic="inbox", slug="example", body={"a": [1]}, detail=Detail(words=["x"]))


class Tally(Record):
    """A second kind, kept apart from the first; db sync copies one once it has ended (like a
    run)."""

    kind = "test_tally"
    indexed = ("name",)
    unique = (("name",),)
    sync = SyncRule.ENDED

    name: str
    total: int = 0
    open: bool = False

    @classmethod
    def example(cls) -> Tally:
        return cls(name="example", total=3)

    def ended(self) -> bool:
        return not self.open


class Scratch(Record):
    """A runtime's own kind (like a watcher's state): on the primary only, and never synced."""

    kind = "test_scratch"
    scope = RecordScope.RUNTIME
    sync = SyncRule.NEVER

    note: str = ""

    @classmethod
    def example(cls) -> Scratch:
        return cls(note="example")
