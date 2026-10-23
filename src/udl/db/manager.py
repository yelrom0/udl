"""
--------------
- manager.py -
--------------

DBManager: the configured database connections ([db] in the config), used as one database
"""

# System Imports
import copy
import logging
from collections.abc import Awaitable, Callable, Collection, Hashable, Iterable
from itertools import chain
from types import TracebackType
from typing import NoReturn, Self
from uuid import UUID

# Local Imports
from udl.config import DBConnectionConfig, DBSettings, ReadPolicy, WritePolicy
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    Outcome,
    Site,
    SiteQuery,
    Strategy,
    download_order,
    strategy_order,
)
from udl.errors import DatabaseError
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.registry import DATABASES, PluginRegistry

log = logging.getLogger(__name__)


def _first_of_each[T](items: Iterable[T], key: Callable[[T], Hashable]) -> list[T]:
    """`items` without repeats (by `key`), keeping the first of each."""
    found: dict[Hashable, T] = {}
    for item in items:
        found.setdefault(key(item), item)
    return list(found.values())


class DBManager:
    """Several named database connections, used as one.

    Connections are consulted in priority order (lower first). The primary connection is the one
    marked `primary`, else the highest-priority writable one.

    Reads:
    - get_site: the highest-priority connection that has the site.
    - strategies_for, by read policy. `first`: the highest-priority connection that has any for
      the site. `merge`: every connection's, keeping the highest-priority copy of each
      (site, url_pattern, tool).
    - list_sites / list_downloads: every connection's, keeping the highest-priority copy of each.

    Writes go to the write policy's connections, never to a read-only one; the primary (or the
    highest-priority one, when the primary is not written) leads and its answer is returned.

    A connection that fails (to connect, or later) is skipped with a warning, unless it is the
    primary or, for a write, the leading connection: then the operation raises DatabaseError,
    and a failed write is not attempted anywhere else.
    """

    def __init__(self, settings: DBSettings, registry: PluginRegistry) -> None:
        self._read_policy = settings.read_policy
        self._connections = sorted(settings.connections, key=lambda c: c.priority)
        self._databases = {c.name: registry.get(DATABASES, c.plugin)(c) for c in self._connections}
        names = [c.name for c in self._connections]
        writable = [c.name for c in self._connections if not c.read_only]
        marked = [c.name for c in self._connections if c.primary]
        self._primary = (marked or writable or names)[0]

        if settings.write_policy == WritePolicy.ALL:
            writers = writable
        elif settings.write_policy == WritePolicy.PRIMARY:
            writers = [self._primary] if self._primary in writable else []
        else:
            writers = list(settings.write_policy)
        # The primary first, then in priority order.
        self._writers = sorted(writers, key=lambda name: (name != self._primary, names.index(name)))
        self._live: set[str] = set()

    @property
    def connections(self) -> list[DBConnectionConfig]:
        """The configured connections, in priority order."""
        return list(self._connections)

    @property
    def primary(self) -> str:
        """The primary connection's name."""
        return self._primary

    def is_connected(self, name: str) -> bool:
        """Whether the connection called `name` is connected (and has not failed to)."""
        return name in self._live

    def only(self, names: Collection[str]) -> Self:
        """A view that reads from, and writes to, only the connections called `names`: writes
        go to the writable ones among them, whatever the write policy. Its primary is this
        manager's if named, else the highest-priority writable one. It shares this manager's
        databases, so connect and close this manager, not the view. Raises DatabaseError when a
        name is not a connection's."""
        unknown = sorted(set(names) - set(self._databases))
        if unknown or not names:
            what = f"named {', '.join(unknown)}" if unknown else "was named"
            known = ", ".join(c.name for c in self._connections)
            raise DatabaseError(
                f"No database connection {what}", hint=f"The connections are: {known}."
            )
        view = copy.copy(self)
        view._connections = [c for c in self._connections if c.name in names]
        listed = [c.name for c in view._connections]
        writable = [c.name for c in view._connections if not c.read_only]
        view._primary = self._primary if self._primary in names else (writable or listed)[0]
        view._writers = sorted(
            writable, key=lambda name: (name != view._primary, listed.index(name))
        )
        return view

    def database(self, name: str) -> DatabasePlugin:
        """The database of the connection called `name`."""
        try:
            return self._databases[name]
        except KeyError:
            raise DatabaseError(f"No database connection named {name!r}") from None

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

    async def connect(self) -> None:
        for connection in self._connections:
            try:
                await self._databases[connection.name].connect()
            except Exception as e:  # noqa: BLE001 - only the primary's failure is fatal
                self._failed(connection.name, e, fatal=connection.name == self._primary)
            else:
                self._live.add(connection.name)

    async def close(self) -> None:
        self._live.clear()
        for database in self._databases.values():
            await database.close()

    def _failed(self, name: str, error: Exception, *, fatal: bool) -> None:
        if fatal:
            self._raise(name, error)
        log.warning("Skipping the %r database, which failed: %s", name, error)

    def _raise(self, name: str, error: Exception) -> NoReturn:
        if isinstance(error, DatabaseError):
            raise error
        raise DatabaseError(f"The {name!r} database failed: {error}") from error

    def _check_connected(self) -> None:
        if self._primary not in self._live:
            raise DatabaseError("The databases are not connected")

    # -----------------------------------------------------------------------------------------
    # Reads
    # -----------------------------------------------------------------------------------------

    async def _read[T](
        self,
        read: Callable[[DatabasePlugin], Awaitable[T]],
        *,
        until: Callable[[T], bool] = lambda _: False,
    ) -> list[T]:
        """Each live connection's answer, in priority order, up to the first that is `until`."""
        self._check_connected()
        answers: list[T] = []
        for connection in self._connections:
            if connection.name not in self._live:
                continue
            try:
                answer = await read(self._databases[connection.name])
            except Exception as e:  # noqa: BLE001 - only the primary's failure is fatal
                self._failed(connection.name, e, fatal=connection.name == self._primary)
                continue
            answers.append(answer)
            if until(answer):
                break
        return answers

    async def get_site(self, key: str) -> Site | None:
        answers = await self._read(lambda db: db.get_site(key), until=lambda s: s is not None)
        return next((site for site in answers if site is not None), None)

    async def list_sites(self, query: SiteQuery) -> list[Site]:
        answers = await self._read(lambda db: db.list_sites(_from_start(query)))
        sites = _first_of_each(chain.from_iterable(answers), key=lambda site: site.key)
        return query.page(sorted(sites, key=lambda site: site.key))

    async def strategies_for(self, site_key: str) -> list[Strategy]:
        def read(db: DatabasePlugin) -> Awaitable[list[Strategy]]:
            return db.strategies_for(site_key)

        if self._read_policy == ReadPolicy.FIRST:
            found = [strategies for strategies in await self._read(read, until=bool) if strategies]
            return found[0] if found else []
        answers = await self._read(read)
        strategies = _first_of_each(
            chain.from_iterable(answers), key=lambda s: (s.site_key, s.url_pattern, s.tool)
        )
        return sorted(strategies, key=strategy_order)

    async def get_download(self, download_id: UUID) -> DownloadRecord | None:
        answers = await self._read(
            lambda db: db.get_download(download_id), until=lambda r: r is not None
        )
        return next((record for record in answers if record is not None), None)

    async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]:
        answers = await self._read(lambda db: db.list_downloads(_from_start(query)))
        records = _first_of_each(chain.from_iterable(answers), key=lambda record: record.id)
        return query.page(sorted(records, key=download_order, reverse=True))

    # -----------------------------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------------------------

    async def _write[T](self, write: Callable[[DatabasePlugin], Awaitable[T]]) -> T:
        """Write to each live write connection, the leading one first. Returns its answer."""
        self._check_connected()
        names = [name for name in self._writers if name in self._live]
        if not names:
            raise DatabaseError(
                "No database connection to write to",
                hint="Every connection is read-only or unavailable. Check [db] in the config.",
            )
        lead, *others = names
        try:
            answer = await write(self._databases[lead])
        except Exception as e:  # noqa: BLE001 - re-raised as DatabaseError
            self._raise(lead, e)
        for name in others:
            try:
                await write(self._databases[name])
            except Exception as e:  # noqa: BLE001 - only the leading connection's failure is fatal
                self._failed(name, e, fatal=False)
        return answer

    async def upsert_site(self, site: Site) -> Site:
        return await self._write(lambda db: db.upsert_site(site))

    async def delete_site(self, key: str) -> bool:
        return await self._write(lambda db: db.delete_site(key))

    async def upsert_strategy(self, strategy: Strategy) -> Strategy:
        return await self._write(lambda db: db.upsert_strategy(strategy))

    async def record_outcome(self, strategy_id: UUID, outcome: Outcome) -> Strategy | None:
        return await self._write(lambda db: db.record_outcome(strategy_id, outcome))

    async def add_download(self, record: DownloadRecord) -> None:
        await self._write(lambda db: db.add_download(record))

    async def update_download(self, record: DownloadRecord) -> None:
        await self._write(lambda db: db.update_download(record))


def _from_start[Q: SiteQuery | DownloadQuery](query: Q) -> Q:
    """`query` from the first item up to the end of its page: from each connection, that holds
    everything the merged page can need."""
    end = None if query.limit is None else query.offset + query.limit
    return query.model_copy(update={"offset": 0, "limit": end})
