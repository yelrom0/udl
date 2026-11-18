"""
----------------
- test_apps.py -
---------------

An app's operations through the interfaces udl makes for every plugin's: a CLI command
(`udl <app> <op>`), an API route (`POST /plugins/<app>/<op>`) and an MCP tool (`<app>_<op>`),
each made from the operation's request and response models, so an app is usable from all three
with no interface code of its own
"""

# System Imports
import json
from pathlib import Path

# Package Imports
import pytest
from mcp.types import TextContent

# Local Imports
from support.api import client_for as api_client_for
from support.apps import FakeApp
from support.cli import CLI
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from udl.core.models import ListPluginsRequest
from udl.plugins.registry import APPS


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(tmp_path)
    found.registry.register(APPS, FakeApp)
    return found


# ---------------------------------------------------------------------------------------------
# The command, the route and the tool
# ---------------------------------------------------------------------------------------------


def test_an_app_s_operation_has_a_cli_command(harness: Harness) -> None:
    result = CLI(harness)("fakeapp", "tick", "--json")

    assert result.exit_code == 0, result.output
    answered = json.loads(result.stdout)
    assert isinstance(answered["count"], int)
    assert answered["at"]


def test_an_app_s_other_operation_has_a_cli_command(harness: Harness) -> None:
    result = CLI(harness)("fakeapp", "ticks", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"items": [], "next_tick": None}


async def test_an_app_s_operation_has_an_api_route(harness: Harness) -> None:
    async with api_client_for(harness) as client:
        answer = await client.post("/plugins/fakeapp/tick", json={})

    assert answer.status_code == 200, answer.text
    assert isinstance(answer.json()["count"], int)
    # (And it is in the OpenAPI document, as every plugin's operation is.)
    async with api_client_for(harness) as client:
        document = (await client.get("/openapi.json")).json()

    assert document["paths"]["/plugins/fakeapp/tick"]["post"]["operationId"] == "fakeapp_tick"


async def test_an_app_s_operation_has_an_mcp_tool(harness: Harness) -> None:
    async with mcp_client_for(harness) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    tool = tools["fakeapp_ticks"]
    assert tool.title == "List the ticks"
    assert set(tool.input_schema["properties"]) == {"page", "limit"}
    assert "fakeapp_tick" in tools


async def test_an_app_s_operation_runs_over_mcp(harness: Harness) -> None:
    async with mcp_client_for(harness) as client:
        answer = await client.call_tool("fakeapp_tick", {})

    assert not answer.is_error
    [found] = answer.content
    assert isinstance(found, TextContent)
    assert isinstance(json.loads(found.text)["count"], int)


# ---------------------------------------------------------------------------------------------
# The app itself
# ---------------------------------------------------------------------------------------------


async def test_an_app_is_listed_as_a_plugin(harness: Harness) -> None:
    async with harness.service() as service:
        listed = await service.list_plugins(ListPluginsRequest())

    [app] = [plugin for plugin in listed.plugins if plugin.name == FakeApp.name]
    assert app.kind == APPS.group
    assert app.available
    assert app.missing == []
