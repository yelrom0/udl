"""
---------------
- test_app.py -
---------------

The API as a whole: udl's errors as ErrorInfo with a fitting status, the API key, the Host check
that keeps a page in a browser from reaching a udl on localhost (DNS rebinding), and the docs
"""

# System Imports
import json
from pathlib import Path

# Package Imports
import httpx
import pytest
from pydantic import SecretStr
from starlette.types import Message, Receive, Scope, Send

# Local Imports
from support.api import client_for
from support.interfaces import A, Harness
from udl.config import APISettings, AutomationsSettings
from udl.core.models import DownloadList
from udl.core.operations import operations
from udl.errors import DuplicateKey, ErrorCode, ErrorInfo
from udl.interfaces.api.app import LocalHostsOnly, create_app


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


@pytest.mark.parametrize(
    "content_type",
    ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", None],
)
async def test_a_page_in_a_browser_cannot_start_downloads(
    client: httpx.AsyncClient, content_type: str | None
) -> None:
    # Any page can make a browser POST these to localhost without asking it first (no CORS
    # preflight), but not JSON, which is all the API takes.
    headers = {"Origin": "https://evil.example"}
    if content_type:
        headers["Content-Type"] = content_type
    body = json.dumps({"urls": [A]})

    sent = await client.post("/downloads?wait=true", content=body, headers=headers)
    asked = await client.options(
        "/downloads",
        headers={**headers, "Access-Control-Request-Method": "POST"},
    )
    listed = DownloadList.model_validate((await client.get("/downloads")).json())

    assert sent.status_code == 422
    assert listed.downloads == []
    # Nor may a page read what udl answers, or send JSON after asking.
    for answer in (sent, asked):
        assert "access-control-allow-origin" not in answer.headers


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


async def test_a_name_another_record_has_is_a_conflict(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def taken(*args: object) -> None:
        raise DuplicateKey("A subscription called 'x' is already stored")

    monkeypatch.setattr(harness.db, "list_sites", taken)
    async with client_for(harness) as client:
        response = await client.get("/sites")

    assert response.status_code == 409
    assert ErrorInfo.model_validate(response.json()).code is ErrorCode.DUPLICATE_KEY


async def test_the_host_check_leaves_the_lifespan_alone() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope["type"])

    async def receive() -> Message:
        raise AssertionError

    async def send(message: Message) -> None:
        raise AssertionError

    await LocalHostsOnly(app)({"type": "lifespan"}, receive, send)

    assert seen == ["lifespan"]


@pytest.mark.parametrize("enabled", [True, False])
async def test_udl_serve_runs_the_automations_unless_told_not_to(
    tmp_path: Path, enabled: bool
) -> None:
    harness = Harness(tmp_path, automations=AutomationsSettings(enabled=enabled))
    app = create_app(harness.settings, make_service=harness.service)

    async with app.router.lifespan_context(app):
        running = app.state.service.runtime is not None

    assert running is enabled
    assert app.state.service.runtime is None
