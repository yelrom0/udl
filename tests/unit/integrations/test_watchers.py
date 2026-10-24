"""
--------------------
- test_watchers.py -
--------------------

Watchers, on a FakeClock: one runs for each connected, enabled account an enabled subscription
could get events from (none for an account nothing subscribes to), following the subscriptions
and accounts as they change (at once in this udl, within 30 s from another); what it publishes
is stamped with its account; its state is saved after each batch, so one started again goes on
from it; one that fails is started again after 1 s, twice as long each time (at most 5 min),
saying so once per streak; one whose credentials are refused stops until the account is
connected again; and each one's status is shown
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

# Package Imports
import pytest

# Local Imports
from support.accounts import Fakes, connected, with_fake_integration
from support.clock import FakeClock
from support.integrations import FakeAccountOptions, FakeIntegration, FakeSettings
from support.interfaces import Harness
from udl.automations.events import AccountEventData, EventRecord
from udl.automations.subscriptions import Subscription
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import DatabaseError, NetworkError, ReauthRequired
from udl.integrations.watchers import (
    MAX_BACKOFF,
    PluginState,
    WatcherState,
    WatcherStatus,
    watcher_statuses,
)
from udl.plugins.actions.base import Action
from udl.plugins.integrations.base import (
    Account,
    AccountHandle,
    AccountIdentity,
    AccountStatus,
    ConnectMethod,
    Credentials,
    EventSink,
    Integration,
    StateStore,
)
from udl.plugins.registry import INTEGRATIONS


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock) -> Harness:
    return Harness(tmp_path, clock=clock)


@pytest.fixture
def fakes(harness: Harness) -> Fakes:
    return with_fake_integration(harness)


@pytest.fixture
async def service(harness: Harness, fakes: Fakes) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        await found.db.upsert_record(Action(name="hello", plugin="fake"))
        yield found


@pytest.fixture
async def watching(service: UDLService) -> AsyncIterator[asyncio.Event]:
    """The service's watchers, running (until the test sets the event it gives)."""
    stop = asyncio.Event()
    task = asyncio.create_task(service.watchers.serve(stop))
    yield stop
    stop.set()
    async with asyncio.timeout(5):
        await task


async def source(service: UDLService, name: str, **fields: Any) -> Account:
    """A fake account that needs no login (so its watcher needs no credentials)."""
    fields.setdefault("options", {"source": name})
    return await connected(service, name, method=ConnectMethod.NONE, **fields)


async def subscribe(service: UDLService, name: str = "things", **fields: Any) -> Subscription:
    fields.setdefault("events", ["fake.thing.happened"])
    saved = await service.db.upsert_record(
        Subscription.model_validate({"name": name, "actions": ["hello"], **fields})
    )
    service.bus.subscriptions_changed()
    service.watchers.changed()
    return saved


async def events(service: UDLService, event_type: str = "fake.thing.happened") -> list[EventRecord]:
    """The events of `event_type`, oldest first (those of one time by their thing)."""
    found = await service.db.list_records(EventRecord, RecordQuery(where={"type": event_type}))
    return sorted(found, key=lambda event: (event.created_at, str(event.data.get("thing"))))


async def settle(clock: FakeClock, by: timedelta = timedelta(0)) -> None:
    await clock.advance(by)
    for _ in range(5):
        await asyncio.sleep(0)
        await clock.advance(timedelta(0))


async def states(service: UDLService) -> dict[str, WatcherState]:
    return {status.account: status.state for status in await watcher_statuses(service)}


# ---------------------------------------------------------------------------------------------
# Which accounts are watched
# ---------------------------------------------------------------------------------------------


async def test_an_account_a_subscription_names_is_watched_and_the_others_not(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work, home = await source(service, "work"), await source(service, "home")
    await subscribe(service, accounts=["fake:work"])
    await settle(clock)

    fakes.plugin.feed("work").add("report")
    fakes.plugin.feed("home").add("recipe")
    await settle(clock)

    published = await events(service)
    assert [(event.account_id, event.data["text"]) for event in published] == [(work.id, "report")]
    assert home.id not in {event.account_id for event in published}
    assert await states(service) == {"fake:work": WatcherState.RUNNING}


async def test_a_subscription_that_names_no_account_watches_every_one(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    await source(service, "work")
    await source(service, "home")
    await subscribe(service, events=["fake.*"])
    await settle(clock)

    assert await states(service) == {
        "fake:home": WatcherState.RUNNING,
        "fake:work": WatcherState.RUNNING,
    }


@pytest.mark.parametrize(
    "subscription",
    [
        {"events": ["custom.*"]},  # (none of the integration's types)
        {"events": ["fake.thing.happened"], "enabled": False},
        {"events": ["fake.thing.happened"], "accounts": ["other:work"]},
    ],
)
async def test_an_account_no_subscription_wants_events_from_is_not_watched(
    service: UDLService,
    fakes: Fakes,
    clock: FakeClock,
    watching: asyncio.Event,
    subscription: dict[str, Any],
) -> None:
    await source(service, "work")
    await subscribe(service, **subscription)

    fakes.plugin.feed("work").add("report")
    await settle(clock)

    assert await events(service) == []
    assert await states(service) == {}


@pytest.mark.parametrize(
    "account",
    [
        {"enabled": False},
        {"status": AccountStatus.NEEDS_REAUTH},
        {"status": AccountStatus.PENDING, "connected_at": None},
    ],
)
async def test_an_account_that_is_not_connected_and_enabled_is_not_watched(
    service: UDLService,
    clock: FakeClock,
    watching: asyncio.Event,
    account: dict[str, Any],
) -> None:
    await source(service, "work", **account)
    await subscribe(service)
    await settle(clock)

    assert await states(service) == {}


class Quiet(Integration[FakeSettings, FakeAccountOptions]):
    """An integration without a watcher."""

    name = "quiet"
    title = "Quiet"
    connect = (ConnectMethod.NONE,)

    async def identify(self, credentials: Credentials, options: Any) -> AccountIdentity:
        return AccountIdentity(id="quiet", display="Quiet")


async def test_an_integration_without_a_watcher_watches_nothing(
    tmp_path: Path, clock: FakeClock
) -> None:
    harness = Harness(tmp_path, clock=clock)
    harness.registry.register(INTEGRATIONS, Quiet)
    async with harness.service() as service:
        await connected(service, "work", integration="quiet", method=ConnectMethod.NONE)
        await service.db.upsert_record(Subscription(name="all", events=["*"], actions=["x"]))

        assert await service.watchers.wanted() == {}


async def test_a_subscription_saved_by_another_udl_starts_a_watcher_within_30_s(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    await source(service, "work")
    await settle(clock)
    await service.db.upsert_record(  # (as another udl saves it: nothing here is told)
        Subscription(name="things", events=["fake.*"], actions=["hello"])
    )

    await settle(clock, timedelta(seconds=29))
    assert await states(service) == {}
    await settle(clock, timedelta(seconds=1))

    assert await states(service) == {"fake:work": WatcherState.RUNNING}


async def test_deleting_the_last_subscription_stops_the_watcher(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    await source(service, "work")
    subscription = await subscribe(service)
    await settle(clock)

    await service.db.delete_record(Subscription, subscription.id)
    service.watchers.changed()
    await settle(clock)
    fakes.plugin.feed("work").add("report")
    await settle(clock)

    assert await events(service) == []
    assert await states(service) == {"fake:work": WatcherState.STOPPED}


async def test_an_account_whose_options_change_is_watched_with_them(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    await settle(clock)

    await service.accounts.save(work.model_copy(update={"options": {"source": "x", "label": "W"}}))
    await settle(clock)
    fakes.plugin.feed("work").add("report")
    await settle(clock)

    [event] = await events(service)
    assert event.data["label"] == "W"


async def test_the_watchers_stop_with_the_supervisor(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await source(service, "work")
    await subscribe(service)
    stop = asyncio.Event()
    task = asyncio.create_task(service.watchers.serve(stop))
    await settle(clock)
    assert await states(service) == {"fake:work": WatcherState.RUNNING}

    stop.set()
    async with asyncio.timeout(1):
        await task

    assert await states(service) == {"fake:work": WatcherState.STOPPED}


# ---------------------------------------------------------------------------------------------
# What a watcher publishes, and its state
# ---------------------------------------------------------------------------------------------


async def test_what_a_watcher_publishes_is_stamped_with_its_account(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    await settle(clock)

    fakes.plugin.feed("work").add("report")
    await settle(clock)

    [event] = await events(service)
    assert (event.source, event.account_id, event.depth, event.caused_by) == (
        "fake",
        work.id,
        0,
        None,
    )
    assert event.dedupe_key == f"{work.id}:t1"
    [status] = await watcher_statuses(service)
    assert status.last_event_at == clock.now()


async def test_a_watcher_saves_its_state_and_goes_on_from_it_when_started_again(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    feed = fakes.plugin.feed("work")
    feed.add("one", "two")
    await settle(clock)
    [state] = await service.db.list_records(PluginState, RecordQuery())
    assert (state.plugin, state.account_id, state.state) == ("fake", work.id, {"position": 2})

    feed.fail(NetworkError("The connection was reset"))
    feed.add("three")
    await settle(clock, timedelta(seconds=1))

    assert [event.data["text"] for event in await events(service)] == ["one", "two", "three"]


async def test_a_watcher_that_publishes_what_is_not_its_own_fails(
    tmp_path: Path, clock: FakeClock
) -> None:
    class Wrong(FakeIntegration):
        name = "wrong"

        async def watch(
            self,
            account: AccountHandle[FakeAccountOptions],
            events: EventSink,
            state: StateStore[Any],
        ) -> None:
            await events.emit("custom.hello", {}, dedupe_key="x")

    harness = Harness(tmp_path, clock=clock)
    harness.registry.register(INTEGRATIONS, Wrong)
    async with harness.service() as service:
        await service.db.upsert_record(Action(name="hello", plugin="fake"))
        await connected(service, integration="wrong", method=ConnectMethod.NONE)
        await subscribe(service, events=["wrong.*"])
        stop = asyncio.Event()
        task = asyncio.create_task(service.watchers.serve(stop))
        await settle(clock)
        stop.set()
        await task

        [status] = await watcher_statuses(service)
        assert status.last_error is not None
        assert "custom.hello, which is not one of its event types" in status.last_error.message


# ---------------------------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------------------------


class Failing(FakeIntegration):
    """A watcher that fails every time, saying when it was started."""

    name = "failing"
    started: ClassVar[list[datetime]] = []

    async def watch(
        self, account: AccountHandle[FakeAccountOptions], events: EventSink, state: StateStore[Any]
    ) -> None:
        Failing.started.append(self.clock.now())
        raise NetworkError("The service is down")


async def test_a_failing_watcher_is_started_again_after_1_s_doubling_up_to_5_minutes(
    tmp_path: Path, clock: FakeClock
) -> None:
    Failing.started = []
    harness = Harness(tmp_path, clock=clock)
    harness.registry.register(INTEGRATIONS, Failing)
    async with harness.service() as service:
        await service.db.upsert_record(Action(name="hello", plugin="fake"))
        await connected(service, integration="failing", method=ConnectMethod.NONE)
        await subscribe(service, events=["failing.*"])
        stop = asyncio.Event()
        task = asyncio.create_task(service.watchers.serve(stop))
        for _ in range(20 * 60):
            await clock.advance(timedelta(seconds=1))
        stop.set()
        await task

        gaps = [
            (later - earlier).total_seconds()
            for earlier, later in zip(Failing.started, Failing.started[1:], strict=False)
        ]
        assert gaps[:10] == [1, 2, 4, 8, 16, 32, 64, 128, 256, MAX_BACKOFF.total_seconds()]
        assert set(gaps[9:]) == {MAX_BACKOFF.total_seconds()}
        said = await events(service, "udl.account.error")
        assert len(said) == 1  # (once for the streak)
        error = AccountEventData.model_validate(said[0].data).error
        assert error is not None
        assert error.message == "The service is down"
        [status] = await watcher_statuses(service)
        assert status.state is WatcherState.STOPPED  # (the supervisor stopped)
        assert status.failures == len(Failing.started)
        [account] = await service.accounts.list()
        assert account.last_error is not None


async def test_a_watcher_that_ran_10_healthy_minutes_starts_a_new_streak(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    await source(service, "work")
    await subscribe(service)
    feed = fakes.plugin.feed("work")
    feed.fail(NetworkError("Down"), times=2)
    await settle(clock, timedelta(seconds=1))
    await settle(clock, timedelta(seconds=2))  # (two failures: one streak)
    [status] = await watcher_statuses(service)
    assert (status.state, status.failures) == (WatcherState.RUNNING, 2)

    await settle(clock, timedelta(minutes=10))
    feed.fail(NetworkError("Down again"))
    await settle(clock)
    await settle(clock, timedelta(seconds=1))  # (after 1 s, not 8)

    assert len(await events(service, "udl.account.error")) == 2
    [status] = await watcher_statuses(service)
    assert (status.state, status.failures) == (WatcherState.RUNNING, 1)


async def test_a_watcher_in_backoff_says_so(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    await source(service, "work")
    await subscribe(service)
    fakes.plugin.feed("work").fail(NetworkError("Down"))
    await settle(clock)

    [status] = await watcher_statuses(service)
    assert (status.state, status.failures) == (WatcherState.BACKOFF, 1)
    assert status.last_error is not None
    assert status.last_error.message == "Down"


async def test_a_bug_in_a_watcher_is_a_failure_like_another(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    await source(service, "work")
    await subscribe(service)
    fakes.plugin.feed("work").fail(RuntimeError("a bug"))
    await settle(clock)

    [status] = await watcher_statuses(service)
    assert status.last_error is not None
    assert status.last_error.message == "The fake watcher failed: RuntimeError: a bug"


async def test_refused_credentials_stop_the_watcher_until_the_account_is_connected_again(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    feed = fakes.plugin.feed("work")
    feed.fail(ReauthRequired("The token was revoked"))
    await settle(clock, timedelta(minutes=5))

    assert (await service.accounts.get("fake:work")).status is AccountStatus.NEEDS_REAUTH
    assert len(await events(service, "udl.account.needs_reauth")) == 1
    assert await states(service) == {"fake:work": WatcherState.STOPPED}

    await service.accounts.save(
        (await service.accounts.get("fake:work")).model_copy(
            update={"status": AccountStatus.CONNECTED}
        )
    )
    await settle(clock)
    feed.add("report")
    await settle(clock)

    assert [event.account_id for event in await events(service)] == [work.id]


# ---------------------------------------------------------------------------------------------
# Their status
# ---------------------------------------------------------------------------------------------


async def test_each_watcher_s_status_is_kept(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    await settle(clock)

    assert await watcher_statuses(service) == [
        WatcherStatus(
            account="fake:work",
            account_id=work.id,
            integration="fake",
            state=WatcherState.RUNNING,
        )
    ]


# ---------------------------------------------------------------------------------------------
# What goes wrong
# ---------------------------------------------------------------------------------------------


async def test_what_a_watcher_publishes_again_is_published_once(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    feed = fakes.plugin.feed("work")
    feed.add("one")
    await settle(clock)
    [state] = await service.db.list_records(PluginState, RecordQuery())
    await service.db.upsert_record(state.model_copy(update={"state": None}))  # (lost)

    feed.fail(NetworkError("Down"))
    await settle(clock, timedelta(seconds=1))

    assert [event.account_id for event in await events(service)] == [work.id]


async def test_a_watcher_whose_saved_state_is_not_valid_starts_afresh(
    service: UDLService,
    fakes: Fakes,
    clock: FakeClock,
    watching: asyncio.Event,
    caplog: pytest.LogCaptureFixture,
) -> None:
    work = await source(service, "work")
    await service.db.upsert_record(
        PluginState(plugin="fake", account_id=work.id, state={"position": "far"})
    )
    fakes.plugin.feed("work").add("one")
    await subscribe(service)
    await settle(clock)

    assert len(await events(service)) == 1
    assert "fake:work's watcher starts afresh" in caplog.text


class Odd(FakeIntegration):
    """A watcher that saves what is not its state, or ends."""

    name = "odd"
    ends: ClassVar[bool] = False

    async def watch(
        self, account: AccountHandle[FakeAccountOptions], events: EventSink, state: StateStore[Any]
    ) -> None:
        if not Odd.ends:
            await state.save(AccountIdentity(id="x", display="x"))


@pytest.mark.parametrize(
    ("ends", "problem"),
    [
        (False, "The odd watcher saved a AccountIdentity, not its state_model"),
        (True, "The odd watcher stopped by itself"),
    ],
)
async def test_a_watcher_that_saves_what_is_not_its_state_or_ends_fails(
    tmp_path: Path, clock: FakeClock, ends: bool, problem: str
) -> None:
    Odd.ends = ends
    harness = Harness(tmp_path, clock=clock)
    harness.registry.register(INTEGRATIONS, Odd)
    async with harness.service() as service:
        await service.db.upsert_record(Action(name="hello", plugin="fake"))
        await connected(service, integration="odd", method=ConnectMethod.NONE)
        await subscribe(service, events=["odd.*"])
        stop = asyncio.Event()
        task = asyncio.create_task(service.watchers.serve(stop))
        await settle(clock)
        stop.set()
        await task

        [status] = await watcher_statuses(service)
        assert status.last_error is not None
        assert status.last_error.message == problem


async def test_an_account_whose_integration_is_gone_is_not_watched(
    service: UDLService,
) -> None:
    await connected(service, integration="gone", method=ConnectMethod.NONE)
    await subscribe(service, events=["*"])

    assert await service.watchers.wanted() == {}


async def test_the_watchers_go_on_when_the_accounts_cannot_be_read(
    service: UDLService,
    clock: FakeClock,
    watching: asyncio.Event,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def unreadable() -> dict[Any, Account]:
        raise DatabaseError("The database went away")

    monkeypatch.setattr(service.watchers, "wanted", unreadable)
    service.watchers.changed()
    await settle(clock)

    assert "Could not tell which accounts to watch: The database went away" in caplog.text


async def test_a_disconnected_account_s_watcher_and_state_are_forgotten(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    await settle(clock)

    await service.watchers.forget(work)
    await service.watchers.forget(work)  # (nothing to stop, nothing to forget)

    assert await service.db.list_records(PluginState, RecordQuery()) == []


async def test_an_account_removed_by_another_udl_while_its_watcher_fails_stays_removed(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    await settle(clock)
    await service.db.delete_record(Account, work.id)  # (this udl is not told)

    fakes.plugin.feed("work").fail(NetworkError("Down"))
    await settle(clock, timedelta(seconds=1))

    assert await service.db.get_record(Account, work.id) is None
    assert len(await events(service, "udl.account.error")) == 1


async def test_an_account_disconnected_by_another_udl_leaves_no_state_behind(
    service: UDLService, fakes: Fakes, clock: FakeClock, watching: asyncio.Event
) -> None:
    work = await source(service, "work")
    await subscribe(service)
    await settle(clock)
    [state] = await service.db.list_records(PluginState, RecordQuery())
    # (As another udl disconnects it: this one is not told, and finds out as it goes.)
    await service.db.delete_record(Account, work.id)
    await service.db.delete_record(PluginState, state.id)

    await settle(clock, timedelta(minutes=1))
    await settle(clock, timedelta(seconds=30))

    assert await service.db.list_records(PluginState, RecordQuery()) == []
