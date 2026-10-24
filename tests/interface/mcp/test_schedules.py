"""
---------------------
- test_schedules.py -
---------------------

The schedules' tools: an agent can preview a spec, make a cron job in one call, see it, run it
by hand and delete it; what changes schedules is marked as changing things
"""

# System Imports
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from mcp import Client

# Local Imports
from support.actions import fake_action
from support.interfaces import Harness
from udl.config import AutomationsSettings
from udl.plugins.registry import ACTIONS

FAKE = fake_action("fake")


async def call(client: Client, name: str, /, **arguments: Any) -> dict[str, Any]:
    """The tool's structured result (it must not fail)."""
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(
        tmp_path,
        automations=AutomationsSettings(scripts_dir=tmp_path / "scripts", timezone="UTC"),
    )
    found.registry.register(ACTIONS, FAKE)
    return found


async def test_an_agent_previews_then_makes_a_cron_job(client: Client) -> None:
    preview = await call(client, "preview_schedule", spec="0 8 * * 1-5", count=3)
    await call(client, "set_action", name="morning-digest", plugin="fake")

    saved = await call(
        client, "set_schedule", name="digest", cron="0 8 * * 1-5", actions=["morning-digest"]
    )

    assert len(preview["times"]) == 3
    assert saved["next_times"][:3] == [time["utc"] for time in preview["times"]]
    assert saved["subscription"]["name"] == "schedule:digest"


async def test_an_agent_lists_shows_runs_and_deletes_a_schedule(client: Client) -> None:
    await call(client, "set_schedule", name="tick", every="15m", timezone="Europe/London")

    listed = await call(client, "list_schedules")
    shown = await call(client, "get_schedule", name="tick")
    fired = await call(client, "run_schedule", name="tick")
    deleted = await call(client, "delete_schedule", name="tick")

    assert [found["name"] for found in listed["schedules"]] == ["tick"]
    assert shown["spec_text"].startswith("every 15m from ")
    assert shown["zone"] == "Europe/London"
    assert (fired["source"], fired["data"]["manual"]) == ("mcp", True)
    assert deleted == {"deleted": True}


@pytest.mark.parametrize(
    ("name", "read_only", "destructive"),
    [
        ("list_schedules", True, False),
        ("get_schedule", True, False),
        ("preview_schedule", True, False),
        ("set_schedule", False, False),
        ("run_schedule", False, False),
        ("delete_schedule", False, True),
    ],
)
async def test_the_schedule_tools_say_what_they_change(
    client: Client, name: str, read_only: bool, destructive: bool
) -> None:
    [tool] = [tool for tool in (await client.list_tools()).tools if tool.name == name]

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is read_only
    assert bool(tool.annotations.destructive_hint) is destructive
