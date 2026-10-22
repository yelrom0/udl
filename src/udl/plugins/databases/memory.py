"""
-------------
- memory.py -
-------------

A database that lives in memory: for tests, and for runs that should remember nothing
"""

# System Imports
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
    download_order,
    strategy_order,
    utcnow,
)
from udl.errors import DatabaseError

from .base import DatabasePlugin


def _copy[M: SQLModel](model_type: type[M], model: M) -> M:
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
        if outcome.ok:
            update = {
                "success_count": strategy.success_count + 1,
                "consecutive_failures": 0,
                "last_success_at": outcome.at,
            }
        else:
            update = {
                "failure_count": strategy.failure_count + 1,
                "consecutive_failures": strategy.consecutive_failures + 1,
                "last_error": outcome.error,
            }
        self._strategies[strategy_id] = _copy(Strategy, strategy.model_copy(update=update))
        return _copy(Strategy, self._strategies[strategy_id])

    async def add_download(self, record: DownloadRecord) -> None:
        if record.id in self._downloads:
            raise DatabaseError(f"Download {record.id} is already stored")
        self._downloads[record.id] = _copy(DownloadRecord, record)

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
