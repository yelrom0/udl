"""
------------------
- test_models.py -
------------------

The models the engine and the service hand out
"""

# System Imports
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Local Imports
from udl.core.models import (
    Attempt,
    CandidateSource,
    DownloadRequest,
    DownloadSpec,
    JobInfo,
    SetStrategyRequest,
    StrategySpec,
)
from udl.db.models import DownloadRecord, JobStatus
from udl.errors import NetworkError

T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 23, 12, 5, tzinfo=UTC)


def test_a_job_is_saved_as_a_download_record() -> None:
    job = JobInfo(
        id=uuid.uuid4(),
        batch_id=uuid.uuid4(),
        url="https://example.com/a",
        site_key="example.com",
        status=JobStatus.FINISHED,
        tool="yt-dlp",
        handed_off_to="http",
        strategy_id=uuid.uuid4(),
        output_dir=Path("/media"),
        files=[Path("/media/a.mp4")],
        size_bytes=10,
        title="A",
        attempts=[Attempt(tool="yt-dlp", source=CandidateSource.PROBE)],
        started_at=T0,
        finished_at=T1,
    )

    assert job.record() == DownloadRecord(
        id=job.id,
        url="https://example.com/a",
        site_key="example.com",
        strategy_id=job.strategy_id,
        tool="yt-dlp",
        status=JobStatus.FINISHED,
        output_dir=Path("/media"),
        files=[Path("/media/a.mp4")],
        size_bytes=10,
        started_at=T0,
        finished_at=T1,
    )


def test_a_download_record_is_a_job_from_the_history() -> None:
    record = DownloadRecord(
        url="https://example.com/a",
        site_key="example.com",
        status=JobStatus.FAILED,
        started_at=T0,
        finished_at=T1,
        error=NetworkError("reset", tool="http").to_info(),
    )

    job = JobInfo.from_record(record)

    assert job.batch_id is None  # not run by this udl
    assert job.record() == record
    assert job.attempts == []


def test_a_download_request_is_what_to_download_and_how_long_to_wait() -> None:
    # (The API takes what to download as the body, and the waiting as query parameters.)
    assert issubclass(DownloadRequest, DownloadSpec)
    assert set(DownloadRequest.model_fields) - set(DownloadSpec.model_fields) == {"wait", "timeout"}


def test_setting_a_strategy_is_the_strategy_and_its_site() -> None:
    # (The API takes the site from the path, and the strategy as the body.)
    assert issubclass(SetStrategyRequest, StrategySpec)
    assert set(SetStrategyRequest.model_fields) - set(StrategySpec.model_fields) == {"site"}
