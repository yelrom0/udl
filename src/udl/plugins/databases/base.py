"""
-----------
- base.py -
-----------

DatabasePlugin: the interface every database plugin implements.

It works at the level of the domain models (a repository), not SQL, so non-SQL databases can be
plugins too. tests/contract/test_database_contract.py is the behaviour every plugin must have.
"""

# System Imports
from abc import ABC, abstractmethod
from types import TracebackType
from typing import ClassVar, Self
from uuid import UUID

# Local Imports
from udl.config import DBConnectionConfig
from udl.db.models import DownloadQuery, DownloadRecord, Outcome, Site, SiteQuery, Strategy
from udl.plugins.requirements import Requirement


class DatabasePlugin(ABC):
    """Where udl stores sites, their strategies and the download history.

    Methods return copies: changing a returned model never changes what is stored. Problems with
    the database itself are raised as DatabaseError.
    """

    name: ClassVar[str]  # what config uses to pick the plugin, e.g. "sqlite"
    # What it needs installed, e.g. its driver (`udl plugins list` says what is missing).
    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(self, connection: DBConnectionConfig) -> None:
        self.connection = connection

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @abstractmethod
    async def connect(self) -> None:
        """Open the database, then migrate() it."""

    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def migrate(self) -> None:
        """Bring the database's schema up to date. Safe to run repeatedly."""

    @abstractmethod
    async def health(self) -> bool:
        """Whether the database is connected and answering."""

    @abstractmethod
    async def get_site(self, key: str) -> Site | None: ...

    @abstractmethod
    async def list_sites(self, query: SiteQuery) -> list[Site]:
        """The sites matching `query`, by key."""

    @abstractmethod
    async def upsert_site(self, site: Site) -> Site:
        """Store a site, matched by key. Updating keeps the stored id and created_at, and sets
        updated_at to now. Returns the stored site."""

    @abstractmethod
    async def delete_site(self, key: str) -> bool:
        """Delete a site and its strategies (its download history is kept).
        Returns whether there was such a site."""

    @abstractmethod
    async def strategies_for(self, site_key: str) -> list[Strategy]:
        """A site's strategies (disabled ones too), in `strategy_order`."""

    @abstractmethod
    async def upsert_strategy(self, strategy: Strategy) -> Strategy:
        """Store a strategy, matched by id. Raises DatabaseError when its site is not stored."""

    @abstractmethod
    async def record_outcome(self, strategy_id: UUID, outcome: Outcome) -> Strategy | None:
        """Count a download attempt against a strategy, atomically.

        Success: success_count += 1, consecutive_failures = 0, last_success_at = outcome.at.
        Failure: failure_count += 1, consecutive_failures += 1, last_error = outcome.error.
        Returns the updated strategy, or None when there is no such strategy.
        """

    @abstractmethod
    async def add_download(self, record: DownloadRecord) -> None:
        """Store a new download. Raises DatabaseError when its id is already stored."""

    @abstractmethod
    async def get_download(self, download_id: UUID) -> DownloadRecord | None: ...

    @abstractmethod
    async def update_download(self, record: DownloadRecord) -> None:
        """Replace a stored download. Raises DatabaseError when its id is not stored."""

    @abstractmethod
    async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]:
        """The downloads matching `query`, newest (started_at) first."""
