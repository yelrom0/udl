"""
----------
- mcp.py -
----------

Calls the MCP server's tools in the tests: through the SDK's client, connected in-process, on a
started service on a Harness's fakes
"""

# System Imports
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

# Package Imports
from mcp import Client

# Local Imports
from support.interfaces import Harness
from udl.interfaces.mcp.server import create_server


@asynccontextmanager
async def client_for(harness: Harness) -> AsyncGenerator[Client]:
    async with (
        harness.service() as service,
        Client(create_server(lambda: service, service.operations)) as client,
    ):
        yield client
