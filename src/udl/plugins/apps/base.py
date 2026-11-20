"""
-----------
- base.py -
-----------

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
import logging
from abc import ABC
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, ForwardRef, TypeIs
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
from udl.plugins.ui.base import UIBundle

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from udl.automations.bus import BusNotice, EventBus
    from udl.core.service import UDLService
    from udl.db.manager import DBManager

# Any app: an app of any settings, as the supervisor and the context hold them. (Not
# AppPlugin[AppSettings]: S is invariant — an app is made with its own settings model and hands
# them out again — so an AppPlugin[FakeAppSettings] is not an AppPlugin[AppSettings]. The
# alias is what PluginKind[ActionPlugin[Any]] and Integration[Any, Any] do elsewhere.)
type AnApp = AppPlugin[Any]

log = logging.getLogger(__name__)

__all__ = [
    "AnApp",
    "AppContext",
    "AppEvent",
    "AppEvents",
    "AppPlugin",
    "AppRecords",
    "AppSettings",
    "AppSubscription",
]


async def _ended(waiter: asyncio.Future[AppEvent]) -> AppEvent | None:
    """`waiter`'s answer, or None when the subscription ended (nothing is coming)."""
    try:
        return await waiter
    except StopAsyncIteration:
        return None


def _operation_method(what: object) -> TypeIs[Callable[..., Awaitable[BaseModel]]]:
    """Whether `what` is an app's operation method: an async `(self, request) -> response`."""
    return callable(what) and inspect.iscoroutinefunction(what)


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

    async def save[R: Record](self, record: R) -> R:
        """Store `record` (by its id, so storing one again replaces it) and give it back, stamped
        with when it was made and last changed."""
        return await self._db.upsert_record(record)

    async def save_all[R: Record](self, records: Sequence[R]) -> list[R]:
        """Store records as one write — all of them, or none — and give them back, stamped with
        when they were made and last changed. A queue's positions are written this way (27.3a):
        half of one is a queue with two tasks in one place."""
        return await self._db.upsert_records(records)

    async def get[R: Record](self, kind: type[R], record_id: UUID) -> R | None:
        """The record of `kind` with that id, or None when there is none."""
        return await self._db.get_record(kind, record_id)

    async def list[R: Record](self, kind: type[R], query: RecordQuery | None = None) -> list[R]:
        """The records of `kind` that match `query` (all of them, when there is none)."""
        return await self._db.list_records(kind, query or RecordQuery())

    async def delete[R: Record](self, kind: type[R], record_id: UUID) -> bool:
        """Delete the record of `kind` with that id. Returns whether there was one."""
        return await self._db.delete_record(kind, record_id)

    async def prune[R: Record](self, kind: type[R], before: datetime) -> int:
        """Delete the records of `kind` created before `before` (an app's history, kept as long
        as its settings say). Returns how many."""
        return await self._db.prune_records(kind, before)


class AppEvents:
    """Where an app publishes its events, and how it waits for others': an event an app publishes
    is stored and dispatched as any other (so subscriptions and actions see it), and an app can
    wait for the events whose type matches a glob (e.g. "orch.task.*") without polling.

    An app only publishes the types it declared (its event_types()), and only events stored from
    now on are waited for: an app that handles one event at a time holds a subscription (see
    subscribe()) rather than calling wait_for() again, so nothing that happens while it works is
    lost."""

    def __init__(self, bus: EventBus, name: str, types: Sequence[EventType[Any]] = ()) -> None:
        self._bus = bus
        self._name = name
        self._types = {event_type.name for event_type in types}
        """The names of the app's own event types (what it may publish)."""

    async def emit(
        self,
        event_type: str,
        data: BaseModel | JsonObject,
        *,
        dedupe_key: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Publish an event of one of this app's declared types, once: one whose dedupe key was
        seen before is not published again. Returns whether it was new.

        The type is this app's: its own name (`tick`, for `fakeapp.tick`), or the whole name of
        one of its types (`fakeapp.tick`). Raises PluginError for another plugin's type and for
        one it never declared — an app may not invent events — and ConfigError for data that does
        not fit the type."""
        from udl.automations.bus import NewEvent

        named = self._own_type(event_type)
        stored = await self._bus.publish(
            NewEvent(
                type=named,
                data=data if isinstance(data, dict) else data.model_dump(mode="json"),
                source=self._name,
                dedupe_key=dedupe_key,
                occurred_at=occurred_at,
            )
        )
        return stored is not None

    def _own_type(self, event_type: str) -> str:
        """`event_type` as one of this app's own declared types. Raises PluginError for anything
        else."""
        named = event_type if "." in event_type else f"{self._name}.{event_type}"
        if not named.startswith(f"{self._name}."):
            raise PluginError(
                f"{self._name} may not publish {event_type!r}: it is another plugin's event type",
                hint=f"Name one of its own ({self._name}.<what>), or just <what>.",
            )
        if named not in self._types:
            declared = ", ".join(sorted(self._types)) or "none"
            raise PluginError(
                f"{self._name} has no event type called {named!r}",
                hint=f"The types it declared: {declared}.",
            )
        return named

    def subscribe(self, pattern: str) -> AppSubscription:
        """A subscription to the events whose type matches `pattern` (a glob), from now on: hold
        it for as long as the app runs, and take what comes with `async for`."""
        return AppSubscription(self._bus, pattern, self._name)

    async def wait_for(self, pattern: str, *, stop: asyncio.Event) -> AppEvent | None:
        """The next event whose type matches `pattern` (a glob, e.g. "orch.task.*") as it is
        stored, or None when `stop` is set first (the app is shutting down).

        One event, for an app that waits once: an app that handles events one after another
        should hold a subscription instead (subscribe()), so that nothing that happens while it
        works is lost."""
        async with self.subscribe(pattern) as subscription:
            waiter = asyncio.ensure_future(subscription.__anext__())
            stopper = asyncio.ensure_future(stop.wait())
            try:
                done, _ = await asyncio.wait({waiter, stopper}, return_when=asyncio.FIRST_COMPLETED)
                if stopper in done:
                    waiter.cancel()
                    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                        await waiter  # (it ends where it waits, and is not left pending)
                    return None
                return await _ended(waiter)
            finally:
                stopper.cancel()
                waiter.cancel()


class AppSubscription:
    """What an app holds to hear of the events whose type matches a glob, from now on: an async
    iterator of AppEvents, one per event as it is stored.

    Entering it starts listening at once, so an event stored after `async with` is never lost,
    even if the app takes a while to ask for it."""

    def __init__(self, bus: EventBus, pattern: str, name: str) -> None:
        self._bus = bus
        self._pattern = pattern
        self._name = name
        self._notices: AsyncGenerator[BusNotice] | None = None
        self._pulled: asyncio.Future[BusNotice] | None = None

    async def __aenter__(self) -> AppSubscription:
        self._notices = self._bus.notices(self._pattern, runs=False)
        self._pull()  # (start listening: the listener is registered on the first step)
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        notices, self._notices = self._notices, None
        pulled, self._pulled = self._pulled, None
        if pulled is not None and not pulled.done():
            pulled.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulled
        if notices is None:
            return
        await asyncio.sleep(0)  # (let a cancelled __anext__ unwind first)
        with contextlib.suppress(RuntimeError):
            await notices.aclose()

    def __aiter__(self) -> AppSubscription:
        return self

    async def __anext__(self) -> AppEvent:
        from udl.automations.bus import NoticeKind
        from udl.automations.events import EventStatus

        if self._notices is None:
            raise RuntimeError("a subscription is only used inside its async with")
        while True:
            notice = await self._next_notice()
            if notice.kind is NoticeKind.LAGGED:
                log.warning(
                    "%s's subscription to %r fell behind: it missed some events",
                    self._name,
                    self._pattern,
                )
                continue
            event = notice.event
            if (
                notice.kind is NoticeKind.EVENT
                and event is not None
                and event.status is EventStatus.PENDING
                and fnmatch.fnmatchcase(event.type, self._pattern)
            ):
                return AppEvent(type=event.type, data=event.data, occurred_at=event.occurred_at)

    async def _next_notice(self) -> BusNotice:
        """The next notice of the bus (one pull is kept going, so nothing is missed).

        The pull is shielded: an app that stops waiting for one event — `wait_for(sub.__anext__(),
        t)` giving up, say — must not take the subscription down with it, so the next call goes
        on from the same listener."""
        if self._notices is None:
            raise RuntimeError("a subscription is only used inside its async with")
        if self._pulled is None:
            self._pulled = asyncio.ensure_future(self._notices.__anext__())
        try:
            found = await asyncio.shield(self._pulled)
        except asyncio.CancelledError:
            # (A pull that was cancelled is over; one that finished holds a notice for the next
            # call, so it is kept.)
            if self._pulled.cancelled():
                self._pulled = None
            raise
        self._pulled = None
        return found

    def _pull(self) -> None:
        """Start the next pull (so the listener is registered without waiting for a caller)."""
        if self._notices is not None and self._pulled is None:
            self._pulled = asyncio.ensure_future(self._notices.__anext__())


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
    # The UI it brings of its own (a bundle, 18.7), or None to get the generated page
    # (24.3-24.6) as every other plugin does.
    ui: ClassVar[UIBundle | None] = None

    def __init__(self, settings: S, *, clock: Clock, records: AppRecords | None = None) -> None:
        self.settings = settings
        self.clock = clock
        self._records = records

    @property
    def records(self) -> AppRecords:
        """Where it keeps its records: what its operations read and write (its service is given
        them too, in its AppContext). Raises RuntimeError when it was made without them."""
        if self._records is None:
            raise RuntimeError(
                f"The {self.name} app has no records: it was made without them (an app udl makes "
                "has them)"
            )
        return self._records

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
        method of that name, `(self, request) -> response` (or `(self, request, *, service)`,
        when it needs udl's records or its secret store), whose docstring describes it.

        It runs on the app of its name this service has (udl.automations.apps.AppSupervisor), so
        an app's operations are offered on the CLI, the API and MCP with no further work.
        Raises PluginError when the app has no such method, or it is not an async
        `(self, request) -> response`."""
        what = f"{cls.__qualname__}.{name}"
        try:
            found: object = getattr(cls, name)
        except AttributeError:
            raise PluginError(f"{what} is not one of {cls.__qualname__}'s methods") from None
        if not _operation_method(found):
            raise PluginError(
                f"{what} is not an operation: an app's operations are its async methods, "
                "(self, request) -> response"
            )
        method = found

        # (Operation.of reads an operation's models and description from its function.)
        forward = annotationlib.Format.FORWARDREF
        annotations = annotationlib.get_annotations(method, format=forward)
        parameters = list(inspect.signature(method, annotation_format=forward).parameters)
        extra = [name for name in parameters if name not in ("self", "request", "service")]
        if len(parameters) < 2 or len(parameters) > 3 or extra:
            raise PluginError(
                f"{what} is not an operation: it takes (self, request), or "
                f"(self, request, *, service) when it needs udl's own records, not "
                f"({', '.join(parameters) or 'nothing'})"
            )
        wants_service = "service" in parameters

        async def call(service: UDLService, request: BaseModel) -> BaseModel:
            app = await service.apps.get(cls.name)
            if wants_service:
                return await method(app, request, service=service)
            return await method(app, request)

        request = annotations.get(parameters[1])
        response = annotations.get("return")
        for label, annotation in (("request", request), ("response", response)):
            if annotation is None or isinstance(annotation, (str, ForwardRef)):
                raise PluginError(
                    f"{what}'s {label} is {annotation!r}, not a model udl can read at run time: "
                    "import it for real (a model only imported under TYPE_CHECKING is a string "
                    "here, and no adapter can be made from it)"
                )
        call.__annotations__ = {"request": request, "return": response}
        call.__doc__ = inspect.getdoc(method)
        call.__qualname__ = f"{cls.__qualname__}.{name}"
        return Operation.of(call, name=name, title=title, hints=hints, cli=cli, api=api, ui=ui)

    async def serve(self, ctx: AppContext) -> None:
        """Its runtime service: run until ctx.stop is set, on ctx.clock. The default does nothing
        (an app that only brings operations and records needs no service)."""
        return
