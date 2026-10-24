"""
-------------------
- test_runtime.py -
-------------------

Two `udl serve`s on one SQLite file (each an API app with its lifespan, reached through httpx's
ASGITransport), with the runtime's real clock on short intervals: one runs the automations and
the other says which; the other takes over when the first stops renewing its lease, or at once
when it closes; and an event a CLI-style udl publishes is dispatched and run within
dispatch_interval
"""

# System Imports
import asyncio
import contextlib
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import timedelta
from pathlib import Path

# Package Imports
import httpx
import pytest
from fastapi import FastAPI

# Local Imports
from support.actions import fake_action
from support.api import BASE_URL
from support.engine import registry
from support.tasks import held
from udl.automations.bus import NewEvent
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.runtime import AutomationRuntime, AutomationState, runtime_status
from udl.automations.subscriptions import Subscription
from udl.config import (
    AutomationsSettings,
    DBConnectionConfig,
    DBSettings,
    SecretsSettings,
    Settings,
)
from udl.core.jobs import InstanceLock
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.interfaces.api.app import create_app
from udl.plugins.actions.base import Action
from udl.plugins.databases.sqlite import SQLitePlugin
from udl.plugins.registry import ACTIONS, DATABASES
from udl.plugins.secrets.memory import MemorySecretStore

FAKE = fake_action("fake")
TTL = timedelta(seconds=0.6)


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        download_dir=tmp_path / "downloads",
        db=DBSettings(
            connections=[
                DBConnectionConfig(
                    name="local",
                    plugin="sqlite",
                    url=f"sqlite+aiosqlite:///{tmp_path / 'udl.db'}",
                    primary=True,
                )
            ]
        ),
        secrets=SecretsSettings(store="memory"),
        automations=AutomationsSettings(
            scripts_dir=tmp_path / "scripts",
            lease_ttl=TTL,
            dispatch_interval=timedelta(seconds=0.1),
        ),
    )


def make_service(tmp_path: Path) -> Callable[[Settings], UDLService]:
    def make(settings: Settings) -> UDLService:
        plugins = registry()
        plugins.register(DATABASES, SQLitePlugin)
        plugins.register(ACTIONS, FAKE)
        return UDLService(
            settings,
            registry=plugins,
            secrets=MemorySecretStore(settings.secrets),
            lock=InstanceLock(tmp_path / "udl.lock"),
        )

    return make


@contextlib.asynccontextmanager
async def serving(tmp_path: Path) -> AsyncGenerator[FastAPI]:
    """A `udl serve` (its app, with its lifespan running), and a request to it answered."""
    app = create_app(settings_for(tmp_path), make_service=make_service(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=BASE_URL) as client,
    ):
        assert (await client.get("/config")).status_code == 200
        yield app


def service_of(app: FastAPI) -> UDLService:
    found: UDLService = app.state.service
    return found


def runtime_of(app: FastAPI) -> AutomationRuntime:
    found = service_of(app).runtime
    assert found is not None
    return found


async def eventually(check: Callable[[], Awaitable[bool]], within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not await check():
        if time.monotonic() > deadline:
            raise AssertionError("it never happened")
        await asyncio.sleep(0.02)


async def state_of(app: FastAPI) -> AutomationState:
    return (await runtime_status(service_of(app))).automations


async def test_one_udl_runs_the_automations_and_the_other_says_which(tmp_path: Path) -> None:
    async with serving(tmp_path) as first:
        await eventually(lambda: _is(first, AutomationState.RUNNING))
        async with serving(tmp_path) as second:
            await asyncio.sleep(TTL.total_seconds())

            status = await runtime_status(service_of(second))
            assert status.automations is AutomationState.ELSEWHERE
            assert status.holder == runtime_of(first).holder
            assert await state_of(first) is AutomationState.RUNNING


async def _is(app: FastAPI, state: AutomationState) -> bool:
    return await state_of(app) is state


async def test_the_other_takes_over_when_the_first_stops_renewing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serving(tmp_path) as first:
        await eventually(lambda: _is(first, AutomationState.RUNNING))
        async with serving(tmp_path) as second:

            async def stuck(ttl: timedelta) -> bool:
                await asyncio.Event().wait()  # (as a udl that hangs: it never renews)
                return False

            monkeypatch.setattr(runtime_of(first), "_claim", stuck)
            monkeypatch.setattr("udl.automations.runtime.STOP_GRACE", 0.1)
            started = time.monotonic()

            await eventually(lambda: _is(second, AutomationState.RUNNING))

            assert time.monotonic() - started < 3 * TTL.total_seconds()


async def test_the_other_takes_over_at_once_when_the_first_closes(tmp_path: Path) -> None:
    # (Each in a task of its own, so the first can close while the second goes on.)
    first_one = contextlib.AsyncExitStack()
    first = await first_one.enter_async_context(held(serving(tmp_path)))
    await eventually(lambda: _is(first, AutomationState.RUNNING))
    async with held(serving(tmp_path)) as second:
        await first_one.aclose()
        started = time.monotonic()
        await eventually(lambda: _is(second, AutomationState.RUNNING))

        # (Renewing every TTL/3, it finds the lease released rather than waiting for it to
        # expire.)
        assert time.monotonic() - started < TTL.total_seconds()


async def test_an_event_a_cli_publishes_is_dispatched_and_run(tmp_path: Path) -> None:
    async with serving(tmp_path) as server:
        await eventually(lambda: _is(server, AutomationState.RUNNING))
        db = service_of(server).db
        await db.upsert_record(Action(name="hello", plugin="fake", options={"output": "hi"}))
        await db.upsert_record(
            Subscription(name="greet", events=["custom.hello"], actions=["hello"])
        )

        async with make_service(tmp_path)(settings_for(tmp_path)) as cli:
            await cli.bus.publish(NewEvent(type="custom.hello", source="cli"))
        started = time.monotonic()

        async def ran() -> bool:
            found = await db.list_records(ActionRun, RecordQuery())
            return bool(found) and found[0].status is RunStatus.SUCCEEDED

        await eventually(ran)

        assert time.monotonic() - started < 1.0  # (dispatch_interval is 0.1s)
