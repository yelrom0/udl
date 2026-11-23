"""
----------------------
- test_transports.py -
----------------------

Where the MCP server is served: `udl mcp` on stdin/stdout (the stdio loop is stood in for here),
and at /mcp in the API, behind its API key and Host check (tests/integration/test_servers.py
connects real clients to both)
"""

# System Imports
import json
from pathlib import Path

# Package Imports
import pytest
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult
from pydantic import SecretStr

# Local Imports
from support.api import client_for
from support.cli import CLI
from support.interfaces import Harness
from udl.config import APISettings
from udl.core.models import ConfigInfo
from udl.core.operations import operations
from udl.errors import ErrorCode, ErrorInfo
from udl.plugins.runners.base import McpEndpoint, SessionSpec
from udl.plugins.runners.claude_code import _mcp_config  # pyright: ignore[reportPrivateUsage]  (the runner's own)

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_udl_mcp_serves_the_tools_on_stdio_on_its_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[tuple[list[str], ConfigInfo]] = []

    async def run_stdio(server: MCPServer) -> None:
        tools = [tool.name for tool in await server.list_tools()]
        config = await server.call_tool("get_config", {})
        assert isinstance(config, CallToolResult)
        served.append((tools, ConfigInfo.model_validate(config.structured_content)))

    monkeypatch.setattr(MCPServer, "run_stdio_async", run_stdio)
    config = tmp_path / "config.toml"
    config.write_text("max_per_site = 5\n")

    result = CLI(Harness(tmp_path))("--config", config, "mcp")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""  # (stdout is the protocol's)
    [(tools, config)] = served
    assert tools == [name for name, op in operations().items() if op.hints.mcp]
    assert config.settings.max_per_site == 5


async def test_the_api_serves_the_mcp_server_at_mcp(harness: Harness) -> None:
    async with client_for(harness) as client:
        response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)

    assert response.status_code == 200
    assert '"name":"udl"' in response.text.replace(" ", "")


async def test_the_mcp_server_at_mcp_needs_the_api_key(tmp_path: Path) -> None:
    harness = Harness(tmp_path, api=APISettings(api_key=SecretStr("s3cret")))
    async with client_for(harness) as client:
        refused = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        allowed = await client.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "X-API-Key": "s3cret"}
        )

    assert refused.status_code == 401
    assert ErrorInfo.model_validate(refused.json()).code is ErrorCode.UNAUTHORIZED
    assert allowed.status_code == 200


async def test_the_header_claude_codes_own_mcp_config_sends_is_accepted(
    tmp_path: Path,
) -> None:
    """The claude-code runner hands its harness a config file that carries
    `Authorization: Bearer <token>`; /mcp reads that as the API key. The two halves meet here, so
    a change to either side fails this test rather than an agent's tools."""
    harness = Harness(tmp_path, api=APISettings(api_key=SecretStr("s3cret")))
    spec = SessionSpec(
        cwd=str(tmp_path),
        mcp=McpEndpoint(url="http://127.0.0.1:8765/mcp", token=SecretStr("s3cret")),
    )
    config = json.loads(_mcp_config(spec))
    sent = config["mcpServers"]["udl"]["headers"]["Authorization"]

    async with client_for(harness) as client:
        response = await client.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Authorization": sent}
        )

    assert response.status_code == 200
    assert '"name":"udl"' in response.text.replace(" ", "")


async def test_the_mcp_server_at_mcp_answers_only_requests_for_localhost(
    harness: Harness,
) -> None:
    async with client_for(harness) as client:
        response = await client.post(
            "/mcp", json=INITIALIZE, headers={**MCP_HEADERS, "Host": "evil.example"}
        )

    assert response.status_code == 421
