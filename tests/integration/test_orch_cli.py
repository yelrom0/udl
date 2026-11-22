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
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
from typer.testing import CliRunner

# Local Imports
from udl.config import Settings
from udl.core.service import UDLService
from udl.interfaces.cli.app import app
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.records import StateChange, TaskRecords
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


async def test_the_cli_sizes_a_task_and_shows_the_totals(tmp_path: Path) -> None:
    # (26.8's own two commands: a task is sized with `--size`, and a feature's answer prints
    # each task's size and what they come to.)
    settings = Settings.load(config(tmp_path))
    plugins = PluginRegistry.discover(builtin_only=True)
    async with UDLService(settings, registry=plugins) as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await service.call("orch_set_feature", {"key": "26", "title": "Work tracking"})
        await service.call(
            "orch_set_task", {"key": "26.2", "feature": "26", "title": "Features and tasks"}
        )

    runner = CliRunner(env={"COLUMNS": "200"})
    sized = await asyncio.to_thread(
        runner.invoke,
        app,
        [
            "--config",
            str(config(tmp_path)),
            "orch",
            "tasks",
            "set",
            "26.2",
            "--size",
            "L",
            "--json",
        ],
    )
    shown = await asyncio.to_thread(
        runner.invoke,
        app,
        ["--config", str(config(tmp_path)), "orch", "features", "show", "26"],
    )
    answered = await asyncio.to_thread(
        runner.invoke,
        app,
        ["--config", str(config(tmp_path)), "orch", "features", "show", "26", "--json"],
    )

    assert sized.exit_code == 0, sized.output
    assert json.loads(sized.stdout)["size"] == "L"
    assert json.loads(sized.stdout)["points"] == 5
    assert shown.exit_code == 0, shown.output
    assert "Size" in shown.stdout
    assert "points: 5" in shown.stdout
    # (The task's own row: `26.2` and its size on one line of the table.)
    assert re.search(r"^.*\b26\.2\b.*\bL\b.*$", shown.stdout, re.MULTILINE)
    assert answered.exit_code == 0, answered.output
    assert json.loads(answered.stdout)["totals"] == {
        "points": 5,
        "points_merged": 0,
        "points_ready": 0,
        "unsized": 0,
    }


async def test_the_cli_prints_a_project_s_progress(tmp_path: Path) -> None:
    # (27.10's done when: the burn-up's numbers, the forecast and the hours per point, on a
    # history: one task merged after two hours' work, and one three-point task still open.)
    settings = Settings.load(config(tmp_path))
    plugins = PluginRegistry.discover(builtin_only=True)
    async with UDLService(settings, registry=plugins) as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await service.call("orch_set_feature", {"key": "26", "title": "Work tracking"})
        await service.call(
            "orch_set_task",
            {"key": "26.1", "feature": "26", "title": "Merged one", "size": "S"},
        )
        await service.call(
            "orch_set_task",
            {"key": "26.2", "feature": "26", "title": "Still open", "size": "M"},
        )
        records = AppRecords(service.db)
        merged = await TaskRecords(records).task("udl", "26.1")
        assert merged is not None
        await TaskRecords(records).import_task(merged.model_copy(update={"state": "merged"}))
        start = datetime(2026, 11, 21, 9, tzinfo=UTC)
        await records.save_all(
            [
                StateChange(
                    task=merged.id,
                    from_state="open",
                    to_state="in_progress",
                    at=start,
                    by="deepseek-a",
                ),
                StateChange(
                    task=merged.id,
                    from_state="in_progress",
                    to_state="merged",
                    at=start + timedelta(hours=2),
                    by="deepseek-a",
                ),
            ]
        )

    runner = CliRunner(env={"COLUMNS": "200"})
    shown = await asyncio.to_thread(
        runner.invoke,
        app,
        ["--config", str(config(tmp_path)), "orch", "progress", "udl"],
    )
    answered = await asyncio.to_thread(
        runner.invoke,
        app,
        ["--config", str(config(tmp_path)), "orch", "progress", "udl", "--json"],
    )

    assert shown.exit_code == 0, shown.output
    assert "26" in shown.stdout  # (the feature's own line)
    assert "forecast" in shown.stdout.lower()
    assert "hours" in shown.stdout.lower()
    assert answered.exit_code == 0, answered.output
    progress = json.loads(answered.stdout)
    assert progress["series"][-1]["points"] == 4  # (one point merged, three still open)
    assert progress["series"][-1]["merged"] == 0
    # (One sample is fewer than a rate needs, so it is an hour a point: what nothing learned
    # answers, and it says so.)
    assert progress["models"][0]["hours_per_point"] == {"hours": 1.0, "source": "default"}
    assert progress["recent"][0]["actual"] == "PT2H"
    assert progress["recent"][0]["estimate"] == "PT1H"  # (one point at that rate)
