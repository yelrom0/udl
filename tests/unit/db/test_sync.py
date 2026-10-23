"""
----------------
- test_sync.py -
----------------

sync(): copying what one database knows (its sites, their strategies and its downloads) into
another, which is what `udl db sync` does for each connection it syncs into
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import connection
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    JobStatus,
    Site,
    SiteQuery,
    Strategy,
    StrategySource,
)
from udl.db.sync import SyncCounts, SyncResult, sync
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.databases.memory import MemoryDatabase

T0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


@pytest.fixture
async def source() -> AsyncIterator[DatabasePlugin]:
    async with MemoryDatabase(connection("laptop")) as db:
        yield db


@pytest.fixture
async def target() -> AsyncIterator[DatabasePlugin]:
    async with MemoryDatabase(connection("nas")) as db:
        yield db


def strategy(site_key: str = "example.com", **fields: object) -> Strategy:
    return Strategy.model_validate(
        {"site_key": site_key, "tool": "yt-dlp", "source": StrategySource.PROBE, **fields}
    )


def download(site_key: str = "example.com", **fields: object) -> DownloadRecord:
    fields.setdefault("status", JobStatus.FINISHED)
    return DownloadRecord.model_validate(
        {"url": f"https://{site_key}/v/1", "site_key": site_key, **fields}
    )


async def everything(db: DatabasePlugin) -> tuple[list[Site], list[Strategy], list[DownloadRecord]]:
    sites = await db.list_sites(SiteQuery())
    strategies = [s for site in sites for s in await db.strategies_for(site.key)]
    return sites, strategies, await db.list_downloads(DownloadQuery())


async def learned(db: DatabasePlugin) -> None:
    """Two sites with a strategy each, and two finished downloads."""
    for key in ("example.com", "example.org"):
        await db.upsert_site(Site(key=key, name=key.upper(), default_output_dir=Path("/media")))
        await db.upsert_strategy(strategy(key, success_count=3, options={"format": "best"}))
        await db.add_download(download(key, files=[Path("/media/a.mp4")], size_bytes=5))


async def test_an_empty_database_gets_everything(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    await learned(source)

    synced = await sync(source, target)

    assert synced == SyncResult(
        target="nas",
        sites=SyncCounts(added=2),
        strategies=SyncCounts(added=2),
        downloads=SyncCounts(added=2),
    )
    assert await everything(target) == await everything(source)


async def test_syncing_again_changes_nothing(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    await learned(source)
    await sync(source, target)
    before = await everything(target)

    synced = await sync(source, target)

    assert (synced.sites, synced.strategies, synced.downloads) == (
        SyncCounts(unchanged=2),
        SyncCounts(unchanged=2),
        SyncCounts(unchanged=2),
    )
    assert await everything(target) == before


async def test_what_the_target_has_differently_is_replaced_by_the_source_s_copy(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    await learned(source)
    await sync(source, target)
    [site, _] = await source.list_sites(SiteQuery())
    [learned_strategy] = await source.strategies_for(site.key)
    [record, _] = await source.list_downloads(DownloadQuery())
    # Changed in the target since the last sync.
    await target.upsert_site(site.model_copy(update={"name": "Renamed"}))
    await target.upsert_strategy(learned_strategy.model_copy(update={"enabled": False}))
    await target.update_download(record.model_copy(update={"size_bytes": 0}))

    synced = await sync(source, target)

    assert (synced.sites, synced.strategies, synced.downloads) == (
        SyncCounts(updated=1, unchanged=1),
        SyncCounts(updated=1, unchanged=1),
        SyncCounts(updated=1, unchanged=1),
    )
    stored = await target.get_site(site.key)
    assert stored is not None
    assert stored.name == site.name
    assert await target.strategies_for(site.key) == [learned_strategy]
    assert await target.get_download(record.id) == record


async def test_a_site_is_matched_by_key_and_the_target_keeps_its_own_id(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    ours = await source.upsert_site(Site(key="example.com", name="Example"))
    theirs = await target.upsert_site(Site(key="example.com", created_at=T0))

    synced = await sync(source, target)

    assert synced.sites == SyncCounts(updated=1)
    stored = await target.get_site("example.com")
    assert stored is not None
    assert (stored.id, stored.created_at, stored.name) == (theirs.id, T0, "Example")
    assert stored.id != ours.id


async def test_a_site_that_differs_only_in_its_id_and_times_is_unchanged(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    await source.upsert_site(Site(key="example.com", name="Example"))
    await target.upsert_site(Site(key="example.com", name="Example", created_at=T0, updated_at=T0))

    synced = await sync(source, target)

    assert synced.sites == SyncCounts(unchanged=1)
    stored = await target.get_site("example.com")
    assert stored is not None
    assert stored.updated_at == T0


async def test_nothing_the_target_has_is_deleted(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    await learned(target)
    before = await everything(target)
    await source.upsert_site(Site(key="example.net"))

    await sync(source, target)

    sites, strategies, downloads = await everything(target)
    assert [site.key for site in sites] == ["example.com", "example.net", "example.org"]
    assert (strategies, downloads) == before[1:]


@pytest.mark.parametrize("status", [JobStatus.QUEUED, JobStatus.RUNNING])
async def test_downloads_that_have_not_ended_are_left_until_they_end(
    source: DatabasePlugin, target: DatabasePlugin, status: JobStatus
) -> None:
    unfinished = download(status=status)
    await source.add_download(unfinished)
    # A copy taken while it ran never replaces the record of how it ended, and vice versa.
    await target.add_download(unfinished.model_copy(update={"status": JobStatus.FINISHED}))

    synced = await sync(source, target)

    assert synced.downloads == SyncCounts()
    stored = await target.get_download(unfinished.id)
    assert stored is not None
    assert stored.status is JobStatus.FINISHED


@pytest.mark.parametrize("status", [JobStatus.FINISHED, JobStatus.FAILED, JobStatus.CANCELLED])
async def test_downloads_that_have_ended_are_copied(
    source: DatabasePlugin, target: DatabasePlugin, status: JobStatus
) -> None:
    await source.add_download(download(status=status))

    synced = await sync(source, target)

    assert synced.downloads == SyncCounts(added=1)


async def test_every_download_is_copied_a_page_at_a_time(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    records = [download(started_at=T0 + timedelta(seconds=n)) for n in range(7)]
    for record in records:
        await source.add_download(record)

    synced = await sync(source, target, page=3)

    assert synced.downloads == SyncCounts(added=7)
    assert await target.list_downloads(DownloadQuery()) == records[::-1]


class Growing(MemoryDatabase):
    """A database in which a download starts (and ends) as each of the first pages of them is
    read, as if another udl were using it."""

    starts = 2

    async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]:
        listed = await super().list_downloads(query)
        if self.starts:
            self.starts -= 1
            await self.add_download(download(started_at=T0 + timedelta(days=1 + query.offset)))
        return listed


async def test_downloads_that_start_during_a_sync_do_not_make_it_count_one_twice(
    target: DatabasePlugin,
) -> None:
    async with Growing(connection("laptop")) as source:
        for n in range(6):
            await source.add_download(download(started_at=T0 + timedelta(seconds=n)))

        synced = await sync(source, target, page=2)

    assert synced.downloads.added == len(await target.list_downloads(DownloadQuery()))
    assert synced.downloads.unchanged == 0


async def test_a_dry_run_counts_what_would_change_and_changes_nothing(
    source: DatabasePlugin, target: DatabasePlugin
) -> None:
    await learned(source)
    await sync(source, target)
    await source.upsert_site(Site(key="example.net"))
    await source.upsert_strategy(strategy("example.net"))
    [record, _] = await source.list_downloads(DownloadQuery())
    await source.update_download(record.model_copy(update={"size_bytes": 99}))
    before = await everything(target)

    synced = await sync(source, target, dry_run=True)

    assert (synced.sites, synced.strategies, synced.downloads) == (
        SyncCounts(added=1, unchanged=2),
        SyncCounts(added=1, unchanged=2),
        SyncCounts(updated=1, unchanged=1),
    )
    assert await everything(target) == before
