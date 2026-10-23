"""
------------------
- test_system.py -
------------------

udl db list, udl db sync, udl plugins list, udl config show
"""

# System Imports
import json
from pathlib import Path

# Package Imports
from pydantic import SecretStr

# Local Imports
from support.cli import CLI
from support.engine import connection
from support.interfaces import Harness
from udl.config import APISettings, config_dir
from udl.core.models import ConfigInfo, DatabaseList, PluginList, SyncReport
from udl.db.manager import DBManager
from udl.db.models import Site, SiteQuery
from udl.db.sync import SyncCounts


def test_db_list_lists_the_connections(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path,
        connections=[
            connection("local", primary=True),
            connection("shared", priority=10, read_only=True).model_copy(
                update={"url": "memory://udl:s3cret@nas/udl"}
            ),
        ],
    )
    cli = CLI(harness)

    result = cli("db", "list")
    listed = DatabaseList.model_validate_json(cli("db", "list", "--json").stdout)

    assert result.exit_code == 0, result.output
    assert "local" in result.stdout
    assert "memory://udl:***@nas/udl" in result.stdout
    assert "s3cret" not in result.stdout
    assert [(db.name, db.primary, db.read_only) for db in listed.databases] == [
        ("local", True, False),
        ("shared", False, True),
    ]


def two_databases(tmp_path: Path, **shared: object) -> Harness:
    harness = Harness(
        tmp_path,
        connections=[
            connection("local", primary=True),
            connection("shared", priority=10).model_copy(update=shared),
        ],
    )
    harness.seed(lambda db: db.database("local").upsert_site(Site(key="example.com")))
    return harness


def test_db_sync_copies_one_database_into_another(tmp_path: Path) -> None:
    harness = two_databases(tmp_path)
    cli = CLI(harness)

    dry = cli("db", "sync", "--from", "local", "--to", "shared", "--dry-run")
    result = cli("db", "sync", "--from", "local", "--to", "shared")
    again = SyncReport.model_validate_json(
        cli("db", "sync", "--from", "local", "--to", "shared", "--json").stdout
    )

    assert dry.exit_code == 0, dry.output
    assert "dry run" in dry.stdout.lower()
    assert result.exit_code == 0, result.output
    assert "shared" in result.stdout
    assert again.targets[0].sites == SyncCounts(unchanged=1)

    async def shared_sites(db: DBManager) -> None:
        assert [s.key for s in await db.database("shared").list_sites(SiteQuery())] == [
            "example.com"
        ]

    harness.seed(shared_sites)


def test_db_sync_into_a_read_only_database_is_refused(tmp_path: Path) -> None:
    cli = CLI(two_databases(tmp_path, read_only=True))

    result = cli("db", "sync", "--from", "local", "--to", "shared")

    assert result.exit_code == 1
    assert "read-only" in result.output


def test_db_sync_needs_a_database_to_copy_from_and_another_to_copy_into(cli: CLI) -> None:
    into_itself = cli("db", "sync", "--from", "local", "--to", "local")

    assert cli("db", "sync", "--to", "local").exit_code == 2
    assert cli("db", "sync", "--from", "local").exit_code == 2
    assert into_itself.exit_code == 2
    assert "'local' cannot be synced into itself" in into_itself.output


def test_plugins_list_lists_the_plugins_and_whether_they_can_run(cli: CLI) -> None:
    result = cli("plugins", "list")
    listed = PluginList.model_validate_json(cli("plugins", "list", "--json").stdout)

    assert result.exit_code == 0, result.output
    for name in ("memory", "http", "yt-dlp"):
        assert name in result.stdout
    assert {plugin.name for plugin in listed.plugins} >= {"memory", "http", "browser"}
    assert listed.plugin_dirs == [config_dir() / "plugins"]
    assert "Local plugins (a single .py file each) are read from" in result.stdout


def test_config_show_shows_the_settings_with_secrets_hidden(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    cli = CLI(harness)
    config = tmp_path / "config.toml"
    config.write_text('max_per_site = 3\n[api]\napi_key = "api-secret"\n')

    result = cli("--config", config, "config", "show")
    shown = ConfigInfo.model_validate_json(
        cli("--config", config, "config", "show", "--json").stdout
    )

    assert result.exit_code == 0, result.output
    assert "api-secret" not in result.stdout
    assert json.loads(result.stdout)["max_per_site"] == 3
    assert shown.settings.max_per_site == 3
    assert shown.settings.api == APISettings(api_key=SecretStr("**********"))
