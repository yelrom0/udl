"""
-------------------
- test_mongodb.py -
-------------------

MongoDBPlugin, without a server: the URLs it takes, what it needs installed, and what it does
before it is connected (the contract suite and tests/integration/test_mongodb.py run it against
a server)
"""

# Package Imports
import pytest

from support.records import Note

# Local Imports
from udl.config import DBConnectionConfig
from udl.db.models import Site
from udl.db.records import Record
from udl.errors import ConfigError, DatabaseError
from udl.plugins.databases.mongodb import MongoDBPlugin
from udl.plugins.registry import DATABASES, PluginRegistry
from udl.plugins.requirements import RequirementKind


def connection(url: str) -> DBConnectionConfig:
    return DBConnectionConfig(name="shared", plugin="mongodb", url=url)


def test_it_is_the_mongodb_plugin() -> None:
    assert PluginRegistry.discover().get(DATABASES, "mongodb") is MongoDBPlugin


@pytest.mark.parametrize(
    "url",
    [
        "mongodb://udl:secret@nas:27018/udl",
        "mongodb://nas",
        "mongodb+srv://udl:secret@cluster.example.net/udl?retryWrites=true",
    ],
)
def test_it_takes_mongodb_urls(url: str) -> None:
    MongoDBPlugin(connection(url))


@pytest.mark.parametrize(
    "url", ["postgresql://udl:secret@nas/udl", "sqlite:///udl.db", "nas:27017", "memory://"]
)
def test_a_url_that_is_not_mongodb_is_refused_without_showing_its_password(url: str) -> None:
    with pytest.raises(ConfigError, match="'shared' is not a MongoDB URL") as caught:
        MongoDBPlugin(connection(url))

    assert "secret" not in caught.value.message


def test_it_needs_pymongo() -> None:
    [requirement] = MongoDBPlugin.requires

    assert (requirement.kind, requirement.name) == (RequirementKind.MODULE, "pymongo")
    assert requirement.hint is not None
    assert "mongodb" in requirement.hint
    assert requirement.met()


async def test_it_is_unhealthy_and_refuses_work_until_connected() -> None:
    db = MongoDBPlugin(connection("mongodb://nas/udl"))

    assert await db.health() is False
    with pytest.raises(DatabaseError, match="'shared' database is not connected"):
        await db.upsert_site(Site(key="example.com"))
    await db.close()  # (closing a database that is not connected does nothing)


@pytest.mark.parametrize(
    ("url", "problem"),
    [
        ("mongodb://udl:secret@nas:http/udl", "Port contains non-digit characters"),
        ("mongodb://udl:secret@/udl", "Empty host"),
    ],
)
async def test_an_invalid_url_is_a_database_error_on_connect_without_the_password(
    url: str, problem: str
) -> None:
    db = MongoDBPlugin(connection(url))

    with pytest.raises(DatabaseError, match=problem) as caught:
        await db.connect()

    assert "secret" not in str(caught.value)
    assert await db.health() is False


async def test_a_commit_that_fails_is_a_database_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session and the commit run outside `_errors()`: a WriteConflict, a transient error or
    a primary that stepped down at commit must still come out as udl's `DatabaseError`, not as
    pymongo's own error (27.3a's contract: a batch fails as a batch)."""
    from pymongo.errors import PyMongoError

    inserted: list[dict[str, object]] = []

    class Collection:
        async def find_one_and_update(self, *args: object, **kwargs: object) -> None:
            return None  # (not there yet: it is inserted)

        async def insert_one(self, document: dict[str, object], **kwargs: object) -> None:
            inserted.append(document)

    class Transaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *exc: object) -> None:
            raise PyMongoError("commit failed: the primary stepped down")

    class Session:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def start_transaction(self) -> Transaction:
            return Transaction()

    class Client:
        def start_session(self) -> Session:
            return Session()

    async def collection_of[R: Record](kind: type[R]) -> Collection:
        del kind
        return Collection()

    db = MongoDBPlugin(connection("mongodb://nas/udl"))
    assert db.transactions is True  # (nothing asked the deployment: the batch assumes it has them)
    monkeypatch.setattr(db, "_client", Client())
    monkeypatch.setattr(db, "_records_of", collection_of)

    with pytest.raises(DatabaseError, match="commit failed") as caught:
        await db.upsert_records([Note(topic="t", slug="s")])

    assert isinstance(caught.value.__cause__, PyMongoError)
    assert len(inserted) == 1  # (the write went in; only the commit failed)
