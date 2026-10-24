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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from udl.automations.events import EventStatus, EventTypeList
from udl.automations.models import (
    ActionInfo,
    ActionList,
    EventDetail,
    EventInfo,
    EventList,
    RunActionResult,
    RunDetail,
    RunInfo,
    RunList,
    ScheduleInfo,
    ScheduleList,
    SchedulePreview,
    ScriptList,
    SubscriptionInfo,
    SubscriptionList,
    SubscriptionTestReport,
)
from udl.automations.runs import RunStatus
from udl.automations.runtime import AutomationState, RuntimeStatus
from udl.automations.schedules import Misfire
from udl.chains.models import FetchMode, TagChain
from udl.chains.shorthand import format_steps
from udl.config import format_duration
from udl.core.models import (
    ChainReport,
    ChainSuggestions,
    ConfigInfo,
    DatabaseList,
    Deleted,
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
from udl.integrations.models import AccountInfo
from udl.plugins.downloaders.base import MediaCandidate, MediaKind
from udl.plugins.integrations.base import AccountStatus
from udl.plugins.registry import ACTIONS, DATABASES, DOWNLOADERS, INTEGRATIONS, SECRETS, SITES

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
    ACTIONS.group: "action",
    INTEGRATIONS.group: "integration",
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
# Automations
# ---------------------------------------------------------------------------------------------

_RUN_STATUS: Final[dict[RunStatus, tuple[str, str]]] = {
    RunStatus.QUEUED: ("…", "dim"),
    RunStatus.RUNNING: ("▶", "blue"),
    RunStatus.AWAITING_APPROVAL: ("?", "magenta"),
    RunStatus.SUCCEEDED: ("✓", "green"),
    RunStatus.FAILED: ("✗", "red"),
    RunStatus.CANCELLED: ("⊘", "yellow"),
    RunStatus.SKIPPED: ("-", "dim"),
}
_EVENT_STATUS: Final[dict[EventStatus, tuple[str, str]]] = {
    EventStatus.PENDING: ("…", "dim"),
    EventStatus.DISPATCHED: ("✓", "green"),
    EventStatus.DROPPED: ("✗", "yellow"),
}


def _marked[S: enum.Enum](status: S, marks: dict[S, tuple[str, str]]) -> str:
    mark, style = marks[status]
    return f"[{style}]{mark} {status.value}[/]"


def _json(value: object) -> str:
    return escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _lines(console: Console, rows: list[tuple[str, str]]) -> None:
    grid = Table.grid(padding=(0, 2))
    for name, value in rows:
        grid.add_row(f"[bold]{name}[/]", value)
    console.print(grid)


def event_types(console: Console, listed: EventTypeList) -> None:
    """The event types, and the fields a filter can name."""
    table = Table("Type", "From", "What", _folded("Fields"))
    for found in listed.types:
        fields = ", ".join(field.path for field in found.fields) or "[dim](any)[/]"
        table.add_row(found.name, found.source, escape(found.summary), fields)
    console.print(table)


def events(console: Console, listed: EventList) -> None:
    """The events, newest first."""
    table = Table("When", "Type", _folded("Subject"), "Status", "Id")
    for found in listed.events:
        table.add_row(
            _when(found.occurred_at),
            found.type,
            escape(found.subject),
            _marked(found.status, _EVENT_STATUS),
            str(found.id),
        )
    console.print(table)
    if not listed.events:
        console.print("No events.")


def event(console: Console, shown: EventInfo) -> None:
    """An event: what happened, and how it was dispatched."""
    rows = [
        ("Event", str(shown.id)),
        ("Type", shown.type),
        ("Subject", escape(shown.subject)),
        ("From", shown.source),
        ("Happened", _when(shown.occurred_at)),
        ("Status", _marked(shown.status, _EVENT_STATUS)),
    ]
    if shown.dropped_reason:
        rows.append(("Dropped", escape(shown.dropped_reason)))
    if shown.caused_by:
        rows.append(("Caused by", f"the run {shown.caused_by} (depth {shown.depth})"))
    rows.append(("Data", _json(shown.data)))
    _lines(console, rows)


def event_detail(console: Console, detail: EventDetail) -> None:
    """An event and the runs it started."""
    event(console, detail.event)
    if detail.runs:
        runs(console, RunList(runs=detail.runs))


def runs(console: Console, listed: RunList) -> None:
    """The runs, newest first."""
    table = Table("Queued", "Action", "For", "Status", _folded("What came of it"), "Id")
    for found in listed.runs:
        said = found.summary
        if found.error is not None:
            said = found.error.message
        elif found.skipped_reason:
            said = found.skipped_reason
        table.add_row(
            _when(found.queued_at),
            found.action,
            escape(found.subscription or found.event_type or "(asked for)"),
            _marked(found.status, _RUN_STATUS),
            escape(said),
            str(found.id),
        )
    console.print(table)
    if not listed.runs:
        console.print("No runs.")


def run(console: Console, shown: RunInfo) -> None:
    """A run: where it is, what it gave and logged, and why it failed."""
    rows = [
        ("Run", str(shown.id)),
        ("Action", f"{shown.action} [dim]({shown.plugin})[/]"),
        ("Status", _marked(shown.status, _RUN_STATUS)),
        ("Attempt", str(shown.attempt)),
    ]
    if shown.subscription:
        rows.append(("Subscription", shown.subscription))
    if shown.event_id:
        rows.append(("Event", f"{shown.event_id} [dim]({shown.event_type})[/]"))
    for name, at in (("Started", shown.started_at), ("Ended", shown.finished_at)):
        if at is not None:
            rows.append((name, _when(at)))
    if shown.next_try_at is not None:
        rows.append(("Next try", _when(shown.next_try_at)))
    if shown.summary:
        rows.append(("Summary", escape(shown.summary)))
    if shown.skipped_reason:
        rows.append(("Skipped", escape(shown.skipped_reason)))
    if shown.error is not None:
        rows.append(("Error", _error(shown.error)))
    if isinstance(shown, RunDetail):
        if shown.output is not None:
            rows.append(("Output", _json(shown.output)))
        if shown.log:
            rows.append(("Log", escape(shown.log.rstrip())))
    _lines(console, rows)


def run_result(console: Console, result: RunActionResult) -> None:
    """A run asked for, or (a dry run) the options it would have run with."""
    if result.run is not None:
        run(console, result.run)
    else:
        console.print("Dry run: the action would run with these options.")
        console.print(_json(result.options))


def subscriptions(console: Console, listed: SubscriptionList) -> None:
    """The subscriptions, by name."""
    table = Table("Name", "On", _folded("Where"), "Runs", "Throttle", "Enabled")
    for found in listed.subscriptions:
        table.add_row(
            found.name,
            "\n".join(found.events),
            escape(found.filter_text or ""),
            "\n".join(found.actions),
            str(found.throttle or ""),
            _yes(found.enabled),
        )
    console.print(table)
    if not listed.subscriptions:
        console.print("No subscriptions: `udl subs set` makes one.")


def subscription(console: Console, info: SubscriptionInfo) -> None:
    """A subscription."""
    rows = [
        ("Subscription", info.name),
        ("On", ", ".join(info.events)),
        ("Accounts", ", ".join(info.accounts) if info.accounts is not None else "any"),
        ("Where", escape(info.filter_text or "(every event)")),
        ("Runs", ", ".join(info.actions)),
        ("Enabled", _yes(info.enabled)),
    ]
    if info.description:
        rows.insert(1, ("About", escape(info.description)))
    if info.throttle is not None:
        rows.append(("Throttle", str(info.throttle)))
    if info.source_filter is not None:
        rows.append(("Source filter", _json(info.source_filter)))
    _lines(console, rows)


def subscription_test(console: Console, report: SubscriptionTestReport) -> None:
    """What a subscription would have matched."""
    if report.filter is not None:
        console.print(f"Where: {escape(report.filter)}")
    for problem in report.problems:
        console.print(f"[yellow]![/] {escape(problem)}")
    table = Table("When", "Type", _folded("Subject"), "Matched", _folded("Failed"))
    for found in report.events:
        table.add_row(
            _when(found.event.occurred_at),
            found.event.type,
            escape(found.event.subject),
            _yes(found.matched),
            escape(found.failed or ""),
        )
    console.print(table)
    matched = sum(1 for found in report.events if found.matched)
    console.print(f"It would have matched {matched} of {len(report.events)} events.")


def actions(console: Console, listed: ActionList) -> None:
    """The actions, by name."""
    table = Table("Name", "Kind", "Run by", "Enabled", _folded("Warnings"))
    for found in listed.actions:
        table.add_row(
            found.name,
            found.plugin,
            "\n".join(found.used_by),
            _yes(found.enabled),
            escape("\n".join(found.warnings)),
        )
    console.print(table)
    if not listed.actions:
        console.print("No actions: `udl actions set` makes one.")


def action(console: Console, info: ActionInfo) -> None:
    """An action: its kind and options (secrets hidden)."""
    rows = [
        ("Action", info.name),
        ("Kind", info.plugin),
        ("Options", _json(info.options)),
        ("Run by", ", ".join(info.used_by) or "[dim](no subscription)[/]"),
        ("Timeout", format_duration(info.timeout)),
        ("Retries", str(info.retries)),
        ("At once", str(info.max_concurrent)),
        ("Enabled", _yes(info.enabled)),
    ]
    if info.description:
        rows.insert(1, ("About", escape(info.description)))
    _lines(console, rows)
    for warning in info.warnings:
        console.print(f"[yellow]![/] {escape(warning)}")


def scripts(console: Console, listed: ScriptList) -> None:
    """The programs script actions may run."""
    if not listed.exists:
        console.print(
            f"There is no scripts_dir ({escape(str(listed.dir))}) yet: make it, and put the "
            "programs script actions run in it."
        )
        return
    table = Table("Name", "Executable", "Size", "Changed")
    for found in listed.scripts:
        table.add_row(
            escape(found.name), _yes(found.executable), decimal(found.size), _when(found.modified)
        )
    console.print(f"In {escape(str(listed.dir))}:")
    console.print(table)


def status(console: Console, shown: RuntimeStatus) -> None:
    """Who runs the automations, and what waits."""
    said = {
        AutomationState.RUNNING: "[green]running here[/]",
        AutomationState.ELSEWHERE: f"running in [bold]{escape(shown.holder or '')}[/]",
        AutomationState.STOPPED: "[yellow]stopped[/]: nothing runs them (start `udl serve`)",
        AutomationState.DISABLED: "disabled ([automations] enabled = false)",
    }[shown.automations]
    rows = [
        ("Automations", said),
        (
            "Runs",
            f"{shown.queued_runs} queued, {shown.running_runs} running, "
            f"{shown.waiting_runs} waiting for approval",
        ),
        ("Events", f"{shown.pending_events} pending"),
    ]
    if shown.last_dispatch is not None:
        rows.append(("Last dispatch", _when(shown.last_dispatch)))
    if shown.lease_expires_at is not None and shown.automations is not AutomationState.STOPPED:
        rows.append(("Lease until", _when(shown.lease_expires_at)))
    if shown.next_schedules:
        rows.append(
            (
                "Next schedules",
                "\n".join(
                    f"{_when(found.next_run_at)}  {escape(found.name)}"
                    for found in shown.next_schedules
                ),
            )
        )
    _lines(console, rows)


def _in_zone(at: datetime | None, zone: str) -> str:
    """A time as it is in a schedule's time zone (the system's, for one without a name)."""
    if at is None:
        return ""
    try:
        local = at.astimezone(ZoneInfo(zone))
    except ZoneInfoNotFoundError, ValueError:
        local = at.astimezone()
    return local.strftime("%Y-%m-%d %H:%M %Z")


def schedules(console: Console, listed: ScheduleList) -> None:
    """The schedules, by name."""
    table = Table("Name", "When", "Zone", "Next", "Last", "Runs", "Enabled")
    for found in listed.schedules:
        table.add_row(
            found.name,
            escape(found.spec_text),
            found.zone,
            _in_zone(found.next_run_at, found.zone) if found.enabled else "",
            _in_zone(found.last_run_at, found.zone),
            "\n".join(found.actions),
            _yes(found.enabled),
        )
    console.print(table)
    if not listed.schedules:
        console.print("No schedules: `udl schedules set` makes one.")


_ACCOUNT_STATUS: Final[dict[AccountStatus, str]] = {
    AccountStatus.PENDING: "[yellow]pending[/] (waiting for the authorization's answer)",
    AccountStatus.CONNECTED: "[green]connected[/]",
    AccountStatus.NEEDS_REAUTH: "[red]needs connecting again[/]",
    AccountStatus.ERROR: "[red]error[/]",
}


def account(console: Console, info: AccountInfo) -> None:
    """An account: who it is, what it is connected for, and what went wrong with it."""
    rows = [
        ("Account", escape(info.account)),
        ("Status", _ACCOUNT_STATUS[info.status]),
        ("Login", escape(info.identity.display) if info.identity is not None else "-"),
        ("Connected", f"{info.method}, for {', '.join(info.access) or 'nothing in particular'}"),
    ]
    if info.options:
        rows.append(("Options", _json(info.options)))
    if not info.enabled:
        rows.append(("Enabled", _yes(info.enabled)))
    if info.pending_until is not None:
        rows.append(("Until", _when(info.pending_until)))
    if info.last_error is not None:
        rows.append(("Error", _error(info.last_error)))
    _lines(console, rows)


def schedule(console: Console, info: ScheduleInfo) -> None:
    """A schedule."""
    rows = [
        ("Schedule", info.name),
        ("When", escape(info.spec_text)),
        ("Zone", info.zone),
        (
            "Next",
            "\n".join(_in_zone(time, info.zone) for time in info.next_times) or "-",
        ),
        ("Runs", ", ".join(info.actions) or "nothing (`--action` to run something)"),
        (
            "Misfire",
            "skip the times missed while udl was stopped"
            if info.misfire is Misfire.SKIP
            else f"fire the latest time missed, if within {format_duration(info.misfire_grace)}",
        ),
        ("Enabled", _yes(info.enabled)),
    ]
    if info.description:
        rows.insert(1, ("About", escape(info.description)))
    if info.jitter:
        rows.append(("Jitter", f"up to {format_duration(info.jitter)}"))
    if info.last_run_at is not None:
        rows.append(("Last", _in_zone(info.last_run_at, info.zone)))
    if info.data:
        rows.append(("Data", _json(info.data)))
    _lines(console, rows)


def schedule_preview(console: Console, preview: SchedulePreview) -> None:
    """When a schedule would fire."""
    console.print(f"When: {escape(preview.spec_text)}")
    table = Table(f"In {preview.zone}", "UTC")
    for time in preview.times:
        table.add_row(time.local.strftime("%Y-%m-%d %H:%M %Z"), time.utc.strftime("%Y-%m-%d %H:%M"))
    console.print(table)
    if not preview.times:
        console.print("It fires no more.")


def deleted(console: Console, done: Deleted) -> None:
    """Whether something was deleted."""
    console.print("Deleted." if done.deleted else "There was nothing to delete.")


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
