"""
--------------
- servers.py -
--------------

udl serve and udl mcp: the other interfaces, served from the command line
"""

# System Imports
from typing import Annotated

# Package Imports
import typer

# Local Imports
from udl.interfaces.cli.common import Runtime, run


def serve(
    ctx: typer.Context,
    host: Annotated[
        str | None,
        typer.Option(
            help="The address to listen on (by default api.host: 127.0.0.1, this machine only)."
        ),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option(min=1, max=65535, help="The port to listen on (by default api.port: 8765)."),
    ] = None,
) -> None:
    """Serve udl's HTTP API, with its MCP server at /mcp, until stopped (Ctrl-C).

    The API's docs are at /docs. To serve other machines, set an api.api_key (sent in the
    X-API-Key header) and allowed_output_roots."""
    # (Imported here: the other commands start faster without the API.)
    from udl.interfaces.api.serve import serve as serve_api

    runtime = Runtime.of(ctx)
    chosen = {"host": host, "port": port}
    api = runtime.settings.api.model_copy(
        update={name: value for name, value in chosen.items() if value is not None}
    )
    serve_api(runtime.settings.model_copy(update={"api": api}), verbosity=runtime.verbosity)


def mcp(ctx: typer.Context) -> None:
    """Serve udl's MCP server on stdin and stdout, for an AI agent to run.

    An agent's MCP config runs it as `{"command": "udl", "args": ["mcp"]}`. Its logs go to
    stderr."""
    # (Imported here: the other commands start faster without the MCP SDK.)
    from udl.interfaces.mcp.server import create_server

    run(ctx, lambda service: create_server(lambda: service).run_stdio_async())
