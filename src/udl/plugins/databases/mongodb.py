"""
--------------
- mongodb.py -
--------------

MongoDB, through pymongo's async client (udl's `mongodb` extra): a database plugin that is not
SQL, which keeps the DatabasePlugin interface honest.

Each model is stored as a document of its JSON form, with its id (as text) for `_id`, in a
collection of its own: sites, strategies and downloads, and a collection per kind of record
(udl.db.records), named after it, with an index on each indexed field and a unique one for each
of its unique keys (counting only documents with a value in every field of the key, so records
without one never clash). Times are written in UTC to the microsecond at a fixed width, so they
sort as text and lose nothing (BSON dates keep milliseconds).

Schema versioning: the schema_version collection holds the version of udl's documents in the
database, and a newer one is refused. A change to the documents needs SCHEMA_VERSION raised and
a step that brings older documents up to date.

pymongo is imported when it is used, so udl starts (and `udl plugins list` says what is missing)
without it.
"""

# System Imports
import enum
import re
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Final
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

# Package Imports
from sqlmodel import SQLModel

# Local Imports
from udl.config import DBConnectionConfig
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    Outcome,
    Site,
    SiteQuery,
    Strategy,
    utcnow,
)
from udl.db.records import Lease, Record, RecordQuery, record_kinds
from udl.errors import ConfigError, DatabaseError, DuplicateKey
from udl.plugins.requirements import Requirement

from .base import DatabasePlugin

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection
    from pymongo.asynchronous.database import AsyncDatabase
    from pymongo.asynchronous.mongo_client import AsyncMongoClient

type Document = dict[str, Any]

# The version of udl's documents this code writes: 2 added records (no document changed).
SCHEMA_VERSION: Final = 2

_SCHEMES: Final = ("mongodb", "mongodb+srv")
_DEFAULT_DATABASE: Final = "udl"  # when the URL names none
_ASCENDING: Final = 1
_DESCENDING: Final = -1
_AFTER: Final = True  # (pymongo.ReturnDocument.AFTER: return the document as updated)
# The types of the values an indexed field is stored as (text, a number, yes or no): a unique
# key counts only documents with one of these in each of its fields.
_VALUED: Final = {"$type": ["string", "number", "bool"]}


class _Duplicate(Exception):
    """A document with the same unique value (an id, a site's key) is already stored: pymongo's
    DuplicateKeyError, for the methods that expect one (pymongo is imported when used)."""


def _time(at: datetime) -> str:
    """`at` as text that sorts in time order: in UTC, to the microsecond, at a fixed width.
    (Pydantic's JSON form leaves out zero microseconds, so it does not sort.)"""
    return at.astimezone(UTC).isoformat(timespec="microseconds")


def _document(model: SQLModel) -> Document:
    """`model` as a document: its JSON form, with its times as _time() writes them and its id as
    `_id`."""
    document = model.model_dump(mode="json")
    for name, value in model:
        if isinstance(value, datetime):
            document[name] = _time(value)
    document["_id"] = document.pop("id")
    return document


def _stored(value: Any) -> Any:
    """A value as documents hold it (to look them up by)."""
    if isinstance(value, datetime):
        return _time(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _collection(kind: type[Record]) -> str:
    """The collection of a kind of record."""
    return f"record_{kind.kind}"


def _model[M: SQLModel](model_type: type[M], document: Document) -> M:
    """The model a document holds."""
    fields = {name: value for name, value in document.items() if name != "_id"}
    return model_type.model_validate({**fields, "id": document["_id"]})


class MongoDBPlugin(DatabasePlugin):
    """A MongoDB database, e.g. `mongodb://nas/udl`: the database the URL names, else `udl`."""

    name = "mongodb"
    requires = (
        Requirement.module(
            "pymongo",
            hint="Install udl's mongodb extra: `uv sync --extra mongodb` in udl's project, "
            "or `uv tool install 'udl[mongodb]'`.",
        ),
    )
    # How long to look for the server before failing, in seconds, unless the URL says
    # (serverSelectionTimeoutMS): pymongo's own default is 30.
    connect_timeout: ClassVar[float] = 10

    def __init__(self, connection: DBConnectionConfig) -> None:
        super().__init__(connection)
        parts = urlsplit(connection.url)
        if parts.scheme not in _SCHEMES:
            raise ConfigError(
                f"The database connection {connection.name!r} is not a MongoDB URL",
                hint="MongoDB URLs start with mongodb:// or mongodb+srv://.",
            )
        self._options: dict[str, Any] = {}
        if "serverselectiontimeoutms" not in {key.lower() for key, _ in parse_qsl(parts.query)}:
            self._options["serverSelectionTimeoutMS"] = round(self.connect_timeout * 1000)
        self._client: AsyncMongoClient[Document] | None = None
        self._record_kinds: set[str] = set()  # the kinds whose indexes are known to be made

    @property
    def _database(self) -> AsyncDatabase[Document]:
        if self._client is None:
            raise DatabaseError(f"The {self.connection.name!r} database is not connected")
        return self._client.get_default_database(_DEFAULT_DATABASE)

    @property
    def _sites(self) -> AsyncCollection[Document]:
        return self._database["sites"]

    @property
    def _strategies(self) -> AsyncCollection[Document]:
        return self._database["strategies"]

    @property
    def _downloads(self) -> AsyncCollection[Document]:
        return self._database["downloads"]

    @contextmanager
    def _errors(self, *, duplicates: bool = False) -> Generator[None]:
        """Raise pymongo's errors as DatabaseError, or a duplicate key as _Duplicate when the
        block expects `duplicates`."""
        from pymongo.errors import DuplicateKeyError, PyMongoError

        try:
            yield
        except PyMongoError as e:
            if duplicates and isinstance(e, DuplicateKeyError):
                raise _Duplicate(str(e)) from e
            raise self._error(e) from e

    def _error(self, e: Exception) -> DatabaseError:
        return DatabaseError(f"The {self.connection.name!r} database failed: {e}")

    async def connect(self) -> None:
        if self._client is None:
            from pymongo import AsyncMongoClient

            try:
                with self._errors():
                    self._client = AsyncMongoClient(self.connection.url, **self._options)
            except ValueError as e:  # e.g. a port that is not a number
                raise self._error(e) from e
        await self.migrate()

    async def close(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.close()

    async def migrate(self) -> None:
        with self._errors():
            # Stamp a new database; every udl that connects to it at once stamps the same.
            versions = self._database["schema_version"]
            await versions.update_one(
                {"_id": 1}, {"$setOnInsert": {"version": SCHEMA_VERSION}}, upsert=True
            )
            stamped = await versions.find_one({"_id": 1})
            stored: int = stamped["version"] if stamped else SCHEMA_VERSION
            if stored > SCHEMA_VERSION:
                raise DatabaseError(
                    f"The {self.connection.name!r} database has schema version {stored}, but "
                    f"this udl only knows versions up to {SCHEMA_VERSION}",
                    hint="Upgrade udl to use this database.",
                )
            await self._sites.create_index("key", unique=True)
            await self._strategies.create_index("site_key")
            await self._downloads.create_index("site_key")
            await self._downloads.create_index([("started_at", _DESCENDING), ("_id", _DESCENDING)])
        for kind in record_kinds():
            await self._index_records(kind)

    async def _index_records(self, kind: type[Record]) -> None:
        """Make the indexes of `kind`'s collection (when they are not made yet)."""
        collection = self._database[_collection(kind)]
        alone = {together[0] for together in kind.unique if len(together) == 1}
        with self._errors():
            await collection.create_index([("created_at", _DESCENDING), ("_id", _DESCENDING)])
            for name in kind.indexed:
                if name not in alone:  # (a unique index of its own is its index)
                    await collection.create_index(name)
            for together in kind.unique:
                await collection.create_index(
                    [(name, _ASCENDING) for name in together],
                    name=f"uq_{'_'.join(together)}",
                    unique=True,
                    partialFilterExpression=dict.fromkeys(together, _VALUED),
                )
        self._record_kinds.add(kind.kind)

    async def _records_of(self, kind: type[Record]) -> AsyncCollection[Document]:
        """The collection of `kind`, indexed first if it is not yet (a kind udl did not know
        when it connected)."""
        if kind.kind not in self._record_kinds:
            await self._index_records(kind)
        return self._database[_collection(kind)]

    async def health(self) -> bool:
        if self._client is None:
            return False
        try:
            with self._errors():
                await self._database.command("ping")
        except DatabaseError:
            return False
        return True

    # -----------------------------------------------------------------------------------------
    # Sites
    # -----------------------------------------------------------------------------------------

    async def get_site(self, key: str) -> Site | None:
        with self._errors():
            document = await self._sites.find_one({"key": key})
        return _model(Site, document) if document else None

    async def list_sites(self, query: SiteQuery) -> list[Site]:
        found: Document = {}
        if query.search:
            text = {"$regex": re.escape(query.search), "$options": "i"}
            found = {"$or": [{"key": text}, {"name": text}]}
        cursor = self._sites.find(found).sort("key", _ASCENDING).skip(query.offset)
        if query.limit is not None:
            cursor = cursor.limit(query.limit)
        with self._errors():
            return [_model(Site, document) async for document in cursor]

    async def upsert_site(self, site: Site) -> Site:
        changes = _document(site)
        for kept in ("_id", "created_at"):
            del changes[kept]
        changes["updated_at"] = _time(utcnow())

        async def update() -> Document | None:
            with self._errors():
                return await self._sites.find_one_and_update(
                    {"key": site.key}, {"$set": changes}, return_document=_AFTER
                )

        if (stored := await update()) is not None:
            return _model(Site, stored)
        document = _document(site)
        try:
            with self._errors(duplicates=True):
                await self._sites.insert_one(document)
        except _Duplicate as e:
            # Another writer stored the site since it was looked for: update theirs. (Unless
            # the duplicate is the id, which another site has.)
            if (stored := await update()) is None:
                raise self._error(e) from e
            return _model(Site, stored)
        return _model(Site, document)

    async def delete_site(self, key: str) -> bool:
        with self._errors():
            await self._strategies.delete_many({"site_key": key})
            deleted = await self._sites.delete_one({"key": key})
        return deleted.deleted_count > 0

    # -----------------------------------------------------------------------------------------
    # Strategies
    # -----------------------------------------------------------------------------------------

    async def strategies_for(self, site_key: str) -> list[Strategy]:
        cursor = self._strategies.find({"site_key": site_key}).sort(
            [("priority", _ASCENDING), ("success_count", _DESCENDING), ("_id", _ASCENDING)]
        )
        with self._errors():
            return [_model(Strategy, document) async for document in cursor]

    async def upsert_strategy(self, strategy: Strategy) -> Strategy:
        document = _document(strategy)
        with self._errors():
            if await self._sites.find_one({"key": strategy.site_key}, {"_id": 1}) is None:
                raise DatabaseError(
                    f"Cannot store a strategy for {strategy.site_key!r}: the site is not stored "
                    f"in the {self.connection.name!r} database"
                )
            await self._strategies.replace_one({"_id": document["_id"]}, document, upsert=True)
        return _model(Strategy, document)

    async def record_outcome(self, strategy_id: UUID, outcome: Outcome) -> Strategy | None:
        # One update that does its own arithmetic, so concurrent outcomes are all counted.
        if outcome.ok:
            update: Document = {
                "$inc": {"success_count": 1},
                "$set": {"consecutive_failures": 0, "last_success_at": _time(outcome.at)},
            }
        else:
            error = outcome.error.model_dump(mode="json") if outcome.error else None
            update = {
                "$inc": {"failure_count": 1, "consecutive_failures": 1},
                "$set": {"last_error": error},
            }
        with self._errors():
            document = await self._strategies.find_one_and_update(
                {"_id": str(strategy_id)}, update, return_document=_AFTER
            )
        return _model(Strategy, document) if document else None

    # -----------------------------------------------------------------------------------------
    # Download history
    # -----------------------------------------------------------------------------------------

    async def add_download(self, record: DownloadRecord) -> None:
        try:
            with self._errors(duplicates=True):
                await self._downloads.insert_one(_document(record))
        except _Duplicate as e:
            raise DatabaseError(f"Download {record.id} is already stored") from e

    async def get_download(self, download_id: UUID) -> DownloadRecord | None:
        with self._errors():
            document = await self._downloads.find_one({"_id": str(download_id)})
        return _model(DownloadRecord, document) if document else None

    async def update_download(self, record: DownloadRecord) -> None:
        document = _document(record)
        with self._errors():
            replaced = await self._downloads.replace_one({"_id": document["_id"]}, document)
        if replaced.matched_count == 0:
            raise DatabaseError(f"Download {record.id} is not stored")

    async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]:
        found: Document = {}
        if query.site_key is not None:
            found["site_key"] = query.site_key
        if query.status is not None:
            found["status"] = query.status.value
        cursor = (
            self._downloads.find(found)
            .sort([("started_at", _DESCENDING), ("_id", _DESCENDING)])
            .skip(query.offset)
        )
        if query.limit is not None:
            cursor = cursor.limit(query.limit)
        with self._errors():
            return [_model(DownloadRecord, document) async for document in cursor]

    # -----------------------------------------------------------------------------------------
    # Records
    # -----------------------------------------------------------------------------------------

    async def get_record[R: Record](self, kind: type[R], record_id: UUID) -> R | None:
        collection = await self._records_of(kind)
        with self._errors():
            document = await collection.find_one({"_id": str(record_id)})
        return _model(kind, document) if document else None

    async def list_records[R: Record](self, kind: type[R], query: RecordQuery) -> list[R]:
        found: Document = {name: _stored(value) for name, value in kind.where(query.where).items()}
        made: Document = {}
        if query.since is not None:
            made["$gte"] = _time(query.since)
        if query.until is not None:
            made["$lt"] = _time(query.until)
        if made:
            found["created_at"] = made
        collection = await self._records_of(kind)
        cursor = (
            collection.find(found)
            .sort([("created_at", _DESCENDING), ("_id", _DESCENDING)])
            .skip(query.offset)
        )
        if query.limit is not None:
            cursor = cursor.limit(query.limit)
        with self._errors():
            return [_model(kind, document) async for document in cursor]

    async def upsert_record[R: Record](self, record: R) -> R:
        kind = type(record)
        collection = await self._records_of(kind)
        document = _document(record)
        changes = {
            name: value for name, value in document.items() if name not in ("_id", "created_at")
        }
        duplicate = DuplicateKey(
            f"A {kind.kind} record with one of its unique keys "
            f"({'; '.join(', '.join(together) for together in kind.unique)}) the same is "
            "already stored"
        )
        try:
            with self._errors(duplicates=True):
                stored = await collection.find_one_and_update(
                    {"_id": document["_id"]}, {"$set": changes}, return_document=_AFTER
                )
                if stored is None:
                    await collection.insert_one(document)
                    stored = document
        except _Duplicate as e:
            raise duplicate from e
        return _model(kind, stored)

    async def delete_record(self, kind: type[Record], record_id: UUID) -> bool:
        collection = await self._records_of(kind)
        with self._errors():
            deleted = await collection.delete_one({"_id": str(record_id)})
        return deleted.deleted_count > 0

    async def prune_records(self, kind: type[Record], before: datetime) -> int:
        collection = await self._records_of(kind)
        with self._errors():
            deleted = await collection.delete_many({"created_at": {"$lt": _time(before)}})
        return deleted.deleted_count

    async def claim_lease(
        self, name: str, holder: str, ttl: timedelta, *, now: datetime
    ) -> Lease | None:
        document = _document(
            Lease(name=name, holder=holder, expires_at=now + ttl, created_at=now, updated_at=now)
        )
        collection = await self._records_of(Lease)
        # One update, which takes the lease when it is free (or its holder's) and stores it
        # when there is none yet; when another holds it, the lease it would store clashes with
        # theirs.
        free = {"name": name, "$or": [{"holder": holder}, {"expires_at": {"$lte": _time(now)}}]}
        taken = {
            "$set": {key: document[key] for key in ("holder", "expires_at", "updated_at")},
            "$setOnInsert": {key: document[key] for key in ("_id", "created_at")},
        }
        try:
            with self._errors(duplicates=True):
                found = await collection.find_one_and_update(
                    free, taken, upsert=True, return_document=_AFTER
                )
        except _Duplicate:
            return None  # (another holds it)
        return _model(Lease, found) if found else None

    async def release_lease(self, name: str, holder: str) -> bool:
        collection = await self._records_of(Lease)
        with self._errors():
            released = await collection.delete_one({"name": name, "holder": holder})
        return released.deleted_count > 0
