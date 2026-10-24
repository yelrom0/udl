"""
-------------
- render.py -
-------------

How the commands show their results to people (--json shows the response models instead):
tables, a job's details, progress bars while downloads run, and any response at all (model(),
for the operations plugins add)
"""

# System Imports
import enum
import json
from datetime import datetime
from types import TracebackType
from typing import Any, Final, Self, cast
from uuid import UUID

# Package Imports
from pydantic import BaseModel, SecretStr
from rich.console import Console, RenderableType
from rich.filesize import decimal
from rich.markup import escape
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Column, Table

# Local Imports
from udl.chains.models import FetchMode, TagChain
from udl.chains.shorthand import format_steps
from udl.core.models import (
    ChainReport,
    ChainSuggestions,
    ConfigInfo,
    DatabaseList,
    DownloadBatch,
    DownloadList,
    ImportedChains,
    JobEvent,
    JobEventType,
    JobInfo,
    PluginList,
    PluginSettingsInfo,
    ProbeReport,
    SiteInfo,
    SiteList,
    SyncReport,
)
from udl.db.models import JobStatus, Strategy
from udl.errors import ErrorInfo
from udl.plugins.downloaders.base import MediaCandidate, MediaKind
from udl.plugins.registry import DATABASES, DOWNLOADERS, SECRETS, SITES

_STATUS: Final[dict[JobStatus, tuple[str, str]]] = {
    JobStatus.QUEUED: ("…", "dim"),
    JobStatus.RUNNING: ("↓", "blue"),
    JobStatus.FINISHED: ("✓", "green"),
    JobStatus.FAILED: ("✗", "red"),
    JobStatus.CANCELLED: ("⊘", "yellow"),
}


def _folded(header: str) -> Column:
    """A column whose long values (URLs, paths) wrap rather than being cut short."""
    return Column(header, overflow="fold")


def _when(at: datetime | None) -> str:
    return at.astimezone().strftime("%Y-%m-%d %H:%M") if at else ""


_PLUGIN_TYPES: Final[dict[str, str]] = {
    DATABASES.group: "database",
    DOWNLOADERS.group: "downloader",
    SITES.group: "site",
    SECRETS.group: "secret store",
}


def _yes(flag: bool | None) -> str:
    return {True: "[green]yes[/]", False: "[red]no[/]", None: "-"}[flag]


def _status(status: JobStatus) -> str:
    mark, style = _STATUS[status]
    return f"[{style}]{mark} {status}[/]"


def _error(error: ErrorInfo, *, hint: bool = True) -> str:
    text = f"[red]{escape(error.message)}[/] [dim]({error.code})[/]"
    if hint and error.hint:
        indented = error.hint.replace("\n", "\n  ")
        text += f"\n  [dim]{escape(indented)}[/]"
    return text


# ---------------------------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------------------------


def batch(console: Console, downloaded: DownloadBatch) -> None:
    """What came of each URL: its files, or why it failed."""
    for job in downloaded.jobs:
        mark, style = _STATUS[job.status]
        line = f"[{style}]{mark}[/] {escape(job.url)}"
        if job.status is JobStatus.FINISHED:
            files = ", ".join(escape(str(path)) for path in job.files)
            line += f" → {files} [dim]({decimal(job.size_bytes)}, {job.tool})[/]"
        elif job.error is not None:
            line += f": {_error(job.error)}"
        else:
            line += f" [dim]({job.status})[/]"
        console.print(line, soft_wrap=True)


def job(console: Console, shown: JobInfo) -> None:
    """A download job in full: what it downloaded or why not, and every try."""
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("Download", str(shown.id))
    if shown.batch_id is not None:
        details.add_row("Batch", str(shown.batch_id))
    details.add_row("URL", escape(shown.url))
    details.add_row("Status", _status(shown.status))
    if shown.tool:
        tool = shown.tool + (f" → {shown.handed_off_to}" if shown.handed_off_to else "")
        details.add_row("Tool", tool)
    if shown.title:
        details.add_row("Title", escape(shown.title))
    if shown.output_dir:
        details.add_row("Folder", escape(str(shown.output_dir)))
    for path in shown.files:
        details.add_row("File", escape(str(path)))
    if shown.size_bytes:
        details.add_row("Size", decimal(shown.size_bytes))
    if shown.progress is not None and shown.status is JobStatus.RUNNING:
        done = decimal(shown.progress.downloaded_bytes)
        total = decimal(shown.progress.total_bytes) if shown.progress.total_bytes else "?"
        details.add_row("Progress", f"{done} of {total}")
    details.add_row("Started", _when(shown.started_at))
    if shown.finished_at:
        details.add_row("Ended", _when(shown.finished_at))
    if shown.error is not None:
        details.add_row("Error", _error(shown.error))
    console.print(details)
    if shown.attempts:
        tries = Table("Tool", "Why", "Signed in", "Result", title="Tries", title_justify="left")
        for attempt in shown.attempts:
            result = _error(attempt.error, hint=False) if attempt.error else "[green]worked[/]"
            tries.add_row(attempt.tool, attempt.source, _yes(attempt.signed_in), result)
        console.print(tries)


def downloads(console: Console, listed: DownloadList) -> None:
    """Downloads, one a row."""
    if not listed.downloads:
        console.print("No downloads.")
        return
    table = Table("Started", "Status", "Site", _folded("URL"), "Tool", "Files", "Id")
    for shown in listed.downloads:
        files = f"{len(shown.files)} ({decimal(shown.size_bytes)})" if shown.files else ""
        status = _status(shown.status)
        if shown.error is not None:
            status += f"\n[dim]{shown.error.code}[/]"
        table.add_row(
            _when(shown.started_at),
            status,
            shown.site_key,
            escape(shown.url),
            shown.tool or "",
            files,
            str(shown.id),
        )
    console.print(table)


class DownloadProgress:
    """Progress bars for the jobs of a download, while it runs (only on a terminal, and only
    when `enabled`)."""

    def __init__(self, console: Console, *, enabled: bool = True) -> None:
        self._enabled = enabled and console.is_terminal
        self._progress = Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        )
        self._tasks: dict[UUID, TaskID] = {}

    def __enter__(self) -> Self:
        if self._enabled:
            self._progress.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._enabled:
            self._progress.stop()

    async def show(self, event: JobEvent) -> None:
        """Show what happened to a job (DownloadRequest's on_event)."""
        if not self._enabled:
            return
        shown = event.job
        task = self._tasks.get(shown.id)
        if task is None:
            task = self._progress.add_task(escape(shown.url), total=None, start=False)
            self._tasks[shown.id] = task
        if event.type is JobEventType.STARTED:
            self._progress.start_task(task)
        elif event.type is JobEventType.PROGRESS and shown.progress is not None:
            self._progress.update(
                task,
                completed=shown.progress.downloaded_bytes,
                total=shown.progress.total_bytes,
                description=escape(f"{shown.url} ({event.tool})"),
            )
        elif event.type.ends:
            mark, style = _STATUS[shown.status]
            # (The last progress event may have come well before the end.)
            done = shown.progress.downloaded_bytes if shown.progress else 0
            if shown.status is JobStatus.FINISHED:
                done = shown.size_bytes
            self._progress.update(
                task,
                completed=done,
                total=done if shown.status is JobStatus.FINISHED else None,
                description=f"[{style}]{mark}[/] {escape(shown.url)}",
            )
            self._progress.stop_task(task)


# ---------------------------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------------------------


def probe(console: Console, report: ProbeReport) -> None:
    """What each downloader found out about the URL."""
    signed_in = " [dim](signed in)[/]" if report.signed_in else ""
    console.print(f"[bold]{escape(report.url)}[/] [dim]({report.site_key})[/]{signed_in}")
    table = Table("Tool", "Can download", "Confidence", _folded("Found"))
    for result in report.results:
        if result.error is not None:
            found = _error(result.error, hint=False)
        else:
            media = [escape(str(item.url)) for item in result.media[:3]]
            if len(result.media) > len(media):
                media.append(f"[dim]and {len(result.media) - len(media)} more[/]")
            if result.handoff is not None:
                media.append(f"[dim](handed to {result.handoff.tool})[/]")
            found = "\n".join([escape(result.title or ""), *media]).strip()
        table.add_row(result.tool, _yes(result.ok), f"{result.confidence:.2f}", found)
    console.print(table)


# ---------------------------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------------------------


def sites(console: Console, listed: SiteList) -> None:
    if not listed.sites:
        console.print("udl knows no sites yet.")
        return
    table = Table("Site", "Name", _folded("Folder"), "Updated")
    for site in listed.sites:
        folder = str(site.default_output_dir) if site.default_output_dir else ""
        table.add_row(site.key, escape(site.name or ""), escape(folder), _when(site.updated_at))
    console.print(table)


def site(console: Console, info: SiteInfo) -> None:
    """A site, and its strategies."""
    console.print(
        f"[bold]{info.site.key}[/]" + (f" ({escape(info.site.name)})" if info.site.name else "")
    )
    if info.site.default_output_dir:
        console.print(f"Downloads to {escape(str(info.site.default_output_dir))}")
    if info.site.notes:
        console.print(escape(info.site.notes), style="dim")
    if not info.strategies:
        console.print("No strategies.")
        return
    table = Table(
        "Tool",
        "Pattern",
        "Source",
        "Priority",
        "Enabled",
        "Worked",
        "Failed",
        "Sign in",
        "Last error",
    )
    for strategy in info.strategies:
        table.add_row(*_strategy_row(strategy))
    console.print(table)


def _strategy_row(strategy: Strategy) -> list[str]:
    failed = str(strategy.failure_count)
    if strategy.consecutive_failures:
        failed += f" ({strategy.consecutive_failures} in a row)"
    tool = strategy.tool
    if strategy.chain is not None:
        tool += f"\n[dim]{escape(format_steps(strategy.chain.steps))}[/]"
    return [
        tool,
        escape(strategy.url_pattern or "(whole site)"),
        strategy.source,
        str(strategy.priority),
        _yes(strategy.enabled),
        str(strategy.success_count),
        failed,
        _yes(strategy.requires_auth),
        _error(strategy.last_error, hint=False) if strategy.last_error else "",
    ]


def strategy(console: Console, stored: Strategy) -> None:
    """A strategy that was just set."""
    where = f"the URLs matching {stored.url_pattern!r} on" if stored.url_pattern else ""
    console.print(
        f"udl downloads {escape(where)} [bold]{stored.site_key}[/] with [bold]{stored.tool}[/]"
        + (f" {escape(str(stored.options))}" if stored.options else "")
        + (", signed in" if stored.requires_auth else "")
        + "."
    )


# ---------------------------------------------------------------------------------------------
# Tag chains
# ---------------------------------------------------------------------------------------------


def _media(media: MediaCandidate) -> str:
    """Media's type and size, as far as they are known."""
    kind = media.content_type or (media.kind if media.kind is not MediaKind.FILE else "")
    size = decimal(media.size_bytes) if media.size_bytes is not None else ""
    return ", ".join(part for part in (kind, size) if part)


def _chain(chain: TagChain) -> str:
    browser = " [dim](in the browser)[/]" if chain.fetch is FetchMode.BROWSER else ""
    return f"[bold]{escape(format_steps(chain.steps))}[/]{browser}"


def suggestions(console: Console, found: ChainSuggestions) -> None:
    """The suggested chains, numbered, best first."""
    how = "as served" if found.fetch is FetchMode.STATIC else "rendered in the browser"
    signed_in = ", signed in" if found.signed_in else ""
    console.print(f"[bold]{escape(found.url)}[/] [dim]({how}{signed_in})[/]")
    if found.error is not None:
        console.print(_error(found.error))
        return
    if not found.suggestions:
        console.print("No media found on the page.")
        return
    table = Table("#", _folded("Chain"), _folded("Media"), "Type, size", "Found by")
    for number, suggestion in enumerate(found.suggestions, start=1):
        media = suggestion.media
        answer = " [red](no answer)[/]" if suggestion.reachable is False else ""
        table.add_row(
            str(number),
            escape(suggestion.shorthand),
            escape(media.url),
            escape(_media(media)) + answer,
            escape(media.found_by or ""),
        )
    console.print(table)


def chain_report(console: Console, report: ChainReport) -> None:
    """What each step of a chain found, and the media it led to."""
    if report.steps:
        table = Table("#", "Step", _folded("Found"), "Count", title="Steps", title_justify="left")
        for number, step in enumerate(report.steps, start=1):
            found = [escape(value) for value in step.found[:3]]
            if step.count > len(found):
                found.append(f"[dim]and {step.count - len(found)} more[/]")
            table.add_row(str(number), escape(step.step), "\n".join(found), str(step.count))
        console.print(table)
    if report.error is not None:
        console.print(f"[red]✗[/] {_error(report.error)}")
        return
    console.print(f"[green]✓[/] It leads to {len(report.media)} media:")
    for media in report.media:
        about = _media(media)
        console.print(f"  {escape(media.url)}" + (f" [dim]({escape(about)})[/]" if about else ""))


def chain_strategy(console: Console, stored: Strategy) -> None:
    """A chain that was just saved."""
    where = f"the URLs matching {stored.url_pattern!r} on " if stored.url_pattern else ""
    chain = f" by following {_chain(stored.chain)}" if stored.chain else f" with {stored.tool}"
    console.print(
        f"udl downloads {escape(where)}[bold]{stored.site_key}[/]{chain}"
        + (", signed in" if stored.requires_auth else "")
        + "."
    )


def imported(console: Console, done: ImportedChains) -> None:
    """The chains that were imported."""
    if not done.strategies:
        console.print("There were no chains to import.")
        return
    for stored in done.strategies:
        where = f" (URLs matching {stored.url_pattern!r})" if stored.url_pattern else ""
        chain = _chain(stored.chain) if stored.chain else stored.tool
        console.print(f"Imported [bold]{stored.site_key}[/]{escape(where)}: {chain}")


# ---------------------------------------------------------------------------------------------
# udl itself
# ---------------------------------------------------------------------------------------------


def databases(console: Console, listed: DatabaseList) -> None:
    table = Table(
        "Name", "Plugin", _folded("URL"), "Primary", "Priority", "Read-only", "Connected", "Healthy"
    )
    for db in listed.databases:
        table.add_row(
            db.name,
            db.plugin,
            escape(db.url),
            _yes(db.primary),
            str(db.priority),
            _yes(db.read_only),
            _yes(db.connected),
            _yes(db.healthy),
        )
    console.print(table)


def synced(console: Console, report: SyncReport) -> None:
    """What a sync added, replaced and left as it was in each database, by kind of record."""
    table = Table("Into", "Records", "Added", "Updated", "Unchanged")
    for result in report.targets:
        for kind, counts in (
            ("sites", result.sites),
            ("strategies", result.strategies),
            ("downloads", result.downloads),
            *sorted(result.records.items()),
        ):
            table.add_row(
                escape(result.target),
                kind,
                str(counts.added),
                str(counts.updated),
                str(counts.unchanged),
            )
    console.print(f"From [bold]{escape(report.source)}[/]:")
    console.print(table)
    if report.dry_run:
        console.print("Dry run: nothing was changed. A sync would make these changes.")


def plugins(console: Console, listed: PluginList) -> None:
    """The plugins by type (a site plugin with the sites it handles), whether each can run, and
    where local plugins are read from."""
    table = Table("Type", "Name", "Source", "Available", "Needs", _folded("Origin"))
    for plugin in listed.plugins:
        needs = [
            f"{requirement.name} ({requirement.kind})"
            + (f": {requirement.hint}" if requirement.hint else "")
            for requirement in plugin.missing
        ]
        if plugin.problem:
            needs.append(plugin.problem)
        origin = escape(plugin.origin)
        if plugin.overrides:
            origin += f"\n[dim](overrides {escape(plugin.overrides)})[/]"
        table.add_row(
            _PLUGIN_TYPES.get(plugin.kind, plugin.kind),
            "\n".join([plugin.name, *(f"[dim]{escape(d)}[/]" for d in plugin.domains)]),
            plugin.source,
            _yes(plugin.available),
            escape("\n".join(needs)),
            origin,
        )
    console.print(table)
    for problem in listed.problems:
        console.print(f"[red]Could not load[/] {escape(problem.origin)}: {escape(problem.error)}")
    if listed.plugin_dirs:
        dirs = ", ".join(escape(str(directory)) for directory in listed.plugin_dirs)
        console.print(f"Local plugins (a single .py file each) are read from {dirs}.")


def settings(console: Console, info: PluginSettingsInfo) -> None:
    """A plugin's settings: each one's value (secrets hidden) and where it comes from."""
    table = Table("Setting", _folded("Value"), "From", "")
    for name, value in info.values.items():
        notes = [
            note
            for note, applies in (
                ("secret", name in info.secrets),
                ("file only", name in info.file_only),
            )
            if applies
        ]
        table.add_row(
            name,
            escape(json.dumps(value, ensure_ascii=False)),
            str(info.sources.get(name, "")),
            f"[dim]{', '.join(notes)}[/]",
        )
    console.print(f"The [bold]{escape(info.name)}[/] plugin's settings:")
    console.print(table)


def config(console: Console, info: ConfigInfo) -> None:
    """The settings, as JSON (secrets hidden)."""
    console.print_json(info.settings.model_dump_json())


# ---------------------------------------------------------------------------------------------
# Any response
# ---------------------------------------------------------------------------------------------


def model(console: Console, response: BaseModel) -> None:
    """Any response: a line for each field (a list of models as a table)."""
    console.print(_fields(response))


def _fields(found: BaseModel) -> Table:
    grid = Table.grid(padding=(0, 2))
    for name, value in found:
        grid.add_row(f"[bold]{escape(name.replace('_', ' ').capitalize())}[/]", _shown(value))
    return grid


def _shown(value: Any) -> RenderableType:
    """A field's value: a list of models as a table, anything else as a line of text."""
    listed = cast(list[object], value) if isinstance(value, list) else []
    items = [item for item in listed if isinstance(item, BaseModel)]
    if items and len(items) == len(listed):
        names = list(type(items[0]).model_fields)
        table = Table(*(name.replace("_", " ").capitalize() for name in names))
        for item in items:
            table.add_row(*(escape(_text(getattr(item, name, None))) for name in names))
        return table
    if value is None:
        return "[dim]-[/]"
    if isinstance(value, bool):
        return _yes(value)
    return escape(_text(value))


def _text(value: Any) -> str:
    """A value in a line of text."""
    if value is None:
        return "-"
    if isinstance(value, SecretStr):
        return "***"
    if isinstance(value, enum.Enum):
        return str(value.value)
    if isinstance(value, BaseModel):
        return ", ".join(f"{name}: {_text(item)}" for name, item in value)
    if isinstance(value, list | tuple | set | frozenset):
        return ", ".join(_text(item) for item in value)  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    return str(value)
