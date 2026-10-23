"""
----------------
- downloads.py -
----------------

udl get (and `udl <url>...`), udl history, udl jobs show / cancel
"""

# System Imports
from pathlib import Path
from typing import Annotated
from uuid import UUID

# Package Imports
import typer

# Local Imports
from udl.core.models import DownloadRequest, JobRequest
from udl.db.models import DownloadQuery, JobStatus
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
    usage_error,
)


def _read_urls(file: typer.FileText) -> list[str]:
    """The URLs in a file, one a line (blank lines and # comments left out)."""
    lines = (line.strip() for line in file)
    return [line for line in lines if line and not line.startswith("#")]


@runs("download")
def get(
    ctx: typer.Context,
    urls: Annotated[list[str] | None, typer.Argument(help="The URLs.", show_default=False)] = None,
    input_file: Annotated[
        typer.FileText | None,
        typer.Option(
            "-i", "--input", help="Also download the URLs in FILE, one a line (- for stdin)."
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--output",
            help="The folder to download into (by default the site's, or the config's "
            "download_dir).",
            metavar="DIR",
        ),
    ] = None,
    tool: Annotated[
        str | None,
        typer.Option(help="Download with this downloader only (e.g. yt-dlp, http)."),
    ] = None,
    template: Annotated[
        str | None,
        typer.Option(
            "-t",
            "--template",
            help="Where each file goes in the folder, e.g. '{site}/{title}.{ext}'.",
        ),
    ] = None,
    databases: Annotated[
        list[str] | None,
        typer.Option(
            "--db", help="Use only this database connection (more than one: --db a --db b)."
        ),
    ] = None,
    reprobe: Annotated[
        bool,
        typer.Option("--reprobe", help="Ignore what worked on the site before, and try again."),
    ] = False,
    no_record: Annotated[
        bool,
        typer.Option(
            "--no-record", help="Remember nothing: neither what worked, nor the downloads."
        ),
    ] = False,
    jobs: Annotated[
        int | None,
        typer.Option("-j", "--jobs", min=1, help="Run at most N downloads at once.", metavar="N"),
    ] = None,
    as_json: JsonOption = False,
) -> None:
    """Download URLs: udl works out which downloader can, and remembers it for the site.

    It exits with 1 when some of the downloads failed. Ctrl-C cancels them."""
    runtime = Runtime.of(ctx)
    listed = [*(urls or []), *(_read_urls(input_file) if input_file else [])]
    if not listed:
        usage_error("No URLs to download: name some, or a file of them (-i FILE).")
    request = parse(
        DownloadRequest,
        urls=listed,
        output_dir=output,
        tool=tool,
        filename_template=template,
        reprobe=reprobe,
        record=not no_record,
        databases=databases,
    )
    settings = runtime.settings
    if jobs is not None:
        settings = settings.model_copy(update={"max_concurrent": jobs})
    with render.DownloadProgress(stderr(), enabled=not as_json and runtime.verbosity >= 0) as bars:
        batch = run(
            ctx, lambda service: service.download(request, on_event=bars.show), settings=settings
        )
    show(batch, as_json, render.batch)
    if any(job.status is not JobStatus.FINISHED for job in batch.jobs):
        raise typer.Exit(EXIT_FAILED)


@runs("list_downloads")
def history(
    ctx: typer.Context,
    site: Annotated[
        str | None, typer.Option(help="Only this site's downloads (its key, a host or a URL).")
    ] = None,
    status: Annotated[
        JobStatus | None, typer.Option(help="Only downloads with this status.")
    ] = None,
    limit: Annotated[
        int, typer.Option("-n", "--limit", min=1, help="Show at most N.", metavar="N")
    ] = 20,
    offset: Annotated[int, typer.Option(min=0, help="Skip the N newest first.", metavar="N")] = 0,
    as_json: JsonOption = False,
) -> None:
    """List the downloads, newest first."""
    query = parse(DownloadQuery, site_key=site, status=status, limit=limit, offset=offset)
    show(run(ctx, lambda service: service.list_downloads(query)), as_json, render.downloads)


jobs = typer.Typer(
    help="Download jobs: one per URL, run by udl get (or the API and MCP servers).",
    no_args_is_help=True,
    rich_markup_mode="markdown",
)

JobId = Annotated[UUID, typer.Argument(help="The download's id (udl history lists them).")]


@jobs.command("show")
@runs("get_download")
def show_job(ctx: typer.Context, job_id: JobId, as_json: JsonOption = False) -> None:
    """Show a download: where it is, and what came of it.

    Its files, or why it failed (and, while this udl runs it, every downloader it tried)."""
    request = JobRequest(id=job_id)
    show(run(ctx, lambda service: service.get_download(request)), as_json, render.job)


@jobs.command("cancel")
@runs("cancel_download")
def cancel_job(
    ctx: typer.Context,
    job_id: Annotated[UUID, typer.Argument(help="The download's id, or its batch's.")],
    as_json: JsonOption = False,
) -> None:
    """Cancel a download, or every download of a batch.

    A udl cancels only the downloads it runs: Ctrl-C cancels udl get's, and the cancel_download
    of the API or the MCP server cancels udl serve's or udl mcp's. A download that has ended is
    left as it is."""
    request = JobRequest(id=job_id)
    show(run(ctx, lambda service: service.cancel_download(request)), as_json, render.downloads)
