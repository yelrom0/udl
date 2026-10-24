"""
--------------
- runtime.py -
--------------

AutomationRuntime: what runs the automations, in `udl serve` (create_app's lifespan, unless
[automations] enabled = false). Only one udl runs them per database: the one that holds the
`automations` lease, which it claims every third of [automations] lease_ttl (named after its
host, its process and a random part). While it holds it, it runs:

- the dispatcher: dispatch_pending() every dispatch_interval, and at once when this udl
  publishes an event;
- the runner (udl.automations.runner), after ending as interrupted what a runtime that stopped
  left running;
- retention: every hour, it prunes events and ended runs older than event_retention_days (never
  a pending event, or a run that waits);
- the scheduler (udl.automations.scheduler), which fires the schedules as they come due;
- the watchers (udl.integrations.watchers), one for each account a subscription wants events
  from.

A runtime that loses the lease stops them (interrupting its runs) until it has it again;
closing releases it, so another `udl serve` takes over at once. It will not start with a
scripts_dir inside a folder udl writes to, so nothing udl downloads can become a script.

runtime_status() is what get_status says: read from the database, so any udl can say which udl
runs the automations.
"""

# System Imports
import asyncio
import contextlib
import logging
import os
import secrets
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import TracebackType
from typing import TYPE_CHECKING, Final, Self

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt

# Local Imports
from udl.automations.events import EventRecord, EventStatus
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.schedules import Schedule
from udl.clock import pause
from udl.config import Settings
from udl.db.manager import DBManager
from udl.db.records import Lease, Record, RecordQuery
from udl.errors import ConfigError, DatabaseError
from udl.integrations.watchers import WatcherState, WatcherStatus, watcher_statuses

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)

LEASE: Final = "automations"
"""The lease the runtime that runs the automations holds."""
RETENTION_EVERY: Final = timedelta(hours=1)
PRUNE_PAGE: Final = 500
"""Records pruned at a time."""
UPCOMING: Final = 5
"""How many of the next schedules the status says."""
STOP_GRACE: Final = 5.0
"""Seconds the runtime's loops get to stop where they wait, before they are cancelled (one
stuck on a database that does not answer)."""


class AutomationState(StrEnum):
    """Who runs the automations."""

    RUNNING = "running"  # this udl
    ELSEWHERE = "elsewhere"  # another udl (the lease's holder)
    STOPPED = "stopped"  # nobody
    DISABLED = "disabled"  # [automations] enabled = false


class UpcomingSchedule(BaseModel):
    """A schedule, and when it fires next."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    name: str
    """The schedule's name."""
    next_run_at: AwareDatetime
    """When it fires next (without its jitter)."""


class RuntimeStatus(BaseModel):
    """Who runs the automations, and what waits."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    automations: AutomationState
    """running (here), elsewhere (in the udl holding the lease), stopped (nobody runs them: start
    `udl serve`) or disabled ([automations] enabled = false)."""
    holder: str | None = None
    """The udl that holds (or last held) the automations' lease: its host:process:random."""
    lease_expires_at: AwareDatetime | None = None
    """When the lease expires unless its holder renews it."""
    here: str | None = None
    """This udl's own name, when it runs a runtime."""
    queued_runs: NonNegativeInt = 0
    """Runs waiting to run (or to be tried again)."""
    running_runs: NonNegativeInt = 0
    """Runs running."""
    waiting_runs: NonNegativeInt = 0
    """Runs waiting for a tool call to be approved."""
    pending_events: NonNegativeInt = 0
    """Events stored but not dispatched yet."""
    last_dispatch: AwareDatetime | None = None
    """When an event was last dispatched."""
    next_schedules: list[UpcomingSchedule] = Field(default_factory=list[UpcomingSchedule])
    """The next schedules to fire (five at most), soonest first."""
    watchers: list[WatcherStatus] = Field(default_factory=list[WatcherStatus])
    """How each account's watcher is (stopped, all, while nothing runs the automations)."""


def check_scripts_dir(settings: Settings) -> None:
    """Raise ConfigError when [automations] scripts_dir is inside a folder udl writes to (what
    it downloads could become a script)."""
    scripts = settings.automations.scripts_dir.expanduser().resolve()
    roots = [("download_dir", settings.download_dir)] + [
        ("api.allowed_output_roots", root) for root in settings.api.allowed_output_roots
    ]
    for name, root in roots:
        if scripts.is_relative_to(root.expanduser().resolve()):
            raise ConfigError(
                f"[automations] scripts_dir ({scripts}) is inside {name} ({root}): what udl "
                "writes there could become a script",
                hint="Move scripts_dir out of the folders udl downloads to (the default is "
                "~/.config/udl/scripts).",
            )


@dataclass(frozen=True)
class _Work:
    """The automations, running (while the runtime holds the lease), and what stops them."""

    task: asyncio.Task[None]
    stop: asyncio.Event


class AutomationRuntime:
    """Runs the automations of `service` while it holds the lease (an async context: entering
    starts it, leaving stops it and releases the lease)."""

    def __init__(self, service: UDLService, *, holder: str | None = None) -> None:
        self.service = service
        self.holder = holder or self.name_holder()
        self.active = False
        """Whether it holds the lease (and runs the automations)."""
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @staticmethod
    def name_holder() -> str:
        """A name for a runtime: host:process:random."""
        return f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"

    async def __aenter__(self) -> Self:
        check_scripts_dir(self.service.settings)
        self.service.runtime = self
        self.service.runner.holder = self.holder
        self._task = asyncio.create_task(self._keep_lease(), name="udl automations")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.stop()

    async def stop(self) -> None:
        """Stop the automations and release the lease. (Each of its loops stops where it waits,
        never in the middle of what it does, such as a database's answer.)"""
        if self._task is not None:
            self._stopping.set()
            await _stopped(self._task)
            self._task = None
        if self.service.runtime is self:
            self.service.runtime = None

    # -- the lease -----------------------------------------------------------------------------

    async def _keep_lease(self) -> None:
        settings = self.service.settings.automations
        work: _Work | None = None  # (the automations, while it holds the lease)
        try:
            while not self._stopping.is_set():
                held = await self._claim(settings.lease_ttl)
                if held and work is None:
                    work = await self._begin()
                elif not held and work is not None:
                    log.warning("This udl lost the automations' lease: it stops running them")
                    await self._end(work)
                    work = None
                await self._pause(settings.lease_ttl / 3, self._stopping)
        finally:
            if work is not None:
                await self._end(work)
            with contextlib.suppress(DatabaseError):
                await self.service.db.release_lease(LEASE, self.holder)

    async def _pause(self, delay: timedelta, *until: asyncio.Event) -> None:
        """Wait for `delay`, or until one of `until` is set."""
        await pause(self.service.clock, delay, *until)

    async def _claim(self, ttl: timedelta) -> bool:
        try:
            return await self.service.db.claim_lease(LEASE, self.holder, ttl) is not None
        except DatabaseError as e:
            log.warning("Could not claim the automations' lease: %s", e)
            return False

    async def _begin(self) -> _Work:
        log.info("This udl runs the automations (as %s)", self.holder)
        self.active = True
        try:
            await self.service.runner.recover()
        except DatabaseError as e:
            log.warning("Could not read the runs to clean them up: %s", e)
        stop = asyncio.Event()
        return _Work(asyncio.create_task(self._serve(stop), name="udl automations' work"), stop)

    async def _end(self, work: _Work) -> None:
        self.active = False
        work.stop.set()
        await _stopped(work.task)
        await self.service.runner.shutdown()

    async def _serve(self, stop: asyncio.Event) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._dispatcher(stop))
            group.create_task(self._forever("run", self.service.runner.serve, stop))
            group.create_task(self._retention(stop))
            group.create_task(self._forever("schedule", self.service.scheduler.serve, stop))
            group.create_task(self._forever("watch", self.service.watchers.serve, stop))

    # -- the work ------------------------------------------------------------------------------

    async def _dispatcher(self, stop: asyncio.Event) -> None:
        bus = self.service.bus
        interval = self.service.settings.automations.dispatch_interval
        while not stop.is_set():
            bus.published.clear()
            try:
                if await bus.dispatch_pending():
                    self.service.runner.wake()
            except Exception:
                log.exception("Dispatching the pending events failed")
            await self._pause(interval, bus.published, stop)

    async def _retention(self, stop: asyncio.Event) -> None:
        settings, clock = self.service.settings.automations, self.service.clock
        while not stop.is_set():
            before = clock.now() - timedelta(days=settings.event_retention_days)
            try:
                pruned = await prune_history(self.service.db, before)
                if pruned:
                    log.info("Pruned %d events and runs older than %s", pruned, before)
            except Exception:
                log.exception("Pruning the old events and runs failed")
            await self._pause(RETENTION_EVERY, stop)

    async def _forever(
        self, what: str, work: Callable[[asyncio.Event], Awaitable[None]], stop: asyncio.Event
    ) -> None:
        """`work` (until `stop` is set), started again after a breath whenever it fails."""
        while not stop.is_set():
            try:
                await work(stop)
            except Exception:
                log.exception("The automations' %s loop failed: starting it again", what)
                await self._pause(timedelta(seconds=1), stop)


async def _stopped(task: asyncio.Task[None]) -> None:
    """Wait for `task` (told to stop) to end, cancelling it when it has not within STOP_GRACE."""
    _, late = await asyncio.wait([task], timeout=STOP_GRACE)
    if late:
        log.warning("The automations did not stop within %ss: cancelling them", STOP_GRACE)
        task.cancel()
        await asyncio.wait([task])


async def prune_history(db: DBManager, before: datetime) -> int:
    """Delete the events (but pending ones) and the ended runs created before `before`. Returns
    how many."""
    pruned = 0
    for status in (EventStatus.DISPATCHED, EventStatus.DROPPED):
        pruned += await _prune(db, EventRecord, status, before)
    for status in RunStatus:
        if status.ended:
            pruned += await _prune(db, ActionRun, status, before)
    return pruned


async def _prune(db: DBManager, kind: type[Record], status: str, before: datetime) -> int:
    pruned = 0
    query = RecordQuery(where={"status": status}, until=before, limit=PRUNE_PAGE)
    while page := await db.list_records(kind, query):
        for record in page:
            await db.delete_record(kind, record.id)
        pruned += len(page)
    return pruned


async def runtime_status(service: UDLService) -> RuntimeStatus:
    """Who runs the automations, and what waits (read from the database)."""
    db, now = service.db, service.clock.now()

    async def count(kind: type[Record], status: str) -> int:
        return len(await db.list_records(kind, RecordQuery(where={"status": status})))

    [lease] = (await db.list_records(Lease, RecordQuery(where={"name": LEASE}, limit=1))) or [None]
    runtime = service.runtime
    if not service.settings.automations.enabled:
        state = AutomationState.DISABLED
    elif lease is None or lease.expires_at <= now:
        state = AutomationState.STOPPED
    elif runtime is not None and runtime.active and lease.holder == runtime.holder:
        state = AutomationState.RUNNING
    else:
        state = AutomationState.ELSEWHERE
    dispatched = await db.list_records(
        EventRecord, RecordQuery(where={"status": EventStatus.DISPATCHED}, limit=1)
    )
    upcoming = sorted(
        (
            UpcomingSchedule(name=schedule.name, next_run_at=schedule.next_run_at)
            for schedule in await db.list_records(Schedule, RecordQuery(where={"enabled": True}))
            if schedule.next_run_at is not None
        ),
        key=lambda found: found.next_run_at,
    )
    watchers = await watcher_statuses(service)
    if state not in (AutomationState.RUNNING, AutomationState.ELSEWHERE):
        watchers = [found.model_copy(update={"state": WatcherState.STOPPED}) for found in watchers]
    return RuntimeStatus(
        automations=state,
        holder=lease.holder if lease is not None else None,
        lease_expires_at=lease.expires_at if lease is not None else None,
        here=runtime.holder if runtime is not None else None,
        queued_runs=await count(ActionRun, RunStatus.QUEUED),
        running_runs=await count(ActionRun, RunStatus.RUNNING),
        waiting_runs=await count(ActionRun, RunStatus.AWAITING_APPROVAL),
        pending_events=await count(EventRecord, EventStatus.PENDING),
        last_dispatch=dispatched[0].dispatched_at if dispatched else None,
        next_schedules=upcoming[:UPCOMING],
        watchers=watchers,
    )
