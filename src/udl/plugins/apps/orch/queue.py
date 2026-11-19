"""
---------------
- queue.py -
---------------

Agents' queues (27.3, the card's second bullet): what an agent is to work, in the order it works
it.

A task on a queue is `queued` and assigned to an agent; its `position` is its place, and a
queue's positions are dense (0, 1, 2 …) so the order is read from them alone. `assign()` puts a
task in a queue (at its end, or where it is asked), `reorder()` sets a queue's order to exactly
the keys it is given, and `unassign()` takes a task out of a queue and closes the gap. Each of
them renumbers the queue it changes, so no caller ever sees a gap.

Assigning a task another agent holds moves it: it leaves that agent's queue, a `system` note says
who must stop working on it (26.5), and the dispatcher stops that agent's session (27.7). Every
state change goes through `orch.transitions.move()`, so a task a role may not move is refused
here too.

A person's task (`task_kind` `human`) is assigned to `human:<name>` and appears in that person's
own list, never in an agent's queue (28.2's To action).
"""

# System Imports
from collections.abc import Sequence

# Local Imports
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.orch import transitions
from udl.plugins.apps.orch.models import (
    IN_PROGRESS,
    MOVED,
    OPEN,
    QUEUED,
    NoteKind,
    OwnerKind,
    Principal,
    Role,
)
from udl.plugins.apps.orch.records import (
    AgentRecords,
    Note,
    NoteRecords,
    Task,
    TaskRecords,
)

__all__ = ["HUMAN", "assign", "next_task", "queue_of", "reorder", "unassign"]

HUMAN = "human:"
"""What a person's assignee begins with (`human:<name>`)."""


def _by(by: Principal | None, role: Role) -> Principal:
    """Who is moving a task: `by`, or udl itself doing `role` (the queue's own moves)."""
    if by is not None:
        return by if by.role is not None else by.model_copy(update={"role": role})
    return Principal(kind="system", role=role)


async def assign(
    tasks: TaskRecords,
    key: str,
    agent: str,
    *,
    project: str,
    position: int | None = None,
    by: Principal | None = None,
) -> Task:
    """The task called `key` in `agent`'s queue, at `position` (the end, when it is None).

    Raises DoesNotExist when there is no such task, and NotAllowed when `position` is outside its
    queue (0 to its length), when a person's task is given to an agent, and when the task is in a
    state the table does not let a caller move to `queued` (a `ready` task has a PR waiting).
    Assigning a task another agent holds leaves that agent's queue (renumbered), and a system note
    says who must stop."""
    task = await tasks.task(project, key)
    if task is None:
        raise DoesNotExist(f"There is no task called {key!r} in {project!r}")
    await _check_agent(tasks, task, agent, key=key)
    if task.assignee == agent and task.state != QUEUED:
        # (Its session is working the task: putting it back on that queue would leave the
        # session working what is no longer its own, and nobody told to stop.)
        raise NotAllowed(
            f"{key!r} is already {agent}'s, and it is on it ({task.state!r})",
            hint="Unassign it first, or reorder the queue it is on.",
        )
    others = [
        found for found in await queue_of(tasks, project=project, agent=agent) if found.key != key
    ]
    at = len(others) if position is None else position
    if at < 0 or at > len(others):
        raise NotAllowed(
            f"{key!r} cannot go to position {at} of {agent}'s queue",
            hint=f"The positions are 0 to {len(others)} (its end, when it is {len(others)}).",
        )
    stopped = task.assignee if task.assignee is not None and task.assignee != agent else None
    # (A reassign is one move, twice: to `moved` for the agent that had it, then to `queued` for
    # the new one, both with the role `move` the table gives that pair.)
    role: Role = "move" if stopped is not None else "assign"
    # (The moves are planned on copies first, so a refusal cannot leave a task half-moved.)
    planned = task
    if stopped is not None:
        planned = transitions.move(planned, MOVED, by=_by(by, "move"))
    if planned.state != QUEUED:
        planned = transitions.move(planned, QUEUED, by=_by(by, role))
    current = task
    if stopped is not None:
        current = await tasks.move(current, MOVED, by=_by(by, "move"))
    if current.state != QUEUED:
        current = await tasks.move(current, QUEUED, by=_by(by, role))
    placed = await tasks.save_task(current.model_copy(update={"assignee": agent, "position": at}))
    order = [*others[:at], placed, *others[at:]]
    await _place(tasks, order)
    if stopped is not None:
        await _stopped(tasks, placed, stopped=stopped, agent=agent)
        await _renumber(tasks, project=project, agent=stopped)
    return placed


async def _check_agent(tasks: TaskRecords, task: Task, agent: str, *, key: str) -> None:
    """`agent` may take `task`: an agent there is (27.1), or a named person for a person's task.

    Raises NotAllowed for a blank name, for `human:` with no name after it, for a person's task
    given to an agent, for a task that is not a person's given to one, and for an agent that is
    not one (a typo would make a queue no dispatcher ever picks up)."""
    if not agent.strip():
        raise NotAllowed(f"{key!r} cannot be assigned to nobody", hint="Name who takes it.")
    if agent.startswith(HUMAN):
        if not agent.removeprefix(HUMAN).strip():
            raise NotAllowed(
                f"{key!r} cannot be assigned to {HUMAN!r}: a person has a name",
                hint=f"Name the person: {HUMAN}<name>.",
            )
        if task.task_kind != "human":
            raise NotAllowed(
                f"{key!r} is not a person's task: it is an agent's",
                hint=(
                    f"A person's task is a `human` one (28.2's To action); assign {key!r} to "
                    "the agent that does it."
                ),
            )
        return
    if task.task_kind == "human":
        raise NotAllowed(
            f"{key!r} is a person's task: it is assigned to {HUMAN}<name>, not {agent!r}",
            hint=f"A human task is a person's (28.2's To action); assign it to {HUMAN}<name>.",
        )
    known = [found.name for found in await AgentRecords(tasks.records).agents()]
    if agent not in known:
        raise NotAllowed(
            f"There is no agent called {agent!r}",
            hint=f"The agents: {', '.join(known) or 'none'} (add it first, 27.1).",
        )


async def reorder(
    tasks: TaskRecords,
    *,
    project: str,
    agent: str,
    keys: Sequence[str],
    by: Principal | None = None,
) -> list[Task]:
    """`agent`'s queue in the order `keys` names, renumbered 0, 1, 2 ….

    Raises NotAllowed when `keys` is not exactly the keys on that queue, each once: a task that
    is not on it is not the reorder's to move, and one it leaves out would be lost."""
    queue = await queue_of(tasks, project=project, agent=agent)
    on_it = [found.key for found in queue]
    if sorted(keys) != sorted(on_it):
        missing = [key for key in on_it if key not in keys]
        extra = [key for key in keys if key not in on_it]
        named = [
            *((f"it is missing {', '.join(missing)}",) if missing else ()),
            *((f"{', '.join(extra)} is not on it",) if extra else ()),
            *((f"it names a task twice ({', '.join(_twice(keys))})",) if _twice(keys) else ()),
        ]
        raise NotAllowed(
            f"{agent}'s queue cannot be reordered by {', '.join(keys) or 'nothing'}: "
            f"{'; '.join(named)}",
            hint=f"Its queue is {', '.join(on_it) or 'empty'}.",
        )
    by_key = {found.key: found for found in queue}
    return await _place(tasks, [by_key[key] for key in keys])


async def unassign(
    tasks: TaskRecords, *, project: str, key: str, by: Principal | None = None
) -> Task:
    """The task called `key`, taken out of its agent's queue: back to `open`, and nobody's.

    Raises DoesNotExist when there is no such task, and NotAllowed when it is nobody's (there is
    no queue to leave) or is not `queued` (a task an agent is on is stopped first, 27.7)."""
    task = await tasks.task(project, key)
    if task is None:
        raise DoesNotExist(f"There is no task called {key!r} in {project!r}")
    if task.assignee is None:
        raise NotAllowed(
            f"{key!r} is nobody's to unassign",
            hint="A task that is not on a queue leaves none.",
        )
    if task.state != QUEUED:
        raise NotAllowed(
            f"{key!r} cannot leave a queue from {task.state!r}",
            hint="Only a queued task is taken off its queue.",
        )
    agent = task.assignee
    freed = await tasks.move(task, OPEN, by=_by(by, "assign"))
    freed = await tasks.save_task(freed.model_copy(update={"assignee": None, "position": None}))
    await _renumber(tasks, project=project, agent=agent)
    return freed


async def queue_of(tasks: TaskRecords, *, project: str, agent: str) -> list[Task]:
    """`agent`'s queue: its queued tasks, in the order it works them (by position)."""
    queued = await tasks.tasks(project, assignee=agent, state=QUEUED)
    return sorted(
        queued, key=lambda found: (found.position is None, found.position or 0, found.key)
    )


async def next_task(tasks: TaskRecords, *, project: str, agent: str) -> Task | None:
    """What `agent` is on: the task it is in progress on, else the first on its queue, or None
    when it has nothing to do."""
    working = await tasks.tasks(project, assignee=agent, state=IN_PROGRESS)
    if working:
        return sorted(
            working, key=lambda found: (found.position is None, found.position or 0, found.key)
        )[0]
    queued = await queue_of(tasks, project=project, agent=agent)
    return queued[0] if queued else None


async def _place(tasks: TaskRecords, order: Sequence[Task]) -> list[Task]:
    """`order`, stored as a queue: each task at its index, and only the ones that moved saved."""
    placed: list[Task] = []
    for position, task in enumerate(order):
        if task.position != position:
            task = await tasks.save_task(task.model_copy(update={"position": position}))
        placed.append(task)
    return placed


async def _renumber(tasks: TaskRecords, *, project: str, agent: str) -> None:
    """`agent`'s queue, closed up: the tasks after a gap take the places they are in."""
    await _place(tasks, await queue_of(tasks, project=project, agent=agent))


async def _stopped(tasks: TaskRecords, task: Task, *, stopped: str, agent: str) -> None:
    """Write the system note that says `stopped` must stop working on `task` (26.5): it was moved
    to `agent`'s queue, and its session is the dispatcher's to stop (27.7)."""
    await NoteRecords(tasks.records).add(
        Note(
            owner_kind=OwnerKind.TASK,
            owner=task.id,
            note_kind=NoteKind.SYSTEM,
            text=f"Moved to {agent}'s queue: {stopped} must stop working on it.",
            author="udl",
        )
    )


def _twice(keys: Sequence[str]) -> list[str]:
    """The keys `keys` names more than once."""
    seen: set[str] = set()
    twice: list[str] = []
    for key in keys:
        if key in seen and key not in twice:
            twice.append(key)
        seen.add(key)
    return twice
