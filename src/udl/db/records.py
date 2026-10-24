"""
--------------
- records.py -
--------------

Records: the data automations keep (accounts, subscriptions, actions, events, runs, schedules,
settings...), each kind a Record model that every database plugin stores, the same way.

A kind names the fields it is looked up by (`indexed`: equality in a RecordQuery) and the ones
that are unique together (`unique`), and they are all a database needs to know about it: a SQL
database keeps a table per kind (`record_<kind>`: a column per indexed field, the rest as JSON),
MongoDB a collection, so a new kind needs no code in any database plugin. Each kind gives an
example(), which the record contract round-trips in every database plugin. Records have UUID
ids, so they are the same records in every database, and are listed newest first.

A kind's `scope` says whose its records are: `config` records (subscriptions, settings...) follow
the read and write policies, as sites do; `runtime` records (leases, events, runs, a watcher's
state) belong to the one runtime that owns them, on the primary connection only. Its `sync` rule
says what db sync copies: `always`, `ended` (history, once it has ended) or `never`.

Leases are records too: claim_lease() is how one udl, of several on one database, does work
that only one may do (running the automations).
"""

# System Imports
import enum
import importlib
import inspect
import re
import types
import typing
import uuid
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Final, Self, cast

# Package Imports
import pydantic
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    TypeAdapter,
)
from sqlmodel import Field, SQLModel

# Local Imports
from udl.db.models import JsonObject, utcnow

_KIND: Final = re.compile(r"[a-z][a-z0-9_]*")
# The types an indexed field can have (or None too): what every database can compare.
_INDEXABLE: Final = (str, int, bool, uuid.UUID, datetime)


class RecordScope(StrEnum):
    """Whose records of a kind are."""

    CONFIG = "config"  # the user's: read and written by the policies, like sites
    RUNTIME = "runtime"  # one runtime's: on the primary connection only


class SyncRule(StrEnum):
    """What db sync copies of a kind."""

    ALWAYS = "always"
    ENDED = "ended"  # history, once it has ended (Record.ended())
    NEVER = "never"  # one runtime's own (leases, a watcher's state)


RECORD_KINDS: Final[dict[str, type[Record]]] = {}
"""Every kind of record, by name: udl's, and any defined since (the tests')."""

# The modules that define udl's own kinds (each kind registers itself as it is defined), loaded
# as soon as the kinds are asked for, so a database knows them all.
_UDL_KINDS: Final = (
    "udl.automations.events",
    "udl.automations.subscriptions",
    "udl.plugins.actions.base",
    "udl.plugins.integrations.base",
    "udl.integrations.watchers",
    "udl.automations.runs",
    "udl.automations.schedules",
)

NAME_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
NAME_LENGTH: Final = 100

type Name = Annotated[str, StringConstraints(pattern=NAME_PATTERN, max_length=NAME_LENGTH)]
"""What names a record the user names (a subscription, an action...): letters, digits, `_`,
`.`, `:` and `-`, starting with a letter or digit."""


class Record(SQLModel):
    """A record of one kind. Subclasses set `kind` and their fields, and may set `indexed` and
    `unique`.

    Databases store a record by its id: storing one again replaces it (keeping when it was
    created). Its fields round-trip, JSON and models in them included.
    """

    kind: ClassVar[str]  # e.g. "subscription": its table's or collection's name
    # The fields a RecordQuery can look records up by (each a str, int, bool, UUID, datetime or
    # an enum of text, or None).
    indexed: ClassVar[tuple[str, ...]] = ()
    # Indexed fields whose values two records may not share (each tuple together); a record
    # with None in any of them clashes with none.
    unique: ClassVar[tuple[tuple[str, ...], ...]] = ()
    scope: ClassVar[RecordScope] = RecordScope.CONFIG
    sync: ClassVar[SyncRule] = SyncRule.ALWAYS

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    created_at: AwareDatetime = Field(default_factory=utcnow)
    updated_at: AwareDatetime = Field(default_factory=utcnow)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        if "kind" not in cls.__dict__:  # (a base for kinds, not one)
            return
        known = RECORD_KINDS.get(cls.kind)
        if known is not None and known.__qualname__ != cls.__qualname__:
            raise TypeError(f"{cls.__qualname__}: {known.__qualname__} is the {cls.kind} kind")
        _check(cls)
        RECORD_KINDS[cls.kind] = cls

    @classmethod
    def example(cls) -> Self:
        """A record of the kind (which the record contract stores in every database)."""
        raise NotImplementedError

    def ended(self) -> bool:
        """Whether it is history that has ended (a run that ran, an event that was
        dispatched): what db sync copies of a kind whose sync rule is `ended`. Yes, unless the
        kind says otherwise."""
        return True

    @classmethod
    def own_fields(cls) -> list[str]:
        """The fields of the kind itself: all but the id and the times."""
        return [name for name in cls.model_fields if name not in Record.model_fields]

    @classmethod
    def where(cls, values: Mapping[str, Any]) -> dict[str, Any]:
        """`values` (a RecordQuery's where) as the indexed fields' types. Raises ValueError for
        a field that is not indexed, or a value it cannot have."""
        found: dict[str, Any] = {}
        for name, value in values.items():
            if name not in cls.indexed:
                raise ValueError(
                    f"{cls.kind} records are not looked up by {name!r} "
                    f"(only by {', '.join(cls.indexed) or 'their id'})"
                )
            annotation = cls.model_fields[name].rebuild_annotation()
            found[name] = TypeAdapter[Any](annotation).validate_python(value)
        return found


def record_kinds() -> list[type[Record]]:
    """Every kind of record (udl's and those defined since), by name."""
    _load_udl_kinds()
    return [RECORD_KINDS[kind] for kind in sorted(RECORD_KINDS)]


def record_kind(name: str) -> type[Record]:
    """The kind of record called `name`. Raises KeyError."""
    _load_udl_kinds()
    try:
        return RECORD_KINDS[name]
    except KeyError:
        raise KeyError(f"No kind of record called {name!r}") from None


def _load_udl_kinds() -> None:
    for module in _UDL_KINDS:
        importlib.import_module(module)


def _plain(annotation: Any) -> Any:
    """`annotation` without its aliases (`type X = ...`) and Annotated's constraints."""
    while True:
        if isinstance(annotation, typing.TypeAliasType):
            annotation = annotation.__value__
        elif typing.get_origin(annotation) is Annotated:
            annotation = typing.get_args(annotation)[0]
        else:
            return annotation


def indexed_type(cls: type[Record], name: str) -> tuple[type, bool]:
    """The type an indexed field holds (an enum's is str), and whether it can be None."""
    annotation = _plain(cls.model_fields[name].annotation)
    nullable = False
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        args = [_plain(arg) for arg in typing.get_args(annotation) if arg is not type(None)]
        nullable = len(args) < len(typing.get_args(annotation))
        annotation = args[0] if len(args) == 1 else None
    if inspect.isclass(annotation) and issubclass(annotation, enum.Enum):
        annotation = str if issubclass(annotation, str) else None
    elif annotation is AwareDatetime:  # (Pydantic's, which is no datetime subclass)
        annotation = datetime
    if not (inspect.isclass(annotation) and issubclass(annotation, _INDEXABLE)):
        raise TypeError(
            f"{cls.__qualname__}.{name} cannot be indexed: it is a str, int, bool, UUID, "
            "datetime or an enum of text (or None)"
        )
    return cast(type, annotation), nullable


def _check(cls: type[Record]) -> None:
    """Raise TypeError when a kind is not one every database can store."""
    if not _KIND.fullmatch(cls.kind):
        raise TypeError(
            f"{cls.__qualname__}.kind: {cls.kind!r} cannot name a kind of record (lower case "
            "letters, digits and _)"
        )
    for name in cls.indexed:
        if name not in cls.own_fields():
            raise TypeError(f"{cls.__qualname__}.indexed: it has no field {name!r}")
        if name == "data":
            raise TypeError(
                f"{cls.__qualname__}.indexed: a field called data is not indexed (databases "
                "keep the rest of a record there)"
            )
        indexed_type(cls, name)
    for together in cls.unique:
        if not together or not set(together) <= set(cls.indexed):
            raise TypeError(f"{cls.__qualname__}.unique: {together!r} are not (all) indexed fields")
    if "example" not in cls.__dict__:
        raise TypeError(f"{cls.__qualname__} gives no example() (the record contract stores it)")


# ---------------------------------------------------------------------------------------------
# Looking records up
# ---------------------------------------------------------------------------------------------


class RecordQuery(BaseModel):
    """Which records of a kind to list: they come newest (created_at) first, then by id."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    where: dict[str, Any] = pydantic.Field(default_factory=dict[str, Any])
    """Only those whose indexed fields have these values (None: those without one)."""
    since: AwareDatetime | None = None
    """Only those created at this time or later."""
    until: AwareDatetime | None = None
    """Only those created before this time."""
    limit: PositiveInt | None = None
    """At most this many."""
    offset: NonNegativeInt = 0
    """Skip this many first."""

    def page[T](self, items: list[T]) -> list[T]:
        """The page of `items` (already filtered and in order) that this query asks for."""
        end = None if self.limit is None else self.offset + self.limit
        return items[self.offset : end]

    def from_start(self) -> RecordQuery:
        """This query from the first record to the end of its page (what each of several
        databases is asked for, to merge their answers)."""
        end = None if self.limit is None else self.offset + self.limit
        return self.model_copy(update={"offset": 0, "limit": end})


def newest_first(record: Record) -> tuple[datetime, uuid.UUID]:
    """The order records are listed in, reversed: by created_at, then by id."""
    return (record.created_at, record.id)


# ---------------------------------------------------------------------------------------------
# Leases
# ---------------------------------------------------------------------------------------------


class Lease(Record):
    """Work only one udl may do at a time (e.g. "automations"), and which udl does it until
    when. Taken with DatabasePlugin.claim_lease(), renewed by its holder before it expires,
    and taken over by another once it has."""

    kind = "lease"
    indexed = ("name", "holder", "expires_at")
    unique = (("name",),)
    scope = RecordScope.RUNTIME
    sync = SyncRule.NEVER

    name: str
    holder: str
    expires_at: AwareDatetime

    @classmethod
    def example(cls) -> Self:
        return cls(name="automations", holder="udl-1", expires_at=utcnow())


# ---------------------------------------------------------------------------------------------
# Plugin settings
# ---------------------------------------------------------------------------------------------


class PluginSettings(Record):
    """What a plugin's settings were set to through udl (set_plugin_settings), over its
    [integrations.<name>] table: the values, but for secrets, which the secret store keeps
    (`secrets` names those it has)."""

    kind = "plugin_settings"
    indexed = ("plugin",)
    unique = (("plugin",),)

    plugin: str
    values: JsonObject = Field(default_factory=dict)
    secrets: list[str] = Field(default_factory=list[str])

    @classmethod
    def example(cls) -> Self:
        return cls(plugin="gmail", values={"poll_interval": "60s"}, secrets=["client_secret"])
