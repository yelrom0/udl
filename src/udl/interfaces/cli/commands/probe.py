"""
------------
- probe.py -
------------

udl probe
"""

# System Imports
from typing import Annotated

# Package Imports
import typer

# Local Imports
from udl.core.models import ProbeRequest
from udl.interfaces.cli import render
from udl.interfaces.cli.common import JsonOption, parse, run, runs, show


@runs("probe_url")
def probe(
    ctx: typer.Context,
    url: Annotated[str, typer.Argument(help="The URL.")],
    tool: Annotated[str | None, typer.Option(help="Probe only this downloader.")] = None,
    all_tools: Annotated[
        bool, typer.Option("--all", help="Probe every downloader, not just until one can.")
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Find out which downloaders can download a URL, without downloading it.

    The downloaders are probed in the order a download would try them, until one can (or all
    of them, with --all), and each says what media it sees."""
    request = parse(ProbeRequest, url=url, tool=tool, all_tools=all_tools)
    show(run(ctx, lambda service: service.probe(request)), as_json, render.probe)
