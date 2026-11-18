"""
------------
- apps.py -
------------

The apps' services, run by the runtime that holds the automations' lease (udl.automations.runtime):
each app of the udl.apps kind is started once per lease, stopped when the lease goes, and started
again (with the watchers' backoff) when its service raises. What each is doing is what get_status
says (RuntimeStatus.apps).

An app's service is given an AppContext (udl.plugins.apps.base): the service to call operations
with, its records, its events, the secret store, the clock, its settings, and what stops it. An
app is made from its settings (its `[apps.<name>]` table, then what was set through udl), as an
integration is.
"""

# System Imports
import asyncio
import logging
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, NonNegativeInt

# Local Imports
from udl.clock import pause
from udl.errors import ErrorCode, ErrorInfo, UDLError
from udl.plugins.apps.base import AppContext, AppEvents, AppPlugin, AppRecords
from udl.plugins.registry import APPS

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)

FIRST_BACKOFF: Final = timedelta(seconds=1)
MAX_BACKOFF: Final = timedelta(minutes=5)
HEALTHY_AFTER: Final = timedelta(minutes=10)
"""How long an app's service runs without failing for its next failure to start a new streak."""
STOP_GRACE: Final = 5.0
"""Seconds an app's service gets to stop where it waits, before it is cancelled."""


class AppState(StrEnum):
    """Where an app's service is."""

    RUNNING = "running"  # it is running (and has not failed)
    BACKOFF = "backoff"  # it raised: it is started again after a while
    STOPPED = "stopped"  # nothing runs it (no udl holds the automations' lease), or it ended
    FAILED = "failed"  # its settings or its plugin are not usable


class AppStatus(BaseModel):
    """An app, and what its service is doing."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    name: str
    """The app's name (e.g. orch)."""
    state: AppState
    """Where its service is."""
    failures: NonNegativeInt = 0
    """How many times it has failed in a row (since it last ran HEALTHY_AFTER)."""
    last_error: ErrorInfo | None = None
    """What its service last failed with."""
    started_at: AwareDatetime | None = None
    """When the run that is running (or last ran) started."""


class AppSupervisor:
    """Runs the services of `service`'s apps (while serve() runs)."""

    def __init__(self, service: UDLService) -> None:
        self._service = service
        self._apps: dict[str, AppPlugin[Any]] = {}
        self._states: dict[str, AppStatus] = {}
        self._stops: dict[str, asyncio.Event] = {}

    def names(self) -> list[str]:
        """The apps udl has (whether or not their service runs)."""
        return [info.name for info in self._service.registry.plugins(APPS)]

    async def get(self, name: str) -> AppPlugin[Any]:
        """The app called `name`, made from its settings (once: the same app its operations run
        on, whether or not its service runs). Raises DoesNotExist, or what its settings fail
        with."""
        found = self._apps.get(name)
        return found if found is not None else await self._make(name)

    def statuses(self) -> list[AppStatus]:
        """What each app's service is doing, by name."""
        return [
            self._states.get(name) or AppStatus(name=name, state=AppState.STOPPED)
            for name in self.names()
        ]

    async def serve(self, stop: asyncio.Event) -> None:
        """Start the apps' services, and keep them running until `stop` is set; then stop them."""
        tasks = [
            asyncio.create_task(self._run(name), name=f"udl app {name}") for name in self.names()
        ]
        try:
            await stop.wait()
        finally:
            # (Told to stop, so a service that ends where it waits is not cut off: they get
            # STOP_GRACE, and only what is left of them is cancelled.)
            for stop in self._stops.values():
                stop.set()
            late: set[asyncio.Task[None]] = set()
            if tasks:
                _, late = await asyncio.wait(tasks, timeout=STOP_GRACE)
                for task in late:
                    task.cancel()
                await asyncio.wait(tasks)

    # -- one app -------------------------------------------------------------------------------

    async def _run(self, name: str) -> None:
        """Run the app called `name`'s service: once, then again after a breath whenever it
        raises (with backoff). A service that ends is an app that is done: it is not started
        again until the next lease."""
        try:
            app = await self._make(name)
        except Exception as e:  # noqa: BLE001 - a broken app is reported, not fatal
            log.warning("The %s app cannot start: %s", name, e)
            self._states[name] = AppStatus(name=name, state=AppState.FAILED, last_error=_error(e))
            return
        backoff, failures = FIRST_BACKOFF, 0
        stop = self._stops.setdefault(name, asyncio.Event())
        while not stop.is_set():
            started = self._service.clock.now()
            ctx = AppContext(
                app=app,
                service=self._service,
                records=AppRecords(self._service.db),
                events=AppEvents(self._service.bus, app.name),
                secrets=self._service.secrets,
                clock=self._service.clock,
                settings=app.settings,
                stop=stop,
            )
            self._states[name] = AppStatus(
                name=name, state=AppState.RUNNING, failures=failures, started_at=started
            )
            try:
                await app.serve(ctx)
            except asyncio.CancelledError:
                stop.set()
                raise
            except Exception as e:  # noqa: BLE001 - a service that fails is started again
                if self._service.clock.now() - started >= HEALTHY_AFTER:  # (a new streak)
                    backoff, failures = FIRST_BACKOFF, 0
                failures += 1
                log.warning(
                    "The %s app's service failed (%d in a row), and is started again in %s: %s",
                    name,
                    failures,
                    backoff,
                    e,
                )
                self._states[name] = AppStatus(
                    name=name,
                    state=AppState.BACKOFF,
                    failures=failures,
                    last_error=_error(e),
                    started_at=started,
                )
                await pause(self._service.clock, backoff, stop)
                continue
            # (It ended by itself: the app is done, and is reported as stopped.)
            self._states[name] = AppStatus(name=name, state=AppState.STOPPED)
            return

    async def _make(self, name: str) -> AppPlugin[Any]:
        """The app called `name`, made from its settings and kept (its settings' secrets readable).
        Raises DoesNotExist for an app udl does not have, or what its settings fail with."""
        plugin = self._service.registry.get(APPS, name)
        app = plugin(await self._service.plugin_settings(name), clock=self._service.clock)
        self._apps[name] = app
        return app


def _error(what: BaseException | None) -> ErrorInfo | None:
    """`what` as an error udl can show (a bug as itself, not a traceback)."""
    if what is None:
        return None
    if isinstance(what, UDLError):
        return what.to_info()
    return ErrorInfo(code=ErrorCode.PLUGIN_FAILED, message=f"{type(what).__name__}: {what}")
