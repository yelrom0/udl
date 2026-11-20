"""
------------
- test_merge.py -
------------

Merging (28.3): the order the ready pulls merge in, and how a list reads.

The pure parts are here; what reads GitHub is `test_merge_operations.py`, and what a merge sets
off is `test_merge_after.py`.
"""

# System Imports
from datetime import UTC, datetime, timedelta

# Local Imports
from udl.plugins.apps.orch.merge import order
from udl.plugins.apps.orch.models import MergeRow

NOW: datetime = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
"""The time the tests are at."""


def row(
    key: str, *, needs: list[str] | None = None, minutes: int = 0, repo: str = "a/one"
) -> MergeRow:
    """A ready row, ready `minutes` after `NOW`."""
    return MergeRow(
        project="udl",
        task=key,
        feature=key.split(".")[0],
        title=f"Task {key}",
        repo=repo,
        number=int(key.split(".")[-1]),
        url=f"https://github.com/{repo}/pull/{key.split('.')[-1]}",
        sha=f"{key}sha",
        state="ready",
        needs=needs or [],
        since=NOW + timedelta(minutes=minutes),
    )


def test_a_task_that_needs_another_comes_after_it() -> None:
    """(A stack merges bottom first, whatever the times say.)"""
    second = row("F-1.2", needs=["F-1.1"], minutes=1)
    first = row("F-1.1", minutes=5)

    assert [one.task for one in order([second, first])] == ["F-1.1", "F-1.2"]


def test_a_chain_of_three_keeps_its_order() -> None:
    third = row("F-1.3", needs=["F-1.2"], minutes=1)
    first = row("F-1.1", minutes=3)
    second = row("F-1.2", needs=["F-1.1"], minutes=2)

    assert [one.task for one in order([third, second, first])] == ["F-1.1", "F-1.2", "F-1.3"]


def test_unrelated_tasks_go_by_when_they_became_ready() -> None:
    late = row("F-2.1", minutes=30)
    early = row("F-1.1", minutes=10)

    assert [one.task for one in order([late, early])] == ["F-1.1", "F-2.1"]


def test_the_same_minute_goes_by_key() -> None:
    second = row("F-1.2", minutes=10)
    first = row("F-1.1", minutes=10)

    assert [one.task for one in order([second, first])] == ["F-1.1", "F-1.2"]


def test_each_repo_is_grouped_and_in_merge_order_inside() -> None:
    other = row("F-9.1", minutes=1, repo="b/two")
    stacked = row("F-1.2", needs=["F-1.1"], minutes=0)
    base = row("F-1.1", minutes=20)

    found = [one.task for one in order([stacked, other, base])]

    assert found == ["F-1.1", "F-1.2", "F-9.1"]


def test_a_row_whose_need_is_not_in_the_list_is_not_held_up_by_it() -> None:
    alone = row("F-1.2", needs=["F-1.1"], minutes=1)

    assert [one.task for one in order([alone])] == ["F-1.2"]


def test_a_cycle_does_not_hang_and_keeps_a_stable_order() -> None:
    """(Two tasks that each need the other: nothing to sort by, so by key, and it ends.)"""
    one = row("F-1.1", needs=["F-1.2"], minutes=1)
    two = row("F-1.2", needs=["F-1.1"], minutes=2)

    assert [one.task for one in order([two, one])] == ["F-1.1", "F-1.2"]
