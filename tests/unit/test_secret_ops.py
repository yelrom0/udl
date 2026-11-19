"""
----------------------
- test_secret_ops.py -
----------------------

The secrets operations: what the store is and what each resolver found (never a value), and
keeping 1Password's service account token in udl's own store (admin, and no MCP tool: an agent
never needs a person's token).
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.engine import connection, registry
from udl.config import DBSettings, OnePasswordSettings, SecretsSettings, Settings
from udl.core.jobs import InstanceLock
from udl.core.operations import OperationRegistry
from udl.core.service import UDLService
from udl.plugins.registry import SECRET_RESOLVERS
from udl.plugins.secrets.memory import MemorySecretStore
from udl.plugins.secrets.models import (
    GetSecretsStatusRequest,
    ResolverStatus,
    SecretsStatus,
    SetToken,
)
from udl.plugins.secrets.onepassword import TOKEN_REF, OnePasswordResolver
from udl.plugins.secrets.resolvers import ResolverHealth, SecretResolver

TOKEN = "ops_servicetoken_1234"  # noqa: S105 - (a test's)

HEALTH: list[ResolverHealth] = [ResolverHealth(ok=True, vaults=["vault-a"])]
"""What the checked resolver answers (a test sets it)."""


class Checked(SecretResolver):
    """A resolver whose check says what the test set HEALTH to."""

    scheme = "checked"

    async def resolve(self, reference: str) -> SecretStr:
        return SecretStr(f"resolved:{reference}")

    async def check(self) -> ResolverHealth:
        return HEALTH[0]


@pytest.fixture(autouse=True)
def health() -> None:
    """Every test starts with a resolver that works."""
    HEALTH[0] = ResolverHealth(ok=True, vaults=["vault-a"])


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    settings = Settings(
        download_dir=tmp_path / "downloads",
        db=DBSettings(connections=[connection(primary=True)]),
        secrets=SecretsSettings(store="memory"),
    )
    made = UDLService(
        settings,
        registry=registry(),
        secrets=MemorySecretStore(settings.secrets),
        lock=InstanceLock(tmp_path / "udl.lock"),
    )
    await made.start()
    try:
        yield made
    finally:
        await made.close()


# ---------------------------------------------------------------------------------------------
# What there is
# ---------------------------------------------------------------------------------------------


async def test_the_status_says_the_store_and_what_each_resolver_found(
    service: UDLService,
) -> None:
    service.registry.register(SECRET_RESOLVERS, Checked)

    found = await service.get_secrets_status(GetSecretsStatusRequest())

    assert found.store == "memory"
    assert found.unavailable is None
    assert [(resolver.name, resolver.ok, resolver.vaults) for resolver in found.resolvers] == [
        ("checked", True, ["vault-a"])
    ]


async def test_a_status_with_no_resolver_says_so(service: UDLService) -> None:
    found = await service.get_secrets_status(GetSecretsStatusRequest())

    assert found.resolvers == []


async def test_a_resolver_that_does_not_work_is_shown_with_what_is_wrong(
    service: UDLService,
) -> None:
    service.registry.register(SECRET_RESOLVERS, Checked)
    HEALTH[0] = ResolverHealth(ok=False, problems=["no token"])

    found = await service.get_secrets_status(GetSecretsStatusRequest())

    [resolver] = found.resolvers
    assert (resolver.ok, resolver.problems) == (False, ["no token"])


class TurnedOff(Checked):
    """A resolver that is off (as [secrets.onepassword] enabled = false is): nothing runs."""

    scheme = "off"

    @property
    def enabled(self) -> bool:
        return False

    async def check(self) -> ResolverHealth:
        raise AssertionError("a resolver that is off is not checked")


async def test_a_resolver_that_is_off_is_shown_as_off_and_not_checked(
    service: UDLService,
) -> None:
    service.registry.register(SECRET_RESOLVERS, TurnedOff)

    found = await service.get_secrets_status(GetSecretsStatusRequest())

    [resolver] = found.resolvers
    assert (resolver.name, resolver.enabled, resolver.ok, resolver.problems) == (
        "off",
        False,
        None,
        [],
    )


async def test_a_real_op_that_is_off_runs_nothing(service: UDLService) -> None:
    # (A read-only MCP tool must not run `op whoami` with the token when 1Password is off.)
    service.registry.register(SECRET_RESOLVERS, OnePasswordResolver)

    found = await service.get_secrets_status(GetSecretsStatusRequest())

    [resolver] = found.resolvers
    assert (resolver.enabled, resolver.ok, resolver.problems) == (False, None, [])


async def test_the_status_names_no_value(service: UDLService) -> None:
    service.registry.register(SECRET_RESOLVERS, Checked)
    await service.secrets.set(TOKEN_REF, SecretStr(TOKEN))

    found = await service.get_secrets_status(GetSecretsStatusRequest())

    assert TOKEN not in found.model_dump_json()


# ---------------------------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------------------------


async def test_the_token_is_kept_in_udl_s_own_store(service: UDLService) -> None:
    service.registry.register(SECRET_RESOLVERS, Checked)

    found = await service.set_onepassword_token(SetToken(token=SecretStr(TOKEN)))

    assert [resolver.ok for resolver in found.resolvers] == [True]
    kept = await service.secrets.get(TOKEN_REF)
    assert kept is not None
    assert kept.get_secret_value() == TOKEN


async def test_the_token_is_never_shown(service: UDLService) -> None:
    service.registry.register(SECRET_RESOLVERS, Checked)

    found = await service.set_onepassword_token(SetToken(token=SecretStr(TOKEN)))

    assert TOKEN not in repr(found)
    assert TOKEN not in found.model_dump_json()


def test_the_token_operation_is_admin_and_no_agent_s_tool() -> None:
    spec = OperationRegistry.core().get("set_onepassword_token")

    assert spec.hints.admin is True
    assert spec.hints.mcp is False
    assert spec.cli is not None
    assert spec.cli.path == ("secrets", "onepassword", "login")


def test_the_status_operation_is_read_only_and_asks_nothing() -> None:
    spec = OperationRegistry.core().get("get_secrets_status")

    assert spec.hints.read_only is True
    assert spec.hints.open_world is True
    assert spec.cli is not None
    assert spec.cli.path == ("secrets", "status")


def test_a_token_of_no_length_is_refused() -> None:
    with pytest.raises(ValueError, match="token"):
        SetToken(token=SecretStr(""))


# ---------------------------------------------------------------------------------------------
# The settings it reads
# ---------------------------------------------------------------------------------------------


def test_the_settings_table_is_the_card_s(service: UDLService) -> None:
    table = service.settings.secrets.onepassword

    assert isinstance(table, OnePasswordSettings)
    assert (table.enabled, table.vaults) == (False, [])
    assert table.cache_ttl == timedelta(minutes=5)
    assert table.timeout == timedelta(seconds=20)


def test_a_status_is_a_store_and_its_resolvers() -> None:
    found = SecretsStatus(
        store="keyring",
        unavailable="the keyring is locked",
        resolvers=[ResolverStatus(name="op", enabled=True, ok=True, vaults=["vault-a"])],
    )

    assert (found.store, found.resolvers[0].vaults) == ("keyring", ["vault-a"])
