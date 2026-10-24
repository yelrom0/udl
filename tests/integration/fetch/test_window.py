"""
------------------
- test_window.py -
------------------

Passing checks (udl.fetch.window, and the queue's person modes) against the anti-bot site's
/checked/ pages, with FakePerson in a headless window: one window for every site that waits,
what passing a site does, a person who closes the window or never comes, wait and fail modes, no
display, and a solve from another udl on the same database
"""

# System Imports
import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.antibot import HOSTS, AntiBotSite, FakePerson, Manner
from support.browser import needs_chromium
from support.engine import StaticCookies, databases, registry, settings
from udl.automations.bus import EventBus
from udl.automations.events import EventRecord, EventTypes
from udl.clock import SystemClock
from udl.config import AutomationsSettings, PersonMode, Settings
from udl.db.manager import DBManager
from udl.db.records import RecordQuery
from udl.errors import ErrorCode, ToolUnavailable
from udl.fetch.kit import FetchKit
from udl.fetch.ladder import FetchOptions, Via
from udl.fetch.models import BatchStatus, FetchBatch, ItemStatus

FETCH = {
    "timeout": "15s",
    "challenge_grace": "1s",
    "challenge_timeout": "20s",
    "site_delay": "0s",
    "browser_idle": "1s",
}


def kit_on(
    db: DBManager,
    config: Settings,
    *,
    person: FakePerson | None = None,
    display: str | None = "no display",
    runner: str = "here",
) -> FetchKit:
    clock = SystemClock()
    bus = EventBus(db, EventTypes.default(), clock, AutomationsSettings())
    return FetchKit(
        config,
        db=db,
        bus=bus,
        clock=clock,
        cookies=StaticCookies(),
        runner=runner,
        person=person,
        display=lambda: display,
    )


@asynccontextmanager
async def kits(tmp_path: Path, **fetch: Any) -> AsyncGenerator[tuple[DBManager, Settings]]:
    needs_chromium()
    config = settings(tmp_path, fetch={**FETCH, **fetch, "store_dir": tmp_path / "fetched"})
    config = config.model_copy(
        update={"browser": config.browser.model_copy(update={"profile_dir": tmp_path / "profile"})}
    )
    async with databases(config, registry()) as db:
        yield db, config


async def fetched(kit: FetchKit, urls: list[str], **fields: Any) -> FetchBatch:
    batch = await kit.queue.submit(urls, FetchOptions(), **fields)
    return await kit.queue.wait(batch.id, within=timedelta(seconds=90))


async def ended(kit: FetchKit, batch: FetchBatch) -> FetchBatch:
    async with asyncio.timeout(60):
        while not (found := await kit.queue.batch(batch.id)).status.ended:  # noqa: ASYNC110
            await asyncio.sleep(0.2)
    return found


async def challenge_events(db: DBManager) -> list[EventRecord]:
    return await db.list_records(EventRecord, RecordQuery(where={"type": "udl.fetch.challenge"}))


def checked(antibot: AntiBotSite, host: str, n: int) -> str:
    return antibot.url(f"/checked/{n}", host)


async def test_a_batch_on_two_checked_hosts_asks_once_for_each_then_finishes(
    antibot: AntiBotSite, tmp_path: Path
) -> None:
    person = FakePerson()
    async with kits(tmp_path) as (db, config):
        kit = kit_on(db, config, person=person)
        urls = [checked(antibot, host, n) for host in HOSTS[:2] for n in range(3)]
        try:
            batch = await fetched(kit, urls, person=PersonMode.ASK)
            items = await kit.queue.items(batch.id)
            told = await challenge_events(db)
        finally:
            await kit.close()

    assert batch.status is BatchStatus.FINISHED
    assert batch.counts == {ItemStatus.SUCCEEDED: 6}
    assert sorted(person.attended) == sorted([urls[0], urls[3]])
    assert {item.via for item in items} == {Via.PERSON, Via.BROWSER}
    assert [item.via for item in items].count(Via.PERSON) == 2
    assert {str(event.data["mode"]) for event in told} == {"ask"}
    assert len(told) == 2


async def test_once_passed_the_next_batch_needs_nobody(
    antibot: AntiBotSite, tmp_path: Path
) -> None:
    person = FakePerson()
    async with kits(tmp_path) as (db, config):
        kit = kit_on(db, config, person=person)
        try:
            await fetched(kit, [checked(antibot, HOSTS[0], 1)], person=PersonMode.ASK)
            again = await fetched(kit, [checked(antibot, HOSTS[0], 2)], person=PersonMode.FAIL)
            [item] = await kit.queue.items(again.id)
            known = await kit.sites.get(HOSTS[0])
        finally:
            await kit.close()

    assert again.status is BatchStatus.FINISHED
    assert (item.status, item.via) == (ItemStatus.SUCCEEDED, Via.BROWSER)
    assert len(person.attended) == 1
    assert known is not None
    assert known.cleared_at is not None
    assert known.start_at is Via.BROWSER


async def test_a_person_who_closes_the_window_leaves_the_urls_waiting(
    antibot: AntiBotSite, tmp_path: Path
) -> None:
    async with kits(tmp_path) as (db, config):
        kit = kit_on(db, config, person=FakePerson(Manner.CLOSES))
        try:
            batch = await fetched(kit, [checked(antibot, HOSTS[0], 1)], person=PersonMode.ASK)
        finally:
            await kit.close()

    assert batch.status is BatchStatus.WAITING


async def test_a_person_who_never_comes_leaves_the_urls_waiting(
    antibot: AntiBotSite, tmp_path: Path
) -> None:
    async with kits(tmp_path, challenge_timeout="3s") as (db, config):
        kit = kit_on(db, config, person=FakePerson(Manner.IGNORES))
        try:
            batch = await fetched(kit, [checked(antibot, HOSTS[0], 1)], person=PersonMode.ASK)
            [item] = await kit.queue.items(batch.id)
        finally:
            await kit.close()

    assert batch.status is BatchStatus.WAITING
    assert item.status is ItemStatus.CHALLENGE


async def test_wait_mode_opens_nothing_until_someone_solves(
    antibot: AntiBotSite, tmp_path: Path
) -> None:
    person = FakePerson()
    async with kits(tmp_path) as (db, config):
        kit = kit_on(db, config, person=person)
        try:
            batch = await fetched(kit, [checked(antibot, HOSTS[0], 1)], person=PersonMode.WAIT)
            assert batch.status is BatchStatus.WAITING
            assert person.attended == []

            report = await kit.solve()
            done = await ended(kit, batch)
        finally:
            await kit.close()

    assert (report.passed, report.waiting) == ([HOSTS[0]], [])
    assert done.counts == {ItemStatus.SUCCEEDED: 1}


async def test_without_a_display_ask_waits_and_says_why(
    antibot: AntiBotSite, tmp_path: Path
) -> None:
    async with kits(tmp_path) as (db, config):
        kit = kit_on(db, config, display="there is no display")
        try:
            batch = await fetched(kit, [checked(antibot, HOSTS[0], 1)], person=PersonMode.ASK)
            [item] = await kit.queue.items(batch.id)
            told = await challenge_events(db)
            with pytest.raises(ToolUnavailable, match="no display"):
                await kit.solve()
        finally:
            await kit.close()

    assert batch.status is BatchStatus.WAITING
    assert item.note is not None
    assert "there is no display" in item.note
    assert [str(event.data["mode"]) for event in told] == ["wait"]


async def test_fail_mode_fails_at_once(antibot: AntiBotSite, tmp_path: Path) -> None:
    async with kits(tmp_path) as (db, config):
        kit = kit_on(db, config, person=FakePerson())
        try:
            batch = await fetched(kit, [checked(antibot, HOSTS[0], 1)], person=PersonMode.FAIL)
            [item] = await kit.queue.items(batch.id)
        finally:
            await kit.close()

    assert batch.status is BatchStatus.FINISHED
    assert item.error is not None
    assert item.error.code is ErrorCode.CHALLENGE_REQUIRED


async def test_a_solve_from_another_udl_writes_the_page_and_requeues_the_rest(
    antibot: AntiBotSite, tmp_path: Path
) -> None:
    async with kits(tmp_path) as (db, config):
        running = kit_on(db, config, runner="runs-it")
        other = kit_on(db, config, person=FakePerson(), runner="solves-it")
        urls = [checked(antibot, HOSTS[0], n) for n in range(3)]
        try:
            batch = await fetched(running, urls, person=PersonMode.WAIT)
            assert batch.status is BatchStatus.WAITING
            await running.browser.close()  # (its browser lets go of the profile)

            report = await other.solve([HOSTS[0]])
            done = await ended(running, batch)
            items = await running.queue.items(batch.id)
        finally:
            await other.close()
            await running.close()

    assert report.passed == [HOSTS[0]]
    assert done.counts == {ItemStatus.SUCCEEDED: 3}
    assert [item.via for item in items] == [Via.PERSON, Via.BROWSER, Via.BROWSER]


async def test_listing_what_waits(antibot: AntiBotSite, tmp_path: Path) -> None:
    async with kits(tmp_path) as (db, config):
        kit = kit_on(db, config)
        try:
            batch = await fetched(
                kit,
                [checked(antibot, HOSTS[0], 1), checked(antibot, HOSTS[0], 2)],
                person=PersonMode.WAIT,
            )
            [waiting] = await kit.waiting()
        finally:
            await kit.close()

    assert (waiting.site, waiting.kind) == (HOSTS[0], "cloudflare")
    assert waiting.batches == [batch.id]
    assert waiting.urls == [checked(antibot, HOSTS[0], 1), checked(antibot, HOSTS[0], 2)]
