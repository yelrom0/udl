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
from udl.db.records import Record


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


class Tally(Record):
    """A second kind, kept apart from the first; db sync leaves it alone until it has ended."""

    kind = "test_tally"
    indexed = ("name",)
    unique = (("name",),)

    name: str
    total: int = 0
    open: bool = False

    def syncable(self) -> bool:
        return not self.open


class Scratch(Record):
    """A kind db sync never copies (like leases)."""

    kind = "test_scratch"
    synced = False

    note: str = ""
