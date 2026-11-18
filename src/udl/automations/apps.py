"""
-----------
- apps.py -
------------

The apps' services, run by the runtime that holds the automations' lease (udl.automations.runtime):
each app of the udl.apps kind is started once per lease, stopped when the lease goes, and started
again (with the watchers' backoff) when its service raises. What each is doing is what get_status
says (RuntimeStatus.apps).

An app's service is given an AppContext (udl.plugins.apps.base): the service to call operations
with, its records, its events, the secret store, the clock, its settings, and what stops it. An
app is made from its settings (its `[apps.<name>]` table, then what was set through udl), as an
integration is, and the same app is what its operations run on (get()).
"""

# System Imports
import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING, Final

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, NonNegativeInt

# Local Imports
from udl.clock import pause
from udl.errors import ConfigError, ErrorCode, ErrorInfo, UDLError
from udl.integrations.watchers import FIRST_BACKOFF, HEALTHY_AFTER, MAX_BACKOFF
from udl.plugins.apps.base import AnApp, AppContext, AppEvents, AppRecords
from udl.plugins.registry import APPS

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)

STOP_GRACE: Final = 2.0
"""Seconds an app's service gets to stop where it waits, before it is cancelled. Less than the
runtime's own grace, so the supervisor always gets to tell them and wait."""


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
        self._apps: dict[str, AnApp] = {}
        self._states: dict[str, AppStatus] = {}

    def names(self) -> list[str]:
        """The apps udl has (whether or not their service runs)."""
        return [info.name for info in self._service.registry.plugins(APPS)]

    def statuses(self) -> list[AppStatus]:
        """What each app's service is doing, by name."""
        return [
            self._states.get(name) or AppStatus(name=name, state=AppState.STOPPED)
            for name in self.names()
        ]

    async def get(self, name: str) -> AnApp:
        """The app called `name`, made from its settings (once: the same app its operations run
        on, whether or not its service runs). Raises DoesNotExist, or what its settings fail
        with."""
        found = self._apps.get(name)
        return found if found is not None else await self._make(name)

    def changed(self, name: str) -> None:
        """Make the app called `name` again the next time it is asked for (its settings
        changed): its operations get the new settings at once, while its running service keeps
        the settings it started with until the next lease (as an integration's watcher does)."""
        self._apps.pop(name, None)

    async def serve(self, stop: asyncio.Event) -> None:
        """Start the apps' services, and keep them running until `stop` is set; then stop them.

        Each lease gets its own stop events: an app whose service ended is started again with the
        next lease, not left stopped."""
        stops = {name: asyncio.Event() for name in self.names()}
        self._states = {name: AppStatus(name=name, state=AppState.STOPPED) for name in stops}
        tasks = [
            asyncio.create_task(self._run(name, stops[name]), name=f"udl app {name}")
            for name in stops
        ]
        try:
            await stop.wait()
        finally:
            for app_stop in stops.values():
                app_stop.set()
            if tasks:
                _, late = await asyncio.wait(tasks, timeout=STOP_GRACE)
                for task in late:
                    task.cancel()
                await asyncio.wait(tasks)
            self._states = {name: AppStatus(name=name, state=AppState.STOPPED) for name in stops}

    # -- one app -------------------------------------------------------------------------------

    async def _run(self, name: str, stop: asyncio.Event) -> None:
        """Run the app called `name`'s service: once, then again after a breath whenever it
        raises (with backoff). A service that ends is an app that is done: it is not started
        again until the next lease."""
        try:
            app = await self.get(name)  # (the one its operations run on, and settings and all)
        except Exception as e:  # noqa: BLE001 - a broken app is reported, not fatal
            log.warning("The %s app cannot start: %s", name, e)
            self._states[name] = AppStatus(name=name, state=AppState.FAILED, last_error=_error(e))
            return
        backoff, failures = FIRST_BACKOFF, 0
        while not stop.is_set():
            started = self._service.clock.now()
            ctx = AppContext(
                app=app,
                service=self._service,
                records=AppRecords(self._service.db),
                events=AppEvents(self._service.bus, app.name, type(app).event_types()),
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
                backoff = min(backoff * 2, MAX_BACKOFF)
                continue
            # (It ended by itself: the app is done, and is reported as stopped.)
            self._states[name] = AppStatus(name=name, state=AppState.STOPPED)
            return

    async def _make(self, name: str) -> AnApp:
        """The app called `name`, made from its settings and kept (its settings' secrets readable).
        Raises DoesNotExist for an app udl does not have, or what its settings fail with."""
        plugin = self._service.registry.get(APPS, name)
        made = await self._service.plugin_settings(name)
        if not isinstance(made, plugin.settings_model):
            raise ConfigError(
                f"The {name} app's settings are {type(made).__qualname__}, not its "
                f"settings_model's ({plugin.settings_model.__qualname__})"
            )
        app: AnApp = plugin(made, clock=self._service.clock)
        self._apps[name] = app
        return app


def _error(what: BaseException | None) -> ErrorInfo | None:
    """`what` as an error udl can show (a bug as itself, not a traceback)."""
    if what is None:
        return None
    if isinstance(what, UDLError):
        return what.to_info()
    return ErrorInfo(code=ErrorCode.PLUGIN_FAILED, message=f"{type(what).__name__}: {what}")
