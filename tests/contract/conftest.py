"""
---------------
- conftest.py -
---------------

The databases the database contract suites run on: one of each plugin, found the way udl finds
it (by entry point), on a fresh, empty database
"""

# System Imports
from collections.abc import AsyncIterator, Callable

# Package Imports
import pytest

# Local Imports
from udl.config import DBConnectionConfig
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.registry import DATABASES, PluginRegistry

# The URL of a fresh, empty database for each plugin. Those that need a server get one from its
# fixture (tests/conftest.py), which skips them when there is no server to use.
CONNECTIONS: dict[str, Callable[[pytest.FixtureRequest], str]] = {
    "memory": lambda _: "memory://",
    "sqlite": lambda request: f"sqlite+aiosqlite:///{request.getfixturevalue('tmp_path')}/udl.db",
    "postgres": lambda request: request.getfixturevalue("postgres_url"),
    "mongodb": lambda request: request.getfixturevalue("mongodb_url"),
}


@pytest.fixture(params=sorted(CONNECTIONS))
def connection(request: pytest.FixtureRequest) -> DBConnectionConfig:
    """A connection to a fresh, empty database of each plugin."""
    plugin_name: str = request.param
    return DBConnectionConfig(
        name="contract", plugin=plugin_name, url=CONNECTIONS[plugin_name](request)
    )


@pytest.fixture
async def db(connection: DBConnectionConfig) -> AsyncIterator[DatabasePlugin]:
    """The connection's database, connected."""
    plugin = PluginRegistry.discover().get(DATABASES, connection.plugin)
    async with plugin(connection) as database:
        yield database


@pytest.fixture
async def other(
    db: DatabasePlugin, connection: DBConnectionConfig
) -> AsyncIterator[DatabasePlugin]:
    """A second connection to the same database (another udl's). A memory database lives in
    one udl only, so for it this is the same connection."""
    if connection.plugin == "memory":
        yield db
        return
    plugin = PluginRegistry.discover().get(DATABASES, connection.plugin)
    async with plugin(connection) as database:
        yield database
