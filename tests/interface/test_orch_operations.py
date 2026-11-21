"""
---------------------------
- test_orch_operations.py -
---------------------------

26.6's catalogue through the interfaces udl makes for every app's operations: each operation's
CLI command (the card's own path), its API route (`POST /plugins/orch/<op>`) and its MCP tool
(`orch_<op>`), and each of them run for real by the owner over all three.

The harness's in-memory databases keep what they hold from one service to the next, so a project
is set through one service and read through another (each CLI run makes its own).
"""

# System Imports
import inspect
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Final

# Package Imports
import pytest
from mcp.types import TextContent
from typer.core import TyperCommand, TyperGroup

# Local Imports
from support.api import client_for as api_client_for
from support.cli import CLI
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from udl.interfaces.adapters import cli_groups
from udl.interfaces.cli.common import operation_of
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.registry import APPS

CATALOGUE: Final[dict[str, tuple[str, ...]]] = {
    "orch_list_projects": ("orch", "projects"),
    "orch_get_project": ("orch", "projects", "show"),
    "orch_set_project": ("orch", "projects", "set"),
    "orch_delete_project": ("orch", "projects", "rm"),
    "orch_set_repo": ("orch", "repos", "set"),
    "orch_delete_repo": ("orch", "repos", "rm"),
    "orch_list_features": ("orch", "features"),
    "orch_get_feature": ("orch", "features", "show"),
    "orch_set_feature": ("orch", "features", "set"),
    "orch_list_tasks": ("orch", "tasks"),
    "orch_get_task": ("orch", "tasks", "show"),
    "orch_set_task": ("orch", "tasks", "set"),
    "orch_move_task": ("orch", "tasks", "move"),
    "orch_split_task": ("orch", "tasks", "split"),
    "orch_add_link": ("orch", "links", "add"),
    "orch_remove_link": ("orch", "links", "rm"),
    "orch_find_links": ("orch", "links", "find"),
    "orch_add_note": ("orch", "note"),
    "orch_import_plan": ("orch", "import"),
}
"""The card's CLI command for each of the catalogue's operations (26.6)."""


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """A udl on the fakes, with orch, and one project in it."""
    found = Harness(tmp_path)
    found.registry.register(APPS, Orch)
    return found


@pytest.fixture
async def with_a_project(harness: Harness) -> AsyncIterator[Harness]:
    """The harness, with the project every read of the catalogue is of, and a feature of it."""
    async with harness.service() as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await service.call("orch_set_feature", {"key": "26", "title": "Work tracking"})
    yield harness  # noqa: PT022 - (the service closes itself: there is nothing to undo)


# ---------------------------------------------------------------------------------------------
# The command, the route and the tool, one operation at a time
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "path"), sorted(CATALOGUE.items()))
async def test_the_cli_command_is_the_card_s(
    harness: Harness, name: str, path: tuple[str, ...]
) -> None:
    # (The groups are the ones the CLI makes for the operations a service has: `udl orch` is one
    # of its plugins' groups, built when a command runs.)
    async with harness.service() as service:
        groups = cli_groups(service.operations)

    command: Any = groups["orch"]
    for part in path[1:]:
        assert isinstance(command, TyperGroup), f"{' '.join(path)} is not a group"
        command = command.commands[part]

    assert isinstance(command, TyperGroup | TyperCommand)
    assert operation_of(inspect.unwrap(command.callback or print)) == name


@pytest.mark.parametrize("name", sorted(CATALOGUE))
async def test_the_api_route_is_there(harness: Harness, name: str) -> None:
    short = name.removeprefix("orch_")

    async with api_client_for(harness) as client:
        document = (await client.get("/openapi.json")).json()

    route = document["paths"][f"/plugins/orch/{short}"]["post"]
    assert route["operationId"] == name


@pytest.mark.parametrize("name", sorted(CATALOGUE))
async def test_the_mcp_tool_is_there(harness: Harness, name: str) -> None:
    async with mcp_client_for(harness) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}

    assert name in tools


# ---------------------------------------------------------------------------------------------
# Run for real, by the owner, over each interface
# ---------------------------------------------------------------------------------------------


def test_the_owner_reads_the_catalogue_over_the_cli(with_a_project: Harness) -> None:
    result = CLI(with_a_project)("orch", "projects", "--json")

    assert result.exit_code == 0, result.output
    assert [project["name"] for project in json.loads(result.stdout)["projects"]] == ["udl"]


def test_the_owner_sets_a_task_over_the_cli(with_a_project: Harness) -> None:
    result = CLI(with_a_project)(
        "orch", "tasks", "set", "--feature", "26", "--title", "Projects and repos", "--json"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["key"] == "26.1"


def test_the_owner_moves_a_task_by_its_key_over_the_cli(with_a_project: Harness) -> None:
    # (A key is text: `26.1` read as JSON would be the float 26.1, and the request would refuse
    # it. The same for a feature key of `26`.)
    made = CLI(with_a_project)(
        "orch", "tasks", "set", "--feature", "26", "--title", "One", "--json"
    )
    assert made.exit_code == 0, made.output

    result = CLI(with_a_project)("orch", "tasks", "move", "26.1", "ready", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["state"] == "ready"


async def test_the_owner_reads_the_catalogue_over_the_api(with_a_project: Harness) -> None:
    async with api_client_for(with_a_project) as client:
        answer = await client.post("/plugins/orch/list_projects", json={})

    assert answer.status_code == 200, answer.text
    assert [project["name"] for project in answer.json()["projects"]] == ["udl"]


async def test_a_move_over_the_api_says_who_made_it(with_a_project: Harness) -> None:
    # (The server's owner is a person, so the system note the move writes is `human:owner`'s:
    # `_by`'s owner branch, through the interface that sets it.)
    async with api_client_for(with_a_project) as client:
        await client.post(
            "/plugins/orch/set_task", json={"key": "26.1", "feature": "26", "title": "One"}
        )
        moved = await client.post("/plugins/orch/move_task", json={"key": "26.1", "to": "ready"})
        found = await client.post("/plugins/orch/get_task", json={"key": "26.1"})

    assert moved.status_code == 200, moved.text
    assert [
        (note["note_kind"], note["text"], note["author"]) for note in found.json()["notes"]
    ] == [("system", "open → ready, by human:owner.", "udl")]


async def test_the_owner_sets_a_project_over_the_api(harness: Harness) -> None:
    async with api_client_for(harness) as client:
        answer = await client.post(
            "/plugins/orch/set_project", json={"name": "udl", "title": "udl"}
        )

    assert answer.status_code == 200, answer.text
    assert answer.json()["title"] == "udl"


async def test_the_owner_reads_the_catalogue_over_mcp(with_a_project: Harness) -> None:
    async with mcp_client_for(with_a_project) as client:
        answer = await client.call_tool("orch_list_projects", {})

    assert not answer.is_error
    [found] = answer.content
    assert isinstance(found, TextContent)
    assert [project["name"] for project in json.loads(found.text)["projects"]] == ["udl"]
