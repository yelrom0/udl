"""
-----------
- jobs.py -
-----------

JobManager: runs downloads as jobs, one asyncio task per URL, the URLs of one request sharing a
batch id. At most `max_concurrent` jobs run at once, and at most `max_per_site` of one site's; the
others wait their turn (a job waits for its site's turn first, so jobs waiting for a busy site
leave room for other sites). It tells subscribers what happens to each job (JobEvent: queued,
started, progress, then finished, failed or cancelled), cancels jobs, and saves each one as a
DownloadRecord (the download history) as it is queued, starts and ends, unless its request says
not to.

When udl stops without ending its jobs (it is killed, or the machine stops), their records stay
queued or running. recover_interrupted() marks them failed (interrupted); a starting udl runs it
only when no other udl is running (InstanceLock), since another udl's jobs look the same.
"""

# System Imports
import asyncio
import fcntl
import itertools
import logging
from collections import deque
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol
from uuid import UUID, uuid4

# Local Imports
from udl.core.executor import Execution, JobSpec, ToolProgress
from udl.core.models import Attempt, DownloadBatch, JobEvent, JobEventType, JobInfo
from udl.db.manager import DBManager
from udl.db.models import DownloadQuery, JobStatus, utcnow
from udl.errors import (
    Cancelled,
    DatabaseError,
    DoesNotExist,
    DownloadError,
    ErrorInfo,
    Interrupted,
    UDLError,
)
from udl.plugins.downloaders.base import CancelToken, DownloadProgress

log = logging.getLogger(__name__)

_ENDINGS: dict[JobStatus, JobEventType] = {
    JobStatus.FINISHED: JobEventType.FINISHED,
    JobStatus.FAILED: JobEventType.FAILED,
    JobStatus.CANCELLED: JobEventType.CANCELLED,
}


class Runner(Protocol):
    """What runs a job's download (the Executor)."""

    async def run(
        self,
        job: JobSpec,
        *,
        db: DBManager,
        cancel: CancelToken,
        progress: ToolProgress,
        on_attempt: Callable[[Attempt], None],
    ) -> Execution: ...


class _Job:
    """A job's state."""

    def __init__(self, spec: JobSpec, batch_id: UUID, db: DBManager) -> None:
        self.id = uuid4()
        self.spec = spec
        self.batch_id = batch_id
        self.db = db
        self.status = JobStatus.QUEUED
        self.started_at = utcnow()
        self.finished_at: datetime | None = None
        self.execution: Execution | None = None
        self.error: ErrorInfo | None = None
        self.attempts: list[Attempt] = []
        self.progress: DownloadProgress | None = None
        self.progress_sent_at: float | None = None
        self.cancel = CancelToken()
        self.task: asyncio.Task[None] | None = None
        # What a subscriber that joins late hears: the events so far (with a sequence number,
        # to merge jobs' events in order), and the latest progress while the job runs.
        self.history: list[tuple[int, JobEvent]] = []
        self.last_progress: tuple[int, JobEvent] | None = None

    @property
    def ended(self) -> bool:
        return self.status in _ENDINGS

    def info(self) -> JobInfo:
        info = JobInfo(
            id=self.id,
            batch_id=self.batch_id,
            url=self.spec.url.value,
            site_key=self.spec.url.site_key,
            status=self.status,
            progress=self.progress,
            attempts=list(self.attempts),
            error=self.error,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )
        if (execution := self.execution) is None:
            return info
        result = execution.result
        return info.model_copy(
            update={
                "tool": execution.candidate.tool,
                "handed_off_to": result.handed_off_to,
                "strategy_id": execution.strategy_id,
                "output_dir": execution.output_dir,
                "files": result.files,
                "size_bytes": result.size_bytes,
                "title": result.title,
            }
        )


class _Subscriber:
    def __init__(self, jobs: set[UUID]) -> None:
        self.jobs = jobs
        self.queue = asyncio.Queue[JobEvent]()


class JobManager:
    """Runs download jobs and keeps track of them.

    Progress events are sent at most every `progress_interval` seconds per job (a job's
    JobInfo always has the latest progress). Ended jobs are kept, for get() and events(), until
    `keep` more have ended; their history stays in the databases.
    """

    def __init__(
        self,
        runner: Runner,
        *,
        max_concurrent: int,
        max_per_site: int,
        progress_interval: float = 0.25,
        keep: int = 1000,
    ) -> None:
        self._runner = runner
        self._turns = asyncio.Semaphore(max_concurrent)
        self._max_per_site = max_per_site
        self._site_turns: dict[str, asyncio.Semaphore] = {}
        self._progress_interval = progress_interval
        self._keep = keep
        self._jobs: dict[UUID, _Job] = {}
        self._batches: dict[UUID, list[UUID]] = {}
        self._ended: deque[UUID] = deque()  # oldest first
        self._subscribers: list[_Subscriber] = []
        self._sequence = itertools.count()

    async def submit(self, specs: Sequence[JobSpec], *, db: DBManager) -> DownloadBatch:
        """Queue a job for each spec, in one batch, saving them to the history in `db`."""
        batch_id = uuid4()
        jobs = [_Job(spec, batch_id, db) for spec in specs]
        self._batches[batch_id] = [job.id for job in jobs]
        for job in jobs:
            self._jobs[job.id] = job
            self._publish(job, JobEventType.QUEUED)
            await self._save(job, new=True)
        for job in jobs:
            job.task = asyncio.create_task(self._run(job), name=f"udl job {job.id}")
        return DownloadBatch(id=batch_id, jobs=[job.info() for job in jobs])

    def get(self, job_id: UUID) -> JobInfo | None:
        job = self._jobs.get(job_id)
        return job.info() if job else None

    def batch(self, batch_id: UUID) -> DownloadBatch | None:
        ids = self._batches.get(batch_id)
        if ids is None:
            return None
        return DownloadBatch(id=batch_id, jobs=[self._jobs[job_id].info() for job_id in ids])

    async def wait(self, id_: UUID) -> None:
        """Wait for a job, or every job of a batch, to end (giving up on waiting leaves them
        running). Raises DoesNotExist."""
        tasks = [job.task for job in self._scope(id_) if job.task is not None]
        if tasks:
            await asyncio.wait(tasks)

    async def cancel(self, id_: UUID) -> list[JobInfo]:
        """Cancel a job, or every job of a batch, and wait for them to stop. Jobs that have
        ended are left as they are. Raises DoesNotExist."""
        jobs = self._scope(id_)
        await self._stop(jobs)
        return [job.info() for job in jobs]

    async def shutdown(self) -> None:
        """Cancel every job that has not ended, and wait for them to stop (and be saved)."""
        await self._stop(list(self._jobs.values()))

    def events(self, id_: UUID) -> AsyncGenerator[JobEvent]:
        """What happens to a job, or to every job of a batch: first what has happened (the
        latest progress only), then each event as it happens, until every job has ended.
        Raises DoesNotExist (at once)."""
        return self._stream(self._scope(id_))

    # -----------------------------------------------------------------------------------------

    def _scope(self, id_: UUID) -> list[_Job]:
        if (job := self._jobs.get(id_)) is not None:
            return [job]
        if (ids := self._batches.get(id_)) is not None:
            return [self._jobs[job_id] for job_id in ids]
        raise DoesNotExist(
            f"No download job or batch {id_} is known to this udl",
            hint="A download that ended a while ago is in the history (`udl history`).",
        )

    async def _stop(self, jobs: list[_Job]) -> None:
        stopping = [(job, job.task) for job in jobs if not job.ended and job.task is not None]
        for job, task in stopping:
            job.cancel.cancel()
            task.cancel()
        if stopping:
            await asyncio.wait([task for _, task in stopping])
        for job, _ in stopping:
            if not job.ended:  # its task was cancelled before it ran at all
                await self._end(job, JobStatus.CANCELLED, Cancelled("The download was cancelled"))

    @asynccontextmanager
    async def _turn(self, site_key: str) -> AsyncGenerator[None]:
        """Wait for the site's turn, then for a turn."""
        site = self._site_turns.setdefault(site_key, asyncio.Semaphore(self._max_per_site))
        async with site, self._turns:
            yield

    async def _run(self, job: _Job) -> None:
        try:
            async with self._turn(job.spec.url.site_key):
                job.status = JobStatus.RUNNING
                job.started_at = utcnow()
                self._publish(job, JobEventType.STARTED)
                await self._save(job)
                job.execution = await self._runner.run(
                    job.spec,
                    db=job.db,
                    cancel=job.cancel,
                    progress=lambda tool, progress: self._progress(job, tool, progress),
                    on_attempt=job.attempts.append,
                )
        except Cancelled as e:
            await self._end(job, JobStatus.CANCELLED, e)
        except asyncio.CancelledError:
            # JobManager cancelled the job (cancel() or shutdown()): it ends here, as cancelled.
            task = asyncio.current_task()
            while task is not None and task.cancelling():
                task.uncancel()
            await self._end(job, JobStatus.CANCELLED, Cancelled("The download was cancelled"))
        except UDLError as e:
            await self._end(job, JobStatus.FAILED, e)
        except Exception as e:
            log.exception("The download of %s failed unexpectedly", job.spec.url)
            await self._end(job, JobStatus.FAILED, DownloadError(f"{type(e).__name__}: {e}"))
        else:
            await self._end(job, JobStatus.FINISHED)

    async def _end(self, job: _Job, status: JobStatus, error: UDLError | None = None) -> None:
        job.status = status
        job.error = error.to_info() if error else None
        job.finished_at = utcnow()
        self._publish(job, _ENDINGS[status])
        await self._save(job)
        self._forget_old(job)

    def _progress(self, job: _Job, tool: str, progress: DownloadProgress) -> None:
        job.progress = progress
        now = asyncio.get_running_loop().time()
        if job.progress_sent_at is None or now - job.progress_sent_at >= self._progress_interval:
            job.progress_sent_at = now
            self._publish(job, JobEventType.PROGRESS, tool=tool)

    def _publish(self, job: _Job, kind: JobEventType, *, tool: str | None = None) -> None:
        event = JobEvent(type=kind, at=utcnow(), job=job.info(), tool=tool)
        numbered = (next(self._sequence), event)
        if kind is JobEventType.PROGRESS:
            job.last_progress = numbered
        else:
            job.history.append(numbered)
            if kind.ends:
                job.last_progress = None
        for subscriber in self._subscribers:
            if job.id in subscriber.jobs:
                subscriber.queue.put_nowait(event)

    async def _stream(self, jobs: list[_Job]) -> AsyncGenerator[JobEvent]:
        # Subscribing and taking the history happen in one step, so no event is missed or heard
        # twice.
        subscriber = _Subscriber({job.id for job in jobs})
        self._subscribers.append(subscriber)
        try:
            past = [
                numbered
                for job in jobs
                for numbered in [*job.history, *filter(None, [job.last_progress])]
            ]
            waiting = {job.id for job in jobs if not job.ended}
            for _, event in sorted(past, key=lambda numbered: numbered[0]):
                yield event
            while waiting:
                event = await subscriber.queue.get()
                yield event
                if event.type.ends:
                    waiting.discard(event.job.id)
        finally:
            self._subscribers.remove(subscriber)

    async def _save(self, job: _Job, *, new: bool = False) -> None:
        """Save a job to the history (a database that fails is logged, never failing the job)."""
        if not job.spec.record:
            return
        record = job.info().record()
        try:
            if new:
                await job.db.add_download(record)
            else:
                await job.db.update_download(record)
        except DatabaseError as e:
            log.warning("Could not save the download of %s to the history: %s", job.spec.url, e)

    def _forget_old(self, job: _Job) -> None:
        self._ended.append(job.id)
        while len(self._ended) > self._keep:
            old = self._jobs.pop(self._ended.popleft())
            batch = self._batches[old.batch_id]
            batch.remove(old.id)
            if not batch:
                del self._batches[old.batch_id]


# ---------------------------------------------------------------------------------------------
# Restarting
# ---------------------------------------------------------------------------------------------


async def recover_interrupted(db: DBManager) -> int:
    """Mark the downloads that a udl which stopped left queued or running as failed
    (interrupted). Run it only when no other udl is running (InstanceLock). Returns how many."""
    try:
        stale = [
            record
            for status in (JobStatus.QUEUED, JobStatus.RUNNING)
            for record in await db.list_downloads(DownloadQuery(status=status))
        ]
    except DatabaseError as e:
        log.warning("Could not read the download history to clean it up: %s", e)
        return 0
    recovered = 0
    for record in stale:
        error = Interrupted("udl stopped before this download ended").to_info()
        try:
            await db.update_download(
                record.model_copy(
                    update={"status": JobStatus.FAILED, "error": error, "finished_at": utcnow()}
                )
            )
        except DatabaseError as e:
            log.warning("Could not mark the download of %s as interrupted: %s", record.url, e)
            continue
        recovered += 1
    if recovered:
        log.info("Marked %d downloads that udl did not finish as interrupted", recovered)
    return recovered


class InstanceLock:
    """Tells a starting udl whether it is the only one running (for this user, on this machine).

    Every running udl holds a shared lock on one file. A udl that can take it exclusively is
    alone: it holds it exclusively while it cleans up (recover_interrupted), then shares it, so a
    udl that starts meanwhile waits for the clean-up instead of having its new jobs cleaned up.
    The lock goes when the process does, however it stops.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[bytes] | None = None

    def acquire(self) -> bool:
        """Start holding the lock (blocking while a udl that is alone cleans up). Returns whether
        this udl is alone; then it holds the lock exclusively until share()."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("ab")
        try:
            fcntl.flock(self._file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fcntl.flock(self._file, fcntl.LOCK_SH)
            return False
        return True

    def share(self) -> None:
        """Let other udls start (after cleaning up)."""
        if self._file is not None:
            fcntl.flock(self._file, fcntl.LOCK_SH)

    def release(self) -> None:
        if self._file is not None:
            fcntl.flock(self._file, fcntl.LOCK_UN)
            self._file.close()
            self._file = None
