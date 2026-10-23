"""
----------
- api.py -
----------

Sends requests to the API in the tests: in-process (httpx's ASGITransport), with the app's
lifespan (which starts and closes its service) running around them, on a Harness's fakes
"""

# System Imports
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

# Package Imports
import httpx

# Local Imports
from support.interfaces import Harness
from udl.interfaces.api.app import create_app

# udl serve's default address: a request to it says so in its Host header.
BASE_URL = "http://127.0.0.1:8765"


@asynccontextmanager
async def client_for(
    harness: Harness, *, raise_app_exceptions: bool = True
) -> AsyncGenerator[httpx.AsyncClient]:
    """A client of the API on `harness`. A bug in the app is raised in the test, unless
    `raise_app_exceptions` is false (then it is answered, as a server answers it)."""
    app = create_app(harness.settings, make_service=harness.service)
    transport = httpx.ASGITransport(app, raise_app_exceptions=raise_app_exceptions)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client,
    ):
        yield client
