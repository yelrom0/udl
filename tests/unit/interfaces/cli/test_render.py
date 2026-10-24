"""
------------------
- test_render.py -
------------------

How the CLI shows results to people, for what the commands' tests do not reach: a job this udl
is running (with its tries), progress bars on a terminal, and the rarer parts of each result
"""

# System Imports
import asyncio
import io
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

# Package Imports
from pydantic import BaseModel, SecretStr
from rich.console import Console

# Local Imports
from udl.automations.models import (
    ActionInfo,
    EventInfo,
    RunDetail,
    RunList,
    ScheduleInfo,
    SchedulePreview,
    SubscriptionInfo,
    SubscriptionTestReport,
)
from udl.automations.runtime import AutomationState, RuntimeStatus
from udl.automations.schedules import AtSpec
from udl.chains.models import FetchMode, StepTrace, TagChain
from udl.chains.suggest import ChainSuggestion
from udl.core.models import (
    Attempt,
    CandidateSource,
    ChainReport,
    ChainSuggestions,
    DownloadBatch,
    DownloadList,
    ImportedChains,
    JobEvent,
    JobEventType,
    JobInfo,
    PluginList,
    PluginStatus,
    ProbeReport,
    SiteInfo,
    SiteList,
)
from udl.db.models import JobStatus, Site, Strategy, StrategySource
from udl.errors import AuthRequired, NoStrategyFound
from udl.interfaces.cli import render
from udl.plugins.downloaders.base import (
    DownloadProgress,
    Handoff,
    MediaCandidate,
    MediaKind,
    ProbeResult,
)
from udl.plugins.registry import PluginProblem, PluginSource
from udl.plugins.requirements import Requirement

URL = "https://example.com/watch"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def shown[M](render_it: Callable[[Console, M], None], model: M) -> str:
    """What `render_it` shows of `model`."""
    output = io.StringIO()
    render_it(Console(file=output, width=200, color_system=None), model)
    return output.getvalue()


def job(**fields: object) -> JobInfo:
    return JobInfo.model_validate(
        {
            "id": uuid.uuid4(),
            "url": URL,
            "site_key": "example.com",
            "started_at": NOW,
            **fields,
        }
    )


def test_a_job_this_udl_runs_shows_its_progress_and_tries() -> None:
    running = job(
        batch_id=uuid.uuid4(),
        status=JobStatus.RUNNING,
        tool="scraper",
        handed_off_to="http",
        title="A video",
        output_dir=Path("/downloads"),
        progress=DownloadProgress(downloaded_bytes=500, total_bytes=None),
        attempts=[
            Attempt(
                tool="http", source=CandidateSource.PROBE, error=AuthRequired("Sign in").to_info()
            ),
            Attempt(tool="http", source=CandidateSource.PROBE, signed_in=True),
        ],
    )

    text = shown(render.job, running)

    assert "scraper → http" in text
    assert "A video" in text
    assert "500 bytes of ?" in text
    assert "Tries" in text
    assert "auth_required" in text
    assert "worked" in text


def test_a_finished_job_shows_its_size() -> None:
    text = shown(
        render.job, job(status=JobStatus.FINISHED, files=[Path("/d/a.mp4")], size_bytes=2_000_000)
    )

    assert "/d/a.mp4" in text
    assert "2.0 MB" in text


def test_a_job_that_has_not_ended_is_listed_with_its_status() -> None:
    text = shown(render.batch, DownloadBatch(id=uuid.uuid4(), jobs=[job(status=JobStatus.RUNNING)]))

    assert f"↓ {URL} (running)" in text


def test_a_failed_download_is_listed_with_its_error() -> None:
    error = NoStrategyFound("Nothing worked").to_info()

    text = shown(
        render.downloads, DownloadList(downloads=[job(status=JobStatus.FAILED, error=error)])
    )

    assert "no_strategy_found" in text


def test_progress_bars_on_a_terminal() -> None:
    output = io.StringIO()
    console = Console(file=output, width=120, force_terminal=True, color_system=None)
    queued = job(status=JobStatus.QUEUED)
    started = queued.model_copy(update={"status": JobStatus.RUNNING})
    progress = started.model_copy(
        update={"progress": DownloadProgress(downloaded_bytes=50, total_bytes=100)}
    )
    # (Progress events are spaced out: the last one can come well before the end.)
    finished = progress.model_copy(update={"status": JobStatus.FINISHED, "size_bytes": 100})
    other = job(url="https://example.com/other", status=JobStatus.QUEUED)
    failed = other.model_copy(update={"status": JobStatus.FAILED})

    async def run() -> None:
        with render.DownloadProgress(console) as bars:
            for kind, info in (
                (JobEventType.QUEUED, queued),
                (JobEventType.STARTED, started),
                (JobEventType.PROGRESS, progress),
                (JobEventType.FINISHED, finished),
                (JobEventType.QUEUED, other),
                (JobEventType.FAILED, failed),
            ):
                await bars.show(JobEvent(type=kind, at=NOW, job=info, tool="http"))

    asyncio.run(run())

    assert URL in output.getvalue()
    assert "100/100 bytes" in output.getvalue()
    assert "✗ https://example.com/other" in output.getvalue()


def test_no_progress_bars_when_not_enabled() -> None:
    output = io.StringIO()
    queued = JobEvent(type=JobEventType.QUEUED, at=NOW, job=job(status=JobStatus.QUEUED))

    async def run() -> None:
        with render.DownloadProgress(
            Console(file=output, force_terminal=True), enabled=False
        ) as bars:
            await bars.show(queued)

    asyncio.run(run())

    assert output.getvalue() == ""


def test_a_probe_shows_some_of_the_media_and_who_it_is_handed_to() -> None:
    media = [MediaCandidate(url=f"https://cdn.example.com/{n}.mp4") for n in range(5)]
    report = ProbeReport(
        url=URL,
        site_key="example.com",
        signed_in=True,
        results=[
            ProbeResult(
                tool="scraper",
                ok=True,
                confidence=0.5,
                title="A video",
                media=media,
                handoff=Handoff(tool="http", url="https://cdn.example.com/0.mp4"),
            )
        ],
    )

    text = shown(render.probe, report)

    assert "(signed in)" in text
    assert "https://cdn.example.com/2.mp4" in text
    assert "https://cdn.example.com/3.mp4" not in text
    assert "and 2 more" in text
    assert "handed to http" in text


def test_a_site_with_its_folder_and_notes() -> None:
    site = Site(key="example.com", name="Example", default_output_dir=Path("/videos"), notes="Mine")

    listed = shown(render.sites, SiteList(sites=[site]))
    text = shown(render.site, SiteInfo(site=site, strategies=[]))

    assert "/videos" in listed
    assert "example.com (Example)" in text
    assert "Downloads to /videos" in text
    assert "Mine" in text
    assert "No strategies" in text


def test_a_strategy_that_keeps_failing() -> None:
    failing = Strategy(
        site_key="example.com",
        tool="yt-dlp",
        source=StrategySource.PROBE,
        failure_count=4,
        consecutive_failures=2,
        last_error=AuthRequired("Sign in").to_info(),
    )

    text = shown(render.site, SiteInfo(site=Site(key="example.com"), strategies=[failing]))

    assert "4 (2 in a row)" in text
    assert "auth_required" in text


def test_a_strategy_just_set() -> None:
    whole = Strategy(site_key="example.com", tool="http", source=StrategySource.MANUAL)
    part = whole.model_copy(
        update={"url_pattern": "^/shorts/", "options": {"format": "best"}, "requires_auth": True}
    )

    assert "udl downloads example.com with http." in " ".join(shown(render.strategy, whole).split())
    text = shown(render.strategy, part)
    assert "'^/shorts/'" in text
    assert "format" in text
    assert "signed in" in text


def test_plugins_that_cannot_run_or_load() -> None:
    listed = PluginList(
        plugins=[
            PluginStatus(
                kind="udl.downloaders",
                name="needs-curl",
                source=PluginSource.LOCAL,
                origin="x.py",
                available=False,
                missing=[Requirement.binary("curl", hint="pacman -S curl")],
            ),
            PluginStatus(
                kind="udl.downloaders",
                name="misconfigured",
                source=PluginSource.BUILTIN,
                origin="m:M",
                available=False,
                problem="Invalid options",
            ),
        ],
        problems=[PluginProblem(kind=None, origin="broken.py", error="SyntaxError")],
        plugin_dirs=[],
    )

    text = shown(render.plugins, listed)

    assert "curl (binary): pacman -S curl" in text
    assert "Invalid options" in text
    assert "Could not load broken.py: SyntaxError" in text
    assert "read from" not in text  # (there are no plugin dirs)


def test_plugins_by_type_with_the_sites_site_plugins_handle_and_what_they_override() -> None:
    listed = PluginList(
        plugins=[
            PluginStatus(
                kind="udl.databases",
                name="sqlite",
                source=PluginSource.BUILTIN,
                origin="udl.plugins.databases.sqlite:SQLitePlugin",
                available=True,
            ),
            PluginStatus(
                kind="udl.sites",
                name="youtube",
                source=PluginSource.LOCAL,
                origin="/home/me/.config/udl/plugins/youtube.py",
                overrides="udl.plugins.sites.youtube:YouTubePlugin",
                available=True,
                domains=["youtube.com", "youtu.be"],
            ),
        ],
        problems=[],
        plugin_dirs=[Path("/home/me/.config/udl/plugins"), Path("/srv/udl/plugins")],
    )

    text = shown(render.plugins, listed)
    rows = [
        line.split("│") for line in text.splitlines() if "sqlite:" in line or "youtube.py" in line
    ]

    assert "Type" in text
    assert [(row[1].strip(), row[2].strip()) for row in rows] == [
        ("database", "sqlite"),
        ("site", "youtube"),
    ]
    assert "youtube.com" in text
    assert "youtu.be" in text
    assert "overrides udl.plugins.sites.youtube:YouTubePlugin" in text
    assert (
        "Local plugins (a single .py file each) are read from /home/me/.config/udl/plugins, "
        "/srv/udl/plugins." in text
    )


def test_every_line_of_a_hint_is_indented() -> None:
    error = NoStrategyFound(
        "Nothing worked", hint="Try this:\n  udl chain set a.com 'x@src'\nOr that."
    )
    batch = DownloadBatch(
        id=uuid.uuid4(), jobs=[job(status=JobStatus.FAILED, error=error.to_info())]
    )

    text = shown(render.batch, batch)

    assert "\n  Try this:\n    udl chain set a.com 'x@src'\n  Or that." in text


# ---------------------------------------------------------------------------------------------
# Tag chains
# ---------------------------------------------------------------------------------------------


def test_a_chain_report_shows_three_of_a_step_s_values_and_counts_the_rest() -> None:
    report = ChainReport(
        url=URL,
        site_key="example.com",
        chain=TagChain.model_validate("a@href | all"),
        ok=True,
        steps=[StepTrace(step="a@href | all", found=[f"/{n}.mp4" for n in range(10)], count=12)],
        media=[MediaCandidate(url=f"{URL}/0.mp4")],
    )

    text = shown(render.chain_report, report)

    assert "/2.mp4" in text
    assert "/3.mp4" not in text
    assert "and 9 more" in text
    assert f"{URL}/0.mp4\n" in text  # (nothing known about it)


def test_suggestions_show_what_did_not_answer_and_streams() -> None:
    def suggestion(url: str, kind: MediaKind, reachable: bool | None) -> ChainSuggestion:
        return ChainSuggestion(
            chain=TagChain.model_validate("video@src"),
            shorthand="video@src",
            media=MediaCandidate(url=url, kind=kind),
            found_on=URL,
            reachable=reachable,
        )

    found = ChainSuggestions(
        url=URL,
        site_key="example.com",
        fetch=FetchMode.BROWSER,
        suggestions=[
            suggestion("https://cdn.example.com/a.m3u8", MediaKind.HLS, None),
            suggestion("https://cdn.example.com/b.mp4", MediaKind.FILE, False),
        ],
        signed_in=True,
    )

    text = shown(render.suggestions, found)

    assert "rendered in the browser, signed in" in text
    assert "hls" in text
    assert "(no answer)" in text


def test_suggestions_for_a_page_that_could_not_be_read() -> None:
    found = ChainSuggestions(
        url=URL,
        site_key="example.com",
        fetch=FetchMode.STATIC,
        suggestions=[],
        error=AuthRequired("Sign in", tool="scraper").to_info(),
    )

    assert "Sign in" in shown(render.suggestions, found)


def test_saved_and_imported_chains() -> None:
    plain = Strategy(site_key="example.com", tool="http", source=StrategySource.MANUAL)
    chained = plain.model_copy(
        update={
            "tool": "browser",
            "chain": TagChain.model_validate("click:a >> video@src"),
            "url_pattern": "^/watch/",
        }
    )

    assert "with http" in shown(render.chain_strategy, plain)
    assert "There were no chains" in shown(render.imported, ImportedChains(strategies=[]))
    imported = shown(render.imported, ImportedChains(strategies=[chained, plain]))
    assert "(URLs matching '^/watch/')" in imported
    assert "click:a >> video@src (in the browser)" in imported
    assert "example.com: http" in imported


# ---------------------------------------------------------------------------------------------
# Any response (a plugin's operation's)
# ---------------------------------------------------------------------------------------------


class _Item(BaseModel):
    name: str
    size: int | None = None


class _Anything(BaseModel):
    items: list[_Item]
    secret: SecretStr
    table: dict[str, int]
    nothing: str | None = None
    words: list[str]


def test_any_response_shows_each_field() -> None:
    found = shown(
        render.model,
        _Anything(
            items=[_Item(name="first", size=3), _Item(name="second")],
            secret=SecretStr("hidden"),
            table={"a": 1},
            words=["x", "y"],
        ),
    )

    assert "first" in found
    assert "Size" in found  # (a list of models is a table)
    assert "hidden" not in found
    assert '{"a": 1}' in found
    assert "x, y" in found


# ---------------------------------------------------------------------------------------------
# Automations
# ---------------------------------------------------------------------------------------------


def _event(**fields: object) -> EventInfo:
    return EventInfo.model_validate(
        {
            "id": uuid.uuid4(),
            "type": "custom.hello",
            "source": "cli",
            "account_id": None,
            "occurred_at": NOW,
            "stored_at": NOW,
            "subject": "Hello",
            "data": {"name": "x"},
            "dedupe_key": None,
            "caused_by": None,
            "depth": 0,
            "status": "pending",
            "dispatched_at": None,
            "dropped_reason": None,
            **fields,
        }
    )


def _run(**fields: object) -> RunDetail:
    return RunDetail.model_validate(
        {
            "id": uuid.uuid4(),
            "action": "hello",
            "plugin": "fake",
            "event_id": None,
            "event_type": None,
            "subscription": None,
            "status": "queued",
            "attempt": 0,
            "next_try_at": None,
            "queued_at": NOW,
            "started_at": None,
            "finished_at": None,
            "summary": "",
            "error": None,
            "skipped_reason": None,
            "output": None,
            "log": "",
            "depth": 0,
            "runner": None,
            **fields,
        }
    )


def test_an_event_says_why_it_was_dropped_and_what_caused_it() -> None:
    run_id = uuid.uuid4()

    text = shown(
        render.event,
        _event(status="dropped", dropped_reason="it is too deep", caused_by=run_id, depth=9),
    )

    assert "it is too deep" in text
    assert f"the run {run_id} (depth 9)" in text


def test_a_run_says_when_it_is_tried_again_why_it_was_skipped_and_what_it_logged() -> None:
    retried = shown(render.run, _run(next_try_at=NOW, attempt=1, log="first line\n"))
    skipped = shown(render.run, _run(status="skipped", skipped_reason="throttled: 1 per 1h"))
    listed = shown(render.runs, RunList(runs=[_run(skipped_reason="throttled: 1 per 1h")]))

    assert "Next try" in retried
    assert "first line" in retried
    assert "throttled: 1 per 1h" in skipped
    assert "throttled: 1 per 1h" in listed


def test_a_subscription_shows_its_source_filter_and_description() -> None:
    info = SubscriptionInfo.model_validate(
        {
            "id": uuid.uuid4(),
            "name": "bank",
            "description": "Bank mail",
            "events": ["gmail.message.received"],
            "actions": ["file"],
            "source_filter": {"query": "has:attachment"},
            "filter_text": None,
            "created_at": NOW,
            "updated_at": NOW,
        }
    )

    text = shown(render.subscription, info)

    assert "Bank mail" in text
    assert "has:attachment" in text
    assert "(every event)" in text


def test_a_subscription_test_without_a_filter_says_its_problems() -> None:
    report = SubscriptionTestReport(
        filter=None, tree=None, problems=["No event type is 'x'."], events=[]
    )

    text = shown(render.subscription_test, report)

    assert "No event type is 'x'." in text
    assert "Where" not in text


def test_an_action_shows_its_description() -> None:
    info = ActionInfo.model_validate(
        {
            "id": uuid.uuid4(),
            "name": "hello",
            "description": "Says hello",
            "plugin": "fake",
            "options_schema": {},
            "used_by": [],
            "created_at": NOW,
            "updated_at": NOW,
        }
    )

    text = shown(render.action, info)

    assert "Says hello" in text
    assert "(no subscription)" in text


def test_the_status_says_who_holds_the_lease_and_the_last_dispatch() -> None:
    elsewhere = RuntimeStatus(
        automations=AutomationState.ELSEWHERE,
        holder="other:1:a",
        lease_expires_at=NOW,
        last_dispatch=NOW,
    )
    off = RuntimeStatus(automations=AutomationState.DISABLED)

    text = shown(render.status, elsewhere)

    assert "other:1:a" in text
    assert "Last dispatch" in text
    assert "Lease until" in text
    assert "disabled" in shown(render.status, off)


def schedule_info(**fields: object) -> ScheduleInfo:
    return ScheduleInfo.model_validate(
        {
            "name": "digest",
            "id": uuid.uuid4(),
            "description": "",
            "spec": {"cron": "0 8 * * *"},
            "spec_text": "0 8 * * *",
            "timezone": None,
            "zone": "Europe/London",
            "data": {},
            "misfire": "skip",
            "misfire_grace": "1h",
            "jitter": "0s",
            "enabled": True,
            "next_run_at": NOW,
            "last_run_at": None,
            "next_times": [NOW],
            "actions": [],
            "subscription": None,
            "created_at": NOW,
            "updated_at": NOW,
            **fields,
        }
    )


def test_a_schedule_says_what_it_does_about_missed_times_and_its_extras() -> None:
    text = shown(
        render.schedule,
        schedule_info(
            description="The morning digest",
            misfire="once",
            misfire_grace="2h",
            jitter="30s",
            last_run_at=NOW,
            data={"report": "daily"},
        ),
    )

    assert "The morning digest" in text
    assert "fire the latest time missed, if within 2h" in text
    assert "up to 30s" in text
    assert "2026-10-23 13:00 BST" in text  # (its last run, in its zone)
    assert '"report": "daily"' in text


def test_a_schedule_in_a_zone_without_a_name_is_shown_in_the_system_s() -> None:
    text = shown(render.schedule, schedule_info(zone="localtime"))

    assert NOW.astimezone().strftime("%Y-%m-%d %H:%M") in text


def test_a_preview_of_a_time_gone_by_says_so() -> None:
    text = shown(
        render.schedule_preview,
        SchedulePreview(
            spec=AtSpec(at=NOW), spec_text="at 2026-10-23T12:00:00+00:00", zone="UTC", times=[]
        ),
    )

    assert "It fires no more." in text
