"""
------------------------
- test_integrations.py -
------------------------

An integration's operations through every interface (their adapters are generated): `udl fake
list-things --account '*'`, POST /integrations/fake/list_things and the fake_list_things tool
merge what every account has, each item tagged with its account; leaving the account out while
there are two is an error that names them
"""

# System Imports
import json
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.accounts import Fakes, connected, issued, with_fake_integration
from support.api import client_for
from support.cli import CLI
from support.clock import FakeClock
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from support.tasks import held
from udl.db.manager import DBManager


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock) -> Harness:
    return Harness(tmp_path, clock=clock)


@pytest.fixture
def fakes(harness: Harness, clock: FakeClock) -> Fakes:
    fakes = with_fake_integration(harness)

    async def two_accounts(db: DBManager) -> None:
        service = harness.service()
        for name, things in (("work", ["report", "memo"]), ("home", ["recipe"])):
            await connected(service, name, credentials=issued(fakes.server, clock))
            fakes.plugin.feed(name).add(*things)

    harness.seed(two_accounts)
    return fakes


EVERY = [("fake:home", "recipe"), ("fake:work", "memo"), ("fake:work", "report")]


def tagged(answer: Any) -> list[tuple[str, str]]:
    """The items of a FanOutResult (as JSON): each one's account and text."""
    return sorted((found["account"], found["item"]["text"]) for found in answer["items"])


def test_the_cli_lists_the_things_in_every_account(harness: Harness, fakes: Fakes) -> None:
    cli = CLI(harness)

    shown = cli("fake", "list-things", "--account", "*")
    answer = cli("fake", "list-things", "--account", "*", "--json")
    one = cli("fake", "list-things", "--account", "home", "--json")

    assert shown.exit_code == 0, shown.output
    for text in ("fake:work", "fake:home", "report", "recipe"):
        assert text in shown.stdout
    assert tagged(json.loads(answer.stdout)) == EVERY
    assert tagged(json.loads(one.stdout)) == EVERY[:1]


def test_the_cli_says_which_accounts_there_are(harness: Harness, fakes: Fakes) -> None:
    result = CLI(harness)("fake", "list-things")

    assert result.exit_code == 1
    assert "Which fake account? There are 2" in result.output
    assert "fake:home, fake:work" in result.output


async def test_the_api_lists_the_things_in_every_account(harness: Harness, fakes: Fakes) -> None:
    async with held(client_for(harness)) as api:
        answer = await api.post("/integrations/fake/list_things", json={"account": "*"})
        ambiguous = await api.post("/integrations/fake/list_things", json={})
        unknown = await api.post("/integrations/fake/list_things", json={"account": "play"})

    assert answer.status_code == 200, answer.text
    assert tagged(answer.json()) == EVERY
    assert (ambiguous.status_code, ambiguous.json()["code"]) == (400, "ambiguous_account")
    assert unknown.status_code == 404


async def test_an_agent_lists_the_things_in_every_account(harness: Harness, fakes: Fakes) -> None:
    async with held(mcp_client_for(harness)) as client:
        result = await client.call_tool("fake_list_things", {"account": "*"})
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert not result.is_error, result.content
    assert result.structured_content is not None
    assert tagged(result.structured_content) == EVERY
    schema = listed["fake_list_things"].input_schema
    assert next(iter(schema["properties"])) == "account"
