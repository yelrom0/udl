"""
-------------
- system.py -
-------------

udl db list, udl db sync, udl plugins list / settings / configure, udl config show: udl itself
"""

# System Imports
from typing import Annotated

# Package Imports
import typer

# Local Imports
from udl.core.models import (
    GetConfigRequest,
    ListDatabasesRequest,
    ListPluginsRequest,
    PluginSettingsRequest,
    SetPluginSettingsRequest,
    SyncDatabasesRequest,
)
from udl.interfaces.cli import render
from udl.interfaces.cli.common import JsonOption, key_values, parse, run, runs, show

db = typer.Typer(
    help="The databases udl keeps what it learns in.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)
plugins = typer.Typer(
    help="udl's plugins: databases, downloaders, site plugins, secret stores and kinds of "
    "action, and their settings.",
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


@db.command("sync")
@runs("sync_databases")
def sync_databases(
    ctx: typer.Context,
    source: Annotated[
        str,
        typer.Option(
            "--from",
            help="The connection to copy from (`udl db list` names them).",
            show_default=False,
        ),
    ],
    targets: Annotated[
        list[str],
        typer.Option(
            "--to", help="A connection to copy into (repeat for more).", show_default=False
        ),
    ],
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only count what would change.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Copy what one database knows into others: sites, strategies and finished downloads.

    What a target lacks is added; what it has differently is replaced by the source's copy, so
    sync from the database that is right. Nothing is deleted, and syncing again changes
    nothing."""
    request = parse(SyncDatabasesRequest, source=source, targets=targets, dry_run=dry_run)
    show(run(ctx, lambda service: service.sync_databases(request)), as_json, render.synced)


@plugins.command("list")
@runs("list_plugins")
def list_plugins(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """List the plugins, and whether each can run.

    One that cannot run says what to install. Local plugins are single .py files in the plugin
    dirs."""
    request = ListPluginsRequest()
    show(run(ctx, lambda service: service.list_plugins(request)), as_json, render.plugins)


Plugin = Annotated[str, typer.Argument(help="The plugin (`udl plugins list` names them).")]


@plugins.command("settings")
@runs("get_plugin_settings")
def plugin_settings(ctx: typer.Context, plugin: Plugin, as_json: JsonOption = False) -> None:
    """Show a plugin's settings, and where each comes from.

    A plugin's settings are its defaults, then its [integrations.<name>] table in the config, then
    what `udl plugins configure` set. Secrets are hidden."""
    request = parse(PluginSettingsRequest, name=plugin)
    show(run(ctx, lambda service: service.get_plugin_settings(request)), as_json, render.settings)


@plugins.command("configure")
@runs("set_plugin_settings")
def configure_plugin(
    ctx: typer.Context,
    plugin: Plugin,
    option: Annotated[
        list[str] | None,
        typer.Option(
            "-O",
            "--option",
            help="Set a setting, e.g. -O poll_interval=30s (JSON values allowed).",
            metavar="KEY=VALUE",
        ),
    ] = None,
    reset: Annotated[
        list[str] | None,
        typer.Option(
            help="Give a setting back to the config file (or its default).", metavar="KEY"
        ),
    ] = None,
    secret: Annotated[
        list[str] | None,
        typer.Option(
            help="Set a secret: its value is asked for, hidden, and kept in udl's secret store.",
            metavar="KEY",
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Change a plugin's settings, over its [integrations.<name>] table in the config.

    Settings that widen what udl reaches (such as folders it sends files from) are set in the
    config file only."""
    values = key_values(option or [])
    for key in secret or []:
        values[key] = typer.prompt(key, hide_input=True, err=True)
    request = parse(SetPluginSettingsRequest, name=plugin, values=values, reset=reset or [])
    show(run(ctx, lambda service: service.set_plugin_settings(request)), as_json, render.settings)


@config.command("show")
@runs("get_config")
def show_config(ctx: typer.Context, as_json: JsonOption = False) -> None:
    """Show the settings udl runs with, secrets hidden.

    They come from its config file, UDL_* environment variables and the defaults."""
    request = GetConfigRequest()
    show(run(ctx, lambda service: service.get_config(request)), as_json, render.config)
