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
    AppEvent,
    AppEvents,
    AppPlugin,
    AppRecords,
    AppSettings,
    _ended,  # pyright: ignore[reportPrivateUsage]  (its own branch, reached only here)
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


def test_an_operation_whose_models_are_only_for_type_checkers_is_refused() -> None:
    # (A model imported under TYPE_CHECKING is a string at run time, and no adapter can be made
    # from it: an app's models have to be imported for real, as udl's own operations' are.)
    class Later(AppPlugin[AppSettings]):
        """An app whose request model is only imported for type checkers."""

        name = "later"

        async def speak(self, request: BaseModel) -> Answered:
            """Say the key back."""
            return Answered(said=cast(Asked, request).key)

        # (A model imported under TYPE_CHECKING is only a string at run time.)
        speak.__annotations__ = {"request": "Asked", "return": "Answered"}  # noqa: RUF012

    with pytest.raises(PluginError, match="not a model udl can read"):
        Later.operation("speak", title="Speak")


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


def events_of(service: UDLService) -> AppEvents:
    """An app's events, knowing the types it declared."""
    return AppEvents(service.bus, FakeApp.name, FakeApp.event_types())


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
    events = events_of(service)

    with pytest.raises(PluginError, match="another plugin's"):
        await events.emit("gmail.message.received", {}, dedupe_key="1")


async def test_an_app_s_events_are_stored_with_its_name(service: UDLService) -> None:
    events = events_of(service)

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
    events = events_of(service)
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
    events = events_of(service)
    waiter = asyncio.ensure_future(events.wait_for("fakeapp.*", stop=stop))
    await _listening()
    stop.set()

    assert await asyncio.wait_for(waiter, 1) is None


async def test_an_app_may_not_publish_a_type_it_never_declared(service: UDLService) -> None:
    events = events_of(service)

    with pytest.raises(PluginError, match="has no event type"):
        await events.emit("nonesuch", {}, dedupe_key="1")


async def test_a_subscription_lives_across_waits(service: UDLService) -> None:
    # (An app that handles one event at a time holds a subscription: nothing that happens while
    # it works is lost, and it is told of each event once.)
    events = events_of(service)

    async with events.subscribe("fakeapp.*") as subscription:
        await _listening()

        made = 0

        async def published(how_many: int) -> None:
            nonlocal made
            for _ in range(how_many):
                await service.bus.publish(
                    NewEvent(
                        type="fakeapp.tick",
                        data=Tick(at=FakeClock().now(), count=made).model_dump(mode="json"),
                        dedupe_key=f"tick-{made}",
                    )
                )
                made += 1

        await published(2)
        first = await asyncio.wait_for(subscription.__anext__(), 1)
        second = await asyncio.wait_for(subscription.__anext__(), 1)
        await published(1)  # (while the app "works": the subscription is still listening)
        third = await asyncio.wait_for(subscription.__anext__(), 1)

    assert [found.data["count"] for found in (first, second, third)] == [0, 1, 2]


async def test_a_subscription_survives_a_wait_that_gave_up(service: UDLService) -> None:
    # (An app that stops waiting for one event — wait_for timing out — keeps its subscription.)
    events = events_of(service)

    async with events.subscribe("fakeapp.*") as subscription:
        await _listening()

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(subscription.__anext__(), 0.05)

        await service.bus.publish(
            NewEvent(
                type="fakeapp.tick",
                data=Tick(at=FakeClock().now(), count=1).model_dump(mode="json"),
                dedupe_key="tick-1",
            )
        )

        found = await asyncio.wait_for(subscription.__anext__(), 1)

        assert found.data["count"] == 1


async def test_waiting_for_an_event_that_never_comes_times_out(service: UDLService) -> None:
    # (An app that is told to stop while it waits is not waiting any more: None, not an error.)
    events = events_of(service)
    stop = asyncio.Event()

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(events.wait_for("fakeapp.*", stop=stop), 0.05)

    stop.set()
    assert await asyncio.wait_for(events.wait_for("fakeapp.*", stop=stop), 1) is None


async def test_a_wait_whose_subscription_ended_is_none() -> None:
    # (The bus's notices only end when the listener is cancelled: nothing is coming, and an app
    # that waits is told so rather than being cancelled.)
    ended: asyncio.Future[AppEvent] = asyncio.get_running_loop().create_future()
    ended.set_exception(StopAsyncIteration())

    assert await _ended(ended) is None


async def test_an_event_an_app_waits_for_is_told_of_once(service: UDLService) -> None:
    # (The bus tells of an event again when it is dispatched: that is not it happening.)
    events = events_of(service)
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

    # (A second wait is not told of the same event again: it was stored, not dispatched.)
    with pytest.raises(TimeoutError):
        # (It was stored, not dispatched: nothing tells of it again.)
        await asyncio.wait_for(events.wait_for("fakeapp.*", stop=asyncio.Event()), 0.1)


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
