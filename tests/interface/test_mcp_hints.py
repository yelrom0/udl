"""
---------------------
- test_mcp_hints.py -
---------------------

An operation whose hints say it is no MCP tool (OperationHints.mcp=False) still gets its CLI
command and its API route, and nothing in the MCP server: a plugin that adds one (a merge, a
power command) is usable by the person, never by an agent
"""

# System Imports
import json
from pathlib import Path
from typing import TYPE_CHECKING

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict

# Local Imports
from support.api import client_for as api_client_for
from support.cli import CLI
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from udl.core.operations import Operation, OperationHints
from udl.plugins.registry import SITES
from udl.plugins.sites.base import SitePlugin

if TYPE_CHECKING:
    from udl.core.service import UDLService


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class MergeRequest(_Model):
    """Merge a pull request."""

    number: int
    """Its number."""


class Merged(_Model):
    number: int
    """Its number."""


async def merge(service: UDLService, request: MergeRequest) -> Merged:
    """Merge a pull request, as a person asked."""
    return Merged(number=request.number)


class Quiet(SitePlugin):
    """A plugin whose one operation is no MCP tool."""

    name = "quiet"

    @classmethod
    def operations(cls) -> list[Operation]:
        return [
            Operation.of(
                merge,
                name="merge",
                title="Merge",
                hints=OperationHints(destructive=True, open_world=True, admin=True, mcp=False),
            )
        ]


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """A harness whose plugins are Quiet's (and udl's own)."""
    found = Harness(tmp_path)
    found.registry.register(SITES, Quiet)
    return found


async def test_an_operation_without_an_mcp_tool_has_no_tool(harness: Harness) -> None:
    async with mcp_client_for(harness) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert "quiet_merge" not in names
    # (The others are all there.)
    assert "list_accounts" in names


def test_it_is_still_a_cli_command(harness: Harness) -> None:
    found = CLI(harness)("quiet", "merge", "--number", "7", "--json")

    assert found.exit_code == 0, found.output
    assert json.loads(found.stdout) == {"number": 7}


async def test_it_is_still_an_api_route(harness: Harness) -> None:
    async with api_client_for(harness) as client:
        answer = await client.post("/plugins/quiet/merge", json={"number": 7})

    assert answer.status_code == 200, answer.text
    assert answer.json() == {"number": 7}
