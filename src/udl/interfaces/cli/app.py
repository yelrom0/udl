"""
----------
- app.py -
----------

The `udl` command (Typer): a command for every operation (planning/01-core.md's Feature Matrix),
with `udl <url>...` short for `udl get <url>...`, plus `udl serve` and `udl mcp` (the other
interfaces) and `udl browser login`. The operations plugins add are commands too: `udl <plugin>
<op>` (udl.interfaces.adapters makes them).
"""

# System Imports
import importlib.metadata
from pathlib import Path
from typing import Annotated, Any

# Package Imports
import typer
from typer.core import TyperGroup, TyperOption

# Local Imports
from udl.config import Settings
from udl.core.operations import OperationRegistry, operations, valid_name
from udl.core.service import UDLService
from udl.errors import ConfigError
from udl.interfaces.adapters import add_commands, cli_groups
from udl.interfaces.cli.commands import (
    accounts,
    automations,
    browser,
    chains,
    downloads,
    probe,
    servers,
    sites,
    system,
)
from udl.interfaces.cli.common import EXIT_USAGE, Runtime, fail
from udl.log import configure_logging


class DefaultGetGroup(TyperGroup):
    """The udl group: when the first argument after udl's own options is not a command's
    name (udl's own, or a plugin's group of them), the arguments are `get`'s (`udl <url>...` is
    `udl get <url>...`)."""

    # (`ctx` is Typer's own Click's Context, which Typer does not export.)
    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        at = self._own_options_end(ctx, args)
        if at < len(args) and args[at] not in self.commands and args[at].startswith("-"):
            args = [*args[:at], "get", *args[at:]]  # (one of get's options, then the URLs)
        return super().parse_args(ctx, args)

    def resolve_command(self, ctx: Any, args: list[str]) -> tuple[str | None, Any, list[str]]:
        if args and self.get_command(ctx, args[0]) is None:
            return "get", self.commands["get"], args
        return super().resolve_command(ctx, args)

    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        found = super().get_command(ctx, cmd_name)
        if found is None and valid_name(cmd_name):  # (not a URL, which has a dot)
            found = _plugin_groups(ctx).get(cmd_name)
        return found

    def list_commands(self, ctx: Any) -> list[str]:
        return [*super().list_commands(ctx), *sorted(_plugin_groups(ctx))]

    def _own_options_end(self, ctx: Any, args: list[str]) -> int:
        """Where udl's own options (such as -v or --config FILE) end in `args`."""
        takes_value = {
            name: not (param.is_flag or param.count)
            for param in self.get_params(ctx)
            if isinstance(param, TyperOption)
            for name in (*param.opts, *param.secondary_opts)
        }
        at = 0
        while at < len(args):
            arg = args[at]
            name, equals, _ = arg.partition("=")
            if name in takes_value:
                at += 2 if takes_value[name] and not equals else 1
            elif (
                arg.startswith("-")
                and not arg.startswith("--")
                and len(arg) > 2
                and all(takes_value.get(f"-{flag}") is False for flag in arg[1:])
            ):  # flags together, as in -vv
                at += 1
            else:
                break
        return at


def _plugin_groups(ctx: Any) -> dict[str, TyperGroup]:
    """The command groups of the plugins that add operations (once a command, from the
    settings and the services it runs with)."""
    root = ctx.find_root()
    if _PLUGIN_GROUPS not in root.meta:
        root.meta[_PLUGIN_GROUPS] = cli_groups(_operations(root))
    groups: dict[str, TyperGroup] = root.meta[_PLUGIN_GROUPS]
    return groups


def _operations(ctx: Any) -> OperationRegistry:
    """The operations of a service made as the command's will be (not started). (Asked for
    before the udl group's callback runs, which loads the settings for the command.)"""
    try:
        settings = Settings.load(ctx.params.get("config"))
    except ConfigError:
        return OperationRegistry()  # (the callback says what is wrong with the config)
    make_service = ctx.obj if ctx.obj is not None else UDLService
    return make_service(settings).operations


_PLUGIN_GROUPS = "udl.plugin_groups"


app = typer.Typer(
    cls=DefaultGetGroup,
    name="udl",
    no_args_is_help=True,
    rich_markup_mode="markdown",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _show_version(show: bool) -> None:
    if show:
        typer.echo(f"udl {importlib.metadata.version('udl')}")
        raise typer.Exit


@app.callback()
def udl(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            help="The config file (by default $UDL_CONFIG, else ~/.config/udl/config.toml).",
            metavar="FILE",
        ),
    ] = None,
    verbose: Annotated[
        int,
        typer.Option(
            "-v",
            "--verbose",
            count=True,
            show_default=False,
            help="Log more: -v what udl does, -vv its details, -vvv every library's.",
        ),
    ] = 0,
    quiet: Annotated[bool, typer.Option("-q", "--quiet", help="Log errors only.")] = False,
    version: Annotated[
        bool,
        typer.Option("--version", callback=_show_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    """Download anything: udl works out how to download each URL, and remembers what worked.

    It tries yt-dlp, gallery-dl, a plain HTTP download, the page's HTML and a browser, and
    remembers what worked for the site. `udl <url>...` downloads the URLs (it is `udl get`)."""
    verbosity = -1 if quiet else verbose
    configure_logging(verbosity)
    try:
        settings = Settings.load(config)
    except ConfigError as e:
        fail(e, EXIT_USAGE)
    make_service = ctx.obj if ctx.obj is not None else UDLService  # (tests pass their own)
    ctx.obj = Runtime(settings=settings, make_service=make_service, verbosity=verbosity)


app.command("get")(downloads.get)
app.command("probe")(probe.probe)
app.command("history")(downloads.history)
app.add_typer(downloads.jobs, name="jobs")
app.add_typer(sites.app, name="sites")
app.add_typer(chains.app, name="chain")
app.add_typer(system.db, name="db")
app.add_typer(system.plugins, name="plugins")
app.add_typer(system.config, name="config")
app.command("serve")(servers.serve)
app.command("mcp")(servers.mcp)
app.add_typer(browser.app, name="browser")
app.add_typer(accounts.app, name="accounts")
# (The automations': events, subs, actions, runs, schedules and status; and the accounts'.)
add_commands(
    app,
    operations().values(),
    renderers=automations.RENDERERS,
    helps=automations.HELPS,
    groups={("accounts",): accounts.app},
)


def main() -> None:
    """The `udl` script."""
    app()
