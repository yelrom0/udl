"""
----------------
- test_base.py -
----------------

The integrations' base: the operations an integration's marked methods are (and what is wrong
with one that is not right), what an integration does by default (checking an account, revoking
its tokens), the Account record, and the public plugin API (all the built-in integrations import
from udl)
"""

# System Imports
import ast
import re
import types
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

# Local Imports
import udl
from support.clock import FakeClock
from support.integrations import (
    FakeAccountOptions,
    FakeIntegration,
    FakeSettings,
    FakeTokens,
    ListThingsRequest,
    StubHandle,
    Thing,
    http_to,
)
from support.oauth import BASE, FakeAuthorizationServer
from udl.errors import PluginError
from udl.integrations.oauth import Tokens
from udl.plugins.integrations.base import (
    Account,
    AccountHandle,
    AccountIdentity,
    ConnectMethod,
    Credentials,
    Integration,
    ItemsPage,
    OperationHints,
    integration_operation,
)

TESTS = Path(__file__).parents[3]

# What an integration may import from udl: the public plugin API.
PUBLIC_API = {
    "udl.plugins.integrations.base",
    "udl.integrations.oauth",
    "udl.automations.events",
    "udl.clock",
    "udl.http",
    "udl.process",
    "udl.errors",
    "udl.plugins.requirements",
}


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Ask(_Model):
    """Ask."""

    what: str = ""
    """What."""


class Answer(_Model):
    said: str


class Bare(Integration[FakeSettings, FakeAccountOptions]):
    """An integration that only says who an account is."""

    name = "bare"
    title = "Bare"
    connect = (ConnectMethod.NONE,)
    access_levels = ("read",)

    async def identify(self, credentials: Credentials, options: Any) -> AccountIdentity:
        return AccountIdentity(id="bare", display="Bare")


def integration_with(method: Any) -> type[Integration[Any, Any]]:
    """An integration whose only operation is `method`."""
    return type("With", (Bare,), {"op": method})


def bare(http: Any = None) -> Bare:
    return Bare(FakeSettings(), http=http or http_to(), clock=FakeClock())


def account(**fields: Any) -> Account:
    return Account.model_validate(
        {"integration": "fake", "name": "work", "method": "oauth", **fields}
    )


# ---------------------------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------------------------


def test_an_integration_s_operations_are_its_marked_methods_in_order() -> None:
    specs = FakeIntegration.operation_specs()

    assert [spec.name for spec in specs] == ["list_things", "do_thing", "version"]
    listing = specs[0]
    assert (listing.request, listing.response, listing.item) == (
        ListThingsRequest,
        ItemsPage[Thing],
        Thing,
    )
    assert (listing.access, listing.fan_out, listing.needs_account) == ("read", True, True)
    assert specs[2].needs_account is False


def test_an_operation_overridden_without_its_mark_is_none() -> None:
    class Quiet(FakeIntegration):
        async def do_thing(self, account: Any, request: Any) -> Any:  # (no longer one)
            return None

    assert [spec.name for spec in Quiet.operation_specs()] == ["list_things", "version"]


def test_an_integration_without_operations_has_none() -> None:
    assert Bare.operation_specs() == []


async def _no_doc(self: Any, account: AccountHandle[Any], request: Ask) -> Answer:
    return Answer(said="")


async def _no_models(self: Any, account: AccountHandle[Any], request: dict[str, str]) -> str:
    """Take and give no models."""
    return ""


async def _no_account(self: Any, request: Ask) -> Answer:
    """Take no account."""
    return Answer(said="")


async def _lists(self: Any, account: AccountHandle[Any], request: Ask) -> ItemsPage[Thing]:
    """List things."""
    return ItemsPage(items=[])


async def _answers(self: Any, account: AccountHandle[Any], request: Ask) -> Answer:
    """Answer."""
    return Answer(said=request.what)


def copy_of(method: Any) -> Any:
    """Another function that is `method` (so marking it leaves `method` as it is)."""
    copied = types.FunctionType(method.__code__, method.__globals__, method.__name__)
    copied.__annotations__ = dict(method.__annotations__)
    copied.__doc__ = method.__doc__
    copied.__qualname__ = method.__qualname__
    return copied


READ_ONLY = OperationHints(read_only=True)
_bare_page = copy_of(_lists)
_bare_page.__annotations__["return"] = ItemsPage  # (that says not what it holds)


@pytest.mark.parametrize(
    ("marked", "method", "problem"),
    [
        ({}, _no_doc, "has no docstring"),
        ({"name": "Bad Name"}, _answers, "lower case letters"),
        ({}, _no_models, "annotated as Pydantic models"),
        ({}, _no_account, "takes (self, account, request)"),
        ({"needs_account": False}, _answers, "takes (self, request)"),
        ({"fan_out": True}, _lists, "is read-only"),
        ({"fan_out": True, "hints": READ_ONLY}, _answers, "gives an ItemsPage[T]"),
        ({"fan_out": True, "hints": READ_ONLY}, _bare_page, "gives an ItemsPage[T]"),
        ({"access": "send"}, _answers, "not one of the bare integration's access_levels"),
    ],
)
def test_an_operation_that_is_not_right_is_a_plugin_error(
    marked: dict[str, Any], method: Any, problem: str
) -> None:
    name = marked.get("name", "x")
    others = {key: value for key, value in marked.items() if key != "name"}
    mark = integration_operation(name, title="X", **others)

    with pytest.raises(PluginError, match=re.escape(problem)):
        integration_with(mark(copy_of(method))).operation_specs()


# ---------------------------------------------------------------------------------------------
# What an integration does by default
# ---------------------------------------------------------------------------------------------


def test_an_integration_watches_when_it_implements_watch() -> None:
    assert FakeIntegration.watches()
    assert not Bare.watches()


def test_an_integration_has_no_event_types_unless_it_says() -> None:
    assert Bare.event_types() == ()


def test_an_integration_that_does_not_connect_with_oauth_has_no_oauth_request() -> None:
    with pytest.raises(PluginError, match="does not connect accounts with OAuth"):
        bare().oauth_request(frozenset())


async def test_an_integration_without_a_source_filter_cannot_check_one() -> None:
    handle = StubHandle(account(), FakeAccountOptions(), None)

    with pytest.raises(PluginError, match="has no source filter"):
        await bare().source_filter_matches(handle, None, Ask())  # type: ignore[arg-type]


async def test_checking_an_account_asks_for_its_credentials_and_says_its_scopes() -> None:
    tokens = Tokens(access_token=SecretStr("a"), scopes=["fake.read"])

    health = await bare().check(StubHandle(account(), FakeAccountOptions(), tokens))
    other = await bare().check(StubHandle(account(), FakeAccountOptions(), None))

    assert (health.ok, health.scopes) == (True, ["fake.read"])
    assert (other.ok, other.scopes) == (True, [])


async def test_an_oauth_account_s_tokens_are_revoked() -> None:
    server = FakeAuthorizationServer()
    server.refresh_tokens["refresh-1"] = "fake.read"
    fake = FakeIntegration(FakeSettings(), http=http_to((BASE, server.app())), clock=FakeClock())
    tokens = Tokens(access_token=SecretStr("a"), refresh_token=SecretStr("refresh-1"))

    assert await fake.revoke(account(access=["read"]), tokens)
    assert not await fake.revoke(account(method="tokens"), FakeTokens(api_key=SecretStr("k")))

    assert server.revoked == ["refresh-1"]  # (and nothing for the tokens the user gave)


async def test_an_account_s_tokens_are_its_oauth_tokens() -> None:
    tokens = Tokens(access_token=SecretStr("a"))

    assert await StubHandle(account(), FakeAccountOptions(), tokens).tokens() == tokens
    with pytest.raises(PluginError, match="was not connected with OAuth"):
        await StubHandle(account(method="none"), FakeAccountOptions(), None).tokens()


def test_an_account_is_called_integration_colon_name() -> None:
    assert account().ref == "fake:work"
    assert Account.example().ref == "gmail:work"


@pytest.mark.parametrize("name", ["", "has:colon", "has space", ".dot", "x" * 65])
def test_an_account_s_name_has_no_colon(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        account(name=name)


# ---------------------------------------------------------------------------------------------
# The public plugin API
# ---------------------------------------------------------------------------------------------


def _udl_imports(path: Path) -> set[str]:
    """The udl modules `path` imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return {name for name in found if name == "udl" or name.startswith("udl.")}


def _built_in_integrations() -> list[Path]:
    root = Path(udl.__file__).parent / "plugins" / "integrations"
    return sorted(path for path in root.rglob("*.py") if path.parent != root)


@pytest.mark.parametrize(
    "path",
    [*_built_in_integrations(), TESTS / "support" / "integrations.py"],
    ids=lambda path: path.name if path.parent.name == "support" else str(path.parent.name),
)
def test_an_integration_imports_only_the_public_plugin_api(path: Path) -> None:
    assert _udl_imports(path) <= PUBLIC_API
