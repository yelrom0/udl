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

# Local Imports
from udl.config import DBConnectionConfig
from udl.db.models import Site
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
