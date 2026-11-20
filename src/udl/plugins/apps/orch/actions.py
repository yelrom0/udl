"""
-----------------
- actions.py -
-----------------

Human actions (28.2): something only a person can do, with a result line — what human-comms.md's
"To action" section is today, as records.

An action is `now`, `after` a task, or `later`. One that waits `after` a task becomes `now` when
that task merges (`become_now()`, which 28.3 calls on a merge). A person's task (`task_kind`
`human`) has an action from the moment it is assigned to `human:<name>` (`for_task()`), and
finishing that action moves the task to `merged`: a person's work is done when they say it is,
not when a PR merges.

`finish()` writes its result as a note on the task the action is about (26.5), so what a person
did is in the task's history like everything else.
"""

# Package Imports
from pydantic import JsonValue

# Local Imports
from udl.db.records import RecordQuery
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch.models import (
    AFTER,
    OPEN,
    ActionState,
    ActionWhen,
    NoteKind,
    OwnerKind,
    Principal,
)
from udl.plugins.apps.orch.records import (
    Counters,
    HumanAction,
    Note,
    NoteRecords,
    ProjectRecords,
    Task,
    TaskRecords,
)

__all__ = [
    "ACTIONS",
    "ActionRecords",
    "become_now",
    "drop",
    "finish",
    "for_task",
    "set_action",
]

ACTIONS = "A-"
"""What an action's key is counted from (`A-1`, `A-2` …)."""

_ORDER: dict[ActionWhen, int] = {ActionWhen.NOW: 0, ActionWhen.AFTER: 1, ActionWhen.LATER: 2}
"""The order actions are due in: now, then after, then later."""


class ActionRecords:
    """Where orch keeps its human actions: read by project, state, when and person."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the actions are kept in."""
        return self._records

    async def save_action(self, action: HumanAction) -> HumanAction:
        """Store `action` (by its id, so storing one again replaces it)."""
        return await self._records.save(action)

    async def action(self, project: str, key: str) -> HumanAction | None:
        """The action called `key` of `project`, or None when there is none."""
        found = await self._records.list(
            HumanAction, RecordQuery(where={"project": project, "key": key})
        )
        return found[0] if found else None

    async def actions(
        self,
        project: str,
        *,
        state: ActionState | str | None = OPEN,
        when: ActionWhen | str | None = None,
        assignee: str | None = None,
        task: str | None = None,
    ) -> list[HumanAction]:
        """The actions of `project` that match what is given, due first (now, after, later),
        then oldest first. `state` is None for every state."""
        where: dict[str, JsonValue] = {"project": project}
        if assignee is not None:
            where["assignee"] = assignee
        if task is not None:
            where["task"] = task
        found = await self._records.list(HumanAction, RecordQuery(where=where))
        if state is not None:
            found = [action for action in found if action.state == state]
        if when is not None:
            found = [action for action in found if action.when == when]
        return sorted(
            found, key=lambda action: (_ORDER[ActionWhen(action.when)], action.created_at)
        )

async def set_action(
    actions: ActionRecords,
    *,
    project: str,
    title: str,
    text: str = "",
    when: ActionWhen | str = ActionWhen.NOW,
    after: str | None = None,
    task: str | None = None,
    assignee: str | None = None,
) -> HumanAction:
    """Add an action to `project`, with the next `A-` number. Raises DoesNotExist when the
    project is not there, and NotAllowed for a `when` that does not match what it waits for, or
    for an assignee that is an agent (an action is a person's)."""
    await ProjectRecords(actions.records).require(project)
    due = ActionWhen(when)
    _check_when(due, after=after)
    tasks = TaskRecords(actions.records)
    for named in (task, after):
        if named is not None and await tasks.task(project, named) is None:
            raise DoesNotExist(f"There is no task called {named!r} in {project!r}")
    if assignee is not None:
        _check_person(assignee)
    number = await Counters(actions.records).claim(project, ACTIONS)
    return await actions.save_action(
        HumanAction(
            project=project,
            key=f"{ACTIONS}{number}",
            title=title,
            text=text,
            when=due,
            after=after,
            task=task,
            assignee=assignee,
        )
    )


def _check_when(when: ActionWhen, *, after: str | None) -> None:
    """Raises NotAllowed when `when` and `after` disagree: an action that waits for a task names
    it, and one that does not names none."""
    if when is ActionWhen.AFTER and after is None:
        raise NotAllowed(
            "An action after a task must say after which task",
            hint="Name the task it waits for, by its key (`28.3`), or set it to now or later.",
        )
    if when is not ActionWhen.AFTER and after is not None:
        raise NotAllowed(
            f"An action that is {when.value} does not wait for a task",
            hint="Only an action `after` a task names one.",
        )


def _check_person(assignee: str) -> None:
    """Raises NotAllowed when `assignee` is not a person (`human:<name>`)."""
    if not assignee.startswith("human:"):
        raise NotAllowed(
            f"An action is a person's: {assignee!r} is an agent's name",
            hint="Assign it to human:<name>, or to nobody.",
        )
    if not assignee.removeprefix("human:").strip():
        raise NotAllowed(
            f"An action is a person's: {assignee!r} has no name",
            hint="Assign it to human:<name>, or to nobody.",
        )


async def become_now(actions: ActionRecords, *, project: str, task: str) -> list[HumanAction]:
    """Every open action waiting `after` the task called `task` is `now` again. Answers with the
    ones that changed (28.3 calls this when a task merges)."""
    waiting = await actions.actions(project, state=OPEN, when=AFTER)
    changed: list[HumanAction] = []
    for action in waiting:
        if action.after != task:
            continue
        changed.append(
            await actions.save_action(
                action.model_copy(update={"when": ActionWhen.NOW, "after": None})
            )
        )
    return changed


async def finish(
    actions: ActionRecords,
    *,
    project: str,
    key: str,
    result: str,
    by: Principal | None = None,
) -> HumanAction:
    """Finish the action called `key`: it is `done`, with `result`, which is written as a note on
    the task it is about — and that task, when it is a person's (`task_kind` `human`), is
    `merged`.

    Raises DoesNotExist when there is no such action, and NotAllowed when it is not open."""
    action = await _action(actions, project=project, key=key)
    if action.state is not ActionState.OPEN:
        raise NotAllowed(
            f"{key!r} is already {action.state.value}",
            hint="Only an open action is finished or dropped.",
        )
    author = (by.who if by is not None else "") or "human"
    tasks = TaskRecords(actions.records)
    if action.task is not None:
        task = await tasks.task(project, action.task)
        if task is not None and task.task_kind == "human" and task.state == "merged":
            # (Before anything is written: a second action about a task that is already done
            # would write its note and then fail the move.)
            raise NotAllowed(
                f"{action.task!r} is already merged",
                hint="The task this action is about is done; there is nothing left to finish.",
            )
        if task is not None:
            await NoteRecords(actions.records).add(
                Note(
                    owner_kind=OwnerKind.TASK,
                    owner=task.id,
                    note_kind=NoteKind.PROGRESS,
                    text=result,
                    author=author,
                )
            )
            if task.task_kind == "human":
                # (A person's task is done when they say it is: there is no PR to merge. It
                # leaves their list, and the list closes up behind it.)
                person = task.assignee
                moved = await tasks.move(
                    task, "merged", by=Principal(kind="human", name=author, role="merge")
                )
                await tasks.save_task(moved.model_copy(update={"assignee": None, "position": None}))
                if person is not None:
                    left = await queues.queue_of(tasks, project=project, agent=person)
                    if left:
                        await queues.reorder(
                            tasks,
                            project=project,
                            agent=person,
                            keys=[found.key for found in left],
                        )
                # (A person's task merges here, with no PR: what waited `after` it is now due.)
                await become_now(actions, project=project, task=task.key)
    return await actions.save_action(
        action.model_copy(update={"state": ActionState.DONE, "result": result})
    )


async def drop(actions: ActionRecords, *, project: str, key: str, reason: str = "") -> HumanAction:
    """Drop the action called `key` (it is not going to be done), with `reason`.

    Raises DoesNotExist when there is no such action, and NotAllowed when it is not open."""
    action = await _action(actions, project=project, key=key)
    if action.state is ActionState.DROPPED:
        # (Dropping is idempotent: a client that retries gets the same answer, 28.2.)
        return action
    if action.state is not ActionState.OPEN:
        raise NotAllowed(
            f"{key!r} is already {action.state.value}",
            hint="Only an open action is finished or dropped.",
        )
    return await actions.save_action(
        action.model_copy(update={"state": ActionState.DROPPED, "result": reason or None})
    )


async def for_task(actions: ActionRecords, task: Task, *, assignee: str) -> HumanAction:
    """The action of the person's task `task`, given to `assignee`: the one it has, or a new one
    (its title and text are the task's). Raises NotAllowed when `task` is not a person's."""
    if task.task_kind != "human":
        raise NotAllowed(
            f"{task.key!r} is not a person's task",
            hint="Only a `human` task has an action (28.2's To action).",
        )
    found = await actions.actions(task.project, state=None, task=task.key)
    if found:
        # (A dropped one is reopened: the task is somebody's again, so the action is open again
        # — otherwise it would never reach `merged` through 28.2.)
        return await actions.save_action(
            found[0].model_copy(
                update={
                    "assignee": assignee,
                    "state": ActionState.OPEN,
                    "result": None,
                }
            )
        )
    return await set_action(
        actions,
        project=task.project,
        title=task.title,
        text=task.spec,
        task=task.key,
        assignee=assignee,
    )


async def _action(actions: ActionRecords, *, project: str, key: str) -> HumanAction:
    """The action called `key` of `project`. Raises DoesNotExist when there is none."""
    found = await actions.action(project, key)
    if found is None:
        raise DoesNotExist(f"There is no action called {key!r} in {project!r}")
    return found
