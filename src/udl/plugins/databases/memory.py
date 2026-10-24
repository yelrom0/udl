"""
-------------
- memory.py -
-------------

A database that lives in memory: for tests, and for runs that should remember nothing
"""

# System Imports
from collections.abc import Iterable
from datetime import datetime, timedelta
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
    counted,
    download_order,
    strategy_order,
    utcnow,
)
from udl.db.records import Lease, Record, RecordQuery, newest_first
from udl.errors import DatabaseError, DuplicateKey

from .base import DatabasePlugin


def _copy[M: SQLModel](model_type: type[M], model: SQLModel) -> M:
    """A validated deep copy, so callers and the store never share a model."""
    return model_type.model_validate(model.model_dump())


class MemoryDatabase(DatabasePlugin):
    """Everything is kept in memory and lost on exit. Each instance is a separate database."""

    name = "memory"

    def __init__(self, connection: DBConnectionConfig) -> None:
        super().__init__(connection)
        self._connected = False
        self._sites: dict[str, Site] = {}
        self._strategies: dict[UUID, Strategy] = {}
        self._downloads: dict[UUID, DownloadRecord] = {}
        self._records: dict[str, dict[UUID, Record]] = {}  # by kind

    async def connect(self) -> None:
        self._connected = True
        await self.migrate()

    async def close(self) -> None:
        self._connected = False

    async def migrate(self) -> None:
        """Nothing to do: the models are the schema."""

    async def health(self) -> bool:
        return self._connected

    async def get_site(self, key: str) -> Site | None:
        site = self._sites.get(key)
        return _copy(Site, site) if site else None

    async def list_sites(self, query: SiteQuery) -> list[Site]:
        sites = sorted(self._sites.values(), key=lambda site: site.key)
        if query.search:
            term = query.search.lower()
            sites = [
                site
                for site in sites
                if term in site.key.lower() or (site.name and term in site.name.lower())
            ]
        return [_copy(Site, site) for site in query.page(sites)]

    async def upsert_site(self, site: Site) -> Site:
        if stored := self._sites.get(site.key):
            site = site.model_copy(
                update={"id": stored.id, "created_at": stored.created_at, "updated_at": utcnow()}
            )
        self._sites[site.key] = _copy(Site, site)
        return _copy(Site, site)

    async def delete_site(self, key: str) -> bool:
        if self._sites.pop(key, None) is None:
            return False
        self._strategies = {
            id_: strategy for id_, strategy in self._strategies.items() if strategy.site_key != key
        }
        return True

    async def strategies_for(self, site_key: str) -> list[Strategy]:
        strategies = [s for s in self._strategies.values() if s.site_key == site_key]
        return [_copy(Strategy, s) for s in sorted(strategies, key=strategy_order)]

    async def upsert_strategy(self, strategy: Strategy) -> Strategy:
        if strategy.site_key not in self._sites:
            raise DatabaseError(
                f"Cannot store a strategy for {strategy.site_key!r}: the site is not stored "
                f"in the {self.connection.name!r} database"
            )
        self._strategies[strategy.id] = _copy(Strategy, strategy)
        return _copy(Strategy, strategy)

    async def record_outcome(self, strategy_id: UUID, outcome: Outcome) -> Strategy | None:
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            return None
        self._strategies[strategy_id] = _copy(Strategy, counted(strategy, outcome))
        return _copy(Strategy, self._strategies[strategy_id])

    async def add_download(self, record: DownloadRecord) -> None:
        if record.id in self._downloads:
            raise DatabaseError(f"Download {record.id} is already stored")
        self._downloads[record.id] = _copy(DownloadRecord, record)

    async def get_download(self, download_id: UUID) -> DownloadRecord | None:
        record = self._downloads.get(download_id)
        return _copy(DownloadRecord, record) if record else None

    async def update_download(self, record: DownloadRecord) -> None:
        if record.id not in self._downloads:
            raise DatabaseError(f"Download {record.id} is not stored")
        self._downloads[record.id] = _copy(DownloadRecord, record)

    async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]:
        records = [
            record
            for record in self._downloads.values()
            if query.site_key in (None, record.site_key) and query.status in (None, record.status)
        ]
        records.sort(key=download_order, reverse=True)
        return [_copy(DownloadRecord, r) for r in query.page(records)]

    # -----------------------------------------------------------------------------------------
    # Records
    # -----------------------------------------------------------------------------------------

    def _of(self, kind: type[Record]) -> dict[UUID, Record]:
        return self._records.setdefault(kind.kind, {})

    async def get_record[R: Record](self, kind: type[R], record_id: UUID) -> R | None:
        record = self._of(kind).get(record_id)
        return _copy(kind, record) if record is not None else None

    async def list_records[R: Record](self, kind: type[R], query: RecordQuery) -> list[R]:
        where = kind.where(query.where)
        found = [
            record
            for record in self._of(kind).values()
            if all(getattr(record, name) == value for name, value in where.items())
            and (query.since is None or record.created_at >= query.since)
            and (query.until is None or record.created_at < query.until)
        ]
        found.sort(key=newest_first, reverse=True)
        return [_copy(kind, record) for record in query.page(found)]

    async def upsert_record[R: Record](self, record: R) -> R:
        kind = type(record)
        records = self._of(kind)
        if (stored := records.get(record.id)) is not None:
            record = record.model_copy(
                update={"created_at": stored.created_at, "updated_at": utcnow()}
            )
        _check_unique(record, records.values())
        records[record.id] = _copy(kind, record)
        return _copy(kind, record)

    async def delete_record(self, kind: type[Record], record_id: UUID) -> bool:
        return self._of(kind).pop(record_id, None) is not None

    async def prune_records(self, kind: type[Record], before: datetime) -> int:
        records = self._of(kind)
        old = [id_ for id_, record in records.items() if record.created_at < before]
        for id_ in old:
            del records[id_]
        return len(old)

    async def claim_lease(self, name: str, holder: str, ttl: timedelta) -> Lease | None:
        now = utcnow()
        leases = self._of(Lease)
        held = next(
            (lease for lease in leases.values() if isinstance(lease, Lease) and lease.name == name),
            None,
        )
        if held is not None and held.holder != holder and held.expires_at > now:
            return None
        lease = (
            held or Lease(name=name, holder=holder, expires_at=now, created_at=now)
        ).model_copy(update={"holder": holder, "expires_at": now + ttl, "updated_at": now})
        leases[lease.id] = _copy(Lease, lease)
        return _copy(Lease, lease)


def _check_unique(record: Record, others: Iterable[Record]) -> None:
    """Raise DuplicateKey when another of `others` has the same values in one of the record's
    kind's unique keys."""
    for together in record.unique:
        values = [getattr(record, name) for name in together]
        if None in values:
            continue
        for other in others:
            if other.id != record.id and [getattr(other, name) for name in together] == values:
                raise DuplicateKey(
                    f"A {record.kind} record with that {', '.join(together)} is already stored"
                )
