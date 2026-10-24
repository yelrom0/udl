"""
------------------
- test_runner.py -
------------------

The action runner, on a FakeClock (so no real time passes): it runs queued runs, at most
max_concurrent_runs at once and at most an action's max_concurrent of one action; renders an
action's options with the event; tries a retryable failure again with backoff; fails a run that
takes longer than its timeout; cancels runs; and, after a crash, resumes what was queued and
says the runs that were running were interrupted. Every run's end is an event.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

# Package Imports
import pytest

# Local Imports
from support.actions import FakeAction, fake_action
from support.clock import FakeClock
from support.interfaces import Harness
from udl.automations.bus import NewEvent
from udl.automations.events import EventRecord, current_run
from udl.automations.runner import ActionRunner
from udl.automations.runs import ActionRun, RunStatus
from udl.config import AutomationsSettings
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import DatabaseError, DoesNotExist, ErrorCode
from udl.plugins.actions.base import Action
from udl.plugins.registry import ACTIONS

HOLDER = "test-host:1:abc"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake() -> type[FakeAction]:
    return fake_action("fake")


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock, fake: type[FakeAction]) -> Harness:
    found = Harness(tmp_path, clock=clock, automations=AutomationsSettings(max_concurrent_runs=2))
    found.registry.register(ACTIONS, fake)
    return found


@pytest.fixture
async def service(harness: Harness) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        yield found


@pytest.fixture
def runner(service: UDLService) -> ActionRunner:
    service.runner.holder = HOLDER
    return service.runner


async def action(service: UDLService, name: str = "hello", **fields: Any) -> Action:
    fields.setdefault("plugin", "fake")
    return await service.db.upsert_record(Action(name=name, **fields))


async def event(service: UDLService, **data: Any) -> EventRecord:
    found = await service.bus.publish(NewEvent(type="custom.hello", data={"name": "x", **data}))
    assert found is not None
    return found


async def queue(
    service: UDLService, done: Action, happened: EventRecord | None = None
) -> ActionRun:
    return await service.db.upsert_record(
        ActionRun(
            action_id=done.id,
            action=done.name,
            plugin=done.plugin,
            event_id=happened.id if happened else None,
            event_type=happened.type if happened else None,
            depth=happened.depth if happened else 0,
        )
    )


async def stored(service: UDLService, run: ActionRun) -> ActionRun:
    found = await service.db.get_record(ActionRun, run.id)
    assert found is not None
    return found


async def ended_events(service: UDLService) -> list[EventRecord]:
    found = await service.db.list_records(EventRecord, RecordQuery())
    return [record for record in reversed(found) if record.type.startswith("udl.action.")]


async def ran(runner: ActionRunner) -> None:
    """Run what is due, and wait for it to end."""
    await runner.run_due()
    await runner.idle()


# ---------------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------------


async def test_a_queued_run_runs_and_succeeds(
    service: UDLService, runner: ActionRunner, fake: type[FakeAction], clock: FakeClock
) -> None:
    done = await action(
        service,
        options={"output": {"name": "{{ event.data.name }}"}, "summary": "Done", "log": ["hi"]},
    )
    happened = await event(service)
    run = await queue(service, done, happened)

    await ran(runner)

    found = await stored(service, run)
    assert found.status is RunStatus.SUCCEEDED
    assert (found.output, found.summary, found.attempt) == ({"name": "x"}, "Done", 1)
    assert found.log == "hi\n"
    assert (found.started_at, found.finished_at) == (clock.now(), clock.now())
    assert found.runner == HOLDER
    [ctx] = fake.ran
    assert ctx.event == happened.event()
    assert (ctx.run_id, ctx.action) == (run.id, "hello")


async def test_a_run_s_end_is_an_event(service: UDLService, runner: ActionRunner) -> None:
    done = await action(service, options={"output": [1], "summary": "Done"})
    happened = await event(service)
    run = await queue(service, done, happened)

    await ran(runner)

    [ended] = await ended_events(service)
    assert ended.type == "udl.action.succeeded"
    assert (ended.caused_by, ended.depth) == (run.id, happened.depth + 1)
    assert ended.data["output"] == [1]
    assert ended.data["action"] == "hello"
    assert ended.data["event"] == str(happened.id)
    assert ended.subject == "hello succeeded: Done"


async def test_a_failed_run_s_end_says_why(service: UDLService, runner: ActionRunner) -> None:
    done = await action(service, options={"outcome": "fail", "message": "No paper"})
    run = await queue(service, done)

    await ran(runner)

    found = await stored(service, run)
    assert found.status is RunStatus.FAILED
    assert found.error is not None
    assert (found.error.code, found.error.message) == (ErrorCode.ACTION_FAILED, "No paper")
    [ended] = await ended_events(service)
    assert ended.type == "udl.action.failed"
    assert cast(dict[str, object], ended.data["error"])["message"] == "No paper"


async def test_options_that_do_not_render_fail_the_run(
    service: UDLService, runner: ActionRunner
) -> None:
    done = await action(service, options={"summary": "{{ event.data.nope }}"})
    run = await queue(service, done, await event(service))

    await ran(runner)

    found = await stored(service, run)
    assert found.status is RunStatus.FAILED
    assert found.error is not None
    assert found.error.code is ErrorCode.TEMPLATE_FAILED


async def test_a_kind_udl_does_not_have_fails_the_run(
    service: UDLService, runner: ActionRunner
) -> None:
    run = await queue(service, await action(service, plugin="nothing"))

    await ran(runner)

    found = await stored(service, run)
    assert found.status is RunStatus.FAILED
    assert found.error is not None
    assert "nothing" in found.error.message


async def test_a_bug_in_an_action_fails_its_run(
    service: UDLService, runner: ActionRunner, caplog: pytest.LogCaptureFixture
) -> None:
    run = await queue(service, await action(service, options={"outcome": "bug"}))

    await ran(runner)

    found = await stored(service, run)
    assert found.status is RunStatus.FAILED
    assert found.error is not None
    assert "a bug in the action" in found.error.message
    assert "a bug in the action" in caplog.text


async def test_a_run_whose_action_is_gone_or_disabled_does_not_run(
    service: UDLService, runner: ActionRunner, fake: type[FakeAction]
) -> None:
    gone = await action(service, "gone")
    off = await action(service, "off")
    lost = await queue(service, gone)
    skipped = await queue(service, off)
    await service.db.delete_record(Action, gone.id)
    await service.db.upsert_record(off.model_copy(update={"enabled": False}))

    await ran(runner)

    assert (await stored(service, lost)).status is RunStatus.FAILED
    found = await stored(service, skipped)
    assert (found.status, found.skipped_reason) == (RunStatus.SKIPPED, "the action is disabled")
    assert fake.ran == []


async def test_the_run_is_current_while_its_action_runs(
    service: UDLService, runner: ActionRunner, fake: type[FakeAction]
) -> None:
    seen: list[Any] = []
    original = fake.run

    async def looking(self: FakeAction, ctx: Any) -> Any:
        seen.append(current_run.get())
        return await original(self, ctx)

    fake.run = looking
    run = await queue(service, await action(service), await event(service))

    await ran(runner)

    [inside] = seen
    assert inside is not None
    assert (inside.id, inside.depth, inside.action) == (run.id, 0, "hello")
    assert current_run.get() is None


async def test_a_run_ends_though_its_end_cannot_be_published(
    service: UDLService,
    runner: ActionRunner,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing(new: NewEvent) -> None:
        raise DatabaseError("The database went away")

    monkeypatch.setattr(service.bus, "publish", failing)
    run = await queue(service, await action(service))

    await ran(runner)

    assert (await stored(service, run)).status is RunStatus.SUCCEEDED
    assert "The database went away" in caplog.text


# ---------------------------------------------------------------------------------------------
# How many at once
# ---------------------------------------------------------------------------------------------


async def test_never_more_than_max_concurrent_runs(
    service: UDLService, runner: ActionRunner, fake: type[FakeAction]
) -> None:
    runs = [
        await queue(service, await action(service, f"wait-{n}", options={"outcome": "wait"}))
        for n in range(3)
    ]

    started = await runner.run_due()

    assert started == 2
    # (Queued at the same time: which two run first is either.)
    assert sorted([(await stored(service, run)).status for run in runs]) == [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
        RunStatus.RUNNING,
    ]
    await runner.shutdown()


async def test_never_more_than_an_action_s_max_concurrent(
    service: UDLService, runner: ActionRunner
) -> None:
    one = await action(service, options={"outcome": "wait"})
    runs = [await queue(service, one) for _ in range(2)]

    assert await runner.run_due() == 1
    assert sorted([(await stored(service, run)).status for run in runs]) == [
        RunStatus.QUEUED,
        RunStatus.RUNNING,
    ]
    await runner.shutdown()


async def test_the_next_runs_once_one_ends(service: UDLService, runner: ActionRunner) -> None:
    one = await action(service, max_concurrent=1)
    runs = [await queue(service, one) for _ in range(3)]

    for _ in range(3):
        await ran(runner)

    assert [(await stored(service, run)).status for run in runs] == [RunStatus.SUCCEEDED] * 3


# ---------------------------------------------------------------------------------------------
# Trying again
# ---------------------------------------------------------------------------------------------


async def test_a_retryable_failure_is_tried_again_with_backoff(
    service: UDLService, runner: ActionRunner, clock: FakeClock, fake: type[FakeAction]
) -> None:
    run = await queue(service, await action(service, retries=3, options={"outcome": "retryable"}))
    started = clock.now()

    waits: list[timedelta] = []
    for _ in range(4):
        await ran(runner)
        found = await stored(service, run)
        if found.next_try_at is None:
            break
        waits.append(found.next_try_at - clock.now())
        await clock.advance(found.next_try_at - clock.now())

    assert waits == [timedelta(seconds=10), timedelta(seconds=20), timedelta(seconds=40)]
    found = await stored(service, run)
    assert (found.status, found.attempt) == (RunStatus.FAILED, 4)
    assert len(fake.ran) == 4
    assert clock.now() - started == timedelta(seconds=70)
    assert [ended.type for ended in await ended_events(service)] == ["udl.action.failed"]


async def test_a_queued_retry_waits_for_its_time(
    service: UDLService, runner: ActionRunner, clock: FakeClock, fake: type[FakeAction]
) -> None:
    run = await queue(service, await action(service, retries=1, options={"outcome": "retryable"}))
    await ran(runner)

    await clock.advance(timedelta(seconds=9))
    await ran(runner)

    assert len(fake.ran) == 1
    assert (await stored(service, run)).status is RunStatus.QUEUED


async def test_backoff_is_at_most_ten_minutes(
    service: UDLService, runner: ActionRunner, clock: FakeClock
) -> None:
    run = await queue(service, await action(service, retries=10, options={"outcome": "retryable"}))

    for _ in range(8):
        await ran(runner)
        found = await stored(service, run)
        assert found.next_try_at is not None
        await clock.advance(found.next_try_at - clock.now())
    await ran(runner)

    found = await stored(service, run)
    assert found.next_try_at is not None
    assert found.next_try_at - clock.now() == timedelta(minutes=10)


async def test_a_failure_s_retry_after_is_its_wait(
    service: UDLService, runner: ActionRunner, clock: FakeClock
) -> None:
    run = await queue(
        service,
        await action(service, retries=1, options={"outcome": "retryable", "retry_after": 90}),
    )

    await ran(runner)

    found = await stored(service, run)
    assert found.next_try_at == clock.now() + timedelta(seconds=90)


async def test_a_failure_that_cannot_help_is_not_tried_again(
    service: UDLService, runner: ActionRunner, fake: type[FakeAction]
) -> None:
    run = await queue(service, await action(service, retries=3, options={"outcome": "fail"}))

    await ran(runner)

    assert (await stored(service, run)).status is RunStatus.FAILED
    assert len(fake.ran) == 1


# ---------------------------------------------------------------------------------------------
# Timeouts and cancelling
# ---------------------------------------------------------------------------------------------


async def test_a_run_that_takes_too_long_fails(
    service: UDLService, runner: ActionRunner, clock: FakeClock
) -> None:
    run = await queue(service, await action(service, timeout="1m", options={"outcome": "hang"}))
    await runner.run_due()

    await clock.advance(timedelta(minutes=1))
    await runner.idle()

    found = await stored(service, run)
    assert found.status is RunStatus.FAILED
    assert found.error is not None
    assert "1m" in found.error.message


async def test_a_timeout_is_tried_again_when_the_action_says_so(
    service: UDLService, runner: ActionRunner, clock: FakeClock
) -> None:
    done = await action(
        service, timeout="1m", retries=1, retry_timeouts=True, options={"outcome": "hang"}
    )
    run = await queue(service, done)
    await runner.run_due()

    await clock.advance(timedelta(minutes=1))
    await runner.idle()

    assert (await stored(service, run)).status is RunStatus.QUEUED


async def test_cancelling_a_queued_run_ends_it_at_once(
    service: UDLService, runner: ActionRunner, fake: type[FakeAction]
) -> None:
    run = await queue(service, await action(service))

    cancelled = await runner.cancel(run.id)
    await ran(runner)

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.finished_at is not None
    assert fake.ran == []
    assert await ended_events(service) == []


async def test_cancelling_a_running_run_stops_it(service: UDLService, runner: ActionRunner) -> None:
    run = await queue(service, await action(service, options={"outcome": "wait"}))
    await runner.run_due()
    await asyncio.sleep(0.01)

    cancelled = await asyncio.wait_for(runner.cancel(run.id), 1)

    assert cancelled.status is RunStatus.CANCELLED
    assert (await stored(service, run)).status is RunStatus.CANCELLED


async def test_cancelling_a_run_that_ignores_it_stops_its_task(
    service: UDLService, runner: ActionRunner
) -> None:
    run = await queue(service, await action(service, options={"outcome": "hang"}))
    await runner.run_due()
    await asyncio.sleep(0.01)

    cancelled = await asyncio.wait_for(runner.cancel(run.id, grace=0.05), 1)

    assert cancelled.status is RunStatus.CANCELLED


async def test_cancelling_a_run_that_ended_changes_nothing(
    service: UDLService, runner: ActionRunner
) -> None:
    run = await queue(service, await action(service))
    await ran(runner)

    assert (await runner.cancel(run.id)).status is RunStatus.SUCCEEDED


async def test_cancelling_an_unknown_run_is_refused(runner: ActionRunner) -> None:
    with pytest.raises(DoesNotExist):
        await runner.cancel(uuid4())


async def test_a_run_another_udl_runs_is_cancelled_there(
    service: UDLService, runner: ActionRunner
) -> None:
    run = await queue(service, await action(service))
    await service.db.upsert_record(
        run.model_copy(update={"status": RunStatus.RUNNING, "runner": "elsewhere:2:x"})
    )

    with pytest.raises(DoesNotExist, match="another udl") as raised:
        await runner.cancel(run.id)

    assert raised.value.hint is not None


# ---------------------------------------------------------------------------------------------
# Runs asked for, and crashes
# ---------------------------------------------------------------------------------------------


async def test_a_run_asked_for_runs_here_and_is_stored(
    service: UDLService, runner: ActionRunner
) -> None:
    done = await action(service, options={"output": "{{ event.data.name }}"})
    happened = await event(service)

    run = await runner.run_now(done, happened)

    assert run.status is RunStatus.SUCCEEDED
    assert run.output == "x"
    assert (run.event_id, run.subscription_id, run.runner) == (happened.id, None, None)
    assert await stored(service, run) == run


async def test_a_run_asked_for_without_an_event(service: UDLService, runner: ActionRunner) -> None:
    run = await runner.run_now(await action(service, options={"output": "{{ event }}"}), None)

    assert (run.status, run.output, run.event_id) == (RunStatus.SUCCEEDED, None, None)


async def test_after_a_crash_queued_runs_resume_and_running_ones_were_interrupted(
    service: UDLService, runner: ActionRunner, clock: FakeClock
) -> None:
    done = await action(service)
    waiting = await queue(service, done)
    crashed = await queue(service, done)
    asked = await queue(service, done)
    for run, by in ((crashed, "old-runtime:1:x"), (asked, None)):
        await service.db.upsert_record(
            run.model_copy(update={"status": RunStatus.RUNNING, "runner": by})
        )

    assert await runner.recover() == 1
    await clock.advance(timedelta(seconds=1))
    await ran(runner)

    found = await stored(service, crashed)
    assert found.status is RunStatus.FAILED
    assert found.error is not None
    assert found.error.code is ErrorCode.INTERRUPTED
    assert (await stored(service, waiting)).status is RunStatus.SUCCEEDED
    # (A run asked for runs in the udl that asked: only it, or a udl that starts alone, knows.)
    assert (await stored(service, asked)).status is RunStatus.RUNNING
    assert [ended.type for ended in await ended_events(service)] == [
        "udl.action.failed",
        "udl.action.succeeded",
    ]


async def test_a_udl_that_starts_alone_ends_the_runs_asked_for_that_were_left(
    harness: Harness,
) -> None:
    async with harness.service() as first:
        run = await queue(first, await action(first))
        await first.db.upsert_record(run.model_copy(update={"status": RunStatus.RUNNING}))

    async with harness.service() as second:
        found = await stored(second, run)

    assert found.status is RunStatus.FAILED


async def test_shutting_down_interrupts_what_runs(
    service: UDLService, runner: ActionRunner
) -> None:
    run = await queue(service, await action(service, options={"outcome": "wait"}))
    await runner.run_due()
    await asyncio.sleep(0.01)

    await runner.shutdown()

    found = await stored(service, run)
    assert found.status is RunStatus.FAILED
    assert found.error is not None
    assert found.error.code is ErrorCode.INTERRUPTED


# ---------------------------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------------------------


async def test_serving_runs_what_is_queued_and_what_comes_due(
    service: UDLService, runner: ActionRunner, clock: FakeClock, fake: type[FakeAction]
) -> None:
    flaky = await queue(
        service, await action(service, "flaky", retries=1, options={"outcome": "retryable"})
    )
    serving = asyncio.create_task(runner.serve())
    try:
        await clock.advance(timedelta(seconds=0))
        await runner.idle()
        assert (await stored(service, flaky)).status is RunStatus.QUEUED

        later = await queue(service, await action(service, "later"))
        runner.wake()
        await clock.advance(timedelta(seconds=0))
        await runner.idle()
        assert (await stored(service, later)).status is RunStatus.SUCCEEDED

        await clock.advance(timedelta(seconds=10))
        await runner.idle()
        assert (await stored(service, flaky)).status is RunStatus.FAILED
    finally:
        serving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await serving


@pytest.mark.parametrize("status", list(RunStatus))
def test_a_run_has_ended_once_it_ran_or_was_skipped(status: RunStatus) -> None:
    run = ActionRun(action_id=uuid4(), action="x", plugin="fake", status=status)

    assert run.ended() is (status not in ("queued", "running", "awaiting_approval"))


async def test_a_udl_that_cannot_read_the_runs_it_left_still_starts(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def gone(self: ActionRunner) -> int:
        raise DatabaseError("The database went away")

    monkeypatch.setattr(ActionRunner, "recover_direct", gone)

    async with harness.service():
        pass

    assert "The database went away" in caplog.text
