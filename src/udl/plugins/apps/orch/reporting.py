"""
------------------
- reporting.py -
------------------

27.10's numbers, gathered from orch's records: a project's progress (`orch_progress`) and one
task's estimate (`orch_estimate`).

The arithmetic is `timing`'s, `estimates`' and `progress`'s, all pure; this is where the records
are read into their shapes — each task's history and waits, each agent's model and its hours a
day, each feature's points — and the answers are put together.
"""

# System Imports
import statistics
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

# Local Imports
from udl.errors import DoesNotExist
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.estimates import (
    Rate,
    Sample,
    estimate,
    hours_per_point,
    points_of,
    spread,
)
from udl.plugins.apps.orch.models import (
    CANCELLED,
    DONE,
    IN_PROGRESS,
    MERGED,
    POINTS,
    Estimate,
    FeatureProgress,
    ForecastInfo,
    ModelRow,
    Progress,
    RateInfo,
    RecentTask,
    SeriesPoint,
    as_shape,
)
from udl.plugins.apps.orch.models import Task as Shaped
from udl.plugins.apps.orch.progress import (
    DEFAULT_HOURS,
    Card,
    Forecast,
    Load,
    finishes,
    pace,
    series,
)
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    ProjectRecords,
    StateChange,
    Task,
    TaskRecords,
    Wait,
)
from udl.plugins.apps.orch.timing import by_agent, working_time

CLOSED: Final[tuple[str, ...]] = (MERGED, DONE, CANCELLED)
"""The states a task is no longer open in."""

DAYS: Final = 7
"""The days an agent's hours a day (and the pace) are learned over."""

RECENT: Final = 20
"""How many merged tasks a progress page shows."""

SPAN: Final = 30
"""The days a series covers when a caller names no `since`."""

FEWEST: Final = 1.0
"""The hours a day below which an agent's own rate is not used."""

type Rates = Mapping[str | None, Rate]
"""What a point costs, by model (`None`: the model of an agent nobody knows)."""

type Spreads = Mapping[str | None, tuple[float, float]]
"""How far the actual hours fell from a rate, by model: its 25th and 75th percentile."""


class Reporting:
    """A project's progress, gathered from its records."""

    def __init__(self, records: AppRecords, *, now: datetime) -> None:
        self._records = records
        self._now = now

    async def progress(self, project: str, *, since: datetime | None = None) -> Progress:
        """The project's burn-up, pace, forecast, features, models and recent tasks."""
        await ProjectRecords(self._records).require(project)
        tasks = await TaskRecords(self._records).tasks(project)
        changes = await self._changes()
        waits = await self._waits()
        agents = {agent.name: agent for agent in await AgentRecords(self._records).agents()}
        history = {task.id: changes.get(task.id, []) for task in tasks}
        rests = {task.id: waits.get(task.id, []) for task in tasks}
        samples = self._samples(tasks, history, rests, agents)
        rates = {model: hours_per_point(samples, model=model) for model in _models(samples)}
        spreads = {model: spread(_mine(samples, model), rate=rate) for model, rate in rates.items()}
        hours_a_day = self._hours_a_day(changes, waits, agents)
        merged = {task.id: _when(history[task.id], MERGED) for task in tasks}
        cards = [
            Card(
                points=points_of(task.size) or 0,
                created_at=task.created_at,
                merged_at=merged[task.id],
                cancelled_at=_when(history[task.id], CANCELLED),
            )
            for task in tasks
            if points_of(task.size) is not None
        ]
        points = series(cards, since=since or self._now - timedelta(days=SPAN), now=self._now)
        features = await FeatureRecords(self._records).features(project)
        return Progress(
            project=project,
            series=[
                SeriesPoint(day=one.day, points=one.points, merged=one.merged) for one in points
            ],
            pace=pace(points),
            forecast=_info(_forecast_of(tasks, rates, spreads, hours_a_day, agents, now=self._now)),
            features=[
                self._feature(feature, tasks, history, rates, spreads, hours_a_day, agents)
                for feature in features
            ],
            models=self._model_rows(samples, rates, agents),
            recent=self._recent(tasks, history, rests, rates, agents),
            estimate_ratio=self._ratio(tasks, history, rests, rates, agents),
        )

    async def estimate(self, project: str, key: str) -> Estimate:
        """One task's size, its estimate and the time worked on it so far."""
        await ProjectRecords(self._records).require(project)
        tasks = TaskRecords(self._records)
        found = await tasks.task(project, key)
        if found is None:
            raise DoesNotExist(f"There is no task {key!r} in {project!r}")
        changes = await self._changes()
        waits = await self._waits()
        agents = {agent.name: agent for agent in await AgentRecords(self._records).agents()}
        samples = self._samples(await tasks.tasks(project), changes, waits, agents)
        mine = changes.get(found.id, [])
        model = _model_of(found.assignee, agents) or _model_of(_last_worked(mine), agents)
        rate = hours_per_point(samples, model=model)
        return Estimate(
            task=as_shape(Shaped, found),
            size=found.size,
            estimate=estimate(points_of(found.size), rate),
            worked=working_time(mine, waits.get(found.id, []), now=self._now),
        )

    # -- what the records say ------------------------------------------------------------------

    async def _changes(self) -> dict[UUID, list[StateChange]]:
        """Every task's moves, by its id, oldest first."""
        found: dict[UUID, list[StateChange]] = {}
        for one in await self._records.list(StateChange):
            found.setdefault(one.task, []).append(one)
        for moves in found.values():
            moves.sort(key=lambda one: one.at)
        return found

    async def _waits(self) -> dict[UUID, list[Wait]]:
        """Every task's waits, by its id."""
        found: dict[UUID, list[Wait]] = {}
        for one in await self._records.list(Wait):
            found.setdefault(one.task, []).append(one)
        return found

    def _samples(
        self,
        tasks: Sequence[Task],
        history: Mapping[UUID, Sequence[StateChange]],
        waits: Mapping[UUID, Sequence[Wait]],
        agents: Mapping[str, Agent],
    ) -> list[Sample]:
        """The merged, sized tasks as samples: each agent that worked one is a sample of its own
        model (a task handed on is two), dated the day it merged."""
        found: list[Sample] = []
        for task in tasks:
            points = points_of(task.size)
            merged = _when(history.get(task.id, []), MERGED)
            if points is None or merged is None:
                continue
            worked = by_agent(history.get(task.id, []), waits.get(task.id, []), now=self._now)
            found.extend(
                Sample(model=_model_of(agent, agents), points=points, hours=hours, merged_at=merged)
                for agent, hours in worked.items()
            )
        return found

    def _hours_a_day(
        self,
        changes: Mapping[UUID, Sequence[StateChange]],
        waits: Mapping[UUID, Sequence[Wait]],
        agents: Mapping[str, Agent],
    ) -> dict[str, float]:
        """Each agent's working hours a day over the last `DAYS` (0.0 when it has none)."""
        since = self._now - timedelta(days=DAYS)
        worked: dict[str, timedelta] = {}
        for task, moves in changes.items():
            for agent, hours in by_agent(
                moves, waits.get(task, []), now=self._now, since=since
            ).items():
                worked[agent] = worked.get(agent, timedelta(0)) + hours
        return {
            name: worked.get(name, timedelta(0)).total_seconds() / 3600 / DAYS for name in agents
        }

    def _feature(
        self,
        feature: Feature,
        tasks: Sequence[Task],
        history: Mapping[UUID, Sequence[StateChange]],
        rates: Rates,
        spreads: Spreads,
        hours_a_day: Mapping[str, float],
        agents: Mapping[str, Agent],
    ) -> FeatureProgress:
        """A feature's points, its merged points, and when what is left of it is done."""
        mine = [task for task in tasks if task.feature == feature.key]
        left = [task for task in mine if task.state not in CLOSED and points_of(task.size)]
        return FeatureProgress(
            key=feature.key,
            title=feature.title,
            points=sum(points_of(task.size) or 0 for task in mine),
            merged=sum(
                points_of(task.size) or 0
                for task in mine
                if _when(history.get(task.id, []), MERGED) is not None
            ),
            forecast=_info(_forecast_of(left, rates, spreads, hours_a_day, agents, now=self._now))
            if left
            else None,
        )

    def _model_rows(
        self, samples: Sequence[Sample], rates: Rates, agents: Mapping[str, Agent]
    ) -> list[ModelRow]:
        """What a point costs each model, and what each size came to for it."""
        found: list[ModelRow] = []
        for model in sorted(_models(samples), key=lambda one: one or ""):
            mine = _mine(samples, model)
            hours: dict[str, list[float]] = {}
            for one in mine:
                hours.setdefault(_size_of(one.points), []).append(one.hours.total_seconds() / 3600)
            found.append(
                ModelRow(
                    model=model or "unknown",
                    runner=_runner_of(model, agents),
                    tasks=len(mine),
                    hours_per_point=_rate_info(rates[model]),
                    hours_per_size={
                        size: statistics.median(found) for size, found in hours.items()
                    },
                )
            )
        return found

    def _recent(
        self,
        tasks: Sequence[Task],
        history: Mapping[UUID, Sequence[StateChange]],
        waits: Mapping[UUID, Sequence[Wait]],
        rates: Rates,
        agents: Mapping[str, Agent],
    ) -> list[RecentTask]:
        """The last `RECENT` merged, sized tasks: what each was estimated at, and what it took."""
        merged = [
            (task, _when(history.get(task.id, []), MERGED))
            for task in tasks
            if points_of(task.size) is not None
        ]
        merged = [(task, at) for task, at in merged if at is not None]
        merged.sort(key=lambda pair: pair[1] or self._now, reverse=True)
        found: list[RecentTask] = []
        for task, _ in merged[:RECENT]:
            expected = estimate(
                points_of(task.size) or 0, rates[_rate_model(task, history, agents)]
            )
            found.append(
                RecentTask(
                    key=task.key,
                    title=task.title,
                    size=task.size,  # pyright: ignore[reportArgumentType]
                    estimate=expected or timedelta(0),
                    actual=working_time(
                        history.get(task.id, []), waits.get(task.id, []), now=self._now
                    ),
                )
            )
        return found

    def _ratio(
        self,
        tasks: Sequence[Task],
        history: Mapping[UUID, Sequence[StateChange]],
        waits: Mapping[UUID, Sequence[Wait]],
        rates: Rates,
        agents: Mapping[str, Agent],
    ) -> float:
        """The median of actual ÷ estimated over the recent tasks (0.0 when there are none)."""
        ratios = [
            one.actual.total_seconds() / one.estimate.total_seconds()
            for one in self._recent(tasks, history, waits, rates, agents)
            if one.estimate.total_seconds() > 0
        ]
        return statistics.median(ratios) if ratios else 0.0


def _forecast_of(
    tasks: Sequence[Task],
    rates: Rates,
    spreads: Spreads,
    hours_a_day: Mapping[str, float],
    agents: Mapping[str, Agent],
    *,
    now: datetime,
) -> Forecast:
    """`finishes` over the work `tasks` are: each agent's own in queue order, and the tasks
    nobody has spread over the enabled implementers in name order."""
    enabled = [
        agent for agent in sorted(agents.values(), key=lambda one: one.name) if agent.enabled
    ]
    average = _average_day(hours_a_day, [agent.name for agent in enabled])
    loads: dict[str, list[timedelta]] = {agent.name: [] for agent in enabled}
    unassigned: list[Task] = []
    unsized = 0
    for task in tasks:
        if task.state in CLOSED:
            continue
        if points_of(task.size) is None:
            unsized += 1
        elif task.assignee is None:
            unassigned.append(task)
        else:
            loads.setdefault(task.assignee, []).append(_left(task, rates, agents))
    for index, task in enumerate(
        sorted(unassigned, key=lambda one: (one.position is None, one.position or 0, one.key))
    ):
        if enabled:
            loads[enabled[index % len(enabled)].name].append(_left(task, rates, agents))
    found = [
        Load(
            agent=name,
            hours=sum(hours, timedelta(0)),
            hours_a_day=_day_of(name, hours_a_day, average),
            low=_low_or_high(_model_of(name, agents), spreads, low=True),
            high=_low_or_high(_model_of(name, agents), spreads, low=False),
        )
        for name, hours in loads.items()
        if hours
    ]
    forecast = finishes(found, now=now)
    return Forecast(
        likely=forecast.likely, early=forecast.early, late=forecast.late, unsized=unsized
    )


def _left(task: Task, rates: Rates, agents: Mapping[str, Agent]) -> timedelta:
    """A queued task's estimate, at its agent's model's rate."""
    rate = rates.get(_model_of(task.assignee, agents), Rate(1.0, "default"))
    return estimate(points_of(task.size) or 0, rate) or timedelta(0)


def _day_of(name: str, hours_a_day: Mapping[str, float], average: float) -> float:
    """An agent's hours a day: its own when it has worked lately, else the project's average,
    else `DEFAULT_HOURS`."""
    own = hours_a_day.get(name, 0.0)
    if own >= FEWEST:
        return own
    return average if average >= FEWEST else DEFAULT_HOURS


def _average_day(hours_a_day: Mapping[str, float], names: Sequence[str]) -> float:
    """The project's agents' average hours a day, over those that have worked lately."""
    worked = [hours_a_day.get(name, 0.0) for name in names]
    worked = [one for one in worked if one >= FEWEST]
    return sum(worked) / len(worked) if worked else 0.0


def _low_or_high(model: str | None, spreads: Spreads, *, low: bool) -> float:
    """A model's rate spread's low or high end (1.0 when nothing is known)."""
    found = spreads.get(model)
    return found[0 if low else 1] if found is not None else 1.0


def _info(found: Forecast) -> ForecastInfo:
    """`found` as the shape a page reads."""
    return ForecastInfo(
        likely=found.likely, early=found.early, late=found.late, unsized=found.unsized
    )


def _rate_info(rate: Rate) -> RateInfo:
    """A rate as the shape a page reads."""
    return RateInfo(hours=rate.hours_per_point, source=rate.source)


def _model_of(agent: str | None, agents: Mapping[str, Agent]) -> str | None:
    """The model an agent runs (None: an agent nobody knows, or one whose model is empty)."""
    found = agents.get(agent) if agent is not None else None
    return (found.model or None) if found is not None else None


def _last_worked(changes: Sequence[StateChange]) -> str | None:
    """Who last moved a task into `in_progress` (whose model the task is learned from)."""
    for change in reversed(changes):
        if change.to_state == IN_PROGRESS:
            return change.by
    return None


def _runner_of(model: str | None, agents: Mapping[str, Agent]) -> str:
    """The runner of the first agent (by name) that runs `model`."""
    for agent in sorted(agents.values(), key=lambda one: one.name):
        if (agent.model or None) == model:
            return agent.runner
    return ""


def _rate_model(
    task: Task, history: Mapping[UUID, Sequence[StateChange]], agents: Mapping[str, Agent]
) -> str | None:
    """The model a task's estimate is at: its assignee's, else the one that last worked it."""
    return _model_of(task.assignee, agents) or _model_of(
        _last_worked(history.get(task.id, [])), agents
    )


def _when(changes: Sequence[StateChange], state: str) -> datetime | None:
    """When a task last moved to `state` (None: it never did)."""
    for change in reversed(changes):
        if change.to_state == state:
            return change.at
    return None


def _models(samples: Sequence[Sample]) -> set[str | None]:
    """The models that worked a sample."""
    return {one.model for one in samples}


def _mine(samples: Sequence[Sample], model: str | None) -> list[Sample]:
    """The samples of one model."""
    return [one for one in samples if one.model == model]


def _size_of(points: int) -> str:
    """The size worth `points` (for the hours-per-size table)."""
    return next((size for size, worth in POINTS.items() if worth == points), str(points))
