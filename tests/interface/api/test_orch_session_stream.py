"""
-------------------------------
- test_orch_session_stream.py -
-------------------------------

`GET /orch/sessions/{session}/stream` (27.9): one session's own live events, as they happen, and
only those — another session's events are not on it, and neither is an agent's limit (that is
`orch.agent.limited`, the agent's own type, not the session's).
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
from support.api import BASE_URL
from support.interfaces import Harness
from support.runners import SharedRunner
from support.tasks import held
from udl.automations.bus import NewEvent
from udl.config import APISettings, AutomationsSettings
from udl.core.service import UDLService
from udl.interfaces.api.app import create_app
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import AgentSession, SessionRecords
from udl.plugins.registry import APPS, RUNNERS
from udl.plugins.runners.base import SessionSpec


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(
        tmp_path,
        api=APISettings(allowed_output_roots=[tmp_path / "out"]),
        automations=AutomationsSettings(enabled=False, scripts_dir=tmp_path / "scripts"),
    )
    found.registry.register(APPS, Orch)
    found.registry.register(RUNNERS, SharedRunner)
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


async def two_sessions(service: UDLService, root: Path) -> tuple[AgentSession, AgentSession]:
    """Two sessions of one agent, started on the harness (the stream is of one of them)."""
    records = AppRecords(service.db)
    found = await service.apps.get("orch")
    assert isinstance(found, Orch)
    runner = await found.harnesses.runner("local", "fake")
    sessions = SessionRecords(records)
    first = await sessions.save_session(
        AgentSession(agent="boss", ref=await runner.start(SessionSpec(cwd=str(root))))
    )
    second = await sessions.save_session(
        AgentSession(agent="boss", ref=await runner.start(SessionSpec(cwd=str(root))))
    )
    return first, second


async def stream(app: FastAPI, path: str, count: int, then: Any) -> list[dict[str, Any]]:
    """The first `count` server-sent events `path` sends, while `then()` (an awaitable) runs;
    then the client goes away (tests/interface/api/test_automations.py's own)."""
    body: list[bytes] = []
    events: list[dict[str, Any]] = []
    gone = asyncio.Event()
    started = asyncio.Event()

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
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
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


async def test_the_stream_is_one_sessions_own_events(app: FastAPI, harness: Harness) -> None:
    # (The app's own service, whose bus the stream is listening to.)
    service: UDLService = app.state.service
    mine, other = await two_sessions(service, harness.tmp_path)
    bus = service.bus

    async def happen() -> None:
        await bus.publish(
            NewEvent(
                type="orch.session.event",
                data={"agent": "boss", "session": str(other.id), "kind": "text_delta"},
            )
        )
        await bus.publish(
            NewEvent(
                type="orch.agent.limited",
                data={"agent": "boss", "session": str(mine.id)},
            )
        )
        await bus.publish(
            NewEvent(
                type="orch.session.event",
                data={"agent": "boss", "session": str(mine.id), "kind": "text_delta"},
            )
        )
        await bus.dispatch_pending()

    heard = await stream(app, f"/orch/sessions/{mine.id}/stream", 1, happen)
    # (The other session's event and the agent's limit are not on it: one session, its own type.)
    assert [(found["event"], found["data"]["data"]["session"]) for found in heard] == [
        ("event", str(mine.id))
    ]
    assert heard[0]["data"]["data"]["kind"] == "text_delta"
