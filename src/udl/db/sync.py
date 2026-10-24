"""
-----------
- sync.py -
-----------

sync(): copy what one database knows into another (`udl db sync`).

What is copied: every site, its strategies, the downloads that have ended (one still queued or
running is left until it ends, so a copy taken while it ran never replaces the record of how it
ended), and the records of each kind db sync copies (udl.db.records: settings and automations,
and the history that has ended, never a runtime's own, such as leases). Records keep their ids,
so they are the same records in every database.

A record the target lacks is added. One it has (a site with the same key; a strategy, download or
record with the same id) is replaced by the source's copy where they differ: the source is taken
to be right. A site's own id and times are its database's, so they are not compared, and the
target keeps its own; nor is when a record was last updated. A record whose unique key (e.g. a
name) the target has under another id replaces that one: that is the only time a sync deletes
anything, so syncing again changes nothing.
"""

# System Imports
from collections import Counter
from typing import Final, Literal
from uuid import UUID

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt

# Local Imports
from udl.db.models import DownloadQuery, SiteQuery
from udl.db.records import Record, RecordQuery, record_kinds
from udl.errors import DuplicateKey
from udl.plugins.databases.base import DatabasePlugin

type Change = Literal["added", "updated", "unchanged"]

# Downloads and records are read this many at a time.
PAGE: Final = 500
# A site's fields that belong to the database that holds it.
_SITE_OWN: Final = {"id", "created_at", "updated_at"}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class SyncCounts(_Frozen):
    """How many records of one kind a sync added, replaced and found the same."""

    added: NonNegativeInt = 0
    """The target did not have them."""
    updated: NonNegativeInt = 0
    """The target had them, differently: they were replaced by the source's copy."""
    unchanged: NonNegativeInt = 0
    """The target had them already."""


class SyncResult(_Frozen):
    """What a sync into one database changed (or, in a dry run, would change)."""

    target: str
    """The connection synced into."""
    sites: SyncCounts
    strategies: SyncCounts
    downloads: SyncCounts
    """Only downloads that have ended are copied."""
    records: dict[str, SyncCounts] = Field(default_factory=dict[str, SyncCounts])
    """By kind of record, the kinds the source has some to copy of."""


def _change[M: BaseModel](stored: M | None, copy: M, *, ignore: set[str] | None = None) -> Change:
    if stored is None:
        return "added"
    if stored.model_dump(exclude=ignore) == copy.model_dump(exclude=ignore):
        return "unchanged"
    return "updated"


def _counts(changes: Counter[Change]) -> SyncCounts:
    return SyncCounts.model_validate(changes)


async def sync(
    source: DatabasePlugin, target: DatabasePlugin, *, dry_run: bool = False, page: int = PAGE
) -> SyncResult:
    """Copy what `source` knows into `target` (see the module docstring), or with `dry_run`
    only count what would change. Raises DatabaseError when either database fails; what was
    copied by then stays copied."""
    sites, strategies, downloads = Counter[Change](), Counter[Change](), Counter[Change]()

    for site in await source.list_sites(SiteQuery()):
        sites[change := _change(await target.get_site(site.key), site, ignore=_SITE_OWN)] += 1
        if change != "unchanged" and not dry_run:
            await target.upsert_site(site)
        theirs = {strategy.id: strategy for strategy in await target.strategies_for(site.key)}
        for strategy in await source.strategies_for(site.key):
            strategies[change := _change(theirs.get(strategy.id), strategy)] += 1
            if change != "unchanged" and not dry_run:
                await target.upsert_strategy(strategy)

    offset, seen = 0, set[UUID]()
    # Newest first, so downloads that start meanwhile push the rest back: a page may repeat
    # ones already read, but never skips one.
    while records := await source.list_downloads(DownloadQuery(limit=page, offset=offset)):
        offset += len(records)
        for record in records:
            if not record.status.ended or record.id in seen:
                continue
            seen.add(record.id)
            downloads[change := _change(await target.get_download(record.id), record)] += 1
            if dry_run:
                continue
            if change == "added":
                await target.add_download(record)
            elif change == "updated":
                await target.update_download(record)

    by_kind: dict[str, SyncCounts] = {}
    for kind in record_kinds():
        if kind.synced and (counted := await _sync_records(source, target, kind, dry_run, page)):
            by_kind[kind.kind] = _counts(counted)

    return SyncResult(
        target=target.connection.name,
        sites=_counts(sites),
        strategies=_counts(strategies),
        downloads=_counts(downloads),
        records=by_kind,
    )


async def _sync_records(
    source: DatabasePlugin, target: DatabasePlugin, kind: type[Record], dry_run: bool, page: int
) -> Counter[Change]:
    """Copy the source's records of `kind` (those that have ended) into the target, a page at a
    time, newest first (as downloads are)."""
    changes, offset, seen = Counter[Change](), 0, set[UUID]()
    while found := await source.list_records(kind, RecordQuery(limit=page, offset=offset)):
        offset += len(found)
        for record in found:
            if record.id in seen or not record.syncable():
                continue
            seen.add(record.id)
            stored = await target.get_record(kind, record.id)
            changes[change := _change(stored, record, ignore={"updated_at"})] += 1
            if change != "unchanged" and not dry_run:
                await _copy(target, record)
    return changes


async def _copy(target: DatabasePlugin, record: Record) -> None:
    """Store `record` in the target, in place of any record there with one of its unique keys
    (under another id)."""
    try:
        await target.upsert_record(record)
    except DuplicateKey:
        kind = type(record)
        for together in kind.unique:
            key = {name: getattr(record, name) for name in together}
            if None in key.values():
                continue
            for clash in await target.list_records(kind, RecordQuery(where=key)):
                if clash.id != record.id:
                    await target.delete_record(kind, clash.id)
        await target.upsert_record(record)
