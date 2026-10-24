"""
-------------
- models.py -
-------------

The accounts' operations' requests and responses (list_accounts, get_account, connect_account,
set_account, check_account, disconnect_account), and what is shown of an account: never its
credentials, which are in the secret store
"""

# System Imports
from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

# Package Imports
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

# Local Imports
from udl.core.models import Deleted
from udl.db.models import JsonObject
from udl.errors import ErrorInfo
from udl.integrations.watchers import WatcherStatus
from udl.plugins.integrations.base import (
    Account,
    AccountIdentity,
    AccountName,
    AccountStatus,
    ConnectMethod,
)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


def _this_udl_s(path: str | None) -> str | None:
    """`path`, when it is a path of this udl's (so a callback sends no browser elsewhere)."""
    if path is not None and not (path.startswith("/") and not path.startswith("//")):
        raise ValueError("it is a path of this udl's, e.g. /ui/accounts")
    return path


class ConnectAccountRequest(_Model):
    """Connect an account of an integration."""

    integration: str
    """The integration to connect an account of (e.g. gmail)."""
    name: AccountName
    """What to call the account (e.g. work): unique within its integration."""
    method: ConnectMethod | None = None
    """How to connect it: oauth (in a browser), tokens (given here) or none (a source that needs
    no login); by default tokens when they are given, else the integration's usual way."""
    access: list[str] = Field(default_factory=list[str])
    """What to connect it for (e.g. read, send): by default, the integration's first access
    level."""
    options: JsonObject = Field(default_factory=dict)
    """Its options (the integration's account options, e.g. the labels to watch); connecting it
    again keeps those it has unless others are given."""
    tokens: dict[str, SecretStr] | None = None
    """The tokens to connect it with (e.g. a Slack app's), by name: kept in the secret store."""
    reconnect: bool = False
    """Connect an account that was connected before again: it keeps its id, options and
    subscriptions, and its credentials are replaced."""
    return_to: Annotated[str | None, AfterValidator(_this_udl_s)] = None
    """Where the browser goes once an OAuth authorization is answered: a path of this udl's
    (e.g. /ui/accounts)."""


class AccountInfo(_Model):
    """An account, as it is shown (its credentials never are)."""

    id: UUID
    """The account's id."""
    account: str
    """The account as integration:name (e.g. gmail:work)."""
    integration: str
    """Its integration."""
    name: str
    """Its name."""
    method: ConnectMethod
    """How it was connected."""
    status: AccountStatus
    """pending (being connected), connected, needs_reauth (connect it again) or error."""
    enabled: bool
    """Whether udl uses it."""
    identity: AccountIdentity | None
    """Who its login is."""
    access: list[str]
    """What it was connected for."""
    options: JsonObject
    """Its options."""
    pending_until: AwareDatetime | None
    """When the authorization connecting it expires, while one runs."""
    last_error: ErrorInfo | None
    """What last went wrong with it."""
    connected_at: AwareDatetime | None
    """When it was last connected."""
    created_at: datetime
    """When it was first added."""
    watcher: WatcherStatus | None = None
    """How its watcher is, if it has had one."""

    @classmethod
    def of(cls, account: Account, *, watcher: WatcherStatus | None = None) -> Self:
        return cls(
            id=account.id,
            account=account.ref,
            integration=account.integration,
            name=account.name,
            method=account.method,
            status=account.status,
            enabled=account.enabled,
            identity=account.identity,
            access=account.access,
            options=account.options,
            pending_until=account.pending_until,
            last_error=account.last_error,
            connected_at=account.connected_at,
            created_at=account.created_at,
            watcher=watcher,
        )


class Connection(_Model):
    """An account being connected (or connected)."""

    account: AccountInfo
    """The account."""
    authorization_url: str | None = None
    """The page to open in a browser to finish connecting it (OAuth): its answer is waited for
    10 minutes. None: it is connected."""


class ListAccountsRequest(_Model):
    """List the accounts."""

    integration: str | None = None
    """Only this integration's (e.g. gmail)."""


class AccountList(_Model):
    accounts: list[AccountInfo]
    """The accounts, by integration and name."""


class AccountRequest(_Model):
    """An account."""

    account: str
    """The account, as integration:name (e.g. gmail:work)."""


class SetAccountRequest(_Model):
    """Change an account's options, or whether udl uses it."""

    account: str
    """The account, as integration:name (e.g. gmail:work)."""
    options: JsonObject = Field(default_factory=dict)
    """Options to set, over those it has (null takes one away)."""
    enabled: bool | None = None
    """Whether udl uses it: a disabled account has no watcher, and operations leave it out."""


class Disconnected(Deleted):
    revoked: bool | None = None
    """Whether its credentials were given back to the service: None when there was nothing to
    give back (tokens the user gave are theirs to revoke), false when that failed."""
    subscriptions: list[str] = Field(default_factory=list[str])
    """The subscriptions that name it, which are kept: they get none of its events until an
    account of its name is connected again."""
