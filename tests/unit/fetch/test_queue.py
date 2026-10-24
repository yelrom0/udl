"""
-----------------
- test_queue.py -
-----------------

The fetch queue (udl.fetch.queue), on a FakeClock with a fake fetcher: its limits and delays, a
site's check holding its other URLs, what each person mode does, cancelling, a udl that stops,
retrying, retention, the pages it keeps, and its events
"""

# System Imports
import asyncio
import itertools
import random
import stat
from collections import Counter
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

# Package Imports
import pytest

# Local Imports
from support.clock import T0, FakeClock
from support.engine import databases, registry, settings
from udl.automations.bus import EventBus
from udl.automations.events import EventRecord, EventTypes
from udl.config import AutomationsSettings, FetchSettings, PersonMode
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.records import RecordQuery
from udl.errors import ConfigError, DoesNotExist, ErrorCode, NotFound
from udl.fetch.challenges import ChallengeKind, Detection
from udl.fetch.content import FetchedPage
from udl.fetch.ladder import FetchOptions, FetchOutcome, Via
from udl.fetch.models import BatchStatus, FetchBatch, FetchItem, ItemStatus
from udl.fetch.queue import FetchQueue, PageStore
from udl.plugins.downloaders.base import CancelToken

CHECK = Detection(kind=ChallengeKind.CLOUDFLARE, passable=True, evidence=["test"])


@dataclass
class Script:
    """What fetching a URL comes to, and how long it takes."""

    outcome: str = "page"  # page, challenge, missing
    takes: timedelta = timedelta(seconds=1)


@dataclass
class FakeFetcher:
    """Fetches on the clock: each URL as its Script says (a page, by default), counting how many
    fetches run at once, in all and per site, and when each started."""

    clock: FakeClock
    scripts: dict[str, Script] = field(default_factory=dict[str, Script])
    started: list[tuple[datetime, str]] = field(default_factory=list[tuple[datetime, str]])
    running: Counter[str] = field(default_factory=Counter[str])
    most: int = 0
    most_per_site: int = 0
    settled: int = 0
    blocks: bool = False  # (a fetch never ends unless cancelled)

    async def fetch(self, url: URL, options: FetchOptions, *, cancel: CancelToken) -> FetchOutcome:
        self.started.append((self.clock.now(), url.value))
        self.running[url.site_key] += 1
        self.most = max(self.most, sum(self.running.values()))
        self.most_per_site = max(self.most_per_site, max(self.running.values()))
        try:
            script = self.scripts.get(url.value, Script())
            if self.blocks:
                await asyncio.Event().wait()
            await self.clock.sleep(script.takes)
        finally:
            self.running[url.site_key] -= 1
        if script.outcome == "challenge":
            return FetchOutcome(url=url.value, challenge=CHECK)
        if script.outcome == "missing":
            error = NotFound(f"HTTP 404 for {url.value}", tool="static").to_info()
            return FetchOutcome(url=url.value, error=error)
        page = FetchedPage(
            url=url.value,
            final_url=url.value,
            status=200,
            content_type="text/html",
            title=f"Page {url.value}",
            via="static",
            body=f"<title>{url.value}</title>".encode(),
        )
        return FetchOutcome(url=url.value, page=page, via=Via.STATIC)

    async def settle(self) -> None:
        self.settled += 1


class InlineStore(PageStore):
    """The page store, without threads (which a FakeClock cannot wait for)."""

    async def write(self, batch: UUID, item: UUID, body: bytes) -> Path:
        return self.put(batch, item, body)

    async def read(self, path: str | Path) -> bytes:
        return self.get(path)

    async def remove(self, batch: UUID) -> None:
        self.drop(batch)


class Kit:
    def __init__(self, db: DBManager, clock: FakeClock, store: Path, **fields: Any) -> None:
        self.db = db
        self.clock = clock
        fields.setdefault("site_delay", timedelta(seconds=1))
        self.settings = FetchSettings.model_validate({"store_dir": store, **fields})
        self.fetcher = FakeFetcher(self.clock)
        self.bus = EventBus(db, EventTypes.default(), self.clock, AutomationsSettings())
        self.store = InlineStore(store)
        self.queue = self.make()

    def make(self, runner: str = "here") -> FetchQueue:
        return FetchQueue(
            self.db,
            self.fetcher,
            self.bus,
            self.clock,
            self.settings,
            runner=runner,
            store=self.store,
            rng=random.Random(1),  # noqa: S311
        )

    async def run(
        self, *urls: str, over: timedelta = timedelta(minutes=5), **fields: Any
    ) -> FetchBatch:
        batch = await self.queue.submit(list(urls), FetchOptions(), **fields)
        await self.clock.advance(over)
        return await self.queue.batch(batch.id)

    async def items(self, batch: FetchBatch) -> list[FetchItem]:
        return await self.queue.items(batch.id)

    async def events(self, kind: str) -> list[EventRecord]:
        return await self.db.list_records(EventRecord, RecordQuery(where={"type": kind}))

    async def until(self, done: Callable[[], bool], step: timedelta = timedelta(seconds=1)) -> None:
        for _ in range(600):
            if done():
                return
            await self.clock.advance(step)
        raise AssertionError("it never happened")


@asynccontextmanager
async def kit_of(tmp_path: Path, **fields: Any) -> AsyncGenerator[Kit]:
    clock = FakeClock()
    async with databases(settings(tmp_path), registry(), clock=clock) as db:
        made = Kit(db, clock, tmp_path / "fetched", **fields)
        yield made
        await made.queue.shutdown()


@pytest.fixture
async def kit(tmp_path: Path) -> AsyncGenerator[Kit]:
    async with kit_of(tmp_path) as made:
        yield made


def url(site: str, n: int) -> str:
    return f"https://{site}/page/{n}"


def statuses(items: list[FetchItem]) -> list[ItemStatus]:
    return [item.status for item in items]


# ---------------------------------------------------------------------------------------------
# A batch
# ---------------------------------------------------------------------------------------------


async def test_a_batchs_urls_are_fetched_and_kept(kit: Kit) -> None:
    batch = await kit.run(url("a.example", 1), url("b.example", 1), label="two")

    assert (batch.status, batch.label, batch.runner) == (BatchStatus.FINISHED, "two", "here")
    assert batch.counts == {ItemStatus.SUCCEEDED: 2}
    assert batch.finished_at is not None
    items = await kit.items(batch)
    assert [(item.url, item.status, item.via, item.position) for item in items] == [
        (url("a.example", 1), ItemStatus.SUCCEEDED, Via.STATIC, 0),
        (url("b.example", 1), ItemStatus.SUCCEEDED, Via.STATIC, 1),
    ]
    first = items[0]
    assert (first.title, first.http_status, first.content_type, first.site) == (
        f"Page {url('a.example', 1)}",
        200,
        "text/html",
        "a.example",
    )
    assert first.body is not None
    assert kit.store.get(first.body) == f"<title>{url('a.example', 1)}</title>".encode()


async def test_its_pages_are_the_users_alone(kit: Kit) -> None:
    batch = await kit.run(url("a.example", 1))

    [item] = await kit.items(batch)
    assert item.body is not None
    body = Path(item.body)
    assert stat.S_IMODE(body.stat().st_mode) == 0o600  # noqa: ASYNC240
    assert stat.S_IMODE(body.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(body.parent.parent.stat().st_mode) == 0o700
    assert body.parent.name == str(batch.id)


async def test_urls_are_normalised_and_repeats_dropped(kit: Kit) -> None:
    batch = await kit.run(
        "https://A.example/page/1?utm_source=x", "a.example/page/1", url("a.example", 2)
    )

    assert [item.url for item in await kit.items(batch)] == [
        url("a.example", 1),
        url("a.example", 2),
    ]


async def test_a_batch_of_too_many_urls_is_refused(kit: Kit) -> None:
    kit.queue = FetchQueue(
        kit.db,
        kit.fetcher,
        kit.bus,
        kit.clock,
        kit.settings.model_copy(update={"max_urls": 2}),
        runner="here",
        store=kit.store,
    )

    with pytest.raises(ConfigError, match="at most 2"):
        await kit.queue.submit([url("a.example", n) for n in range(3)], FetchOptions())


async def test_what_is_not_a_url_is_refused(kit: Kit) -> None:
    with pytest.raises(ConfigError, match="ftp://x"):
        await kit.queue.submit(["ftp://x"], FetchOptions())


async def test_a_url_that_fails_is_failed_with_why(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 1)] = Script("missing")

    batch = await kit.run(url("a.example", 1), url("a.example", 2))

    assert batch.counts == {ItemStatus.FAILED: 1, ItemStatus.SUCCEEDED: 1}
    failed = (await kit.items(batch))[0]
    assert failed.error is not None
    assert failed.error.code is ErrorCode.NOT_FOUND


async def test_an_unknown_batch_does_not_exist(kit: Kit) -> None:
    with pytest.raises(DoesNotExist):
        await kit.queue.batch(UUID(int=1))


# ---------------------------------------------------------------------------------------------
# Limits and delays
# ---------------------------------------------------------------------------------------------


async def test_a_batch_of_30_urls_over_five_sites_keeps_every_limit_and_ends_once(
    kit: Kit,
) -> None:
    sites = [f"{name}.example" for name in "abcde"]
    urls = [url(site, n) for site in sites for n in range(6)]

    batch = await kit.run(*urls, over=timedelta(minutes=10))

    assert batch.status is BatchStatus.FINISHED
    assert batch.counts == {ItemStatus.SUCCEEDED: 30}
    assert kit.fetcher.most <= kit.settings.max_concurrent
    assert kit.fetcher.most == 4
    assert kit.fetcher.most_per_site == 1
    for site in sites:
        starts = [when for when, found in kit.fetcher.started if URL.parse(found).site_key == site]
        gaps = [later - earlier for earlier, later in itertools.pairwise(starts)]
        assert min(gaps) >= timedelta(seconds=0.9)
    assert len(await kit.events("udl.fetch.finished")) == 1
    assert kit.fetcher.settled == 1


async def test_a_sites_requests_are_site_delay_apart_give_or_take_a_tenth(kit: Kit) -> None:
    kit.fetcher.scripts = {url("a.example", n): Script(takes=timedelta(0)) for n in range(5)}

    await kit.run(*(url("a.example", n) for n in range(5)))

    starts = [when for when, _ in kit.fetcher.started]
    gaps = [(later - earlier).total_seconds() for earlier, later in itertools.pairwise(starts)]
    assert all(0.9 <= gap <= 1.1 for gap in gaps)
    assert len(set(gaps)) > 1  # (jittered)


async def test_sites_take_turns_so_a_big_one_holds_up_none(kit: Kit) -> None:
    big = [url("big.example", n) for n in range(20)]

    await kit.run(*big, url("small.example", 1))

    small = next(when for when, found in kit.fetcher.started if "small" in found)
    assert small == T0


async def test_max_per_site_fetches_of_a_site_run_at_once(tmp_path: Path) -> None:
    async with kit_of(tmp_path, max_per_site=2, site_delay=timedelta(0)) as made:
        await made.run(*(url("a.example", n) for n in range(6)))

    assert made.fetcher.most_per_site == 2


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------


async def test_a_sites_check_holds_its_other_urls(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 0)] = Script("challenge")
    urls = [url("a.example", n) for n in range(4)]

    batch = await kit.run(*urls, url("b.example", 1), person=PersonMode.WAIT)

    assert batch.status is BatchStatus.WAITING
    assert batch.counts == {ItemStatus.CHALLENGE: 4, ItemStatus.SUCCEEDED: 1}
    fetched = [found for _, found in kit.fetcher.started]
    assert fetched.count(url("a.example", 0)) == 1
    assert not set(urls[1:]) & set(fetched)  # (each did not meet the same wall)
    held = (await kit.items(batch))[1]
    assert (held.challenge, held.challenged_at) == (
        ChallengeKind.CLOUDFLARE,
        T0 + timedelta(seconds=1),
    )


async def test_a_waiting_site_is_told_of_once(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 0)] = Script("challenge")

    batch = await kit.run(*(url("a.example", n) for n in range(3)), person=PersonMode.WAIT)
    await kit.run(url("a.example", 9), person=PersonMode.WAIT)

    [told] = await kit.events("udl.fetch.challenge")
    assert told.data == {
        "batch": str(batch.id),
        "site": "a.example",
        "url": url("a.example", 0),
        "kind": "cloudflare",
        "waiting": 3,
        "mode": "wait",
        "hint": told.data["hint"],
    }
    assert "udl fetch solve" in str(told.data["hint"])


async def test_in_fail_mode_a_check_fails_the_sites_urls_at_once(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 0)] = Script("challenge")

    batch = await kit.run(*(url("a.example", n) for n in range(3)), person=PersonMode.FAIL)

    assert batch.status is BatchStatus.FINISHED
    items = await kit.items(batch)
    assert statuses(items) == [ItemStatus.FAILED] * 3
    assert {item.error.code for item in items if item.error} == {ErrorCode.CHALLENGE_REQUIRED}
    assert len(kit.fetcher.started) == 1
    assert await kit.events("udl.fetch.challenge") == []


async def test_until_a_window_can_be_opened_ask_waits(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 0)] = Script("challenge")

    batch = await kit.run(url("a.example", 0), person=PersonMode.ASK)

    assert batch.status is BatchStatus.WAITING
    [told] = await kit.events("udl.fetch.challenge")
    assert told.data["mode"] == "wait"


async def test_waiting_urls_fail_after_challenge_expiry(tmp_path: Path) -> None:
    async with kit_of(tmp_path, challenge_expiry=timedelta(hours=1)) as kit:
        kit.fetcher.scripts[url("a.example", 0)] = Script("challenge")
        batch = await kit.run(url("a.example", 0), url("a.example", 1), person=PersonMode.WAIT)

        await kit.clock.advance(timedelta(hours=1))

        ended = await kit.queue.batch(batch.id)
        items = await kit.items(ended)
    assert ended.status is BatchStatus.FINISHED
    assert {item.error.code for item in items if item.error} == {ErrorCode.CHALLENGE_REQUIRED}


async def test_a_site_passed_elsewhere_has_its_urls_fetched_again_within_a_recheck(
    kit: Kit,
) -> None:
    kit.fetcher.scripts[url("a.example", 0)] = Script("challenge")
    batch = await kit.run(url("a.example", 0), url("a.example", 1), person=PersonMode.WAIT)
    kit.fetcher.scripts.clear()  # (the site was passed: its pages come now)

    # (Another udl's window passed it: the challenged URL has its page, the other is queued.)
    first, second = await kit.items(batch)
    await kit.db.upsert_record(
        first.model_copy(update={"status": ItemStatus.SUCCEEDED, "via": Via.PERSON})
    )
    await kit.db.upsert_record(second.model_copy(update={"status": ItemStatus.QUEUED}))
    await kit.clock.advance(timedelta(seconds=5) + timedelta(seconds=5))

    ended = await kit.queue.batch(batch.id)
    assert ended.status is BatchStatus.FINISHED
    assert ended.counts == {ItemStatus.SUCCEEDED: 2}
    assert [found for _, found in kit.fetcher.started][-1] == url("a.example", 1)


async def test_an_automation_never_asks(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 0)] = Script("challenge")

    batch = await kit.run(url("a.example", 0), person=PersonMode.ASK, asks=False)

    assert batch.person is PersonMode.WAIT


# ---------------------------------------------------------------------------------------------
# Waiting for a batch
# ---------------------------------------------------------------------------------------------


async def test_waiting_for_a_batch_waits_for_its_end(kit: Kit) -> None:
    batch = await kit.queue.submit([url("a.example", 1)], FetchOptions())
    waiting = asyncio.create_task(kit.queue.wait(batch.id))

    await kit.clock.advance(timedelta(seconds=3))

    assert (await waiting).status is BatchStatus.FINISHED


async def test_waiting_gives_up_after_its_timeout_leaving_the_batch_running(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 1)] = Script(takes=timedelta(minutes=5))
    batch = await kit.queue.submit([url("a.example", 1)], FetchOptions())
    waiting = asyncio.create_task(kit.queue.wait(batch.id, within=timedelta(seconds=30)))

    await kit.clock.advance(timedelta(seconds=31))

    assert (await waiting).status is BatchStatus.RUNNING


async def test_a_batch_that_only_waits_for_nobody_is_not_waited_for(kit: Kit) -> None:
    kit.fetcher.scripts[url("a.example", 1)] = Script("challenge")
    batch = await kit.queue.submit([url("a.example", 1)], FetchOptions(), person=PersonMode.WAIT)
    waiting = asyncio.create_task(kit.queue.wait(batch.id))

    await kit.clock.advance(timedelta(seconds=3))

    assert (await waiting).status is BatchStatus.WAITING


# ---------------------------------------------------------------------------------------------
# Cancelling, stopping, retrying
# ---------------------------------------------------------------------------------------------


async def test_cancelling_a_batch_stops_its_running_fetches(kit: Kit) -> None:
    kit.fetcher.blocks = True
    batch = await kit.queue.submit(
        [url("a.example", 1), url("b.example", 1), url("a.example", 2)], FetchOptions()
    )
    await kit.until(lambda: len(kit.fetcher.started) == 2)

    cancelled = await kit.queue.cancel(batch.id)

    assert cancelled.status is BatchStatus.CANCELLED
    assert cancelled.counts == {ItemStatus.CANCELLED: 3}
    assert sum(kit.fetcher.running.values()) == 0
    [ended] = await kit.events("udl.fetch.finished")
    assert ended.data["cancelled"] == 3


async def test_cancelling_a_batch_that_ended_leaves_it(kit: Kit) -> None:
    batch = await kit.run(url("a.example", 1))

    assert (await kit.queue.cancel(batch.id)).status is BatchStatus.FINISHED


async def test_a_batch_another_udl_runs_is_not_cancelled_here(kit: Kit) -> None:
    kit.fetcher.blocks = True
    batch = await kit.queue.submit([url("a.example", 1)], FetchOptions())
    await kit.until(lambda: len(kit.fetcher.started) == 1)

    with pytest.raises(DoesNotExist, match="another udl"):
        await kit.make(runner="there").cancel(batch.id)


async def test_a_udl_that_stops_leaves_its_batches_interrupted(kit: Kit) -> None:
    kit.fetcher.blocks = True
    batch = await kit.queue.submit([url("a.example", 1), url("a.example", 2)], FetchOptions())
    await kit.until(lambda: len(kit.fetcher.started) == 1)

    await kit.queue.shutdown()

    stopped = await kit.queue.batch(batch.id)
    assert stopped.status is BatchStatus.INTERRUPTED
    assert stopped.counts == {ItemStatus.INTERRUPTED: 2}
    assert await kit.events("udl.fetch.finished") == []


async def test_what_a_udl_that_was_killed_left_is_interrupted_when_the_next_starts(
    kit: Kit,
) -> None:
    left = FetchBatch(status=BatchStatus.RUNNING, runner="gone")
    await kit.db.upsert_record(left)
    for n, status in enumerate((ItemStatus.RUNNING, ItemStatus.SUCCEEDED, ItemStatus.CHALLENGE)):
        await kit.db.upsert_record(
            FetchItem(
                batch_id=left.id,
                url=url("a.example", n),
                site="a.example",
                position=n,
                status=status,
            )
        )

    assert await kit.queue.recover() == 1

    recovered = await kit.queue.batch(left.id)
    assert recovered.status is BatchStatus.INTERRUPTED
    assert statuses(await kit.items(recovered)) == [
        ItemStatus.INTERRUPTED,
        ItemStatus.SUCCEEDED,
        ItemStatus.INTERRUPTED,
    ]


async def test_retrying_runs_what_did_not_finish(kit: Kit) -> None:
    kit.fetcher.blocks = True
    batch = await kit.queue.submit([url("a.example", 1), url("b.example", 1)], FetchOptions())
    await kit.until(lambda: len(kit.fetcher.started) == 2)
    await kit.queue.shutdown()
    kit.fetcher.blocks = False
    kit.queue = kit.make(runner="again")

    retried = await kit.queue.retry(batch.id)
    await kit.clock.advance(timedelta(seconds=5))

    ended = await kit.queue.batch(retried.id)
    assert (ended.status, ended.runner) == (BatchStatus.FINISHED, "again")
    assert ended.counts == {ItemStatus.SUCCEEDED: 2}


async def test_retrying_some_urls_on_another_rung(kit: Kit) -> None:
    kit.fetcher.scripts = {url("a.example", n): Script("missing") for n in (1, 2)}
    batch = await kit.run(url("a.example", 1), url("a.example", 2))
    first, _ = await kit.items(batch)
    kit.fetcher.scripts.clear()

    await kit.queue.retry(batch.id, items=[first.id], via=Via.BROWSER)
    await kit.clock.advance(timedelta(seconds=5))

    ended = await kit.queue.batch(batch.id)
    assert ended.counts == {ItemStatus.SUCCEEDED: 1, ItemStatus.FAILED: 1}
    assert ended.options.via is Via.BROWSER
    assert len(await kit.events("udl.fetch.finished")) == 2


async def test_a_retry_of_a_batch_another_udl_runs_is_refused(kit: Kit) -> None:
    kit.fetcher.blocks = True
    batch = await kit.queue.submit([url("a.example", 1)], FetchOptions())
    await kit.until(lambda: len(kit.fetcher.started) == 1)

    with pytest.raises(DoesNotExist, match="another udl"):
        await kit.make(runner="there").retry(batch.id)


# ---------------------------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------------------------


async def test_batches_and_their_pages_go_after_keep(kit: Kit) -> None:
    old = await kit.run(url("a.example", 1))
    [item] = await kit.items(old)
    await kit.clock.advance(timedelta(days=6))
    recent = await kit.run(url("a.example", 2))
    await kit.queue.shutdown()  # (its hourly retention would beat this test to it)
    await kit.clock.advance(timedelta(days=1, hours=1))
    kit.queue = kit.make()

    assert await kit.queue.prune() == 1

    with pytest.raises(DoesNotExist):
        await kit.queue.batch(old.id)
    assert await kit.db.get_record(FetchItem, item.id) is None
    assert item.body is not None
    assert not Path(item.body).parent.exists()
    assert (await kit.queue.batch(recent.id)).status is BatchStatus.FINISHED


async def test_retention_runs_every_hour_while_the_queue_does(kit: Kit) -> None:
    old = await kit.run(url("a.example", 1))

    await kit.clock.advance(timedelta(days=7, hours=1))

    with pytest.raises(DoesNotExist):
        await kit.queue.batch(old.id)


# ---------------------------------------------------------------------------------------------
# The records
# ---------------------------------------------------------------------------------------------


def test_a_batch_or_url_says_when_it_has_ended() -> None:
    assert [status.ended for status in BatchStatus] == [False, False, False, True, True, True]
    assert [status.ended for status in ItemStatus] == [False, False, False, True, True, True, True]


async def test_the_page_store_reads_what_it_wrote(tmp_path: Path) -> None:
    store = PageStore(tmp_path / "pages")
    batch, item = UUID(int=1), UUID(int=2)

    written = await store.write(batch, item, b"page")

    assert await store.read(written) == b"page"
    await store.remove(batch)
    await store.remove(batch)  # (nothing left: nothing happens)
    assert not written.exists()
