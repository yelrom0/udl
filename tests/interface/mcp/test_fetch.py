"""
-----------------
- test_fetch.py -
-----------------

The page fetcher's tools: fetch_pages (for an agent whose own fetch failed), get_fetch,
get_fetched_page, the checks that wait, and the cookies' tools, against the anti-bot fixture
site
"""

# System Imports
from datetime import timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from mcp import Client

# Local Imports
from support.antibot import AntiBotSite
from support.interfaces import Harness
from udl.config import APISettings, FetchSettings


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(
        tmp_path,
        api=APISettings(allowed_output_roots=[tmp_path]),
        fetch=FetchSettings(site_delay=timedelta(0), timeout=timedelta(seconds=10)),
    )


async def called(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def test_an_agent_reads_pages_its_own_fetch_could_not(
    client: Client, antibot: AntiBotSite
) -> None:
    shown = await called(
        client, "fetch_pages", urls=[antibot.url("/docs"), antibot.url("/data.json")]
    )

    docs, data = shown["items"]
    assert docs["content"].startswith("# Widgets: a guide")
    assert data["content"].startswith('{"name":"widget"')
    assert shown["status"] == "finished"


async def test_its_tools_say_what_they_do_to_the_world(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    fetch = tools["fetch_pages"]
    assert fetch.annotations is not None
    assert (fetch.annotations.read_only_hint, fetch.annotations.open_world_hint) == (True, True)
    assert fetch.description is not None
    assert "your own fetch failed" in fetch.description
    deleting = tools["delete_cookies"].annotations
    assert deleting is not None
    assert deleting.destructive_hint is True


async def test_a_fetch_and_its_pages_are_had_again(client: Client, antibot: AntiBotSite) -> None:
    shown = await called(client, "fetch_pages", urls=[antibot.url("/docs")], max_chars=0)

    again = await called(client, "get_fetch", id=shown["id"])
    page = await called(
        client, "get_fetched_page", id=shown["items"][0]["id"], offset=2, max_chars=7
    )

    assert again["items"][0]["content"].startswith("# Widgets")
    assert page["content"] == "Widgets"
    assert page["length"] > 100


async def test_the_checks_that_wait_and_the_cookies(client: Client) -> None:
    assert await called(client, "list_challenges") == {"challenges": []}
    assert await called(client, "list_fetches") == {"fetches": []}


async def test_a_fetch_the_tool_refuses_says_why(client: Client, antibot: AntiBotSite) -> None:
    result = await client.call_tool(
        "fetch_pages", {"urls": [antibot.url("/docs")], "headers": {"Cookie": "a=1"}}
    )

    assert result.is_error
    assert "import_cookies" in str(result.content)
