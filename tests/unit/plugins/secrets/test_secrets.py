"""
------------------
- test_secrets.py -
------------------

Secret stores: references to secrets, the file and keyring stores' own behaviour, and which
store udl uses ([secrets] store)
"""

# System Imports
import asyncio
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
from pydantic import BaseModel, SecretStr, ValidationError

# Local Imports
from support.engine import registry as fake_registry
from support.engine import settings as fake_settings
from support.secrets import LockedKeyring, MemoryKeyring
from udl.config import SecretsSettings, Settings, data_dir
from udl.core.service import UDLService
from udl.errors import ConfigError, PluginError, SecretStoreError
from udl.plugins.registry import SECRETS, PluginRegistry
from udl.plugins.secrets.base import (
    KeptSecret,
    SecretField,
    SecretRef,
    load_secrets,
    open_secret_store,
    store_secrets,
)
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


async def test_two_file_stores_on_one_file_keep_each_other_s_secrets(tmp_path: Path) -> None:
    # (As two udls do: each rewrites the whole file, under the lock beside it.)
    settings = SecretsSettings(store="file", file=tmp_path / "secrets.json")
    first, second = FileSecretStore(settings), FileSecretStore(settings)

    await asyncio.gather(
        *(
            (first if n % 2 else second).set(SecretRef(f"many/{n}"), SecretStr(str(n)))
            for n in range(20)
        )
    )

    assert json.loads(settings.file.read_text()) == {f"many/{n}": str(n) for n in range(20)}


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

    assert backend.passwords == {("udl", "a/b"): "s3cret", ("udl", "#refs"): '["a/b"]'}


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
    assert isinstance(open_secret_store(SecretsSettings(), plugins()), KeyringSecretStore)


def test_auto_is_the_file_without_a_keyring_backend() -> None:
    keyring.set_keyring(fail.Keyring())

    assert isinstance(open_secret_store(SecretsSettings(), plugins()), FileSecretStore)


def test_a_store_can_be_named(tmp_path: Path) -> None:
    memory = open_secret_store(SecretsSettings(store="memory"), plugins())
    file = open_secret_store(SecretsSettings(store="file", file=tmp_path / "s.json"), plugins())

    assert isinstance(memory, MemorySecretStore)
    assert isinstance(file, FileSecretStore)


def test_a_store_udl_does_not_have_is_a_plugin_error() -> None:
    with pytest.raises(PluginError, match="vault"):
        open_secret_store(SecretsSettings(store="vault"), plugins())


def test_a_named_store_that_cannot_be_used_is_a_config_error() -> None:
    keyring.set_keyring(fail.Keyring())

    with pytest.raises(ConfigError, match="keyring") as raised:
        open_secret_store(SecretsSettings(store="keyring"), plugins())

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


def test_the_memory_store_says_it_keeps_nothing(caplog: pytest.LogCaptureFixture) -> None:
    open_secret_store(SecretsSettings(store="memory"), plugins())

    assert "forgotten" in caplog.text


async def test_a_keyring_secret_is_deleted_with_its_path(backend: MemoryKeyring) -> None:
    store = KeyringSecretStore(SecretsSettings())
    await store.set(SecretRef("accounts/1/token"), SecretStr("a"))
    await store.set(SecretRef("accounts/2/token"), SecretStr("b"))

    assert await store.delete_prefix(SecretRef("accounts/1")) == 1

    # (Only udl's own secrets, and the list of them it keeps beside them.)
    assert set(backend.passwords) == {("udl", "accounts/2/token"), ("udl", "#refs")}


# ---------------------------------------------------------------------------------------------
# A model's secrets
# ---------------------------------------------------------------------------------------------


class Inner(BaseModel):
    key: SecretField | None = None


class Options(BaseModel):
    url: str
    token: SecretField
    spare: SecretField | None = None
    inner: Inner = Inner()


async def test_a_model_s_secrets_go_to_the_store_and_come_back() -> None:
    store = MemorySecretStore(SecretsSettings(store="memory"))
    options = Options.model_validate(
        {"url": "https://x", "token": "t0ken", "inner": {"key": "k3y"}}
    )

    kept = await store_secrets(options, store, SecretRef("actions/a1"))
    stored = Options.model_validate_json(kept.model_dump_json())
    loaded = await load_secrets(stored, store)

    assert kept.token == KeptSecret(secret=SecretRef("actions/a1/token"))
    assert "t0ken" not in kept.model_dump_json()
    assert "k3y" not in kept.model_dump_json()
    assert stored == kept  # (as a record keeps it)
    assert isinstance(loaded.token, SecretStr)
    assert loaded.token.get_secret_value() == "t0ken"
    assert loaded.inner.key == SecretStr("k3y")
    assert loaded.spare is None


async def test_a_model_whose_secrets_are_kept_already_is_left_as_it_is() -> None:
    store = MemorySecretStore(SecretsSettings(store="memory"))
    kept = Options(url="u", token=KeptSecret(secret=SecretRef("actions/a1/token")))

    assert await store_secrets(kept, store, SecretRef("actions/a1")) == kept


async def test_a_secret_the_store_lost_is_an_error_that_says_so() -> None:
    store = MemorySecretStore(SecretsSettings(store="memory"))
    kept = Options(url="u", token=KeptSecret(secret=SecretRef("actions/a1/token")))

    with pytest.raises(SecretStoreError, match="actions/a1/token"):
        await load_secrets(kept, store)


def test_a_plain_text_is_a_secret_and_a_kept_one_a_reference() -> None:
    assert isinstance(Options.model_validate({"url": "u", "token": "plain"}).token, SecretStr)
    assert Options.model_validate({"url": "u", "token": {"secret": "a/b"}}).token == KeptSecret(
        secret=SecretRef("a/b")
    )
