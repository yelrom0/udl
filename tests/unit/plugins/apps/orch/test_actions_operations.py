"""
---------------------------------------
- test_actions_operations.py -
---------------------------------------

The human actions' operations (28.2): `orch_set_action`, `orch_list_actions`,
`orch_finish_action` and `orch_drop_action`, each one method of the `orch` app, offered on the
CLI, the API and MCP by udl's generated adapters (tests/interface/test_parity.py lists them).

They are run here through `service.call()`, as every interface calls them, so what is tested is
the operation a caller gets: its request, its answer, and what it does to the records.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.engine import registry, settings
from udl.core.service import UDLService
from udl.errors import DoesNotExist
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    Action,
    Actions,
    ActionState,
    ListActionsRequest,
    SetActionRequest,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Feature,
    FeatureRecords,
    Project,
    ProjectRecords,
    Task,
    TaskRecords,
)
from udl.plugins.registry import APPS, PluginRegistry


def orch_registry() -> PluginRegistry:
    """The memory database and the secret store, and the orch app (which the tests are of)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    return plugins


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service over a fresh in-memory database, with a project, a feature and two tasks."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="Q"))
        for key in ("F-1.1", "F-1.2"):
            await TaskRecords(records).save_task(
                Task(project="udl", feature="F-1", key=key, title=key)
            )
        yield running


async def an_action(service: UDLService, **request: Any) -> Action:
    """The answer of `orch_set_action`, as any interface's caller gets it."""
    found = await service.call("orch_set_action", {"project": "udl", **request})

    assert isinstance(found, Action)
    return found


async def actions_of(service: UDLService, **request: Any) -> Actions:
    """The answer of `orch_list_actions`."""
    found = await service.call("orch_list_actions", {"project": "udl", **request})

    assert isinstance(found, Actions)
    return found


async def test_setting_an_action_answers_it(service: UDLService) -> None:
    found = await an_action(service, title="Send the token", assignee="human:claude")

    assert (found.key, found.state, found.when) == ("A-1", "open", "now")
    assert (found.title, found.assignee) == ("Send the token", "human:claude")


async def test_the_actions_are_listed_in_the_order_they_are_due(service: UDLService) -> None:
    await an_action(service, title="Later", when="later")
    await an_action(service, title="After", when="after", after="F-1.1")
    await an_action(service, title="Now")

    listed = await actions_of(service)

    assert [found.title for found in listed.actions] == ["Now", "After", "Later"]
    assert listed.project == "udl"


async def test_the_listing_can_be_narrowed(service: UDLService) -> None:
    first = await an_action(service, title="One", assignee="human:claude")
    await an_action(service, title="Two", assignee="human:sam")
    await service.call(
        "orch_finish_action", {"project": "udl", "key": first.key, "result": "Done."}
    )

    mine = await actions_of(service, assignee="human:claude")
    done = await actions_of(service, state="done")

    assert [found.title for found in mine.actions] == []
    assert [found.title for found in done.actions] == ["One"]


async def test_finishing_an_action_answers_it_with_its_result(service: UDLService) -> None:
    found = await an_action(service, title="Send the token", task="F-1.1")

    finished = await service.call(
        "orch_finish_action", {"project": "udl", "key": found.key, "result": "Sent it."}
    )

    assert isinstance(finished, Action)
    assert (finished.state, finished.result) == ("done", "Sent it.")
    notes = await service.call("orch_list_actions", {"project": "udl", "state": "done"})
    assert isinstance(notes, Actions)


async def test_dropping_an_action_answers_it_dropped(service: UDLService) -> None:
    found = await an_action(service, title="Send the token")

    dropped = await service.call(
        "orch_drop_action", {"project": "udl", "key": found.key, "reason": "Not needed."}
    )

    assert isinstance(dropped, Action)
    assert (dropped.state, dropped.result) == ("dropped", "Not needed.")


async def test_an_action_that_is_not_there_is_told_so_through_the_operation(
    service: UDLService,
) -> None:
    with pytest.raises(DoesNotExist, match="A-9"):
        await service.call("orch_finish_action", {"project": "udl", "key": "A-9", "result": "x"})


def test_the_requests_are_the_operations_own_models() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert offered["set_action"].request is SetActionRequest
    assert offered["list_actions"].request is ListActionsRequest
    assert offered["finish_action"].request.__name__ == "FinishActionRequest"
    assert offered["drop_action"].request.__name__ == "DropActionRequest"
    assert offered["set_action"].response is Action
    assert offered["list_actions"].response is Actions
    assert offered["list_actions"].hints.read_only is True
    assert ActionState.OPEN == "open"
