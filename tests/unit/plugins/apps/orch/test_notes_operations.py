"""
-------------------------------------
- test_notes_operations.py -
-------------------------------------

The catalogue's notes (26.6): `orch_add_note`, on the CLI, the API and MCP through udl's generated
adapters. A note is appended to a task or a feature and is never replaced: a correction is another
note (26.5).
"""

# System Imports
from typing import Any

# Package Imports
import pytest

# Local Imports
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed
from udl.interfaces.security import Principal, PrincipalKind, current_principal
from udl.plugins.apps.orch.models import (
    AddNoteRequest,
    FeatureInfo,
    NoteInfo,
    TaskInfo,
)
from udl.plugins.apps.orch.plugin import Orch


def test_orch_offers_the_notes_operation() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert "add_note" in offered
    assert offered["add_note"].hints == OperationHints()
    assert offered["add_note"].request is AddNoteRequest
    assert offered["add_note"].response is NoteInfo
    # (Its command is the one the card gives: `udl orch note <key> <text>`.)
    assert offered["add_note"].cli is not None
    assert offered["add_note"].cli.path == ("orch", "note")
    assert offered["add_note"].cli.arguments == ("owner", "text")


async def a_note(service: UDLService, **request: Any) -> NoteInfo:
    """The answer of `orch_add_note`, as any interface's caller gets it."""
    found = await service.call("orch_add_note", request)

    assert isinstance(found, NoteInfo)
    return found


async def test_a_note_is_written_on_a_task(service: UDLService) -> None:
    made = await a_note(
        service,
        owner_kind="task",
        owner="26.2",
        text="Its links are scanned as it is saved.",
        note_kind="finding",
    )

    assert (made.note_kind, made.author) == ("finding", "human:owner")
    read = await service.call("orch_get_task", {"key": "26.2"})
    assert isinstance(read, TaskInfo)
    assert [note.text for note in read.notes] == ["Its links are scanned as it is saved."]


async def test_a_note_is_written_on_a_feature(service: UDLService) -> None:
    await a_note(service, owner_kind="feature", owner="26", text="Phase 26 is the critical path.")

    read = await service.call("orch_get_feature", {"key": "26"})
    assert isinstance(read, FeatureInfo)
    assert [note.text for note in read.notes] == ["Phase 26 is the critical path."]


async def test_a_note_is_appended_never_replaced(service: UDLService) -> None:
    await a_note(service, owner_kind="task", owner="26.1", text="First.")
    await a_note(service, owner_kind="task", owner="26.1", text="A correction.")

    read = await service.call("orch_get_task", {"key": "26.1"})
    assert isinstance(read, TaskInfo)
    assert [note.text for note in read.notes] == ["First.", "A correction."]


async def test_a_note_on_what_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"26\.9"):
        await a_note(service, owner_kind="task", owner="26.9", text="Nothing to say.")


def test_a_note_of_no_owner_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="owner_kind"):
        AddNoteRequest.model_validate({"owner": "26.1", "text": "Nothing to say."})


async def test_a_note_is_the_owner_s_when_the_cli_writes_it(service: UDLService) -> None:
    # (No interface set a caller: the person at the keyboard, who is udl's owner.)
    made = await a_note(service, owner_kind="task", owner="26.1", text="Noted.")

    assert made.author == "human:owner"
    assert made.note_kind == "progress"


async def test_an_agent_s_note_is_the_agent_s(service: UDLService) -> None:
    # (An implementer may add a note (27.2), and the note says who wrote it, not who it says.)
    caller = current_principal.set(
        Principal(kind=PrincipalKind.AGENT, name="deepseek-a", role="implementer")
    )
    try:
        made = await a_note(service, owner_kind="task", owner="26.1", text="Noted.")
    finally:
        current_principal.reset(caller)

    assert made.author == "deepseek-a"


async def test_a_caller_cannot_write_a_system_note(service: UDLService) -> None:
    # (A system note is udl's own: the moves and the splits write them.)
    with pytest.raises(NotAllowed, match="system"):
        await a_note(service, owner_kind="task", owner="26.1", text="Moved.", note_kind="system")


def test_a_note_s_author_is_not_the_caller_s_to_say() -> None:
    with pytest.raises(ValueError, match="author"):
        AddNoteRequest.model_validate(
            {"owner_kind": "task", "owner": "26.1", "text": "By me.", "author": "human:claude"}
        )
