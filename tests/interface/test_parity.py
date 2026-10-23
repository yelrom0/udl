"""
------------------
- test_parity.py -
------------------

Every operation (udl.core.service.operations()) has a CLI command, an API route and an MCP tool,
named as planning.md's Feature Matrix names them, and the interfaces offer nothing else except
the interface-only commands listed here
"""

# System Imports
import inspect
from collections.abc import Iterator
from typing import Any

# Package Imports
from typer.core import TyperGroup

# Local Imports
from udl.config import Settings
from udl.core.service import UDLService, operations

# planning.md's Feature Matrix: each operation's CLI command and API route (its MCP tool is
# named after the operation).
MATRIX: dict[str, tuple[str, str]] = {
    "download": ("get", "POST /downloads"),
    "get_download": ("jobs show", "GET /downloads/{id}"),
    "list_downloads": ("history", "GET /downloads"),
    "cancel_download": ("jobs cancel", "DELETE /downloads/{id}"),
    "probe_url": ("probe", "POST /probe"),
    "list_sites": ("sites list", "GET /sites"),
    "get_site": ("sites show", "GET /sites/{key}"),
    "set_site_strategy": ("sites set", "PUT /sites/{key}/strategies"),
    "delete_site": ("sites rm", "DELETE /sites/{key}"),
    "list_databases": ("db list", "GET /databases"),
    "list_plugins": ("plugins list", "GET /plugins"),
    "get_config": ("config show", "GET /config"),
}

# Interface-only, so exempt: they run the other interfaces, or need a browser on the user's
# machine.
CLI_ONLY = {"serve", "mcp", "browser login"}
# A job's events as they happen (SSE): UDLService.events() is a stream, not an operation.
API_ONLY = {"GET /downloads/{id}/events": "download_events"}


def test_the_matrix_lists_every_operation() -> None:
    assert set(MATRIX) == set(operations())


# ---------------------------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------------------------


def _commands(command: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[str, Any]]:
    # (Typer's commands are its own Click's, which it does not export.)
    if isinstance(command, TyperGroup):
        for name, sub in command.commands.items():
            yield from _commands(sub, (*path, name))
    else:
        yield " ".join(path), command


def test_every_operation_has_its_cli_command() -> None:
    import typer.main

    from udl.interfaces.cli.app import app
    from udl.interfaces.cli.common import operation_of

    found = {
        path: operation_of(inspect.unwrap(command.callback)) if command.callback else None
        for path, command in _commands(typer.main.get_command(app))
    }

    assert {path: op for path, op in found.items() if op} == {
        cli: op for op, (cli, _) in MATRIX.items()
    }
    assert {path for path, op in found.items() if op is None} == CLI_ONLY


# ---------------------------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------------------------


def test_every_operation_has_its_api_route() -> None:
    from udl.interfaces.api.app import create_app

    # (The documented routes, from the OpenAPI schema: its operationIds are the operations'.)
    paths = create_app(Settings()).openapi()["paths"]
    found = {
        f"{method.upper()} {path}": details["operationId"]
        for path, methods in paths.items()
        for method, details in methods.items()
    }

    assert found == {api: op for op, (_, api) in MATRIX.items()} | API_ONLY


# ---------------------------------------------------------------------------------------------
# The MCP server
# ---------------------------------------------------------------------------------------------


async def test_every_operation_has_its_mcp_tool() -> None:
    from udl.interfaces.mcp.server import create_server

    def no_service() -> UDLService:
        raise AssertionError("listing the tools needs no service")

    server = create_server(no_service)

    assert {tool.name for tool in await server.list_tools()} == set(operations())
