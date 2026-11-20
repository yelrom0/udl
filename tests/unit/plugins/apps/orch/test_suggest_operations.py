"""
--------------------------------------
- test_suggest_operations.py -
--------------------------------------

`orch_suggest` (27.4), through the service as the CLI, the API and MCP call it: the tasks to do
next, best first, each with the agent it suits and why.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.engine import registry, settings
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import Principal, Suggestions, SuggestRequest
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
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


# ---------------------------------------------------------------------------------------------
# What the app offers
# ---------------------------------------------------------------------------------------------


def test_orch_offers_suggest_as_a_read_only_operation() -> None:
    (found,) = [op for op in Orch.operations() if op.name == "suggest"]

    assert found.hints == OperationHints(read_only=True)
    assert found.request is SuggestRequest
    assert found.response is Suggestions


async def test_it_is_offered_as_orch_suggest(tmp_path: Path) -> None:
    async with UDLService(settings(tmp_path), registry=orch_registry()) as service:
        names = [op.name for op in service.operations.in_namespace("orch")]

    assert "orch_suggest" in names


# ---------------------------------------------------------------------------------------------
# The operation, through the service
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service over a fresh in-memory database, with a plan: F-1.1 (which F-1.2 waits on) and
    F-2.1, and one agent."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
        features = FeatureRecords(records)
        await features.save_feature(Feature(project="udl", key="F-1", title="The queue", order=1))
        await features.save_feature(Feature(project="udl", key="F-2", title="The board", order=2))
        tasks = TaskRecords(records)
        await tasks.save_task(
            Task(project="udl", feature="F-1", key="F-1.1", title="Assign a task")
        )
        await tasks.save_task(
            Task(project="udl", feature="F-1", key="F-1.2", title="Reorder", needs=["F-1.1"])
        )
        await tasks.save_task(Task(project="udl", feature="F-2", key="F-2.1", title="The board"))
        await AgentRecords(records).save_agent(Agent(name="fake-a", title="Fake A", runner="fake"))
        yield running


async def suggested(service: UDLService, **request: Any) -> Suggestions:
    """`orch_suggest`, as a caller of any interface calls it."""
    found = await service.call("orch_suggest", request)

    assert isinstance(found, Suggestions)
    return found


async def test_the_first_task_of_the_plan_is_suggested_first(service: UDLService) -> None:
    answer = await service.call("orch_suggest", {"project": "udl", "agent": "fake-a"})

    assert isinstance(answer, Suggestions)
    # (F-1.1 first: it unblocks F-1.2; F-2.1 waits on nothing.)
    assert [(one.task, one.agent) for one in answer.suggestions] == [
        ("F-1.1", "fake-a"),
        ("F-2.1", "fake-a"),
    ]
    assert answer.suggestions[0].reasons == ["it unblocks 1 task"]


async def test_the_plan_is_read_best_first(service: UDLService) -> None:
    answer = await suggested(service, project="udl")

    assert [one.task for one in answer.suggestions] == ["F-1.1", "F-2.1"]


async def test_an_agent_that_is_not_one_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match="erine-c"):
        await service.call("orch_suggest", {"project": "udl", "agent": "erine-c"})


async def test_an_agent_on_another_projects_task_is_not_idle(tmp_path: Path) -> None:
    # (A task of this project cannot know about another's: the operation reads them all.)
    async with UDLService(settings(tmp_path), registry=orch_registry()) as service:
        records = AppRecords(service.db)
        projects = ProjectRecords(records)
        for name in ("udl", "thevault"):
            await projects.save_project(Project(name=name, title=name))
        await FeatureRecords(records).save_feature(
            Feature(project="udl", key="F-1", title="The queue")
        )
        await FeatureRecords(records).save_feature(
            Feature(project="thevault", key="F-1", title="The queue")
        )
        tasks = TaskRecords(records)
        await tasks.save_task(Task(project="udl", feature="F-1", key="F-1.1", title="Assign"))
        other = await tasks.save_task(
            Task(
                project="thevault",
                feature="F-1",
                key="F-1.1",
                title="Something else",
                assignee="fake-a",
            )
        )
        await tasks.move(other, "queued", by=Principal(kind="human", name="me"))
        await tasks.move(
            other, "in_progress", by=Principal(kind="agent", name="fake-a", role="start")
        )
        await AgentRecords(records).save_agent(Agent(name="fake-a", title="Fake A", runner="fake"))

        answer = await suggested(service, project="udl")

    assert answer.suggestions == []


async def test_the_limit_is_the_operations(service: UDLService) -> None:
    answer = await suggested(service, project="udl", limit=1)

    assert [one.task for one in answer.suggestions] == ["F-1.1"]
