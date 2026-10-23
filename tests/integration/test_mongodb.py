"""
-------------------
- test_mongodb.py -
-------------------

MongoDBPlugin against a real server (a Docker container, see tests/support/servers.py): where it
keeps things, schema versions, several udls connecting at once, and failures that keep the
password to themselves
"""

# System Imports
import asyncio
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

# Package Imports
import pymongo
import pytest
from pymongo.asynchronous.collection import AsyncCollection

# Local Imports
from udl.config import DBConnectionConfig
from udl.db.models import DownloadRecord, Site, SiteQuery
from udl.errors import DatabaseError
from udl.plugins.databases.mongodb import SCHEMA_VERSION, MongoDBPlugin

UNREACHABLE = "mongodb://udl:hunter2@127.0.0.1:9/udl"  # port 9 (discard) has nothing listening


def connection(url: str) -> DBConnectionConfig:
    return DBConnectionConfig(name="shared", plugin="mongodb", url=url)


def raw(url: str) -> pymongo.MongoClient[dict[str, Any]]:
    return pymongo.MongoClient(url)


async def test_the_database_is_the_one_the_url_names(mongodb_url: str) -> None:
    async with MongoDBPlugin(connection(mongodb_url)) as db:
        await db.upsert_site(Site(key="example.com"))

    with raw(mongodb_url) as client:
        stored = client.get_default_database()
        assert set(stored.list_collection_names()) >= {"sites", "schema_version"}
        assert stored["sites"].count_documents({"key": "example.com"}) == 1


async def test_documents_hold_the_json_form_with_times_that_sort_as_text(mongodb_url: str) -> None:
    started = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    record = DownloadRecord(
        url="https://example.com/v/1", site_key="example.com", started_at=started
    )
    async with MongoDBPlugin(connection(mongodb_url)) as db:
        await db.add_download(record)

    with raw(mongodb_url) as client:
        document = client.get_default_database()["downloads"].find_one()
    assert document is not None
    assert document["_id"] == str(record.id)
    assert document["started_at"] == "2026-01-02T03:04:05.000000+00:00"
    assert document["files"] == []


async def test_connecting_again_keeps_the_connection(mongodb_url: str) -> None:
    async with MongoDBPlugin(connection(mongodb_url)) as db:
        site = await db.upsert_site(Site(key="example.com"))

        await db.connect()

        assert await db.get_site("example.com") == site


async def test_a_new_site_another_udl_stores_meanwhile_is_updated_not_stored_twice(
    mongodb_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    theirs = uuid4()
    looked = AsyncCollection[dict[str, Any]].find_one_and_update
    other = MongoDBPlugin(connection(mongodb_url))
    raced = False

    async def look_then_lose_the_race(
        self: AsyncCollection[dict[str, Any]], *args: Any, **kwargs: Any
    ) -> Any:
        nonlocal raced
        found = await looked(self, *args, **kwargs)
        if not raced:  # the other udl stores the site just after this one looked for it
            raced = True
            await other.upsert_site(Site(id=theirs, key="example.com"))
        return found

    async with other, MongoDBPlugin(connection(mongodb_url)) as db:
        monkeypatch.setattr(AsyncCollection, "find_one_and_update", look_then_lose_the_race)

        stored = await db.upsert_site(Site(key="example.com", name="Mine"))

        assert raced
        assert (stored.id, stored.name) == (theirs, "Mine")
        assert await db.list_sites(SiteQuery()) == [stored]


async def test_a_site_with_the_id_of_another_is_refused(mongodb_url: str) -> None:
    async with MongoDBPlugin(connection(mongodb_url)) as db:
        stored = await db.upsert_site(Site(key="example.com"))

        with pytest.raises(DatabaseError, match="duplicate key"):
            await db.upsert_site(Site(id=stored.id, key="example.org"))

        assert await db.list_sites(SiteQuery()) == [stored]


async def test_a_database_from_a_newer_udl_is_refused(mongodb_url: str) -> None:
    async with MongoDBPlugin(connection(mongodb_url)):
        pass
    with raw(mongodb_url) as client:
        client.get_default_database()["schema_version"].update_one(
            {}, {"$set": {"version": SCHEMA_VERSION + 1}}
        )
    db = MongoDBPlugin(connection(mongodb_url))

    with pytest.raises(DatabaseError, match=f"schema version {SCHEMA_VERSION + 1}") as caught:
        await db.connect()
    await db.close()

    assert caught.value.hint is not None
    assert "upgrade udl" in caught.value.hint.lower()


async def test_udls_that_connect_to_a_new_database_at_once_all_get_it(mongodb_url: str) -> None:
    databases = [MongoDBPlugin(connection(mongodb_url)) for _ in range(5)]
    try:
        await asyncio.gather(*(db.connect() for db in databases))

        await databases[0].upsert_site(Site(key="example.com"))
        for db in databases:
            assert [site.key for site in await db.list_sites(SiteQuery())] == ["example.com"]
    finally:
        for db in databases:
            await db.close()

    with raw(mongodb_url) as client:
        assert list(client.get_default_database()["schema_version"].find()) == [
            {"_id": 1, "version": SCHEMA_VERSION}
        ]


async def test_a_server_that_cannot_be_reached_is_a_database_error_without_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MongoDBPlugin, "connect_timeout", 0.5)
    db = MongoDBPlugin(connection(UNREACHABLE))

    with pytest.raises(DatabaseError, match="'shared' database failed") as caught:
        await db.connect()

    assert "hunter2" not in str(caught.value)
    assert await db.health() is False
    await db.close()


async def test_the_url_s_server_selection_timeout_beats_udl_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(MongoDBPlugin, "connect_timeout", 60)
    db = MongoDBPlugin(connection(f"{UNREACHABLE}?serverSelectionTimeoutMS=300"))
    began = time.monotonic()

    with pytest.raises(DatabaseError):
        await db.connect()
    await db.close()

    assert time.monotonic() - began < 10
