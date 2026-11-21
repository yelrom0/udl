"""
---------------------
- test_questions.py -
---------------------

Questions (28.1): what an agent cannot answer itself, asked of the agent's manager when that
manager may answer, or of the humans when it may not.

A question is `Q-<n>` of its project, with its thread of `Message`s. A blocking one moves its
task to `blocked` and leaves the agent to its next queued task; answering it writes a `decision`
note on every task and feature it names and puts the task back at the front of its queue. The
done-when is that last part: no question is answered without its answer landing on what it names.

These run through `service.call()`, as every interface calls the operations, so what is tested is
the operations and what they write.
"""

# System Imports
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.engine import registry, settings
from udl.config import APISettings
from udl.core.service import UDLService
from udl.db.records import Record
from udl.errors import DatabaseError, DoesNotExist, InUse, NotAllowed
from udl.interfaces.security import Principal as Caller
from udl.interfaces.security import PrincipalKind, current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch import questions as asked
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers
from udl.plugins.apps.orch.models import (
    BLOCKED,
    IN_PROGRESS,
    QUEUED,
    AQuestion,
    NoteKind,
    OwnerKind,
    Principal,
    Questions,
    QuestionState,
)
from udl.plugins.apps.orch.models import (
    Question as Asked,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    LinkRecords,
    Note,
    NoteRecords,
    Project,
    ProjectRecords,
    Question,
    QuestionRecords,
    Task,
    TaskRecords,
)
from udl.plugins.registry import APPS, PluginRegistry

AGENT: str = "deepseek-b"
"""The agent that asks."""
MANAGER: str = "ernie-c"
"""Its manager, when a test gives it one."""
HUMANS: str = "humans"
"""What a question is asked of when no manager may answer it."""


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, and orch."""
    plugins = registry()
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with a project (its two features), two agents and one of them's two tasks."""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
    ) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]")
        )
        features = FeatureRecords(records)
        await features.save_feature(Feature(project="udl", key="F-1", title="Questions"))
        await features.save_feature(Feature(project="udl", key="F-2", title="Managers"))
        agents = AgentRecords(records)
        await agents.save_agent(
            Agent(name=AGENT, title=AGENT, runner="fake", host="local", brief="Build it.")
        )
        await agents.save_agent(
            Agent(
                name=MANAGER,
                title=MANAGER,
                runner="fake",
                host="local",
                role=AgentRole.MANAGER,
                manages=ManagerPowers(answer=True),
            )
        )
        tasks = TaskRecords(records)
        for key, feature in (("F-1.1", "F-1"), ("F-2.1", "F-2")):
            task = Task(project="udl", feature=feature, key=key, title=f"It {key}", assignee=AGENT)
            await tasks.save_task(task)
            await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))
        yield running


def as_person() -> Any:
    """The caller is the owner for a while (a person answers and closes what an agent cannot).

    Reset with `current_principal.reset(token)`."""
    return current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))


def as_agent_named(name: str, *, role: str = "manager") -> Any:
    """The caller is the agent called `name` for a while (a token's principal)."""
    return current_principal.set(Caller(kind=PrincipalKind.AGENT, name=name, role=role))


async def another_manager(service: UDLService) -> Agent:
    """A second manager, so a test can show one touching another's question."""
    found = Agent(
        name="ernie-d",
        title="ernie-d",
        runner="fake",
        host="local",
        role=AgentRole.MANAGER,
        manages=ManagerPowers(answer=True),
    )
    return await AgentRecords(AppRecords(service.db)).save_agent(found)


def as_manager() -> Any:
    """The caller is the manager for a while (it answers and passes up its reports' questions)."""
    return current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name=MANAGER, role=AgentRole.MANAGER)
    )


@pytest.fixture
def as_agent() -> Iterator[None]:
    """The caller is the agent (an agent token's principal, as an interface sets it)."""
    token = current_principal.set(Caller(kind=PrincipalKind.AGENT, name=AGENT, role="implementer"))
    try:
        yield
    finally:
        current_principal.reset(token)


async def agents_of(service: UDLService) -> AgentRecords:
    """The project's agents."""
    return AgentRecords(AppRecords(service.db))


async def changed(service: UDLService, name: str, **changes: Any) -> Agent:
    """The agent `name`, with `changes` stored on it."""
    agents = await agents_of(service)
    found = await agents.agent(name)
    assert found is not None
    return await agents.save_agent(found.model_copy(update=changes))


async def managing(service: UDLService, *, answer: bool = True) -> None:
    """`AGENT` reporting to `MANAGER`, which may (or may not) answer its questions."""
    await changed(service, MANAGER, manages=ManagerPowers(answer=answer))
    await changed(service, AGENT, manager=MANAGER)


async def questions_of(service: UDLService) -> list[Question]:
    """Every question of the project, oldest first."""
    return await QuestionRecords(AppRecords(service.db)).questions("udl", state=None)


async def task_of(service: UDLService, key: str) -> Task:
    """The task called `key`."""
    found = await TaskRecords(AppRecords(service.db)).task("udl", key)
    assert found is not None
    return found


async def notes_of(service: UDLService, owner_kind: OwnerKind, owner: Task | Feature) -> list[str]:
    """What the notes on `owner` say, oldest first."""
    found = await NoteRecords(AppRecords(service.db)).for_owner(owner_kind, owner.id)
    return [note.text for note in found]


async def ask(service: UDLService, **request: Any) -> Asked:
    """`orch_ask`, with the project and the fixture's first task."""
    found = await service.call(
        "orch_ask", {"project": "udl", "task": "F-1.1", "title": "Which repo?", **request}
    )

    assert isinstance(found, Asked)
    return found


async def answered(service: UDLService, key: str, answer: str) -> Asked:
    """`orch_answer`'s answer, given by the owner (a person)."""
    with as_person():
        found = await service.call("orch_answer", {"project": "udl", "key": key, "answer": answer})

    assert isinstance(found, Asked)
    return found


# ---------------------------------------------------------------------------------------------
# Who a question goes to
# ---------------------------------------------------------------------------------------------


async def test_a_question_goes_to_the_agents_manager_when_it_may_answer(
    service: UDLService, as_agent: None
) -> None:
    """(A manager with the `answer` power answers its reports' questions: that is what the power
    is for, and it keeps the humans out of it.)"""
    await managing(service)

    found = await ask(service, text="`udl` has two repos.")

    assert found.to == MANAGER
    assert found.asked_by == AGENT
    assert found.key == "Q-1"


async def test_a_question_goes_to_the_humans_when_the_manager_may_not_answer(
    service: UDLService, as_agent: None
) -> None:
    """(A manager that may not answer is told nothing: the question goes past it, to the humans —
    the card's routing rule.)"""
    await managing(service, answer=False)

    assert (await ask(service)).to == HUMANS


async def test_an_agent_with_no_manager_asks_the_humans(
    service: UDLService, as_agent: None
) -> None:
    """(An implementer that reports to nobody asks the humans: there is no one else.)"""
    assert (await ask(service)).to == HUMANS


# ---------------------------------------------------------------------------------------------
# A blocking question, and what answering it does
# ---------------------------------------------------------------------------------------------


async def test_a_blocking_question_blocks_its_task(service: UDLService, as_agent: None) -> None:
    """(Blocking moves the task to `blocked`, with the question's key on it: the agent's loop
    then takes its next queued task, 27.7.)"""
    found = await ask(service, blocking=True, text="Which repo is it in?")

    assert found.blocking
    task = await task_of(service, "F-1.1")
    assert task.state == BLOCKED
    assert task.blocked_on == found.key


async def test_a_question_that_does_not_block_leaves_the_task_alone(
    service: UDLService, as_agent: None
) -> None:
    """(A question asked mid-work is not a stop: the agent carries on.)"""
    await ask(service)

    assert (await task_of(service, "F-1.1")).state == QUEUED


async def test_answering_writes_the_answer_on_every_task_and_feature_it_names(
    service: UDLService, as_agent: None
) -> None:
    """(The card's done-when: no question is answered without its answer landing on what it names
    — here a second task and a feature, as well as the task that asked.)"""
    records = AppRecords(service.db)
    asked_about = await ask(service, blocking=True, text="Which repo is it in?")
    links = LinkRecords(records)
    await links.add(
        project="udl",
        owner_kind=OwnerKind.QUESTION,
        owner=asked_about.key,
        kind="task",
        url="udl:F-1.1",
        title="F-1.1",
    )
    await links.add(
        project="udl",
        owner_kind=OwnerKind.QUESTION,
        owner=asked_about.key,
        kind="task",
        url="udl:F-2.1",
        title="F-2.1",
    )
    await links.add(
        project="udl",
        owner_kind=OwnerKind.QUESTION,
        owner=asked_about.key,
        kind="feature",
        url="udl:F-2",
        title="F-2",
    )

    found = await answered(service, asked_about.key, "The `api` one.")

    assert found.state is QuestionState.ANSWERED
    assert found.answer == "The `api` one."
    assert found.answered_by == "human:owner"
    said = f"{asked_about.key}, answered by human:owner: The `api` one."
    for key in ("F-1.1", "F-2.1"):
        assert said in await notes_of(service, OwnerKind.TASK, await task_of(service, key))
    feature = await FeatureRecords(records).feature("udl", "F-2")
    assert feature is not None
    assert said in await notes_of(service, OwnerKind.FEATURE, feature)
    decisions = await NoteRecords(records).for_owner(
        OwnerKind.TASK, (await task_of(service, "F-1.1")).id
    )
    assert [note.note_kind for note in decisions if note.text == said] == [NoteKind.DECISION]


async def test_answering_puts_a_blocked_task_back_at_the_front_of_its_queue(
    service: UDLService, as_agent: None
) -> None:
    """(The agent picks it up again first: it was stopped in the middle of it.)"""
    asked_about = await ask(service, blocking=True)
    assert (await task_of(service, "F-1.1")).state == BLOCKED

    await answered(service, asked_about.key, "Do it.")

    task = await task_of(service, "F-1.1")
    assert task.state == QUEUED
    assert task.position == 0


async def test_the_answer_and_its_notes_are_one_write(service: UDLService) -> None:
    """(A store that cannot write the notes writes no answer either: half of it is a question
    marked answered with nothing to show for it.)"""
    records = AppRecords(service.db)
    asked_about = await QuestionRecords(records).save_question(
        Question(
            project="udl",
            key="Q-1",
            asked_by=AGENT,
            to=HUMANS,
            title="Which repo?",
            blocking=True,
        )
    )
    await LinkRecords(records).add(
        project="udl",
        owner_kind=OwnerKind.QUESTION,
        owner=asked_about.key,
        kind="task",
        url="udl:F-1.1",
        title="F-1.1",
    )

    class _CannotWriteNotes(AppRecords):
        """A store that cannot write a note (the answer's decision)."""

        async def save[R: Record](self, record: R) -> R:
            if isinstance(record, Note):
                raise DatabaseError("the note could not be written")
            return await super().save(record)

        async def save_all[R: Record](self, records: Sequence[R]) -> list[R]:
            if any(isinstance(one, Note) for one in records):
                raise DatabaseError("the note could not be written")
            return await super().save_all(records)

    with pytest.raises(DatabaseError, match="the note could not be written"):
        await asked.answer(
            QuestionRecords(_CannotWriteNotes(service.db)),
            project="udl",
            key=asked_about.key,
            answer="The `api` one.",
            by=Principal(kind="human", name="claude"),
        )

    still = await QuestionRecords(records).question("udl", asked_about.key)
    assert still is not None
    assert still.state is QuestionState.OPEN
    assert (await task_of(service, "F-1.1")).state == QUEUED


async def test_a_question_that_is_not_open_cannot_be_answered_again(
    service: UDLService, as_agent: None
) -> None:
    """(An answered question is history: another answer would be a second decision on one
    question.)"""
    asked_about = await ask(service)
    await answered(service, asked_about.key, "Yes.")

    with pytest.raises(NotAllowed, match="already answered"):
        await answered(service, asked_about.key, "No.")


async def test_a_question_that_is_not_there_is_refused(service: UDLService) -> None:
    """(A key nobody has is a refusal that names it.)"""
    with pytest.raises(DoesNotExist, match=r"no question called 'Q-9'"):
        await answered(service, "Q-9", "Yes.")


async def test_a_task_in_progress_can_be_asked_about_and_blocked(
    service: UDLService, as_agent: None
) -> None:
    """(An agent asks in the middle of its work: the task is `in_progress`, and blocking it is
    the move the table allows for that pair.)"""
    tasks = TaskRecords(AppRecords(service.db))
    task = await task_of(service, "F-1.1")
    await tasks.move(task, IN_PROGRESS, by=Principal(kind="agent", name=AGENT, role="start"))

    found = await ask(service, blocking=True)

    assert found.blocking
    assert (await task_of(service, "F-1.1")).state == BLOCKED


# ---------------------------------------------------------------------------------------------
# The thread, escalating and closing
# ---------------------------------------------------------------------------------------------


async def test_a_reply_joins_the_thread(service: UDLService, as_agent: None) -> None:
    """(A follow-up from either side: the agent asks again, then the manager answers, then the
    agent asks what the answer means for something else.)"""
    asked_about = await ask(service, blocking=True, text="Which repo?")

    await service.call(
        "orch_reply", {"project": "udl", "key": asked_about.key, "text": "`api` or `web`?"}
    )
    await answered(service, asked_about.key, "`api`.")
    await service.call(
        "orch_reply", {"project": "udl", "key": asked_about.key, "text": "The tests too?"}
    )

    records = QuestionRecords(AppRecords(service.db))
    found = await records.question("udl", asked_about.key)
    assert found is not None
    thread = await records.thread(found)
    assert [one.text for one in thread] == [
        "Which repo?",
        "`api` or `web`?",
        "`api`.",
        "The tests too?",
    ]


async def test_escalating_passes_it_up(service: UDLService, as_agent: None) -> None:
    """(A manager that cannot answer — or will not — passes it up: to its own manager, or to the
    humans.)"""
    await managing(service)
    asked_about = await ask(service)
    assert asked_about.to == MANAGER

    token = as_manager()
    try:
        found = await service.call("orch_escalate", {"project": "udl", "key": asked_about.key})
    finally:
        current_principal.reset(token)

    assert isinstance(found, Asked)
    assert found.to == HUMANS
    assert found.escalated


async def test_escalating_from_a_manager_with_one_of_its_own_goes_to_it(
    service: UDLService, as_agent: None
) -> None:
    """(A manager's manager that may answer is the next step up: the humans are the last.)"""
    await managing(service)
    await AgentRecords(AppRecords(service.db)).save_agent(
        Agent(
            name="someone",
            title="someone",
            runner="fake",
            host="local",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(answer=True),
        )
    )
    await changed(service, MANAGER, manager="someone", manages=ManagerPowers(answer=True))
    asked_about = await ask(service)

    token = as_manager()
    try:
        found = await service.call("orch_escalate", {"project": "udl", "key": asked_about.key})
    finally:
        current_principal.reset(token)

    assert isinstance(found, Asked)
    assert found.to == "someone"


async def test_closing_records_the_outcome(service: UDLService, as_agent: None) -> None:
    """(A question nobody answers is closed with what was done about it instead.)"""
    asked_about = await ask(service, blocking=True)

    token = as_person()
    try:
        found = await service.call(
            "orch_close_question",
            {"project": "udl", "key": asked_about.key, "outcome": "Asked in the queue instead."},
        )
    finally:
        current_principal.reset(token)

    assert isinstance(found, Asked)
    assert found.state is QuestionState.CLOSED
    assert found.outcome == "Asked in the queue instead."


async def test_closing_a_blocked_question_leaves_its_task_blocked(
    service: UDLService, as_agent: None
) -> None:
    """(Closing is not answering: the task still waits for a person.)"""
    asked_about = await ask(service, blocking=True)

    token = as_person()
    try:
        await service.call(
            "orch_close_question",
            {"project": "udl", "key": asked_about.key, "outcome": "Never mind."},
        )
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.1")).state == BLOCKED


# ---------------------------------------------------------------------------------------------
# Reading them
# ---------------------------------------------------------------------------------------------


async def test_listing_questions_filters_by_state_and_who_they_are_for(
    service: UDLService, as_agent: None
) -> None:
    """(The humans' list is the open ones asked of them; everything is everything.)"""
    first = await ask(service, title="One?")
    second = await ask(service, title="Two?", blocking=True)
    await answered(service, first.key, "Yes.")

    open_ones = await service.call(
        "orch_list_questions", {"project": "udl", "state": "open", "to": HUMANS}
    )
    assert isinstance(open_ones, Questions)
    assert [one.key for one in open_ones.questions] == [second.key]

    every = await service.call("orch_list_questions", {"project": "udl", "state": None})
    assert isinstance(every, Questions)
    assert [one.key for one in every.questions] == [first.key, second.key]


async def test_a_question_can_be_read_with_its_thread(service: UDLService, as_agent: None) -> None:
    """(`orch_get_question` is the whole thing: the question, its thread, and what it names.)"""
    asked_about = await ask(service, text="Which repo?")
    await service.call(
        "orch_reply", {"project": "udl", "key": asked_about.key, "text": "Or neither?"}
    )

    found = await service.call("orch_get_question", {"project": "udl", "key": asked_about.key})

    assert isinstance(found, AQuestion)
    assert found.question.key == asked_about.key
    assert [one.text for one in found.thread] == ["Which repo?", "Or neither?"]


async def test_a_question_is_asked_about_a_task_that_is_there(
    service: UDLService, as_agent: None
) -> None:
    """(A question about no task of the project is a refusal: its links and its block would have
    nowhere to go.)"""
    with pytest.raises(DoesNotExist, match=r"no task called 'F-9.9'"):
        await service.call("orch_ask", {"project": "udl", "task": "F-9.9", "title": "Which?"})


async def test_a_question_is_a_record_of_its_project(service: UDLService) -> None:
    """(The kinds orch keeps, so the app contract and db sync see them.)"""
    kinds = {kind.kind for kind in Orch.record_kinds()}
    assert {"orch_question", "orch_message"} <= kinds


# ---------------------------------------------------------------------------------------------
# The review's first round: what a second question may not do, and who may touch one
# ---------------------------------------------------------------------------------------------


async def test_a_second_blocking_question_is_refused_while_the_task_waits(
    service: UDLService, as_agent: None
) -> None:
    """(A task waits on one question: a second one would overwrite what it waits on, and the
    first would be answered into nothing.)"""
    first = await ask(service, blocking=True)
    assert (await task_of(service, "F-1.1")).blocked_on == first.key

    with pytest.raises(InUse, match="already waits on"):
        await ask(service, title="And which branch?", blocking=True)

    assert (await task_of(service, "F-1.1")).blocked_on == first.key
    assert [one.key for one in await questions_of(service)] == [first.key]


async def test_a_question_has_nowhere_higher_to_go_than_the_humans(
    service: UDLService, as_agent: None
) -> None:
    """(Escalating what the humans already hold has nowhere higher: it is refused rather than
    routed back down.)"""
    asked_about = await ask(service)
    assert asked_about.to == HUMANS

    token = as_person()
    try:
        with pytest.raises(NotAllowed, match="already the humans"):
            await service.call("orch_escalate", {"project": "udl", "key": asked_about.key})
    finally:
        current_principal.reset(token)


async def test_an_agent_may_not_answer_another_s_question(
    service: UDLService,
) -> None:
    """(An answer is the holder's: a manager answering what was asked of another is refused.)"""
    await ask(service)  # (asked of the humans, by AGENT)
    other = await another_manager(service)
    token = as_agent_named(other.name)
    try:
        with pytest.raises(NotAllowed, match="not yours to answer"):
            await service.call(
                "orch_answer", {"project": "udl", "key": "Q-1", "answer": "The `api` one."}
            )
    finally:
        current_principal.reset(token)


async def test_an_agent_may_not_close_or_pass_up_another_s_question(
    service: UDLService, as_agent: None
) -> None:
    """(Closing or escalating another's question could leave their task blocked for good.)"""
    asked_about = await ask(service, blocking=True)
    other = await another_manager(service)

    token = as_agent_named(other.name)
    try:
        with pytest.raises(NotAllowed, match="not yours"):
            await service.call(
                "orch_close_question",
                {"project": "udl", "key": asked_about.key, "outcome": "Never mind."},
            )
        with pytest.raises(NotAllowed, match="not yours"):
            await service.call("orch_escalate", {"project": "udl", "key": asked_about.key})
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.1")).state == BLOCKED


async def test_a_thread_is_only_the_two_sides(service: UDLService, as_agent: None) -> None:
    """(An agent replies on a question it asked or one asked of it, and not on a stranger's.)"""
    asked_about = await ask(service)
    stranger = Caller(kind=PrincipalKind.AGENT, name="ernie-d", role="implementer")
    token = current_principal.set(stranger)
    try:
        with pytest.raises(NotAllowed, match="not yours"):
            await service.call(
                "orch_reply", {"project": "udl", "key": asked_about.key, "text": "A thought."}
            )
    finally:
        current_principal.reset(token)


async def test_the_asker_replies_in_its_own_thread(service: UDLService, as_agent: None) -> None:
    """(The asker is one side of the thread: it may add to it after asking.)"""
    asked_about = await ask(service)
    await changed(service, AGENT, manager=None)  # (so the question is the humans', not ours)

    token = current_principal.set(Caller(kind=PrincipalKind.AGENT, name=AGENT, role="implementer"))
    try:
        said = await service.call(
            "orch_reply", {"project": "udl", "key": asked_about.key, "text": "It is `api`."}
        )
    finally:
        current_principal.reset(token)

    assert said.text == "It is `api`."


async def test_an_agent_may_not_ask_about_another_s_task(service: UDLService) -> None:
    """(An agent asks in the tasks it works: asking in another's is refused.)"""
    await changed(service, AGENT, manager=None)
    token = current_principal.set(
        Caller(kind=PrincipalKind.AGENT, name=MANAGER, role=AgentRole.MANAGER)
    )
    try:
        with pytest.raises(NotAllowed, match="not yours to ask about"):
            await ask(service, title="Which repo?")
    finally:
        current_principal.reset(token)
