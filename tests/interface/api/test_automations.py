"""
-----------------------
- test_automations.py -
-----------------------

The automations' routes: /event-types, /events, /subscriptions, /actions, /runs and /status
(made from their operations), with udl's errors as their statuses, and /events/stream: what
happens, as it happens (server-sent events), in order, filtered by type, saying when a client
lagged
"""

# System Imports
import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message

# Local Imports
from support.actions import fake_action
from support.api import BASE_URL
from support.interfaces import Harness
from support.tasks import held
from udl.automations.bus import NewEvent
from udl.config import APISettings, AutomationsSettings
from udl.core.service import UDLService
from udl.errors import ErrorCode
from udl.interfaces.api.app import create_app
from udl.plugins.actions.webhook import WebhookAction
from udl.plugins.registry import ACTIONS

FAKE = fake_action("fake")


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(
        tmp_path,
        api=APISettings(allowed_output_roots=[tmp_path / "out"]),
        # (Not running the automations: these tests look at what is queued.)
        automations=AutomationsSettings(enabled=False, scripts_dir=tmp_path / "scripts"),
    )
    found.registry.register(ACTIONS, FAKE)
    found.registry.register(ACTIONS, WebhookAction)
    return found


@pytest.fixture
async def app(harness: Harness) -> AsyncIterator[FastAPI]:
    made = create_app(harness.settings, make_service=harness.service)
    async with held(made.router.lifespan_context(made)):
        yield made


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=BASE_URL) as found:
        yield found


async def setup(client: httpx.AsyncClient) -> None:
    """An action, and a subscription that runs it on custom.hello."""
    put = await client.put("/actions/hello", json={"plugin": "fake", "options": {"output": 1}})
    assert put.status_code == 200, put.text
    put = await client.put(
        "/subscriptions/greet",
        json={"events": ["custom.hello"], "actions": ["hello"], "filter": 'name == "x"'},
    )
    assert put.status_code == 200, put.text


# ---------------------------------------------------------------------------------------------
# The routes
# ---------------------------------------------------------------------------------------------


async def test_the_event_types_are_listed(client: httpx.AsyncClient) -> None:
    response = await client.get("/event-types", params={"match": "udl.action.*"})

    assert response.status_code == 200
    assert {found["name"] for found in response.json()["types"]} == {
        "udl.action.succeeded",
        "udl.action.failed",
        "udl.action.awaiting_approval",
    }


async def test_an_event_is_emitted_listed_shown_and_replayed(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await setup(client)

    emitted = await client.post("/events", json={"type": "custom.hello", "data": {"name": "x"}})
    event = emitted.json()
    service: UDLService = app.state.service
    await service.bus.dispatch_pending()
    listed = await client.get("/events", params={"type": "custom.*"})
    shown = await client.get(f"/events/{event['id']}")
    replayed = await client.post(f"/events/{event['id']}/replay", json={})

    assert emitted.status_code == 201
    assert (event["type"], event["source"], event["status"]) == ("custom.hello", "api", "pending")
    assert [found["id"] for found in listed.json()["events"]] == [event["id"]]
    assert shown.json()["event"]["status"] == "dispatched"
    assert [run["action"] for run in shown.json()["runs"]] == ["hello"]
    assert [run["subscription"] for run in replayed.json()["runs"]] == ["greet"]


async def test_only_custom_events_are_emitted(client: httpx.AsyncClient) -> None:
    response = await client.post("/events", json={"type": "udl.download.finished"})

    assert response.status_code == 422


async def test_an_unknown_event_is_a_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/events/00000000-0000-4000-8000-000000000009")

    assert response.status_code == 404
    assert response.json()["code"] == ErrorCode.DOES_NOT_EXIST


async def test_a_subscription_is_saved_shown_listed_tested_and_deleted(
    client: httpx.AsyncClient,
) -> None:
    await setup(client)
    await client.post("/events", json={"type": "custom.hello", "data": {"name": "x"}})

    shown = await client.get("/subscriptions/greet")
    listed = await client.get("/subscriptions", params={"enabled": "true"})
    tested = await client.post("/subscriptions/test", json={"name": "greet"})
    deleted = await client.delete("/subscriptions/greet")
    gone = await client.get("/subscriptions/greet")

    assert shown.json()["filter_text"] == 'name == "x"'
    assert shown.json()["filter"] == {
        "field": "name",
        "op": "eq",
        "value": "x",
        "case_sensitive": True,
    }
    assert [found["name"] for found in listed.json()["subscriptions"]] == ["greet"]
    assert [found["matched"] for found in tested.json()["events"]] == [True]
    assert deleted.json() == {"deleted": True}
    assert gone.status_code == 404


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"events": ["custom.x"], "actions": ["nope"]}, 400, ErrorCode.CONFIG_ERROR),
        (
            {"events": ["udl.download.finished"], "actions": ["hello"], "filter": "nope"},
            400,
            ErrorCode.INVALID_FILTER,
        ),
        ({"events": ["custom.x"], "actions": ["hello"], "filter": "a =="}, 422, None),
    ],
)
async def test_a_subscription_that_cannot_be_is_refused(
    client: httpx.AsyncClient, body: dict[str, Any], status: int, code: ErrorCode | None
) -> None:
    await setup(client)

    response = await client.put("/subscriptions/bad", json=body)

    assert response.status_code == status
    if code is not None:
        assert response.json()["code"] == code


async def test_a_schedule_s_subscription_is_a_409(client: httpx.AsyncClient) -> None:
    await setup(client)

    response = await client.put(
        "/subscriptions/schedule:x", json={"events": ["custom.x"], "actions": ["hello"]}
    )

    assert response.status_code == 409
    assert response.json()["code"] == ErrorCode.ALREADY_EXISTS


async def test_an_action_is_saved_shown_listed_run_and_deleted(client: httpx.AsyncClient) -> None:
    saved = await client.put(
        "/actions/hello", json={"plugin": "fake", "options": {"output": "hi"}, "retries": 2}
    )
    shown = await client.get("/actions/hello")
    listed = await client.get("/actions", params={"plugin": "fake"})
    scripts = await client.get("/actions/scripts")
    ran = await client.post("/actions/hello/run", json={})
    dry = await client.post("/actions/hello/run", json={"dry_run": True})
    deleted = await client.delete("/actions/hello")

    assert saved.status_code == 200, saved.text
    assert (shown.json()["retries"], shown.json()["timeout"]) == (2, "5m")
    assert [found["name"] for found in listed.json()["actions"]] == ["hello"]
    assert scripts.json()["exists"] is False
    assert (ran.json()["run"]["status"], ran.json()["run"]["output"]) == ("succeeded", "hi")
    assert (dry.json()["run"], dry.json()["options"]["output"]) == (None, "hi")
    assert deleted.json() == {"deleted": True}


async def test_an_action_a_subscription_runs_is_a_409_to_delete(client: httpx.AsyncClient) -> None:
    await setup(client)

    response = await client.delete("/actions/hello")

    assert response.status_code == 409
    assert response.json()["code"] == ErrorCode.IN_USE


async def test_an_action_called_scripts_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.put("/actions/scripts", json={"plugin": "fake"})

    assert response.status_code == 422


async def test_an_action_s_secrets_are_hidden(client: httpx.AsyncClient) -> None:
    saved = await client.put(
        "/actions/hook",
        json={
            "plugin": "webhook",
            "options": {"url": "https://hooks.example.com/", "secret_headers": {"X": "s3cret"}},
        },
    )

    assert saved.status_code == 200, saved.text
    assert "s3cret" not in saved.text
    assert saved.json()["options"]["secret_headers"]["X"]["secret"].startswith("actions/")


async def test_runs_are_listed_shown_and_cancelled(client: httpx.AsyncClient, app: FastAPI) -> None:
    await setup(client)
    await client.post("/events", json={"type": "custom.hello", "data": {"name": "x"}})
    service: UDLService = app.state.service
    await service.bus.dispatch_pending()

    listed = await client.get("/runs", params={"status": "queued"})
    [run] = listed.json()["runs"]
    shown = await client.get(f"/runs/{run['id']}")
    cancelled = await client.delete(f"/runs/{run['id']}")

    assert (shown.json()["action"], shown.json()["status"]) == ("hello", "queued")
    assert cancelled.json()["status"] == "cancelled"


async def test_the_status_is_given(client: httpx.AsyncClient) -> None:
    response = await client.get("/status")

    assert response.json()["automations"] == "disabled"


# ---------------------------------------------------------------------------------------------
# The live stream
# ---------------------------------------------------------------------------------------------


async def stream(app: FastAPI, path: str, count: int, then: Any) -> list[dict[str, Any]]:
    """The first `count` server-sent events `path` sends, while `then()` (an awaitable) runs;
    then the client goes away."""
    body: list[bytes] = []
    events: list[dict[str, Any]] = []
    gone = asyncio.Event()
    started = asyncio.Event()
    query = path.partition("?")[2]

    async def receive() -> Message:
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            started.set()
        elif message["type"] == "http.response.body":
            body.append(message.get("body", b""))
            events[:] = _sse(b"".join(body).decode())
            if len(events) >= count:
                gone.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path.partition("?")[0],
        "raw_path": path.partition("?")[0].encode(),
        "query_string": query.encode(),
        "headers": [(b"host", b"127.0.0.1:8765")],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8765),
    }
    serving = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.sleep(0.05)  # (the stream listens)
    await then()
    await asyncio.wait_for(gone.wait(), 2)
    await asyncio.wait_for(serving, 2)
    return events[:count]


def _sse(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for block in text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":")
        )
        if fields:
            found.append(
                {"event": fields.get("event"), "data": json.loads(fields.get("data", "null"))}
            )
    return found


async def test_the_stream_sends_events_and_runs_in_order(
    client: httpx.AsyncClient, app: FastAPI
) -> None:
    await setup(client)
    service: UDLService = app.state.service

    async def happen() -> None:
        await service.bus.publish(NewEvent(type="custom.hello", data={"name": "x"}))
        await service.bus.dispatch_pending()

    heard = await stream(app, "/events/stream", 3, happen)

    assert [(found["event"], found["data"]["status"]) for found in heard] == [
        ("event", "pending"),
        ("run", "queued"),
        ("event", "dispatched"),
    ]
    assert heard[1]["data"]["action"] == "hello"


async def test_the_stream_sends_only_the_types_asked_for(app: FastAPI) -> None:
    service: UDLService = app.state.service

    async def happen() -> None:
        await service.bus.publish(NewEvent(type="custom.hello"))
        await service.bus.publish(NewEvent(type="custom.bye"))

    heard = await stream(app, "/events/stream?types=custom.bye&runs=false", 1, happen)

    assert [found["data"]["type"] for found in heard] == ["custom.bye"]


async def test_a_client_that_falls_behind_hears_that_it_lagged(app: FastAPI) -> None:
    service: UDLService = app.state.service

    async def flood() -> None:
        for n in range(700):
            await service.bus.publish(NewEvent(type="custom.hello", dedupe_key=str(n)))

    heard = await stream(app, "/events/stream", 2, flood)

    assert heard[0] == {"event": "lagged", "data": {"lagged": True}}
    assert heard[1]["data"]["dedupe_key"] == "200"  # (the oldest 200 were lost)


async def test_the_stream_is_documented(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()

    route = schema["paths"]["/events/stream"]["get"]
    assert route["operationId"] == "events_stream"
    assert {parameter["name"] for parameter in route["parameters"]} == {"types", "runs"}
