"""
--------------------
- test_reporting.py -
--------------------

`reporting`'s small pure helpers: what is left of a task, the rate a model falls back to, and
when a task merged (27.10's review turned each of them up).
"""

# System Imports
from datetime import UTC, datetime, timedelta

# Package Imports
import pytest

# Local Imports
from udl.plugins.apps.orch.estimates import DEFAULT, Rate
from udl.plugins.apps.orch.models import DONE, IN_PROGRESS, MERGED, State
from udl.plugins.apps.orch.records import Agent, StateChange, Task, Wait
from udl.plugins.apps.orch.reporting import left_of, merged_at, rate_in

NOW = datetime(2026, 11, 20, 12, tzinfo=UTC)
"""When the helpers are asked."""

PROJECT = Rate(2.0, "project")
"""What a point costs the project, for a model with no samples of its own."""

AGENTS = {"deepseek-a": Agent(name="deepseek-a", title="a", runner="fake", model="sampled")}
"""One agent, whose model has samples."""


def _at(hour: int) -> datetime:
    return datetime(2026, 11, 20, hour, tzinfo=UTC)


def _move(to: State, hour: int) -> StateChange:
    return StateChange(task=TASK.id, from_state="open", to_state=to, at=_at(hour), by="deepseek-a")


TASK = Task(project="udl", feature="26", key="26.1", title="A task", size="M")
"""A three-point task."""


def test_what_is_left_is_the_estimate_less_the_work_done() -> None:
    changes = [_move(IN_PROGRESS, 8)]

    left = left_of(TASK, changes, [], {None: PROJECT, "sampled": PROJECT}, AGENTS, _at(10))

    # (Three points at two hours each is six hours, of which two are worked.)
    assert left == timedelta(hours=4)


def test_a_task_nobody_started_has_all_of_its_estimate_left() -> None:
    assert left_of(TASK, [], [], {None: PROJECT, "sampled": PROJECT}, AGENTS, NOW) == timedelta(
        hours=6
    )


def test_a_wait_is_not_work_so_it_leaves_the_estimate_whole() -> None:
    changes = [_move(IN_PROGRESS, 8)]
    waits = [Wait(task=TASK.id, wait_kind="ci", start=_at(8), end=_at(10))]

    left = left_of(TASK, changes, waits, {None: PROJECT, "sampled": PROJECT}, AGENTS, _at(10))

    assert left == timedelta(hours=6)


def test_a_model_with_no_samples_is_the_project_s_rate() -> None:
    assert rate_in({"sampled": PROJECT, None: PROJECT}, "brand-new") == PROJECT


def test_a_project_with_no_samples_is_an_hour_a_point() -> None:
    assert rate_in({None: Rate(DEFAULT, "default")}, "brand-new").hours_per_point == DEFAULT


@pytest.mark.parametrize("state", [MERGED, DONE])
def test_an_imported_merged_task_merged_when_it_was_written(state: str) -> None:
    task = TASK.model_copy(update={"state": state})

    assert merged_at(task, []) == task.updated_at


def test_a_merged_task_merged_when_it_moved() -> None:
    task = TASK.model_copy(update={"state": MERGED})

    assert merged_at(task, [_move(MERGED, 9)]) == _at(9)


def test_an_open_task_has_not_merged() -> None:
    assert merged_at(TASK, [_move(IN_PROGRESS, 9)]) is None
