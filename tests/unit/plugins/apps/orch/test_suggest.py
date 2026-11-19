"""27.4: what an agent is suggested next, ranked, each with its reasons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from udl.plugins.apps.orch.models import FeatureStatus, State, TaskKind
from udl.plugins.apps.orch.records import Agent, Feature, Task
from udl.plugins.apps.orch.suggest import suggest

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)


def task(
    key: str,
    *,
    feature: str = "F-1",
    state: State = "open",
    needs: list[str] | None = None,
    repo: str | None = None,
    task_kind: TaskKind = "build",
    assignee: str | None = None,
    project: str = "udl",
    minutes: int = 0,
) -> Task:
    """A task, as a test makes one."""
    return Task(
        project=project,
        feature=feature,
        key=key,
        title=key,
        state=state,
        needs=needs or [],
        repo=repo,
        task_kind=task_kind,
        assignee=assignee,
        updated_at=NOW + timedelta(minutes=minutes),
    )


def feature(
    key: str = "F-1", *, status: FeatureStatus = "active", order: int = 1, project: str = "udl"
) -> Feature:
    """A feature, as a test makes one."""
    return Feature(project=project, key=key, title=key, status=status, order=order)


def agent(name: str = "fake-a", **fields: object) -> Agent:
    """An agent of the fake runner, as a test changes it."""
    return Agent.model_validate({"name": name, "title": name, "runner": "fake", **fields})


def test_a_candidate_is_an_open_build_task_of_the_agents_project() -> None:
    tasks = [
        task("F-1.1"),
        task("F-1.2", state="in_progress", assignee="fake-a"),
        task("F-1.3", task_kind="human"),
        task("F-1.4", project="other"),
        task("F-1.5", state="queued", assignee="fake-a"),
    ]

    found = suggest(tasks, [feature()], [agent(projects=["udl"])], agent="fake-a")

    assert [one.task for one in found] == ["F-1.1"]


def test_a_task_whose_need_is_not_done_is_not_suggested() -> None:
    tasks = [task("F-1.1", state="in_progress"), task("F-1.2", needs=["F-1.1"])]

    assert suggest(tasks, [feature()], [agent()], agent="fake-a") == []


def test_a_task_whose_need_is_merged_or_done_is_suggested() -> None:
    tasks = [
        task("F-1.1", state="merged"),
        task("F-1.2", state="done"),
        task("F-1.3", needs=["F-1.1"]),
        task("F-1.4", needs=["F-1.2"]),
    ]

    found = suggest(tasks, [feature()], [agent()], agent="fake-a")

    assert [one.task for one in found] == ["F-1.3", "F-1.4"]
    assert [one.stack_on for one in found] == [None, None]


def test_a_task_whose_need_is_ready_stacks_on_it() -> None:
    # (The need's PR is green but not merged: the work can start, on its branch.)
    tasks = [task("F-1.1", state="ready"), task("F-1.2", needs=["F-1.1"])]

    (found,) = suggest(tasks, [feature()], [agent()], agent="fake-a")

    assert found.stack_on == "F-1.1"
    assert "stack on F-1.1" in found.reasons


def test_an_active_feature_scores_higher() -> None:
    tasks = [task("F-1.1", feature="F-1"), task("F-2.1", feature="F-2")]
    features = [feature("F-1", status="planned", order=1), feature("F-2", order=2)]

    found = suggest(tasks, features, [agent()], agent="fake-a")

    assert [one.task for one in found] == ["F-2.1", "F-1.1"]
    assert found[0].score == 3
    assert "the feature is active" in found[0].reasons
    assert found[1].score == 0


def test_the_agents_last_task_points_at_what_follows_it() -> None:
    # (Its last task was in F-1: F-1.2 follows it. One in the same repo counts for a little.)
    tasks = [
        task("F-1.1", feature="F-1", state="merged", repo="udl", assignee="fake-a", minutes=5),
        task("F-1.2", feature="F-1"),
        task("F-2.1", feature="F-2", repo="udl"),
    ]
    features = [
        feature("F-1", status="planned", order=1),
        feature("F-2", status="planned", order=2),
    ]

    found = suggest(tasks, features, [agent()], agent="fake-a")

    assert [one.task for one in found] == ["F-1.2", "F-2.1"]
    assert found[0].score == 3
    assert "your last task was in this feature" in found[0].reasons
    assert found[1].score == 1
    assert "your last task was in this repo" in found[1].reasons


def test_a_task_that_unblocks_others_scores_higher() -> None:
    tasks = [
        task("F-1.1"),
        task("F-2.1"),
        task("F-3.1", needs=["F-1.1"]),
        task("F-3.2", needs=["F-1.1"]),
    ]
    features = [
        feature("F-1", status="planned", order=1),
        feature("F-2", status="planned", order=2),
        feature("F-3", status="planned", order=3),
    ]

    found = suggest(tasks, features, [agent()], agent="fake-a")

    assert [one.task for one in found] == ["F-1.1", "F-2.1"]
    assert found[0].score == 2
    assert "it unblocks 2 tasks" in found[0].reasons


def test_an_active_feature_that_unblocks_everything_comes_first() -> None:
    tasks = [
        task("F-1.1"),
        task("F-2.1", feature="F-2"),
        *[task(f"F-3.{n}", feature="F-3", needs=["F-1.1"]) for n in range(1, 6)],
    ]
    features = [
        feature("F-1", status="planned", order=1),
        feature("F-2", status="active", order=2),
        feature("F-3", status="planned", order=3),
    ]

    found = suggest(tasks, features, [agent()], agent="fake-a")

    # (F-1.1 unblocks five, but only to +3; F-2.1's feature is active, +3. They tie, so the
    # feature's order decides: F-1 comes before F-2, not the key.)
    assert [one.task for one in found] == ["F-1.1", "F-2.1"]
    assert found[0].score == found[1].score == 3


def test_the_limit_keeps_the_best() -> None:
    tasks = [task(f"F-1.{n}") for n in range(1, 6)]

    found = suggest(tasks, [feature()], [agent()], agent="fake-a", limit=2)

    assert [one.task for one in found] == ["F-1.1", "F-1.2"]


def test_the_keys_are_read_as_numbers() -> None:
    # (22.10 comes after 22.9, not before 22.2.)
    tasks = [task("22.10", feature="22"), task("22.2", feature="22")]

    found = suggest(tasks, [feature("22")], [agent()], agent="fake-a")

    assert [one.task for one in found] == ["22.2", "22.10"]


def test_with_no_agent_each_task_goes_to_the_idle_agent_it_suits() -> None:
    # (fake-b's last task was in F-2, so F-2.1 suits it; F-1.1 goes to fake-a.)
    tasks = [
        task("F-1.1", feature="F-1"),
        task("F-2.1", feature="F-2"),
        task("F-2.2", feature="F-2", state="merged", assignee="fake-b"),
        task("F-1.2", feature="F-1", state="in_progress", assignee="fake-c"),
    ]
    features = [feature("F-1", order=1), feature("F-2", order=2)]
    agents = [agent("fake-a"), agent("fake-b"), agent("fake-c")]

    found = suggest(tasks, features, agents)

    # (F-2.1 suits fake-b best, +3: its last task was in F-2, so it comes first.)
    assert [(one.task, one.agent) for one in found] == [
        ("F-2.1", "fake-b"),
        ("F-1.1", "fake-a"),
    ]


def test_a_paused_or_busy_agent_is_not_suggested_anything() -> None:
    tasks = [
        task("F-1.1"),
        task("F-2.1", feature="F-2"),
        task("F-2.2", feature="F-2", state="in_progress", assignee="fake-b"),
    ]
    features = [feature("F-1", order=1), feature("F-2", order=2)]
    agents = [agent("fake-a", paused=True), agent("fake-b")]

    assert suggest(tasks, features, agents) == []


def test_an_agent_that_does_not_work_the_project_is_not_suggested_it() -> None:
    tasks = [task("F-1.1")]
    agents = [agent("fake-a", projects=["other"])]

    assert suggest(tasks, [feature()], agents) == []


def test_a_suggestion_says_why() -> None:
    tasks = [task("F-1.1"), task("F-2.1", feature="F-2", needs=["F-1.1"])]
    features = [feature("F-1", order=1), feature("F-2", order=2)]

    (found,) = suggest(tasks, features, [agent()], agent="fake-a", limit=1)

    assert found.reasons == ["the feature is active", "it unblocks 1 task"]


def test_the_plans_first_task_comes_before_the_second() -> None:
    # (The card's done-when: over an imported plan, 22.1 is suggested before 22.2.)
    tasks = [
        task("22.2", feature="22", needs=["22.1"]),
        task("22.1", feature="22"),
    ]

    found = suggest(tasks, [feature("22")], [agent()], agent="fake-a")

    assert [one.task for one in found] == ["22.1"]


def test_a_task_in_the_queue_is_not_suggested_again() -> None:
    tasks = [task("F-1.1", state="queued", assignee="fake-a")]

    assert suggest(tasks, [feature()], [agent()], agent="fake-a") == []


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_of_nothing_suggests_nothing(limit: int) -> None:
    tasks = [task("F-1.1")]

    assert suggest(tasks, [feature()], [agent()], agent="fake-a", limit=limit) == []
