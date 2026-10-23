"""
---------------
- conftest.py -
---------------

The API's tests send requests to the app in-process, on a Harness's fakes (support.api)
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import httpx
import pytest

# Local Imports
from support.api import client_for
from support.interfaces import Harness
from support.tasks import held
from udl.config import APISettings


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path, api=APISettings(allowed_output_roots=[tmp_path]))


@pytest.fixture
async def client(harness: Harness) -> AsyncIterator[httpx.AsyncClient]:
    async with held(client_for(harness)) as client:
        yield client
