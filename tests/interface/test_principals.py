"""
---------------------
- test_principals.py -
---------------------

Who udl answers (27.2): the server's owner (its API key), and an agent with its own token, which
may call only what its role allows. Over the API and over MCP's /mcp, since both answer the same
operations on the same service.

The role table's own operations (`orch_*`, and `github_get_pull`) come with 26.6 and 25.5, so the
table is given one of udl's own operations here (`get_status`) — what is being tested is the
gate, and that an agent that may call one thing may not call another.
"""

# System Imports
import json
from pathlib import Path
from typing import Any, Final

# Package Imports
import httpx
import pytest
from pydantic import SecretStr

# Local Imports
from support.api import client_for
from support.interfaces import Harness
from support.tasks import held
from udl.config import APISettings
from udl.core.models import DownloadRequest
from udl.errors import ErrorCode, ErrorInfo, NotAllowed
from udl.interfaces.security import current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import AgentRole
from udl.plugins.apps.orch.records import Agent as AgentRecord
from udl.plugins.apps.orch.records import AgentRecords
from udl.plugins.apps.orch.tokens import ROLE_OPERATIONS, OrchTokens, issue_token

API_KEY: Final = "s3cret"
TOKEN: Final = "udl_agent_abcdefghijklmnop"  # noqa: S105 - (a fake's own)
URL: Final = "https://example.com/a"
"""A URL a download would start (it is refused before it is looked at)."""
STATUS: Final = "get_status"
CONFIG: Final = "get_config"
DOWNLOAD: Final = "download"


MCP_HEADERS: Final = {"Accept": "application/json, text/event-stream"}
INITIALIZE: Final = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
"""The MCP handshake's first message."""


@pytest.fixture
def keyed(tmp_path: Path) -> Harness:
    """A udl whose server needs its API key."""
    return Harness(tmp_path, api=APISettings(api_key=SecretStr(API_KEY)))


@pytest.fixture
def may_call_get_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """A role table that lets an agent call `get_status` (the orch_* operations come with 26.6)."""
    monkeypatch.setitem(ROLE_OPERATIONS, AgentRole.IMPLEMENTER, frozenset({STATUS}))


@pytest.fixture
async def token(keyed: Harness, may_call_get_status: None) -> str:
    """A token issued to an agent (stored, so the server can look it up)."""
    async with keyed.service() as service:
        agents = AgentRecords(AppRecords(service.db))
        await agents.save_agent(
            AgentRecord.model_validate({"name": "fake-a", "title": "Fake A", "runner": "fake"})
        )
        return await issue_token(service, "fake-a")


# ---------------------------------------------------------------------------------------------
# Over the API
# ---------------------------------------------------------------------------------------------


async def test_the_owner_calls_anything(keyed: Harness) -> None:
    async with held(client_for(keyed)) as client:
        response = await client.get("/status", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200


async def test_a_request_with_neither_key_nor_token_is_unauthorized(keyed: Harness) -> None:
    async with held(client_for(keyed)) as client:
        response = await client.get("/status")

    assert response.status_code == 401


async def test_a_token_udl_does_not_know_is_unauthorized(keyed: Harness) -> None:
    async with held(client_for(keyed)) as client:
        response = await client.get("/status", headers={"X-API-Key": TOKEN})

    assert response.status_code == 401


async def test_an_agent_calls_what_its_role_allows(keyed: Harness, token: str) -> None:
    async with held(client_for(keyed)) as client:
        response = await client.get("/status", headers={"X-API-Key": token})

    assert response.status_code == 200


async def test_an_agent_is_refused_anything_else(keyed: Harness, token: str) -> None:
    async with held(client_for(keyed)) as client:
        response = await client.post(
            "/downloads", headers={"X-API-Key": token}, json={"urls": [URL]}
        )

    assert response.status_code == 403
    error = ErrorInfo.model_validate(response.json())
    assert error.code is ErrorCode.NOT_ALLOWED
    assert "implementer" in error.message
    assert error.hint


async def test_a_service_refuses_an_agent_before_it_reads_the_request(
    keyed: Harness, token: str
) -> None:
    """UDLService.call refuses before the request is validated: an agent that asks for something
    it may not have is told so, not what the request would have taken."""
    async with keyed.service() as service:
        current_principal.set(await OrchTokens(AppRecords(service.db)).principal_for(token))

        with pytest.raises(NotAllowed, match="implementer"):
            await service.call(DOWNLOAD, DownloadRequest(urls=["not-a-url"]))


async def test_a_service_method_is_guarded_too(keyed: Harness, token: str) -> None:
    """A route that runs the service's own method rather than UDLService.call is held to the
    same rule (the guard is on the method, not only on the call)."""
    async with keyed.service() as service:
        current_principal.set(await OrchTokens(AppRecords(service.db)).principal_for(token))

        with pytest.raises(NotAllowed, match="implementer"):
            await service.download(DownloadRequest(urls=[URL], record=False))


# ---------------------------------------------------------------------------------------------
# Over MCP's /mcp
# ---------------------------------------------------------------------------------------------


async def test_the_mcp_server_needs_a_key_or_a_token(keyed: Harness) -> None:
    async with held(client_for(keyed)) as client:
        refused = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        allowed = await client.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "X-API-Key": API_KEY}
        )

    assert refused.status_code == 401
    assert allowed.status_code == 200


async def test_the_mcp_server_refuses_an_agent_a_tool_it_may_not_call(
    keyed: Harness, token: str
) -> None:
    async with held(client_for(keyed)) as client:
        session = await mcp_session(client, token)
        refused = await mcp_call(client, token, session, CONFIG, {})

    assert refused["result"]["isError"] is True
    assert "implementer" in refused["result"]["content"][0]["text"]


async def test_the_mcp_server_lets_an_agent_call_what_its_role_allows(
    keyed: Harness, token: str
) -> None:
    async with held(client_for(keyed)) as client:
        session = await mcp_session(client, token)
        allowed = await mcp_call(client, token, session, STATUS, {})

    assert "structuredContent" in allowed["result"]


async def test_a_call_on_someone_elses_session_is_its_own_callers(
    keyed: Harness, token: str
) -> None:
    """A session is opened by one request and then serves every call on it, which may come with
    another token: each call is its own caller's, not the one that opened the session."""
    async with held(client_for(keyed)) as client:
        session = await mcp_session(client, API_KEY)  # (the owner opens it)
        refused = await mcp_call(client, token, session, CONFIG, {})

    assert refused["result"]["isError"] is True
    assert "implementer" in refused["result"]["content"][0]["text"]


async def test_the_mcp_server_does_not_know_a_token_it_was_not_given(
    keyed: Harness, token: str
) -> None:
    async with held(client_for(keyed)) as client:
        response = await client.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "X-API-Key": f"{token} rotated"}
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------------------------


async def mcp_session(client: httpx.AsyncClient, token: str) -> dict[str, str]:
    """An MCP session over /mcp, opened with `token`: the headers a call needs."""
    started = await client.post(
        "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "X-API-Key": token}
    )
    assert started.status_code == 200, started.text
    session = {"mcp-session-id": started.headers.get("mcp-session-id", "")}
    await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**MCP_HEADERS, "X-API-Key": token, **session},
    )
    return session


async def mcp_call(
    client: httpx.AsyncClient,
    token: str,
    session: dict[str, str],
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """One tool call's answer, as the JSON-RPC result."""
    response = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={**MCP_HEADERS, "X-API-Key": token, **session},
    )
    assert response.status_code == 200, response.text
    [answer] = _bodies(response)
    return answer


def _bodies(response: httpx.Response) -> list[dict[str, Any]]:
    """The JSON-RPC answers in a response (one, as JSON or as an SSE `data:` line)."""
    if "text/event-stream" not in response.headers.get("content-type", ""):
        return [response.json()]
    found: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if line.startswith("data:"):
            found.append(json.loads(line.removeprefix("data:").strip()))
    return found
