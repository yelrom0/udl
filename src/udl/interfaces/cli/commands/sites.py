"""
------------
- sites.py -
------------

udl sites list / show / set / rm
"""

# System Imports
from typing import Annotated

# Package Imports
import typer
from rich.console import Console

# Local Imports
from udl.core.models import Deleted, SetStrategyRequest, SiteRequest
from udl.db.models import SiteQuery
from udl.interfaces.cli import render
from udl.interfaces.cli.common import EXIT_FAILED, JsonOption, key_values, parse, run, runs, show

app = typer.Typer(
    help="What udl knows about sites, and which downloaders to use on them.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)

Site = Annotated[str, typer.Argument(help="The site: its key (example.com), a host or a URL.")]


@app.command("list")
@runs("list_sites")
def list_sites(
    ctx: typer.Context,
    search: Annotated[
        str | None, typer.Option(help="Only the sites whose key or name has this in it.")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("-n", "--limit", min=1, help="Show at most N.", metavar="N")
    ] = None,
    offset: Annotated[int, typer.Option(min=0, help="Skip the first N.", metavar="N")] = 0,
    as_json: JsonOption = False,
) -> None:
    """List the sites udl knows: those it has downloaded from, or has a strategy for."""
    query = parse(SiteQuery, search=search, limit=limit, offset=offset)
    show(run(ctx, lambda service: service.list_sites(query)), as_json, render.sites)


@app.command("show")
@runs("get_site")
def show_site(ctx: typer.Context, site: Site, as_json: JsonOption = False) -> None:
    """Show what udl knows about a site.

    Its strategies (the downloaders to use on it), with how often each has worked and failed."""
    request = parse(SiteRequest, site=site)
    show(run(ctx, lambda service: service.get_site(request)), as_json, render.site)


@app.command("set")
@runs("set_site_strategy")
def set_strategy(
    ctx: typer.Context,
    site: Site,
    tool: Annotated[str, typer.Option(help="The downloader to use (udl plugins list lists them).")],
    pattern: Annotated[
        str | None,
        typer.Option(
            help="Use it only for URLs whose path this regex is found in, e.g. '^/shorts/'.",
            metavar="REGEX",
        ),
    ] = None,
    option: Annotated[
        list[str] | None,
        typer.Option(
            "-O",
            "--option",
            help="One of the downloader's options, e.g. -O format=best (JSON values allowed).",
            metavar="KEY=VALUE",
        ),
    ] = None,
    priority: Annotated[int, typer.Option(help="Lower is tried first.")] = 0,
    requires_auth: Annotated[
        bool, typer.Option("--requires-auth", help="Send the site's cookies from the first try.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Tell udl which downloader to use for a site (or for some of its URLs).

    udl tries it before what it has learned, and never replaces it when it fails. Setting the
    same tool and pattern again changes that strategy."""
    request = parse(
        SetStrategyRequest,
        site=site,
        tool=tool,
        pattern=pattern,
        options=key_values(option or []),
        priority=priority,
        requires_auth=requires_auth,
    )
    show(run(ctx, lambda service: service.set_site_strategy(request)), as_json, render.strategy)


@app.command("rm")
@runs("delete_site")
def delete_site(ctx: typer.Context, site: Site, as_json: JsonOption = False) -> None:
    """Forget a site and its strategies.

    Its downloads stay in the history. Exits with 1 when udl knew nothing about it."""
    request = parse(SiteRequest, site=site)

    def forgot(console: Console, deleted: Deleted) -> None:
        if deleted.deleted:
            console.print(f"Forgot {request.site}.")
        else:
            console.print(f"udl knew nothing about {request.site}.")

    deleted = run(ctx, lambda service: service.delete_site(request))
    show(deleted, as_json, forgot)
    if not deleted.deleted:
        raise typer.Exit(EXIT_FAILED)
