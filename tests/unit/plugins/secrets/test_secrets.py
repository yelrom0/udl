"""
------------------
- test_secrets.py -
------------------

Secret stores: references to secrets, the file and keyring stores' own behaviour, and which
store udl uses ([secrets] store)
"""

# System Imports
import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

# Package Imports
import keyring
import pytest
from keyring.backends import fail
from pydantic import SecretStr, ValidationError

# Local Imports
from support.engine import registry as fake_registry
from support.engine import settings as fake_settings
from support.secrets import LockedKeyring, MemoryKeyring
from udl.config import SecretsSettings, Settings, data_dir
from udl.core.service import UDLService
from udl.errors import ConfigError, PluginError, SecretStoreError
from udl.plugins.registry import SECRETS, PluginRegistry
from udl.plugins.secrets.base import SecretRef, secret_store
from udl.plugins.secrets.file import FileSecretStore
from udl.plugins.secrets.keyring import KeyringSecretStore
from udl.plugins.secrets.memory import MemorySecretStore


@pytest.fixture
def backend() -> Iterator[MemoryKeyring]:
    found = MemoryKeyring()
    before = keyring.get_keyring()
    keyring.set_keyring(found)
    yield found
    keyring.set_keyring(before)


def plugins() -> PluginRegistry:
    return PluginRegistry.discover()


def files(folder: Path) -> list[str]:
    return sorted(found.name for found in folder.iterdir())


# ---------------------------------------------------------------------------------------------
# References
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["token", "accounts/1234/refresh_token", "plugins/gmail/a.b-c"])
def test_a_reference_is_a_path(path: str) -> None:
    assert str(SecretRef(path)) == path


@pytest.mark.parametrize(
    "path", ["", "/token", "token/", "a//b", "a/../b", "..", "a/./b", "two words", "a\\b", "é"]
)
def test_a_reference_is_a_plain_path_with_no_way_out(path: str) -> None:
    with pytest.raises(ValidationError):
        SecretRef(path)


def test_a_reference_is_made_of_parts() -> None:
    account = UUID(int=7)

    ref = SecretRef.of("accounts", account, "refresh_token")

    assert str(ref) == f"accounts/{account}/refresh_token"
    assert ref == SecretRef(f"accounts/{account}/refresh_token")
    assert hash(ref) == hash(SecretRef(str(ref)))


# ---------------------------------------------------------------------------------------------
# The file store
# ---------------------------------------------------------------------------------------------


async def test_the_file_store_s_file_is_its_owner_s_alone(tmp_path: Path) -> None:
    path = tmp_path / "udl" / "secrets.json"
    store = FileSecretStore(SecretsSettings(store="file", file=path))

    await store.set(SecretRef("token"), SecretStr("s3cret"))

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


async def test_the_file_store_keeps_secrets_for_the_next_udl(tmp_path: Path) -> None:
    settings = SecretsSettings(store="file", file=tmp_path / "secrets.json")
    await FileSecretStore(settings).set(SecretRef("token"), SecretStr("s3cret"))

    found = await FileSecretStore(settings).get(SecretRef("token"))

    assert found is not None
    assert found.get_secret_value() == "s3cret"
    assert json.loads(settings.file.read_text()) == {"token": "s3cret"}


async def test_the_file_store_makes_a_file_others_could_read_its_owner_s(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.write_text("{}")
    path.chmod(0o644)

    await FileSecretStore(SecretsSettings(store="file", file=path)).set(
        SecretRef("token"), SecretStr("s3cret")
    )

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"token": 1}'])
async def test_a_file_that_is_not_a_store_s_is_an_error_that_names_it(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "secrets.json"
    path.write_text(content)
    store = FileSecretStore(SecretsSettings(store="file", file=path))

    with pytest.raises(SecretStoreError, match=r"secrets\.json"):
        await store.get(SecretRef("token"))


async def test_a_file_store_that_cannot_be_read_is_an_error_that_names_it(tmp_path: Path) -> None:
    path = tmp_path / "secrets.json"
    path.mkdir()  # (not a file)
    store = FileSecretStore(SecretsSettings(store="file", file=path))

    with pytest.raises(SecretStoreError, match="Cannot read"):
        await store.get(SecretRef("token"))


async def test_a_file_store_that_cannot_be_written_is_an_error_that_names_it(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "udl"
    folder.mkdir(mode=0o500)  # (its owner cannot write in it)
    store = FileSecretStore(SecretsSettings(store="file", file=folder / "secrets.json"))

    with pytest.raises(SecretStoreError, match=r"Cannot write the secrets in .*secrets\.json"):
        await store.set(SecretRef("token"), SecretStr("s3cret"))


async def test_a_secret_that_cannot_be_written_leaves_the_file_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secrets.json"
    store = FileSecretStore(SecretsSettings(store="file", file=path))
    await store.set(SecretRef("kept"), SecretStr("old"))

    def full(descriptor: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", full)
    with pytest.raises(SecretStoreError, match="No space left"):
        await store.set(SecretRef("new"), SecretStr("new"))

    assert json.loads(path.read_text()) == {"kept": "old"}
    assert files(tmp_path) == ["secrets.json", "secrets.json.lock"]  # (no new file left)


def test_the_file_store_is_in_udl_s_data_dir_by_default() -> None:
    assert SecretsSettings().file == data_dir() / "secrets.json"


# ---------------------------------------------------------------------------------------------
# The keyring store
# ---------------------------------------------------------------------------------------------


async def test_the_keyring_store_keeps_secrets_under_udl_s_service(
    backend: MemoryKeyring,
) -> None:
    await KeyringSecretStore(SecretsSettings()).set(SecretRef("a/b"), SecretStr("s3cret"))

    assert backend.passwords == {("udl", "a/b"): "s3cret"}


def test_without_a_keyring_backend_the_keyring_store_says_so() -> None:
    keyring.set_keyring(fail.Keyring())

    reason = KeyringSecretStore(SecretsSettings()).unavailable()

    assert reason is not None
    assert "keyring" in reason.lower()


async def test_a_keyring_that_refuses_is_an_error_that_says_so(backend: MemoryKeyring) -> None:
    keyring.set_keyring(LockedKeyring())
    store = KeyringSecretStore(SecretsSettings())

    for attempt in (
        store.get(SecretRef("a")),
        store.set(SecretRef("a"), SecretStr("x")),
        store.delete(SecretRef("a")),
    ):
        with pytest.raises(SecretStoreError, match="locked") as raised:
            await attempt
        assert raised.value.hint is not None


# ---------------------------------------------------------------------------------------------
# Which store
# ---------------------------------------------------------------------------------------------


def test_auto_is_the_keyring_when_it_has_a_backend(backend: MemoryKeyring) -> None:
    assert isinstance(secret_store(SecretsSettings(), plugins()), KeyringSecretStore)


def test_auto_is_the_file_without_a_keyring_backend() -> None:
    keyring.set_keyring(fail.Keyring())

    assert isinstance(secret_store(SecretsSettings(), plugins()), FileSecretStore)


def test_a_store_can_be_named(tmp_path: Path) -> None:
    store = secret_store(SecretsSettings(store="memory"), plugins())

    assert isinstance(store, MemorySecretStore)


def test_a_store_udl_does_not_have_is_a_plugin_error() -> None:
    with pytest.raises(PluginError, match="vault"):
        secret_store(SecretsSettings(store="vault"), plugins())


def test_a_named_store_that_cannot_be_used_is_a_config_error() -> None:
    keyring.set_keyring(fail.Keyring())

    with pytest.raises(ConfigError, match="keyring") as raised:
        secret_store(SecretsSettings(store="keyring"), plugins())

    assert raised.value.hint is not None
    assert "[secrets]" in raised.value.hint


def test_the_stores_are_udl_s_own_plugins() -> None:
    assert {"keyring", "file", "memory"} <= {info.name for info in plugins().plugins(SECRETS)}


async def test_a_service_s_store_is_made_when_it_is_first_used(tmp_path: Path) -> None:
    settings = fake_settings(tmp_path, secrets=SecretsSettings(store="memory"))
    registry = fake_registry()
    for info in PluginRegistry.discover().plugins(SECRETS):
        registry.register(SECRETS, PluginRegistry.discover().get(SECRETS, info.name))
    service = UDLService(settings, registry=registry)

    store = service.secrets

    assert isinstance(store, MemorySecretStore)
    assert service.secrets is store


def test_the_settings_say_which_store() -> None:
    assert Settings(secrets={"store": "file"}).secrets.store == "file"  # pyright: ignore[reportArgumentType]
