"""
--------------------
- test_accounts.py -
--------------------

The accounts' tools: an agent can list and show the accounts (which say which accounts an
integration's tools may name), check one, change and disconnect one; what changes accounts is
marked as changing things (and is never an AI action's tool)
"""

# System Imports
from typing import Any

# Package Imports
import pytest
from mcp import Client

# Local Imports
from support.accounts import Fakes, with_two_accounts
from support.interfaces import Harness


@pytest.fixture
def fakes(harness: Harness) -> Fakes:
    return with_two_accounts(harness)


async def call(client: Client, name: str, /, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def test_an_agent_sees_the_accounts(fakes: Fakes, client: Client) -> None:
    listed = await call(client, "list_accounts")
    shown = await call(client, "get_account", account="fake:home")

    assert [found["account"] for found in listed["accounts"]] == ["fake:home", "fake:work"]
    assert (shown["method"], shown["identity"]["display"]) == ("tokens", "home@example.com")


async def test_an_agent_checks_changes_and_disconnects_an_account(
    fakes: Fakes, client: Client
) -> None:
    checked = await call(client, "check_account", account="fake:work")
    changed = await call(client, "set_account", account="fake:work", options={"label": "Office"})
    gone = await call(client, "disconnect_account", account="fake:work")

    assert checked["ok"] is True
    assert changed["options"] == {"label": "Office"}
    assert (gone["deleted"], gone["subscriptions"]) == (True, ["work-things"])


async def test_the_account_tools_say_what_they_change(fakes: Fakes, client: Client) -> None:
    tools = {tool.name: tool.annotations for tool in (await client.list_tools()).tools}

    names = [
        "list_accounts",
        "get_account",
        "connect_account",
        "set_account",
        "check_account",
        "disconnect_account",
    ]
    hints = {
        name: (found.read_only_hint, found.destructive_hint)
        for name in names
        if (found := tools[name]) is not None
    }
    assert hints == {
        "list_accounts": (True, False),
        "get_account": (True, False),
        "connect_account": (False, False),
        "set_account": (False, False),
        "check_account": (True, False),
        "disconnect_account": (False, True),
    }
