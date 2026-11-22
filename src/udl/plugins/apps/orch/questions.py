"""
-----------------
- questions.py -
-----------------

Questions (28.1): what an agent cannot answer itself, asked of the agent's manager when that
manager may answer, and of the humans when it may not.

A question is `Q-<n>` of its project, and its thread is its `Message`s — the question's own text
first, then a follow-up from either side (`reply()`). What it is about is its `Link`s (26.4): the
task it was asked in, and any other task or feature it names, so an answer has somewhere to land.

A blocking one moves its task to `blocked` with the question's key on it, so the agent's loop
leaves it and takes the next queued task (27.7). Answering it (`answer()`) writes a `decision` note
on every task and feature it names and puts the task back at the front of its queue — all of it in
one write (27.3a's rule: half of it is a question marked answered with nothing to show for it).

`escalate()` passes a question up: to the asker's manager's manager when that one may answer, and
to the humans at the top. `close()` is for a question nobody answers, with what was done instead.
"""

# System Imports
import logging
from collections.abc import Sequence
from datetime import datetime

# Package Imports
from pydantic import BaseModel

# Local Imports
from udl.clock import Clock
from udl.db.models import utcnow
from udl.db.records import Record
from udl.errors import DoesNotExist, InUse, NotAllowed
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch import transitions
from udl.plugins.apps.orch.events import (
    QUESTION_ANSWERED,
    QUESTION_ASKED,
    QuestionAnswered,
    QuestionAsked,
)
from udl.plugins.apps.orch.models import (
    BLOCKED,
    QUEUED,
    NoteKind,
    OwnerKind,
    Principal,
    QuestionState,
)
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Counters,
    Feature,
    FeatureRecords,
    Link,
    LinkRecords,
    Message,
    Note,
    ProjectRecords,
    Question,
    QuestionRecords,
    StateChange,
    Task,
    TaskRecords,
    move_note,
)

__all__ = [
    "HUMANS",
    "QUESTIONS",
    "answer",
    "ask",
    "block",
    "close",
    "escalate",
    "reply",
    "route",
]

log = logging.getLogger(__name__)

QUESTIONS = "Q-"
"""What a question's key is counted from (`Q-1`, `Q-2` …)."""

HUMANS = "humans"
"""What a question is asked of when no agent may answer it."""

_Named = tuple[OwnerKind, Task | Feature]
"""A task or a feature a question names, with which of the two it is."""


async def ask(
    questions: QuestionRecords,
    *,
    project: str,
    task: str,
    title: str,
    text: str = "",
    options: Sequence[str] = (),
    blocking: bool = False,
    tasks: Sequence[str] = (),
    features: Sequence[str] = (),
    asked_by: str = "",
    clock: Clock | None = None,
) -> Question:
    """Ask `title` in `task`, of the asker's manager when that manager may answer and of the
    humans otherwise. A blocking question moves `task` to `blocked`, in the same write — and its
    move is in the task's history, on the app's clock (27.10: the blocked hours are not work).

    Raises DoesNotExist when the project, the task, or one of the tasks or features it names is
    not there, and NotAllowed when the task is one the table does not let go to `blocked` (a
    `done` one, say) — before anything is written."""
    await ProjectRecords(questions.records).require(project)
    tasks_of = TaskRecords(questions.records)
    asked_in = await tasks_of.task(project, task)
    if asked_in is None:
        raise DoesNotExist(f"There is no task called {task!r} in {project!r}")
    if blocking and asked_in.state == BLOCKED:
        # (One question at a time: a second one would overwrite what the task waits on, and the
        # first would then be answered into nothing (the review's fifth).)
        raise InUse(
            f"{task!r} already waits on {asked_in.blocked_on or 'a question'}",
            hint=(
                "Answer the question it waits on, and ask again if it still stands. Closing it "
                "does not unblock the task: a person moves it on, or it is answered."
            ),
        )
    named = await _named(questions, project=project, tasks=tasks, features=features)
    to = await route(questions, asked_by=asked_by)
    number = await Counters(questions.records).claim(project, QUESTIONS)
    asked = Question(
        project=project,
        key=f"{QUESTIONS}{number}",
        asked_by=asked_by,
        to=to,
        title=title,
        text=text,
        options=list(options),
        blocking=blocking,
        task=task,
    )
    batch: list[Record] = [
        asked,
        Message(question=asked.id, text=text or title, author=asked_by or "udl"),
        _link(asked, OwnerKind.TASK, task, title=asked_in.title),
        *(_link(asked, kind, one.key, title=one.title) for kind, one in named),
    ]
    if blocking:
        batch.extend(_blocking(asked_in, asked, asked_by=asked_by, at=_now(clock)))
    saved = await questions.records.save_all(batch)
    asked = next(one for one in saved if isinstance(one, Question))
    await _told(
        questions,
        QUESTION_ASKED.name,
        QuestionAsked(
            project=asked.project,
            question=asked.key,
            task=asked.task or "",
            blocking=asked.blocking,
            asked_by=asked.asked_by,
            to=asked.to,
        ),
        key=f"question.asked:{asked.id}",
    )
    return asked


async def _told(questions: QuestionRecords, event_type: str, data: BaseModel, *, key: str) -> None:
    """Publish `data` as `event_type` once (28.5), when these records publish at all: a question
    is how a person hears of what an agent needs, and an event that cannot be published is not
    worth failing the question for."""
    events = questions.records.events
    if events is None:
        return
    try:
        await events.emit(event_type, data, dedupe_key=key)
    except Exception:
        log.warning("The %s event could not be published", event_type, exc_info=True)


async def block(
    questions: QuestionRecords,
    *,
    project: str,
    task: str,
    reason: str,
    asked_by: str = "",
    clock: Clock | None = None,
) -> Question:
    """`ask()`, blocking: only a person can do this (the card's `orch_block`)."""
    return await ask(
        questions,
        project=project,
        task=task,
        title=reason,
        text=reason,
        blocking=True,
        asked_by=asked_by,
        clock=clock,
    )


async def route(questions: QuestionRecords, *, asked_by: str) -> str:
    """Who a question asked by `asked_by` goes to: its manager when that manager is one and may
    answer, and the humans otherwise."""
    asker = await AgentRecords(questions.records).agent(asked_by) if asked_by else None
    return await _up_from(questions, asker)


async def answer(
    questions: QuestionRecords,
    *,
    project: str,
    key: str,
    answer: str,
    by: Principal | None = None,
    clock: Clock | None = None,
) -> Question:
    """Answer the question called `key`: it is `answered`, a `decision` note says so on every task
    and feature it names, and a task it blocked is `queued` again at the front of its agent's
    queue — all of it in one write.

    Raises DoesNotExist when there is no such question, and NotAllowed when it is not open."""
    found = await _question(questions, project=project, key=key)
    if found.state is not QuestionState.OPEN:
        raise NotAllowed(
            f"{key!r} is already {found.state.value}",
            hint="A question is answered once; a follow-up is `orch_reply`.",
        )
    who = (by.who if by is not None else "") or "human"
    said = f"{found.key}, answered by {who}: {answer}"
    batch: list[Record] = [
        found.model_copy(
            update={"state": QuestionState.ANSWERED, "answer": answer, "answered_by": who}
        ),
        Message(question=found.id, text=answer, author=who),
        *await _decisions(questions, found, text=said, author=who),
        *await _unblocked(questions, found, at=_now(clock)),
    ]
    saved = await questions.records.save_all(batch)
    answered = next(one for one in saved if isinstance(one, Question))
    await _told(
        questions,
        QUESTION_ANSWERED.name,
        QuestionAnswered(
            project=answered.project,
            question=answered.key,
            task=answered.task or "",
            by=answered.answered_by or "",
            answer=answered.answer or "",
        ),
        key=f"question.answered:{answered.id}",
    )
    return answered


async def reply(
    questions: QuestionRecords, *, project: str, key: str, text: str, by: Principal | None = None
) -> Message:
    """Add `text` to the question's thread, from whoever says it (either side).

    Raises DoesNotExist when there is no such question, and NotAllowed when it is closed: what
    comes after a closed question is another question."""
    found = await _question(questions, project=project, key=key)
    if found.state is QuestionState.CLOSED:
        raise NotAllowed(
            f"{key!r} is closed",
            hint="Ask again in the task: a closed question is history.",
        )
    return await questions.say(found, text, author=(by.who if by is not None else "") or "human")


async def escalate(
    questions: QuestionRecords,
    *,
    project: str,
    key: str,
    why: str = "",
    by: Principal | None = None,
) -> Question:
    """Pass the question up: to the manager's manager when that one may answer, and to the humans
    at the top. A blocked task stays blocked (it still waits for the answer).

    Raises DoesNotExist when there is no such question, and NotAllowed when it is not open."""
    found = await _question(questions, project=project, key=key)
    if found.state is not QuestionState.OPEN:
        raise NotAllowed(
            f"{key!r} is already {found.state.value}",
            hint="Only an open question is passed up.",
        )
    if found.to == HUMANS:
        raise NotAllowed(
            f"{key!r} is already the humans'",
            hint="There is nowhere above the humans: say why in a reply, or close it.",
        )
    agents = AgentRecords(questions.records)
    asked_of = await agents.agent(found.to)
    if asked_of is None:
        raise DoesNotExist(f"There is no agent called {found.to!r} to pass {key!r} up from")
    up = await _up_from(questions, asked_of)
    said = why or f"Passed up to {up}."
    author = (by.who if by is not None else "") or "udl"
    saved = await questions.records.save_all(
        [
            found.model_copy(update={"to": up, "escalated": True}),
            Message(question=found.id, text=said, author=author),
        ]
    )
    return next(one for one in saved if isinstance(one, Question))


async def close(questions: QuestionRecords, *, project: str, key: str, outcome: str) -> Question:
    """Close the question called `key` with what was done about it instead. A task a blocking
    question left blocked stays blocked: closing is not answering.

    Raises DoesNotExist when there is no such question, and NotAllowed when it is not open."""
    found = await _question(questions, project=project, key=key)
    if found.state is not QuestionState.OPEN:
        raise NotAllowed(
            f"{key!r} is already {found.state.value}",
            hint="Only an open question is closed.",
        )
    return await questions.save_question(
        found.model_copy(update={"state": QuestionState.CLOSED, "outcome": outcome})
    )


async def named(questions: QuestionRecords, question: Question) -> list[_Named]:
    """The tasks and features `question` names (its links, in the order it names them)."""
    links = await LinkRecords(questions.records).for_owner(
        question.project, OwnerKind.QUESTION, question.key
    )
    found: list[_Named] = []
    for link in links:
        key = link.url.removeprefix("udl:")
        if link.link_kind == "task":
            task = await TaskRecords(questions.records).task(question.project, key)
            if task is not None:
                found.append((OwnerKind.TASK, task))
        elif link.link_kind == "feature":
            feature = await FeatureRecords(questions.records).feature(question.project, key)
            if feature is not None:
                found.append((OwnerKind.FEATURE, feature))
    return found


async def _up_from(questions: QuestionRecords, agent: Agent | None) -> str:
    """Who is above `agent`: its manager when that one is a manager with `answer`, else up its
    own managers to the first that may, and the humans at the top."""
    agents = AgentRecords(questions.records)
    seen: set[str] = set()
    found = agent
    while found is not None and found.manager is not None:
        if found.manager in seen:
            break  # (AgentRecords refuses to store a cycle; this is only a guard)
        seen.add(found.manager)
        manager = await agents.agent(found.manager)
        if manager is None:
            break
        if manager.role == "manager" and manager.manages.answer:
            return manager.name
        found = manager
    return HUMANS


def _blocking(task: Task, question: Question, *, asked_by: str, at: datetime) -> list[Record]:
    """`task` moved to `blocked`, waiting on `question`, and the note that says so — planned here
    so the question and the block are one write."""
    who = (
        Principal(kind="agent", name=asked_by, role="block")
        if asked_by and task.assignee == asked_by
        else Principal(kind="system", role="block")
    )
    moved = transitions.move(task, BLOCKED, by=who, note=question.key)
    return [moved, move_note(task, moved, who), _change(task, moved, at=at, by=who.who)]


async def block_with(
    questions: QuestionRecords,
    *,
    project: str,
    task: str,
    question: Question,
    asked_by: str = "",
    why: str = "",
    clock: Clock | None = None,
) -> Task:
    """Block `task` on `question` (an open one it already has): one write, and the note that says
    what it waits on.

    A task already blocked is left blocked, whatever it waits on — it is waiting already, and a
    `blocked → blocked` move is one the table refuses — and `why` (the reason it is waiting now:
    a stall, a failed session) is said on the question's thread, so the manager reads it beside
    the question it was asked.

    Raises DoesNotExist when there is no such task."""
    tasks = TaskRecords(questions.records)
    found = await tasks.task(project, task)
    if found is None:
        raise DoesNotExist(f"There is no task called {task!r} in {project!r}")
    said = [Message(question=question.id, text=why, author="udl")] if why else []
    if found.state == BLOCKED:
        if said:
            await questions.records.save_all(said)
        return found
    saved = await questions.records.save_all(
        [*_blocking(found, question, asked_by=asked_by, at=_now(clock)), *said]
    )
    return next(one for one in saved if isinstance(one, Task))


async def _unblocked(
    questions: QuestionRecords, question: Question, *, at: datetime
) -> list[Record]:
    """What answering `question` writes besides the answer: its task, when it is blocked on this
    question, back to `queued` at the front of its agent's queue (the rest renumbered)."""
    if question.task is None:
        return []
    tasks = TaskRecords(questions.records)
    found = await tasks.task(question.project, question.task)
    if found is None or found.state != BLOCKED or found.blocked_on != question.key:
        # (Not blocked on this question: it was answered mid-work, or it has moved on since.)
        return []
    who = Principal(kind="system", role="assign")
    placed = transitions.move(found, QUEUED, by=who).model_copy(update={"position": 0})
    others = [
        one
        for one in await queues.queue_of(
            tasks, project=question.project, agent=found.assignee or ""
        )
        if one.key != found.key
    ]
    renumbered = [
        one.model_copy(update={"position": number + 1}) for number, one in enumerate(others)
    ]
    return [
        placed,
        *renumbered,
        move_note(found, placed, who),
        _change(found, placed, at=at, by=who.who),
    ]


def _now(clock: Clock | None) -> datetime:
    """When a move is written: the app's clock when it was given one, else the wall clock."""
    return clock.now() if clock is not None else utcnow()


def _change(before: Task, after: Task, *, at: datetime, by: str) -> StateChange:
    """The history entry a move writes when it is planned in a batch of its own (27.10: every
    move has one, `TaskRecords.save_all`'s or this)."""
    return StateChange(task=before.id, from_state=before.state, to_state=after.state, at=at, by=by)


async def _decisions(
    questions: QuestionRecords, question: Question, *, text: str, author: str
) -> list[Record]:
    """The `decision` notes answering `question` writes: one on every task and feature it names."""
    return [
        Note(owner_kind=kind, owner=owner.id, note_kind=NoteKind.DECISION, text=text, author=author)
        for kind, owner in await named(questions, question)
    ]


def _link(question: Question, kind: OwnerKind, key: str, *, title: str) -> Link:
    """The link from `question` to the task or feature called `key` (26.4: `udl:<key>`)."""
    return Link(
        project=question.project,
        owner_kind=OwnerKind.QUESTION,
        owner=question.key,
        link_kind="task" if kind is OwnerKind.TASK else "feature",
        url=f"udl:{key}",
        title=title or key,
        added_by=question.asked_by,
    )


async def _named(
    questions: QuestionRecords,
    *,
    project: str,
    tasks: Sequence[str],
    features: Sequence[str],
) -> list[_Named]:
    """The tasks and features a question names, each one there (DoesNotExist otherwise)."""
    found: list[_Named] = []
    for key in tasks:
        task = await TaskRecords(questions.records).task(project, key)
        if task is None:
            raise DoesNotExist(f"There is no task called {key!r} in {project!r}")
        found.append((OwnerKind.TASK, task))
    for key in features:
        feature = await FeatureRecords(questions.records).feature(project, key)
        if feature is None:
            raise DoesNotExist(f"There is no feature called {key!r} in {project!r}")
        found.append((OwnerKind.FEATURE, feature))
    return found


async def _question(questions: QuestionRecords, *, project: str, key: str) -> Question:
    """The question called `key` of `project`. Raises DoesNotExist when there is none."""
    found = await questions.question(project, key)
    if found is None:
        raise DoesNotExist(f"There is no question called {key!r} in {project!r}")
    return found
