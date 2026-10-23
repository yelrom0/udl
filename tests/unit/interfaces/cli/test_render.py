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
from rich.console import Console

# Local Imports
from udl.core.models import (
    Attempt,
    CandidateSource,
    DownloadBatch,
    DownloadList,
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
    ProbeResult,
    Requirement,
)
from udl.plugins.registry import PluginProblem, PluginSource

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
    )

    text = shown(render.plugins, listed)

    assert "curl (binary): pacman -S curl" in text
    assert "Invalid options" in text
    assert "Could not load broken.py: SyntaxError" in text
