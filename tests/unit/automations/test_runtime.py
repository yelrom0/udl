"""
-------------------
- test_runtime.py -
-------------------

The automations' runtime (in `udl serve`), on a FakeClock: it runs the automations only while it
holds the `automations` lease (so one udl runs them per database), dispatches what is published
(at once when it is published here, else within dispatch_interval) and runs it; it will not
start with a scripts_dir that udl writes to. Downloads that end publish udl.download.*. What
get_status says comes from the database, so any udl can say who runs the automations.
"""

# System Imports
import asyncio
import os
import socket
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Package Imports
import pytest

# Local Imports
from support.accounts import connected, with_fake_integration
from support.actions import FakeAction, fake_action
from support.clock import FakeClock
from support.downloaders import FakeMedia
from support.engine import script
from support.interfaces import A, Harness
from udl.automations.bus import NewEvent
from udl.automations.events import EventRecord, EventStatus
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.runtime import LEASE, AutomationRuntime, AutomationState, runtime_status
from udl.automations.schedules import AtSpec, EverySpec, Schedule
from udl.automations.subscriptions import Subscription
from udl.config import APISettings, AutomationsSettings
from udl.core.models import DownloadRequest
from udl.core.service import UDLService
from udl.db.records import Lease, RecordQuery
from udl.errors import ConfigError, DatabaseError
from udl.integrations.watchers import PluginState, WatcherState
from udl.plugins.actions.base import Action
from udl.plugins.integrations.base import ConnectMethod
from udl.plugins.registry import ACTIONS


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake() -> type[FakeAction]:
    return fake_action("fake")


def harness_of(tmp_path: Path, clock: FakeClock, fake: type[FakeAction], **fields: Any) -> Harness:
    fields.setdefault("automations", AutomationsSettings(scripts_dir=tmp_path / "scripts"))
    found = Harness(tmp_path, clock=clock, **fields)
    found.registry.register(ACTIONS, fake)
    return found


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock, fake: type[FakeAction]) -> Harness:
    return harness_of(tmp_path, clock, fake)


@pytest.fixture
async def service(harness: Harness) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        yield found


async def greet(service: UDLService) -> None:
    await service.db.upsert_record(Action(name="hello", plugin="fake", options={"output": 1}))
    await service.db.upsert_record(
        Subscription(name="greet", events=["custom.hello"], actions=["hello"])
    )
    service.bus.subscriptions_changed()


async def runs(service: UDLService) -> list[ActionRun]:
    return await service.db.list_records(ActionRun, RecordQuery())


async def leases(service: UDLService) -> list[Lease]:
    return await service.db.list_records(Lease, RecordQuery(where={"name": LEASE}))


async def until(check: Any, clock: FakeClock, *, step: timedelta = timedelta(seconds=1)) -> None:
    """Move the clock on, a step at a time, until `check()` is true (or fail after 100)."""
    for _ in range(100):
        if await check():
            return
        await clock.advance(step)
        await asyncio.sleep(0)
    raise AssertionError("it never happened")


# ---------------------------------------------------------------------------------------------
# The lease
# ---------------------------------------------------------------------------------------------


async def test_the_runtime_runs_the_automations_while_it_holds_the_lease(
    service: UDLService, clock: FakeClock
) -> None:
    async with AutomationRuntime(service, holder="here:1:a") as runtime:
        await clock.advance(timedelta(0))

        [lease] = await leases(service)
        assert lease.holder == "here:1:a"
        assert runtime.active
        assert service.runtime is runtime
        assert service.runner.holder == "here:1:a"

    assert service.runtime is None
    assert await leases(service) == []  # (released: another may take it at once)


async def test_a_second_runtime_waits_for_the_lease(harness: Harness, clock: FakeClock) -> None:
    async with (
        harness.service() as first,
        harness.service() as second,
        AutomationRuntime(first, holder="first:1:a") as one,
    ):
        await clock.advance(timedelta(0))
        async with AutomationRuntime(second, holder="second:2:b") as two:
            await clock.advance(timedelta(seconds=10))

            assert (one.active, two.active) == (True, False)
            status = await runtime_status(second)
            assert status.automations is AutomationState.ELSEWHERE
            assert status.holder == "first:1:a"

            await one.stop()  # (closing releases the lease)
            await until(lambda: _active(two), clock)

            assert (await runtime_status(second)).automations is AutomationState.RUNNING


async def _active(runtime: AutomationRuntime) -> bool:
    return runtime.active


async def test_a_runtime_takes_over_a_lease_its_holder_stopped_renewing(
    harness: Harness, clock: FakeClock
) -> None:
    async with harness.service() as service:
        await service.db.claim_lease(LEASE, "crashed:9:z", timedelta(seconds=30))
        async with AutomationRuntime(service, holder="new:1:a") as runtime:
            await clock.advance(timedelta(seconds=20))
            assert not runtime.active

            await until(lambda: _active(runtime), clock)

            [lease] = await leases(service)
            assert lease.holder == "new:1:a"


async def test_a_runtime_that_loses_its_lease_stops_the_automations(
    service: UDLService, clock: FakeClock, fake: type[FakeAction]
) -> None:
    await service.db.upsert_record(Action(name="hello", plugin="fake", options={"outcome": "wait"}))
    await service.db.upsert_record(
        Subscription(name="greet", events=["custom.hello"], actions=["hello"])
    )
    async with AutomationRuntime(service, holder="here:1:a") as runtime:
        await clock.advance(timedelta(0))
        await service.bus.publish(NewEvent(type="custom.hello"))
        await until(lambda: _running(service), clock, step=timedelta(0))

        [lease] = await leases(service)  # (another udl took it: say this one's clock was off)
        await service.db.upsert_record(lease.model_copy(update={"holder": "thief:2:b"}))
        await until(lambda: _not(runtime), clock)

        [run] = await runs(service)
        assert run.status is RunStatus.FAILED  # (interrupted)


async def _running(service: UDLService) -> bool:
    return any(run.status is RunStatus.RUNNING for run in await runs(service))


async def _not(runtime: AutomationRuntime) -> bool:
    return not runtime.active


async def test_runs_a_crashed_runtime_left_running_are_interrupted(
    service: UDLService, clock: FakeClock
) -> None:
    left = await service.db.upsert_record(
        ActionRun(
            action_id=(await service.db.upsert_record(Action(name="x", plugin="fake"))).id,
            action="x",
            plugin="fake",
            status=RunStatus.RUNNING,
            runner="crashed:9:z",
        )
    )

    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))
        found = await service.db.get_record(ActionRun, left.id)

    assert found is not None
    assert found.status is RunStatus.FAILED


# ---------------------------------------------------------------------------------------------
# Dispatching and running
# ---------------------------------------------------------------------------------------------


async def test_what_is_published_here_is_dispatched_and_run_at_once(
    service: UDLService, clock: FakeClock, fake: type[FakeAction]
) -> None:
    await greet(service)
    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))

        await service.bus.publish(NewEvent(type="custom.hello"))
        await clock.advance(timedelta(0))
        await service.runner.idle()

        [run] = await runs(service)
        assert run.status is RunStatus.SUCCEEDED


async def test_what_another_udl_published_is_dispatched_within_the_interval(
    harness: Harness, clock: FakeClock, fake: type[FakeAction]
) -> None:
    async with harness.service() as server, harness.service() as cli:
        await greet(server)
        async with AutomationRuntime(server, holder="here:1:a"):
            await clock.advance(timedelta(0))

            await cli.bus.publish(NewEvent(type="custom.hello"))
            await clock.advance(timedelta(seconds=1))
            assert await runs(server) == []

            await clock.advance(timedelta(seconds=1))  # (dispatch_interval: 2s)
            await server.runner.idle()

            [run] = await runs(server)
            assert run.status is RunStatus.SUCCEEDED
            assert (await runtime_status(cli)).last_dispatch == clock.now()


async def test_the_runtime_keeps_going_when_the_database_fails(
    service: UDLService,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing() -> int:
        raise RuntimeError("the disk is full")

    async with AutomationRuntime(service, holder="here:1:a") as runtime:
        monkeypatch.setattr(service.bus, "dispatch_pending", failing)
        await clock.advance(timedelta(seconds=4))

        assert runtime.active
        assert "the disk is full" in caplog.text


async def test_the_runtime_fires_the_schedules_and_runs_what_they_start(
    service: UDLService, clock: FakeClock, fake: type[FakeAction]
) -> None:
    await service.db.upsert_record(Action(name="hello", plugin="fake", options={"output": 1}))
    await service.db.upsert_record(
        Subscription(name="ticks", events=["schedule.fired"], actions=["hello"])
    )
    await service.db.upsert_record(
        Schedule(name="tick", spec=EverySpec(every=timedelta(minutes=1))).planned(
            clock.now(), ZoneInfo("UTC")
        )
    )
    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))

        await clock.advance(timedelta(minutes=1))
        await service.runner.idle()

        [run] = await runs(service)
        assert (run.status, run.event_type) == (RunStatus.SUCCEEDED, "schedule.fired")


# ---------------------------------------------------------------------------------------------
# What it will not start with
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("inside", ["downloads", "roots"])
async def test_a_scripts_dir_udl_writes_to_is_refused(
    tmp_path: Path, clock: FakeClock, fake: type[FakeAction], inside: str
) -> None:
    folder = tmp_path / inside
    harness = harness_of(
        tmp_path,
        clock,
        fake,
        download_dir=tmp_path / "downloads",
        api=APISettings(allowed_output_roots=[tmp_path / "roots"]),
        automations=AutomationsSettings(scripts_dir=folder / "scripts"),
    )

    async with harness.service() as service:
        with pytest.raises(ConfigError, match="scripts_dir"):
            async with AutomationRuntime(service):
                pass


# ---------------------------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------------------------


async def test_a_download_that_ends_publishes_its_end(tmp_path: Path, clock: FakeClock) -> None:
    gone = "https://example.com/gone"
    harness = Harness(
        tmp_path,
        clock=clock,
        tools={"http": script({A: "a"}, errors={gone: "not_found"})},
    )
    async with harness.service() as service:
        await service.download(DownloadRequest(urls=[A, gone], wait=True))

        found = {
            record.type: record
            for record in await service.db.list_records(EventRecord, RecordQuery())
        }

    finished, failed = found["udl.download.finished"], found["udl.download.failed"]
    assert finished.data["url"] == A
    assert finished.data["files"]
    assert failed.data["url"] == gone
    assert failed.data["error"] is not None


async def test_a_cancelled_download_publishes_nothing(tmp_path: Path, clock: FakeClock) -> None:
    harness = Harness(tmp_path, clock=clock, tools={"http": script({A: FakeMedia(delay=0.2)})})
    async with harness.service() as service:
        batch = await service.download(DownloadRequest(urls=[A], wait=False))
        await asyncio.sleep(0.05)
        [cancelled] = await service.jobs.cancel(batch.id)

        found = await service.db.list_records(EventRecord, RecordQuery())

    assert cancelled.status == "cancelled"
    assert found == []


async def test_a_download_whose_end_cannot_be_published_still_ends(
    tmp_path: Path,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def gone(new: NewEvent) -> None:
        raise DatabaseError("The database went away")

    async with Harness(tmp_path, clock=clock).service() as service:
        monkeypatch.setattr(service.bus, "publish", gone)
        batch = await service.download(DownloadRequest(urls=[A], wait=True))

    assert [job.status for job in batch.jobs] == ["finished"]
    assert "The database went away" in caplog.text


# ---------------------------------------------------------------------------------------------
# What get_status says
# ---------------------------------------------------------------------------------------------


async def test_the_status_says_what_waits(service: UDLService, clock: FakeClock) -> None:
    action = await service.db.upsert_record(Action(name="x", plugin="fake"))
    for status in (
        RunStatus.QUEUED,
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.AWAITING_APPROVAL,
    ):
        await service.db.upsert_record(
            ActionRun(action_id=action.id, action="x", plugin="fake", status=status)
        )
    await service.bus.publish(NewEvent(type="custom.hello"))

    status = await runtime_status(service)

    assert status.automations is AutomationState.STOPPED
    assert (status.queued_runs, status.running_runs, status.waiting_runs) == (2, 1, 1)
    assert status.pending_events == 1
    assert (status.holder, status.lease_expires_at, status.last_dispatch) == (None, None, None)


async def test_the_status_says_the_next_five_schedules(
    service: UDLService, clock: FakeClock
) -> None:
    for n in (6, 3, 1, 5, 2, 4):
        await service.db.upsert_record(
            Schedule(
                name=f"in-{n}h",
                spec=AtSpec(at=clock.now() + timedelta(hours=n)),
                next_run_at=clock.now() + timedelta(hours=n),
            )
        )
    await service.db.upsert_record(
        Schedule(
            name="off",
            spec=AtSpec(at=clock.now()),
            next_run_at=clock.now(),
            enabled=False,
        )
    )

    status = await runtime_status(service)

    assert [(found.name, found.next_run_at) for found in status.next_schedules] == [
        (f"in-{n}h", clock.now() + timedelta(hours=n)) for n in range(1, 6)
    ]


async def test_the_status_says_when_automations_are_disabled(tmp_path: Path) -> None:
    harness = Harness(tmp_path, automations=AutomationsSettings(enabled=False))

    async with harness.service() as service:
        assert (await runtime_status(service)).automations is AutomationState.DISABLED


async def test_the_status_says_this_udl_runs_them(service: UDLService, clock: FakeClock) -> None:
    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))

        status = await runtime_status(service)

    assert status.automations is AutomationState.RUNNING
    assert (status.holder, status.here) == ("here:1:a", "here:1:a")
    assert status.lease_expires_at == clock.now() + timedelta(seconds=30)


async def test_a_lease_that_expired_is_stopped(service: UDLService, clock: FakeClock) -> None:
    await service.db.claim_lease(LEASE, "crashed:9:z", timedelta(seconds=30))
    await clock.advance(timedelta(minutes=1))

    status = await runtime_status(service)

    assert status.automations is AutomationState.STOPPED
    assert status.holder == "crashed:9:z"


def test_a_runtime_names_its_host_and_process() -> None:
    holder = AutomationRuntime.name_holder()

    host, pid, random = holder.split(":")
    assert (host, pid) == (socket.gethostname(), str(os.getpid()))
    assert random
    assert AutomationRuntime.name_holder() != holder


# ---------------------------------------------------------------------------------------------
# When things fail
# ---------------------------------------------------------------------------------------------


async def test_a_lease_that_cannot_be_claimed_runs_nothing(
    service: UDLService,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def gone(*args: Any) -> None:
        raise DatabaseError("The database went away")

    monkeypatch.setattr(service.db, "claim_lease", gone)

    async with AutomationRuntime(service, holder="here:1:a") as runtime:
        await clock.advance(timedelta(0))

        assert not runtime.active
        assert "The database went away" in caplog.text


async def test_runs_that_cannot_be_read_are_left_as_they_are(
    service: UDLService,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def gone() -> int:
        raise DatabaseError("The database went away")

    monkeypatch.setattr(service.runner, "recover", gone)

    async with AutomationRuntime(service, holder="here:1:a") as runtime:
        await clock.advance(timedelta(0))

        assert runtime.active
        assert "The database went away" in caplog.text


async def test_pruning_that_fails_is_tried_again_later(
    service: UDLService,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tries: list[object] = []

    async def failing(*args: Any) -> int:
        tries.append(args)
        raise RuntimeError("the disk is full")

    monkeypatch.setattr("udl.automations.runtime.prune_history", failing)

    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(hours=1))

    assert len(tries) == 2
    assert "the disk is full" in caplog.text


async def test_old_history_is_pruned_every_hour(service: UDLService, clock: FakeClock) -> None:
    old = await service.bus.publish(NewEvent(type="custom.hello"))
    assert old is not None
    await service.db.upsert_record(old.model_copy(update={"status": EventStatus.DISPATCHED}))

    await clock.advance(timedelta(days=31))

    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(0))  # (it prunes as it starts, then every hour)

        assert await service.db.get_record(EventRecord, old.id) is None


async def test_a_runner_that_fails_is_started_again(
    service: UDLService,
    clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    served: list[int] = []

    async def failing_once(stop: asyncio.Event) -> None:
        served.append(1)
        if len(served) == 1:
            raise RuntimeError("a bug in the runner")
        await stop.wait()

    monkeypatch.setattr(service.runner, "serve", failing_once)

    async with AutomationRuntime(service, holder="here:1:a"):
        await clock.advance(timedelta(seconds=2))

    assert len(served) == 2
    assert "a bug in the runner" in caplog.text


# ---------------------------------------------------------------------------------------------
# Watchers
# ---------------------------------------------------------------------------------------------


async def test_the_runtime_runs_the_watchers_and_their_events_runs(
    tmp_path: Path, clock: FakeClock, fake: type[FakeAction]
) -> None:
    harness = harness_of(tmp_path, clock, fake)
    fakes = with_fake_integration(harness)
    async with harness.service() as service:
        await service.db.upsert_record(Action(name="hello", plugin="fake"))
        work = await connected(service, method=ConnectMethod.NONE, options={"source": "x"})
        await service.db.upsert_record(
            Subscription(name="things", events=["fake.*"], actions=["hello"])
        )
        async with AutomationRuntime(service, holder="here:1:a"):
            await clock.advance(timedelta(0))
            fakes.plugin.feed("work").add("report")

            await until(lambda: _ran(service), clock)
            status = await runtime_status(service)

        [watcher] = status.watchers
        assert (watcher.account_id, watcher.state) == (work.id, WatcherState.RUNNING)
        [ran] = fake.ran
        assert ran.event is not None
        assert ran.event.account_id == work.id
        stopped = await runtime_status(service)
        assert [found.state for found in stopped.watchers] == [WatcherState.STOPPED]


async def _ran(service: UDLService) -> bool:
    return any(run.status is RunStatus.SUCCEEDED for run in await runs(service))


async def test_watchers_are_shown_stopped_while_nothing_runs_the_automations(
    tmp_path: Path, clock: FakeClock, fake: type[FakeAction]
) -> None:
    harness = harness_of(tmp_path, clock, fake)
    with_fake_integration(harness)
    async with harness.service() as service:
        work = await connected(service, method=ConnectMethod.NONE, options={"source": "x"})
        await service.db.upsert_record(
            PluginState(plugin="fake", account_id=work.id, watcher=WatcherState.RUNNING)
        )

        status = await runtime_status(service)

    assert status.automations is AutomationState.STOPPED
    assert [found.state for found in status.watchers] == [WatcherState.STOPPED]
