"""
----------------------
- test_orch_inbox.py -
----------------------

28.4's done-when: the inbox is the same from the CLI, the API and MCP — one snapshot test of its
model. What a person reads today in human-comms.md (what is ready to merge, what is being asked,
what only a person can do, and what every agent is doing) comes back from one call, whoever asks.
"""

# System Imports
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Final

# Package Imports
import pytest
from mcp.types import TextContent

# Local Imports
from support.api import client_for as api_client_for
from support.clock import FakeClock
from support.cli import CLI
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import AgentRole, Inbox, InboxRequest
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import Agent, AgentRecords
from udl.plugins.registry import APPS

SNAPSHOT: Final = Path(__file__).parent / "snapshots" / "orch_inbox.json"
"""The inbox's own snapshot: what the CLI, the API and MCP all answer, in one file."""


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """A udl on the fakes, with orch, on a clock that stands still (so the snapshot is one)."""
    found = Harness(tmp_path, clock=FakeClock())
    found.registry.register(APPS, Orch)
    return found


@pytest.fixture
async def with_things_to_do(harness: Harness) -> AsyncIterator[Harness]:
    """The project every read is of, with a blocking question, an action only a person can do,
    and an agent that has not started: human-comms.md's sections, as data."""
    async with harness.service() as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await service.call("orch_set_feature", {"key": "26", "title": "Work tracking"})
        await service.call(
            "orch_set_task", {"key": "26.4", "feature": "26", "title": "Features and tasks"}
        )
        await service.call(
            "orch_set_action", {"title": "Merge 27.10", "when": "now", "task": "26.4"}
        )
        await service.call(
            "orch_ask",
            {
                "task": "26.4",
                "title": "Which theme?",
                "text": "The shadcn example or the template's?",
                "blocking": True,
            },
        )
        await AgentRecords(AppRecords(service.db)).save_agent(
            Agent(name="deepseek-a", title="deepseek-a", runner="fake", role=AgentRole.IMPLEMENTER)
        )
    yield harness  # noqa: PT022 - (the service closes itself: there is nothing to undo)


def test_the_cli_s_inbox_is_the_snapshot(with_things_to_do: Harness) -> None:
    """(One snapshot of the model, and each interface answers it: the CLI's here.)"""
    result = CLI(with_things_to_do)("orch", "inbox", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _snapshot()


async def test_the_api_s_and_mcp_s_inbox_is_the_snapshot(with_things_to_do: Harness) -> None:
    async with api_client_for(with_things_to_do) as client:
        answer = await client.post("/plugins/orch/inbox", json={})
    assert answer.status_code == 200, answer.text

    async with mcp_client_for(with_things_to_do) as client:
        found = await client.call_tool("orch_inbox", {})

    (said,) = found.content
    assert isinstance(said, TextContent)
    assert answer.json() == json.loads(said.text) == _snapshot()


def _snapshot() -> Any:
    """The inbox the fixture's udl answers, as each interface prints it (28.4's snapshot)."""
    return json.loads(SNAPSHOT.read_text())


async def test_the_inbox_says_what_waits_on_a_person(with_things_to_do: Harness) -> None:
    """(The card's sections, each item with what it is about: a person reads it, not udl.)"""
    async with with_things_to_do.service() as service:
        found = await service.call("orch_inbox", {})

    assert isinstance(found, Inbox)
    assert [(one.key, one.blocking, one.task) for one in found.questions] == [
        ("Q-1", True, "26.4")
    ]
    assert [(one.key, one.title, one.when.value) for one in found.actions] == [
        ("A-1", "Merge 27.10", "now")
    ]
    assert (found.project, found.merge.ready, found.merge.done) == ("udl", [], [])


async def test_an_agents_line_says_what_it_is_doing(with_things_to_do: Harness) -> None:
    # (Its role, its state and its task are derived from its session, never stored in the line.)
    async with with_things_to_do.service() as service:
        found = await service.call("orch_inbox", {})

    assert isinstance(found, Inbox)
    (line,) = found.agents
    assert (line.name, line.role, line.state.value, line.task) == (
        "deepseek-a",
        AgentRole.IMPLEMENTER,
        "idle",
        None,
    )
    assert line.line == "deepseek-a — idle"


def test_the_inbox_only_reads() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert offered["inbox"].hints.read_only
    assert offered["inbox"].hints.open_world, "it reads the pulls' state on GitHub"
    assert offered["inbox"].request is InboxRequest
    assert offered["inbox"].response is Inbox
