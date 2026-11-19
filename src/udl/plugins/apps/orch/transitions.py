"""
------------------
- transitions.py -
------------------

Task states and their transitions (Phase 26.3, the card's second bullet).

`TRANSITIONS` is the table: `(from_state, to_state) → the roles that may`, one row per
(state, operation) pair the Concepts table allows. `move()` is the one code path that sets a
task's `state` — it raises `NotAllowed` naming the rule for anything the table does not allow,
and for an agent touching a task that is not its own.

Only a `reviewer` agent or a human reaches `done`: an implementer cannot mark its own work
reviewed (O-C7's rule, on the task's side).
"""

# System Imports
from typing import Final

# Local Imports
from udl.errors import NotAllowed
from udl.plugins.apps.orch.models import (
    BLOCKED,
    DONE,
    MERGED,
    MOVED,
    OPEN,
    QUEUED,
    READY,
    Principal,
)
from udl.plugins.apps.orch.records import Task

__all__ = ["TRANSITIONS", "allowed", "move", "roles"]

# (Every state, operation and caller role the Concepts table allows. A role is what the caller
# is doing: assigning, starting, blocking, readying, merging, reviewing, moving, cancelling.)
TRANSITIONS: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    # -- open: not assigned -------------------------------------------------------------------
    (OPEN, QUEUED): ("assign", "move", "human"),
    (OPEN, "in_progress"): ("start", "human"),
    (OPEN, BLOCKED): ("block", "human"),
    (OPEN, "cancelled"): ("cancel", "human"),
    (OPEN, MOVED): ("move", "human"),
    # -- queued: in an agent's queue ----------------------------------------------------------
    (QUEUED, "in_progress"): ("start", "human"),
    (QUEUED, OPEN): ("assign", "move", "human"),
    (QUEUED, BLOCKED): ("block", "human"),
    (QUEUED, "cancelled"): ("cancel", "move", "human"),
    (QUEUED, MOVED): ("move", "human"),
    # -- in_progress: the agent is on it -----------------------------------------------------
    ("in_progress", READY): ("ready", "human"),
    ("in_progress", BLOCKED): ("block", "human"),
    ("in_progress", QUEUED): ("assign", "move", "human"),
    ("in_progress", "cancelled"): ("cancel", "move", "human"),
    # -- blocked: only a human or a manager unblocks ------------------------------------------
    (BLOCKED, "in_progress"): ("start", "block", "move", "human"),
    (BLOCKED, QUEUED): ("assign", "move", "human"),
    (BLOCKED, "cancelled"): ("cancel", "move", "human"),
    # -- ready: PR green, waiting to be merged -------------------------------------------------
    (READY, MERGED): ("merge", "human"),
    (READY, "in_progress"): ("ready", "move", "human"),  # (back to work: changes were asked for)
    (READY, BLOCKED): ("block", "move", "human"),
    (READY, "cancelled"): ("cancel", "move", "human"),
    # -- merged: the GitHub watcher saw it -----------------------------------------------------
    (MERGED, DONE): ("review", "human"),
    (MERGED, "in_progress"): ("move", "human"),  # (a stacked branch to rebase)
    # -- done: reviewed (only a reviewer or a human) -------------------------------------------
    (DONE, "in_progress"): ("move", "human"),
    # -- moved / cancelled: nothing moves on, but a human may undo -----------------------------
    (MOVED, QUEUED): ("move", "human"),
    (MOVED, "cancelled"): ("cancel", "human"),
    ("cancelled", OPEN): ("move", "human"),
    ("cancelled", QUEUED): ("move", "human"),
}

# (The states, as one place to read them from.)
_STATES: Final = frozenset(
    {OPEN, QUEUED, "in_progress", BLOCKED, READY, MERGED, DONE, MOVED, "cancelled"}
)


def roles(from_state: str, to_state: str) -> tuple[str, ...]:
    """The roles that may move a task from `from_state` to `to_state` (empty when none may)."""
    return TRANSITIONS.get((from_state, to_state), ())


def allowed(from_state: str, to_state: str, role: str) -> bool:
    """Whether a caller doing `role` may move a task from `from_state` to `to_state`."""
    return role in roles(from_state, to_state)


def move(
    task: Task,
    to: str,
    *,
    by: Principal,
    note: str = "",
) -> Task:
    """`task` moved to `to`, by `by` (who is doing one of the roles, or a person).

    Raises NotAllowed naming the rule when `to` is not a state a task is in, when the task is
    already there, when the table does not allow `by`'s role that move (naming the roles that
    may), and when an agent moves a task that is not its own (or one nobody has assigned). A
    person may move a task anywhere: the rules are for the agents. `note` is what a blocked task
    waits on, or why a task was cancelled or moved."""
    if to not in _STATES:
        raise NotAllowed(
            f"{task.key!r} cannot move to {to!r}: it is not a state a task is in",
            hint=f"It is in {task.state!r}, one of: {', '.join(sorted(_STATES))}.",
        )
    if to == task.state:
        raise NotAllowed(
            f"{task.key!r} cannot move from {task.state!r} to {to!r}: it is already there",
            hint=f"A move is to another state, not the one it is in ({task.state!r}).",
        )
    who = by.who
    if to == DONE and not (by.is_reviewer or by.is_human):
        raise NotAllowed(
            f"{task.key!r} cannot be marked done by {who}: only a reviewer or a human reviews",
            hint="O-C7: an implementer never marks its own work reviewed.",
        )
    if not by.is_human:
        if not _is_yours(task, by):
            raise NotAllowed(
                f"{task.key!r} is not yours to move: it is {task.assignee or 'nobody'}'s",
                hint="An implementer moves only its own tasks; a human moves any.",
            )
        role = by.role or _default_role(task.state, to)
        if role is None or role not in roles(task.state, to):
            raise NotAllowed(
                f"{task.key!r} cannot move from {task.state!r} to {to!r} by {who}",
                hint=_rule(task.state, to),
            )
    return task.model_copy(
        update={
            "state": to,
            "blocked_on": note if to == BLOCKED else None,
            "assignee": None if to == MOVED else task.assignee,
            "updated_by": who,
        }
    )


def _default_role(from_state: str, to: str) -> str | None:
    """The role a move from `from_state` to `to` is when the caller does not say: the first the
    table allows that an agent does (never a person's own), or None when the table has none.

    Read from the table itself, so a caller that says nothing is never given a verb the table
    would refuse."""
    return next((may for may in roles(from_state, to) if may != "human"), None)


def _is_yours(task: Task, by: Principal) -> bool:
    """Whether `by` may touch `task`: udl itself may (the dispatcher assigns, the watcher merges),
    the reviewer may (it reviews other agents' work), and an agent only its own task. (A person
    is asked before this.)"""
    if by.kind == "system" or by.is_reviewer:
        return True
    return task.assignee is not None and task.assignee == by.who


def _rule(from_state: str, to_state: str) -> str:
    """The rule a refusal names: what may move a task from `from_state` to `to_state`."""
    who = roles(from_state, to_state)
    if who:
        return f"Only {' or '.join(who)} may move a task from {from_state!r} to {to_state!r}."
    return f"No role moves a task from {from_state!r} to {to_state!r}."
