"""
---------------
- accounts.py -
---------------

Accounts (each a connected login of an integration: udl.plugins.integrations.base.Account), and
the integrations they belong to:

- Integrations (UDLService.integrations) makes each integration once per service, from its
  plugin settings (and again once they change).
- AccountManager (UDLService.accounts) finds accounts (AccountRef: `gmail:work`, or `work` where
  the integration is known), saves and removes them, and gives their handles.

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
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, NoReturn, Self
from uuid import UUID

# Package Imports
import pydantic_core
from pydantic import BaseModel, SecretStr, ValidationError

# Local Imports
from udl.automations.bus import NewEvent
from udl.automations.events import AccountEventData
from udl.core.operations import valid_name
from udl.db.records import RecordQuery
from udl.errors import ConfigError, DoesNotExist, PluginError, ReauthRequired, UDLError
from udl.integrations.oauth import OAuthClient, Tokens
from udl.plugins.integrations.base import (
    ACCOUNT_NAME_PATTERN,
    Account,
    AccountHandle,
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

    async def find(self, integration: str, name: str) -> Account | None:
        """The account called `name` of `integration`, if there is one."""
        found = await self._service.db.list_records(
            Account, RecordQuery(where={"integration": integration, "name": name}, limit=1)
        )
        return found[0] if found else None

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
        return sorted(found, key=lambda account: (account.integration, account.name))

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
        return saved

    async def remove(self, account: Account) -> bool:
        """Delete an account and forget its credentials. Returns whether it was there."""
        await self._service.secrets.delete_prefix(SecretRef.of("accounts", account.id))
        removed = await self._service.db.delete_record(Account, account.id)
        self._service.bus.subscriptions_changed()
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
                return _token_model(integration).model_validate_json(kept.get_secret_value())
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


def _again(account: Account) -> str:
    return f"Connect it again: `udl accounts add {account.integration} {account.name} --reconnect`."


def _token_model(integration: Integration[Any, Any]) -> type[BaseModel]:
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
