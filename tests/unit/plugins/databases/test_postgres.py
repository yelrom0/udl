"""
--------------------
- test_postgres.py -
--------------------

PostgresPlugin, without a server: the URLs it takes and what it needs installed (the contract
suite and tests/integration/test_postgres.py run it against a server)
"""

# Package Imports
import pytest

# Local Imports
from udl.config import DBConnectionConfig
from udl.errors import ConfigError
from udl.plugins.databases.postgres import PostgresPlugin
from udl.plugins.registry import DATABASES, PluginRegistry
from udl.plugins.requirements import RequirementKind


def connection(url: str) -> DBConnectionConfig:
    return DBConnectionConfig(name="shared", plugin="postgres", url=url)


def test_it_is_the_postgres_plugin() -> None:
    assert PluginRegistry.discover().get(DATABASES, "postgres") is PostgresPlugin


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://udl:secret@nas:5433/udl",
        "postgres://udl:secret@nas:5433/udl",  # libpq's spelling
        "postgresql+psycopg://udl:secret@nas:5433/udl",
        "postgresql+psycopg2://udl:secret@nas:5433/udl",
        "postgresql+asyncpg://udl:secret@nas:5433/udl",
    ],
)
def test_any_postgres_url_is_used_through_async_psycopg(url: str) -> None:
    plugin = PostgresPlugin(connection(url))

    assert plugin.url.drivername == "postgresql+psycopg"
    assert (plugin.url.username, plugin.url.password) == ("udl", "secret")
    assert (plugin.url.host, plugin.url.port, plugin.url.database) == ("nas", 5433, "udl")


def test_the_url_s_query_is_kept() -> None:
    plugin = PostgresPlugin(connection("postgresql://nas/udl?options=-csearch_path%3Dudl"))

    assert dict(plugin.url.query) == {"options": "-csearch_path=udl"}


@pytest.mark.parametrize(
    "url", ["sqlite:///udl.db", "mysql://udl:secret@nas/udl", "mongodb://nas/udl"]
)
def test_a_url_that_is_not_postgres_is_refused_without_showing_its_password(url: str) -> None:
    with pytest.raises(ConfigError, match="'shared' is not a PostgreSQL URL") as caught:
        PostgresPlugin(connection(url))

    assert "secret" not in caught.value.message


def test_it_needs_psycopg() -> None:
    [requirement] = PostgresPlugin.requires

    assert (requirement.kind, requirement.name) == (RequirementKind.MODULE, "psycopg")
    assert requirement.hint is not None
    assert "postgres" in requirement.hint
    assert requirement.met()
