"""
-------------------------------------
- test_progress_operations.py -
-------------------------------------

The catalogue's progress (27.10): `orch_progress` (a project's burn-up, pace, forecast, features,
models and the tasks merged lately) and `orch_estimate` (one task's size, its estimate and the
time worked on it so far), through the service.

The history is written as a history: five one-point tasks, two hours' work each, merged a day
apart, and one three-point task on the agent's queue — so a point is two hours, the five samples
make that the model's own rate, and the estimate of the queued task is six hours.
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import settings
from support.orch import orch_registry
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    CANCELLED,
    IN_PROGRESS,
    MERGED,
    OPEN,
    Estimate,
    EstimateRequest,
    Progress,
    ProgressRequest,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    Project,
    ProjectRecords,
    StateChange,
    Task,
    TaskRecords,
)

PROGRESS_OPERATIONS: list[str] = ["progress", "estimate"]

NOW = datetime.now(UTC)
"""When the fixture's history ends: its merges are placed relative to now, so the seven-day pace
is the same whenever the test runs (an absolute date drifts out of the window in a week)."""

START = NOW - timedelta(days=10)
"""Ten days back: where the one merge outside the pace's seven days is."""

DAYS_AGO = (10, 3, 2, 1, 0)
"""How long before now each merged task's pull was merged: one older than the week."""


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service over a fresh in-memory database, with five merged one-point tasks (two hours
    each, a day apart) and one three-point task on the agent's queue."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(
            Feature(project="udl", key="26", title="Work tracking", order=26)
        )
        await AgentRecords(records).save_agent(
            Agent(name="deepseek-a", title="deepseek-a", runner="fake", model="m")
        )
        tasks = TaskRecords(records)
        for number, days_ago in enumerate(DAYS_AGO):
            task = await tasks.save_task(
                Task(
                    project="udl",
                    feature="26",
                    key=f"26.{number + 1}",
                    title=f"Merged {number}",
                    size="S",
                    assignee="deepseek-a",
                )
            )
            await tasks.import_task(task.model_copy(update={"state": MERGED}))
            await records.save_all(
                [
                    StateChange(
                        task=task.id,
                        from_state=OPEN,
                        to_state=IN_PROGRESS,
                        at=NOW - timedelta(days=days_ago, hours=3),
                        by="deepseek-a",
                    ),
                    StateChange(
                        task=task.id,
                        from_state=IN_PROGRESS,
                        to_state=MERGED,
                        at=NOW - timedelta(days=days_ago, hours=1),
                        by="deepseek-a",
                    ),
                ]
            )
        await tasks.save_task(
            Task(
                project="udl",
                feature="26",
                key="26.9",
                title="Queued",
                size="M",
                assignee="deepseek-a",
            )
        )
        yield running


def test_orch_offers_the_progress_operations() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert [name for name in offered if name in PROGRESS_OPERATIONS] == PROGRESS_OPERATIONS
    assert offered["progress"].hints == OperationHints(read_only=True)
    assert offered["estimate"].hints == OperationHints(read_only=True)
    assert offered["progress"].request is ProgressRequest
    assert offered["progress"].response is Progress
    assert offered["estimate"].request is EstimateRequest
    assert offered["estimate"].response is Estimate


async def test_the_burn_up_counts_a_day_s_scope_and_what_merged(service: UDLService) -> None:
    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    assert found.project == "udl"
    assert len(found.series) == 31  # (thirty days back, and today)
    assert [one.merged for one in found.series if one.merged] == [1, 1, 1, 1, 1]
    assert found.series[-1].points == 8  # (the five merged, and the three-point one)


async def test_the_whole_of_the_work_is_counted_even_after_the_series_s_start(
    service: UDLService,
) -> None:
    """A window that starts after a merge cannot count it, and the page still must: 29.11 draws
    the points merged of all of them, not of the days it happens to show."""
    found = await service.call(
        "orch_progress", {"project": "udl", "since": NOW - timedelta(days=1)}
    )

    assert isinstance(found, Progress)
    # (Three merges are older than the window, so the series holds the two that are not…)
    assert sum(one.merged for one in found.series) == 2
    # (…and the whole of the work is still what the page is told.)
    assert found.merged == 5
    assert found.points == 8


async def test_the_forecast_is_the_queued_work_at_the_model_s_rate(service: UDLService) -> None:
    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    assert found.models[0].model == "m"
    assert found.models[0].runner == "fake"
    assert found.models[0].tasks == 5
    assert found.models[0].hours_per_point.hours == 2.0
    assert found.models[0].hours_per_point.source == "model"
    assert found.forecast.likely > datetime.now(UTC)
    assert found.forecast.early <= found.forecast.likely <= found.forecast.late
    assert found.forecast.unsized == 0


async def test_a_feature_has_its_points_and_its_own_forecast(service: UDLService) -> None:
    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    (feature,) = found.features
    assert (feature.key, feature.points, feature.merged) == ("26", 8, 5)
    assert feature.forecast is not None
    assert feature.forecast.likely > datetime.now(UTC)


async def test_the_recent_tasks_say_what_they_were_estimated_at_and_took(
    service: UDLService,
) -> None:
    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    assert len(found.recent) == 5
    assert {one.size for one in found.recent} == {"S"}
    assert all(one.estimate == timedelta(hours=2) for one in found.recent)
    assert all(one.actual == timedelta(hours=2) for one in found.recent)
    assert found.estimate_ratio == 1.0


async def test_the_pace_is_the_points_merged_a_day_over_the_last_seven(
    service: UDLService,
) -> None:
    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    # (Four of the five merges fell in the last seven days: the tenth-day-old one is older.)
    assert found.pace == pytest.approx(4 / 7)


async def test_a_since_starts_the_series_earlier_or_later(service: UDLService) -> None:
    found = await service.call(
        "orch_progress", {"project": "udl", "since": (START - timedelta(days=2)).isoformat()}
    )

    assert isinstance(found, Progress)
    assert found.series[0].day.isoformat() == (START - timedelta(days=2)).date().isoformat()


async def test_an_estimate_is_a_task_s_size_and_the_time_worked_on_it(
    service: UDLService,
) -> None:
    found = await service.call("orch_estimate", {"project": "udl", "task": "26.9"})

    assert isinstance(found, Estimate)
    assert (found.size, found.worked) == ("M", timedelta(0))
    assert found.estimate == timedelta(hours=6)


async def test_a_merged_task_s_estimate_says_what_it_really_took(service: UDLService) -> None:
    found = await service.call("orch_estimate", {"project": "udl", "task": "26.1"})

    assert isinstance(found, Estimate)
    assert (found.size, found.worked) == ("S", timedelta(hours=2))
    assert found.estimate == timedelta(hours=2)


async def test_an_unsized_task_is_not_estimated(service: UDLService) -> None:
    records = AppRecords(service.db)
    await TaskRecords(records).save_task(
        Task(project="udl", feature="26", key="26.20", title="Unsized")
    )

    found = await service.call("orch_estimate", {"project": "udl", "task": "26.20"})

    assert isinstance(found, Estimate)
    assert (found.size, found.estimate, found.worked) == (None, None, timedelta(0))


async def test_a_task_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"26\.99"):
        await service.call("orch_estimate", {"project": "udl", "task": "26.99"})


async def test_a_model_with_no_samples_uses_the_project_s_rate(service: UDLService) -> None:
    # (27.10's review: a merged task whose assignee's model has no samples yet is the project's
    # rate, not a key that is not there — and a merged task is in the recent list either way.)
    records = AppRecords(service.db)
    await AgentRecords(records).save_agent(
        Agent(name="fresh", title="fresh", runner="fake", model="brand-new")
    )
    tasks = TaskRecords(records)
    task = await tasks.save_task(
        Task(
            project="udl",
            feature="26",
            key="26.21",
            title="By the new model",
            size="M",
            assignee="fresh",
        )
    )
    await records.save_all(
        [
            StateChange(
                task=task.id,
                from_state=OPEN,
                to_state=IN_PROGRESS,
                at=START,
                by="fresh",
            ),
            StateChange(
                task=task.id,
                from_state=IN_PROGRESS,
                to_state=MERGED,
                at=START + timedelta(hours=3),
                by="fresh",
            ),
        ]
    )
    await tasks.import_task(task.model_copy(update={"state": MERGED}))

    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    # (Its three points at the project's two hours a point: the six of the five samples.)
    recent = next(one for one in found.recent if one.key == "26.21")
    assert recent.estimate == timedelta(hours=6)


async def test_a_feature_s_points_leave_out_a_cancelled_task(service: UDLService) -> None:
    records = AppRecords(service.db)
    tasks = TaskRecords(records)
    dropped = await tasks.save_task(
        Task(project="udl", feature="26", key="26.22", title="Dropped", size="M")
    )
    await tasks.import_task(dropped.model_copy(update={"state": CANCELLED}))

    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    (feature,) = found.features
    assert feature.points == 8, "the five merged and the queued three-point task, not the cancelled"
    assert feature.merged == 5


async def test_an_imported_merged_task_counts_as_merged(service: UDLService) -> None:
    # (An import writes no move, so a plan's `[x]` has no `MERGED` change: it is merged all the
    # same, and the feature's merged points say so.)
    records = AppRecords(service.db)
    await FeatureRecords(records).save_feature(
        Feature(project="udl", key="27", title="Agents", order=27)
    )
    tasks = TaskRecords(records)
    imported = await tasks.save_task(
        Task(project="udl", feature="27", key="27.1", title="From the plan", size="M")
    )
    await tasks.import_task(imported.model_copy(update={"state": MERGED}))

    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    feature = next(one for one in found.features if one.key == "27")
    assert (feature.points, feature.merged) == (3, 3)
    assert feature.forecast is None, "nothing is left of it"


async def test_a_task_that_ran_past_its_estimate_has_nothing_left(service: UDLService) -> None:
    # (27.10's review: `left_of` clamps at nothing, and nothing must not read as "not known" —
    # an overrun task cannot push the forecast out by its whole estimate again.)
    records = AppRecords(service.db)
    await ProjectRecords(records).save_project(Project(name="solo", title="Solo"))
    await FeatureRecords(records).save_feature(
        Feature(project="solo", key="1", title="One", order=1)
    )
    tasks = TaskRecords(records)
    overrun = await tasks.save_task(
        Task(
            project="solo",
            feature="1",
            key="1.1",
            title="Overrun",
            size="M",
            assignee="deepseek-a",
        )
    )
    await tasks.import_task(overrun.model_copy(update={"state": IN_PROGRESS}))
    await records.save_all(
        [
            StateChange(
                task=overrun.id,
                from_state=OPEN,
                to_state=IN_PROGRESS,
                at=datetime.now(UTC) - timedelta(hours=10),
                by="deepseek-a",
            )
        ]
    )

    found = await service.call("orch_progress", {"project": "solo"})

    assert isinstance(found, Progress)
    assert found.forecast.likely <= datetime.now(UTC) + timedelta(minutes=1)


async def test_an_imported_merge_is_not_a_recent_task(service: UDLService) -> None:
    # (Its history says nothing of hours, so it is no sample and no recent task: it would
    # otherwise read as zero hours of work, and drag `estimate_ratio` to nothing.)
    records = AppRecords(service.db)
    await FeatureRecords(records).save_feature(
        Feature(project="udl", key="27", title="Agents", order=27)
    )
    tasks = TaskRecords(records)
    imported = await tasks.save_task(
        Task(project="udl", feature="27", key="27.1", title="From the plan", size="M")
    )
    await tasks.import_task(imported.model_copy(update={"state": MERGED}))

    found = await service.call("orch_progress", {"project": "udl"})

    assert isinstance(found, Progress)
    assert all(one.key != "27.1" for one in found.recent)
    assert found.estimate_ratio == 1.0, "the five worked tasks, not an imported one at zero"
