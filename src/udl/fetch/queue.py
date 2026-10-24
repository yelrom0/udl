"""
------------
- queue.py -
------------

FetchQueue: fetches a request's URLs (a batch) in the udl that was asked, as downloads are, each
URL a task of its own. At most `max_concurrent` fetches run at once and `max_per_site` of a
site's, its starts `site_delay` apart (give or take a tenth); a URL waits for its site's turn
first, so sites take turns and one big site holds up none.

The first check a person can pass on a site holds that site's other URLs (`challenge`), so each
does not meet the same wall, and hands the site to the person, as the batch's person mode says:
`fail` fails them with ChallengeRequired at once; `wait` keeps them waiting until someone passes
the check (`udl fetch solve`, in any udl: the queue looks at its waiting URLs every `recheck`
for what another udl's window did); `ask` opens a window too (udl.fetch.window), unless an
automation asked, as nobody looks out for a window then. udl.fetch.challenge tells of a site that
waits, once a streak. A waiting URL fails with ChallengeRequired after challenge_expiry, so every
batch ends, and udl.fetch.finished tells of it.

Batches and their URLs are records (udl.fetch.models), their pages files in [fetch] store_dir
(PageStore), readable by the user alone. A udl that stops leaves its batches `interrupted`
(recover() marks what a udl that was killed left, when no other udl runs); retry() runs what did
not finish. Batches older than `keep` go, with their pages, every hour while the queue runs.
"""

# System Imports
import asyncio
import contextlib
import logging
import random
import shutil
import stat
from collections import Counter
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol
from uuid import UUID

# Local Imports
from udl.automations.bus import EventBus, NewEvent
from udl.automations.events import FetchChallengeData, FetchFailure, FetchFinishedData
from udl.clock import Clock, pause
from udl.config import FetchSettings, PersonMode
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.records import RecordQuery
from udl.errors import (
    Cancelled,
    ChallengeRequired,
    ConfigError,
    DatabaseError,
    DoesNotExist,
    DownloadError,
    ErrorCode,
    UDLError,
)
from udl.fetch.challenges import ChallengeKind
from udl.fetch.content import FetchedPage
from udl.fetch.ladder import FetchOptions, FetchOutcome, Via
from udl.fetch.models import (
    BatchStatus,
    FetchBatch,
    FetchItem,
    ItemStatus,
    Passed,
    WaitingSite,
)
from udl.plugins.downloaders.base import CancelToken, cancellable

log = logging.getLogger(__name__)

RECHECK: Final = timedelta(seconds=5)
"""How often a queue looks at its waiting URLs (for what another udl's window did)."""
RETENTION_EVERY: Final = timedelta(hours=1)
JITTER: Final = 0.1
"""A site's starts are site_delay apart, give or take this share of it."""
_WINDOW_HINT: Final = (
    "A window opened in udl's browser, with a tab for the site: pass its check there, and its "
    "URLs go on by themselves."
)
_SOLVE_HINT: Final = (
    "Run `udl fetch solve` (solve_challenges) on udl's machine to pass the check in a browser "
    "window; the URLs then go on by themselves."
)


class PageFetcher(Protocol):
    """What reads a URL's page (the ladder's Fetcher)."""

    async def fetch(
        self, url: URL, options: FetchOptions, *, cancel: CancelToken
    ) -> FetchOutcome: ...

    async def settle(self) -> None:
        """Write back what a batch's plain requests kept (a batch ended)."""
        ...

    async def cleared(self, site: str) -> None:
        """A person passed the site's check: its fetches start in the browser."""
        ...


class Asker(Protocol):
    """Shows the sites that wait to the person (udl.fetch.window, through udl.fetch.kit)."""

    def unavailable(self) -> str | None:
        """Why no window can open (None when one can)."""
        ...

    def also(self, waiting: WaitingSite) -> bool:
        """Add a site to the window that is open (False when none is)."""
        ...

    async def ask(self, sites: Sequence[WaitingSite], passed: Passed) -> list[str]:
        """Show `sites` until the person passed them, closed the window, or the time is up.
        Returns the sites passed."""
        ...


# ---------------------------------------------------------------------------------------------
# The pages
# ---------------------------------------------------------------------------------------------


class PageStore:
    """Fetched pages' bodies: <root>/<batch id>/<item id>, files only their owner can read in
    folders only they can enter (a page read with the user's cookies is theirs). put(), get()
    and drop() block; write(), read() and remove() do the same in a thread."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def put(self, batch: UUID, item: UUID, body: bytes) -> Path:
        folder = self.root / str(batch)
        for made in (self.root, folder):
            made.mkdir(mode=0o700, parents=True, exist_ok=True)
            made.chmod(0o700)
        path = folder / str(item)
        path.touch(mode=0o600)
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        path.write_bytes(body)
        return path

    def get(self, path: str | Path) -> bytes:
        return Path(path).read_bytes()

    def drop(self, batch: UUID) -> None:
        shutil.rmtree(self.root / str(batch), ignore_errors=True)

    async def write(self, batch: UUID, item: UUID, body: bytes) -> Path:
        return await asyncio.to_thread(self.put, batch, item, body)

    async def read(self, path: str | Path) -> bytes:
        return await asyncio.to_thread(self.get, path)

    async def remove(self, batch: UUID) -> None:
        await asyncio.to_thread(self.drop, batch)


# ---------------------------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------------------------


@dataclass
class _Running:
    """A batch this queue runs."""

    record: FetchBatch
    items: dict[UUID, FetchItem]
    cancel: CancelToken = field(default_factory=CancelToken)
    tasks: dict[UUID, asyncio.Task[None]] = field(default_factory=dict[UUID, asyncio.Task[None]])
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    cancelling: bool = False
    walls: set[str] = field(default_factory=set[str])  # (sites whose check failed it: fail mode)


@dataclass
class _Hold:
    """A site whose URLs wait for a person to pass its check."""

    kind: ChallengeKind
    since: datetime
    told: bool = False  # (udl.fetch.challenge was published for it)


class FetchQueue:
    """Runs fetches in batches (see the module's docs), in `db`, as `runner`."""

    recheck: timedelta = RECHECK

    def __init__(
        self,
        db: DBManager,
        fetcher: PageFetcher,
        bus: EventBus,
        clock: Clock,
        settings: FetchSettings,
        *,
        runner: str,
        store: PageStore,
        asker: Asker | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.fetcher = fetcher
        self.bus = bus
        self.clock = clock
        self.settings = settings
        self.runner = runner
        self.store = store
        self.asker = asker
        self._showing: set[asyncio.Task[None]] = set()  # (windows shown)
        self._rng = rng or random.Random()  # noqa: S311 - (a delay's jitter, no secret)
        self._running: dict[UUID, _Running] = {}
        self._holds: dict[str, _Hold] = {}
        self._turns = asyncio.Semaphore(settings.max_concurrent)
        self._site_turns: dict[str, asyncio.Semaphore] = {}
        self._next_start: dict[str, datetime] = {}
        self._maintainer: asyncio.Task[None] | None = None
        self._waiting = asyncio.Event()  # (a URL began waiting: recheck)
        self._closing = False

    # -- asking ----------------------------------------------------------------------------

    async def submit(
        self,
        urls: Sequence[str],
        options: FetchOptions,
        *,
        person: PersonMode | None = None,
        label: str | None = None,
        asks: bool = True,
    ) -> FetchBatch:
        """Queue a batch of `urls` (normalised, repeats dropped, at most max_urls) and start
        fetching them. `person` is what to do at a check (by default [fetch] person); when
        `asks` is false (an automation asked), nobody is asked: it waits. Raises ConfigError."""
        parsed: dict[str, URL] = {}
        for text in urls:
            try:
                found = URL.parse(text)
            except ValueError as e:
                raise ConfigError(str(e)) from e
            parsed.setdefault(found.value, found)
        if not parsed:
            raise ConfigError("There are no URLs to fetch")
        if len(parsed) > self.settings.max_urls:
            raise ConfigError(
                f"A fetch takes at most {self.settings.max_urls} URLs, not {len(parsed)}",
                hint="Split them over several fetches, or raise [fetch] max_urls.",
            )
        mode = person or self.settings.person
        if mode is PersonMode.ASK and not asks:
            mode = PersonMode.WAIT
        batch = FetchBatch(label=label, options=options, person=mode, runner=self.runner)
        items = [
            FetchItem(batch_id=batch.id, url=found.value, site=found.site_key, position=n)
            for n, found in enumerate(parsed.values())
        ]
        batch = batch.model_copy(update={"counts": {ItemStatus.QUEUED: len(items)}})
        batch = await self.db.upsert_record(batch)
        running = _Running(record=batch, items={})
        for item in items:
            running.items[item.id] = await self.db.upsert_record(item)
        self._running[batch.id] = running
        for item in items:
            self._start(running, item.id)
        self._maintain()
        return batch

    async def batch(self, batch_id: UUID) -> FetchBatch:
        """A batch, as it is now. Raises DoesNotExist."""
        if (running := self._running.get(batch_id)) is not None:
            return running.record
        found = await self.db.get_record(FetchBatch, batch_id)
        if found is None:
            raise DoesNotExist(
                f"No fetch {batch_id}", hint="`udl fetch list` (list_fetches) lists the fetches."
            )
        return found

    async def items(self, batch_id: UUID) -> list[FetchItem]:
        """A batch's URLs, in their order. Raises DoesNotExist."""
        if (running := self._running.get(batch_id)) is not None:
            found = list(running.items.values())
        else:
            await self.batch(batch_id)
            query = RecordQuery(where={"batch_id": batch_id})
            found = await self.db.list_records(FetchItem, query)
        return sorted(found, key=lambda item: item.position)

    async def wait(self, batch_id: UUID, *, within: timedelta | None = None) -> FetchBatch:
        """Wait for a batch this queue runs to end (or to wait only for a person nobody asked
        for), at most `within`; giving up leaves it running. Gives the batch as it is then."""
        running = self._running.get(batch_id)
        if running is None:
            return await self.batch(batch_id)
        with contextlib.suppress(TimeoutError):
            async with self.clock.timeout(within) if within else contextlib.nullcontext():
                while not self._settled(running):
                    running.changed.clear()
                    await running.changed.wait()
        return running.record

    def _settled(self, running: _Running) -> bool:
        """Whether a batch has ended, or only waits for a person nobody is being shown its
        check."""
        status = running.record.status
        return status.ended or (status is BatchStatus.WAITING and not self._showing)

    # -- cancelling, stopping, retrying ----------------------------------------------------

    async def cancel(self, batch_id: UUID) -> FetchBatch:
        """Cancel a batch this queue runs: its running fetches stop, and its other URLs are
        not fetched. A batch that ended is left as it is. Raises DoesNotExist (also for one
        another udl runs)."""
        running = self._running.get(batch_id)
        if running is None or running.record.status.ended:
            found = await self.batch(batch_id)
            if found.status.ended:
                return found
            raise self._elsewhere(found)
        running.cancelling = True
        await self._stop(running)
        for item in list(running.items.values()):
            if not item.status.ended:
                await self._update(running, item, status=ItemStatus.CANCELLED)
        await self._refresh(running)
        return running.record

    async def retry(
        self,
        batch_id: UUID,
        *,
        items: Sequence[UUID] | None = None,
        via: Via | None = None,
    ) -> FetchBatch:
        """Put a batch's failed, cancelled and interrupted URLs (or only `items` of them) back
        in the queue, here, starting at `via` if it says. Raises DoesNotExist (also for a batch
        another udl runs)."""
        found = await self.batch(batch_id)
        running = self._running.get(batch_id)
        if running is None and not found.status.ended:
            raise self._elsewhere(found)
        stored = await self.items(batch_id)
        if running is not None and running.record.status.ended:
            running = None  # (it ended: it runs again from what was stored)
        again = [
            item
            for item in stored
            if item.status in (ItemStatus.FAILED, ItemStatus.CANCELLED, ItemStatus.INTERRUPTED)
            and (items is None or item.id in items)
        ]
        if not again:
            return found
        options = found.options if via is None else found.options.model_copy(update={"via": via})
        record = await self.db.upsert_record(
            found.model_copy(
                update={
                    "options": options,
                    "runner": self.runner,
                    "status": BatchStatus.RUNNING,
                    "finished_at": None,
                }
            )
        )
        if running is None:
            running = _Running(record=record, items={item.id: item for item in stored})
            self._running[batch_id] = running
        running.record = record
        running.cancelling = False
        running.walls.clear()
        for item in again:
            await self._update(
                running,
                item,
                status=ItemStatus.QUEUED,
                error=None,
                challenge=None,
                challenged_at=None,
                note=None,
                finished_at=None,
            )
            self._start(running, item.id)
        await self._refresh(running)
        self._maintain()
        return running.record

    async def recover(self) -> int:
        """Mark the batches a udl that stopped left (queued, running or waiting) interrupted,
        with their URLs that had not ended. Run it only when no other udl runs. Returns how
        many."""
        recovered = 0
        for status in (BatchStatus.QUEUED, BatchStatus.RUNNING, BatchStatus.WAITING):
            for batch in await self.db.list_records(
                FetchBatch, RecordQuery(where={"status": status})
            ):
                query = RecordQuery(where={"batch_id": batch.id})
                items = await self.db.list_records(FetchItem, query)
                for item in items:
                    if not item.status.ended:
                        await self.db.upsert_record(
                            item.model_copy(update={"status": ItemStatus.INTERRUPTED})
                        )
                counts = Counter(
                    ItemStatus.INTERRUPTED if not item.status.ended else item.status
                    for item in items
                )
                await self.db.upsert_record(
                    batch.model_copy(
                        update={
                            "status": BatchStatus.INTERRUPTED,
                            "counts": dict(counts),
                            "finished_at": self.clock.now(),
                        }
                    )
                )
                recovered += 1
        if recovered:
            log.info("Marked %d fetches that udl did not finish as interrupted", recovered)
        return recovered

    async def prune(self) -> int:
        """Delete the batches older than [fetch] keep, with their URLs and pages (not those
        this queue runs). Returns how many."""
        cutoff = self.clock.now() - self.settings.keep
        old = await self.db.list_records(FetchBatch, RecordQuery(until=cutoff))
        pruned = 0
        for batch in old:
            if batch.id in self._running:
                continue
            for item in await self.db.list_records(
                FetchItem, RecordQuery(where={"batch_id": batch.id})
            ):
                await self.db.delete_record(FetchItem, item.id)
            await self.store.remove(batch.id)
            await self.db.delete_record(FetchBatch, batch.id)
            pruned += 1
        if pruned:
            log.info(
                "Deleted %d fetches older than %s, with their pages", pruned, self.settings.keep
            )
        return pruned

    async def shutdown(self) -> None:
        """Stop: the batches this queue runs are left interrupted (retry_fetch runs the rest)."""
        self._closing = True
        for showing in list(self._showing):
            showing.cancel()
        if self._showing:
            await asyncio.wait(self._showing)
        if self._maintainer is not None:
            self._maintainer.cancel()
            await asyncio.wait([self._maintainer])
            self._maintainer = None
        for running in list(self._running.values()):
            await self._stop(running)
            for item in list(running.items.values()):
                if not item.status.ended:
                    await self._update(running, item, status=ItemStatus.INTERRUPTED)
            await self._refresh(running)
        try:
            await self.fetcher.settle()
        except UDLError as e:
            log.warning("Could not keep the cookies sites set: %s", e)

    def _elsewhere(self, batch: FetchBatch) -> DoesNotExist:
        return DoesNotExist(
            f"The fetch {batch.id} is run by another udl ({batch.runner}), not this one",
            hint="Ask the udl that runs it (`udl serve`'s API or MCP server, or the `udl fetch` "
            "that started it), or wait for it to end.",
        )

    async def _stop(self, running: _Running) -> None:
        running.cancel.cancel()
        tasks = [task for task in running.tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)

    # -- running a URL ---------------------------------------------------------------------

    def _start(self, running: _Running, item_id: UUID) -> None:
        running.tasks[item_id] = asyncio.create_task(
            self._run(running, item_id), name=f"udl fetch {item_id}"
        )

    @asynccontextmanager
    async def _turn(self, site: str) -> AsyncGenerator[None]:
        """Wait for the site's turn, then for a turn."""
        site_turn = self._site_turns.setdefault(site, asyncio.Semaphore(self.settings.max_per_site))
        async with site_turn, self._turns:
            yield

    async def _pace(self, site: str) -> None:
        """Wait until the site may be asked again: site_delay (give or take a tenth) after the
        last start on it."""
        now = self.clock.now()
        start = max(now, self._next_start.get(site, now))
        delay = self.settings.site_delay * self._rng.uniform(1 - JITTER, 1 + JITTER)
        self._next_start[site] = start + delay
        await self.clock.sleep_until(start)

    async def _run(self, running: _Running, item_id: UUID) -> None:
        site = running.items[item_id].site
        try:
            async with self._turn(site):
                if not await self._held(running, item_id):
                    await self._pace(site)
                    if not await self._held(running, item_id):
                        await self._fetch(running, item_id)
        except Cancelled:
            await self._end(running, item_id, ItemStatus.CANCELLED)
        except asyncio.CancelledError:
            task = asyncio.current_task()
            while task is not None and task.cancelling():
                task.uncancel()
            ended = ItemStatus.INTERRUPTED if self._closing else ItemStatus.CANCELLED
            await self._end(running, item_id, ended)
        except Exception as e:  # (a bug: the URL fails, never the queue)
            log.exception("The fetch of %s failed unexpectedly", running.items[item_id].url)
            error = DownloadError(f"{type(e).__name__}: {e}").to_info()
            await self._update(
                running, running.items[item_id], status=ItemStatus.FAILED, error=error
            )
        await self._refresh(running)

    async def _end(self, running: _Running, item_id: UUID, status: ItemStatus) -> None:
        item = running.items[item_id]
        if not item.status.ended:
            await self._update(running, item, status=status)

    async def _fetch(self, running: _Running, item_id: UUID) -> None:
        item = await self._update(running, running.items[item_id], status=ItemStatus.RUNNING)
        await self._refresh(running)
        options = running.record.options
        outcome = await cancellable(
            self.fetcher.fetch(URL.parse(item.url), options, cancel=running.cancel),
            running.cancel,
            message="The fetch was cancelled",
        )
        if outcome.page is not None:
            page = outcome.page
            body = await self.store.write(running.record.id, item.id, page.body)
            await self._update(
                running,
                item,
                status=ItemStatus.SUCCEEDED,
                via=outcome.via,
                attempts=outcome.attempts,
                title=page.title,
                final_url=page.final_url,
                http_status=page.status,
                content_type=page.content_type,
                charset=page.charset,
                headers=page.headers,
                size=len(page.body),
                truncated=page.truncated,
                body=str(body),
                note=f"Cut at {self.settings.max_page} bytes" if page.truncated else None,
            )
        elif outcome.challenge is not None:
            item = await self._update(running, item, attempts=outcome.attempts)
            hold = self._holds.get(item.site)
            if hold is None and running.record.person is not PersonMode.FAIL:
                self._holds[item.site] = _Hold(kind=outcome.challenge.kind, since=self.clock.now())
            await self._wait_for_person(running, item, outcome.challenge.kind)
        else:
            await self._update(
                running,
                item,
                status=ItemStatus.FAILED,
                attempts=outcome.attempts,
                error=outcome.error,
            )

    async def _held(self, running: _Running, item_id: UUID) -> bool:
        """Whether the URL waits behind its site's check (or fails at it), rather than being
        fetched."""
        item = running.items[item_id]
        if item.site in running.walls:
            await self._wait_for_person(running, item, None)
            return True
        hold = self._holds.get(item.site)
        if hold is None:
            return False
        await self._wait_for_person(running, item, hold.kind)
        return True

    async def _wait_for_person(
        self, running: _Running, item: FetchItem, kind: ChallengeKind | None
    ) -> None:
        """A URL met its site's check: it fails (fail mode) or waits for a person."""
        mode = running.record.person
        if mode is PersonMode.FAIL:
            running.walls.add(item.site)
            what = f"a {kind} check" if kind is not None else "a check"
            error = ChallengeRequired(
                f"{item.url} waits behind {what} that nobody was asked to pass (person: fail)",
                hint=_SOLVE_HINT,
            )
            await self._update(
                running,
                item,
                status=ItemStatus.FAILED,
                challenge=kind,
                error=error.to_info(),
            )
            return
        item = await self._update(
            running,
            item,
            status=ItemStatus.CHALLENGE,
            challenge=kind,
            challenged_at=self.clock.now(),
        )
        self._waiting.set()
        hold = self._holds.get(item.site)
        if hold is not None and not hold.told:
            hold.told = True
            mode = await self._ask(running, item, hold)
            await self._tell(running, item, hold, mode)

    async def _ask(self, running: _Running, item: FetchItem, hold: _Hold) -> PersonMode:
        """Show the site to the person, when the batch says to ask and a window can open.
        Returns what was done: ask (a window shows it), or wait (and the URL's note says why
        no window opened)."""
        if running.record.person is not PersonMode.ASK:
            return PersonMode.WAIT
        problem = self.asker.unavailable() if self.asker else "this udl cannot open a window"
        if self.asker is None or problem is not None:
            await self._update(
                running,
                item,
                note=f"No window opened for its check ({problem}): it waits for `udl fetch solve`",
            )
            return PersonMode.WAIT
        waiting = WaitingSite(site=item.site, url=item.url, item=item.id, kind=hold.kind)
        if not self.asker.also(waiting):
            showing = asyncio.create_task(self._show(self.asker, waiting), name="udl fetch window")
            self._showing.add(showing)
            showing.add_done_callback(self._shown)
        return PersonMode.ASK

    async def _show(self, asker: Asker, waiting: WaitingSite) -> None:
        try:
            await asker.ask([waiting], self.passed)
        except UDLError as e:
            log.warning("udl could not show you the check of %s: %s", waiting.site, e)

    def _shown(self, showing: asyncio.Task[None]) -> None:
        """A window closed: batches that only wait are settled now."""
        self._showing.discard(showing)
        for running in self._running.values():
            running.changed.set()

    async def passed(self, waiting: WaitingSite, page: FetchedPage) -> None:
        """A person passed a site's check: `page` is its waiting URL's (written to its record,
        whichever udl runs its batch), the site's other waiting URLs go back in the queue
        (this udl's at once; another udl's at its next recheck), and the site's fetches start
        in the browser."""
        owner = next((r for r in self._running.values() if waiting.item in r.items), None)
        item = (
            owner.items[waiting.item]
            if owner
            else await self.db.get_record(FetchItem, waiting.item)
        )
        if item is not None and not item.status.ended:
            body = await self.store.write(item.batch_id, item.id, page.body)
            answer: dict[str, Any] = {
                "status": ItemStatus.SUCCEEDED,
                "via": Via.PERSON,
                "title": page.title,
                "final_url": page.final_url,
                "http_status": page.status,
                "content_type": page.content_type,
                "charset": page.charset,
                "headers": page.headers,
                "size": len(page.body),
                "truncated": page.truncated,
                "body": str(body),
                "error": None,
                "note": None,
            }
            if owner is not None:
                await self._update(owner, item, **answer)
            else:
                answer["finished_at"] = self.clock.now()
                await self.db.upsert_record(item.model_copy(update=answer))
        self._holds.pop(waiting.site, None)
        again = {"status": ItemStatus.QUEUED, "challenged_at": None}
        ours: set[UUID] = set()
        for running in self._running.values():
            for found in list(running.items.values()):
                if found.site == waiting.site and found.status is ItemStatus.CHALLENGE:
                    ours.add(found.id)
                    await self._update(running, found, **again)
                    self._start(running, found.id)
        query = RecordQuery(where={"site": waiting.site, "status": ItemStatus.CHALLENGE})
        for found in await self.db.list_records(FetchItem, query):
            if found.id not in ours:  # (another udl's: it picks them up at its next recheck)
                await self.db.upsert_record(found.model_copy(update=again))
        await self.fetcher.cleared(waiting.site)
        for running in list(self._running.values()):
            await self._refresh(running)

    async def _tell(
        self, running: _Running, item: FetchItem, hold: _Hold, mode: PersonMode
    ) -> None:
        """Publish udl.fetch.challenge: the site waits for a person (once a streak)."""
        waiting = sum(
            1
            for other in self._running.values()
            for found in other.items.values()
            if found.site == item.site and not found.status.ended
        )
        data = FetchChallengeData(
            batch=running.record.id,
            site=item.site,
            url=item.url,
            kind=hold.kind,
            waiting=waiting,
            mode=mode,
            hint=_WINDOW_HINT if mode is PersonMode.ASK else _SOLVE_HINT,
        )
        try:
            await self.bus.publish(
                NewEvent(
                    type="udl.fetch.challenge",
                    data=data.model_dump(mode="json"),
                    dedupe_key=f"fetch-challenge:{item.site}:{hold.since.isoformat()}",
                )
            )
        except UDLError as e:
            log.warning("Could not tell that %s waits for a person: %s", item.site, e)

    # -- keeping track ---------------------------------------------------------------------

    async def _update(self, running: _Running, item: FetchItem, **changes: Any) -> FetchItem:
        if changes.get("status") in (ItemStatus.SUCCEEDED, ItemStatus.FAILED, ItemStatus.CANCELLED):
            changes.setdefault("finished_at", self.clock.now())
        changed = item.model_copy(update=changes)
        running.items[item.id] = changed
        try:
            changed = await self.db.upsert_record(changed)
        except DatabaseError as e:
            log.warning("Could not save the fetch of %s: %s", item.url, e)
        running.items[item.id] = changed
        return changed

    async def _refresh(self, running: _Running) -> None:
        """Work out the batch's status and counts from its URLs, keep them, and tell of its
        end."""
        items = list(running.items.values())
        counts = dict(Counter(item.status for item in items))
        before = running.record
        if all(item.status.ended for item in items):
            if self._closing and ItemStatus.INTERRUPTED in counts:
                status = BatchStatus.INTERRUPTED
            elif running.cancelling:
                status = BatchStatus.CANCELLED
            else:
                status = BatchStatus.FINISHED
        elif any(item.status in (ItemStatus.QUEUED, ItemStatus.RUNNING) for item in items):
            started = any(item.status is not ItemStatus.QUEUED for item in items)
            status = BatchStatus.RUNNING if started else BatchStatus.QUEUED
        else:
            status = BatchStatus.WAITING
        if (status, counts) == (before.status, before.counts):
            return
        changes: dict[str, Any] = {"status": status, "counts": counts}
        ended = status.ended and not before.status.ended
        if ended:
            changes["finished_at"] = self.clock.now()
        try:
            running.record = await self.db.upsert_record(before.model_copy(update=changes))
        except DatabaseError as e:
            running.record = before.model_copy(update=changes)
            log.warning("Could not save the fetch %s: %s", before.id, e)
        self._release_holds()
        running.changed.set()
        if ended:
            await self._ended(running)
            if running.record.status.ended:  # (not retried meanwhile)
                self._running.pop(running.record.id, None)

    def _release_holds(self) -> None:
        """Let go of the sites none of whose URLs wait any more."""
        waiting = {
            item.site
            for running in self._running.values()
            for item in running.items.values()
            if item.status is ItemStatus.CHALLENGE
        }
        for site in [site for site in self._holds if site not in waiting]:
            fetching = any(
                item.site == site and item.status is ItemStatus.RUNNING
                for running in self._running.values()
                for item in running.items.values()
            )
            if not fetching:
                del self._holds[site]

    async def _ended(self, running: _Running) -> None:
        """A batch ended: write back what its plain requests kept, and tell of it (unless this
        udl is stopping: it was interrupted)."""
        try:
            await self.fetcher.settle()
        except UDLError as e:
            log.warning("Could not keep the cookies sites set: %s", e)
        if running.record.status is BatchStatus.INTERRUPTED:
            return
        items = sorted(running.items.values(), key=lambda item: item.position)
        counts = Counter(item.status for item in items)
        data = FetchFinishedData(
            batch=running.record.id,
            label=running.record.label,
            succeeded=counts[ItemStatus.SUCCEEDED],
            failed=counts[ItemStatus.FAILED],
            cancelled=counts[ItemStatus.CANCELLED],
            failures=[
                FetchFailure(
                    url=item.url, code=item.error.code if item.error else ErrorCode.UDL_ERROR
                )
                for item in items
                if item.status is ItemStatus.FAILED
            ],
        )
        try:
            await self.bus.publish(
                NewEvent(
                    type="udl.fetch.finished",
                    data=data.model_dump(mode="json"),
                    # (A retried batch ends again: each end is told of.)
                    dedupe_key=f"fetch:{running.record.id}:{running.record.finished_at}",
                )
            )
        except UDLError as e:
            log.warning("Could not tell that the fetch %s ended: %s", running.record.id, e)

    # -- the queue's own upkeep ------------------------------------------------------------

    def _maintain(self) -> None:
        """Start the queue's upkeep (rechecking waiting URLs, retention), if it has not."""
        if self._maintainer is None and not self._closing:
            self._maintainer = asyncio.create_task(self._upkeep(), name="udl fetch upkeep")

    async def _upkeep(self) -> None:
        pruned_at = self.clock.now()
        while True:
            waiting = self._has_waiting()
            if not waiting:
                self._waiting.clear()
            await pause(self.clock, self.recheck if waiting else RETENTION_EVERY, self._waiting)
            try:
                if self._has_waiting():
                    await self._recheck()
                if self.clock.now() - pruned_at >= RETENTION_EVERY:
                    pruned_at = self.clock.now()
                    await self.prune()
            except DatabaseError as e:
                log.warning("The fetch queue's upkeep failed: %s", e)

    def _has_waiting(self) -> bool:
        return any(
            item.status is ItemStatus.CHALLENGE
            for running in self._running.values()
            for item in running.items.values()
        )

    async def _recheck(self) -> None:
        """Look at the URLs that wait: what another udl's window did (a page written, the
        site's other URLs queued again), and those that waited too long."""
        expiry = self.settings.challenge_expiry
        for running in list(self._running.values()):
            for item in [i for i in running.items.values() if i.status is ItemStatus.CHALLENGE]:
                stored = await self.db.get_record(FetchItem, item.id)
                if stored is not None and stored.status is not ItemStatus.CHALLENGE:
                    running.items[item.id] = stored
                    if stored.status is ItemStatus.QUEUED:
                        self._holds.pop(item.site, None)  # (it was passed)
                        self._start(running, item.id)
                elif (
                    item.challenged_at is not None
                    and self.clock.now() - item.challenged_at >= expiry
                ):
                    error = ChallengeRequired(
                        f"Nobody passed the {item.challenge or ''} check in front of {item.url} "
                        f"within {expiry}".replace("the  check", "the check"),
                        hint=_SOLVE_HINT,
                    )
                    await self._update(
                        running, item, status=ItemStatus.FAILED, error=error.to_info()
                    )
            await self._refresh(running)
