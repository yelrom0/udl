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
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

# Package Imports
import pytest
from pydantic import BaseModel, SecretStr
from rich.console import Console

# Local Imports
from udl.automations.events import EventStatus
from udl.automations.models import (
    ActionInfo,
    EventInfo,
    EventList,
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
from udl.db.models import JobStatus, JsonObject, Site, Strategy, StrategySource
from udl.errors import AuthRequired, ErrorCode, ErrorInfo, NoStrategyFound, PluginError
from udl.integrations.models import AccountInfo
from udl.integrations.watchers import WatcherState, WatcherStatus
from udl.interfaces.cli import render
from udl.interfaces.cli.common import show
from udl.plugins.apps.orch.models import (
    Action,
    AgentLine,
    AgentRole,
    AgentStatus,
    FeatureInfo,
    Inbox,
    InboxRequest,
    MergeList,
    MergeRow,
    Question,
    agent_line,
)
from udl.plugins.downloaders.base import (
    DownloadProgress,
    Handoff,
    MediaCandidate,
    MediaKind,
    ProbeResult,
)
from udl.plugins.integrations.base import Account, AccountHealth, AccountStatus, ConnectMethod
from udl.plugins.registry import PluginProblem, PluginSource
from udl.plugins.requirements import Requirement

URL = "https://example.com/watch"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def shown[M](render_it: Callable[[Console, M], None], model: M) -> str:
    """What `render_it` shows of `model`."""
    output = io.StringIO()
    render_it(Console(file=output, width=200, color_system=None), model)
    return output.getvalue()


def shown_at_80[M](render_it: Callable[[Console, M], None], model: M) -> str:
    """What `render_it` shows of `model` at 80 columns (a terminal's own width)."""
    output = io.StringIO()
    render_it(Console(file=output, width=80, color_system=None), model)
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
                kind="udl.integrations",
                name="gmail",
                source=PluginSource.BUILTIN,
                origin="udl.plugins.integrations.gmail:Gmail",
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
        line.split("│")
        for line in text.splitlines()
        if any(origin in line for origin in ("sqlite:", "gmail:", "youtube.py"))
    ]

    assert "Type" in text
    assert [(row[1].strip(), row[2].strip()) for row in rows] == [
        ("database", "sqlite"),
        ("integration", "gmail"),
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


def test_the_status_says_how_each_watcher_is() -> None:
    failing = WatcherStatus(
        account="gmail:work",
        account_id=uuid.uuid4(),
        integration="gmail",
        state=WatcherState.BACKOFF,
        failures=3,
        last_error=ErrorInfo(code=ErrorCode.NETWORK_ERROR, message="Gmail is down"),
    )
    running = WatcherStatus(
        account="slack:acme",
        account_id=uuid.uuid4(),
        integration="slack",
        state=WatcherState.RUNNING,
        last_event_at=NOW,
    )

    text = shown(
        render.status,
        RuntimeStatus(automations=AutomationState.RUNNING, watchers=[failing, running]),
    )

    assert "gmail:work  backoff (failed 3 times: Gmail is down)" in text
    assert "slack:acme  running, last event" in text


def test_an_account_says_what_is_wrong_with_it_and_its_watcher() -> None:
    account = Account(
        integration="gmail",
        name="work",
        method=ConnectMethod.OAUTH,
        status=AccountStatus.PENDING,
        enabled=False,
        pending_until=NOW,
        last_error=ErrorInfo(code=ErrorCode.NOT_ALLOWED, message="Refused"),
    )
    watcher = WatcherStatus(
        account="gmail:work",
        account_id=account.id,
        integration="gmail",
        state=WatcherState.STOPPED,
    )

    text = shown(render.account, AccountInfo.of(account, watcher=watcher))

    for said in ("pending", "Enabled", "Until", "Refused", "gmail:work  stopped"):
        assert said in text


def test_an_account_s_health_says_its_problems_and_what_it_was_granted() -> None:
    sick = shown(render.account_health, AccountHealth(ok=False, problems=["It expired"]))
    well = shown(render.account_health, AccountHealth(ok=True, scopes=["a.read", "a.send"]))

    assert "It expired" in sick
    assert "a.read a.send" in well


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


# ---------------------------------------------------------------------------------------------
# 28.4: the inbox, in the three sections a person reads
# ---------------------------------------------------------------------------------------------


def _orch_event(one: UUID) -> EventInfo:
    """An `orch.*` event, as `list_events` answers it."""
    return EventInfo(
        id=one,
        type="orch.task.changed",
        source="udl",
        account_id=None,
        occurred_at=NOW,
        stored_at=NOW,
        subject="26.4 ready",
        data={},
        dedupe_key=None,
        caused_by=None,
        depth=0,
        status=EventStatus.DISPATCHED,
        dispatched_at=NOW,
        dropped_reason=None,
    )


def _inbox(**fields: object) -> Inbox:
    return Inbox.model_validate(
        {
            "project": "udl",
            "merge": MergeList(
                ready=[
                    MergeRow(
                        project="udl",
                        task="27.10",
                        feature="27",
                        title="Progress",
                        repo="yelrom0/udl",
                        number=72,
                        url="https://github.com/yelrom0/udl/pull/72",
                        sha="0b06c41",
                        state="ready",
                    ),
                    MergeRow(
                        project="udl",
                        task="26.4",
                        feature="26",
                        title="Features",
                        repo="yelrom0/udl",
                        number=70,
                        url="https://github.com/yelrom0/udl/pull/70",
                        sha="d5d5584",
                        state="ready",
                        blockers=["its checks failed"],
                    ),
                ],
                held=[
                    MergeRow(
                        project="udl",
                        task="26.9",
                        feature="26",
                        title="Held one",
                        repo="yelrom0/udl",
                        number=73,
                        url="https://github.com/yelrom0/udl/pull/73",
                        sha="aaaaaaa",
                        state="ready",
                        hold=True,
                    )
                ],
                coming=[
                    MergeRow(
                        project="udl",
                        task="26.8",
                        feature="26",
                        title="Sizes",
                        repo="yelrom0/udl",
                        number=69,
                        url="https://github.com/yelrom0/udl/pull/69",
                        sha="0c4f331",
                        state="in_progress",
                    )
                ],
                done=[],
            ),
            "questions": [
                Question(
                    key="Q-1",
                    project="udl",
                    asked_by="deepseek-a",
                    title="Which theme?",
                    blocking=True,
                    task="26.4",
                ),
                Question(key="Q-2", project="udl", title="A later one"),
            ],
            "actions": [
                Action(key="A-1", project="udl", title="Merge 27.10", task="27.10"),
            ],
            "agents": [
                AgentLine(
                    name="deepseek-a",
                    title="deepseek-a",
                    role=AgentRole.IMPLEMENTER,
                    state=AgentStatus.WORKING,
                    task="28.4",
                    line=agent_line(
                        "deepseek-a", AgentRole.IMPLEMENTER, AgentStatus.WORKING, "28.4"
                    ),
                ),
                AgentLine(
                    name="claude",
                    title="Claude",
                    role=AgentRole.MANAGER,
                    state=AgentStatus.IDLE,
                    line=agent_line("claude", AgentRole.MANAGER, AgentStatus.IDLE, None),
                ),
            ],
            "updated_at": NOW,
            **fields,
        }
    )


def test_the_inbox_s_three_sections_and_its_agents() -> None:
    said = shown(render.inbox, _inbox())

    assert "Ready to merge" in said
    assert "27.10  Progress  https://github.com/yelrom0/udl/pull/72" in said
    assert "26.4  Features  https://github.com/yelrom0/udl/pull/70  (its checks failed)" in said
    assert "held 26.9  Held one" in said
    assert "Coming: 26.8" in said
    assert "blocking  Q-1  Which theme?  (26.4)  asked by deepseek-a" in said
    assert "Q-2  A later one  asked by udl" in said
    assert "A-1  now  Merge 27.10  (27.10)" in said
    assert "deepseek-a — implementing 28.4" in said
    assert "claude — idle" in said


def test_an_empty_inbox_says_so_in_each_section() -> None:
    said = shown(render.inbox, _inbox(questions=[], actions=[], agents=[]))

    assert "Nothing waits for a merge." not in said  # (its ready rows are there)
    assert "Nothing is being asked." in said
    assert "Nothing is only yours to do." in said
    assert "No agents." in said


def test_an_inbox_with_nothing_in_it_says_so_everywhere() -> None:
    said = shown(
        render.inbox,
        _inbox(merge=MergeList(), questions=[], actions=[], agents=[]),
    )

    assert "Nothing waits for a merge." in said
    assert "Nothing is being asked." in said


async def test_the_watch_draws_it_and_again_when_a_new_event_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(28.4's `--watch`: one draw at once, then one for each new `orch.*` event *stored* — the
    events are in the database, so a watch in the CLI's own process hears what `udl serve`
    published, which a listener in this process would never hear.)"""
    output = io.StringIO()
    events: list[UUID] = [uuid.uuid4()]  # (one already stored: the first look finds it)
    calls: list[tuple[str, dict[str, object]]] = []

    class _Source:
        looks = 0

        async def call(self, name: str, request: JsonObject) -> BaseModel:
            calls.append((name, dict(request)))
            if name != "list_events":
                return _inbox()
            self.looks += 1
            if (
                self.looks == 4
            ):  # (three draws is the whole test: the watch then ends as Ctrl-C does)
                raise asyncio.CancelledError
            if self.looks in (2, 3):  # (a new event is stored between the looks)
                events.append(uuid.uuid4())
            return EventList(events=[_orch_event(events[-1])])

    monkeypatch.setattr(render, "WATCH_POLL", 0.0)
    console = Console(file=output, width=200, color_system=None)

    with pytest.raises(asyncio.CancelledError):
        await render.watch_inbox(console, _Source(), InboxRequest(project="udl"))

    assert [name for name, _ in calls].count("orch_inbox") == 3, "once, then once per new event"
    assert output.getvalue().count("Ready to merge") == 3
    name, asked = calls[0]
    assert (name, asked["type"], asked["limit"]) == ("list_events", "orch.*", 1)
    assert "since" in asked, "the poll reads what was stored while the watch has run"


async def test_the_watch_says_why_and_keeps_going_when_the_inbox_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (GitHub unreachable, say: a dashboard that stops is no use, so it says why, and draws on
    # as soon as the next event gives it a reason to.)
    output = io.StringIO()
    drawn = 0

    class _Source:
        looks = 0

        async def call(self, name: str, request: JsonObject) -> BaseModel:
            nonlocal drawn
            if name != "list_events":
                drawn += 1
                if drawn == 1:
                    raise PluginError("GitHub did not answer")
                return _inbox()
            self.looks += 1
            if self.looks == 3:  # (the second draw is the test's end)
                raise asyncio.CancelledError
            return EventList(events=[] if self.looks == 1 else [_orch_event(uuid.uuid4())])

    monkeypatch.setattr(render, "WATCH_POLL", 0.0)
    console = Console(file=output, width=200, color_system=None)

    with pytest.raises(asyncio.CancelledError):
        await render.watch_inbox(console, _Source(), InboxRequest(project="udl"))

    assert "GitHub did not answer" in output.getvalue()
    assert "Watching: it draws again when something changes." in output.getvalue()
    assert "Ready to merge" in output.getvalue(), "and then it drew the inbox it could read"


async def test_an_outage_that_lasts_across_a_draw_is_said_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(The refresh draws the inbox and clears the screen: if the events still cannot be read,
    that is said again — a clean inbox with no word of the outage would look like a good one.)"""
    output = io.StringIO()
    drawn = 0

    class _Source:
        async def call(self, name: str, request: JsonObject) -> BaseModel:
            nonlocal drawn
            if name == "list_events":
                raise PluginError("the events cannot be read")
            drawn += 1
            if drawn == 3:
                raise asyncio.CancelledError
            return _inbox()

    monkeypatch.setattr(render, "WATCH_POLL", 0.0)
    monkeypatch.setattr(render, "WATCH_REFRESH", 0.0)  # (every look redraws: one outage, drawn)
    console = Console(file=output, width=200, color_system=None)

    with pytest.raises(asyncio.CancelledError):
        await render.watch_inbox(console, _Source(), InboxRequest(project="udl"))

    assert output.getvalue().count("the events cannot be read") == 2, "once per draw"


async def test_the_watch_says_a_second_outage_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """(An outage is said once; when the events can be read again and then cannot, that is a new
    outage and it is said again — a watch that went quiet would look like one that is fine.)"""
    output = io.StringIO()
    looks = 0

    class _Source:
        async def call(self, name: str, request: JsonObject) -> BaseModel:
            nonlocal looks
            if name != "list_events":
                return _inbox()
            looks += 1
            if looks == 4:
                raise asyncio.CancelledError
            if looks == 2:  # (one look that reads them, between the two outages)
                return EventList(events=[])
            raise PluginError("the events cannot be read")

    monkeypatch.setattr(render, "WATCH_POLL", 0.0)
    monkeypatch.setattr(render, "WATCH_REFRESH", 0.0)
    console = Console(file=output, width=200, color_system=None)

    with pytest.raises(asyncio.CancelledError):
        await render.watch_inbox(console, _Source(), InboxRequest(project="udl"))

    assert output.getvalue().count("the events cannot be read") == 2


async def test_the_watch_says_an_outage_once_and_draws_anyway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(The events cannot be read at all — the first look included: it says so once, draws the
    inbox it can, and does not add a line every two seconds for as long as the outage lasts.)"""
    output = io.StringIO()
    looks = 0

    class _Source:
        async def call(self, name: str, request: JsonObject) -> BaseModel:
            nonlocal looks
            if name != "list_events":
                return _inbox()
            looks += 1
            if looks == 5:  # (five looks, one outage, no draw between them)
                raise asyncio.CancelledError
            raise PluginError("the events cannot be read")

    monkeypatch.setattr(render, "WATCH_POLL", 0.0)
    console = Console(file=output, width=200, color_system=None)

    with pytest.raises(asyncio.CancelledError):
        await render.watch_inbox(console, _Source(), InboxRequest(project="udl"))

    assert output.getvalue().count("the events cannot be read") == 1
    assert output.getvalue().count("Ready to merge") == 1, "and it drew the inbox it could read"


async def test_the_watch_draws_it_again_on_its_timer_when_no_event_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(Not every change publishes an event — an ask made over the API, a task moved by hand —
    so the watch draws again when its refresh is up, whether or not anything was published.)"""
    output = io.StringIO()
    drawn = 0

    class _Source:
        async def call(self, name: str, request: JsonObject) -> BaseModel:
            nonlocal drawn
            if name == "list_events":
                return EventList(events=[])  # (nothing was published at all)
            drawn += 1
            if drawn == 3:  # (two draws are the whole test)
                raise asyncio.CancelledError
            return _inbox()

    monkeypatch.setattr(render, "WATCH_POLL", 0.0)
    monkeypatch.setattr(render, "WATCH_REFRESH", 0.0)
    console = Console(file=output, width=200, color_system=None)

    with pytest.raises(asyncio.CancelledError):
        await render.watch_inbox(console, _Source(), InboxRequest(project="udl"))

    assert output.getvalue().count("Ready to merge") == 2


# ---------------------------------------------------------------------------------------------
# A feature at 80 columns (26.6)
# ---------------------------------------------------------------------------------------------


def _feature_info(**fields: object) -> FeatureInfo:
    """A feature with two tasks, as `orch_get_feature` answers with it."""
    return FeatureInfo.model_validate(
        {
            "feature": {
                "project": "udl",
                "key": "26",
                "title": "Work tracking",
                "spec": "The phase's own words.",
                "status": "active",
                "order": 26,
            },
            "totals": {"points": 5, "points_merged": 2, "points_ready": 1, "unsized": 1},
            "tasks": [
                {
                    "project": "udl",
                    "feature": "26",
                    "key": "26.1",
                    "title": "Projects and repos, with a long title that will not fit in a column",
                    "spec": "The card's first bullet.\nThe card's second bullet.",
                    "state": "merged",
                    "size": "M",
                    "assignee": "deepseek-a",
                },
                {
                    "project": "udl",
                    "feature": "26",
                    "key": "26.2",
                    "title": "Features and tasks",
                    "spec": "Its own spec.",
                    "state": "in_progress",
                    "size": "S",
                },
            ],
            **fields,
        }
    )


def test_a_feature_shows_its_tasks_in_five_columns() -> None:
    """At 80 columns a task's row is key, state, title, size and assignee — not all seventeen
    fields, where every cell is `…`. The row itself is checked, not the whole output."""
    said = shown_at_80(render.feature, _feature_info())

    row = next(line for line in said.splitlines() if "26.1" in line)
    assert "26.1" in row
    assert "merged" in row
    assert "deepseek-a" in row
    assert "…" not in row
    # (The second task is in progress: its state is not cut to a `…` either.)
    other = next(line for line in said.splitlines() if "26.2" in line)
    assert "in_progress" in other
    assert "…" not in other


def test_a_feature_shows_every_task_s_spec_below_the_table() -> None:
    """Each task's spec is printed under its own key, so `features show` still prints every spec
    (Phase 26's done-when)."""
    said = shown_at_80(render.feature, _feature_info())

    table, _, below = said.partition("26.2")
    assert "The card's first bullet." in below
    assert "The card's second bullet." in below
    assert "Its own spec." in below
    # (The table itself carries no spec: it is the five columns.)
    assert "The card's first bullet." not in table


def test_a_feature_as_json_is_the_operations_own_answer(capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` prints the operation's own answer — every field — not the narrow view."""
    show(_feature_info(), True, render.feature)

    said = json.loads(capsys.readouterr().out)
    assert said["tasks"][0]["spec"].startswith("The card's first bullet.")
    assert set(said["tasks"][0]) >= {"key", "title", "spec", "state", "size", "assignee", "needs"}


def test_a_feature_with_no_tasks_says_so() -> None:
    said = shown_at_80(render.feature, _feature_info(tasks=[]))

    assert "No tasks." in said
    assert "Work tracking" in said


def test_the_phase_s_own_words_are_shown_under_its_title() -> None:
    said = shown_at_80(render.feature, _feature_info())

    assert "The phase's own words." in said


def test_a_feature_s_links_and_notes_are_shown() -> None:
    """`features show` used to print every `FeatureInfo` field: its links (pulls, issues) and its
    notes are part of what a person reads, so the narrow view keeps them."""
    said = shown_at_80(
        render.feature,
        _feature_info(
            links=[
                {
                    "owner_kind": "feature",
                    "owner": "26",
                    "link_kind": "pull",
                    "url": "https://github.com/yelrom0/udl/pull/72",
                    "title": "Progress",
                }
            ],
            notes=[
                {
                    "note_kind": "decision",
                    "text": "We do it this way.",
                    "author": "claude",
                    "at": NOW.isoformat(),
                }
            ],
        ),
    )

    assert "Links" in said
    assert "https://github.com/yelrom0/udl/pull/72" in said
    assert "Progress" in said
    assert "Notes" in said
    assert "We do it this way." in said
    assert "claude" in said


def test_a_feature_without_links_or_notes_says_nothing_of_them() -> None:
    said = shown_at_80(render.feature, _feature_info())

    assert "Links" not in said
    assert "Notes" not in said


def test_a_feature_with_no_totals_says_only_the_points() -> None:
    """A feature whose tasks have no sizes: `points: 0`, and none of the zero parts."""
    said = shown_at_80(render.feature, _feature_info(totals={}))

    first = said.splitlines()[0]
    assert "points: 0" in first
    assert "merged" not in first
    assert "ready" not in first
    assert "unsized" not in first


def test_an_unsized_task_shows_a_dash_and_a_spec_less_one_prints_nothing() -> None:
    """The size cell is `-` when a task has no size, and a task with no spec gets no block."""
    said = shown_at_80(
        render.feature,
        _feature_info(
            tasks=[
                {
                    "project": "udl",
                    "feature": "26",
                    "key": "26.3",
                    "title": "Unsized",
                    "state": "open",
                }
            ]
        ),
    )

    row = next(line for line in said.splitlines() if "26.3" in line)
    cells = [cell.strip() for cell in row.strip("│").split("│")]
    assert cells[3] == "-"  # (its size)
    assert cells[4] == "-"  # (nobody's)
    assert said.count("26.3") == 1  # (its row only: no empty spec block)


def test_a_feature_s_points_are_on_its_own_line() -> None:
    """What its tasks come to (26.8): the points are read beside the feature's title, so a
    `features show` still says what a feature is worth without reading every row."""
    said = shown_at_80(render.feature, _feature_info())

    first = said.splitlines()[0]
    assert "points: 5" in first
    assert "2 merged" in first
    assert "1 ready" in first
    assert "1 unsized" in first
