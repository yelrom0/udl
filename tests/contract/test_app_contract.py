"""
------------------------
- test_app_contract.py -
----------------------

The behaviour every app plugin (udl.apps) must have, run over each of them (each a Case: the
plugin class, an app made from it, and what its service publishes and keeps): it is named,
titled and described; its settings are its own model's, layered as every plugin's are; its
operations are operations (described, with models for their request and response) and a service
offers them named after it; its event types are its own, with valid examples; its record kinds
are records whose examples round-trip; and its service runs until it is told to stop, and stops.
"""

# System Imports
import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, cast

# Package Imports
import pytest
from pydantic import BaseModel

# Local Imports
from support.apps import FakeApp, FakeAppSettings, Tick
from support.clock import FakeClock
from udl.automations.events import EventTypes
from udl.core.operations import Operation, OperationRegistry, resolved_view
from udl.db.records import Record, record_kind
from udl.errors import PluginError
from udl.plugins.apps.base import AnApp, AppContext, AppEvents, AppPlugin, AppRecords, AppSettings
from udl.plugins.registry import APPS, PluginRegistry


class _Events(AppEvents):
    """An app's events, keeping what it publishes (nothing here dispatches them)."""

    def __init__(self, name: str) -> None:
        super().__init__(bus=None, name=name)  # pyright: ignore[reportArgumentType]  (a bus is only needed to wait)
        self.published: list[tuple[str, BaseModel]] = []

    async def emit(
        self,
        event_type: str,
        data: BaseModel | dict[str, Any],
        *,
        dedupe_key: str,
        occurred_at: Any = None,
    ) -> bool:
        self.published.append((event_type, data if isinstance(data, BaseModel) else Tick(**data)))
        return True


class _Records(AppRecords):
    """An app's records, kept in a list (the record contract runs over them elsewhere)."""

    def __init__(self) -> None:
        super().__init__(db=None)  # pyright: ignore[reportArgumentType]
        self.kept: list[Record] = []

    async def save(self, record: Record) -> Record:
        self.kept.append(record)
        return record


@dataclass
class Case:
    """An app plugin, an app of it, and what a test needs to run its service."""

    plugin: type[AnApp]
    app: AnApp
    clock: FakeClock
    events: _Events
    records: _Records
    context: AppContext = field(init=False)

    def __post_init__(self) -> None:
        self.context = AppContext(
            app=self.app,
            service=None,  # pyright: ignore[reportArgumentType]  (no test here calls an operation)
            records=self.records,  # (the list stand-in)
            events=self.events,  # (the sink stand-in)
            secrets=None,  # pyright: ignore[reportArgumentType]
            clock=self.clock,
            settings=self.app.settings,
            stop=asyncio.Event(),
        )


def fake_case() -> Case:
    clock = FakeClock()
    settings = FakeAppSettings(every=timedelta(seconds=1))
    return Case(
        plugin=FakeApp,
        app=FakeApp(settings, clock=clock),
        clock=clock,
        events=_Events(FakeApp.name),
        records=_Records(),
    )


CASES: dict[str, Callable[[], Case]] = {"fakeapp": fake_case}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]()


# ---------------------------------------------------------------------------------------------
# What it is
# ---------------------------------------------------------------------------------------------


def test_an_app_is_named_titled_and_described(case: Case) -> None:
    assert case.plugin.name
    assert case.plugin.title
    assert case.plugin.summary


def test_an_app_has_settings_of_its_own(case: Case) -> None:
    assert issubclass(case.plugin.settings_model, AppSettings)
    assert isinstance(case.app.settings, case.plugin.settings_model)


def test_an_app_is_registered_as_a_plugin_of_its_kind(case: Case) -> None:
    registry = PluginRegistry()
    registry.register(APPS, case.plugin)

    [info] = registry.plugins(APPS)

    assert (info.kind, info.name) == (APPS.group, case.plugin.name)
    assert registry.get(APPS, case.plugin.name) is case.plugin


def test_what_is_not_an_app_is_refused() -> None:
    class NotAnApp:
        name = "notanapp"

    registry = PluginRegistry()

    with pytest.raises(PluginError, match=r"udl\.apps"):
        registry.register(APPS, NotAnApp)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------------------------
# Its operations
# ---------------------------------------------------------------------------------------------


def test_an_app_s_operations_are_operations(case: Case) -> None:
    found = case.plugin.operations()

    assert found
    for op in found:
        assert isinstance(op, Operation)
        assert op.title
        assert op.summary
        assert op.description
        assert issubclass(op.request, BaseModel)
        assert issubclass(op.response, BaseModel)


def test_a_service_offers_an_app_s_operations_named_after_it(case: Case) -> None:
    registry = PluginRegistry()
    registry.register(APPS, case.plugin)

    ops = OperationRegistry.discover(registry)

    assert len(ops.in_namespace(case.plugin.name)) == len(case.plugin.operations())
    for op in case.plugin.operations():
        named = ops.get(f"{case.plugin.name}_{op.name}")
        assert named.kind == APPS.group
        assert named.ui == op.ui
        assert resolved_view(named) == resolved_view(
            op.in_namespace(case.plugin.name, kind=APPS.group)
        )


def test_an_app_s_event_types_are_its_own(case: Case) -> None:
    registry = PluginRegistry()
    registry.register(APPS, case.plugin)

    found = EventTypes.discover(registry)

    for event_type in case.plugin.event_types():
        known = found.get(event_type.name)
        assert known.name == f"{case.plugin.name}.{event_type.name.split('.', 1)[-1]}"
        assert event_type.example is not None
        assert event_type.subject_of(event_type.example)


def test_an_app_s_record_kinds_are_records(case: Case) -> None:
    kinds = list(case.plugin.record_kinds())

    assert kinds
    for kind in kinds:
        assert issubclass(kind, Record)
        assert record_kind(kind.kind) is kind
        example = kind.example()
        assert kind.model_validate(example.model_dump()) == example


async def test_an_app_that_overrides_nothing_has_no_service(case: Case) -> None:
    class Bare(AppPlugin[AppSettings]):
        """An app that overrides no hook: it only brings its name."""

        name = "bare"
        title = "Bare"
        summary = "A bare app."

    bare = Bare(AppSettings(), clock=case.clock)

    await asyncio.wait_for(bare.serve(case.context), 1)

    assert not case.events.published


# ---------------------------------------------------------------------------------------------
# Its service
# ---------------------------------------------------------------------------------------------


async def test_an_app_s_service_runs_until_it_is_told_to_stop(case: Case) -> None:
    ctx = case.context

    task = asyncio.create_task(case.app.serve(ctx))
    await case.clock.advance(timedelta(seconds=3))
    ctx.stop.set()
    await asyncio.wait_for(task, 1)

    assert cast(Any, case.app).stopped
    assert case.events.published
    assert case.records.kept


async def test_an_app_s_service_stops_where_it_waits(case: Case) -> None:
    ctx = case.context
    started = asyncio.create_task(case.app.serve(ctx))
    await case.clock.advance(timedelta(seconds=1))

    ctx.stop.set()
    await asyncio.wait_for(started, 1)

    assert not started.cancelled()
