"""
------------
- base.py -
------------

AppPlugin: the interface every app implements (the udl.apps kind), and what one is given to run
with (an AppContext).

An app is a plugin that brings operations, record kinds, event types, a runtime service and a UI,
without accounts. udl's orchestrator (orch) is one; so is anything else that keeps its own data
and runs a loop of its own while udl serves.

An app is code, not data: it sets `name` (what its operations, event types and record kinds are
named after), `title`, `summary`, its `settings_model` and what it `requires`, and it overrides
the classmethods and the hooks it needs. `operations()` and `event_types()` are read as any other
plugin's are, so its operations are offered on the CLI, the API and MCP with no further work; its
record kinds register themselves when its module is imported, as udl's own do.

Its service, `serve(ctx)`, runs while the udl that holds the automations' lease runs the
automations (udl.automations.apps): it is started once per lease, stopped when the lease goes,
and started again (with the watchers' backoff) when it raises.

tests/contract/test_app_contract.py is the behaviour every app must have.
"""

# System Imports
import annotationlib
import asyncio
import contextlib
import fnmatch
import inspect
from abc import ABC
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import UUID

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.automations.events import EventType
from udl.clock import Clock
from udl.core.operations import APISpec, CLISpec, Operation, OperationHints, UISpec
from udl.db.models import JsonObject
from udl.db.records import Record, RecordQuery
from udl.errors import PluginError
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.base import SecretStore

if TYPE_CHECKING:
    from udl.automations.bus import EventBus
    from udl.core.service import UDLService
    from udl.db.manager import DBManager

# Any app: an app of any settings, as the supervisor and the context hold them.
type AnApp = AppPlugin[Any]

__all__ = [
    "AnApp",
    "AppContext",
    "AppEvent",
    "AppEvents",
    "AppPlugin",
    "AppRecords",
    "AppSettings",
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class AppSettings(_Model):
    """An app's settings (each app's model extends it): its defaults, then its `[apps.<name>]`
    table in the config, then what set_plugin_settings set."""


class AppEvent(_Model):
    """An event an app waited for: its type, its data and when it happened."""

    type: str
    """Its event type (e.g. orch.task.ready)."""
    data: JsonObject
    """What happened, as its type's model has it."""
    occurred_at: datetime
    """When it happened."""


class AppRecords:
    """Where an app keeps its records (udl.db.records): the kinds it defined, stored in udl's
    database. Its own kinds register themselves when its module is imported, so a record an app
    keeps is a record every database plugin stores the same way."""

    def __init__(self, db: DBManager) -> None:
        self._db = db

    async def save(self, record: Record) -> Record:
        """Store `record` (by its id, so storing one again replaces it) and give it back, stamped
        with when it was made and last changed."""
        return await self._db.upsert_record(record)

    async def get[R: Record](self, kind: type[R], record_id: UUID) -> R | None:
        """The record of `kind` with that id, or None when there is none."""
        return await self._db.get_record(kind, record_id)

    async def list[R: Record](self, kind: type[R], query: RecordQuery | None = None) -> list[R]:
        """The records of `kind` that match `query` (all of them, when there is none)."""
        return await self._db.list_records(kind, query or RecordQuery())

    async def delete[R: Record](self, kind: type[R], record_id: UUID) -> bool:
        """Delete the record of `kind` with that id. Returns whether there was one."""
        return await self._db.delete_record(kind, record_id)


class AppEvents:
    """Where an app publishes its events, and how it waits for others': an event an app publishes
    is stored and dispatched as any other (so subscriptions and actions see it), and an app can
    wait for the events whose type matches a glob (e.g. "orch.task.*") without polling."""

    def __init__(self, bus: EventBus, name: str) -> None:
        self._bus = bus
        self._name = name

    async def emit(
        self,
        event_type: str,
        data: BaseModel | JsonObject,
        *,
        dedupe_key: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Publish an event of this app's own type, once: one whose dedupe key was seen before is
        not published again. Returns whether it was new.

        The type is this app's: its own name (`tick`, for `fakeapp.tick`), or the whole name of
        one of its types (`fakeapp.tick`). Raises PluginError for anything else — an app may not
        publish another plugin's events — and ConfigError for data that does not fit the type."""
        from udl.automations.bus import NewEvent

        stored = await self._bus.publish(
            NewEvent(
                type=self._own_type(event_type),
                data=data if isinstance(data, dict) else data.model_dump(mode="json"),
                source=self._name,
                dedupe_key=dedupe_key,
                occurred_at=occurred_at,
            )
        )
        return stored is not None

    def _own_type(self, event_type: str) -> str:
        """`event_type` as one of this app's own. Raises PluginError for anything else."""
        if "." not in event_type:
            return f"{self._name}.{event_type}"
        if event_type.startswith(f"{self._name}."):
            return event_type
        raise PluginError(
            f"{self._name} may not publish {event_type!r}: it is another plugin's event type",
            hint=f"Name one of its own ({self._name}.<what>), or just <what>.",
        )

    async def wait_for(self, pattern: str, *, stop: asyncio.Event) -> AppEvent | None:
        """The next event whose type matches `pattern` (a glob, e.g. "orch.task.*") as it is
        stored, or None when `stop` is set first (the app is shutting down).

        An event is waited for once: the bus tells of it again when it is dispatched or dropped,
        which is not it happening. Events stored before the call are not seen (the bus tells of
        what happens from now on)."""
        waiter = asyncio.ensure_future(self._next(pattern))
        stopper = asyncio.ensure_future(stop.wait())
        try:
            done, _ = await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if stopper in done:
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter  # (it ends where it waits, and is not left pending)
                return None
            return await waiter
        finally:
            stopper.cancel()
            waiter.cancel()

    async def _next(self, pattern: str) -> AppEvent:
        from udl.automations.bus import NoticeKind
        from udl.automations.events import EventStatus

        async for notice in self._bus.notices(pattern, runs=False):
            event = notice.event
            if (
                notice.kind is NoticeKind.EVENT
                and event is not None
                and event.status is EventStatus.PENDING
                and fnmatch.fnmatchcase(event.type, pattern)
            ):
                return AppEvent(type=event.type, data=event.data, occurred_at=event.occurred_at)
        # (The bus's notices only end when the listener is cancelled: nothing is coming.)
        raise asyncio.CancelledError


class AppContext:
    """What an app's service is given to run with: the app itself, the operations it may call,
    its records, its events, the secret store, the clock, its settings, and what stops it."""

    def __init__(
        self,
        *,
        app: AnApp,
        service: UDLService,
        records: AppRecords,
        events: AppEvents,
        secrets: SecretStore,
        clock: Clock,
        settings: AppSettings,
        stop: asyncio.Event,
    ) -> None:
        self.app = app
        """The app this is (its class is the plugin)."""
        self.service = service
        """The service to call operations with (udl.core.service.UDLService)."""
        self.records = records
        """Where it keeps its records."""
        self.events = events
        """What it publishes, and what it waits for."""
        self.secrets = secrets
        """The secret store (udl.plugins.secrets.base.SecretStore)."""
        self.clock = clock
        """The clock (udl.clock.Clock): nothing here calls datetime.now() or asyncio.sleep()."""
        self.settings = settings
        """Its settings, as its settings_model has them."""
        self.stop = stop
        """Set when it should stop; an app's service returns at once when it is set."""


class AppPlugin[S: AppSettings](ABC):
    """An app plugin: a plugin that brings operations, record kinds, event types, a runtime
    service and a UI, without accounts. Made with its settings and udl's clock.

    Subclasses set `name`, `title`, `summary`, `requires`, `settings_model` and, when they bring
    a UI of their own, `ui`; they override the classmethods and serve() as they need.
    """

    name: ClassVar[str]  # e.g. "orch": its operations, event types and records are named after it
    title: ClassVar[str]  # e.g. "Orchestrator"
    summary: ClassVar[str] = ""  # what it is, in a line
    # What it needs installed (`udl plugins list` says what is missing).
    requires: ClassVar[tuple[Requirement, ...]] = ()
    settings_model: ClassVar[type[AppSettings]] = AppSettings
    # The UI it brings of its own (a udl.ui plugin's manifest, 18.7), or None to get the
    # generated page (24.3-24.6) as every other plugin does.
    ui: ClassVar[object | None] = None

    def __init__(self, settings: S, *, clock: Clock) -> None:
        self.settings = settings
        self.clock = clock

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        """The event types it publishes, each named <its name>.<what> (e.g. orch.task.ready)."""
        return ()

    @classmethod
    def record_kinds(cls) -> Sequence[type[Record]]:
        """The kinds of record it defines (each of which registers itself as its module is
        imported): for the record contract suite to run over them."""
        return ()

    @classmethod
    def operations(cls) -> list[Operation]:
        """The operations it adds, each from one of its methods (see operation())."""
        return []

    @classmethod
    def operation(
        cls,
        name: str,
        *,
        title: str,
        hints: OperationHints | None = None,
        cli: CLISpec | None = None,
        api: APISpec | None = None,
        ui: UISpec | None = None,
    ) -> Operation:
        """The operation called `name` (offered as `<its name>_<name>`): the app's own async
        method of that name, `(self, request) -> response`, whose docstring describes it.

        It runs on the app of its name this service has (udl.automations.apps.AppSupervisor), so
        an app's operations are offered on the CLI, the API and MCP with no further work.
        Raises PluginError when the app has no such method, or it is not an async
        `(self, request) -> response`."""
        what = f"{cls.__qualname__}.{name}"
        try:
            found: object = getattr(cls, name)
        except AttributeError:
            raise PluginError(f"{what} is not one of {cls.__qualname__}'s methods") from None
        if not callable(found) or not inspect.iscoroutinefunction(found):
            raise PluginError(
                f"{what} is not an operation: an app's operations are its async methods, "
                "(self, request) -> response"
            )
        method = cast(Callable[..., Awaitable[BaseModel]], found)

        async def call(service: UDLService, request: BaseModel) -> BaseModel:
            app = await service.apps.get(cls.name)
            return await method(app, request)

        # (Operation.of reads an operation's models and description from its function.)
        forward = annotationlib.Format.FORWARDREF
        annotations = annotationlib.get_annotations(method, format=forward)
        parameters = list(inspect.signature(method, annotation_format=forward).parameters)
        if len(parameters) != 2:
            raise PluginError(
                f"{what} is not an operation: it takes (self, request), not "
                f"{', '.join(parameters) or 'nothing'}"
            )
        call.__annotations__ = {
            "request": annotations.get(parameters[1]),
            "return": annotations.get("return"),
        }
        call.__doc__ = inspect.getdoc(method)
        call.__qualname__ = f"{cls.__qualname__}.{name}"
        return Operation.of(call, name=name, title=title, hints=hints, cli=cli, api=api, ui=ui)

    async def serve(self, ctx: AppContext) -> None:
        """Its runtime service: run until ctx.stop is set, on ctx.clock. The default does nothing
        (an app that only brings operations and records needs no service)."""
        return
