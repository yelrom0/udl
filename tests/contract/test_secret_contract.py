"""
---------------------------
- test_secret_contract.py -
---------------------------

The behaviour every secret store (udl.secrets plugins) must have: secrets by reference, kept
where only udl reads them, and never shown (in a repr, or a log line). Each test runs once per
store in STORES; a new store starts by adding itself there.
"""

# System Imports
import asyncio
import logging
from collections.abc import Callable, Iterator
from pathlib import Path

# Package Imports
import keyring
import pytest
from pydantic import SecretStr

# Local Imports
from support.secrets import MemoryKeyring
from udl.config import SecretsSettings
from udl.plugins.registry import SECRETS, PluginRegistry
from udl.plugins.secrets.base import SecretRef, SecretStore

SECRET = "s3cret-Value_42"  # noqa: S105 - (a test's)

# The settings each store is tried with.
STORES: dict[str, Callable[[Path], SecretsSettings]] = {
    "memory": lambda _: SecretsSettings(store="memory"),
    "file": lambda tmp_path: SecretsSettings(store="file", file=tmp_path / "udl" / "secrets.json"),
    "keyring": lambda _: SecretsSettings(store="keyring", keyring_service="udl-test"),
}


@pytest.fixture
def backend() -> Iterator[MemoryKeyring]:
    """The keyring the keyring store uses (in memory, never the user's)."""
    found = MemoryKeyring()
    before = keyring.get_keyring()
    keyring.set_keyring(found)
    yield found
    keyring.set_keyring(before)


@pytest.fixture(params=sorted(STORES))
def settings(
    request: pytest.FixtureRequest, tmp_path: Path, backend: MemoryKeyring
) -> SecretsSettings:
    return STORES[request.param](tmp_path)


@pytest.fixture
def store(settings: SecretsSettings) -> SecretStore:
    """A store of each kind, found the way udl finds it (by entry point)."""
    return PluginRegistry.discover().get(SECRETS, settings.store)(settings)


def ref(path: str = "accounts/1234/refresh_token") -> SecretRef:
    return SecretRef(path)


async def test_an_unknown_secret_is_none(store: SecretStore) -> None:
    assert await store.get(ref()) is None


async def test_a_secret_round_trips(store: SecretStore) -> None:
    await store.set(ref(), SecretStr(SECRET))

    found = await store.get(ref())

    assert found is not None
    assert found.get_secret_value() == SECRET


@pytest.mark.parametrize("value", ["", "two\nlines", "ünïcødé ✓", "x" * 10_000, '{"json": true}'])
async def test_any_text_is_a_secret(store: SecretStore, value: str) -> None:
    await store.set(ref(), SecretStr(value))

    found = await store.get(ref())

    assert found is not None
    assert found.get_secret_value() == value


async def test_setting_a_secret_again_replaces_it(store: SecretStore) -> None:
    await store.set(ref(), SecretStr("old"))
    await store.set(ref(), SecretStr("new"))

    found = await store.get(ref())

    assert found is not None
    assert found.get_secret_value() == "new"


async def test_secrets_are_kept_apart_by_reference(store: SecretStore) -> None:
    await store.set(ref("accounts/1/token"), SecretStr("one"))
    await store.set(ref("accounts/2/token"), SecretStr("two"))

    one, two = await store.get(ref("accounts/1/token")), await store.get(ref("accounts/2/token"))

    assert one is not None
    assert two is not None
    assert (one.get_secret_value(), two.get_secret_value()) == ("one", "two")


async def test_deleting_a_secret(store: SecretStore) -> None:
    await store.set(ref(), SecretStr(SECRET))
    await store.set(ref("kept"), SecretStr("kept"))

    assert await store.delete(ref()) is True
    assert await store.delete(ref()) is False

    assert await store.get(ref()) is None
    assert await store.get(ref("kept")) is not None


async def test_secrets_set_at_once_are_all_kept(store: SecretStore) -> None:
    await asyncio.gather(*(store.set(ref(f"many/{n}"), SecretStr(str(n))) for n in range(20)))

    found = [await store.get(ref(f"many/{n}")) for n in range(20)]

    assert [secret.get_secret_value() if secret else None for secret in found] == [
        str(n) for n in range(20)
    ]


async def test_a_secret_is_never_shown(
    store: SecretStore, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    await store.set(ref(), SecretStr(SECRET))
    found = await store.get(ref())
    await store.delete(ref())

    assert SECRET not in repr(store)
    assert SECRET not in repr(found)
    assert SECRET not in caplog.text


async def test_a_store_can_be_used(store: SecretStore) -> None:
    assert store.unavailable() is None
