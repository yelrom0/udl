"""
-----------
- base.py -
-----------

Integration: the interface every integration implements (a service or a source udl connects
accounts of: gmail, slack, pages), the Account record, and what an integration is given to work
with an account: an AccountHandle (the account, its options, and its credentials, kept fresh),
an EventSink (what its watcher publishes events into) and a StateStore (where its watcher keeps
its cursor between runs).

An integration says how its accounts connect (ConnectMethod: oauth, tokens, or none for a source
that needs no login), the options each account has (its account_model), the event types it
publishes (event_types(), each named after it) and what its watcher does (watch()), and the
operations it adds: its methods marked @integration_operation, each taking an account and a
request (udl.integrations.operations makes them operations, with an `account` argument).

The public plugin API, which is all an integration imports from udl (a test checks the built-in
ones): this module, udl.integrations.oauth, udl.automations.events, udl.clock, udl.http,
udl.process, udl.errors and udl.plugins.requirements. tests/contract/test_integration_contract.py
is the behaviour every integration must have.
"""

# System Imports
import annotationlib
import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Final, Self

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

# Local Imports
from udl.automations.events import Event, EventType
from udl.clock import Clock
from udl.config import Duration
from udl.core.operations import APISpec, CLISpec, OperationHints, valid_name
from udl.db.models import JsonObject, utcnow
from udl.db.records import Record
from udl.errors import ErrorInfo, PluginError
from udl.http import HttpClientFactory
from udl.integrations.oauth import OAuthClient, OAuthProvider, Tokens
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.base import SecretRef

__all__ = [
    "APISpec",
    "Account",
    "AccountHandle",
    "AccountHealth",
    "AccountIdentity",
    "AccountOptions",
    "AccountStatus",
    "CLISpec",
    "ConnectMethod",
    "Credentials",
    "Duration",
    "EventSink",
    "Integration",
    "IntegrationOperation",
    "IntegrationSettings",
    "ItemsPage",
    "OAuthRequest",
    "OperationHints",
    "StateStore",
    "integration_operation",
]

_MARK: Final = "__udl_integration_operation__"
# An account's name (the user's: work, personal, acme): no colon, which parts it from its
# integration's in `gmail:work`.
ACCOUNT_NAME_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"

type AccountName = Annotated[str, StringConstraints(pattern=ACCOUNT_NAME_PATTERN, max_length=64)]
"""What the user calls an account (work, personal): letters, digits, `_`, `.` and `-`."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class IntegrationSettings(_Model):
    """An integration's settings (each integration's model extends it): its defaults, then its
    [integrations.<name>] table in the config, then what set_plugin_settings set."""


class AccountOptions(_Model):
    """An account's options (each integration's model extends it), e.g. the labels it watches.
    Unknown options are refused, so a misspelt one is never ignored."""


class ConnectMethod(StrEnum):
    """How an account is connected."""

    OAUTH = "oauth"  # an OAuth 2.0 authorization, in the browser
    TOKENS = "tokens"  # tokens the user gives (e.g. their own Slack app's)
    NONE = "none"  # nothing: a source that needs no login (its options say what it is)


class AccountIdentity(_Model):
    """Who an account's login is, as its service says."""

    id: str
    """The service's stable id for the login."""
    display: str
    """How it is shown: e.g. the address, or the team and the user."""
    extra: JsonObject = Field(default_factory=dict)
    """What else the service says of it."""


class AccountHealth(_Model):
    """Whether an account works, as checking it found."""

    ok: bool
    """Whether it works."""
    problems: list[str] = Field(default_factory=list[str])
    """What is wrong with it."""
    scopes: list[str] = Field(default_factory=list[str])
    """What it was granted (an OAuth account's scopes)."""


class OAuthRequest(_Model):
    """What an integration's accounts are authorized with: the provider (with the user's own
    client), the scopes for the access asked for, and extra parameters for its page."""

    provider: OAuthProvider
    """The authorization server, and udl's client of it."""
    scopes: list[str]
    """The scopes to ask for."""
    extra: dict[str, str] = Field(default_factory=dict[str, str])
    """More parameters for the authorization page (e.g. access_type)."""


type Credentials = Tokens | BaseModel | None
"""An account's credentials: OAuth tokens, the integration's token_model (tokens the user
gave), or none (a source that needs no login)."""


class ItemsPage[T](BaseModel):
    """A page of what an operation lists, in one account (what a fan-out operation's method
    gives: the operation merges the pages of the accounts it runs on)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    items: list[T]
    """What it found."""
    next_page: str | None = None
    """What gives the next page (as the request's page), if there is one."""


# ---------------------------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------------------------


class AccountStatus(StrEnum):
    """Where an account is."""

    PENDING = "pending"  # being connected (an OAuth authorization in the browser)
    CONNECTED = "connected"  # its credentials work, as far as udl knows
    NEEDS_REAUTH = "needs_reauth"  # its credentials were refused, or are missing: connect it again
    ERROR = "error"  # connecting it failed


class Account(Record):
    """A connected login of an integration (gmail:work), or, for a source that needs no login,
    one configured source. Its credentials are in the secret store (secret_ref), never here."""

    kind = "account"
    indexed = ("integration", "name", "status", "enabled")
    unique = (("integration", "name"),)

    integration: str
    """The integration it is an account of (e.g. gmail)."""
    name: AccountName
    """What the user calls it (e.g. work): unique within its integration."""
    method: ConnectMethod
    """How it was connected."""
    identity: AccountIdentity | None = None
    """Who its login is (None until it is connected)."""
    access: list[str] = Field(default_factory=list[str])
    """What it was connected for (e.g. read, send)."""
    options: JsonObject = Field(default_factory=dict)
    """Its integration's account options."""
    status: AccountStatus = AccountStatus.PENDING
    """Where it is: pending, connected, needs_reauth or error."""
    enabled: bool = True
    """Whether udl uses it (a disabled account has no watcher, and fan-outs leave it out)."""
    pending_until: AwareDatetime | None = None
    """When the authorization connecting it (again) expires, while one runs."""
    secret_ref: SecretRef | None = None
    """Where the secret store keeps its credentials (None: it has none)."""
    last_error: ErrorInfo | None = None
    """What last went wrong with it."""
    connected_at: AwareDatetime | None = None
    """When it was last connected."""

    @property
    def ref(self) -> str:
        """The account as integration:name (e.g. gmail:work)."""
        return f"{self.integration}:{self.name}"

    @classmethod
    def example(cls) -> Self:
        return cls(
            integration="gmail",
            name="work",
            method=ConnectMethod.OAUTH,
            identity=AccountIdentity(id="1234", display="me@example.com"),
            access=["read", "send"],
            options={"labels": ["INBOX"]},
            status=AccountStatus.CONNECTED,
            secret_ref=SecretRef("accounts/00000000-0000-4000-8000-000000000001/credentials"),
            connected_at=utcnow(),
        )


class AccountHandle[A: AccountOptions](ABC):
    """An account, as an integration's operations and watcher are given it: the account, its
    options (as the integration's account_model has them) and its credentials."""

    def __init__(self, account: Account, options: A) -> None:
        self.account = account
        self.options = options

    @property
    def ref(self) -> str:
        """The account as integration:name (e.g. gmail:work)."""
        return self.account.ref

    @abstractmethod
    async def credentials(self) -> Credentials:
        """The account's credentials, fresh (OAuth tokens are refreshed when they expire). Raises
        ReauthRequired when they are refused or missing: the account then needs connecting
        again. An integration that finds them refused raises ReauthRequired too."""

    async def tokens(self) -> Tokens:
        """An OAuth account's tokens, fresh (see credentials()). Raises PluginError for an account
        connected otherwise."""
        found = await self.credentials()
        if not isinstance(found, Tokens):
            raise PluginError(f"{self.ref} was not connected with OAuth: it has no OAuth tokens")
        return found


class EventSink(ABC):
    """Where an integration's watcher publishes its events: each is stamped with the account it
    happened in, and its dedupe key with the account's id."""

    @abstractmethod
    async def emit(
        self,
        event_type: str,
        data: BaseModel | JsonObject,
        *,
        dedupe_key: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Publish an event of one of the integration's types, once: one whose dedupe key (in
        this account) was seen before is not published again. Returns whether it was new.
        Raises PluginError for a type that is not the integration's, and ConfigError for data
        that does not fit the type."""


class StateStore[S: BaseModel](ABC):
    """Where a watcher keeps its state (e.g. a cursor) between runs, per account: as the
    integration's state_model has it."""

    @abstractmethod
    async def load(self) -> S | None:
        """The state saved last, or None when there is none (the watcher's first run)."""

    @abstractmethod
    async def save(self, state: S) -> None:
        """Save `state` (after each batch of events the watcher publishes)."""


# ---------------------------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationOperation:
    """An operation an integration adds: one of its methods, marked @integration_operation."""

    name: str  # e.g. "send_message" (the operation is <integration>_send_message)
    title: str
    hints: OperationHints
    access: str | None  # what the account must have been connected for (e.g. "send")
    fan_out: bool  # it lists things, and runs on several accounts at once
    needs_account: bool
    cli: CLISpec | None
    api: APISpec | None
    method: Callable[..., Awaitable[BaseModel]]  # (integration, account, request) -> response
    request: type[BaseModel] = BaseModel  # (read from its method by operation_specs())
    response: type[BaseModel] = BaseModel  # (an ItemsPage[T] when it fans out)
    item: type[Any] | None = None  # what the ItemsPage of a fan-out holds


def integration_operation[F: Callable[..., Awaitable[BaseModel]]](
    name: str,
    *,
    title: str,
    hints: OperationHints | None = None,
    access: str | None = None,
    fan_out: bool = False,
    needs_account: bool = True,
    cli: CLISpec | None = None,
    api: APISpec | None = None,
) -> Callable[[F], F]:
    """Mark an integration's method as the operation called `name` (offered as
    `<integration>_<name>`): `async def m(self, account: AccountHandle[A], request: R) -> T`
    (or, with needs_account=False, `(self, request: R)`), whose docstring describes it, for
    people and AI agents. It needs the account to have been connected for `access`. One that
    fans out lists things: it gives an ItemsPage[T] for one account, and runs on several at
    once (so it is read-only)."""

    def mark(method: F) -> F:
        spec = IntegrationOperation(
            name=name,
            title=title,
            hints=hints or OperationHints(),
            access=access,
            fan_out=fan_out,
            needs_account=needs_account,
            cli=cli,
            api=api,
            method=method,
        )
        setattr(method, _MARK, spec)
        return method

    return mark


# ---------------------------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------------------------


class Integration[S: IntegrationSettings, A: AccountOptions](ABC):
    """A service or source udl connects accounts of. Made for a service, with its settings,
    udl's HTTP client and its clock."""

    name: ClassVar[str]  # e.g. "gmail": its operations, event types and accounts are named after it
    title: ClassVar[str]  # e.g. "Gmail"
    summary: ClassVar[str] = ""  # what it is, in a line
    # What it needs installed (`udl plugins list` says what is missing).
    requires: ClassVar[tuple[Requirement, ...]] = ()
    settings_model: ClassVar[type[IntegrationSettings]] = IntegrationSettings
    account_model: ClassVar[type[AccountOptions]] = AccountOptions
    connect: ClassVar[tuple[ConnectMethod, ...]]  # how its accounts connect, the usual first
    # What an account may be connected for (e.g. read, send), the default first.
    access_levels: ClassVar[tuple[str, ...]] = ()
    # For ConnectMethod.TOKENS: the tokens an account is connected with (its SecretStr fields).
    token_model: ClassVar[type[BaseModel] | None] = None
    # What a subscription's source_filter is (e.g. a Gmail search), checked by the integration.
    source_filter_model: ClassVar[type[BaseModel] | None] = None
    state_model: ClassVar[type[BaseModel] | None] = None  # its watcher's state (a cursor)

    def __init__(self, settings: S, *, http: HttpClientFactory, clock: Clock) -> None:
        self.settings = settings
        self.http = http
        self.clock = clock

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        """The event types it publishes, each named <its name>.<what> (e.g.
        gmail.message.received)."""
        return ()

    @classmethod
    def watches(cls) -> bool:
        """Whether it has a watcher (it implements watch())."""
        below = cls.__mro__[: cls.__mro__.index(Integration)]  # (its own classes)
        return any("watch" in vars(klass) for klass in below)

    @classmethod
    def operation_specs(cls) -> list[IntegrationOperation]:
        """The operations it adds (its methods marked @integration_operation), in the order they
        are defined, read from their methods. Raises PluginError for one that is not right."""
        marked: dict[str, IntegrationOperation] = {}
        for klass in reversed(cls.__mro__):
            for attribute, value in vars(klass).items():
                spec = getattr(value, _MARK, None)
                if isinstance(spec, IntegrationOperation):
                    marked[attribute] = spec
                elif attribute in marked:  # (overridden, unmarked)
                    del marked[attribute]
        return [_resolved(cls, spec) for spec in marked.values()]

    def oauth_request(self, access: frozenset[str]) -> OAuthRequest:
        """What an account connected for `access` (e.g. {"read", "send"}) is authorized with:
        the provider, with the user's own client from the settings, and the scopes. For
        integrations that connect with ConnectMethod.OAUTH."""
        raise PluginError(f"The {self.name} integration does not connect accounts with OAuth")

    @abstractmethod
    async def identify(self, credentials: Credentials, options: A) -> AccountIdentity:
        """Who a login is (asked when an account is connected), from its credentials (or, for
        a source that needs no login, its options). Raises ReauthRequired or NotAllowed when
        the credentials are refused, and ConfigError for options that name nothing."""

    async def check(self, account: AccountHandle[A]) -> AccountHealth:
        """Whether an account works (and what it was granted)."""
        credentials = await account.credentials()
        scopes = credentials.scopes if isinstance(credentials, Tokens) else []
        return AccountHealth(ok=True, scopes=scopes)

    async def watch(
        self, account: AccountHandle[A], events: EventSink, state: StateStore[Any]
    ) -> None:
        """Watch an account until cancelled: publish what happens in it into `events` (each
        once), saving its state after each batch, so a watcher started again (after a failure,
        or udl's restart) goes on from where it was without publishing anything twice. Raises
        ReauthRequired when the account's credentials are refused (it is not started again
        until the account is connected again), and what it fails with otherwise (it is started
        again, after a while)."""
        raise NotImplementedError

    async def source_filter_matches(
        self, account: AccountHandle[A], event: Event, source_filter: BaseModel
    ) -> bool:
        """Whether `event` (one of its types, in `account`) passes a subscription's source
        filter (a source_filter_model): what only the integration can check, such as a Gmail
        search."""
        raise PluginError(f"The {self.name} integration has no source filter")

    async def revoke(self, account: Account, credentials: Credentials) -> None:
        """Give an account's credentials back, as it is disconnected: OAuth tokens are revoked
        at the provider; tokens the user gave are theirs to revoke, unless the integration
        says otherwise."""
        if isinstance(credentials, Tokens):
            request = self.oauth_request(frozenset(account.access))
            async with self.http(tool=self.name) as client:
                await OAuthClient(request.provider, http=client, clock=self.clock).revoke(
                    credentials
                )


def _resolved(cls: type[Integration[Any, Any]], spec: IntegrationOperation) -> IntegrationOperation:
    """`spec`, with its request and response read from its method's annotations."""
    what = f"{cls.__qualname__}.{getattr(spec.method, '__name__', spec.name)}"
    if not valid_name(spec.name):
        raise PluginError(
            f"{what}: an operation's name is lower case letters, digits, _ and -, not {spec.name!r}"
        )
    if not inspect.getdoc(spec.method):
        raise PluginError(
            f"{what} has no docstring: it describes the operation to people and to AI agents"
        )
    if spec.fan_out and not (spec.hints.read_only and spec.needs_account):
        raise PluginError(
            f"{what} fans out, so it is read-only and runs on accounts (it lists things in "
            "several at once)"
        )
    if spec.access is not None and spec.access not in cls.access_levels:
        raise PluginError(
            f"{what} needs {spec.access!r} access, which is not one of the {cls.name} "
            f"integration's access_levels ({', '.join(cls.access_levels) or 'none'})"
        )
    forward = annotationlib.Format.FORWARDREF
    annotations = annotationlib.get_annotations(spec.method, format=forward)
    parameters = list(inspect.signature(spec.method, annotation_format=forward).parameters)
    wanted = 3 if spec.needs_account else 2
    request = annotations.get(parameters[-1]) if len(parameters) == wanted else None
    response = annotations.get("return")
    item = _page_item(response) if spec.fan_out else None
    if not (_is_model(request) and _is_model(response)) or (spec.fan_out and item is None):
        takes = "(self, account, request)" if spec.needs_account else "(self, request)"
        gives = "an ItemsPage[T]" if spec.fan_out else "a response"
        raise PluginError(
            f"{what} is not an integration's operation: it takes {takes} and gives {gives}, "
            "annotated as Pydantic models"
        )
    return replace(spec, request=request, response=response, item=item)


def _is_model(annotation: object) -> bool:
    return inspect.isclass(annotation) and issubclass(annotation, BaseModel)


def _page_item(annotation: object) -> type[Any] | None:
    """What an ItemsPage[T] annotation holds (T), or None when it is no ItemsPage."""
    if not (inspect.isclass(annotation) and issubclass(annotation, ItemsPage)):
        return None
    args = annotation.__pydantic_generic_metadata__["args"]
    return args[0] if len(args) == 1 else None
