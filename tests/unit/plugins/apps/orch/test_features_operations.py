"""
-------------------------------------
- test_features_operations.py -
-------------------------------------

The catalogue's features (26.6): `orch_list_features`, `orch_get_feature` (with its tasks, its
links and its notes) and `orch_set_feature`, on the CLI, the API and MCP through udl's generated
adapters.

A feature is set by its key: one that is there is edited, and what is not given is kept.
"""

# System Imports
from typing import Any

# Package Imports
import pytest

# Local Imports
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    Feature,
    FeatureInfo,
    Features,
    GetFeatureRequest,
    ListFeaturesRequest,
    NoteInfo,
    NoteKind,
    OwnerKind,
    SetFeatureRequest,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import FeatureRecords, Note, NoteRecords

FEATURE_OPERATIONS: list[str] = ["list_features", "get_feature", "set_feature"]


def test_orch_offers_the_features_operations() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert [name for name in offered if name in FEATURE_OPERATIONS] == FEATURE_OPERATIONS
    assert offered["list_features"].hints == OperationHints(read_only=True)
    assert offered["get_feature"].hints == OperationHints(read_only=True)
    assert offered["set_feature"].hints == OperationHints(idempotent=True)
    assert offered["list_features"].request is ListFeaturesRequest
    assert offered["list_features"].response is Features
    assert offered["get_feature"].request is GetFeatureRequest
    assert offered["get_feature"].response is FeatureInfo
    assert offered["set_feature"].request is SetFeatureRequest
    assert offered["set_feature"].response is Feature


# ---------------------------------------------------------------------------------------------
# Reading them
# ---------------------------------------------------------------------------------------------


async def test_the_features_are_listed_in_their_order(service: UDLService) -> None:
    await service.call("orch_set_feature", {"key": "9", "title": "First", "order": 9})

    found = await service.call("orch_list_features", {})

    assert isinstance(found, Features)
    assert found.project == "udl"
    assert [(feature.key, feature.order) for feature in found.features] == [
        ("9", 9),
        ("26", 26),
    ]


async def test_a_feature_is_read_with_its_tasks_links_and_notes(service: UDLService) -> None:
    records = AppRecords(service.db)
    stored = await FeatureRecords(records).feature("udl", "26")
    assert stored is not None
    await NoteRecords(records).add(
        Note(
            owner_kind=OwnerKind.FEATURE,
            owner=stored.id,
            note_kind=NoteKind.DECISION,
            text="Its tasks are numbered from its key.",
            author="deepseek-a",
        )
    )

    found = await service.call("orch_get_feature", {"key": "26"})

    assert isinstance(found, FeatureInfo)
    assert found.feature.title == "Work tracking"
    assert [task.key for task in found.tasks] == ["26.1", "26.2"]
    assert found.links == []
    assert [(note.note_kind, note.author) for note in found.notes] == [("decision", "deepseek-a")]
    assert isinstance(found.notes[0], NoteInfo)


async def test_a_feature_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"22"):
        await service.call("orch_get_feature", {"key": "22"})


# ---------------------------------------------------------------------------------------------
# Setting them
# ---------------------------------------------------------------------------------------------


async def a_feature(service: UDLService, **request: Any) -> Feature:
    """The answer of `orch_set_feature`, as any interface's caller gets it."""
    found = await service.call("orch_set_feature", request)

    assert isinstance(found, Feature)
    return found


async def test_a_feature_is_created(service: UDLService) -> None:
    made = await a_feature(
        service, key="22", title="Local AI", spec="Phase 22.", status="planned", order=22
    )

    assert (made.key, made.title, made.order) == ("22", "Local AI", 22)
    read = await service.call("orch_get_feature", {"key": "22"})
    assert isinstance(read, FeatureInfo)
    assert read.feature.spec == "Phase 22."


async def test_setting_a_feature_again_edits_what_is_given(service: UDLService) -> None:
    edited = await a_feature(service, key="26", spec="Phase 26, as its section reads.")

    assert edited.spec == "Phase 26, as its section reads."
    assert edited.title == "Work tracking"
    assert edited.order == 26


async def test_a_new_feature_needs_a_title(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="title"):
        await a_feature(service, key="22")


async def test_a_feature_of_a_project_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"liively"):
        await a_feature(service, project="liively", key="1", title="One")


async def test_a_feature_s_spec_is_scanned_into_links(service: UDLService) -> None:
    # (26.4's scan still runs: a GitHub URL in a spec is a link of the feature's.)
    await a_feature(
        service,
        key="22",
        title="Local AI",
        spec="Its card is https://github.com/yelrom0/udl/pull/22, and `26.2` is a task of it.",
    )

    read = await service.call("orch_get_feature", {"key": "22"})

    assert isinstance(read, FeatureInfo)
    assert [(link.link_kind, link.url) for link in read.links] == [
        ("pull", "https://github.com/yelrom0/udl/pull/22"),
        ("task", "udl:task/udl/26.2"),
    ]
