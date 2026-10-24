"""
---------------
- accounts.py -
---------------

Accounts (each a connected login of an integration: udl.plugins.integrations.base.Account), and
the integrations they belong to:

- Integrations (UDLService.integrations) makes each integration once per service, from its
  plugin settings (and again once they change).
- AccountManager (UDLService.accounts) finds accounts (AccountRef: `gmail:work`, or `work` where
  the integration is known), saves and removes them, gives their handles, and connects them.

Connecting an account (connect()) is one of three ways, as its integration says. With OAuth, the
account is pending while the user answers the authorization page in a browser, for at most 10
minutes: the page sends the browser back to the server's /accounts/callback when this udl serves
HTTP (`udl serve`: callback_url), else to a listener on 127.0.0.1 in this process (the CLI, `udl
mcp`), whose task finishes connecting. The authorization (with its PKCE verifier) is kept in this
process only, under its one-time state, which is what the callback is checked by. With tokens,
the integration's token model takes them, and they are kept. A source that needs no login is
connected with its options. Either way the integration says who the login is (its identity),
and udl.account.connected is published. A name that is taken is refused, unless the account is
connected again (reconnect), which keeps its id, options and subscriptions; an account that was
never connected is connected afresh.

An account's credentials are one secret, accounts/<its id>/credentials, holding them as JSON (an
OAuth account's tokens, or the tokens it was given). Its handle gives them fresh: OAuth tokens
that expire within a minute are refreshed, one refresh at a time per account, and the new ones
kept. Credentials that are refused, or missing (an account synced from another machine has none
in this one's store), mark the account needs_reauth and say so (udl.account.needs_reauth), once
per streak, until it is connected again: a handle gives working credentials or raises
ReauthRequired, nothing in between.
"""

# System Imports
import asyncio
import functools
import logging
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, NoReturn, Self
from uuid import UUID

# Package Imports
import pydantic_core
from pydantic import BaseModel, SecretStr, ValidationError

# Local Imports
from udl.automations.bus import NewEvent
from udl.automations.events import AccountEventData, EventRecord
from udl.automations.subscriptions import Subscription
from udl.core.operations import valid_name
from udl.db.models import JsonObject
from udl.db.records import RecordQuery
from udl.errors import (
    AlreadyExists,
    ConfigError,
    DoesNotExist,
    DuplicateKey,
    NotAllowed,
    PluginError,
    ReauthRequired,
    UDLError,
)
from udl.integrations.models import AccountInfo, ConnectAccountRequest, Connection
from udl.integrations.oauth import (
    Authorization,
    LoopbackRedirect,
    OAuthClient,
    OAuthProvider,
    Tokens,
)
from udl.plugins.integrations.base import (
    ACCOUNT_NAME_PATTERN,
    Account,
    AccountHandle,
    AccountIdentity,
    AccountOptions,
    AccountStatus,
    ConnectMethod,
    Credentials,
    Integration,
)
from udl.plugins.registry import INTEGRATIONS
from udl.plugins.secrets.base import SecretRef

if TYPE_CHECKING:
    from udl.core.service import UDLService

log = logging.getLogger(__name__)

CREDENTIALS: Final = "credentials"
"""The secret an account's credentials are, under accounts/<its id>."""
PENDING_FOR: Final = timedelta(minutes=10)
"""How long an OAuth authorization connecting an account is waited for."""
CALLBACK_PATH: Final = "/accounts/callback"
"""Where, on udl's server, OAuth authorizations send the browser back to."""
_ACCOUNT_NAME: Final = re.compile(ACCOUNT_NAME_PATTERN)
_ACCOUNT_NAME_LENGTH: Final = 64


@dataclass(frozen=True)
class AccountRef:
    """An account, by its integration and name: `gmail:work`."""

    integration: str
    name: str

    @classmethod
    def parse(cls, text: str, integration: str | None = None) -> Self:
        """The account `text` names: integration:name, or (where `integration` is known) its
        name. Raises ConfigError when it names none, or one of another integration."""
        named, colon, name = text.partition(":")
        if not colon:
            if integration is None:
                raise ConfigError(
                    f"{text!r} names no integration: an account is integration:name "
                    "(e.g. gmail:work)"
                )
            named, name = integration, text
        if not (
            valid_name(named)
            and _ACCOUNT_NAME.fullmatch(name)
            and len(name) <= _ACCOUNT_NAME_LENGTH
        ):
            raise ConfigError(
                f"{text!r} is not an account: one is integration:name (e.g. gmail:work)"
            )
        if integration is not None and named != integration:
            raise ConfigError(f"{text} is not a {integration} account")
        return cls(named, name)

    def __str__(self) -> str:
        return f"{self.integration}:{self.name}"


# ---------------------------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------------------------


class Integrations:
    """A service's integrations, each made once, from its plugin settings (and again once they
    change)."""

    def __init__(self, service: UDLService) -> None:
        self._service = service
        self._made: dict[str, Integration[Any, Any]] = {}

    def names(self) -> list[str]:
        """The integrations' names."""
        return [info.name for info in self._service.registry.plugins(INTEGRATIONS)]

    def plugin(self, name: str) -> type[Integration[Any, Any]]:
        """The integration called `name`. Raises DoesNotExist."""
        names = self.names()
        if name not in names:
            raise DoesNotExist(
                f"No integration called {name!r}",
                hint=f"The integrations: {', '.join(names) or 'none'} (`udl plugins list`).",
            )
        return self._service.registry.get(INTEGRATIONS, name)

    async def get(self, name: str) -> Integration[Any, Any]:
        """The integration called `name`, made from its settings. Raises DoesNotExist, or
        ConfigError when its settings are not valid."""
        found = self._made.get(name)
        if found is None:
            plugin = self.plugin(name)
            settings = await self._service.plugin_settings(name)
            found = plugin(settings, http=self._service.http, clock=self._service.clock)
            self._made[name] = found
        return found

    def changed(self, name: str) -> None:
        """Make the integration called `name` again the next time it is asked for (its settings
        changed)."""
        self._made.pop(name, None)

    def source_filter_checks(self) -> dict[str, Callable[[JsonObject], None]]:
        """What checks a subscription's source filter, by the integration whose events it is
        for: its source_filter_model (raising ConfigError for what it does not take)."""
        return {
            name: functools.partial(_check_source_filter, self.plugin(name))
            for name in self.names()
        }

    async def source_filter_matches(self, subscription: Subscription, event: EventRecord) -> bool:
        """Whether `event` passes `subscription`'s source filter, as its account's integration
        says. Raises what keeps it from saying (the event has no account, the filter is not
        valid any more, the integration fails)."""
        accounts = self._service.accounts
        account = await accounts.name_of(event.account_id)
        found = None if account is None else await accounts.get(account)
        if found is None:
            raise ConfigError("the event's account is not connected (any more)")
        integration = await self.get(found.integration)
        model = integration.source_filter_model
        if model is None:
            raise PluginError(f"The {integration.name} integration has no source filter")
        try:
            checked = model.model_validate(subscription.source_filter)
        except ValidationError as e:
            raise ConfigError(f"it is not valid: {_problems(e)}") from e
        handle = await accounts.handle(found)
        return await integration.source_filter_matches(handle, event.event(), checked)


# ---------------------------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------------------------


class _Handle[A: AccountOptions](AccountHandle[A]):
    def __init__(
        self,
        manager: AccountManager,
        integration: Integration[Any, A],
        account: Account,
        options: A,
    ) -> None:
        super().__init__(account, options)
        self._manager = manager
        self._integration = integration

    async def credentials(self) -> Credentials:
        return await self._manager.credentials(self.account, self._integration)


class AccountManager:
    """The accounts of a service's databases, with their credentials in its secret store."""

    def __init__(self, service: UDLService) -> None:
        self._service = service
        self._locks: defaultdict[UUID, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._flows: dict[str, _Flow] = {}  # (the authorizations waited for, by their state)
        self._listening: dict[UUID, _Listening] = {}  # (by account id)
        self.callback_url: str | None = None
        """Where OAuth authorizations send the browser back to, when this udl serves HTTP (`udl
        serve`): its /accounts/callback. None: a listener on 127.0.0.1, in this process."""

    async def close(self) -> None:
        """Stop waiting for the authorizations this process listens for."""
        for account_id in list(self._listening):
            await self._stop_listening(account_id)

    async def find(self, integration: str, name: str) -> Account | None:
        """The account called `name` of `integration`, if there is one."""
        found = await self._service.db.list_records(
            Account, RecordQuery(where={"integration": integration, "name": name}, limit=1)
        )
        return await self._current(found[0]) if found else None

    async def get(self, ref: str | AccountRef, integration: str | None = None) -> Account:
        """The account `ref` names (integration:name, or its name where `integration` is
        known). Raises DoesNotExist (naming the integration's accounts), or ConfigError."""
        wanted = ref if isinstance(ref, AccountRef) else AccountRef.parse(ref, integration)
        found = await self.find(wanted.integration, wanted.name)
        if found is None:
            others = [account.ref for account in await self.list(wanted.integration)]
            raise DoesNotExist(
                f"No {wanted.integration} account called {wanted.name!r}",
                hint=f"The {wanted.integration} accounts: {', '.join(others)}."
                if others
                else f"`udl accounts add {wanted.integration} {wanted.name}` connects one.",
            )
        return found

    async def list(self, integration: str | None = None) -> list[Account]:
        """The accounts (of `integration`, or of every one), by integration and name."""
        where = {} if integration is None else {"integration": integration}
        found = await self._service.db.list_records(Account, RecordQuery(where=where))
        current = [await self._current(account) for account in found]
        return sorted(current, key=lambda account: (account.integration, account.name))

    async def names(self) -> dict[UUID, str]:
        """Each account's integration:name, by its id."""
        return {account.id: account.ref for account in await self.list()}

    async def name_of(self, account_id: UUID | None) -> str | None:
        """The integration:name of the account whose id is `account_id`, if there is one."""
        if account_id is None:
            return None
        found = await self._service.db.get_record(Account, account_id)
        return found.ref if found is not None else None

    async def save(self, account: Account) -> Account:
        """Store `account`. Raises DuplicateKey for a name its integration has another of."""
        saved = await self._service.db.upsert_record(account)
        self._service.bus.subscriptions_changed()  # (it knows the accounts by name)
        self._service.watchers.changed()
        return saved

    async def remove(self, account: Account) -> bool:
        """Delete an account and forget its credentials. Returns whether it was there."""
        await self._service.secrets.delete_prefix(SecretRef.of("accounts", account.id))
        removed = await self._service.db.delete_record(Account, account.id)
        self._service.bus.subscriptions_changed()
        self._service.watchers.changed()
        return removed

    async def keep_credentials(self, account: Account, credentials: BaseModel) -> Account:
        """`account`, with `credentials` (its tokens) kept in the secret store, saved."""
        ref = SecretRef.of("accounts", account.id, CREDENTIALS)
        await self._service.secrets.set(ref, SecretStr(revealed(credentials)))
        return await self.save(account.model_copy(update={"secret_ref": ref}))

    async def handle(self, account: Account) -> AccountHandle[Any]:
        """The handle an integration is given for `account`. Raises ConfigError when its
        options are not its integration's, and DoesNotExist for an integration udl lacks."""
        integration = await self._service.integrations.get(account.integration)
        try:
            options = integration.account_model.model_validate(account.options)
        except ValidationError as e:
            problems = "; ".join(
                f"{'.'.join(map(str, problem['loc'])) or 'options'}: {problem['msg']}"
                for problem in e.errors()
            )
            raise ConfigError(
                f"{account.ref}'s options are not valid: {problems}",
                hint=f"Set them again (`udl accounts set {account.ref} -O KEY=VALUE`).",
            ) from e
        return _Handle(self, integration, account, options)

    async def credentials(
        self, account: Account, integration: Integration[Any, Any]
    ) -> Credentials:
        """`account`'s credentials, fresh (see the module's docstring). Raises ReauthRequired."""
        async with self._locks[account.id]:
            current = await self._service.db.get_record(Account, account.id)
            if current is None:
                raise ReauthRequired(
                    f"{account.ref} is no longer connected",
                    hint=f"`udl accounts add {account.integration} {account.name}` connects it.",
                )
            if current.method is ConnectMethod.NONE:
                return None
            if current.status is AccountStatus.NEEDS_REAUTH:
                raise ReauthRequired(f"{current.ref} needs connecting again", hint=_again(current))
            store = self._service.secrets
            kept = None if current.secret_ref is None else await store.get(current.secret_ref)
            if kept is None or current.secret_ref is None:
                await self._refused(
                    current,
                    ReauthRequired(
                        f"udl has no credentials for {current.ref} here (it was connected on "
                        "another machine, or they were forgotten)",
                        hint=_again(current),
                    ),
                )
            if current.method is ConnectMethod.TOKENS:
                return token_model(integration).model_validate_json(kept.get_secret_value())
            tokens = Tokens.model_validate_json(kept.get_secret_value())
            if not tokens.expiring(self._service.clock.now()):
                return tokens
            provider = integration.oauth_request(frozenset(current.access)).provider
            try:
                async with self._service.http(tool=current.integration) as client:
                    oauth = OAuthClient(provider, http=client, clock=self._service.clock)
                    fresh = await oauth.refresh(tokens)
            except ReauthRequired as e:
                await self._refused(
                    current, ReauthRequired(f"{current.ref}: {e.message}", hint=_again(current))
                )
            await store.set(current.secret_ref, SecretStr(revealed(fresh)))
            return fresh

    # -- connecting ----------------------------------------------------------------------------

    async def connect(self, request: ConnectAccountRequest) -> Connection:
        """Connect an account (see the module's docstring). Raises DoesNotExist for an
        integration udl lacks, AlreadyExists for a name that is taken, ConfigError for what the
        integration does not take, and what it raises when it refuses the credentials."""
        integrations = self._service.integrations
        plugin = integrations.plugin(request.integration)
        integration = await integrations.get(request.integration)
        method = request.method or (
            ConnectMethod.TOKENS if request.tokens is not None else plugin.connect[0]
        )
        if method not in plugin.connect:
            ways = ", ".join(str(way) for way in plugin.connect)
            raise ConfigError(f"{plugin.name} accounts connect with {ways}, not {method}")
        access = list(dict.fromkeys(request.access)) or list(plugin.access_levels[:1])
        unknown = [level for level in access if level not in plugin.access_levels]
        if unknown:
            raise ConfigError(
                f"{plugin.name} accounts are not connected for {', '.join(unknown)}",
                hint=f"They are for {', '.join(plugin.access_levels) or 'nothing in particular'}.",
            )
        existing = await self.find(plugin.name, request.name)
        if existing is not None and existing.connected_at is not None and not request.reconnect:
            raise AlreadyExists(
                f"{existing.ref} is connected already",
                hint="Connect it again (--reconnect), keeping its options and subscriptions, or "
                "give the new one another name.",
            )
        kept = existing.options if existing is not None else {}
        options = _options(plugin, request.name, request.options or kept)
        account = existing or Account(integration=plugin.name, name=request.name, method=method)
        if method is ConnectMethod.OAUTH:
            return await self._authorize(integration, account, access, options, request.return_to)
        credentials = _tokens(plugin, request.tokens) if method is ConnectMethod.TOKENS else None
        identity = await integration.identify(
            credentials, plugin.account_model.model_validate(options)
        )
        connected = await self._connected(
            account,
            method,
            access=access,
            options=options,
            identity=identity,
            credentials=credentials,
        )
        return Connection(account=AccountInfo.of(connected))

    async def complete(self, callback: Mapping[str, str]) -> Completed:
        """Finish connecting the account an OAuth authorization was for, from its callback's
        query. Raises NotAllowed when it answers no authorization this process waits for (its
        state), it expired, or it was refused; the account is then failed."""
        flow = self._flows.pop(callback.get("state", ""), None)
        if flow is None:
            raise NotAllowed(
                "That is the answer to no authorization this udl waits for: it was answered "
                "already, it expired, or another udl started it",
                hint="Start connecting the account again.",
            )
        if self._service.clock.now() >= flow.until:
            error = NotAllowed(
                f"Connecting {flow.ref} was not finished within 10 minutes",
                hint="Start connecting it again.",
            )
            await self._failed(flow.account_id, error)
            raise error
        integration = await self._service.integrations.get(flow.integration)
        try:
            async with self._service.http(tool=flow.integration) as client:
                oauth = OAuthClient(flow.provider, http=client, clock=self._service.clock)
                tokens = await oauth.exchange(flow.authorization, callback)
            identity = await integration.identify(
                tokens, integration.account_model.model_validate(flow.options)
            )
        except UDLError as e:
            await self._failed(flow.account_id, e)
            raise
        account = await self._service.db.get_record(Account, flow.account_id)
        if account is None:
            raise NotAllowed(
                f"{flow.ref} was removed while it was being connected",
                hint="Start connecting it again.",
            )
        connected = await self._connected(
            account,
            ConnectMethod.OAUTH,
            access=flow.access,
            options=flow.options,
            identity=identity,
            credentials=tokens,
        )
        return Completed(connected, flow.return_to)

    async def finished(self, account_id: UUID) -> Account:
        """The account this process listens for the authorization of, once it is connected.
        Raises what connecting it failed with, and DoesNotExist when nothing is listened for."""
        listening = self._listening.get(account_id)
        if listening is None:
            raise DoesNotExist("This udl is not waiting for that account's authorization")
        try:
            return (await listening.task).account
        finally:
            self._listening.pop(account_id, None)

    async def _authorize(
        self,
        integration: Integration[Any, Any],
        account: Account,
        access: list[str],
        options: JsonObject,
        return_to: str | None,
    ) -> Connection:
        """Start the OAuth authorization of `account`: it is pending until it is answered."""
        request = integration.oauth_request(frozenset(access))
        listener = None
        redirect = self.callback_url
        if redirect is None:
            listener = await LoopbackRedirect.listen("/callback", PENDING_FOR.total_seconds())
            redirect = listener.uri
        try:
            async with self._service.http(tool=integration.name) as client:
                oauth = OAuthClient(request.provider, http=client, clock=self._service.clock)
                authorization = oauth.authorize(request.scopes, redirect, extra=request.extra)
            until = self._service.clock.now() + PENDING_FOR
            pending = await self._saved(
                account.model_copy(update={"pending_until": until, "last_error": None})
            )
        except BaseException:
            if listener is not None:
                await listener.close()
            raise
        await self._forget(pending.id)  # (an authorization started before for it is no more)
        flow = _Flow(
            account_id=pending.id,
            ref=pending.ref,
            integration=integration.name,
            provider=request.provider,
            authorization=authorization,
            access=access,
            options=options,
            until=until,
            return_to=return_to,
        )
        self._flows[authorization.state] = flow
        if listener is not None:
            task = asyncio.create_task(self._listen(listener, flow), name=f"connecting {flow.ref}")
            task.add_done_callback(_retrieved)
            self._listening[pending.id] = _Listening(task, listener)
        return Connection(account=AccountInfo.of(pending), authorization_url=authorization.url)

    async def _listen(self, listener: LoopbackRedirect, flow: _Flow) -> Completed:
        """Wait for the answer to `flow`'s authorization on `listener`, then finish it."""
        try:
            callback = await listener.wait()
        except NotAllowed as e:
            self._flows.pop(flow.authorization.state, None)
            await self._failed(flow.account_id, e)
            raise
        finally:
            await listener.close()
        return await self.complete(callback)

    async def _forget(self, account_id: UUID) -> None:
        """Forget the authorizations waited for for an account."""
        for state, flow in list(self._flows.items()):
            if flow.account_id == account_id:
                del self._flows[state]
        await self._stop_listening(account_id)

    async def _stop_listening(self, account_id: UUID) -> None:
        listening = self._listening.pop(account_id, None)
        if listening is not None:
            listening.task.cancel()
            await asyncio.wait([listening.task])
            await listening.listener.close()  # (the task's own, unless it never ran)

    async def _connected(
        self,
        account: Account,
        method: ConnectMethod,
        *,
        access: list[str],
        options: JsonObject,
        identity: AccountIdentity,
        credentials: BaseModel | None,
    ) -> Account:
        """`account`, connected (with its credentials kept), and said so."""
        connected = account.model_copy(
            update={
                "method": method,
                "access": access,
                "options": options,
                "identity": identity,
                "status": AccountStatus.CONNECTED,
                "pending_until": None,
                "last_error": None,
                "connected_at": self._service.clock.now(),
            }
        )
        if credentials is not None:
            saved = await self.keep_credentials(await self._saved(connected), credentials)
        else:
            await self._service.secrets.delete_prefix(SecretRef.of("accounts", account.id))
            saved = await self._saved(connected.model_copy(update={"secret_ref": None}))
        log.info("Connected %s (%s)", saved.ref, identity.display)
        await self.tell("udl.account.connected", saved)
        return saved

    async def _saved(self, account: Account) -> Account:
        """`account`, saved. Raises AlreadyExists when another of its name was saved since."""
        try:
            return await self.save(account)
        except DuplicateKey as e:
            raise AlreadyExists(f"{account.ref} was connected meanwhile") from e

    async def _failed(self, account_id: UUID, error: UDLError) -> None:
        """Say why connecting an account failed (one never connected is then in error)."""
        account = await self._service.db.get_record(Account, account_id)
        if account is not None:
            await self._failing(account, error)

    async def _failing(self, account: Account, error: UDLError) -> Account:
        update: dict[str, Any] = {"pending_until": None, "last_error": error.to_info()}
        if account.connected_at is None:
            update["status"] = AccountStatus.ERROR
        log.warning("Connecting %s failed: %s", account.ref, error.message)
        return await self.save(account.model_copy(update=update))

    async def _current(self, account: Account) -> Account:
        """`account`, failed if the authorization connecting it expired."""
        until = account.pending_until
        if until is None or until > self._service.clock.now():
            return account
        await self._forget(account.id)
        return await self._failing(
            account,
            NotAllowed(
                f"Connecting {account.ref} did not finish within 10 minutes",
                hint="Start connecting it again.",
            ),
        )

    # -- credentials ---------------------------------------------------------------------------

    async def needs_reauth(self, account: Account, error: ReauthRequired) -> None:
        """Mark `account` needs_reauth (its credentials were refused: `error`), saying so unless
        it already was."""
        async with self._locks[account.id]:
            current = await self._service.db.get_record(Account, account.id)
            if current is not None:
                await self._mark(current, error)

    async def _refused(self, account: Account, error: ReauthRequired) -> NoReturn:
        await self._mark(account, error)
        raise error

    async def _mark(self, account: Account, error: ReauthRequired) -> None:
        if account.status is AccountStatus.NEEDS_REAUTH:  # (the streak goes on)
            return
        marked = await self.save(
            account.model_copy(
                update={"status": AccountStatus.NEEDS_REAUTH, "last_error": error.to_info()}
            )
        )
        log.warning("%s needs connecting again: %s", account.ref, error.message)
        await self.tell("udl.account.needs_reauth", marked, error)

    async def tell(self, event_type: str, account: Account, error: UDLError | None = None) -> None:
        """Publish what happened to `account` (a udl.account.* event)."""
        data = AccountEventData(
            account=account.ref,
            account_id=account.id,
            integration=account.integration,
            error=error.to_info() if error is not None else None,
        )
        try:
            await self._service.bus.publish(
                NewEvent(type=event_type, data=data.model_dump(mode="json"), account_id=account.id)
            )
        except UDLError as e:
            log.warning("Could not publish %s for %s: %s", event_type, account.ref, e)


@dataclass(frozen=True)
class _Flow:
    """An OAuth authorization this process waits for the answer to."""

    account_id: UUID
    ref: str
    integration: str
    provider: OAuthProvider
    authorization: Authorization
    access: list[str]
    options: JsonObject
    until: datetime
    return_to: str | None


@dataclass(frozen=True)
class _Listening:
    """A listener this process waits on for an authorization's answer, and what waits."""

    task: asyncio.Task[Completed]
    listener: LoopbackRedirect


@dataclass(frozen=True)
class Completed:
    """An account an OAuth authorization connected, and where the browser goes next."""

    account: Account
    return_to: str | None


def _retrieved(task: asyncio.Task[Completed]) -> None:
    """(What a listener's task failed with is said by finished(), if anyone asks, else here.)"""
    if not task.cancelled() and (error := task.exception()) is not None:
        log.debug("Connecting an account failed: %s", error)


def _options(plugin: type[Integration[Any, Any]], name: str, given: JsonObject) -> JsonObject:
    """`given`, as the account options of `plugin`'s account called `name`. Raises ConfigError."""
    try:
        options = plugin.account_model.model_validate(given)
    except ValidationError as e:
        raise ConfigError(
            f"The options of {plugin.name}:{name} are not valid: {_problems(e)}",
            hint=f"Its options: {', '.join(plugin.account_model.model_fields) or 'none'}.",
        ) from e
    return options.model_dump(mode="json", exclude_unset=True)


def _tokens(plugin: type[Integration[Any, Any]], given: dict[str, SecretStr] | None) -> BaseModel:
    """The tokens `given`, as `plugin`'s token model. Raises ConfigError."""
    model = token_model(plugin)
    if given is None:
        raise ConfigError(
            f"Connecting a {plugin.name} account with tokens takes them",
            hint=f"Its tokens: {', '.join(model.model_fields)}.",
        )
    try:
        return model.model_validate(given)
    except ValidationError as e:
        raise ConfigError(f"The {plugin.name} tokens are not valid: {_problems(e)}") from e


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'it'}: {problem['msg']}"
        for problem in error.errors()
    )


def _check_source_filter(plugin: type[Integration[Any, Any]], source_filter: JsonObject) -> None:
    """Raise ConfigError when `plugin` would not take `source_filter`."""
    model = plugin.source_filter_model
    if model is None:
        raise ConfigError(
            f"The {plugin.name} integration has no source filter",
            hint="Leave the source filter out (a filter on the events' data still works).",
        )
    try:
        model.model_validate(source_filter)
    except ValidationError as e:
        raise ConfigError(
            f"The source filter is not a {plugin.name} one: {_problems(e)}",
            hint=f"Its fields: {', '.join(model.model_fields)}.",
        ) from e


def _again(account: Account) -> str:
    return f"Connect it again: `udl accounts add {account.integration} {account.name} --reconnect`."


def token_model(
    integration: type[Integration[Any, Any]] | Integration[Any, Any],
) -> type[BaseModel]:
    """The tokens an integration's accounts connect with. Raises PluginError when it has none."""
    if integration.token_model is None:
        raise PluginError(f"The {integration.name} integration has no token_model")
    return integration.token_model


def revealed(credentials: BaseModel) -> str:
    """`credentials` as JSON with their secrets in it: what the secret store keeps."""
    return pydantic_core.to_json(_reveal(credentials.model_dump())).decode()


def _reveal(value: Any) -> Any:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: _reveal(item) for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [_reveal(item) for item in value]  # pyright: ignore[reportUnknownVariableType]
    return value
