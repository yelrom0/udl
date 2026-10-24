"""
-------------
- runner.py -
-------------

ActionRunner: runs the runs the bus queued (in the runtime, udl.automations.runtime), and the
ones asked for (run_action: run_now(), in the udl that asks).

A queued run whose time has come starts when there is room: at most [automations]
max_concurrent_runs at once, and at most its action's max_concurrent of one action. Running it
renders the action's options with its event (udl.automations.templates), validates them by its
kind's model, and calls the action plugin, within the action's timeout, with the run as
current_run (so what it publishes is caused by it, one deeper) and the automations as the
caller (so paths are held to their roots).

A failure that trying again may help (is_retryable: ActionFailed(retryable=True), a network
error, a rate limit; a timeout when the action says so) is tried again after 10s x 2^n (at most
10 minutes, or what the failure says), until the action's retries are used. A run's end is
published: udl.action.succeeded or udl.action.failed (a cancelled or skipped run publishes
nothing). After a crash, queued runs run as before; runs that were running end as failed:
interrupted (recover(), and recover_direct() for the runs a udl that stopped had asked for).
"""

# System Imports
import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

# Local Imports
from udl.automations.bus import NewEvent
from udl.automations.events import ActionEventData, CurrentRun, EventRecord, current_run
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.templates import Templates
from udl.config import format_duration
from udl.core.operations import Caller, current_caller
from udl.db.records import RecordQuery
from udl.errors import (
    ActionFailed,
    Cancelled,
    DatabaseError,
    DoesNotExist,
    Interrupted,
    RateLimited,
    UDLError,
    is_retryable,
)
from udl.plugins.actions.base import Action, ActionContext, ActionPlugin, RunLog, template_context
from udl.plugins.registry import ACTIONS
from udl.process import CancelToken

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)

FIRST_RETRY: Final = timedelta(seconds=10)
"""How long a run waits before its first retry (each later wait is twice the last)."""
LONGEST_RETRY: Final = timedelta(minutes=10)
"""How long a run waits before a retry, at most (unless the failure says otherwise)."""
GRACE: Final = 1.0
"""Seconds a cancelled run gets to stop before its task is cancelled."""


@dataclass
class _Running:
    task: asyncio.Task[None]
    cancel: CancelToken
    action_id: UUID
    asked: bool = False  # (a run asked for: it runs whatever the limits)


class ActionRunner:
    """Runs a service's runs. `holder` names the runtime that runs them (None for runs asked
    for, which the udl that asks runs)."""

    def __init__(self, service: UDLService, *, holder: str | None = None) -> None:
        self.service = service
        self.holder = holder
        self._templates = Templates()
        self._plugins: dict[str, ActionPlugin[Any]] = {}
        self._running: dict[UUID, _Running] = {}
        self._cancelling: set[UUID] = set()
        self._woken = asyncio.Event()
        self._next_due: datetime | None = None

    @property
    def running(self) -> int:
        """How many queued runs this runner is running (runs asked for aside)."""
        return sum(1 for found in self._running.values() if not found.asked)

    def wake(self) -> None:
        """Look for queued runs now (the dispatcher queued some)."""
        self._woken.set()

    # -- running what is queued ----------------------------------------------------------------

    async def serve(self, stop: asyncio.Event | None = None) -> None:
        """Run the queued runs as their time comes (when woken, when a run ends, when a retry
        is due, and every [automations] dispatch_interval), until `stop` is set (or for ever).
        It stops between runs' starts, never in the middle of one."""
        stop = stop or asyncio.Event()
        while not stop.is_set():
            await self.run_due()
            clock = self.service.clock
            until = clock.now() + self.service.settings.automations.dispatch_interval
            if self._next_due is not None:
                until = min(until, self._next_due)
            await self._wait(until, stop)

    async def _wait(self, until: datetime, stop: asyncio.Event) -> None:
        waits = [
            asyncio.ensure_future(self._woken.wait()),
            asyncio.ensure_future(stop.wait()),
            asyncio.ensure_future(self.service.clock.sleep_until(until)),
        ]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for wait in waits:
                wait.cancel()
            self._woken.clear()

    async def run_due(self) -> int:
        """Start the queued runs whose time has come (oldest first), as far as there is room.
        Returns how many started."""
        db, clock = self.service.db, self.service.clock
        limit = self.service.settings.automations.max_concurrent_runs
        queued = await db.list_records(ActionRun, RecordQuery(where={"status": RunStatus.QUEUED}))
        now = clock.now()
        self._next_due = None
        started = 0
        for run in [run for run in reversed(queued) if run.id not in self._running]:
            if run.next_try_at is not None and run.next_try_at > now:
                self._next_due = min(filter(None, (self._next_due, run.next_try_at)))
                continue
            if self.running >= limit:
                break
            action = await db.get_record(Action, run.action_id)
            if action is None:
                gone = DoesNotExist(f"The action {run.action} was deleted before it ran")
                await self._end(run, RunStatus.FAILED, error=gone)
                continue
            if not action.enabled:
                await self._save(
                    run,
                    status=RunStatus.SKIPPED,
                    skipped_reason="the action is disabled",
                    finished_at=now,
                )
                continue
            busy = sum(
                1
                for found in self._running.values()
                if found.action_id == action.id and not found.asked
            )
            if busy >= action.max_concurrent:
                continue
            await self._start(run, action)
            started += 1
        return started

    async def _start(self, run: ActionRun, action: Action) -> None:
        running = await self._began(run, holder=self.holder)
        cancel = CancelToken()
        task = asyncio.create_task(self._execute(running, action, cancel), name=f"udl run {run.id}")
        self._running[run.id] = _Running(task, cancel, action.id)

        def ended(_: asyncio.Task[None]) -> None:
            self._running.pop(run.id, None)
            self._cancelling.discard(run.id)
            self.wake()

        task.add_done_callback(ended)

    async def idle(self) -> None:
        """Wait until nothing runs."""
        while self._running:
            await asyncio.wait([found.task for found in list(self._running.values())])

    async def shutdown(self) -> None:
        """Stop every run that runs here: each ends as failed (interrupted)."""
        for found in list(self._running.values()):
            found.task.cancel()
        await self.idle()

    # -- runs asked for ------------------------------------------------------------------------

    async def run_now(self, action: Action, event: EventRecord | None) -> ActionRun:
        """Run `action` here and now (for `event`, if any), storing it like any run. Returns it
        as it ended (or queued again, when a failure is to be tried again: the runtime does)."""
        run = await self._save(
            ActionRun(
                action_id=action.id,
                action=action.name,
                plugin=action.plugin,
                event_id=event.id if event is not None else None,
                event_type=event.type if event is not None else None,
                depth=event.depth if event is not None else 0,
            )
        )
        await (await self._start_asked(run, action, event))
        return await self._stored(run.id)

    async def _start_asked(
        self, run: ActionRun, action: Action, event: EventRecord | None
    ) -> asyncio.Task[None]:
        running = await self._began(run, holder=None)
        cancel = CancelToken()
        task = asyncio.create_task(
            self._execute(running, action, cancel, event=event), name=f"udl run {run.id}"
        )
        self._running[run.id] = _Running(task, cancel, action.id, asked=True)

        def ended(_: asyncio.Task[None]) -> None:
            self._running.pop(run.id, None)
            self._cancelling.discard(run.id)

        task.add_done_callback(ended)
        return task

    # -- cancelling ----------------------------------------------------------------------------

    async def cancel(self, run_id: UUID, *, grace: float = GRACE) -> ActionRun:
        """Cancel a run: a queued (or waiting) one at once, a running one through its cancel
        token (its task is cancelled when it has not stopped within `grace` seconds). A run
        that ended is left as it is. Raises DoesNotExist (also for a run another udl runs)."""
        run = await self._stored(run_id)
        if run.status.ended:
            return run
        found = self._running.get(run_id)
        if found is not None:
            self._cancelling.add(run_id)
            found.cancel.cancel()
            done, _ = await asyncio.wait([found.task], timeout=grace)
            if not done:
                found.task.cancel()
                await asyncio.wait([found.task])
            return await self._stored(run_id)
        if run.status in (RunStatus.QUEUED, RunStatus.AWAITING_APPROVAL):
            return await self._save(
                run, status=RunStatus.CANCELLED, finished_at=self.service.clock.now()
            )
        raise DoesNotExist(
            f"The run {run_id} is run by another udl, not this one",
            hint="Cancel it through the udl that runs it (`udl status` says which runs the "
            "automations): its API's DELETE /runs/{id}, or its MCP server's cancel_run.",
        )

    # -- crashes -------------------------------------------------------------------------------

    async def recover(self) -> int:
        """End the runs a runtime left running (it crashed, or lost its lease) as failed
        (interrupted). Returns how many."""
        return await self._recover(lambda run: run.runner is not None)

    async def recover_direct(self) -> int:
        """End the runs asked for that a udl which stopped left running (run only when no other
        udl is running: udl.core.jobs.InstanceLock). Returns how many."""
        return await self._recover(lambda run: run.runner is None)

    async def _recover(self, left: Callable[[ActionRun], bool]) -> int:
        running = await self.service.db.list_records(
            ActionRun, RecordQuery(where={"status": RunStatus.RUNNING})
        )
        stale = [run for run in running if left(run) and run.id not in self._running]
        for run in stale:
            await self._end(run, RunStatus.FAILED, error=Interrupted("udl stopped before it ended"))
        if stale:
            log.info("Marked %d runs that udl did not finish as interrupted", len(stale))
        return len(stale)

    # -- a run ---------------------------------------------------------------------------------

    async def _began(self, run: ActionRun, *, holder: str | None) -> ActionRun:
        return await self._save(
            run,
            status=RunStatus.RUNNING,
            attempt=run.attempt + 1,
            started_at=run.started_at or self.service.clock.now(),
            next_try_at=None,
            runner=holder,
        )

    async def _execute(
        self,
        run: ActionRun,
        action: Action,
        cancel: CancelToken,
        *,
        event: EventRecord | None = None,
    ) -> None:
        """Run `run` (running) to its end, or to its next try."""
        logged = RunLog()
        try:
            if event is None and run.event_id is not None:
                event = await self.service.db.get_record(EventRecord, run.event_id)
            done = await self._attempt(run, action, event, cancel, logged)
        except Cancelled:
            await self._save(
                run,
                status=RunStatus.CANCELLED,
                finished_at=self.service.clock.now(),
                log=logged.text,
            )
        except asyncio.CancelledError:
            task = asyncio.current_task()
            while task is not None and task.cancelling():
                task.uncancel()
            if run.id in self._cancelling:
                await self._save(
                    run,
                    status=RunStatus.CANCELLED,
                    finished_at=self.service.clock.now(),
                    log=logged.text,
                )
            else:
                stopped = Interrupted("udl stopped before it ended")
                await self._end(run, RunStatus.FAILED, error=stopped, logged=logged)
        except UDLError as e:
            await self._failed(run, action, e, logged)
        except Exception as e:
            log.exception("The %s action's run %s failed unexpectedly", action.name, run.id)
            await self._failed(run, action, ActionFailed(f"{type(e).__name__}: {e}"), logged)
        else:
            await self._end(
                run,
                RunStatus.SUCCEEDED,
                output=done.output,
                summary=done.summary,
                logged=logged,
            )

    async def _attempt(
        self,
        run: ActionRun,
        action: Action,
        event: EventRecord | None,
        cancel: CancelToken,
        logged: RunLog,
    ) -> Any:
        """One try of a run: its options rendered and validated, then its action."""
        service = self.service
        plugin = self._plugin(action.plugin)
        context = template_context(
            event.event() if event is not None else None,
            run={
                "id": str(run.id),
                "action": run.action,
                "subscription": run.subscription,
                "attempt": run.attempt,
            },
            now=service.clock.now(),
        )
        rendered = self._templates.render_value(action.options, context)
        options = plugin.options(rendered if isinstance(rendered, dict) else {})
        ctx = ActionContext(
            run_id=run.id,
            event=event.event() if event is not None else None,
            options=options,
            service=service,
            secrets=service.secrets,
            clock=service.clock,
            cancel=cancel,
            log=logged,
            action=action.name,
            subscription=run.subscription,
            depth=run.depth,
        )
        running = current_run.set(
            CurrentRun(
                id=run.id, depth=run.depth, action=action.name, subscription=run.subscription
            )
        )
        called = current_caller.set(Caller.AUTOMATION)
        try:
            try:
                async with service.clock.timeout(action.timeout):
                    return await plugin.run(ctx)
            except TimeoutError as e:
                cancel.cancel()
                raise ActionFailed(
                    f"It took longer than its timeout ({format_duration(action.timeout)})",
                    retryable=action.retry_timeouts,
                ) from e
        finally:
            current_caller.reset(called)
            current_run.reset(running)

    def _plugin(self, name: str) -> ActionPlugin[Any]:
        """The action plugin called `name`, made for the service. Raises PluginError."""
        if name not in self._plugins:
            self._plugins[name] = self.service.registry.get(ACTIONS, name)(self.service)
        return self._plugins[name]

    async def _failed(
        self, run: ActionRun, action: Action, error: UDLError, logged: RunLog
    ) -> None:
        """A try of `run` failed with `error`: try again later when that may help."""
        if not (is_retryable(error) and run.attempt <= action.retries):
            await self._end(run, RunStatus.FAILED, error=error, logged=logged)
            return
        given = error.retry_after if isinstance(error, ActionFailed | RateLimited) else None
        wait = (
            timedelta(seconds=given)
            if given is not None
            else min(FIRST_RETRY * 2 ** (run.attempt - 1), LONGEST_RETRY)
        )
        log.info(
            "The %s action's run %s failed (%s): trying again in %s",
            run.action,
            run.id,
            error.message,
            format_duration(wait),
        )
        await self._save(
            run,
            status=RunStatus.QUEUED,
            next_try_at=self.service.clock.now() + wait,
            error=error.to_info(),
            log=logged.text,
        )

    async def _end(
        self,
        run: ActionRun,
        status: RunStatus,
        *,
        error: UDLError | None = None,
        output: Any = None,
        summary: str = "",
        logged: RunLog | None = None,
    ) -> ActionRun:
        """End `run` as `status` (succeeded or failed), and publish its end."""
        ended = await self._save(
            run,
            status=status,
            finished_at=self.service.clock.now(),
            error=error.to_info() if error is not None else None,
            output=output,
            summary=summary,
            log=logged.text if logged is not None else run.log,
        )
        data = ActionEventData(
            run=ended.id,
            action=ended.action,
            plugin=ended.plugin,
            subscription=ended.subscription,
            event=ended.event_id,
            event_type=ended.event_type,
            attempt=max(ended.attempt, 1),
            output=ended.output,
            summary=ended.summary,
            error=ended.error,
        )
        kind = "succeeded" if status is RunStatus.SUCCEEDED else "failed"
        try:
            await self.service.bus.publish(
                NewEvent(
                    type=f"udl.action.{kind}",
                    data=data.model_dump(mode="json"),
                    source="udl",
                    caused_by=ended.id,
                    depth=ended.depth + 1,
                    dedupe_key=str(ended.id),
                )
            )
        except DatabaseError as e:
            log.warning("Could not publish the end of the run %s: %s", ended.id, e)
        return ended

    async def _save(self, run: ActionRun, **changes: Any) -> ActionRun:
        stored = await self.service.db.upsert_record(run.model_copy(update=changes))
        self.service.bus.run_changed(stored)
        return stored

    async def _stored(self, run_id: UUID) -> ActionRun:
        found = await self.service.db.get_record(ActionRun, run_id)
        if found is None:
            raise DoesNotExist(f"No run {run_id}", hint="`udl runs list` lists the runs.")
        return found
