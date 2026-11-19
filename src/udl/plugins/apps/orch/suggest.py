"""27.4: what an agent is suggested next, ranked, each with its reasons.

Pure: the caller reads the records (the tasks, features and agents of a project) and this says
what to do next, so the same answer is given for the same records — the board (29.4), a
manager (27.8) and a person all see one ranking.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Final

from udl.errors import DoesNotExist
from udl.plugins.apps.orch.models import Suggestion
from udl.plugins.apps.orch.records import Agent, Feature, Task

__all__ = ["suggest"]

DONE: Final[frozenset[str]] = frozenset({"merged", "done"})
"""The states a need is done in: its work is in, so what waits on it may start."""

STARTS: Final[frozenset[str]] = frozenset({"ready"})
"""The states a need may be in for a task to start on its branch instead of waiting."""

UNBLOCKS: Final[int] = 3
"""How much unblocking other tasks may add: +1 each, to +3."""


def suggest(
    tasks: Sequence[Task],
    features: Sequence[Feature],
    agents: Sequence[Agent],
    *,
    agent: str | None = None,
    limit: int = 10,
) -> list[Suggestion]:
    """The tasks to do next, best first, each with the agent it suits and why.

    Candidates are the `open` build tasks of the agents' projects whose `needs` are each
    `merged` or `done` (or `ready`: then it may start on that task's branch, which `stack_on`
    names). A candidate scores: +3 when its feature is `active`; +3 when the agent's last task
    was in the same feature, or +1 when it was in the same repo; +1 for each task it unblocks,
    to +3. Ties go to the feature's `order`, then the task's key.

    With `agent`, its own tasks are ranked. Without, each candidate is paired with the idle
    agent (one with nothing in progress) it scores best for. Raises DoesNotExist for an agent
    that is not one."""
    if limit <= 0:
        return []
    known = {one.name: one for one in agents}
    if agent is not None and agent not in known:
        raise DoesNotExist(
            f"There is no agent called {agent!r}",
            hint=f"The agents: {', '.join(sorted(known)) or 'none'} (add it first, 27.1).",
        )
    by_key = {one.key: one for one in tasks}
    by_feature = {one.key: one for one in features}
    unblocks = _unblocks(tasks)
    wanted = [known[agent]] if agent is not None else _idle(tasks, agents)
    ranked: list[Suggestion] = []
    for one in tasks:
        if not _candidate(one, by_key):
            continue
        best: tuple[int, Agent] | None = None
        for who in wanted:
            if who.projects and one.project not in who.projects:
                continue
            scored = _score(one, who, tasks, by_feature, unblocks)
            # (A tie goes to the agent whose name comes first, so the answer never depends on
            # the order the agents were read in.)
            if best is None or scored > best[0] or (scored == best[0] and who.name < best[1].name):
                best = (scored, who)
        if best is None:
            continue
        score, who = best
        reasons = _reasons(one, who, tasks, by_feature, by_key, unblocks)
        ranked.append(
            Suggestion(
                task=one.key,
                title=one.title,
                feature=one.feature,
                agent=who.name,
                score=score,
                reasons=reasons,
                stack_on=_stack_on(one, by_key),
            )
        )
    ranked.sort(
        key=lambda one: (
            -one.score,
            by_feature[one.feature].order if one.feature in by_feature else 0,
            _natural(one.task),
        )
    )
    return ranked[:limit]


def _candidate(task: Task, by_key: dict[str, Task]) -> bool:
    """`task` may be started now: it is an open build task whose needs are in."""
    if task.state != "open" or task.task_kind != "build":
        return False
    for need in task.needs:
        found = by_key.get(need)
        if found is None or found.state not in DONE | STARTS:
            return False
    return True


def _unblocks(tasks: Sequence[Task]) -> Counter[str]:
    """How many unfinished tasks each task's key is a need of."""
    counted: Counter[str] = Counter()
    for one in tasks:
        if one.state in DONE:
            continue
        for need in one.needs:
            counted[need] += 1
    return counted


def _idle(tasks: Sequence[Task], agents: Sequence[Agent]) -> list[Agent]:
    """The agents a task may be suggested to: enabled, not paused, and on nothing now."""
    busy = {one.assignee for one in tasks if one.state == "in_progress" and one.assignee}
    return [one for one in agents if one.enabled and not one.paused and one.name not in busy]


def _last(tasks: Sequence[Task], name: str) -> Task | None:
    """The task `name` worked most recently (by when it last changed), or None."""
    mine = [one for one in tasks if one.assignee == name]
    return max(mine, key=lambda one: (one.updated_at, _natural(one.key))) if mine else None


def _score(
    task: Task,
    who: Agent,
    tasks: Sequence[Task],
    by_feature: dict[str, Feature],
    unblocks: Counter[str],
) -> int:
    """How well `task` suits `who` (higher is better)."""
    score = 0
    found = by_feature.get(task.feature)
    if found is not None and found.status == "active":
        score += 3
    last = _last(tasks, who.name)
    if last is not None and last.key != task.key:
        if last.feature == task.feature:
            score += 3
        elif task.repo is not None and last.repo == task.repo:
            score += 1
    return score + min(unblocks[task.key], UNBLOCKS)


def _reasons(
    task: Task,
    who: Agent,
    tasks: Sequence[Task],
    by_feature: dict[str, Feature],
    by_key: dict[str, Task],
    unblocks: Counter[str],
) -> list[str]:
    """Why `task` was suggested to `who`, in the order they were counted."""
    reasons: list[str] = []
    found = by_feature.get(task.feature)
    if found is not None and found.status == "active":
        reasons.append("the feature is active")
    last = _last(tasks, who.name)
    if last is not None and last.key != task.key:
        if last.feature == task.feature:
            reasons.append("your last task was in this feature")
        elif task.repo is not None and last.repo == task.repo:
            reasons.append("your last task was in this repo")
    counted = unblocks[task.key]
    if counted:
        reasons.append(f"it unblocks {counted} task{'s' if counted > 1 else ''}")
    stack_on = _stack_on(task, by_key)
    if stack_on is not None:
        reasons.append(f"stack on {stack_on}")
    return reasons


def _stack_on(task: Task, by_key: dict[str, Task]) -> str | None:
    """The need `task` may start on the branch of (it is `ready`, not merged yet), or None."""
    for need in task.needs:
        found = by_key.get(need)
        if found is not None and found.state in STARTS:
            return need
    return None


def _natural(key: str) -> tuple[tuple[int, int | str], ...]:
    """A key as numbers where they are numbers, so `22.10` comes after `22.9`."""
    parts: list[tuple[int, int | str]] = []
    for piece in key.replace(".", " . ").split():
        parts.append((0, int(piece)) if piece.isdigit() else (1, piece))
    return tuple(parts)
