"""
---------------
- test_app.py -
---------------

The API as a whole: udl's errors as ErrorInfo with a fitting status, the API key, the Host check
that keeps a page in a browser from reaching a udl on localhost (DNS rebinding), and the docs
"""

# System Imports
from pathlib import Path

# Package Imports
import httpx
import pytest
from pydantic import SecretStr

# Local Imports
from support.api import client_for
from support.interfaces import Harness
from udl.config import APISettings
from udl.core.service import operations
from udl.errors import ErrorCode, ErrorInfo


def keyed(tmp_path: Path, key: str = "s3cret") -> Harness:
    return Harness(tmp_path, api=APISettings(api_key=SecretStr(key)))


async def test_with_an_api_key_a_request_without_it_is_unauthorized(tmp_path: Path) -> None:
    async with client_for(keyed(tmp_path)) as client:
        missing = await client.get("/sites")
        wrong = await client.get("/sites", headers={"X-API-Key": "nope"})
        right = await client.get("/sites", headers={"X-API-Key": "s3cret"})
        events = await client.get(f"/downloads/{'0' * 32}/events")

    for refused in (missing, wrong, events):
        assert refused.status_code == 401
        assert ErrorInfo.model_validate(refused.json()).code is ErrorCode.UNAUTHORIZED
    assert right.status_code == 200


async def test_the_docs_need_no_api_key(tmp_path: Path) -> None:
    async with client_for(keyed(tmp_path)) as client:
        docs = await client.get("/docs")
        schema = await client.get("/openapi.json")

    assert (docs.status_code, schema.status_code) == (200, 200)
    assert "X-API-Key" in schema.text


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765", "localhost"])
async def test_on_localhost_requests_for_localhost_are_answered(
    client: httpx.AsyncClient, host: str
) -> None:
    response = await client.get("/sites", headers={"Host": host})

    assert response.status_code == 200


@pytest.mark.parametrize("host", ["evil.example:8765", "127.0.0.1.evil.example", ""])
async def test_on_localhost_requests_for_another_host_are_refused(
    client: httpx.AsyncClient, host: str
) -> None:
    # (A page that rebinds its own name to 127.0.0.1 sends its name as the Host.)
    response = await client.get("/sites", headers={"Host": host})

    assert response.status_code == 421
    assert ErrorInfo.model_validate(response.json()).code is ErrorCode.NOT_ALLOWED


async def test_served_beyond_localhost_any_host_is_answered(tmp_path: Path) -> None:
    async with client_for(Harness(tmp_path, api=APISettings(host="0.0.0.0"))) as client:  # noqa: S104
        response = await client.get("/sites", headers={"Host": "nas.lan:8765"})

    assert response.status_code == 200


async def test_the_docs_describe_every_operation(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    documented = {
        details["operationId"]: details
        for path in schema["paths"].values()
        for details in path.values()
    }
    for name, op in operations().items():
        assert documented[name]["summary"] == op.summary
        assert documented[name]["description"] == op.description
    download = schema["components"]["schemas"]["DownloadSpec"]["properties"]
    assert all(field.get("description") for field in download.values())


async def test_a_bug_is_a_server_error_without_its_details(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken(*args: object) -> None:
        raise RuntimeError("a secret detail")

    monkeypatch.setattr(harness.db, "list_sites", broken)
    async with client_for(harness, raise_app_exceptions=False) as client:
        response = await client.get("/sites")

    assert response.status_code == 500
    assert ErrorInfo.model_validate(response.json()).code is ErrorCode.UDL_ERROR
    assert "a secret detail" not in response.text
