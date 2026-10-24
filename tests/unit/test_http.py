"""
----------------
- test_http.py -
----------------

udl's client of a service's API (what integrations are given by default): it says it is udl,
and httpx's errors are the UDLErrors they mean, from the integration
"""

# Package Imports
import httpx
import pytest
import respx

# Local Imports
from udl.errors import NetworkError
from udl.http import api_client

API = "https://api.example.com/v1/things"


@respx.mock
async def test_the_api_client_says_it_is_udl() -> None:
    route = respx.get(API).mock(return_value=httpx.Response(200, json=[]))

    async with api_client(tool="things") as client:
        await client.get(API)

    assert route.calls.last.request.headers["user-agent"].startswith("udl/")


@respx.mock
async def test_the_api_client_s_errors_are_udl_s() -> None:
    respx.get(API).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(NetworkError) as raised:
        async with api_client(tool="things") as client:
            await client.get(API)

    assert raised.value.tool == "things"
