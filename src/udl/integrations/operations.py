"""
-----------------
- operations.py -
-----------------

An integration's operations: each of its methods marked @integration_operation is the operation
`<integration>_<name>`, whose request is the method's with `account` put in front (e.g.
GmailSendMessageRequest): the account to use, by name (work, or gmail:work), which may be left out
while the integration has one account. udl's generated adapters make it a command, a route and
an MCP tool (udl.interfaces.adapters), as for any plugin's operation.

An operation that fans out lists things (so it only reads): its account may also be a list of
them, or "*" for every one. It runs on them at once (FAN_OUT at a time) and merges what each
found, every item tagged with its account; an account that fails is listed with why while the
others' items still come back. Its answer is a FanOutResult, for one account or many.

Before the method runs the account is checked: it must exist (DoesNotExist names the others),
be enabled, and have been connected for what the operation needs (NotAllowed says how to connect
it again for that); with two or more accounts, leaving it out is AmbiguousAccount. When the
method finds the account's credentials refused (ReauthRequired), the account is marked
needs_reauth.
"""

# System Imports
import asyncio
import inspect
from typing import TYPE_CHECKING, Any, Final, cast

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, create_model

# Local Imports
from udl.core.operations import Operation
from udl.errors import (
    AmbiguousAccount,
    ConfigError,
    DoesNotExist,
    ErrorInfo,
    NotAllowed,
    PluginError,
    ReauthRequired,
    UDLError,
)
from udl.integrations.accounts import AccountRef
from udl.plugins.integrations.base import (
    Account,
    Integration,
    IntegrationOperation,
    ItemsPage,
)

if TYPE_CHECKING:
    from udl.core.service import UDLService

FAN_OUT: Final = 4
"""How many accounts a fan-out operation runs on at once."""
EVERY: Final = "*"
"""What names every account, for a fan-out operation."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class AccountItem[T](_Model):
    """What an operation found, and the account it found it in."""

    account: str
    """The account (integration:name)."""
    item: T
    """What it found."""


class AccountFailure(_Model):
    """An account an operation failed in."""

    account: str
    """The account (integration:name)."""
    error: ErrorInfo
    """Why."""


class FanOutResult[T](_Model):
    """What an operation that fans out found, in each account it ran on."""

    items: list[AccountItem[T]]
    """What it found, each with its account."""
    failures: list[AccountFailure] = Field(default_factory=list[AccountFailure])
    """The accounts it failed in, and why (the others' items still came back)."""
    next_pages: dict[str, str] = Field(default_factory=dict[str, str])
    """The next page of each account that has more (give it as the page, on that account)."""


def operations_of(plugin: type[Integration[Any, Any]]) -> list[Operation]:
    """The operations an integration adds: one for each of its marked methods. Raises
    PluginError for one that is not right."""
    return [_operation(plugin, spec) for spec in plugin.operation_specs()]


def _operation(plugin: type[Integration[Any, Any]], spec: IntegrationOperation) -> Operation:
    request = _request_model(plugin, spec)
    response: type[BaseModel] = FanOutResult[spec.item] if spec.fan_out else spec.response
    integration = plugin.name

    async def call(service: UDLService, given: BaseModel) -> BaseModel:
        return await _call(service, integration, spec, given)

    # (Operation.of reads an operation's models and description from its function.)
    call.__annotations__ = {"given": request, "return": response}
    call.__doc__ = inspect.getdoc(spec.method)
    call.__qualname__ = f"{plugin.__qualname__}.{getattr(spec.method, '__name__', spec.name)}"
    return Operation.of(
        call, name=spec.name, title=spec.title, hints=spec.hints, cli=spec.cli, api=spec.api
    )


def _request_model(
    plugin: type[Integration[Any, Any]], spec: IntegrationOperation
) -> type[BaseModel]:
    """The operation's request: the method's, with `account` in front (unless it needs none)."""
    if not spec.needs_account:
        return spec.request
    if "account" in spec.request.model_fields:
        raise PluginError(
            f"{plugin.__qualname__}'s {spec.name} request has a field called account: that is "
            "udl's (the account it runs on)"
        )
    example = f"e.g. work, or {plugin.name}:work"
    if spec.fan_out:
        annotation: Any = str | list[str] | None
        about = (
            f"The accounts to run on: one by name ({example}), a list of them, or * for every "
            "one; it may be left out while the integration has one account."
        )
    else:
        annotation = str | None
        about = (
            f"The account to use, by name ({example}); it may be left out while the integration "
            "has one account."
        )
    name = f"{_title(plugin.name)}{_title(spec.name)}Request"
    account: type[BaseModel] = create_model(
        f"{name}Account",
        __config__=ConfigDict(frozen=True, extra="forbid"),
        account=(annotation, Field(default=None, description=about)),
    )
    # (Its bases' fields come in reverse order of the bases: the account's first.)
    made: type[BaseModel] = create_model(
        name, __base__=(spec.request, account), __module__=spec.request.__module__
    )
    made.__doc__ = spec.request.__doc__
    return made


def _title(name: str) -> str:
    return "".join(part.capitalize() for part in name.replace("-", "_").split("_"))


# ---------------------------------------------------------------------------------------------
# Running one
# ---------------------------------------------------------------------------------------------


async def _call(
    service: UDLService, integration: str, spec: IntegrationOperation, given: BaseModel
) -> BaseModel:
    made = await service.integrations.get(integration)
    if not spec.needs_account:
        return await spec.method(made, given)
    plain = spec.request.model_construct(
        _fields_set=given.model_fields_set - {"account"},
        **{field: getattr(given, field) for field in spec.request.model_fields},
    )
    asked: str | list[str] | None = cast(Any, given).account  # (the request's own)
    accounts, many = await _selected(service, integration, spec, asked)
    if not spec.fan_out:
        return await _on(service, made, spec, accounts[0], plain)
    limit = asyncio.Semaphore(FAN_OUT)

    async def each(account: Account) -> BaseModel | UDLError:
        async with limit:
            try:
                return await _on(service, made, spec, account, plain)
            except UDLError as e:
                return e

    found = await asyncio.gather(*(each(account) for account in accounts))
    if not many and isinstance(found[0], UDLError):
        raise found[0]
    items: list[dict[str, Any]] = []
    failures: list[AccountFailure] = []
    next_pages: dict[str, str] = {}
    for account, answer in zip(accounts, found, strict=True):
        if isinstance(answer, UDLError):
            failures.append(AccountFailure(account=account.ref, error=answer.to_info()))
            continue
        page = cast(ItemsPage[Any], answer)
        items += [{"account": account.ref, "item": item} for item in page.items]
        if page.next_page is not None:
            next_pages[account.ref] = page.next_page
    return FanOutResult[spec.item].model_validate(
        {"items": items, "failures": failures, "next_pages": next_pages}
    )


async def _selected(
    service: UDLService, integration: str, spec: IntegrationOperation, asked: str | list[str] | None
) -> tuple[list[Account], bool]:
    """The accounts an operation runs on, and whether it was asked for several (so failures are
    listed rather than raised)."""
    every = await service.accounts.list(integration)
    connected = [account for account in every if account.connected_at is not None]
    usable = [account for account in connected if account.enabled]
    names = ", ".join(account.ref for account in usable)
    if asked is None or asked == EVERY or asked == []:
        if asked == EVERY and not spec.fan_out:
            raise ConfigError(
                f"{integration}_{spec.name} works on one account at a time",
                hint=f"Name it: {names}." if names else None,
            )
        if not usable:
            raise DoesNotExist(
                f"No {integration} account is connected",
                hint=f"`udl accounts add {integration} <name>` connects one.",
            )
        if asked is None and len(usable) > 1:
            raise AmbiguousAccount(
                f"Which {integration} account? There are {len(usable)}",
                hint=f"Name one (account): {names}"
                + (", a list of them, or * for every one." if spec.fan_out else "."),
            )
        return usable, asked is not None
    wanted = asked if isinstance(asked, list) else [asked]
    found: list[Account] = []
    for text in wanted:
        ref = AccountRef.parse(text, integration)
        account = next((account for account in connected if account.name == ref.name), None)
        if account is None:
            raise DoesNotExist(
                f"No {integration} account called {ref.name!r} is connected",
                hint=f"The {integration} accounts: {names}." if names else None,
            )
        if not account.enabled:
            raise NotAllowed(
                f"{account.ref} is disabled",
                hint=f"Enable it: `udl accounts set {account.ref} --enabled`.",
            )
        found.append(account)
    return found, isinstance(asked, list)


async def _on(
    service: UDLService,
    integration: Integration[Any, Any],
    spec: IntegrationOperation,
    account: Account,
    request: BaseModel,
) -> BaseModel:
    """Run the operation on one account."""
    if spec.access is not None and spec.access not in account.access:
        levels = ",".join(dict.fromkeys([*account.access, spec.access]))
        raise NotAllowed(
            f"{account.ref} was not connected for {spec.access}",
            hint="Connect it again for it: "
            f"`udl accounts add {account.integration} {account.name} --access {levels} "
            "--reconnect`.",
        )
    handle = await service.accounts.handle(account)
    try:
        return await spec.method(integration, handle, request)
    except ReauthRequired as e:
        await service.accounts.needs_reauth(account, e)
        raise
