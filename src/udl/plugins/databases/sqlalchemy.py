"""
-----------------
- sqlalchemy.py -
-----------------

SQLAlchemyDatabase: the base for SQL database plugins, usable with any async SQLAlchemy URL.

The domain models are mapped onto tables of udl's own (the *Table classes), in a registry of their
own so they never mix with other SQLModel tables. A SQL plugin is then mostly a name, a URL scheme
and a driver dependency.

Schema versioning: the schema_version table holds the version of udl's tables in the database.
migrate() (run by connect()) creates a new database at the current version, and brings one made
by an older udl up to date by running the `migrations` it lacks, all in one transaction. So a
change to the tables needs a migration appended to SQLAlchemyDatabase.migrations.
"""

# System Imports
import logging
import re
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4

# Package Imports
from pydantic import AwareDatetime, TypeAdapter
from sqlalchemy import (
    JSON,
    BigInteger,
    Connection,
    DateTime,
    Dialect,
    TypeDecorator,
    func,
    insert,
    or_,
)
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.orm import declared_attr, registry
from sqlmodel import Field, SQLModel, col, delete, select, update
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.sqltypes import AutoString

# Local Imports
from udl.config import DBConnectionConfig
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    JobStatus,
    JsonObject,
    Outcome,
    Site,
    SiteQuery,
    Strategy,
    StrategySource,
    utcnow,
)
from udl.errors import ConfigError, DatabaseError, ErrorInfo

from .base import DatabasePlugin

log = logging.getLogger(__name__)

type Migration = Callable[[Connection], None]
"""A step that upgrades the schema by one version, e.g. `conn.exec_driver_sql("ALTER TABLE …")`."""

# ---------------------------------------------------------------------------------------------
# Column types: store the domain models' Python types faithfully
# ---------------------------------------------------------------------------------------------


class UTCDateTime(TypeDecorator[datetime]):
    """An aware datetime, stored in UTC (SQLite keeps no timezone, so it is put back on load)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class PathType(TypeDecorator[Path]):
    """A filesystem path, stored as text."""

    impl = AutoString
    cache_ok = True

    def process_bind_param(self, value: Path | None, dialect: Dialect) -> str | None:
        return str(value) if value is not None else None

    def process_result_value(self, value: str | None, dialect: Dialect) -> Path | None:
        return Path(value) if value is not None else None


class PydanticJSON(TypeDecorator[Any]):
    """A JSON column, (de)serialised through a Pydantic type, so it loads as that type."""

    impl = JSON
    cache_ok = True

    def __init__(self, pydantic_type: Any) -> None:
        super().__init__(none_as_null=True)
        self.pydantic_type = pydantic_type  # part of the statement cache key
        self._adapter = TypeAdapter[Any](pydantic_type)

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else self._adapter.dump_python(value, mode="json")

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else self._adapter.validate_python(value)


# ---------------------------------------------------------------------------------------------
# Tables: the domain models plus primary keys, constraints and column types
# ---------------------------------------------------------------------------------------------

_registry = registry()


class _Table(SQLModel, registry=_registry):
    # SiteTable -> site, DownloadRecordTable -> download_record. (SQLModel types __tablename__
    # as a plain declared_attr, which no override can match.)
    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805  # pyright: ignore[reportIncompatibleVariableOverride]
        return re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__.removesuffix("Table")).lower()


class SiteTable(Site, _Table, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    key: str = Field(min_length=1, unique=True)
    default_output_dir: Path | None = Field(default=None, sa_type=PathType)
    created_at: AwareDatetime = Field(default_factory=utcnow, sa_type=UTCDateTime)
    updated_at: AwareDatetime = Field(default_factory=utcnow, sa_type=UTCDateTime)


class StrategyTable(Strategy, _Table, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    site_key: str = Field(min_length=1, foreign_key="site.key", index=True)
    options: JsonObject = Field(default_factory=dict, sa_type=PydanticJSON(JsonObject))
    source: StrategySource = Field(sa_type=AutoString)
    last_success_at: AwareDatetime | None = Field(default=None, sa_type=UTCDateTime)
    last_error: ErrorInfo | None = Field(default=None, sa_type=PydanticJSON(ErrorInfo))


class DownloadRecordTable(DownloadRecord, _Table, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    site_key: str = Field(min_length=1, index=True)
    status: JobStatus = Field(default=JobStatus.QUEUED, sa_type=AutoString)
    output_dir: Path | None = Field(default=None, sa_type=PathType)
    files: list[Path] = Field(default_factory=list, sa_type=PydanticJSON(list[Path]))
    size_bytes: int = Field(default=0, ge=0, sa_type=BigInteger)
    started_at: AwareDatetime = Field(default_factory=utcnow, sa_type=UTCDateTime, index=True)
    finished_at: AwareDatetime | None = Field(default=None, sa_type=UTCDateTime)
    error: ErrorInfo | None = Field(default=None, sa_type=PydanticJSON(ErrorInfo))


class SchemaVersionTable(_Table, table=True):
    """The version of udl's tables in this database (a single row)."""

    id: int = Field(default=1, primary_key=True)
    version: int


def _site(row: SiteTable) -> Site:
    return Site.model_validate(row, from_attributes=True)


def _strategy(row: StrategyTable) -> Strategy:
    return Strategy.model_validate(row, from_attributes=True)


def _download(row: DownloadRecordTable) -> DownloadRecord:
    return DownloadRecord.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------------------------


class SQLAlchemyDatabase(DatabasePlugin):
    """A SQL database reached through an async SQLAlchemy URL, e.g. `sqlite+aiosqlite:///udl.db`.

    Each instance owns its own engine (created by connect()), so several can coexist.
    """

    # migrations[n] upgrades the schema from version n + 1 to n + 2.
    migrations: ClassVar[Sequence[Migration]] = ()

    def __init__(self, connection: DBConnectionConfig) -> None:
        super().__init__(connection)
        try:
            self.url: URL = make_url(connection.url)
        except ArgumentError as e:
            raise ConfigError(
                f"The database connection {connection.name!r} has an invalid URL: {e}"
            ) from e
        self._engine: AsyncEngine | None = None

    @property
    def schema_version(self) -> int:
        """The schema version this code creates and uses."""
        return 1 + len(self.migrations)

    def _create_engine(self) -> AsyncEngine:
        """Subclasses can tune the engine (pooling, dialect options) here."""
        return create_async_engine(self.url)

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise DatabaseError(f"The {self.connection.name!r} database is not connected")
        return self._engine

    async def connect(self) -> None:
        if self._engine is None:
            self._engine = self._create_engine()
        await self.migrate()

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def migrate(self) -> None:
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(self._migrate)
        except SQLAlchemyError as e:
            raise self._error(e) from e

    def _migrate(self, conn: Connection) -> None:
        name = self.connection.name
        versions = _registry.metadata.tables[SchemaVersionTable.__tablename__]
        versions.create(conn, checkfirst=True)
        stored: int | None = conn.execute(select(versions.c.version)).scalar_one_or_none()
        if stored is None:
            _registry.metadata.create_all(conn)
            conn.execute(insert(versions).values(id=1, version=self.schema_version))
            return
        if stored > self.schema_version:
            raise DatabaseError(
                f"The {name!r} database has schema version {stored}, but this udl only knows "
                f"versions up to {self.schema_version}",
                hint="Upgrade udl to use this database.",
            )
        for version in range(stored + 1, self.schema_version + 1):
            log.info("Migrating the %r database to schema version %d", name, version)
            try:
                self.migrations[version - 2](conn)
            except Exception as e:
                raise DatabaseError(
                    f"Could not migrate the {name!r} database to schema version {version}: {e}"
                ) from e
            conn.execute(update(versions).values(version=version))

    async def health(self) -> bool:
        if self._engine is None:
            return False
        try:
            async with self._engine.connect() as conn:
                await conn.scalar(select(1))
        except SQLAlchemyError:
            return False
        return True

    def _error(self, e: SQLAlchemyError) -> DatabaseError:
        return DatabaseError(f"The {self.connection.name!r} database failed: {e}")

    @asynccontextmanager
    async def _transaction(self) -> AsyncGenerator[AsyncSession]:
        """A session that commits when the block succeeds and rolls back when it raises."""
        try:
            async with AsyncSession(self.engine, expire_on_commit=False) as s, s.begin():
                yield s
        except SQLAlchemyError as e:
            raise self._error(e) from e

    async def get_site(self, key: str) -> Site | None:
        async with self._transaction() as s:
            row = (await s.exec(select(SiteTable).where(SiteTable.key == key))).first()
            return _site(row) if row else None

    async def list_sites(self, query: SiteQuery) -> list[Site]:
        statement = select(SiteTable).order_by(col(SiteTable.key))
        if query.search:
            term = query.search.lower()
            statement = statement.where(
                or_(
                    func.lower(col(SiteTable.key)).contains(term, autoescape=True),
                    func.lower(col(SiteTable.name)).contains(term, autoescape=True),
                )
            )
        statement = statement.offset(query.offset).limit(query.limit)
        async with self._transaction() as s:
            return [_site(row) for row in await s.exec(statement)]

    async def upsert_site(self, site: Site) -> Site:
        async with self._transaction() as s:
            row = (await s.exec(select(SiteTable).where(SiteTable.key == site.key))).first()
            if row is None:
                row = SiteTable.model_validate(site.model_dump())
                s.add(row)
            else:
                changes = site.model_dump(exclude={"id", "created_at", "updated_at"})
                row.sqlmodel_update(changes, update={"updated_at": utcnow()})
            return _site(row)

    async def delete_site(self, key: str) -> bool:
        async with self._transaction() as s:
            await s.exec(delete(StrategyTable).where(col(StrategyTable.site_key) == key))
            result = await s.exec(delete(SiteTable).where(col(SiteTable.key) == key))
            return result.rowcount > 0

    async def strategies_for(self, site_key: str) -> list[Strategy]:
        statement = (
            select(StrategyTable)
            .where(StrategyTable.site_key == site_key)
            .order_by(
                col(StrategyTable.priority),
                col(StrategyTable.success_count).desc(),
                col(StrategyTable.id),
            )
        )
        async with self._transaction() as s:
            return [_strategy(row) for row in await s.exec(statement)]

    async def upsert_strategy(self, strategy: Strategy) -> Strategy:
        async with self._transaction() as s:
            site = select(SiteTable.id).where(SiteTable.key == strategy.site_key)
            if (await s.exec(site)).first() is None:
                raise DatabaseError(
                    f"Cannot store a strategy for {strategy.site_key!r}: the site is not stored "
                    f"in the {self.connection.name!r} database"
                )
            row = await s.merge(StrategyTable.model_validate(strategy.model_dump()))
            return _strategy(row)

    async def record_outcome(self, strategy_id: UUID, outcome: Outcome) -> Strategy | None:
        # One UPDATE that does its own arithmetic, so concurrent outcomes are all counted.
        if outcome.ok:
            values: dict[str, Any] = {
                "success_count": StrategyTable.success_count + 1,
                "consecutive_failures": 0,
                "last_success_at": outcome.at,
            }
        else:
            values = {
                "failure_count": StrategyTable.failure_count + 1,
                "consecutive_failures": StrategyTable.consecutive_failures + 1,
                "last_error": outcome.error,
            }
        statement = update(StrategyTable).where(col(StrategyTable.id) == strategy_id)
        async with self._transaction() as s:
            result = await s.exec(statement.values(values))
            if result.rowcount == 0:
                return None
            row = await s.get_one(StrategyTable, strategy_id)
            return _strategy(row)

    async def add_download(self, record: DownloadRecord) -> None:
        async with self._transaction() as s:
            if await s.get(DownloadRecordTable, record.id) is not None:
                raise DatabaseError(f"Download {record.id} is already stored")
            s.add(DownloadRecordTable.model_validate(record.model_dump()))

    async def get_download(self, download_id: UUID) -> DownloadRecord | None:
        async with self._transaction() as s:
            row = await s.get(DownloadRecordTable, download_id)
            return _download(row) if row else None

    async def update_download(self, record: DownloadRecord) -> None:
        async with self._transaction() as s:
            if await s.get(DownloadRecordTable, record.id) is None:
                raise DatabaseError(f"Download {record.id} is not stored")
            await s.merge(DownloadRecordTable.model_validate(record.model_dump()))

    async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]:
        statement = select(DownloadRecordTable).order_by(
            col(DownloadRecordTable.started_at).desc(), col(DownloadRecordTable.id).desc()
        )
        if query.site_key is not None:
            statement = statement.where(DownloadRecordTable.site_key == query.site_key)
        if query.status is not None:
            statement = statement.where(DownloadRecordTable.status == query.status)
        statement = statement.offset(query.offset).limit(query.limit)
        async with self._transaction() as s:
            return [_download(row) for row in await s.exec(statement)]
