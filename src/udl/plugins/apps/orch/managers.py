"""
---------------
- managers.py -
---------------

What a manager may do for its reports (27.8): its four powers, the operations each switches on,
and who is inside its subtree.

A manager is an agent whose `role` is `manager` and whose `manages` (27.8's `ManagerPowers`) says
which of the four it has: `assign`, `answer`, `review` and `plan`. Each is off until it is given
(O-D10), and each switches on operations in its role's list (27.2) — always over its own subtree,
never another manager's reports:

- `assign` → `orch_assign`, `orch_reorder_queue`, `orch_suggest`
- `answer` → `orch_answer`
- `review` → `orch_review_task`
- `plan` → `orch_set_task`, `orch_split_task`

Nothing here writes: the operations check `in_subtree()`, the role table asks `switched_on()`, and
the dispatcher asks `dispatcher.manager_prompt()` for the news a manager's turn is told.
"""

# System Imports
from collections.abc import Mapping, Sequence
from typing import Final

# Local Imports
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch.agents import ManagerPowers

__all__ = [
    "POWERS",
    "POWER_OPERATIONS",
    "in_subtree",
    "managers_of",
    "reports",
    "subtree",
    "switched_on",
]

POWERS: Final[tuple[str, ...]] = ("assign", "answer", "review", "plan")
"""The four powers a manager may be given, in the card's order."""

POWER_OPERATIONS: Final[dict[str, frozenset[str]]] = {
    "assign": frozenset({"orch_assign", "orch_reorder_queue", "orch_suggest"}),
    "answer": frozenset({"orch_answer"}),
    "review": frozenset({"orch_review_task"}),
    "plan": frozenset({"orch_set_task", "orch_split_task"}),
}
"""What each power switches on: operations of its role's list, over its own subtree."""


def switched_on(powers: ManagerPowers | None) -> frozenset[str]:
    """The operations `powers` switch on (none, when a manager has none of them)."""
    if powers is None:
        return frozenset()
    on: set[str] = set()
    for power in POWERS:
        if getattr(powers, power):
            on |= POWER_OPERATIONS[power]
    return frozenset(on)


async def reports(agents: stored.AgentRecords, name: str) -> list[stored.Agent]:
    """The agents that report to `name` directly."""
    every = await agents.agents()
    return [one for one in every if one.manager == name]


async def subtree(agents: stored.AgentRecords, name: str) -> set[str]:
    """`name` and every agent below it (a cycle, if one were ever made, is not followed twice)."""
    found: set[str] = {name}
    frontier = [name]
    while frontier:
        here = frontier.pop()
        for one in await reports(agents, here):
            if one.name in found:
                continue
            found.add(one.name)
            frontier.append(one.name)
    return found


async def in_subtree(agents: stored.AgentRecords, who: str, other: str) -> bool:
    """Whether `other` is `who` or reports to it, however far down (27.8: a manager's reach)."""
    return other in await subtree(agents, who)


def managers_of(every: Sequence[stored.Agent], name: str) -> list[stored.Agent]:
    """The managers above `name`, nearest first."""
    by_name: Mapping[str, stored.Agent] = {one.name: one for one in every}
    found: list[stored.Agent] = []
    seen: set[str] = set()
    here = by_name.get(name)
    while here is not None and here.manager and here.manager not in seen:
        seen.add(here.manager)
        above = by_name.get(here.manager)
        if above is None:
            break
        found.append(above)
        here = above
    return found
