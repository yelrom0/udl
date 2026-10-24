"""
-----------------------
- test_automations.py -
-----------------------

The automations' tools: an agent can see the event types, emit events, make actions and
subscriptions (a filter as the shorthand), run actions, and follow runs and the status; what
changes the automations is marked as changing things
"""

# System Imports
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from mcp import Client
from mcp_types import TextContent

# Local Imports
from support.actions import fake_action
from support.interfaces import Harness
from udl.config import APISettings, AutomationsSettings
from udl.plugins.registry import ACTIONS

FAKE = fake_action("fake")


async def call(client: Client, name: str, /, **arguments: Any) -> dict[str, Any]:
    """The tool's structured result (it must not fail)."""
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def failure(client: Client, name: str, /, **arguments: Any) -> str:
    """The tool's error, as the agent reads it."""
    result = await client.call_tool(name, arguments)
    assert result.is_error
    [content] = result.content
    assert isinstance(content, TextContent)
    return content.text


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(
        tmp_path,
        api=APISettings(allowed_output_roots=[tmp_path]),
        automations=AutomationsSettings(scripts_dir=tmp_path / "scripts"),
    )
    found.registry.register(ACTIONS, FAKE)
    return found


async def setup(client: Client) -> None:
    await call(client, "set_action", name="hello", plugin="fake", options={"output": "hi"})
    await call(
        client,
        "set_subscription",
        name="greet",
        events=["custom.hello"],
        actions=["hello"],
        filter='name == "x"',
    )


async def test_an_agent_sees_the_event_types_and_their_fields(client: Client) -> None:
    found = await call(client, "list_event_types", match="udl.download.finished")

    [finished] = found["types"]
    assert "url" in {field["path"] for field in finished["fields"]}


async def test_an_agent_emits_lists_and_shows_events(client: Client) -> None:
    emitted = await call(client, "emit_event", type="custom.hello", data={"name": "x"})
    listed = await call(client, "list_events", type="custom.*")
    shown = await call(client, "get_event", id=emitted["id"])

    assert emitted["source"] == "mcp"
    assert [found["id"] for found in listed["events"]] == [emitted["id"]]
    assert shown["event"]["data"] == {"name": "x"}


async def test_an_agent_makes_and_tries_a_subscription(client: Client) -> None:
    await setup(client)
    await call(client, "emit_event", type="custom.hello", data={"name": "x"})

    shown = await call(client, "get_subscription", name="greet")
    listed = await call(client, "list_subscriptions")
    tested = await call(client, "test_subscription", name="greet")

    assert shown["filter_text"] == 'name == "x"'
    assert [found["name"] for found in listed["subscriptions"]] == ["greet"]
    assert [found["matched"] for found in tested["events"]] == [True]


async def test_a_filter_that_does_not_fit_is_a_tool_error(client: Client) -> None:
    await call(client, "set_action", name="hello", plugin="fake")

    said = await failure(
        client,
        "set_subscription",
        name="bad",
        events=["udl.download.finished"],
        actions=["hello"],
        filter="nope",
    )

    assert "invalid_filter:" in said


async def test_an_agent_runs_an_action_and_follows_its_run(client: Client) -> None:
    await setup(client)

    ran = await call(client, "run_action", name="hello")
    listed = await call(client, "list_runs", action="hello")
    shown = await call(client, "get_run", id=ran["run"]["id"])
    cancelled = await call(client, "cancel_run", id=ran["run"]["id"])
    actions = await call(client, "list_actions")
    action = await call(client, "get_action", name="hello")

    assert ran["run"]["output"] == "hi"
    assert [found["id"] for found in listed["runs"]] == [ran["run"]["id"]]
    assert shown["status"] == "succeeded"
    assert cancelled["status"] == "succeeded"  # (it had ended)
    assert [found["name"] for found in actions["actions"]] == ["hello"]
    assert action["used_by"] == ["greet"]


async def test_an_agent_replays_an_event_and_deletes_what_it_made(client: Client) -> None:
    await setup(client)
    emitted = await call(client, "emit_event", type="custom.hello", data={"name": "x"})

    replayed = await call(client, "replay_event", id=emitted["id"])
    in_use = await failure(client, "delete_action", name="hello")
    subscription = await call(client, "delete_subscription", name="greet")
    action = await call(client, "delete_action", name="hello")

    assert [run["action"] for run in replayed["runs"]] == ["hello"]
    assert "in_use:" in in_use
    assert (subscription, action) == ({"deleted": True}, {"deleted": True})


async def test_an_agent_sees_the_scripts_and_the_status(client: Client) -> None:
    scripts = await call(client, "list_scripts")
    status = await call(client, "get_status")

    assert scripts["exists"] is False
    assert status["automations"] == "stopped"


@pytest.mark.parametrize(
    ("name", "read_only", "destructive"),
    [
        ("list_events", True, False),
        ("emit_event", False, False),
        ("set_subscription", False, False),
        ("delete_subscription", False, True),
        ("run_action", False, False),
        ("cancel_run", False, True),
        ("get_status", True, False),
    ],
)
async def test_what_a_tool_does_is_said(
    client: Client, name: str, read_only: bool, destructive: bool
) -> None:
    tools: dict[str, Any] = {tool.name: tool for tool in (await client.list_tools()).tools}

    annotations = tools[name].annotations
    assert (annotations.read_only_hint, annotations.destructive_hint) == (read_only, destructive)
