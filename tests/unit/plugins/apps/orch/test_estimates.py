"""
--------------------------------
- test_estimates.py -
--------------------------------

What a point costs in hours (27.10), learned from the tasks that are done: the median of a
model's working hours per point over its last 30 merged, sized tasks; below five of them the
project's own rate; and below five of those, an hour a point.

The spread is how far the actual hours fell from that rate (its 25th and 75th percentile), which
is what makes a forecast a range rather than a date.
"""

# System Imports
from datetime import UTC, datetime, timedelta

# Local Imports
from udl.plugins.apps.orch.estimates import Sample, estimate, hours_per_point, spread

DAY = datetime(2026, 11, 22, tzinfo=UTC)


def _sample(model: str, points: int, hours: float, day: int = 0) -> Sample:
    """A merged task's sample: `model` took `hours` for `points`, merged `day` days before."""
    return Sample(
        model=model,
        points=points,
        hours=timedelta(hours=hours),
        merged_at=DAY - timedelta(days=day),
    )


def test_a_model_s_rate_is_the_median_of_its_hours_per_point() -> None:
    samples = [
        _sample("m", 1, 1.0),
        _sample("m", 1, 2.0),
        _sample("m", 2, 3.0),  # (1.5 an hour a point)
        _sample("m", 1, 3.0),
        _sample("m", 1, 4.0),
    ]

    found = hours_per_point(samples, model="m")

    assert found.source == "model"
    assert found.hours_per_point == 2.0


def test_a_model_s_rate_is_its_last_thirty_tasks() -> None:
    # (Thirty-one: the oldest is left out, so its 100 hours a point does not move the rate.)
    samples = [_sample("m", 1, 2.0, day=day) for day in range(30)]
    samples.append(_sample("m", 1, 100.0, day=31))

    found = hours_per_point(samples, model="m")

    assert found.source == "model"
    assert found.hours_per_point == 2.0


def test_a_model_with_fewer_than_five_tasks_uses_the_project_s_rate() -> None:
    samples = [_sample("m", 1, 1.0, day=day) for day in range(4)]
    samples += [_sample("other", 1, 3.0, day=day) for day in range(4)]

    found = hours_per_point(samples, model="m")

    assert found.source == "project"
    assert found.hours_per_point == 2.0  # (the median of the eight: 1 and 3)


def test_a_project_with_fewer_than_five_tasks_uses_an_hour_a_point() -> None:
    samples = [_sample("m", 1, 4.0, day=day) for day in range(3)]

    found = hours_per_point(samples, model="m")

    assert found.source == "default"
    assert found.hours_per_point == 1.0


def test_a_model_nobody_has_sampled_yet_uses_the_project_s_rate() -> None:
    samples = [_sample("other", 1, 3.0, day=day) for day in range(5)]

    found = hours_per_point(samples, model="new")

    assert found.source == "project"
    assert found.hours_per_point == 3.0


def test_an_estimate_is_its_points_at_that_rate() -> None:
    rate = hours_per_point([_sample("m", 1, 2.0, day=day) for day in range(5)], model="m")

    assert estimate(5, rate) == timedelta(hours=10)
    assert estimate(1, rate) == timedelta(hours=2)


def test_an_unsized_task_has_no_estimate() -> None:
    rate = hours_per_point([_sample("m", 1, 2.0, day=day) for day in range(5)], model="m")

    assert estimate(None, rate) is None


def test_the_spread_is_how_far_the_actual_hours_fell_from_the_rate() -> None:
    # (Hours a point: 1, 2, 3, 4, 5 — a median of 3; the quartiles are 2 and 4, so two thirds to
    # four thirds of the estimate.)
    samples = [
        _sample("m", 1, hours, day=day) for day, hours in enumerate([1.0, 2.0, 3.0, 4.0, 5.0])
    ]
    rate = hours_per_point(samples, model="m")

    low, high = spread(samples, rate=rate)

    assert rate.hours_per_point == 3.0
    assert round(low, 2) == round(2 / 3, 2)
    assert round(high, 2) == round(4 / 3, 2)


def test_one_sample_spreads_nowhere() -> None:
    samples = [_sample("m", 1, 3.0)]
    rate = hours_per_point(samples, model="m")

    assert spread(samples, rate=rate) == (1.0, 1.0)
