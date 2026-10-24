"""
--------------------
- test_postgres.py -
--------------------

PostgresPlugin against a real server (a Docker container, see tests/support/servers.py): a
shared database that several udls connect to at once, migrations, and failures that keep the
password to themselves
"""

# System Imports
import asyncio
from uuid import uuid4

# Package Imports
import psycopg
import pytest
from sqlalchemy.engine import make_url

# Local Imports
from udl.chains.models import TagChain
from udl.config import DBConnectionConfig
from udl.db.models import Site, SiteQuery, Strategy, StrategySource
from udl.errors import DatabaseError
from udl.plugins.databases.postgres import PostgresPlugin


def connection(url: str) -> DBConnectionConfig:
    return DBConnectionConfig(name="shared", plugin="postgres", url=url)


def plain(url: str) -> str:
    """`url` for psycopg itself (no SQLAlchemy driver name)."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def test_udls_that_connect_to_a_new_database_at_once_all_get_it(postgres_url: str) -> None:
    databases = [PostgresPlugin(connection(postgres_url)) for _ in range(5)]
    try:
        await asyncio.gather(*(db.connect() for db in databases))

        await databases[0].upsert_site(Site(key="example.com"))
        for db in databases:
            assert [site.key for site in await db.list_sites(SiteQuery())] == ["example.com"]
    finally:
        for db in databases:
            await db.close()


async def until_blocked(watcher: psycopg.AsyncConnection) -> None:
    """Wait until a session of the watched database waits for another's lock."""
    query = (
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE datname = current_database() AND wait_event_type = 'Lock'"
    )
    async with asyncio.timeout(10):
        while True:
            if await (await watcher.execute(query)).fetchone() == (1,):
                return
            await asyncio.sleep(0.02)


async def test_a_new_site_another_udl_stores_meanwhile_is_updated_not_stored_twice(
    postgres_url: str,
) -> None:
    theirs = uuid4()
    async with (
        PostgresPlugin(connection(postgres_url)) as db,
        await psycopg.AsyncConnection.connect(plain(postgres_url)) as other,
        await psycopg.AsyncConnection.connect(plain(postgres_url), autocommit=True) as watcher,
    ):
        # The other udl has stored the site, but not committed yet.
        await other.execute(
            "INSERT INTO site (id, key, created_at, updated_at) "
            "VALUES (%s, 'example.com', now(), now())",
            [theirs],
        )
        upserting = asyncio.create_task(db.upsert_site(Site(key="example.com", name="Mine")))
        await until_blocked(watcher)  # this udl found no site, so it is inserting one
        await other.commit()

        stored = await upserting

        assert (stored.id, stored.name) == (theirs, "Mine")
        assert await db.list_sites(SiteQuery()) == [stored]


async def test_version_2_adds_strategies_tag_chains(postgres_url: str) -> None:
    async with PostgresPlugin(connection(postgres_url)) as db:
        await db.upsert_site(Site(key="example.com"))
        learned = await db.upsert_strategy(
            Strategy(site_key="example.com", tool="yt-dlp", source=StrategySource.PROBE)
        )
    # As udl 0.1 made it (schema version 1): no tag chains.
    with psycopg.connect(plain(postgres_url), autocommit=True) as raw:
        raw.execute("ALTER TABLE strategy DROP COLUMN chain")
        raw.execute("UPDATE schema_version SET version = 1")

    chain = TagChain.model_validate({"steps": [{"select": "video", "attr": "src"}]})
    async with PostgresPlugin(connection(postgres_url)) as db:
        assert await db.strategies_for("example.com") == [learned]
        chained = await db.upsert_strategy(
            Strategy(
                site_key="example.com", tool="scraper", chain=chain, source=StrategySource.MANUAL
            )
        )
        assert chained in await db.strategies_for("example.com")

    with psycopg.connect(plain(postgres_url)) as raw:
        assert raw.execute("SELECT version FROM schema_version").fetchall() == [(3,)]


async def test_a_server_that_cannot_be_reached_is_a_database_error_without_the_password() -> None:
    # Port 9 (discard) has nothing listening.
    db = PostgresPlugin(connection("postgresql://udl:hunter2@127.0.0.1:9/udl?connect_timeout=2"))

    with pytest.raises(DatabaseError, match="'shared' database failed") as caught:
        await db.connect()
    await db.close()

    assert "hunter2" not in str(caught.value)
    assert await db.health() is False


async def test_a_wrong_password_is_a_database_error_without_the_password(
    postgres_url: str,
) -> None:
    wrong = make_url(postgres_url).set(password="hunter2")  # noqa: S106 - a wrong one
    db = PostgresPlugin(connection(wrong.render_as_string(hide_password=False)))

    with pytest.raises(DatabaseError, match="password authentication failed") as caught:
        await db.connect()
    await db.close()

    assert "hunter2" not in str(caught.value)
