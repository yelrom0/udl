"""27.4: what an agent is suggested next, ranked, each with its reasons.

Pure: the caller reads the records (the tasks, features and agents of a project) and this says
what to do next, so the same answer is given for the same records — the board (29.4), a
manager (27.8) and a person all see one ranking.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Final

from udl.errors import DoesNotExist
from udl.plugins.apps.orch.models import Suggestion
from udl.plugins.apps.orch.records import Agent, Feature, Task

__all__ = ["suggest"]

DONE: Final[frozenset[str]] = frozenset({"merged", "done"})
"""The states a need is done in: its work is in, so what waits on it may start."""

OVER: Final[frozenset[str]] = DONE | {"cancelled"}
"""The states nothing waits on: its work is in, or it will not be done at all."""

WORKED: Final[frozenset[str]] = frozenset({"in_progress", "blocked", "ready", "merged", "done"})
"""The states an agent has started a task in: a `queued` one it has not, and says nothing about
what follows it."""

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
    busy: Iterable[str] = (),
) -> list[Suggestion]:
    """The tasks to do next, best first, each with the agent it suits and why.

    Candidates are the `open` build tasks of the agents' projects whose `needs` are each
    `merged` or `done` (or `ready`: then it may start on that task's branch, which `stack_on`
    names). A candidate scores: +3 when its feature is `active`; +3 when the agent's last task
    was in the same feature, or +1 when it was in the same repo; +1 for each task it unblocks,
    to +3. Ties go to the feature's `order`, then the task's key.

    With `agent`, its own tasks are ranked. Without, each candidate is paired with the idle
    agent it scores best for: one enabled, not paused, on nothing in these tasks — or named in
    `busy`, which is how a caller says an agent is on something in another project (a task of
    one project cannot know of another's). Raises DoesNotExist for an agent that is not one."""
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
    wanted = [known[agent]] if agent is not None else _idle(tasks, agents, busy)
    # (What each agent worked most recently, read once: it is what two of the scores and the
    # tie-break ask about.)
    last = {one.name: _last(tasks, one.name) for one in wanted}
    ranked: list[Suggestion] = []
    for one in tasks:
        if not _candidate(one, by_key):
            continue
        best: tuple[int, list[str], Agent] | None = None
        for who in wanted:
            if who.projects and one.project not in who.projects:
                continue
            score, reasons = _rank(one, who, last[who.name], by_feature, by_key, unblocks)
            # (A tie goes to the agent whose name comes first, so the answer never depends on
            # the order the agents were read in.)
            if best is None or score > best[0] or (score == best[0] and who.name < best[2].name):
                best = (score, reasons, who)
        if best is None:
            continue
        score, reasons, who = best
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
    if sum(1 for need in task.needs if need in by_key and by_key[need].state in STARTS) > 1:
        # (It would have to stack on two branches at once, which one suggestion cannot say:
        # it waits until one of them is merged.)
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
        if one.state in OVER:
            continue
        for need in one.needs:
            counted[need] += 1
    return counted


def _idle(tasks: Sequence[Task], agents: Sequence[Agent], busy: Iterable[str]) -> list[Agent]:
    """The agents a task may be suggested to: enabled, not paused, and on nothing now (in these
    tasks, or in the ones the caller named)."""
    on = {one.assignee for one in tasks if one.state == "in_progress" and one.assignee}
    on.update(busy)
    return [one for one in agents if one.enabled and not one.paused and one.name not in on]


def _last(tasks: Sequence[Task], name: str) -> Task | None:
    """The task `name` worked most recently (by when it last changed), or None."""
    mine = [one for one in tasks if one.assignee == name and one.state in WORKED]
    return max(mine, key=lambda one: (one.updated_at, _natural(one.key))) if mine else None


def _rank(
    task: Task,
    who: Agent,
    last: Task | None,
    by_feature: dict[str, Feature],
    by_key: dict[str, Task],
    unblocks: Counter[str],
) -> tuple[int, list[str]]:
    """How well `task` suits `who`, and why: its score and the reasons for it, counted once."""
    score = 0
    reasons: list[str] = []
    found = by_feature.get(task.feature)
    if found is not None and found.status == "active":
        score += 3
        reasons.append("the feature is active")
    if last is not None and last.key != task.key:
        if last.feature == task.feature:
            score += 3
            reasons.append("your last task was in this feature")
        elif task.repo is not None and last.repo == task.repo:
            score += 1
            reasons.append("your last task was in this repo")
    counted = unblocks[task.key]
    if counted:
        score += min(counted, UNBLOCKS)
        reasons.append(f"it unblocks {counted} task{'s' if counted > 1 else ''}")
    stack_on = _stack_on(task, by_key)
    if stack_on is not None:
        reasons.append(f"stack on {stack_on}")
    return score, reasons


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
