"""
------------------
- test_models.py -
------------------

The domain models every database plugin stores: Site, Strategy, DownloadRecord
"""

# System Imports
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest
from pydantic import BaseModel, ValidationError

# Local Imports
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    JobStatus,
    Outcome,
    Site,
    SiteQuery,
    Strategy,
    StrategySource,
    counted,
    strategy_order,
)
from udl.errors import AuthRequired, ErrorCode

NOW = datetime(2026, 10, 23, 12, 30, tzinfo=UTC)


def make_strategy(**fields: object) -> Strategy:
    return Strategy.model_validate(
        {"site_key": "example.com", "tool": "yt-dlp", "source": "probe", **fields}
    )


def test_new_records_get_a_random_uuid_and_utc_timestamps() -> None:
    before = datetime.now(UTC)
    first, second = Site(key="example.com"), Site(key="example.com")

    assert isinstance(first.id, uuid.UUID)
    assert first.id != second.id
    assert before <= first.created_at <= datetime.now(UTC)
    assert first.created_at.tzinfo is UTC
    assert first.created_at <= first.updated_at <= datetime.now(UTC)


def test_strategy_defaults() -> None:
    strategy = make_strategy()

    assert strategy.url_pattern is None  # the whole site
    assert strategy.options == {}
    assert (strategy.priority, strategy.enabled, strategy.requires_auth) == (0, True, False)
    assert (strategy.success_count, strategy.failure_count, strategy.consecutive_failures) == (
        0,
        0,
        0,
    )
    assert strategy.last_success_at is None
    assert strategy.last_error is None


def test_a_download_starts_queued_with_no_files() -> None:
    record = DownloadRecord(url="https://example.com/v/1", site_key="example.com")

    assert record.status is JobStatus.QUEUED
    assert (record.files, record.size_bytes, record.finished_at) == ([], 0, None)
    assert record.strategy_id is None
    assert record.tool is None


def test_a_strategy_must_say_where_it_came_from() -> None:
    with pytest.raises(ValidationError, match="source"):
        Strategy(site_key="example.com", tool="yt-dlp")  # pyright: ignore[reportCallIssue]


def test_url_pattern_must_be_a_valid_regex() -> None:
    with pytest.raises(ValidationError, match=re.escape("not a valid regular expression")):
        make_strategy(url_pattern="^/watch/(")


@pytest.mark.parametrize(
    "fields",
    [
        {"tool": ""},
        {"site_key": ""},
        {"success_count": -1},
        {"failure_count": -1},
        {"consecutive_failures": -1},
    ],
)
def test_invalid_strategy_fields_are_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_strategy(**fields)


def test_timestamps_must_have_a_timezone() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Site(key="example.com", created_at=datetime(2026, 1, 1))


def test_options_must_be_json() -> None:
    assert make_strategy(options={"format": "bv*+ba/b", "retries": [1, 2]}).options == {
        "format": "bv*+ba/b",
        "retries": [1, 2],
    }
    with pytest.raises(ValidationError):
        make_strategy(options={"when": NOW})


@pytest.mark.parametrize(
    "model",
    [
        Site(
            key="example.com",
            name="Example",
            default_output_dir=Path("/media/example"),
            notes="needs a login",
        ),
        make_strategy(
            url_pattern="^/shorts/",
            options={"format": "best"},
            requires_auth=True,
            source=StrategySource.MANUAL,
            priority=-1,
            success_count=3,
            failure_count=1,
            consecutive_failures=1,
            last_success_at=NOW,
            last_error=AuthRequired("Sign in", tool="yt-dlp").to_info(),
            enabled=False,
        ),
        DownloadRecord(
            url="https://example.com/v/1",
            site_key="example.com",
            strategy_id=uuid.uuid4(),
            tool="http",
            status=JobStatus.FINISHED,
            output_dir=Path("/media"),
            files=[Path("/media/a.mp4"), Path("/media/b.mp4")],
            size_bytes=1234,
            finished_at=NOW,
        ),
    ],
    ids=["site", "strategy", "download"],
)
def test_models_round_trip_through_json(model: BaseModel) -> None:
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_strategies_are_ordered_by_priority_then_most_successes() -> None:
    later = make_strategy(priority=1, success_count=100)
    proven = make_strategy(priority=0, success_count=10)
    unproven = make_strategy(priority=0, success_count=0)

    assert sorted([unproven, later, proven], key=strategy_order) == [proven, unproven, later]


def test_equal_strategies_are_ordered_by_id_so_every_database_agrees() -> None:
    a, b = make_strategy(), make_strategy()

    assert sorted([a, b], key=strategy_order) == sorted([a, b], key=lambda s: s.id)


def test_an_outcome_happens_now_unless_told_otherwise() -> None:
    before = datetime.now(UTC)

    outcome = Outcome(ok=True)

    assert before <= outcome.at <= before + timedelta(seconds=5)
    assert outcome.error is None


def test_a_failed_outcome_carries_the_error() -> None:
    error = AuthRequired("Sign in", tool="yt-dlp").to_info()

    assert Outcome(ok=False, error=error).error == error
    assert error.code is ErrorCode.AUTH_REQUIRED


def test_a_success_counts_on_a_copy_of_the_strategy() -> None:
    strategy = make_strategy(success_count=2, failure_count=1, consecutive_failures=1)

    assert counted(strategy, Outcome(ok=True, at=NOW)) == strategy.model_copy(
        update={"success_count": 3, "consecutive_failures": 0, "last_success_at": NOW}
    )
    assert strategy.success_count == 2


def test_a_failure_counts_on_a_copy_of_the_strategy() -> None:
    strategy = make_strategy(success_count=2, last_success_at=NOW)
    error = AuthRequired("Sign in").to_info()

    assert counted(strategy, Outcome(ok=False, error=error)) == strategy.model_copy(
        update={"failure_count": 1, "consecutive_failures": 1, "last_error": error}
    )


def test_queries_default_to_everything() -> None:
    assert SiteQuery() == SiteQuery(search=None, limit=None, offset=0)
    assert DownloadQuery() == DownloadQuery(site_key=None, status=None, limit=None, offset=0)


@pytest.mark.parametrize("fields", [{"limit": 0}, {"offset": -1}])
def test_query_bounds_are_checked(fields: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        SiteQuery.model_validate(fields)
    with pytest.raises(ValidationError):
        DownloadQuery.model_validate(fields)
