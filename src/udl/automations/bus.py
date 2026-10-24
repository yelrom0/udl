"""
----------
- bus.py -
----------

EventBus: where every event goes. Publishing validates an event's data by its type, writes its
subject and stores it, pending, before anything is done with it; an event whose type and dedupe
key were seen before is stored once (the same email or Slack message never fires twice, even
after a restart). Every service has a bus (UDLService.bus), so any udl can publish: a CLI run,
`udl mcp`, a run's action.

Dispatching (the runtime's, udl.automations.runtime) matches an event against the enabled
subscriptions and queues a run for each action of each one that matches, then marks the event
dispatched. Runs are keyed by event, action, subscription and dispatch, so dispatching again
(after a crash) queues nothing twice, and a replay is a new dispatch. An event deeper than
[automations] max_event_depth (events caused by runs, caused by events...) is dropped: stored,
never dispatched. dispatch_pending() dispatches what is pending, whoever published it.

notices() is what happens, as it happens (events stored and dispatched, runs changed), for the
live stream (GET /events/stream). A listener that falls 500 behind loses the oldest and hears
that it lagged.
"""

# System Imports
import asyncio
import fnmatch
import logging
from collections import deque
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final
from uuid import UUID, uuid4

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt, ValidationError

# Local Imports
from udl.automations.events import EventRecord, EventStatus, EventTypes, current_run
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.subscriptions import Subscription, matches
from udl.clock import Clock
from udl.config import AutomationsSettings
from udl.db.manager import DBManager
from udl.db.models import JsonObject
from udl.db.records import RecordQuery
from udl.errors import ConfigError, DuplicateKey
from udl.plugins.actions.base import Action

log = logging.getLogger(__name__)

BATCH: Final = 100
"""Pending events dispatched at a time."""
LISTENER_QUEUE: Final = 500
"""Notices a listener may fall behind by before it loses the oldest."""


class NewEvent(BaseModel):
    """An event to publish."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    type: str
    """Its event type."""
    data: JsonObject = Field(default_factory=dict)
    """What happened, as its type's model has it."""
    source: str = "udl"
    """What publishes it: udl, the scheduler, an integration, cli, api, mcp."""
    account_id: UUID | None = None
    """The account it happened in, if any."""
    occurred_at: AwareDatetime | None = None
    """When it happened (now, when not said)."""
    dedupe_key: str | None = None
    """What makes it the same event as another of its type: it is stored once."""
    caused_by: UUID | None = None
    """The run that led to it (the run that is running, when not said)."""
    depth: NonNegativeInt | None = None
    """How deep it is (the running run's depth plus one, else 0, when not said)."""


class NoticeKind(StrEnum):
    EVENT = "event"  # an event was stored, or dispatched (or dropped)
    RUN = "run"  # a run was queued, or changed
    LAGGED = "lagged"  # the listener missed some: it should reload what it shows


@dataclass(frozen=True)
class BusNotice:
    """What happened, for a listener."""

    kind: NoticeKind
    event: EventRecord | None = None
    run: ActionRun | None = None

    @property
    def event_id(self) -> UUID | None:
        """The event it is about (a run's event)."""
        if self.event is not None:
            return self.event.id
        return self.run.event_id if self.run is not None else None


@dataclass
class _Listener:
    types: str
    runs: bool
    queue: deque[BusNotice] = field(default_factory=lambda: deque[BusNotice](maxlen=LISTENER_QUEUE))
    lagged: bool = False
    woken: asyncio.Event = field(default_factory=asyncio.Event)

    def wants(self, notice: BusNotice) -> bool:
        if notice.event is not None:
            return fnmatch.fnmatchcase(notice.event.type, self.types)
        if notice.run is not None and self.runs:
            event_type = notice.run.event_type
            return self.types == "*" or (
                event_type is not None and fnmatch.fnmatchcase(event_type, self.types)
            )
        return False

    def hear(self, notice: BusNotice) -> None:
        if len(self.queue) == self.queue.maxlen:
            self.lagged = True
        self.queue.append(notice)
        self.woken.set()


type AccountNames = Callable[[UUID], str | None]
"""An account's name (integration:name) by its id, for matching a subscription's accounts."""


class EventBus:
    """Stores events and dispatches them, in `db` (on `clock`'s time)."""

    def __init__(
        self,
        db: DBManager,
        types: EventTypes,
        clock: Clock,
        settings: AutomationsSettings,
        *,
        account_names: AccountNames | None = None,
    ) -> None:
        self.db = db
        self.types = types
        self.clock = clock
        self.settings = settings
        self._account_names: AccountNames = account_names or (lambda account_id: None)
        self._listeners: list[_Listener] = []
        self._subscriptions: list[Subscription] | None = None
        self._actions: dict[str, Action] | None = None
        self.published = asyncio.Event()
        """Set when this bus stores an event (the runtime's dispatcher waits for it)."""

    # -- publishing ----------------------------------------------------------------------------

    async def publish(self, new: NewEvent) -> EventRecord | None:
        """Store an event (pending), validated by its type. Returns it, or None when an event
        of its type with its dedupe key was stored before. Raises ConfigError for a type udl
        does not have, or data it does not fit."""
        event_type = self.types.get(new.type)
        try:
            data = event_type.validate(new.data)
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, problem['loc'])) or 'data'}: {problem['msg']}"
                for problem in e.errors()
            )
            raise ConfigError(f"The {new.type} event's data is not valid: {problems}") from e
        running = current_run.get()
        caused_by, depth = new.caused_by, new.depth
        if caused_by is None and running is not None:
            caused_by = running.id
        if depth is None:
            depth = running.depth + 1 if running is not None else 0
        record = EventRecord(
            type=new.type,
            source=new.source,
            account_id=new.account_id,
            occurred_at=new.occurred_at or self.clock.now(),
            subject=event_type.subject_of(event_type.data.model_validate(data)),
            data=data,
            dedupe_key=new.dedupe_key,
            caused_by=caused_by,
            depth=depth,
        )
        try:
            stored = await self.db.upsert_record(record)
        except DuplicateKey:
            log.debug("The %s event %r was seen before", new.type, new.dedupe_key)
            return None
        self.published.set()
        self._notify(BusNotice(NoticeKind.EVENT, event=stored))
        return stored

    # -- dispatching ---------------------------------------------------------------------------

    def subscriptions_changed(self) -> None:
        """Read the subscriptions and actions again before the next dispatch (this process
        changed them: another's changes are read at each dispatch_pending())."""
        self._subscriptions = None
        self._actions = None

    async def dispatch(
        self, event: EventRecord, *, replay: bool = False, subscription: str | None = None
    ) -> list[ActionRun]:
        """Queue a run for each action of each enabled subscription `event` matches (or only
        `subscription`'s), and mark it dispatched (unless this is a `replay`: a new dispatch of
        an event dispatched before). An event deeper than max_event_depth is dropped. Returns
        the runs queued (and skipped)."""
        limit = self.settings.max_event_depth
        if event.depth > limit:
            reason = f"it is {event.depth} deep, deeper than max_event_depth ({limit})"
            log.warning("Dropped the %s event %s: %s", event.type, event.id, reason)
            await self._mark(event, EventStatus.DROPPED, reason)
            return []
        subscriptions, actions = await self._configured()
        account = None if event.account_id is None else self._account_names(event.account_id)
        dispatch = uuid4() if replay else event.id
        queued: list[ActionRun] = []
        for found in subscriptions:
            if subscription is not None and found.name != subscription:
                continue
            if not matches(found, event.event(), account):
                continue
            for name in found.actions:
                action = actions.get(name)
                if action is None:
                    log.warning("The %s subscription runs %r, which is no action", found.name, name)
                    continue
                run = await self._queue(event, found, action, dispatch)
                if run is not None:
                    queued.append(run)
        if not replay:
            await self._mark(event, EventStatus.DISPATCHED)
        return queued

    async def dispatch_pending(self) -> int:
        """Dispatch the pending events (whoever published them), oldest first, BATCH at a
        time, reading the subscriptions again first. Returns how many."""
        self.subscriptions_changed()
        dispatched = 0
        while True:
            pending = await self.db.list_records(
                EventRecord, RecordQuery(where={"status": EventStatus.PENDING})
            )
            batch = list(reversed(pending))[:BATCH]
            for event in batch:
                await self.dispatch(event)
            dispatched += len(batch)
            if len(pending) <= BATCH:
                return dispatched

    async def _configured(self) -> tuple[list[Subscription], dict[str, Action]]:
        if self._subscriptions is None or self._actions is None:
            every = await self.db.list_records(Subscription, RecordQuery(where={"enabled": True}))
            self._subscriptions = list(reversed(every))  # (oldest first)
            self._actions = {
                action.name: action for action in await self.db.list_records(Action, RecordQuery())
            }
        return self._subscriptions, self._actions

    async def _queue(
        self, event: EventRecord, subscription: Subscription, action: Action, dispatch: UUID
    ) -> ActionRun | None:
        """Store a run of `action` for `event` (skipped when the action is disabled). None when
        it was queued before."""
        skipped = None if action.enabled else "the action is disabled"
        run = ActionRun(
            action_id=action.id,
            action=action.name,
            plugin=action.plugin,
            event_id=event.id,
            event_type=event.type,
            subscription_id=subscription.id,
            subscription=subscription.name,
            dispatch=dispatch,
            depth=event.depth,
            status=RunStatus.QUEUED if skipped is None else RunStatus.SKIPPED,
            skipped_reason=skipped,
            finished_at=None if skipped is None else self.clock.now(),
        )
        try:
            stored = await self.db.upsert_record(run)
        except DuplicateKey:
            return None
        self.run_changed(stored)
        return stored

    async def _mark(
        self, event: EventRecord, status: EventStatus, reason: str | None = None
    ) -> None:
        marked = event.model_copy(
            update={"status": status, "dispatched_at": self.clock.now(), "dropped_reason": reason}
        )
        self._notify(BusNotice(NoticeKind.EVENT, event=await self.db.upsert_record(marked)))

    # -- listening -----------------------------------------------------------------------------

    def run_changed(self, run: ActionRun) -> None:
        """Tell the listeners a run was queued or changed (the runner does, at each step)."""
        self._notify(BusNotice(NoticeKind.RUN, run=run))

    async def notices(self, types: str = "*", runs: bool = True) -> AsyncGenerator[BusNotice]:
        """What happens from now on: the events whose type `types` (a glob) matches, as they
        are stored and dispatched, and (with `runs`) their runs as they change, in order. A
        listener that falls behind loses the oldest and hears a LAGGED notice."""
        listener = _Listener(types=types, runs=runs)
        self._listeners.append(listener)
        try:
            while True:
                await listener.woken.wait()
                listener.woken.clear()
                if listener.lagged:
                    listener.lagged = False
                    yield BusNotice(NoticeKind.LAGGED)
                while listener.queue:
                    yield listener.queue.popleft()
        finally:
            self._listeners.remove(listener)

    def _notify(self, notice: BusNotice) -> None:
        for listener in self._listeners:
            if listener.wants(notice):
                listener.hear(notice)
