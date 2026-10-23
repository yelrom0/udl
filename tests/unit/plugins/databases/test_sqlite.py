"""
------------------
- test_sqlite.py -
------------------

SQLitePlugin: the default database, and where its file is created
"""

# System Imports
import re
import stat
from pathlib import Path

# Package Imports
import platformdirs
import pytest

# Local Imports
from udl.config import DBConnectionConfig, Settings
from udl.db.models import Site
from udl.errors import ConfigError
from udl.plugins.databases.sqlite import SQLitePlugin


def connection(url: str) -> DBConnectionConfig:
    return DBConnectionConfig(name="local", plugin="sqlite", url=url)


async def test_the_default_database_lives_in_the_user_data_dir_not_the_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    [default] = Settings().db.connections

    async with SQLitePlugin(default):
        pass

    assert (platformdirs.user_data_path("udl") / "udl.db").is_file()
    assert list(cwd.iterdir()) == []


async def test_connect_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "udl.db"

    async with SQLitePlugin(connection(f"sqlite+aiosqlite:///{path}")):
        pass

    assert path.is_file()


async def test_the_home_dir_and_env_vars_in_the_path_are_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("UDL_TEST_DBS", str(tmp_path / "dbs"))

    async with (
        SQLitePlugin(connection("sqlite+aiosqlite:///~/udl.db")),
        SQLitePlugin(connection("sqlite+aiosqlite:///$UDL_TEST_DBS/udl.db")),
    ):
        pass

    assert (tmp_path / "home" / "udl.db").is_file()
    assert (tmp_path / "dbs" / "udl.db").is_file()


@pytest.mark.parametrize("scheme", ["sqlite", "sqlite+pysqlite"])
async def test_a_sync_driver_in_the_url_is_replaced_by_the_async_one(
    tmp_path: Path, scheme: str
) -> None:
    path = tmp_path / "udl.db"

    async with SQLitePlugin(connection(f"{scheme}:///{path}")) as db:
        await db.upsert_site(Site(key="example.com"))

    assert path.is_file()


@pytest.mark.parametrize("url", ["sqlite://", "sqlite+aiosqlite:///:memory:"])
async def test_an_in_memory_database_needs_no_file(url: str) -> None:
    async with SQLitePlugin(connection(url)) as db:
        site = await db.upsert_site(Site(key="example.com"))

        assert await db.get_site("example.com") == site


def test_a_url_for_another_kind_of_database_raises_config_error() -> None:
    with pytest.raises(ConfigError, match=re.escape("'local' is not a SQLite URL")):
        SQLitePlugin(connection("postgresql+psycopg://udl@nas/udl"))


async def test_a_new_database_is_private_to_its_owner(tmp_path: Path) -> None:
    # It holds the download history, and the options of strategies.
    path = tmp_path / "data" / "udl.db"

    async with SQLitePlugin(connection(f"sqlite:///{path}")) as db:
        await db.upsert_site(Site(key="example.com"))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


async def test_an_existing_database_and_its_dir_keep_their_permissions(tmp_path: Path) -> None:
    path = tmp_path / "shared" / "udl.db"
    path.parent.mkdir(mode=0o750)
    async with SQLitePlugin(connection(f"sqlite:///{path}")):
        pass
    path.chmod(0o640)

    async with SQLitePlugin(connection(f"sqlite:///{path}")):
        pass

    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o750
