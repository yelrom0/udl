"""
---------------
- watchers.py -
---------------

Watchers: what turns what happens in an account into events. The WatcherSupervisor
(UDLService.watchers, run by the automations' runtime) runs one watcher (its integration's
watch()) for each connected, enabled account that an enabled subscription could get events
from: one of the subscription's globs matches one of the integration's event types, and it
names the account or none. It looks again whenever subscriptions or accounts change in this udl
(changed()), and every 30 s (recheck); an account whose options change is watched again with
them.

A watcher that fails is started again after a second, then twice as long each time it fails
again (at most MAX_BACKOFF), and after a second again once it has run HEALTHY_AFTER without
failing; each streak of failures says so once (udl.account.error) and keeps the error on the
account. Credentials it finds refused (ReauthRequired) mark the account needs_reauth: its
watcher stops until the account is connected again.

What a watcher publishes goes through a sink that stamps its integration as the source, the
account, depth 0 and the account's id on the dedupe key (so two accounts' events never clash).
Its state (a cursor, as the integration's state_model has it) is a PluginState record, which
the watcher saves after each batch; the watcher's status (running, in backoff or stopped, its
failures, its last error and last event) is kept with it, so any udl can show it.
"""

# System Imports
import asyncio
import fnmatch
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Self
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, NonNegativeInt, ValidationError

# Local Imports
from udl.automations.bus import NewEvent
from udl.automations.subscriptions import Subscription
from udl.clock import pause
from udl.db.models import JsonObject, utcnow
from udl.db.records import Record, RecordQuery, RecordScope, SyncRule
from udl.errors import ErrorInfo, PluginError, PluginFailed, ReauthRequired, UDLError
from udl.plugins.integrations.base import (
    Account,
    AccountStatus,
    EventSink,
    Integration,
    StateStore,
)

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)

RECHECK: Final = timedelta(seconds=30)
"""How often the supervisor looks at the subscriptions and accounts (another udl's changes)."""
FIRST_BACKOFF: Final = timedelta(seconds=1)
MAX_BACKOFF: Final = timedelta(minutes=5)
HEALTHY_AFTER: Final = timedelta(minutes=10)
"""How long a watcher runs without failing for its next failure to start a new streak."""


class WatcherState(StrEnum):
    """Where a watcher is."""

    RUNNING = "running"
    BACKOFF = "backoff"  # it failed: it is started again after a while
    STOPPED = "stopped"  # nothing wants its events, its account needs connecting again, or no
    # udl runs the automations


class PluginState(Record):
    """A watcher's state (its integration's state_model: a cursor) and status, per account."""

    kind = "plugin_state"
    indexed = ("plugin", "account_id")
    unique = (("plugin", "account_id"),)
    scope = RecordScope.RUNTIME
    sync = SyncRule.NEVER

    plugin: str
    account_id: UUID
    state: JsonObject | None = None
    watcher: WatcherState = WatcherState.STOPPED
    failures: NonNegativeInt = 0
    last_error: ErrorInfo | None = None
    last_event_at: AwareDatetime | None = None

    @classmethod
    def example(cls) -> Self:
        return cls(
            plugin="gmail",
            account_id=UUID("00000000-0000-4000-8000-000000000001"),
            state={"history_id": "12345"},
            watcher=WatcherState.RUNNING,
            last_event_at=utcnow(),
        )


class WatcherStatus(BaseModel):
    """How an account's watcher is."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    account: str
    """The account it watches (integration:name)."""
    account_id: UUID
    """Its id."""
    integration: str
    """Its integration."""
    state: WatcherState
    """running, backoff (it failed: it is started again after a while) or stopped."""
    failures: NonNegativeInt = 0
    """How many times it failed in a row."""
    last_error: ErrorInfo | None = None
    """Why it last failed."""
    last_event_at: AwareDatetime | None = None
    """When it last published an event."""


async def watcher_statuses(service: UDLService) -> list[WatcherStatus]:
    """How the watchers are (as their PluginState records say), by account."""
    names = await service.accounts.names()
    found = [
        WatcherStatus(
            account=names[record.account_id],
            account_id=record.account_id,
            integration=record.plugin,
            state=record.watcher,
            failures=record.failures,
            last_error=record.last_error,
            last_event_at=record.last_event_at,
        )
        for record in await service.db.list_records(PluginState, RecordQuery())
        if record.account_id in names
    ]
    return sorted(found, key=lambda status: status.account)


# ---------------------------------------------------------------------------------------------
# What a watcher is given
# ---------------------------------------------------------------------------------------------


class _Kept:
    """An account's PluginState record, read and written by its watcher (and its supervisor)."""

    def __init__(self, service: UDLService, account: Account) -> None:
        self._service = service
        self.account = account

    async def get(self) -> PluginState:
        found = await self._service.db.list_records(
            PluginState,
            RecordQuery(
                where={"plugin": self.account.integration, "account_id": self.account.id}, limit=1
            ),
        )
        if found:
            return found[0]
        return PluginState(plugin=self.account.integration, account_id=self.account.id)

    async def update(self, **fields: Any) -> PluginState:
        return await self._service.db.upsert_record((await self.get()).model_copy(update=fields))


class _Sink(EventSink):
    """Publishes a watcher's events, stamped with its account."""

    def __init__(
        self, service: UDLService, plugin: type[Integration[Any, Any]], kept: _Kept
    ) -> None:
        self._service = service
        self._plugin = plugin
        self._types = {event_type.name for event_type in plugin.event_types()}
        self._kept = kept
        self.last_event_at: datetime | None = None

    async def emit(
        self,
        event_type: str,
        data: BaseModel | JsonObject,
        *,
        dedupe_key: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        if event_type not in self._types:
            raise PluginError(
                f"The {self._plugin.name} watcher published {event_type}, which is not one of "
                "its event types"
            )
        account = self._kept.account
        stored = await self._service.bus.publish(
            NewEvent(
                type=event_type,
                data=data.model_dump(mode="json") if isinstance(data, BaseModel) else data,
                source=self._plugin.name,
                account_id=account.id,
                occurred_at=occurred_at,
                dedupe_key=f"{account.id}:{dedupe_key}",
                depth=0,
            )
        )
        if stored is None:
            return False
        self.last_event_at = self._service.clock.now()
        return True


class _State(StateStore[Any]):
    """A watcher's state, in its PluginState record."""

    def __init__(self, plugin: type[Integration[Any, Any]], kept: _Kept, sink: _Sink) -> None:
        self._plugin = plugin
        self._kept = kept
        self._sink = sink

    async def load(self) -> BaseModel | None:
        model = self._plugin.state_model
        saved = (await self._kept.get()).state
        if model is None or saved is None:
            return None
        try:
            return model.model_validate(saved)
        except ValidationError as e:  # (its integration's state_model changed)
            log.warning(
                "%s's watcher starts afresh: its state is not valid (%s)", self._kept.account.ref, e
            )
            return None

    async def save(self, state: BaseModel) -> None:
        model = self._plugin.state_model
        if model is None or not isinstance(state, model):
            raise PluginError(
                f"The {self._plugin.name} watcher saved a {type(state).__name__}, not its "
                "state_model"
            )
        await self._kept.update(
            state=state.model_dump(mode="json"), last_event_at=self._sink.last_event_at
        )


# ---------------------------------------------------------------------------------------------
# The supervisor
# ---------------------------------------------------------------------------------------------


type _Key = tuple[UUID, str, tuple[str, ...]]
"""What an account is watched with: its id, options and access (watched again when they change)."""


@dataclass(frozen=True)
class _Watcher:
    account: Account
    key: _Key
    task: asyncio.Task[None]


def _key(account: Account) -> _Key:
    return (account.id, json.dumps(account.options, sort_keys=True), tuple(account.access))


class WatcherSupervisor:
    """Runs the watchers of `service`'s accounts (while serve() runs)."""

    def __init__(self, service: UDLService) -> None:
        self._service = service
        self._watchers: dict[UUID, _Watcher] = {}
        self._woken = asyncio.Event()
        self.recheck = RECHECK
        """How often it looks at the subscriptions and accounts again."""

    def changed(self) -> None:
        """Look at the subscriptions and accounts again now (this udl changed them)."""
        self._woken.set()

    async def serve(self, stop: asyncio.Event) -> None:
        """Run the watchers that are wanted, until `stop` is set; then stop them."""
        try:
            while not stop.is_set():
                self._woken.clear()
                await self._follow()
                await pause(self._service.clock, self.recheck, stop, self._woken)
        finally:
            for account_id in list(self._watchers):
                await self._stop(account_id)

    async def wanted(self) -> dict[UUID, Account]:
        """The accounts to watch, by id: the connected, enabled accounts of integrations that
        watch, that an enabled subscription could get events from."""
        service = self._service
        subscriptions = await service.db.list_records(
            Subscription, RecordQuery(where={"enabled": True})
        )
        wanted: dict[UUID, Account] = {}
        for account in await service.accounts.list():
            if account.status is not AccountStatus.CONNECTED or not account.enabled:
                continue
            if account.integration not in service.integrations.names():
                continue  # (its plugin is gone)
            plugin = service.integrations.plugin(account.integration)
            types = [event_type.name for event_type in plugin.event_types()]
            if plugin.watches() and any(
                _wants(subscription, account, types) for subscription in subscriptions
            ):
                wanted[account.id] = account
        return wanted

    async def forget(self, account: Account) -> None:
        """Stop an account's watcher, and forget its state (the account is disconnected)."""
        await self._stop(account.id, keep=False)
        kept = await _Kept(self._service, account).get()
        await self._service.db.delete_record(PluginState, kept.id)

    async def _follow(self) -> None:
        """Start the watchers that are wanted, and stop those that are not (any more)."""
        try:
            wanted = await self.wanted()
        except UDLError as e:
            log.warning("Could not tell which accounts to watch: %s", e)
            return
        for account_id, watcher in list(self._watchers.items()):
            found = wanted.get(account_id)
            if found is None or _key(found) != watcher.key:
                await self._stop(account_id)
        for account_id, account in wanted.items():
            if account_id not in self._watchers:
                task = asyncio.create_task(self._watch(account), name=f"watching {account.ref}")
                self._watchers[account_id] = _Watcher(account, _key(account), task)

    async def _stop(self, account_id: UUID, *, keep: bool = True) -> None:
        watcher = self._watchers.pop(account_id, None)
        if watcher is None:
            return
        watcher.task.cancel()
        await asyncio.wait([watcher.task])
        if keep:
            await _Kept(self._service, watcher.account).update(watcher=WatcherState.STOPPED)

    async def _watch(self, account: Account) -> None:
        """Run an account's watcher, starting it again after each failure (with backoff), until
        it is cancelled or the account's credentials are refused."""
        service, clock = self._service, self._service.clock
        kept = _Kept(service, account)
        backoff, failures = FIRST_BACKOFF, (await kept.get()).failures
        while True:
            started = clock.now()
            try:
                await kept.update(watcher=WatcherState.RUNNING, failures=failures)
                integration = await service.integrations.get(account.integration)
                plugin = type(integration)
                sink = _Sink(service, plugin, kept)
                handle = await service.accounts.handle(account)
                await integration.watch(handle, sink, _State(plugin, kept, sink))
                raise PluginError(f"The {account.integration} watcher stopped by itself")
            except ReauthRequired as e:
                await service.accounts.needs_reauth(account, e)
                await kept.update(watcher=WatcherState.STOPPED, last_error=e.to_info())
                return
            except Exception as e:  # noqa: BLE001 - a watcher that fails is started again
                error = e if isinstance(e, UDLError) else _bug(account, e)
            if clock.now() - started >= HEALTHY_AFTER:  # (a new streak)
                backoff, failures = FIRST_BACKOFF, 0
            failures += 1
            log.warning(
                "%s's watcher failed (%d in a row), and is started again in %s: %s",
                account.ref,
                failures,
                backoff,
                error.message,
            )
            if failures == 1:
                await self._failed(account, error)
            await kept.update(
                watcher=WatcherState.BACKOFF, failures=failures, last_error=error.to_info()
            )
            await clock.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _failed(self, account: Account, error: UDLError) -> None:
        """Say a watcher's streak of failures began, keeping the error on its account."""
        accounts = self._service.accounts
        current = await accounts.find(account.integration, account.name)
        if current is not None:
            await accounts.save(current.model_copy(update={"last_error": error.to_info()}))
        await accounts.tell("udl.account.error", account, error)


def _wants(subscription: Subscription, account: Account, types: list[str]) -> bool:
    """Whether `subscription` could get events of `types` from `account`."""
    if subscription.accounts is not None and account.ref not in subscription.accounts:
        return False
    return any(
        fnmatch.fnmatchcase(event_type, glob)
        for glob in subscription.events
        for event_type in types
    )


def _bug(account: Account, error: Exception) -> UDLError:
    log.error("%s's watcher failed", account.ref, exc_info=error)
    return PluginFailed(
        f"The {account.integration} watcher failed: {type(error).__name__}: {error}"
    )
