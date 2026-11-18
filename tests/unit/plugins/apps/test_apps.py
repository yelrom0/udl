"""
------------------
- test_apps.py -
------------------

An app plugin's own rules (udl.plugins.apps.base): it is named, its settings are its own model's,
its operations come from its methods (each on the app the service has), and its service is given
an AppContext whose events publish through udl's bus and whose records are udl's records
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.apps import FakeApp, FakeAppSettings, Tick, TickRecord
from support.clock import FakeClock
from support.engine import databases, registry, settings
from support.interfaces import Harness
from udl.automations.bus import NewEvent
from udl.automations.events import EventRecord
from udl.config import Settings
from udl.core.operations import OperationHints, UISpec, resolved_view
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import PluginError
from udl.plugins.apps.base import (
    AnApp,
    AppContext,
    AppEvents,
    AppPlugin,
    AppRecords,
    AppSettings,
)
from udl.plugins.registry import APPS


class Asked(BaseModel):
    """A request with one field."""

    key: str


class Answered(BaseModel):
    """A response with one field."""

    said: str


class Bare(AppPlugin[AppSettings]):
    """An app that only adds an operation."""

    name = "bare"
    title = "Bare"
    summary = "A bare app (for tests)."

    async def speak(self, request: Asked) -> Answered:
        """Say the key back."""
        return Answered(said=request.key)

    @classmethod
    def operations(cls) -> list[Any]:
        return [cls.operation("speak", title="Speak", hints=OperationHints(read_only=True))]


class Stated(Bare):
    """An app whose operation says how it is shown."""

    name = "stated"

    @classmethod
    def operations(cls) -> list[Any]:
        return [
            cls.operation(
                "speak",
                title="Speak",
                hints=OperationHints(read_only=True),
                ui=UISpec(group="Say", view="form"),
            )
        ]


def harness_of(tmp_path: Path, *apps: type[AnApp]) -> Harness:
    """A harness with `apps` (and the fake one) registered as apps."""
    found = Harness(tmp_path)
    for app in (FakeApp, *apps):
        found.registry.register(APPS, app)
    return found


def service_of(tmp_path: Path, clock: FakeClock) -> UDLService:
    """A service on the memory database, with the fake app (for the AppContext tests)."""
    plugins = registry()
    plugins.register(APPS, FakeApp)
    config: Settings = settings(tmp_path)
    return UDLService(
        config,
        registry=plugins,
        databases=databases(config, plugins),
        clock=clock,
    )


# ---------------------------------------------------------------------------------------------
# Its operations
# ---------------------------------------------------------------------------------------------


def test_an_operation_is_made_from_one_of_its_methods() -> None:
    [op] = Bare.operations()

    assert (op.name, op.title) == ("speak", "Speak")
    assert op.summary == "Say the key back."
    assert op.hints.read_only
    assert op.request is Asked
    assert op.response is Answered


def test_an_operation_s_ui_spec_is_kept() -> None:
    [op] = Stated.operations()

    assert op.ui == UISpec(group="Say", view="form")


def test_an_operation_of_a_method_that_is_not_there_is_refused() -> None:
    with pytest.raises(PluginError, match="not one of"):
        Bare.operation("nope", title="Nope")


def test_an_operation_of_a_method_that_is_not_async_is_refused() -> None:
    class Sync(AppPlugin[AppSettings]):
        """An app with a method that is not async."""

        name = "sync"

        def speak(self, request: Asked) -> Answered:
            """Say it (but not awaitable)."""
            return Answered(said=request.key)

    with pytest.raises(PluginError, match="async"):
        Sync.operation("speak", title="Speak")


def test_an_operation_of_a_method_that_takes_the_wrong_thing_is_refused() -> None:
    class Odd(AppPlugin[AppSettings]):
        """An app whose operation method takes something else."""

        name = "odd"

        async def speak(self) -> Answered:
            """Say nothing."""
            return Answered(said="")

    with pytest.raises(PluginError, match="self, request"):
        Odd.operation("speak", title="Speak")


async def test_an_operation_runs_on_the_app_the_service_has(tmp_path: Path) -> None:
    harness = harness_of(tmp_path, Bare)

    async with harness.service() as service:
        answered = cast(Answered, await service.call("bare_speak", {"key": "x"}))

    assert answered.said == "x"


def test_a_service_offers_the_operation_resolved() -> None:
    [op] = Bare.operations()

    assert resolved_view(op.in_namespace("bare", kind=APPS.group)) == "detail"


# ---------------------------------------------------------------------------------------------
# The app's class
# ---------------------------------------------------------------------------------------------


def test_an_app_that_says_nothing_brings_nothing() -> None:
    assert Bare.event_types() == ()
    assert Bare.record_kinds() == ()
    assert FakeApp.event_types()
    assert FakeApp.record_kinds()


def test_an_app_s_name_is_what_its_things_are_called_after() -> None:
    [event_type] = FakeApp.event_types()

    assert event_type.name == "fakeapp.tick"
    assert event_type.example is not None


def test_an_app_s_settings_are_its_own_model_s() -> None:
    assert issubclass(FakeApp.settings_model, AppSettings)
    assert FakeApp(FakeAppSettings(), clock=FakeClock()).settings.every == timedelta(seconds=1)


def test_an_app_s_default_service_does_nothing() -> None:
    app = Bare(AppSettings(), clock=FakeClock())

    asyncio.run(app.serve(_context(app)))


def _context(app: AnApp) -> AppContext:
    return AppContext(
        app=app,
        service=None,  # pyright: ignore[reportArgumentType]
        records=None,  # pyright: ignore[reportArgumentType]
        events=None,  # pyright: ignore[reportArgumentType]
        secrets=None,  # pyright: ignore[reportArgumentType]
        clock=FakeClock(),
        settings=app.settings,
        stop=asyncio.Event(),
    )


# ---------------------------------------------------------------------------------------------
# Its events
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    async with service_of(tmp_path, FakeClock()) as found:
        yield found


async def test_an_app_may_not_publish_another_plugin_s_event(service: UDLService) -> None:
    events = AppEvents(service.bus, FakeApp.name)

    with pytest.raises(PluginError, match="another plugin's"):
        await events.emit("gmail.message.received", {}, dedupe_key="1")


async def test_an_app_s_events_are_stored_with_its_name(service: UDLService) -> None:
    events = AppEvents(service.bus, FakeApp.name)

    stored = await events.emit("tick", Tick(at=FakeClock().now(), count=1), dedupe_key="1")
    again = await events.emit("tick", Tick(at=FakeClock().now(), count=1), dedupe_key="1")

    assert stored
    assert not again  # (the same dedupe key is not stored twice)
    [record] = await service.db.list_records(
        EventRecord, RecordQuery(where={"type": "fakeapp.tick"})
    )
    assert record.type == "fakeapp.tick"
    assert record.source == FakeApp.name


async def test_what_an_app_waits_for_is_the_next_event_that_matches(service: UDLService) -> None:
    events = AppEvents(service.bus, FakeApp.name)
    waiter = asyncio.ensure_future(events.wait_for("fakeapp.*", stop=asyncio.Event()))
    await _listening()

    await service.bus.publish(
        NewEvent(
            type="fakeapp.tick", data=Tick(at=FakeClock().now(), count=2).model_dump(mode="json")
        )
    )
    found = await asyncio.wait_for(waiter, 1)

    assert found is not None
    assert found.type == "fakeapp.tick"
    assert found.data["count"] == 2


async def test_waiting_stops_when_the_app_is_told_to(service: UDLService) -> None:
    stop = asyncio.Event()
    events = AppEvents(service.bus, FakeApp.name)
    waiter = asyncio.ensure_future(events.wait_for("fakeapp.*", stop=stop))
    await _listening()
    stop.set()

    assert await asyncio.wait_for(waiter, 1) is None


async def test_an_event_an_app_waits_for_is_told_of_once(service: UDLService) -> None:
    # (The bus tells of an event again when it is dispatched: that is not it happening.)
    events = AppEvents(service.bus, FakeApp.name)
    waiter = asyncio.ensure_future(events.wait_for("fakeapp.*", stop=asyncio.Event()))
    await _listening()
    await service.bus.publish(
        NewEvent(
            type="fakeapp.tick", data=Tick(at=FakeClock().now(), count=1).model_dump(mode="json")
        )
    )

    first = await asyncio.wait_for(waiter, 1)

    assert first is not None
    assert first.type == "fakeapp.tick"


async def _listening() -> None:
    """Let an app's wait_for get as far as listening on the bus."""
    for _ in range(3):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------------------------
# Its records
# ---------------------------------------------------------------------------------------------


async def test_an_app_s_records_are_udl_s_records(service: UDLService) -> None:
    records = AppRecords(service.db)
    kept = await records.save(TickRecord.example())

    found = await records.get(TickRecord, kept.id)
    listed = await records.list(TickRecord, RecordQuery())

    assert found == kept
    assert listed == [kept]
    assert await records.delete(TickRecord, kept.id)
    assert await records.get(TickRecord, kept.id) is None
