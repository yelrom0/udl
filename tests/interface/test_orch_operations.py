"""
---------------------------
- test_orch_operations.py -
---------------------------

26.6's catalogue through the interfaces udl makes for every app's operations: each operation's
CLI command (the card's own path), its API route (`POST /plugins/orch/<op>`) and its MCP tool
(`orch_<op>`), and each of them run for real by the owner over all three.

The harness's in-memory databases keep what they hold from one service to the next, so a project
is set through one service and read through another (each CLI run makes its own).
"""

# System Imports
import inspect
import json
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import Any, Final

# Package Imports
import pytest
from mcp.types import TextContent
from typer.core import TyperCommand, TyperGroup

from support.ai_servers import FakeOpenRouter

# Local Imports
from support.api import client_for as api_client_for
from support.cli import CLI
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from support.runners import SharedRunner
from udl.db.manager import DBManager
from udl.interfaces.adapters import cli_groups
from udl.interfaces.cli.common import operation_of
from udl.plugins.ai.base import AIProviderConnection
from udl.plugins.ai.openrouter import OpenRouterProvider
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers, SessionState
from udl.plugins.apps.orch.models import Actions
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import Agent, AgentRecords, AgentSession, SessionRecords
from udl.plugins.registry import AI, APPS, RUNNERS
from udl.plugins.runners.base import SessionSpec

CATALOGUE: Final[dict[str, tuple[str, ...]]] = {
    "orch_inbox": ("orch", "inbox"),
    "orch_usage": ("orch", "agents", "usage"),
    "orch_list_projects": ("orch", "projects"),
    "orch_get_project": ("orch", "projects", "show"),
    "orch_set_project": ("orch", "projects", "set"),
    "orch_delete_project": ("orch", "projects", "rm"),
    "orch_set_repo": ("orch", "repos", "set"),
    "orch_delete_repo": ("orch", "repos", "rm"),
    "orch_list_features": ("orch", "features"),
    "orch_get_feature": ("orch", "features", "show"),
    "orch_set_feature": ("orch", "features", "set"),
    "orch_list_tasks": ("orch", "tasks"),
    "orch_get_task": ("orch", "tasks", "show"),
    "orch_set_task": ("orch", "tasks", "set"),
    "orch_move_task": ("orch", "tasks", "move"),
    "orch_split_task": ("orch", "tasks", "split"),
    "orch_add_link": ("orch", "links", "add"),
    "orch_remove_link": ("orch", "links", "rm"),
    "orch_find_links": ("orch", "links", "find"),
    "orch_add_note": ("orch", "note"),
    "orch_import_plan": ("orch", "import"),
    "orch_list_agents": ("orch", "agents"),
    "orch_get_agent": ("orch", "agents", "show"),
    "orch_set_agent": ("orch", "agents", "set"),
    "orch_delete_agent": ("orch", "agents", "rm"),
    "orch_pause_agent": ("orch", "agents", "pause"),
    "orch_resume_agent": ("orch", "agents", "resume"),
    "orch_get_session": ("orch", "agents", "session"),
    "orch_stop_session": ("orch", "agents", "stop"),
    "orch_send": ("orch", "agents", "send"),
    "orch_get_queue": ("orch", "queue", "show"),
    "orch_my_task": ("orch", "queue", "mine"),
    "orch_assign": ("orch", "queue", "add"),
    "orch_reorder_queue": ("orch", "queue", "set"),
    "orch_unassign": ("orch", "queue", "rm"),
    "orch_suggest": ("orch", "suggest"),
    "orch_chat_start": ("orch", "chats", "start"),
    "orch_chat_send": ("orch", "chats", "send"),
    "orch_chat_list": ("orch", "chats"),
    "orch_chat_get": ("orch", "chats", "show"),
    "orch_chat_stop": ("orch", "chats", "stop"),
    "orch_chat_delete": ("orch", "chats", "rm"),
    "orch_chat_permission": ("orch", "chats", "allow"),
}
"""The card's CLI command for each of the catalogue's operations (26.6)."""


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """A udl on the fakes, with orch, and one project in it."""
    found = Harness(tmp_path)
    found.registry.register(APPS, Orch)
    found.registry.register(RUNNERS, SharedRunner)  # (the runner a session is started on)
    return found


@pytest.fixture
async def with_a_project(harness: Harness) -> AsyncIterator[Harness]:
    """The harness, with the project every read of the catalogue is of, and a feature of it."""
    async with harness.service() as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await service.call("orch_set_feature", {"key": "26", "title": "Work tracking"})
    yield harness  # noqa: PT022 - (the service closes itself: there is nothing to undo)


# ---------------------------------------------------------------------------------------------
# The command, the route and the tool, one operation at a time
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "path"), sorted(CATALOGUE.items()))
async def test_the_cli_command_is_the_card_s(
    harness: Harness, name: str, path: tuple[str, ...]
) -> None:
    # (The groups are the ones the CLI makes for the operations a service has: `udl orch` is one
    # of its plugins' groups, built when a command runs.)
    async with harness.service() as service:
        groups = cli_groups(service.operations)

    command: Any = groups["orch"]
    for part in path[1:]:
        assert isinstance(command, TyperGroup), f"{' '.join(path)} is not a group"
        command = command.commands[part]

    assert isinstance(command, TyperGroup | TyperCommand)
    assert operation_of(inspect.unwrap(command.callback or print)) == name


@pytest.mark.parametrize("name", sorted(CATALOGUE))
async def test_the_api_route_is_there(harness: Harness, name: str) -> None:
    short = name.removeprefix("orch_")

    async with api_client_for(harness) as client:
        document = (await client.get("/openapi.json")).json()

    route = document["paths"][f"/plugins/orch/{short}"]["post"]
    assert route["operationId"] == name


@pytest.mark.parametrize("name", sorted(CATALOGUE))
async def test_the_mcp_tool_is_there(harness: Harness, name: str) -> None:
    async with mcp_client_for(harness) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}

    assert name in tools


# ---------------------------------------------------------------------------------------------
# Run for real, by the owner, over each interface
# ---------------------------------------------------------------------------------------------


def test_the_owner_reads_the_catalogue_over_the_cli(with_a_project: Harness) -> None:
    result = CLI(with_a_project)("orch", "projects", "--json")

    assert result.exit_code == 0, result.output
    assert [project["name"] for project in json.loads(result.stdout)["projects"]] == ["udl"]


def test_the_owner_sets_a_task_over_the_cli(with_a_project: Harness) -> None:
    result = CLI(with_a_project)(
        "orch", "tasks", "set", "--feature", "26", "--title", "Projects and repos", "--json"
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["key"] == "26.1"


def test_the_owner_moves_a_task_by_its_key_over_the_cli(with_a_project: Harness) -> None:
    # (A key is text: `26.1` read as JSON would be the float 26.1, and the request would refuse
    # it. The same for a feature key of `26`.)
    made = CLI(with_a_project)(
        "orch", "tasks", "set", "--feature", "26", "--title", "One", "--json"
    )
    assert made.exit_code == 0, made.output

    result = CLI(with_a_project)("orch", "tasks", "move", "26.1", "ready", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["state"] == "ready"


async def test_the_owner_reads_the_catalogue_over_the_api(with_a_project: Harness) -> None:
    async with api_client_for(with_a_project) as client:
        answer = await client.post("/plugins/orch/list_projects", json={})

    assert answer.status_code == 200, answer.text
    assert [project["name"] for project in answer.json()["projects"]] == ["udl"]


async def test_a_move_over_the_api_says_who_made_it(with_a_project: Harness) -> None:
    # (The server's owner is a person, so the system note the move writes is `human:owner`'s:
    # `_by`'s owner branch, through the interface that sets it.)
    async with api_client_for(with_a_project) as client:
        await client.post(
            "/plugins/orch/set_task", json={"key": "26.1", "feature": "26", "title": "One"}
        )
        moved = await client.post("/plugins/orch/move_task", json={"key": "26.1", "to": "ready"})
        found = await client.post("/plugins/orch/get_task", json={"key": "26.1"})

    assert moved.status_code == 200, moved.text
    assert [
        (note["note_kind"], note["text"], note["author"]) for note in found.json()["notes"]
    ] == [("system", "open → ready, by human:owner.", "udl")]


async def test_the_owner_sets_a_project_over_the_api(harness: Harness) -> None:
    async with api_client_for(harness) as client:
        answer = await client.post(
            "/plugins/orch/set_project", json={"name": "udl", "title": "udl"}
        )

    assert answer.status_code == 200, answer.text
    assert answer.json()["title"] == "udl"


async def test_the_owner_reads_the_catalogue_over_mcp(with_a_project: Harness) -> None:
    async with mcp_client_for(with_a_project) as client:
        answer = await client.call_tool("orch_list_projects", {})

    assert not answer.is_error
    [found] = answer.content
    assert isinstance(found, TextContent)
    assert [project["name"] for project in json.loads(found.text)["projects"]] == ["udl"]


def test_the_owner_sets_and_reads_an_agent_over_the_cli(with_a_project: Harness) -> None:
    made = CLI(with_a_project)(
        "orch",
        "agents",
        "set",
        "--name",
        "boss",
        "--title",
        "Boss",
        "--runner",
        "fake",
        "--role",
        "manager",
        "--json",
    )
    assert made.exit_code == 0, made.output
    assert json.loads(made.stdout)["role"] == "manager"

    listed = CLI(with_a_project)("orch", "agents", "--json")

    assert listed.exit_code == 0, listed.output
    assert [one["agent"]["name"] for one in json.loads(listed.stdout)["items"]] == ["boss"]


def test_the_owner_reads_a_queue_over_the_cli(with_a_project: Harness) -> None:
    made = CLI(with_a_project)(
        "orch", "agents", "set", "--name", "boss", "--title", "Boss", "--runner", "fake", "--json"
    )
    assert made.exit_code == 0, made.output
    task = CLI(with_a_project)(
        "orch", "tasks", "set", "--feature", "26", "--title", "One", "--json"
    )
    assert task.exit_code == 0, task.output
    assigned = CLI(with_a_project)("orch", "queue", "add", "26.1", "--agent", "boss", "--json")
    assert assigned.exit_code == 0, assigned.output

    queue = CLI(with_a_project)("orch", "queue", "show", "boss", "--json")

    assert queue.exit_code == 0, queue.output
    assert [one["key"] for one in json.loads(queue.stdout)["tasks"]] == ["26.1"]


AN_AGENT: Final = ("agents", "set", "--name", "boss", "--title", "Boss", "--runner", "fake")
A_TASK: Final = ("tasks", "set", "--feature", "26", "--title", "One")
TAKEN: Final = ("queue", "add", "26.1", "--agent", "boss")


@pytest.mark.parametrize(
    ("name", "before", "argv"),
    [
        ("orch_list_agents", (), ("agents",)),
        ("orch_get_agent", (AN_AGENT,), ("agents", "show", "boss")),
        ("orch_set_agent", (), AN_AGENT),
        ("orch_delete_agent", (AN_AGENT,), ("agents", "rm", "boss")),
        ("orch_pause_agent", (AN_AGENT,), ("agents", "pause", "boss")),
        (
            "orch_resume_agent",
            (AN_AGENT, ("agents", "pause", "boss")),
            ("agents", "resume", "boss"),
        ),
        ("orch_get_queue", (AN_AGENT, A_TASK, TAKEN), ("queue", "show", "boss")),
        ("orch_my_task", (AN_AGENT, A_TASK, TAKEN), ("queue", "mine", "boss")),
        ("orch_assign", (AN_AGENT, A_TASK), TAKEN),
        (
            "orch_reorder_queue",
            (AN_AGENT, A_TASK, TAKEN),
            ("queue", "set", "boss", "--keys", "26.1"),
        ),
        ("orch_unassign", (AN_AGENT, A_TASK, TAKEN), ("queue", "rm", "26.1")),
        ("orch_suggest", (AN_AGENT, A_TASK), ("suggest",)),
    ],
)
def test_the_owner_runs_each_operation_over_the_cli(
    with_a_project: Harness, name: str, before: tuple[tuple[str, ...], ...], argv: tuple[str, ...]
) -> None:
    """Every one of the card's operations, run for real by the owner over the CLI (the card's
    "an interface test per operation")."""
    for command in before:
        made = CLI(with_a_project)("orch", *command, "--json")
        assert made.exit_code == 0, made.output

    result = CLI(with_a_project)("orch", *argv, "--json")

    assert result.exit_code == 0, result.output
    assert isinstance(json.loads(result.stdout), dict), f"{name} answered no JSON"


async def test_the_owner_runs_each_operation_over_the_api(harness: Harness) -> None:
    """The same three the CLI cannot set up on its own: a session's read, its stop and a message
    typed into it (the session is started here, on the harness, as the dispatcher starts one)."""
    async with harness.service() as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await service.call("orch_set_agent", {"name": "boss", "title": "Boss", "runner": "fake"})
        records = AppRecords(service.db)
        app = await service.apps.get("orch")
        assert isinstance(app, Orch)
        runner = await app.harnesses.runner("local", "fake")
        ref = await runner.start(SessionSpec(cwd=str(harness.tmp_path)))
        found = await SessionRecords(records).save_session(
            AgentSession(agent="boss", ref=ref, state=SessionState.IDLE)
        )
    async with api_client_for(harness) as client:
        read = await client.post("/plugins/orch/get_session", json={"session": str(found.id)})
        sent = await client.post(
            "/plugins/orch/send", json={"agent": "boss", "text": "Go on", "delivery": "turn"}
        )
        stopped = await client.post("/plugins/orch/stop_session", json={"session": str(found.id)})

    assert read.status_code == 200, read.text
    assert read.json()["id"] == str(found.id)
    assert sent.status_code == 200, sent.text
    assert sent.json()["turn"] == 0
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["state"] == "ended"


async def test_the_owner_asks_what_to_do_next_over_mcp(with_a_project: Harness) -> None:
    async with api_client_for(with_a_project) as client:
        made = await client.post(
            "/plugins/orch/set_task", json={"key": "26.1", "feature": "26", "title": "One"}
        )
        agent = await client.post(
            "/plugins/orch/set_agent",
            json={"name": "boss", "title": "Boss", "runner": "fake"},
        )
    assert made.status_code == 200, made.text
    assert agent.status_code == 200, agent.text

    async with mcp_client_for(with_a_project) as client:
        answer = await client.call_tool("orch_suggest", {"project": "udl"})

    assert not answer.is_error
    [found] = answer.content
    assert isinstance(found, TextContent)
    assert [one["task"] for one in json.loads(found.text)["suggestions"]] == ["26.1"]


# ---------------------------------------------------------------------------------------------
# 28.7: the switch, as the card writes it
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def with_a_merger(harness: Harness) -> AsyncIterator[Harness]:
    """A udl on the fakes, its project, and `claude`, a manager of it (28.7's merger)."""
    async with harness.service() as service:
        await service.call("orch_set_project", {"name": "udl", "title": "udl"})
        await AgentRecords(AppRecords(service.db)).save_agent(
            Agent(
                name="claude",
                title="Claude",
                runner="fake",
                host="local",
                role=AgentRole.MANAGER,
                manages=ManagerPowers(assign=True, answer=True, review=True, plan=True),
            )
        )
    yield harness  # noqa: PT022 - (the service closes itself: there is nothing to undo)


def test_the_owner_turns_auto_merge_on_over_the_cli(with_a_merger: Harness) -> None:
    """(The card's own line: `udl orch projects auto-merge udl --on --merger claude`.)"""
    result = CLI(with_a_merger)(
        "orch", "projects", "auto-merge", "udl", "--on", "--merger", "claude"
    )

    assert result.exit_code == 0, result.output
    shown = CLI(with_a_merger)("orch", "projects", "show", "udl", "--json")
    assert shown.exit_code == 0, shown.output
    found = json.loads(shown.stdout)
    assert (found["project"]["auto_merge"], found["project"]["merger"]) == (True, "claude")


# ---------------------------------------------------------------------------------------------
# 27.14: what each agent has left, on the CLI and over MCP
# ---------------------------------------------------------------------------------------------


def test_the_usage_table_shows_a_paid_and_a_free_agent(harness: Harness) -> None:
    """The card's Done when: a paid agent's money left and a free agent's requests left, with
    its reset time, on the CLI and over MCP."""
    _wire_openrouter(harness)

    def seed(db: DBManager) -> Awaitable[object]:
        return db.upsert_record(
            AIProviderConnection(
                name="openrouter",
                plugin="openrouter",
                options={"api_key": "sk-or-v1-test"},
                default_model="fake-1",
            )
        )

    harness.seed(seed)
    harness.seed(_save_agents)

    result = CLI(harness)("orch", "agents", "usage", "--json")

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["items"]
    by = {one["agent"]: one for one in rows}
    # (FakeOpenRouter's key has a limit and is not on the free tier: a paid key, 1,000 a day.)
    assert by["deepseek-b"]["paid"]["left"] == "8.75"
    assert by["ernie-c"]["free"] is not None
    assert by["ernie-c"]["free"]["allowance"] == 1000
    assert by["ernie-c"]["free"]["resets_at"].endswith("T00:00:00Z")

    # (The table, not just --json: the renderer is what a person reads.)
    table = CLI(harness)("orch", "agents", "usage")

    assert table.exit_code == 0, table.output
    assert "deepseek-b" in table.stdout
    assert "ernie-c" in table.stdout
    assert "8.75" in table.stdout
    assert "1,000" in table.stdout


async def _save_agents(db: DBManager) -> None:
    """(The two agents the usage table is of: one paid, one free.)"""
    await db.upsert_record(
        Agent(name="deepseek-b", title="deepseek-b", runner="fake", model="deepseek/chat")
    )
    await db.upsert_record(
        Agent(name="ernie-c", title="ernie-c", runner="fake", model="deepseek/free")
    )


def _wire_openrouter(harness: Harness, fake: FakeOpenRouter | None = None) -> FakeOpenRouter:
    """(The fake OpenRouter, as the provider's transport: the service's HTTP client is the
    fake's, so nothing here patches the provider itself.)"""
    fake = fake or FakeOpenRouter()
    harness.registry.register(AI, OpenRouterProvider)
    harness.http = fake.http()
    return fake


async def test_the_usage_comes_over_mcp(harness: Harness) -> None:
    """(The same answer over MCP: the operation is offered, and its rows are the CLI's —
    the paid key's money and the free agent's requests.)"""
    _wire_openrouter(harness)

    async with harness.service() as service:
        await service.db.upsert_record(
            AIProviderConnection(
                name="openrouter",
                plugin="openrouter",
                options={"api_key": "sk-or-v1-test"},
                default_model="fake-1",
            )
        )
        await _save_agents(service.db)

    async with mcp_client_for(harness) as client:
        listed = (await client.list_tools()).tools
        assert "orch_usage" in {tool.name for tool in listed}

        answer = await client.call_tool("orch_usage", {})

    assert answer is not None
    first = answer.content[0]
    assert isinstance(first, TextContent)
    rows = json.loads(first.text)["items"]
    by = {one["agent"]: one for one in rows}
    assert by["deepseek-b"]["paid"]["left"] == "8.75"
    assert by["ernie-c"]["free"]["allowance"] == 1000


def test_an_agent_s_answer_carries_its_usage_line(harness: Harness) -> None:
    """(27.14: `orch_get_agent` fills `AgentInfo.usage` — the one-line `line` — and asks the
    provider once, not once per agent.)"""
    _wire_openrouter(harness)

    def seed(db: DBManager) -> Awaitable[object]:
        return db.upsert_record(
            AIProviderConnection(
                name="openrouter",
                plugin="openrouter",
                options={"api_key": "sk-or-v1-test"},
                default_model="fake-1",
            )
        )

    harness.seed(seed)
    harness.seed(_save_agents)

    result = CLI(harness)("orch", "agents", "show", "deepseek-b", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["usage"] == "$8.75 of $10 left"


def test_the_provider_is_the_one_the_model_names(harness: Harness) -> None:
    """(27.14: an agent whose model names a provider spends that provider's key, not the first
    enabled connection's — here an `openai` connection that answers nothing is first.)"""
    from udl.plugins.ai.openai import OpenAICompatibleProvider

    _wire_openrouter(harness)
    harness.registry.register(AI, OpenAICompatibleProvider)

    def seed(db: DBManager) -> Awaitable[object]:
        return _two_connections(db)

    harness.seed(seed)
    harness.seed(lambda db: _save_one_agent(db, "deepseek-b", "openrouter/deepseek/chat"))

    result = CLI(harness)("orch", "agents", "usage", "--json")

    assert result.exit_code == 0, result.output
    # (The openrouter key's money: the openai connection was never asked.)
    assert json.loads(result.stdout)["items"][0]["paid"]["left"] == "8.75"


async def _two_connections(db: DBManager) -> None:
    """(An `openai` connection that answers nothing, and the `openrouter` one that does.)"""
    await db.upsert_record(
        AIProviderConnection(
            name="openai",
            plugin="openai",
            options={"api_key": "sk-test", "url": "http://127.0.0.1:9/v1"},
            default_model="gpt-4",
        )
    )
    await db.upsert_record(
        AIProviderConnection(
            name="openrouter",
            plugin="openrouter",
            options={"api_key": "sk-or-v1-test"},
            default_model="fake-1",
        )
    )


async def _save_one_agent(db: DBManager, name: str, model: str) -> None:
    """(One agent, on `model`.)"""
    await db.upsert_record(Agent(name=name, title=name, runner="fake", model=model))


def test_an_agent_with_no_provider_has_an_empty_usage_line(harness: Harness) -> None:
    """(27.14: a paid agent with no provider connection has nothing to measure, and
    `AgentInfo.usage` is empty rather than the placeholder line.)"""
    harness.seed(lambda db: _save_one_agent(db, "deepseek-b", "deepseek/chat"))

    result = CLI(harness)("orch", "agents", "show", "deepseek-b", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["usage"] == ""


def test_the_usage_totals_are_per_account(harness: Harness) -> None:
    """(27.15: the "in all" totals — what each account has spent today across its agents,
    keyed by the udl connection the key was read through.)"""
    _wire_openrouter(harness)

    def seed(db: DBManager) -> Awaitable[object]:
        return db.upsert_record(
            AIProviderConnection(
                name="openrouter",
                plugin="openrouter",
                options={"api_key": "sk-or-v1-test"},
                default_model="fake-1",
            )
        )

    harness.seed(seed)
    harness.seed(_save_agents)

    result = CLI(harness)("orch", "agents", "usage", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["totals"] == {"openrouter": "0"}


async def _a_project(db: DBManager) -> None:
    """(A project, so the inbox has one to be of.)"""
    from udl.plugins.apps.orch.records import Project as ProjectRecord
    from udl.plugins.apps.orch.records import ProjectRecords

    await ProjectRecords(AppRecords(db)).save_project(ProjectRecord(name="udl", title="udl"))


def test_the_inbox_s_agent_lines_carry_what_each_agent_has_left(harness: Harness) -> None:
    """(27.15: `AgentLine.usage` — the inbox's one-line usage, the same line `orch_usage`
    gives.)"""
    _wire_openrouter(harness)

    def seed(db: DBManager) -> Awaitable[object]:
        return db.upsert_record(
            AIProviderConnection(
                name="openrouter",
                plugin="openrouter",
                options={"api_key": "sk-or-v1-test"},
                default_model="fake-1",
            )
        )

    harness.seed(seed)
    harness.seed(_save_agents)
    harness.seed(_a_project)

    result = CLI(harness)("orch", "inbox", "--json")

    assert result.exit_code == 0, result.output
    lines = {one["name"]: one["usage"] for one in json.loads(result.stdout)["agents"]}
    assert lines["deepseek-b"] == "$8.75 of $10 left"
    assert lines["ernie-c"].startswith("1,000 of 1,000 requests")


async def test_a_low_paid_key_raises_one_event_and_one_to_action(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """(27.15: a crossing into `low` publishes `orch.usage.low` once, and raises one To action
    ("raise the cap?") for the key — not one a read.)"""
    from udl.plugins.apps.base import AppEvents

    _wire_openrouter(harness, FakeOpenRouter(used="9.5", limit="10"))

    async with harness.service() as service:
        await _one_connection(service.db)
        await _a_project(service.db)
        await _save_one_agent(service.db, "deepseek-b", "deepseek/chat")
        app = await service.apps.get("orch")
        assert isinstance(app, Orch)
        events = AppEvents(service.bus, "orch", app.event_types())
        seen: list[str] = []
        said: list[str] = []
        async with events.subscribe("orch.usage.*") as sub:
            await service.call("orch_usage", {})
            await service.call("orch_usage", {})
            async for event in sub:
                seen.append(event.type)
                said.append(event.model_dump_json())
                if len(seen) >= 1:
                    break
        actions = await service.call("orch_list_actions", {})

    assert seen == ["orch.usage.low"]
    assert isinstance(actions, Actions)
    assert [one.title for one in actions.actions] == ["Raise the cap for openrouter (limit $10)"]
    assert "sk-or-v1-test" not in caplog.text
    assert all("sk-or-v1-test" not in one for one in said)
    assert all("sk-or-v1-xxx" not in one for one in said)


async def _one_connection(db: DBManager) -> None:
    """(The openrouter connection the usage tests read through.)"""
    await db.upsert_record(
        AIProviderConnection(
            name="openrouter",
            plugin="openrouter",
            options={"api_key": "sk-or-v1-test"},
            default_model="fake-1",
        )
    )
