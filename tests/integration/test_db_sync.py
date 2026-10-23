"""
-------------------
- test_db_sync.py -
-------------------

Syncing between different kinds of database (SQLite, PostgreSQL, MongoDB): records keep their
ids, so they arrive as they left, and syncing them back changes nothing
"""

# System Imports
from datetime import UTC, datetime
from pathlib import Path

# Local Imports
from udl.chains.models import TagChain
from udl.config import DBConnectionConfig
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    JobStatus,
    Site,
    SiteQuery,
    Strategy,
    StrategySource,
)
from udl.db.sync import SyncCounts, sync
from udl.errors import ErrorCode, ErrorInfo
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.databases.mongodb import MongoDBPlugin
from udl.plugins.databases.postgres import PostgresPlugin
from udl.plugins.databases.sqlite import SQLitePlugin

T0 = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC)


async def everything(db: DatabasePlugin) -> tuple[list[Site], list[Strategy], list[DownloadRecord]]:
    sites = await db.list_sites(SiteQuery())
    strategies = [s for site in sites for s in await db.strategies_for(site.key)]
    return sites, strategies, await db.list_downloads(DownloadQuery())


async def test_records_arrive_as_they_left_whatever_the_database(
    tmp_path: Path, postgres_url: str, mongodb_url: str
) -> None:
    error = ErrorInfo(code=ErrorCode.EXTRACTION_FAILED, message="no video", tool="scraper")
    chain = TagChain.model_validate({"steps": [{"select": "video", "attr": "src"}]})
    sqlite = SQLitePlugin(
        DBConnectionConfig(name="laptop", plugin="sqlite", url=f"sqlite:///{tmp_path}/udl.db")
    )
    postgres = PostgresPlugin(DBConnectionConfig(name="nas", plugin="postgres", url=postgres_url))
    mongodb = MongoDBPlugin(DBConnectionConfig(name="cloud", plugin="mongodb", url=mongodb_url))
    async with sqlite, postgres, mongodb:
        await sqlite.upsert_site(
            Site(key="example.com", name="Example", default_output_dir=Path("/media"), notes="n")
        )
        await sqlite.upsert_strategy(
            Strategy(
                site_key="example.com",
                url_pattern=r"^/watch/\d+",
                tool="scraper",
                chain=chain,
                options={"timeout": 5, "headers": {"X-A": "b"}},
                source=StrategySource.MANUAL,
                success_count=4,
                failure_count=1,
                last_success_at=T0,
                last_error=error,
            )
        )
        await sqlite.add_download(
            DownloadRecord(
                url="https://example.com/watch/1",
                site_key="example.com",
                tool="scraper",
                status=JobStatus.FAILED,
                output_dir=Path("/media"),
                files=[Path("/media/a.mp4")],
                size_bytes=2**40,
                started_at=T0,
                finished_at=T0,
                error=error,
            )
        )

        await sync(sqlite, postgres)
        await sync(postgres, mongodb)
        back = await sync(mongodb, sqlite)

        assert await everything(mongodb) == await everything(sqlite)
        assert (back.sites, back.strategies, back.downloads) == (
            SyncCounts(unchanged=1),
            SyncCounts(unchanged=1),
            SyncCounts(unchanged=1),
        )
