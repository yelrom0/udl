"""
------------------
- test_system.py -
------------------

udl db list, udl plugins list, udl config show
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
from udl.config import APISettings
from udl.core.models import ConfigInfo, DatabaseList, PluginList


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


def test_plugins_list_lists_the_plugins_and_whether_they_can_run(cli: CLI) -> None:
    result = cli("plugins", "list")
    listed = PluginList.model_validate_json(cli("plugins", "list", "--json").stdout)

    assert result.exit_code == 0, result.output
    for name in ("memory", "http", "yt-dlp"):
        assert name in result.stdout
    assert {plugin.name for plugin in listed.plugins} >= {"memory", "http", "browser"}


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
