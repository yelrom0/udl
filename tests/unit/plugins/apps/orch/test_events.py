"""
----------------
- test_events.py -
----------------

28.5: the events orch publishes, and the moment each is published at.

A type is declared (so `udl events types` lists it with its schema and example), and the thing
that happens publishes it once — a task's move (`orch.task.changed`, and `blocked`/`merged` when
that is what it moved to), a question (`orch.question.asked`, `orch.question.answered`) and a
person's action (`orch.action.needed`). The session, ready and link events are asserted where
they are published (27.6, 27.7, 26.4), and `orch.agent.idle` in the dispatcher's own test.
"""

# System Imports
from typing import Any, Final

# Package Imports
import pytest

# Local Imports
from udl.automations.events import EventRecord, EventType
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.plugins.apps.base import AppEvents, AppRecords
from udl.plugins.apps.orch import actions, questions
from udl.plugins.apps.orch.models import BLOCKED, MERGED, QUEUED, READY, ActionWhen, Principal
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import QuestionRecords, TaskRecords

UDL: Final = Principal(kind="system", name="")
"""udl itself, which may move any task (the dispatcher assigns, the watcher merges)."""

THE_CARD_S_TYPES: Final = frozenset(
    {
        "orch.task.changed",
        "orch.task.ready",
        "orch.task.blocked",
        "orch.task.merged",
        "orch.question.asked",
        "orch.question.answered",
        "orch.action.needed",
        "orch.agent.limited",
        "orch.agent.idle",
        "orch.session.started",
        "orch.session.ended",
        "orch.link.changed",
    }
)


def records_of(service: UDLService) -> AppRecords:
    """orch's records, publishing into `service`'s bus: what the app is given when it runs."""
    return AppRecords(service.db, events=AppEvents(service.bus, "orch", Orch.event_types()))


async def events_of(service: UDLService, type_: str) -> list[EventRecord]:
    """The events of `type_` stored so far, oldest first."""
    found = await service.db.list_records(EventRecord, RecordQuery(where={"type": type_}))
    return sorted(found, key=lambda one: one.occurred_at)


# ---------------------------------------------------------------------------------------------
# The types themselves
# ---------------------------------------------------------------------------------------------


def test_the_card_s_events_are_all_declared() -> None:
    names = {one.name for one in Orch.event_types()}

    assert names >= THE_CARD_S_TYPES


@pytest.mark.parametrize("declared", list(Orch.event_types()), ids=lambda one: one.name)
def test_every_type_has_a_schema_and_an_example(declared: EventType[Any]) -> None:
    """Each is a type `udl events types` can list: a schema, an example that fits it, and a
    subject line."""
    example = declared.example.model_dump(mode="json", by_alias=True)
    assert declared.validate(example) == example  # (its own example is its own data)
    assert declared.data.model_json_schema()["properties"]
    assert declared.subject_of(declared.example).strip()
    assert declared.source == "orch"


async def test_the_listing_shows_them_with_their_schemas(service: UDLService) -> None:
    """(The card's Done-when: `udl events types` lists them with schemas.)"""
    from udl.automations.events import EventTypeList

    listed = await service.call("list_event_types", {})
    assert isinstance(listed, EventTypeList)
    by_name = {one.name: one for one in listed.types}
    assert set(by_name) >= THE_CARD_S_TYPES

    question = by_name["orch.question.asked"]
    assert question.source == "orch"
    assert "blocking" in {field.path for field in question.fields}
    properties = question.json_schema["properties"]
    assert isinstance(properties, dict)
    assert "blocking" in properties
    assert question.example["blocking"] is True


def test_a_task_s_change_names_what_it_was_and_what_it_is() -> None:
    """(`from` and `to`, as the card says: the words a filter names.)"""
    found = next(one for one in Orch.event_types() if one.name == "orch.task.changed")

    assert {"from", "to"} <= set(found.data.model_json_schema()["properties"])


# ---------------------------------------------------------------------------------------------
# A task's move
# ---------------------------------------------------------------------------------------------


async def test_a_move_publishes_the_task_s_change_once(service: UDLService) -> None:
    records = records_of(service)
    tasks = TaskRecords(records)
    task = await tasks.task("udl", "26.1")
    assert task is not None

    moved = await tasks.move(task, QUEUED, by=UDL)
    await tasks.save_all([moved])  # (a save that changes no state is no second change)

    published = await events_of(service, "orch.task.changed")
    assert len(published) == 1
    assert published[0].data == {
        "project": "udl",
        "task": "26.1",
        "from": "open",
        "to": "queued",
        "by": "udl",
    }
    assert "26.1" in published[0].subject


async def test_each_move_is_its_own_event(service: UDLService) -> None:
    records = records_of(service)
    tasks = TaskRecords(records)
    task = await tasks.task("udl", "26.1")
    assert task is not None

    moved = await tasks.move(task, QUEUED, by=UDL)
    await tasks.move(moved, BLOCKED, by=UDL, note="waiting on a person")

    assert [one.data["to"] for one in await events_of(service, "orch.task.changed")] == [
        "queued",
        "blocked",
    ]


async def test_a_task_that_is_blocked_says_what_it_waits_on(service: UDLService) -> None:
    records = records_of(service)
    tasks = TaskRecords(records)
    task = await tasks.task("udl", "26.2")
    assert task is not None

    await tasks.move(task, BLOCKED, by=UDL, note="Q-1")

    published = await events_of(service, "orch.task.blocked")
    assert len(published) == 1
    assert published[0].data == {"project": "udl", "task": "26.2", "on": "Q-1"}


async def test_a_task_that_merges_says_so(service: UDLService) -> None:
    """(The move is published as any move is. `orch.task.merged` itself is 28.7's, published
    where the merge happens, with the pull and the sha — a move to `merged` made any other way
    is still a move, and says so.)"""
    records = records_of(service)
    tasks = TaskRecords(records)
    task = await tasks.task("udl", "26.1")
    assert task is not None

    queued = await tasks.move(task, QUEUED, by=UDL)
    working = await tasks.move(queued, "in_progress", by=UDL)
    ready = await tasks.move(working, READY, by=UDL)
    await tasks.move(ready, MERGED, by=Principal(kind="agent", name="reviewer", role="merge"))

    moved = await events_of(service, "orch.task.changed")
    assert moved[-1].data == {
        "project": "udl",
        "task": "26.1",
        "from": "ready",
        "to": "merged",
        "by": "reviewer",
    }


# ---------------------------------------------------------------------------------------------
# Questions and actions
# ---------------------------------------------------------------------------------------------


async def test_a_question_s_asking_and_its_answer_are_published(service: UDLService) -> None:
    records = records_of(service)

    asked = await questions.ask(
        QuestionRecords(records),
        project="udl",
        task="26.1",
        title="Which file?",
        text="The spec names two.",
        blocking=True,
        asked_by="deepseek-a",
    )
    await questions.answer(
        QuestionRecords(records),
        project="udl",
        key=asked.key,
        answer="The first one.",
        by=Principal(kind="human", name="human:claude"),
    )

    asking = await events_of(service, "orch.question.asked")
    assert len(asking) == 1
    assert asking[0].data["question"] == asked.key
    assert asking[0].data["blocking"] is True
    assert asking[0].data["task"] == "26.1"

    answered = await events_of(service, "orch.question.answered")
    assert len(answered) == 1
    assert answered[0].data["question"] == asked.key
    assert answered[0].data["by"] == "human:claude"


async def test_a_person_s_action_says_it_is_needed(service: UDLService) -> None:
    records = records_of(service)

    made = await actions.set_action(
        actions.ActionRecords(records),
        project="udl",
        title="Send the key",
        text="The one from 1Password.",
        when=ActionWhen.NOW,
        assignee="human:claude",
    )

    published = await events_of(service, "orch.action.needed")
    assert len(published) == 1
    assert published[0].data["action"] == made.key
    assert published[0].data["when"] == "now"
    assert published[0].data["assignee"] == "human:claude"
