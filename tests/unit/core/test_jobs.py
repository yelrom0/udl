"""
----------------
- test_jobs.py -
----------------

JobManager: runs each URL of a batch as a job, tells its subscribers what happens to the jobs
(queued, started, progress, then finished, failed or cancelled), cancels them and saves each as
a DownloadRecord. On a restart, the records a stopped udl left queued or running become failed:
interrupted, unless another udl is still running.
"""

# System Imports
import asyncio
import threading
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing
from pathlib import Path
from types import TracebackType
from typing import Any, Self

# Package Imports
import pytest

# Local Imports
from support.downloaders import FakeMedia
from support.engine import StaticCookies, databases, registry, script, settings
from udl.config import Settings
from udl.core.executor import Execution, Executor, JobSpec, RetryPolicy, ToolProgress
from udl.core.jobs import InstanceLock, JobManager, recover_interrupted
from udl.core.models import JobEvent, JobEventType
from udl.core.resolver import Resolver
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import DownloadQuery, DownloadRecord, JobStatus
from udl.errors import DatabaseError, DoesNotExist, ErrorCode
from udl.plugins.downloaders.base import CancelToken

A = "https://example.com/a"
B = "https://example.com/b"
SLOW = FakeMedia(content=b"x" * 2000, delay=0.02)  # 100 chunks of 20 bytes: about 2 s


def slow_settings(tmp_path: Path, **fields: Any) -> Settings:
    return settings(
        tmp_path,
        probe_order=["http"],
        tools={"http": {**script({A: SLOW, B: SLOW}), "chunk_size": 20}},
        **fields,
    )


class Harness:
    """A job manager on an in-memory database, running the real executor with fake tools."""

    def __init__(
        self,
        config: Settings,
        *,
        progress_interval: float = 0.0,
        keep: int = 1000,
        runner: Any = None,
    ) -> None:
        toolbox = Toolbox(config, registry())
        executor = Executor(
            config,
            Resolver(config, toolbox),
            toolbox,
            cookies=StaticCookies(),
            retry=RetryPolicy(retries=0),
        )
        self.db = databases(config, registry())
        self.jobs = JobManager(runner or executor, progress_interval=progress_interval, keep=keep)

    async def __aenter__(self) -> Self:
        await self.db.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.jobs.shutdown()
        await self.db.close()

    async def submit(self, *urls: str, **spec: Any) -> list[uuid.UUID]:
        batch = await self.jobs.submit(
            [JobSpec(url=URL.parse(url), **spec) for url in urls], db=self.db
        )
        return [job.id for job in batch.jobs]

    async def events(self, id_: uuid.UUID) -> list[JobEvent]:
        return [event async for event in self.jobs.events(id_)]

    async def records(self) -> list[DownloadRecord]:
        return await self.db.list_downloads(DownloadQuery())


def types(events: Sequence[JobEvent]) -> list[JobEventType]:
    return [event.type for event in events]


def without_progress(events: Sequence[JobEvent]) -> list[JobEventType]:
    return [t for t in types(events) if t is not JobEventType.PROGRESS]


async def started(harness: Harness, job_id: uuid.UUID) -> None:
    """Wait until a job has started."""
    async with aclosing(harness.jobs.events(job_id)) as events:
        async for event in events:
            if event.type is JobEventType.STARTED:
                return


# ---------------------------------------------------------------------------------------------
# Running batches
# ---------------------------------------------------------------------------------------------


async def test_a_batch_runs_every_url_as_a_job(tmp_path: Path) -> None:
    media = {A: FakeMedia(content=b"a", filename="a.mp4"), B: FakeMedia(content=b"b", filename="b")}
    config = settings(tmp_path, tools={"http": script(media)})
    async with Harness(config) as harness:
        batch = await harness.jobs.submit(
            [JobSpec(url=URL.parse(A)), JobSpec(url=URL.parse(B))], db=harness.db
        )

        assert [job.status for job in batch.jobs] == [JobStatus.QUEUED, JobStatus.QUEUED]
        assert {job.batch_id for job in batch.jobs} == {batch.id}
        await harness.jobs.wait(batch.id)

        done = harness.jobs.batch(batch.id)
        assert done is not None
        assert [job.status for job in done.jobs] == [JobStatus.FINISHED, JobStatus.FINISHED]
        assert [[p.read_bytes() for p in job.files] for job in done.jobs] == [[b"a"], [b"b"]]


async def test_an_empty_batch_is_done_at_once(tmp_path: Path) -> None:
    async with Harness(settings(tmp_path)) as harness:
        batch = await harness.jobs.submit([], db=harness.db)

        await harness.jobs.wait(batch.id)
        assert batch.jobs == []


async def test_a_finished_job_says_what_came_of_it(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"yt-dlp": script({A: FakeMedia(content=b"v", title="A")})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)

        job = harness.jobs.get(job_id)
        assert job is not None
        assert (job.status, job.tool, job.title, job.size_bytes) == (
            JobStatus.FINISHED,
            "yt-dlp",
            "A",
            1,
        )
        assert job.output_dir == tmp_path / "downloads"
        assert job.strategy_id is not None
        assert [a.tool for a in job.attempts] == ["http", "yt-dlp"]
        assert job.finished_at is not None
        assert job.error is None


async def test_a_failed_job_says_why(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script(errors={A: "not_found"})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)

        job = harness.jobs.get(job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.error is not None
        assert job.error.code is ErrorCode.NOT_FOUND
        assert [a.tool for a in job.attempts] == ["http"]
        assert job.files == []


async def test_a_bug_in_the_runner_fails_the_job_instead_of_udl(tmp_path: Path) -> None:
    class Buggy:
        async def run(self, job: JobSpec, **_: Any) -> Execution:
            raise RuntimeError("oops")

    async with Harness(settings(tmp_path), runner=Buggy()) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)

        job = harness.jobs.get(job_id)
        assert job is not None
        assert job.status is JobStatus.FAILED
        assert job.error is not None
        assert "oops" in job.error.message


async def test_unknown_ids_do_not_exist(tmp_path: Path) -> None:
    async with Harness(settings(tmp_path)) as harness:
        unknown = uuid.uuid4()

        assert harness.jobs.get(unknown) is None
        assert harness.jobs.batch(unknown) is None
        with pytest.raises(DoesNotExist):
            await harness.jobs.wait(unknown)
        with pytest.raises(DoesNotExist):
            await harness.jobs.cancel(unknown)
        with pytest.raises(DoesNotExist):
            await harness.events(unknown)


async def test_giving_up_on_waiting_leaves_the_job_running(tmp_path: Path) -> None:
    async with Harness(slow_settings(tmp_path)) as harness:
        [job_id] = await harness.submit(A)

        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                await harness.jobs.wait(job_id)

        job = harness.jobs.get(job_id)
        assert job is not None
        assert job.status is JobStatus.RUNNING


async def test_waiting_for_a_job_that_has_ended_returns_at_once(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a"})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)

        async with asyncio.timeout(1):
            await harness.jobs.wait(job_id)


# ---------------------------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------------------------


async def test_a_job_s_events_come_in_order(tmp_path: Path) -> None:
    media = FakeMedia(content=b"x" * 30)
    config = settings(tmp_path, tools={"http": {**script({A: media}), "chunk_size": 10}})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)

        events = await harness.events(job_id)

    assert types(events) == [
        JobEventType.QUEUED,
        JobEventType.STARTED,
        JobEventType.PROGRESS,
        JobEventType.PROGRESS,
        JobEventType.PROGRESS,
        JobEventType.FINISHED,
    ]
    progress = [e for e in events if e.type is JobEventType.PROGRESS]
    assert [(e.tool, e.job.progress and e.job.progress.downloaded_bytes) for e in progress] == [
        ("http", 10),
        ("http", 20),
        ("http", 30),
    ]
    assert [e.job.status for e in events] == [
        JobStatus.QUEUED,
        JobStatus.RUNNING,
        JobStatus.RUNNING,
        JobStatus.RUNNING,
        JobStatus.RUNNING,
        JobStatus.FINISHED,
    ]
    assert all(e.job.id == job_id for e in events)


async def test_a_failed_job_s_last_event_is_failed(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script(errors={A: "not_found"})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)

        events = await harness.events(job_id)

    assert types(events) == [JobEventType.QUEUED, JobEventType.STARTED, JobEventType.FAILED]
    assert events[-1].job.error is not None


async def test_progress_events_are_spaced_out(tmp_path: Path) -> None:
    media = FakeMedia(content=b"x" * 100)
    config = settings(tmp_path, tools={"http": {**script({A: media}), "chunk_size": 10}})
    async with Harness(config, progress_interval=60) as harness:
        [job_id] = await harness.submit(A)

        events = await harness.events(job_id)
        job = harness.jobs.get(job_id)

    assert types(events).count(JobEventType.PROGRESS) == 1
    # The job always has the latest progress, sent or not.
    assert job is not None
    assert job.progress is not None
    assert job.progress.downloaded_bytes == 100


async def test_a_late_subscriber_hears_what_already_happened(tmp_path: Path) -> None:
    media = FakeMedia(content=b"x" * 30)
    config = settings(tmp_path, tools={"http": {**script({A: media}), "chunk_size": 10}})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)

        events = await harness.events(job_id)

    # (Progress is history once the job has ended.)
    assert types(events) == [JobEventType.QUEUED, JobEventType.STARTED, JobEventType.FINISHED]


async def test_a_subscriber_that_joins_mid_download_hears_the_latest_progress(
    tmp_path: Path,
) -> None:
    async with Harness(slow_settings(tmp_path)) as harness:
        [job_id] = await harness.submit(A)
        await started(harness, job_id)
        await asyncio.sleep(0.1)

        stream = harness.jobs.events(job_id)
        first = [await anext(stream) for _ in range(3)]
        await harness.jobs.cancel(job_id)
        rest = [event async for event in stream]

    assert types(first) == [JobEventType.QUEUED, JobEventType.STARTED, JobEventType.PROGRESS]
    assert rest[-1].type is JobEventType.CANCELLED


async def test_a_batch_s_events_end_when_every_job_has_ended(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a"}, errors={B: "not_found"})})
    async with Harness(config) as harness:
        batch = await harness.jobs.submit(
            [JobSpec(url=URL.parse(A)), JobSpec(url=URL.parse(B))], db=harness.db
        )

        events = await harness.events(batch.id)

    ends = [(e.job.url, e.type) for e in events if e.type.ends]
    assert sorted(ends) == [(A, JobEventType.FINISHED), (B, JobEventType.FAILED)]
    assert types(events)[:2] == [JobEventType.QUEUED, JobEventType.QUEUED]


async def test_a_subscriber_hears_only_of_its_own_job(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a", B: "b"})})
    async with Harness(config) as harness:
        batch = await harness.jobs.submit(
            [JobSpec(url=URL.parse(A)), JobSpec(url=URL.parse(B))], db=harness.db
        )

        events = await harness.events(batch.jobs[0].id)

    assert {event.job.url for event in events} == {A}


async def test_several_subscribers_each_hear_every_event(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a"})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)

        first, second = await asyncio.gather(harness.events(job_id), harness.events(job_id))

    assert (
        without_progress(first)
        == without_progress(second)
        == [
            JobEventType.QUEUED,
            JobEventType.STARTED,
            JobEventType.FINISHED,
        ]
    )


# ---------------------------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------------------------


async def test_a_running_job_can_be_cancelled(tmp_path: Path) -> None:
    async with Harness(slow_settings(tmp_path)) as harness:
        [job_id] = await harness.submit(A)
        await started(harness, job_id)

        [cancelled] = await harness.jobs.cancel(job_id)
        events = await harness.events(job_id)

    assert cancelled.status is JobStatus.CANCELLED
    assert cancelled.finished_at is not None
    assert cancelled.error is not None
    assert cancelled.error.code is ErrorCode.CANCELLED
    assert without_progress(events) == [
        JobEventType.QUEUED,
        JobEventType.STARTED,
        JobEventType.CANCELLED,
    ]


async def test_cancelling_a_batch_cancels_its_jobs(tmp_path: Path) -> None:
    async with Harness(slow_settings(tmp_path)) as harness:
        batch = await harness.jobs.submit(
            [JobSpec(url=URL.parse(A)), JobSpec(url=URL.parse(B))], db=harness.db
        )

        cancelled = await harness.jobs.cancel(batch.id)

    assert [job.status for job in cancelled] == [JobStatus.CANCELLED, JobStatus.CANCELLED]


async def test_cancelling_a_job_that_has_ended_changes_nothing(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a"})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)

        [job] = await harness.jobs.cancel(job_id)

    assert job.status is JobStatus.FINISHED


async def test_shutting_down_cancels_the_jobs_and_saves_them(tmp_path: Path) -> None:
    async with Harness(slow_settings(tmp_path)) as harness:
        [job_id] = await harness.submit(A)
        await started(harness, job_id)

        await harness.jobs.shutdown()

        job = harness.jobs.get(job_id)
        assert job is not None
        assert job.status is JobStatus.CANCELLED
        [record] = await harness.records()
        assert record.status is JobStatus.CANCELLED


async def test_a_runner_that_raises_cancelled_cancels_the_job(tmp_path: Path) -> None:
    class Cancels:
        async def run(
            self,
            job: JobSpec,
            *,
            db: DBManager,
            cancel: CancelToken,
            progress: ToolProgress,
            on_attempt: Any,
        ) -> Execution:
            cancel.cancel()
            cancel.check()
            raise AssertionError  # (never reached)

    async with Harness(settings(tmp_path), runner=Cancels()) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)

        job = harness.jobs.get(job_id)
        assert job is not None
        assert job.status is JobStatus.CANCELLED


# ---------------------------------------------------------------------------------------------
# The download history
# ---------------------------------------------------------------------------------------------


async def test_a_job_is_saved_as_queued_then_running_then_as_it_ended(tmp_path: Path) -> None:
    async with Harness(slow_settings(tmp_path)) as harness:
        [job_id] = await harness.submit(A)
        [queued] = await harness.records()
        await started(harness, job_id)
        [running] = await harness.records()
        await harness.jobs.wait(job_id)
        [finished] = await harness.records()
        job = harness.jobs.get(job_id)

    assert (queued.id, queued.status, queued.url, queued.site_key) == (
        job_id,
        JobStatus.QUEUED,
        A,
        "example.com",
    )
    assert running.status is JobStatus.RUNNING
    assert job is not None
    assert finished == job.record()
    assert finished.status is JobStatus.FINISHED
    assert finished.tool == "http"
    assert finished.files == job.files != []
    assert finished.size_bytes == 2000
    assert finished.strategy_id is not None


async def test_a_failed_job_is_saved_with_its_error(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script(errors={A: "not_found"})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A)
        await harness.jobs.wait(job_id)
        [record] = await harness.records()

    assert record.status is JobStatus.FAILED
    assert record.error is not None
    assert record.error.code is ErrorCode.NOT_FOUND


async def test_nothing_is_saved_when_the_request_says_not_to(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a"})})
    async with Harness(config) as harness:
        [job_id] = await harness.submit(A, record=False)
        await harness.jobs.wait(job_id)

        assert await harness.records() == []
        job = harness.jobs.get(job_id)
        assert job is not None
        assert job.status is JobStatus.FINISHED


async def test_a_database_that_cannot_save_the_history_never_fails_a_job(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a"})})

    class Full(DBManager):
        async def add_download(self, record: DownloadRecord) -> None:
            raise DatabaseError("the disk is full")

        async def update_download(self, record: DownloadRecord) -> None:
            raise DatabaseError("the disk is full")

    async with Harness(config) as harness:
        full = Full(config.db, registry())
        await full.connect()
        batch = await harness.jobs.submit([JobSpec(url=URL.parse(A))], db=full)
        await harness.jobs.wait(batch.id)
        await full.close()

        done = harness.jobs.batch(batch.id)

    assert done is not None
    assert done.jobs[0].status is JobStatus.FINISHED
    assert "the disk is full" in caplog.text


async def test_ended_jobs_beyond_keep_are_forgotten_but_their_history_stays(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a", B: "b"})})
    async with Harness(config, keep=1) as harness:
        [first] = await harness.submit(A)
        await harness.jobs.wait(first)
        [second] = await harness.submit(B)
        await harness.jobs.wait(second)

        assert harness.jobs.get(first) is None
        assert harness.jobs.get(second) is not None
        assert len(await harness.records()) == 2


async def test_a_batch_keeps_the_jobs_that_are_not_forgotten(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script({A: "a"}, errors={B: "not_found"})})
    async with Harness(config, keep=1) as harness:
        batch = await harness.jobs.submit(
            [JobSpec(url=URL.parse(A)), JobSpec(url=URL.parse(B))], db=harness.db
        )
        await harness.jobs.wait(batch.id)

        kept = harness.jobs.batch(batch.id)

    assert kept is not None
    assert len(kept.jobs) == 1


# ---------------------------------------------------------------------------------------------
# Restarting
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DBManager]:
    async with databases(settings(tmp_path), registry()) as manager:
        yield manager


def record(status: JobStatus) -> DownloadRecord:
    return DownloadRecord(url=A, site_key="example.com", status=status)


async def test_recovery_marks_downloads_a_stopped_udl_left_as_interrupted(db: DBManager) -> None:
    queued, running = record(JobStatus.QUEUED), record(JobStatus.RUNNING)
    finished = record(JobStatus.FINISHED)
    for r in (queued, running, finished):
        await db.add_download(r)

    assert await recover_interrupted(db) == 2

    records = {r.id: r for r in await db.list_downloads(DownloadQuery())}
    for r in (queued, running):
        assert records[r.id].status is JobStatus.FAILED
        assert records[r.id].finished_at is not None
        error = records[r.id].error
        assert error is not None
        assert error.code is ErrorCode.INTERRUPTED
    assert records[finished.id] == finished


async def test_recovery_with_nothing_to_recover(db: DBManager) -> None:
    assert await recover_interrupted(db) == 0


async def test_a_record_that_cannot_be_recovered_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(tmp_path)

    class Stubborn(DBManager):
        async def update_download(self, record: DownloadRecord) -> None:
            raise DatabaseError("read-only")

    async with Stubborn(config.db, registry()) as stubborn:
        await stubborn.add_download(record(JobStatus.RUNNING))

        assert await recover_interrupted(stubborn) == 0
    assert "read-only" in caplog.text


async def test_recovery_does_nothing_when_the_history_cannot_be_read(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(tmp_path)

    class Unreadable(DBManager):
        async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]:
            raise DatabaseError("gone")

    assert await recover_interrupted(Unreadable(config.db, registry())) == 0
    assert "gone" in caplog.text


def test_the_first_udl_is_alone(tmp_path: Path) -> None:
    lock = InstanceLock(tmp_path / "udl.lock")

    assert lock.acquire() is True
    lock.release()


def test_a_second_udl_is_not_alone_while_the_first_runs(tmp_path: Path) -> None:
    first, second = InstanceLock(tmp_path / "udl.lock"), InstanceLock(tmp_path / "udl.lock")

    assert first.acquire() is True
    first.share()
    assert second.acquire() is False
    second.release()
    first.release()


def test_a_udl_that_starts_while_the_first_recovers_waits_for_it(tmp_path: Path) -> None:
    first, second = InstanceLock(tmp_path / "udl.lock"), InstanceLock(tmp_path / "udl.lock")
    assert first.acquire() is True
    answers: list[bool] = []
    starting = threading.Thread(target=lambda: answers.append(second.acquire()))

    starting.start()
    starting.join(timeout=0.2)
    assert answers == []  # (the first is still recovering)
    first.share()
    starting.join(timeout=5)

    assert answers == [False]
    second.release()
    first.release()


def test_once_the_others_stop_the_next_udl_is_alone(tmp_path: Path) -> None:
    first, second = InstanceLock(tmp_path / "udl.lock"), InstanceLock(tmp_path / "udl.lock")
    first.acquire()
    first.share()
    second.acquire()
    first.release()
    second.release()

    third = InstanceLock(tmp_path / "udl.lock")
    assert third.acquire() is True
    third.release()


def test_the_lock_s_dir_is_created_and_releasing_twice_is_harmless(tmp_path: Path) -> None:
    lock = InstanceLock(tmp_path / "new" / "udl.lock")
    lock.share()  # (before acquiring: nothing to share)

    assert lock.acquire() is True
    lock.share()
    lock.release()
    lock.release()
