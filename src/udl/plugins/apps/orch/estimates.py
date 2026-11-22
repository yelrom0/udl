"""
-----------------
- estimates.py -
-----------------

What a point costs, in hours (27.10), learned from the work that is done: the median of a model's
working hours per point, over its last 30 merged and sized tasks. A model with fewer than five of
them is answered with the project's own rate, and a project with fewer than five with an hour a
point — and the `Rate` says which of the three answered, so a page can say how sure it is.

Pure: samples in, a rate out. `spread` is how far the actual hours fell from the rate (its 25th
and 75th percentile), which is what makes a forecast a range rather than a date.
"""

# System Imports
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

# Local Imports
from udl.plugins.apps.orch.models import POINTS, Size

__all__ = ["Rate", "Sample", "estimate", "hours_per_point", "spread"]

SAMPLES: int = 30
"""How many of a model's last tasks its rate is learned from."""

FEWEST: int = 5
"""How many samples a rate needs before it is used: below this the next rate down answers."""

DEFAULT: float = 1.0
"""An hour a point: what is used when nothing has been learned yet."""

type Source = Literal["model", "project", "default"]
"""Which rate answered: the model's own, its project's, or the default."""


@dataclass(frozen=True)
class Sample:
    """One merged, sized task: the model that worked it, its points, and how long it took."""

    model: str | None
    points: int
    hours: timedelta
    merged_at: datetime

    @property
    def per_point(self) -> float:
        """Its hours a point."""
        return self.hours.total_seconds() / 3600 / self.points


@dataclass(frozen=True)
class Rate:
    """What a point costs in hours, and which of the three rates said so."""

    hours_per_point: float
    source: Source


def hours_per_point(samples: Sequence[Sample], *, model: str | None) -> Rate:
    """The rate `model` is estimated at: the median of its own last 30 tasks' hours a point when
    it has five of them, else the project's (every sample's), else an hour a point."""
    mine = _latest([one for one in samples if one.model == model and one.points > 0])
    if len(mine) >= FEWEST:
        return Rate(statistics.median(one.per_point for one in mine), "model")
    everything = _latest([one for one in samples if one.points > 0])
    if len(everything) >= FEWEST:
        return Rate(statistics.median(one.per_point for one in everything), "project")
    return Rate(DEFAULT, "default")


def estimate(points: int | None, rate: Rate) -> timedelta | None:
    """What `points` is expected to take at `rate` (None: an unsized task, which is not
    estimated)."""
    if points is None:
        return None
    return timedelta(hours=points * rate.hours_per_point)


def spread(samples: Sequence[Sample], *, rate: Rate) -> tuple[float, float]:
    """How far the actual hours fell from `rate`, as the 25th and 75th percentile of each
    sample's actual ÷ estimated (1.0 and 1.0 when there is too little to say)."""
    if rate.hours_per_point <= 0:
        return 1.0, 1.0
    ratios = [one.per_point / rate.hours_per_point for one in _latest(samples) if one.points > 0]
    if len(ratios) < 2:
        return 1.0, 1.0
    quartiles = statistics.quantiles(ratios, n=4, method="inclusive")
    return quartiles[0], quartiles[2]


def points_of(size: Size | None) -> int | None:
    """What a size is worth (None: unsized), from the one place a size becomes points."""
    return None if size is None else POINTS[size]


def _latest(samples: Sequence[Sample]) -> list[Sample]:
    """`samples`' last `SAMPLES`, newest first (the rate is learned from what was done lately)."""
    return sorted(samples, key=lambda one: one.merged_at, reverse=True)[:SAMPLES]
