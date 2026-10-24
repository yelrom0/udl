"""
-------------------
- integrations.py -
-------------------

FakeIntegration: an integration whose accounts connect with oauth (against
support.oauth.FakeAuthorizationServer), tokens (an API key) or none (a source it names). Its
watcher publishes fake.thing.happened for each thing a test feeds an account (and for each line
of the account's file in its feed_dir, for trying it by hand), keeping a cursor; its source
filter is {"contains": "x"}; it adds fake_list_things (fan-out, read-only), fake_do_thing
(open-world, with write access) and fake_version (no account). And stand-ins for what an
integration is given (an AccountHandle, an EventSink, a StateStore), to try one without a
service, and HTTP clients of fake servers
"""

# System Imports
import asyncio
import hashlib
from collections import deque
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, ClassVar, Final

# Package Imports
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.types import ASGIApp

# Local Imports
from support.oauth import BASE, CLIENT_ID, CLIENT_SECRET
from udl.automations.events import Event, EventType, current_run
from udl.clock import pause
from udl.errors import ConfigError, NotAllowed, PluginError, ReauthRequired
from udl.http import HttpClientFactory
from udl.integrations.oauth import OAuthClient, OAuthProvider, Tokens
from udl.plugins.integrations.base import (
    Account,
    AccountHandle,
    AccountIdentity,
    AccountOptions,
    ConnectMethod,
    Credentials,
    Duration,
    EventSink,
    Integration,
    IntegrationSettings,
    ItemsPage,
    OAuthRequest,
    OperationHints,
    StateStore,
    integration_operation,
)

REFUSED_KEY: Final = "refused-key"
"""An API key the fake service refuses."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class FakeSettings(IntegrationSettings):
    auth_url: str = BASE
    """The fake authorization server."""
    client_id: str = CLIENT_ID
    """udl's OAuth client's id there."""
    client_secret: SecretStr | None = SecretStr(CLIENT_SECRET)
    """Its secret."""
    poll_every: Duration = timedelta(seconds=60)
    """How often the watcher looks at the feed files (what a test feeds wakes it at once)."""
    feed_dir: Path | None = None
    """A folder of feeds, a file per account (its name), a thing a line, that the watcher
    reads too (for trying it by hand)."""


class FakeAccountOptions(AccountOptions):
    label: str = ""
    """What the account's things are labelled."""
    source: str | None = None
    """What the account watches, when it connects with none (its identity)."""


class FakeTokens(_Model):
    api_key: SecretStr
    """The fake service's API key."""


class Thing(_Model):
    """A thing in an account."""

    id: str
    """Its id."""
    text: str
    """What it says."""
    sent_by_udl: bool = False
    """Whether a run of udl's did it."""


class ThingHappened(_Model):
    """A thing happened in an account: fake.thing.happened."""

    thing: str
    """The thing's id."""
    text: str
    """What it says."""
    label: str = ""
    """The account's label."""
    sent_by_udl: bool = False
    """Whether a run of udl's did it."""


class FakeSourceFilter(_Model):
    contains: str
    """What the thing's text has in it."""


class FakeCursor(_Model):
    position: int = 0
    """How many of the account's things the watcher has published."""


class ListThingsRequest(_Model):
    """List the things in an account."""

    contains: str = ""
    """Only the things whose text has this in it."""
    page: str | None = None
    """Where to go on from: a page's next_page."""
    limit: Annotated[int, Field(ge=1, le=100)] = 50
    """At most this many."""


class DoThingRequest(_Model):
    """Do a thing in an account."""

    text: str
    """What the thing says."""


class DoneThing(_Model):
    account: str
    """The account it was done in."""
    thing: Thing
    """What was done."""


class VersionRequest(_Model):
    """Ask the fake service for its version."""


class FakeVersion(_Model):
    version: str
    """The fake service's version."""


@dataclass
class Feed:
    """An account's things, and the failures its watcher meets (one each time it gets to look
    for more, until they are used up)."""

    things: list[Thing] = field(default_factory=list[Thing])
    errors: deque[Exception] = field(default_factory=deque[Exception])
    woken: asyncio.Event = field(default_factory=asyncio.Event)

    def add(self, *texts: str, sent_by_udl: bool = False) -> list[Thing]:
        """Things that happen in the account (the watcher is woken)."""
        added = [
            Thing(id=f"t{len(self.things) + n + 1}", text=text, sent_by_udl=sent_by_udl)
            for n, text in enumerate(texts)
        ]
        self.things += added
        self.woken.set()
        return added

    def fail(self, error: Exception, *, times: int = 1) -> None:
        """Make the watcher fail with `error`, the next `times` times it looks."""
        self.errors.extend([error] * times)
        self.woken.set()


class FakeIntegration(Integration[FakeSettings, FakeAccountOptions]):
    """A fake service. `feeds` are its accounts' things, by account name (its subclasses'
    own)."""

    name = "fake"
    title = "Fake"
    summary = "A fake service (for tests)."
    settings_model = FakeSettings
    account_model = FakeAccountOptions
    connect = (ConnectMethod.OAUTH, ConnectMethod.TOKENS, ConnectMethod.NONE)
    access_levels = ("read", "write")
    token_model = FakeTokens
    source_filter_model = FakeSourceFilter
    state_model = FakeCursor
    feeds: ClassVar[dict[str, Feed]] = {}
    source_filter_error: ClassVar[Exception | None] = None
    """What checking a source filter fails with, if anything."""

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        cls.feeds = {}

    @classmethod
    def feed(cls, account: str) -> Feed:
        """The feed of the account called `account` (e.g. work)."""
        return cls.feeds.setdefault(account, Feed())

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        return (
            EventType(
                name=f"{cls.name}.thing.happened",
                data=ThingHappened,
                summary="A thing happened in a fake account.",
                example=ThingHappened(thing="t1", text="Hello", label="work"),
                subject=lambda data: f"Thing: {data.text}",
            ),
        )

    def oauth_request(self, access: frozenset[str]) -> OAuthRequest:
        settings = self.settings
        provider = OAuthProvider(
            name=self.name,
            authorize_url=f"{settings.auth_url}/authorize",
            token_url=f"{settings.auth_url}/token",
            revoke_url=f"{settings.auth_url}/revoke",
            userinfo_url=f"{settings.auth_url}/userinfo",
            client_id=settings.client_id,
            client_secret=settings.client_secret,
        )
        return OAuthRequest(
            provider=provider,
            scopes=[f"fake.{level}" for level in sorted(access)],
            extra={"access_type": "offline"},
        )

    async def identify(
        self, credentials: Credentials, options: FakeAccountOptions
    ) -> AccountIdentity:
        if isinstance(credentials, Tokens):
            provider = self.oauth_request(frozenset()).provider
            async with self.http(tool=self.name) as client:
                found = await OAuthClient(provider, http=client, clock=self.clock).userinfo(
                    credentials
                )
            return AccountIdentity(id=str(found["sub"]), display=str(found["email"]))
        if isinstance(credentials, FakeTokens):
            key = credentials.api_key.get_secret_value()
            if key == REFUSED_KEY:
                raise NotAllowed("The fake service refused the API key")
            digest = hashlib.sha256(key.encode()).hexdigest()[:8]
            return AccountIdentity(id=f"key-{digest}", display=f"API key ...{key[-4:]}")
        if options.source is None:
            raise ConfigError("A fake source names what it watches (-O source=...)")
        return AccountIdentity(id=options.source, display=options.source)

    async def watch(
        self,
        account: AccountHandle[FakeAccountOptions],
        events: EventSink,
        state: StateStore[Any],
    ) -> None:
        cursor = await state.load()
        position = cursor.position if isinstance(cursor, FakeCursor) else 0
        feed = self.feed(account.account.name)
        while True:
            feed.woken.clear()
            if feed.errors:
                raise feed.errors.popleft()
            await account.credentials()  # (ReauthRequired, when they are refused)
            things = self._things(account.account.name)
            if len(things) <= position:
                await pause(self.clock, self.settings.poll_every, feed.woken)
                continue
            for thing in things[position:]:
                await events.emit(
                    f"{self.name}.thing.happened",
                    ThingHappened(
                        thing=thing.id,
                        text=thing.text,
                        label=account.options.label,
                        sent_by_udl=thing.sent_by_udl,
                    ),
                    dedupe_key=thing.id,
                )
            position = len(things)
            await state.save(FakeCursor(position=position))

    async def source_filter_matches(
        self, account: AccountHandle[FakeAccountOptions], event: Event, source_filter: BaseModel
    ) -> bool:
        if self.source_filter_error is not None:
            raise self.source_filter_error
        assert isinstance(source_filter, FakeSourceFilter)
        return source_filter.contains in str(event.data.get("text", ""))

    def _things(self, account: str) -> list[Thing]:
        things = list(self.feed(account).things)
        folder = self.settings.feed_dir
        if folder is not None and (folder / account).is_file():
            lines = (folder / account).read_text().splitlines()
            things += [Thing(id=f"line-{n}", text=line) for n, line in enumerate(lines, 1)]
        return things

    # -- operations ----------------------------------------------------------------------------

    @integration_operation(
        "list_things",
        title="List the things",
        hints=OperationHints(read_only=True),
        access="read",
        fan_out=True,
    )
    async def list_things(
        self, account: AccountHandle[FakeAccountOptions], request: ListThingsRequest
    ) -> ItemsPage[Thing]:
        """List the things in an account (those that happened in it), a page at a time."""
        await account.credentials()
        things = [
            thing for thing in self._things(account.account.name) if request.contains in thing.text
        ]
        start = int(request.page or 0)
        end = start + request.limit
        return ItemsPage(items=things[start:end], next_page=str(end) if end < len(things) else None)

    @integration_operation(
        "do_thing",
        title="Do a thing",
        hints=OperationHints(open_world=True),
        access="write",
    )
    async def do_thing(
        self, account: AccountHandle[FakeAccountOptions], request: DoThingRequest
    ) -> DoneThing:
        """Do a thing in an account: it happens there (marked sent_by_udl when a run did it)."""
        await account.credentials()
        [thing] = self.feed(account.account.name).add(
            request.text, sent_by_udl=current_run.get() is not None
        )
        return DoneThing(account=account.ref, thing=thing)

    @integration_operation(
        "version",
        title="Say the fake service's version",
        hints=OperationHints(read_only=True),
        needs_account=False,
    )
    async def version(self, request: VersionRequest) -> FakeVersion:
        """Say the fake service's version (no account needed)."""
        return FakeVersion(version="1.0")


def fake_integration(called: str = "fake") -> type[FakeIntegration]:
    """A FakeIntegration called `called`, with feeds of its own."""

    class Fake(FakeIntegration):
        name = called

    Fake.__qualname__ = Fake.__name__ = f"FakeIntegration[{called}]"
    return Fake


# ---------------------------------------------------------------------------------------------
# Stand-ins for what an integration is given
# ---------------------------------------------------------------------------------------------


class StubHandle[A: AccountOptions](AccountHandle[A]):
    """An account with the credentials it is given (refused: ReauthRequired)."""

    def __init__(
        self, account: Account, options: A, credentials: Credentials, *, refused: bool = False
    ) -> None:
        super().__init__(account, options)
        self._credentials = credentials
        self.refused = refused

    async def credentials(self) -> Credentials:
        if self.refused:
            raise ReauthRequired("The credentials were refused")
        return self._credentials


@dataclass(frozen=True)
class Emitted:
    event_type: str
    data: dict[str, Any]
    dedupe_key: str
    occurred_at: datetime | None


class RecordingSink(EventSink):
    """Records what a watcher emits (each once), refusing types not the integration's."""

    def __init__(self, integration: type[Integration[Any, Any]]) -> None:
        self.types = {event_type.name: event_type for event_type in integration.event_types()}
        self.emitted: list[Emitted] = []

    async def emit(
        self,
        event_type: str,
        data: BaseModel | dict[str, Any],
        *,
        dedupe_key: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        found = self.types.get(event_type)
        if found is None:
            raise PluginError(f"{event_type} is not the integration's")
        values = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        if any(emitted.dedupe_key == dedupe_key for emitted in self.emitted):
            return False
        self.emitted.append(Emitted(event_type, found.validate(values), dedupe_key, occurred_at))
        return True


class MemoryState(StateStore[Any]):
    """A watcher's state, kept here: every state saved is in `saved`."""

    def __init__(self, model: type[BaseModel] | None) -> None:
        self.model = model
        self.saved: list[BaseModel] = []

    async def load(self) -> BaseModel | None:
        return self.saved[-1] if self.saved else None

    async def save(self, state: BaseModel) -> None:
        assert self.model is not None
        assert isinstance(state, self.model)
        self.saved.append(state)


# ---------------------------------------------------------------------------------------------
# HTTP clients of fake servers
# ---------------------------------------------------------------------------------------------


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"No fake server at {request.url}", request=request)


def http_to(*servers: tuple[str, ASGIApp]) -> HttpClientFactory:
    """Clients that reach each of `servers` (a base URL, and its ASGI app) in-process, and
    nothing else."""

    @asynccontextmanager
    async def client(*, tool: str) -> AsyncGenerator[httpx.AsyncClient]:
        mounts: dict[str, httpx.AsyncBaseTransport] = {
            base: httpx.ASGITransport(app) for base, app in servers
        }
        async with httpx.AsyncClient(
            mounts=mounts, transport=httpx.MockTransport(_unreachable)
        ) as found:
            yield found

    return client
