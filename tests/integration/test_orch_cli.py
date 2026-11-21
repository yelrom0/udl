"""
-------------------
- test_orch_cli.py -
-------------------

26.4's done when, run with the real `udl` command: `udl orch links find --url <a PR>` names the
task the pull is on. The records are written by one service, and the command reads them through
another (its own, over the same database file), as it does for a person.
"""

# System Imports
import asyncio
from pathlib import Path

# Package Imports
from typer.testing import CliRunner

# Local Imports
from udl.config import Settings
from udl.core.service import UDLService
from udl.interfaces.cli.app import app
from udl.plugins.registry import PluginRegistry

PULL = "https://github.com/yelrom0/udl/pull/57"


def config(tmp_path: Path) -> Path:
    """A udl config that keeps its records in a file (so a second service reads them)."""
    path = tmp_path / "config.toml"
    path.write_text(
        "\n".join(
            [
                "[db]",
                'write_policy = "primary"',
                "[[db.connections]]",
                'name = "local"',
                'plugin = "sqlite"',
                f'url = "sqlite:///{tmp_path}/udl.db"',
                "primary = true",
                "",
                "[secrets]",
                'store = "memory"',
                "",
            ]
        )
    )
    return path


async def test_the_cli_finds_a_task_from_its_pull(tmp_path: Path) -> None:
    settings = Settings.load(config(tmp_path))
    # (udl's own plugins, as a real `udl` run loads them: the sqlite database and orch.)
    plugins = PluginRegistry.discover(builtin_only=True)
    async with UDLService(settings, registry=plugins) as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await service.call(
            "orch_set_repo",
            {"name": "udl", "github": "yelrom0/udl", "account": "github:yelrom0"},
        )
        await service.call("orch_set_feature", {"key": "26", "title": "Work tracking"})
        await service.call(
            "orch_set_task", {"key": "26.2", "feature": "26", "title": "Features and tasks"}
        )
        await service.call(
            "orch_add_link",
            {
                "owner_kind": "task",
                "owner": "26.2",
                "link_kind": "pull",
                "url": f"{PULL}/files",
                "title": "Features and tasks",
            },
        )

    # (The command runs its own event loop, so in a thread of its own.)
    cli = await asyncio.to_thread(
        CliRunner(env={"COLUMNS": "200"}).invoke,
        app,
        ["--config", str(config(tmp_path)), "orch", "links", "find", "--url", PULL],
    )

    assert cli.exit_code == 0, cli.output
    assert "26.2" in cli.stdout
    assert "Features and tasks" in cli.stdout
