"""
------------------------
- test_runtime_apps.py -
-------------------------

The apps' services in the runtime: an app of the udl.apps kind is started once per lease, is
started again after its service raises (with the watchers' backoff), and is stopped when the
runtime stops or loses the lease; what each is doing is what get_status says
"""

# System Imports
import asyncio
import inspect
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

# Package Imports
import pytest

# Local Imports
from support.apps import FakeApp, FakeAppSettings
from support.clock import FakeClock
from support.interfaces import Harness
from udl.automations.apps import AppState, AppStatus
from udl.automations.runtime import AutomationRuntime, runtime_status
from udl.config import AutomationsSettings
from udl.core.models import SetPluginSettingsRequest
from udl.core.service import UDLService
from udl.errors import ConfigError
from udl.integrations.watchers import HEALTHY_AFTER, MAX_BACKOFF
from udl.plugins.apps.base import AnApp
from udl.plugins.registry import APPS


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def harness_of(tmp_path: Path, clock: FakeClock, *apps: type[AnApp]) -> Harness:
    """A harness whose apps are `apps` (and the fake one), registered before any service is
    made (a service reads the plugins it has when it is made)."""
    found = Harness(
        tmp_path,
        clock=clock,
        automations=AutomationsSettings(scripts_dir=tmp_path / "scripts"),
    )
    for app in (FakeApp, *apps):
        found.registry.register(APPS, app)
    return found


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock) -> Harness:
    return harness_of(tmp_path, clock)


def app_of(service: UDLService, name: str = FakeApp.name) -> Any:
    """The app called `name` the supervisor made (the tests read what its service did)."""
    return service.apps._apps[name]  # pyright: ignore[reportPrivateUsage]


def states(service: UDLService) -> dict[str, AppStatus]:
    """What each app's service is doing, by name."""
    return {found.name: found for found in service.apps.statuses()}


async def until(
    check: Callable[[], object], clock: FakeClock, *, step: timedelta = timedelta(seconds=1)
) -> None:
    """Move the clock on, a step at a time, until `check()` is true (or fail after 100)."""
    for _ in range(100):
        found = check()
        if await found if inspect.isawaitable(found) else found:
            return
        await clock.advance(step)
        await asyncio.sleep(0)
    raise AssertionError("it never happened")


# ---------------------------------------------------------------------------------------------
# Started once per lease
# ---------------------------------------------------------------------------------------------


async def test_an_app_s_service_is_started_once_per_lease(
    harness: Harness, clock: FakeClock
) -> None:
    async with harness.service() as service:
        async with AutomationRuntime(service, holder="here:1:a"):
            await clock.advance(timedelta(0))
            app = app_of(service)

            assert app.started == 1
            await until(lambda: _ticks(app, 1), clock)
            await until(lambda: _ticks(app, 2), clock)
            assert app.started == 1  # (started once, not once per tick)

        assert app.stopped


async def test_an_app_s_service_is_stopped_when_the_lease_goes(
    harness: Harness, clock: FakeClock
) -> None:
    async with (
        harness.service() as first,
        harness.service() as second,
        AutomationRuntime(first, holder="first:1:a") as one,
    ):
        await clock.advance(timedelta(0))
        one_app = app_of(first)

        async with AutomationRuntime(second, holder="second:2:b"):
            await clock.advance(timedelta(seconds=10))
            await one.stop()  # (releasing the lease)

            await until(lambda: states(second)[FakeApp.name].state is AppState.RUNNING, clock)

        assert one_app.stopped


async def test_an_app_s_service_starts_again_with_the_next_lease(
    harness: Harness, clock: FakeClock
) -> None:
    async with (
        harness.service() as first,
        harness.service() as second,
        AutomationRuntime(first, holder="first:1:a") as one,
    ):
        await clock.advance(timedelta(0))
        await until(lambda: _ticks(app_of(first), 1), clock)

        await one.stop()
        await until(lambda: states(first)[FakeApp.name].state is AppState.STOPPED, clock)
        async with AutomationRuntime(second, holder="second:2:b"):
            await clock.advance(timedelta(seconds=10))
            await until(lambda: states(second)[FakeApp.name].state is AppState.RUNNING, clock)

            # (The next lease's app is a new one: it ticks from the start again.)
            assert app_of(second).started == 1
            await until(lambda: _ticks(app_of(second), 1), clock)
            assert states(second)[FakeApp.name].failures == 0


async def test_an_app_s_service_is_not_started_without_the_lease(
    harness: Harness, clock: FakeClock
) -> None:
    async with (
        harness.service() as first,
        harness.service() as second,
        AutomationRuntime(first, holder="first:1:a"),
    ):
        await clock.advance(timedelta(0))

        async with AutomationRuntime(second, holder="second:2:b"):
            await clock.advance(timedelta(seconds=10))

            assert states(second) == {
                FakeApp.name: AppStatus(name=FakeApp.name, state=AppState.STOPPED)
            }


# ---------------------------------------------------------------------------------------------
# Started again after a crash
# ---------------------------------------------------------------------------------------------


class Crashing(FakeApp):
    """A fake app whose service raises the first time it runs."""

    name = "crashing"
    runs = 0

    async def serve(self, ctx: Any) -> None:
        type(self).runs += 1
        if type(self).runs == 1:
            raise RuntimeError("the first run fails")
        await super().serve(ctx)


class AlwaysFailing(FakeApp):
    """A fake app whose service raises at once, every time it is started."""

    name = "failing"
    at: ClassVar[list[Any]] = []

    async def serve(self, ctx: Any) -> None:
        type(self).at.append(ctx.clock.now())
        raise RuntimeError("it always fails")


class FailingOnce(FakeApp):
    """A fake app that fails at once until it has been started `healthy` times."""

    name = "flaky"
    runs = 0
    healthy = 0

    async def serve(self, ctx: Any) -> None:
        type(self).runs += 1
        if type(self).runs <= type(self).healthy:
            raise RuntimeError("it fails")
        await super().serve(ctx)


class Broken(FakeApp):
    """A fake app that cannot be made at all."""

    name = "broken"

    def __init__(self, settings: FakeAppSettings, *, clock: Any) -> None:
        raise ConfigError("this app has no settings today")


async def test_an_app_s_service_is_started_again_after_it_raises(
    tmp_path: Path, clock: FakeClock
) -> None:
    Crashing.runs = 0
    harness = harness_of(tmp_path, clock, Crashing)

    async with harness.service() as service, AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))
        app = app_of(service, Crashing.name)

        await until(lambda: Crashing.runs == 2, clock)

        assert app.started == 1  # (the second run got past the crash and into its loop)
        [crashing] = [
            found for found in (await runtime_status(service)).apps if found.name == Crashing.name
        ]
        assert crashing.state is AppState.RUNNING
        assert crashing.failures == 1


async def test_an_app_that_cannot_be_made_is_reported_not_fatal(
    tmp_path: Path, clock: FakeClock
) -> None:
    harness = harness_of(tmp_path, clock, Broken)

    async with harness.service() as service, AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))

        found = states(service)

        assert found[Broken.name].state is AppState.FAILED
        assert found[Broken.name].last_error is not None
        # (The other app runs all the same.)
        assert found[FakeApp.name].state is AppState.RUNNING


def _ticks(app: Any, count: int) -> bool:
    return len(app.done) >= count


# ---------------------------------------------------------------------------------------------
# The backoff
# ---------------------------------------------------------------------------------------------


async def test_a_failing_app_is_started_again_more_and_more_slowly(
    tmp_path: Path, clock: FakeClock
) -> None:
    AlwaysFailing.at = []
    harness = harness_of(tmp_path, clock, AlwaysFailing)

    async with harness.service() as service, AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))
        await until(lambda: len(AlwaysFailing.at) >= 4, clock, step=timedelta(seconds=30))

    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(AlwaysFailing.at, AlwaysFailing.at[1:], strict=False)
    ]

    assert gaps[:3] == [1, 2, 4]  # (the watchers' backoff, doubled each time)


async def test_a_failing_app_is_never_kept_waiting_longer_than_the_cap(
    tmp_path: Path, clock: FakeClock
) -> None:
    AlwaysFailing.at = []
    harness = harness_of(tmp_path, clock, AlwaysFailing)

    async with harness.service() as service, AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))
        await until(lambda: len(AlwaysFailing.at) >= 12, clock, step=timedelta(minutes=2))

    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(AlwaysFailing.at, AlwaysFailing.at[1:], strict=False)
    ]

    assert max(gaps) == MAX_BACKOFF.total_seconds()
    assert gaps[-1] == MAX_BACKOFF.total_seconds()


async def test_an_app_that_ran_well_starts_again_at_once_after_it_fails(
    tmp_path: Path, clock: FakeClock
) -> None:
    FailingOnce.runs = 0
    FailingOnce.healthy = 1
    harness = harness_of(tmp_path, clock, FailingOnce)

    async with harness.service() as service, AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))
        # (It fails once, waits FIRST_BACKOFF, then runs for HEALTHY_AFTER: the next failure
        # starts a new streak, at FIRST_BACKOFF again.)
        await clock.advance(HEALTHY_AFTER + timedelta(seconds=5))
        app = app_of(service, FailingOnce.name)

        assert FailingOnce.runs >= 2
        assert app.started >= 1


async def test_a_changed_setting_is_picked_up_by_the_next_start(
    harness: Harness, clock: FakeClock
) -> None:
    async with harness.service() as service:
        async with AutomationRuntime(service, holder="here:1:a"):
            await clock.advance(timedelta(0))
            await until(lambda: _ticks(app_of(service), 1), clock)

            await service.set_plugin_settings(
                SetPluginSettingsRequest(name=FakeApp.name, values={"loud": True}, reset=[])
            )
            await service.apps.get(FakeApp.name)  # (the settings are read again)

        assert app_of(service).settings.loud
