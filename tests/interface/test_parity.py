"""
------------------
- test_parity.py -
------------------

Every operation (udl.core.service.operations()) has a CLI command, an API route and an MCP tool,
named as planning/01-core.md's Feature Matrix names them, and the interfaces offer nothing else
except the interface-only commands listed here
"""

# System Imports
import inspect
from collections.abc import Iterator
from typing import Any

# Package Imports
from typer.core import TyperGroup

# Local Imports
from udl.config import Settings
from udl.core.operations import operations
from udl.core.service import UDLService

# The Feature Matrices of planning/01-core.md and planning.md: each operation's CLI command and API
# route (its MCP tool is named after the operation).
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
    "suggest_chain": ("chain suggest", "POST /chains/suggest"),
    "test_chain": ("chain test", "POST /chains/test"),
    "set_site_chain": ("chain set", "PUT /sites/{key}/chain"),
    "import_chains": ("chain import", "POST /chains/import"),
    "export_chain": ("chain export", "GET /sites/{key}/chain"),
    "list_databases": ("db list", "GET /databases"),
    "sync_databases": ("db sync", "POST /databases/sync"),
    "list_plugins": ("plugins list", "GET /plugins"),
    "get_plugin_settings": ("plugins settings", "GET /plugins/{name}/settings"),
    "set_plugin_settings": ("plugins configure", "PUT /plugins/{name}/settings"),
    "get_config": ("config show", "GET /config"),
    "list_event_types": ("events types", "GET /event-types"),
    "list_events": ("events list", "GET /events"),
    "get_event": ("events show", "GET /events/{id}"),
    "emit_event": ("events emit", "POST /events"),
    "replay_event": ("events replay", "POST /events/{id}/replay"),
    "list_subscriptions": ("subs list", "GET /subscriptions"),
    "get_subscription": ("subs show", "GET /subscriptions/{name}"),
    "set_subscription": ("subs set", "PUT /subscriptions/{name}"),
    "test_subscription": ("subs test", "POST /subscriptions/test"),
    "delete_subscription": ("subs rm", "DELETE /subscriptions/{name}"),
    "list_actions": ("actions list", "GET /actions"),
    "get_action": ("actions show", "GET /actions/{name}"),
    "set_action": ("actions set", "PUT /actions/{name}"),
    "run_action": ("actions run", "POST /actions/{name}/run"),
    "delete_action": ("actions rm", "DELETE /actions/{name}"),
    "list_scripts": ("actions scripts", "GET /actions/scripts"),
    "list_runs": ("runs list", "GET /runs"),
    "get_run": ("runs show", "GET /runs/{id}"),
    "cancel_run": ("runs cancel", "DELETE /runs/{id}"),
    "get_status": ("status", "GET /status"),
    "list_schedules": ("schedules list", "GET /schedules"),
    "get_schedule": ("schedules show", "GET /schedules/{name}"),
    "set_schedule": ("schedules set", "PUT /schedules/{name}"),
    "run_schedule": ("schedules run", "POST /schedules/{name}/run"),
    "preview_schedule": ("schedules preview", "POST /schedules/preview"),
    "delete_schedule": ("schedules rm", "DELETE /schedules/{name}"),
    "list_accounts": ("accounts list", "GET /accounts"),
    "get_account": ("accounts show", "GET /accounts/{account}"),
    "connect_account": ("accounts add", "POST /accounts"),
    "set_account": ("accounts set", "PATCH /accounts/{account}"),
    "check_account": ("accounts check", "POST /accounts/{account}/check"),
    "disconnect_account": ("accounts rm", "DELETE /accounts/{account}"),
}

# Interface-only, so exempt: they run the other interfaces, need a browser on the user's
# machine, or ask the user (the wizard, which runs suggest_chain, test_chain and set_site_chain).
CLI_ONLY = {"serve", "mcp", "browser login", "chain wizard"}
# A job's events, and the events and runs, as they happen (SSE): streams, not operations.
API_ONLY = {
    "GET /downloads/{id}/events": "download_events",
    "GET /events/stream": "events_stream",
}


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
