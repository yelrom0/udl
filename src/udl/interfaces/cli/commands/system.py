"""
-------------
- system.py -
-------------

udl db list, udl plugins list, udl config show: udl itself
"""

# Package Imports
import typer

# Local Imports
from udl.core.models import GetConfigRequest, ListDatabasesRequest, ListPluginsRequest
from udl.interfaces.cli import render
from udl.interfaces.cli.common import JsonOption, run, runs, show

db = typer.Typer(
    help="The databases udl keeps what it learns in.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
plugins = typer.Typer(
    help="udl's plugins: databases and downloaders.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
config = typer.Typer(help="udl's settings.", no_args_is_help=True, rich_markup_mode="markdown")


@db.command("list")
@runs("list_databases")
def list_databases(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """List the database connections, and whether each is answering.

    They are listed in the order udl consults them."""
    request = ListDatabasesRequest()
    show(run(ctx, lambda service: service.list_databases(request)), as_json, render.databases)


@plugins.command("list")
@runs("list_plugins")
def list_plugins(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """List the plugins, and whether each downloader can run.

    A downloader that cannot run says what to install."""
    request = ListPluginsRequest()
    show(run(ctx, lambda service: service.list_plugins(request)), as_json, render.plugins)


@config.command("show")
@runs("get_config")
def show_config(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """Show the settings udl runs with, secrets hidden.

    They come from its config file, UDL_* environment variables and the defaults."""
    request = GetConfigRequest()
    show(run(ctx, lambda service: service.get_config(request)), as_json, render.config)
