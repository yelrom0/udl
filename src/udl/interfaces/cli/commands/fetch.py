"""
------------
- fetch.py -
------------

udl fetch: reading pages (`udl fetch <url>...`, which is `udl fetch get <url>...`), showing each
URL's progress on stderr, and printing one page to stdout or writing each to --out DIR; and
`udl fetch solve`, which shows the checks that wait in a window, saying each site as it passes.
The fetch group's other commands (list, show, page, cancel, retry, challenges) are made from
their operations (udl.interfaces.adapters).
"""

# System Imports
import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Final
from urllib.parse import urlsplit

# Package Imports
import typer
from rich.markup import escape
from typer.core import TyperGroup

# Local Imports
from udl.config import PersonMode
from udl.core.service import UDLService
from udl.fetch.content import PageFormat
from udl.fetch.ladder import Via
from udl.fetch.models import (
    FetchBatchInfo,
    FetchItem,
    FetchRequest,
    ItemStatus,
    SolveChallengesRequest,
    SolveReport,
)
from udl.interfaces.cli import render
from udl.interfaces.cli.common import (
    EXIT_FAILED,
    JsonOption,
    Runtime,
    parse,
    run,
    runs,
    show,
    stderr,
    stdout,
    usage_error,
)

RENDERERS: Final = {
    "list_fetches": render.fetch_batches,
    "get_fetch": render.fetch_batch,
    "get_fetched_page": render.fetched_page,
    "cancel_fetch": render.fetch_batch,
    "retry_fetch": render.fetch_batch,
    "list_challenges": render.challenges,
    "list_cookies": render.cookies,
    "delete_cookies": render.deleted,
}
"""How the fetch group's (and the cookies') made commands show their responses, by operation."""

_LOOK_EVERY: Final = 0.25  # (seconds between looks at a fetch's progress)
_EXTENSIONS: Final = {PageFormat.MARKDOWN: "md", PageFormat.TEXT: "txt", PageFormat.HTML: "html"}
_RAW_EXTENSIONS: Final = {
    "text/html": "html",
    "application/xhtml+xml": "html",
    "application/json": "json",
    "text/plain": "txt",
    "application/pdf": "pdf",
    "application/xml": "xml",
    "text/xml": "xml",
    "text/csv": "csv",
}
_UNSAFE: Final = re.compile(r"[^A-Za-z0-9._-]+")


class FetchGroup(TyperGroup):
    """The fetch group: when its first argument is not one of its commands, the arguments are
    `get`'s (`udl fetch <url>...` is `udl fetch get <url>...`)."""

    # (`ctx` is Typer's own Click's Context, which Typer does not export.)
    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        if args and args[0] not in self.commands and args[0] not in ctx.help_option_names:
            args = ["get", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=FetchGroup,
    help="Read web pages a plain fetch cannot (a 5xx, a 403, an anti-bot check, a page built by "
    "JavaScript or behind a login), in batches: `udl fetch <url>...`.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)


def _read_urls(file: typer.FileText) -> list[str]:
    lines = (line.strip() for line in file)
    return [line for line in lines if line and not line.startswith("#")]


def _headers(given: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for header in given:
        name, colon, value = header.partition(":")
        if not colon or not name.strip():
            raise typer.BadParameter(f"{header!r} is not NAME: VALUE", param_hint="'-H'")
        headers[name.strip()] = value.strip()
    return headers


@app.command("get")
@runs("fetch_pages")
def get(
    ctx: typer.Context,
    urls: Annotated[list[str] | None, typer.Argument(help="The URLs.", show_default=False)] = None,
    input_file: Annotated[
        typer.FileText | None,
        typer.Option("-i", "--input", help="Also read the URLs in FILE, one a line (- for stdin)."),
    ] = None,
    via: Annotated[
        Via,
        typer.Option(
            help="Where to start: auto (where the site's pages last worked), static, "
            "cookies or browser."
        ),
    ] = Via.AUTO,
    format_: Annotated[
        PageFormat,
        typer.Option("-F", "--format", help="markdown, text, html, or raw (as served)."),
    ] = PageFormat.MARKDOWN,
    whole: Annotated[
        bool,
        typer.Option("--whole", help="The whole page, not only its main content."),
    ] = False,
    select: Annotated[
        str | None, typer.Option(help="Only what this CSS selector matches on each page.")
    ] = None,
    wait_for: Annotated[
        str | None,
        typer.Option(help="In the browser, wait for an element matching this CSS selector."),
    ] = None,
    header: Annotated[
        list[str] | None,
        typer.Option(
            "-H",
            "--header",
            help="Send this header too, 'NAME: VALUE' (repeat for more).",
            show_default=False,
        ),
    ] = None,
    person: Annotated[
        PersonMode | None,
        typer.Option(
            help="At a check udl cannot pass: ask (a window opens for you), wait (for "
            "`udl fetch solve`), or fail. By default [fetch] person.",
            show_default=False,
        ),
    ] = None,
    label: Annotated[str | None, typer.Option(help="A name for the fetch.")] = None,
    timeout: Annotated[
        float | None,
        typer.Option(help="Wait at most this many seconds (by default, to the end).", min=0),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--out",
            help="Write each page to DIR/<site>/<name> (raw keeps the bytes as served).",
            metavar="DIR",
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Read pages that a plain fetch cannot: udl climbs from a plain request, to the site's
    cookies, to its browser, and asks you to pass an anti-bot check when one stops it.

    One page is printed; several are listed (or written to --out DIR). It exits with 1 when
    any URL was not read."""
    runtime = Runtime.of(ctx)
    listed = [*(urls or []), *(_read_urls(input_file) if input_file else [])]
    if not listed:
        usage_error("No URLs to read: name some, or a file of them (-i FILE).")
    request = parse(
        FetchRequest,
        urls=listed,
        via=via,
        format=format_,
        main_only=not whole,
        select=select,
        wait_for=wait_for,
        headers=_headers(header or []),
        person=person,
        label=label,
        wait=False,
    )
    told = not as_json and runtime.verbosity >= 0

    async def work(service: UDLService) -> tuple[FetchBatchInfo, list[tuple[FetchItem, bytes]]]:
        started = await service.fetch_pages(request)
        queue = service.fetches.queue
        seen: dict[object, ItemStatus] = {}
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            batch = await queue.batch(started.id)
            items = await queue.items(started.id)
            for item in items:
                if (
                    told
                    and item.status is not ItemStatus.QUEUED
                    and seen.get(item.id) != item.status
                ):
                    stderr().print(f"{render.item_status(item.status)} {escape(item.url)}")
                seen[item.id] = item.status
            if batch.status.ended or (deadline is not None and time.monotonic() >= deadline):
                break
            await asyncio.sleep(_LOOK_EVERY)
        pages: list[tuple[FetchItem, bytes]] = []
        for item in items:
            if item.status is not ItemStatus.SUCCEEDED:
                continue
            if format_ is PageFormat.RAW:
                body = await service.fetches.body(item)
            else:
                shown = await service.fetches.rendered(
                    item, format_, main_only=not whole, select=select
                )
                body = shown.content.encode() if shown and shown.content is not None else None
            if body is not None:
                pages.append((item, body))
        return await service.fetches.info(batch, max_chars=request.max_chars), pages

    shown, pages = run(ctx, work)
    if out is not None:
        for item, body in pages:
            written = _write(out, item, body, format_)
            if told:
                stderr().print(f"Wrote {escape(str(written))}")
        show(shown, as_json, render.fetch_batch)
    elif len(listed) == 1 and len(pages) == 1 and not as_json:
        if format_ is PageFormat.RAW:
            sys.stdout.buffer.write(pages[0][1])
            sys.stdout.flush()
        else:
            stdout().print(pages[0][1].decode(), markup=False, highlight=False, soft_wrap=True)
    else:
        show(shown, as_json, render.fetch_batch)
    if any(item.status is not ItemStatus.SUCCEEDED for item in shown.items):
        raise typer.Exit(EXIT_FAILED)


def _write(out: Path, item: FetchItem, body: bytes, format_: PageFormat) -> Path:
    """Write a page to <out>/<site>/<name>.<extension> (another name if that is taken)."""
    parts = urlsplit(item.url)
    name = parts.path.rstrip("/").rsplit("/", 1)[-1] or "index"
    stem, dot, given = name.rpartition(".")
    if format_ is PageFormat.RAW:
        extension = _RAW_EXTENSIONS.get(item.content_type or "", given if dot else "bin")
    else:
        extension = _EXTENSIONS[format_]
    stem = _UNSAFE.sub("_", (stem if dot else name) + (f"_{parts.query}" if parts.query else ""))
    folder = out / _UNSAFE.sub("_", item.site)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem}.{extension}"
    n = 2
    while path.exists():
        path = folder / f"{stem}-{n}.{extension}"
        n += 1
    path.write_bytes(body)
    return path


@app.command("solve")
@runs("solve_challenges")
def solve(
    ctx: typer.Context,
    sites: Annotated[
        list[str] | None,
        typer.Option(
            "--site", help="Only this site's check (repeat for more).", show_default=False
        ),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Show the anti-bot checks that pages wait for in a window, a tab per site: pass each, and
    its pages go on by themselves (in whichever udl fetches them)."""
    request = parse(SolveChallengesRequest, sites=sites)

    def passed(site: str) -> None:
        if not as_json:
            stderr().print(f"[green]✓[/] {escape(site)} was passed")

    async def work(service: UDLService) -> SolveReport:
        return await service.fetches.solve(request.sites, told=passed)

    show(run(ctx, work), as_json, render.solve_report)
