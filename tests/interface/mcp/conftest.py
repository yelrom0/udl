"""
---------------
- conftest.py -
---------------

The MCP server's tools are called through the SDK's client, in-process (support.mcp)
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest
from mcp import Client

# Local Imports
from support.interfaces import Harness
from support.mcp import client_for
from support.tasks import held
from udl.config import APISettings


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path, api=APISettings(allowed_output_roots=[tmp_path]))


@pytest.fixture
async def client(harness: Harness) -> AsyncIterator[Client]:
    async with held(client_for(harness)) as client:
        yield client
