"""
-------------------
- test_onepassword.py -
-------------------

The onepassword resolver: a secret udl does not keep is read from 1Password with the `op` CLI
when it is used. The service account's token goes in the child's environment only (never in its
argv, never in a log line), `op` is run as argv (no shell), and only the vaults that
[secrets.onepassword] allows are read. A test's `op` is a shell script on PATH, as the
downloader tests fake their tools.
"""

# System Imports
import os
from datetime import timedelta
from pathlib import Path
from typing import Final

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from udl.config import OnePasswordSettings, SecretsSettings
from udl.errors import ConfigError, SecretStoreError
from udl.plugins.secrets.base import SecretRef
from udl.plugins.secrets.memory import MemorySecretStore
from udl.plugins.secrets.onepassword import TOKEN_REF, OnePasswordResolver

TOKEN: Final = "ops_servicetoken_1234"  # noqa: S105 - (a test's)
VALUE: Final = "s3cret-Value_42"
REFERENCE: Final = "vault-a/item-1/field-1"


def fake_op_script(args_log: Path, env_log: Path) -> str:
    """An `op` that answers as 1Password's does: `read` prints the value for an item, `whoami` and
    `vault list` print JSON, and anything else is a usage error. Its arguments and its environment
    are logged (one a line each), so a test can see what udl gave it."""
    return f"""#!/bin/sh
printf '%s\\n' "$@" > {args_log}
env > {env_log}
case "$1" in
    read)
        case "$3" in
            op://vault-a/*) printf '%s' '{VALUE}' ;;
            op://empty/*) printf '' ;;
            *) echo "not found" >&2; exit 1 ;;
        esac
        ;;
    whoami) printf '%s' '{{"url": "my.1password.com", "email": "dev@example.com"}}' ;;
    vault) printf '%s' '[{{"id": "v1", "name": "vault-a"}}, {{"id": "v2", "name": "vault-b"}}]' ;;
    *) echo "op: usage error" >&2; exit 1 ;;
esac
"""


# An `op` that never answers (so a test can time it out).
SLOW_OP = """#!/bin/sh
exec sleep 30
"""


def fake_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, script: str | None = None) -> Path:
    """A fake `op` on PATH, logging its arguments and environment under tmp_path."""
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    program = folder / "op"
    program.write_text(script or fake_op_script(tmp_path / "args", tmp_path / "env"))
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    return tmp_path


def settings(
    *, vaults: list[str] | None = None, timeout: timedelta | None = None
) -> SecretsSettings:
    """The [secrets] settings with 1Password on, and the vaults it may read (vault-a, unless the
    test says which — `vaults=[]` is none)."""
    return SecretsSettings(
        store="memory",
        onepassword=OnePasswordSettings(
            enabled=True,
            vaults=["vault-a"] if vaults is None else vaults,
            timeout=timeout or timedelta(seconds=20),
        ),
    )


def resolver(found: SecretsSettings, store: MemorySecretStore | None = None) -> OnePasswordResolver:
    return OnePasswordResolver(found, store=store or MemorySecretStore(found))


async def with_token(found: SecretsSettings, token: str = TOKEN) -> MemorySecretStore:
    """udl's own store, with the service account's token in it."""
    store = MemorySecretStore(found)
    await store.set(TOKEN_REF, SecretStr(token))
    return store


# ---------------------------------------------------------------------------------------------
# Reading a secret
# ---------------------------------------------------------------------------------------------


async def test_a_secret_is_read_with_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = fake_op(tmp_path, monkeypatch)
    found = settings()

    value = await resolver(found, await with_token(found)).resolve(REFERENCE)

    assert value.get_secret_value() == VALUE
    assert (folder / "args").read_text().splitlines() == [
        "read",
        "--no-newline",
        f"op://{REFERENCE}",
    ]


async def test_the_token_is_in_the_child_s_environment_and_never_in_its_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = fake_op(tmp_path, monkeypatch)
    found = settings()

    await resolver(found, await with_token(found)).resolve(REFERENCE)

    environment = (folder / "env").read_text()
    assert f"OP_SERVICE_ACCOUNT_TOKEN={TOKEN}" in environment
    assert TOKEN not in (folder / "args").read_text()


async def test_the_child_gets_the_home_it_needs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (op keeps its config under HOME: without it, a real op cannot find its account.)
    folder = fake_op(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", "/home/tester")
    found = settings()

    await resolver(found, await with_token(found)).resolve(REFERENCE)

    environment = (folder / "env").read_text().splitlines()
    assert "HOME=/home/tester" in environment
    assert f"PATH={os.environ['PATH']}" in environment


async def test_an_empty_field_is_an_empty_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_op(tmp_path, monkeypatch)
    found = settings(vaults=["empty"])

    value = await resolver(found, await with_token(found)).resolve("empty/item/field")

    assert value.get_secret_value() == ""


async def test_a_vault_udl_may_not_read_is_refused_without_running_op(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = fake_op(tmp_path, monkeypatch)
    found = settings(vaults=["vault-a"])

    with pytest.raises(ConfigError, match="vault udl may read") as raised:
        await resolver(found, await with_token(found)).resolve("vault-b/item-1/field-1")

    # (The hint names its config table, which is not its scheme: op's is [secrets.onepassword].)
    assert "[secrets.onepassword] vaults" in (raised.value.hint or "")
    assert not (folder / "args").exists()  # (op was never run)


async def test_with_no_vault_configured_no_vault_may_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (Vaults are allow-listed: nothing is read until one is named, and op is not run.)
    folder = fake_op(tmp_path, monkeypatch)
    found = settings(vaults=[])

    with pytest.raises(ConfigError, match="vault udl may read"):
        await resolver(found, await with_token(found)).resolve(REFERENCE)

    assert not (folder / "args").exists()


def test_only_the_vaults_the_settings_list_are_allowed() -> None:
    listed = OnePasswordResolver(settings(vaults=["vault-a"]))

    assert listed.allows("vault-a/item-1/field-1") is True
    assert listed.allows("vault-b/item-1/field-1") is False
    assert OnePasswordResolver(settings(vaults=[])).allows("vault-a/item-1/field-1") is False


async def test_a_vault_the_account_names_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_op(tmp_path, monkeypatch)
    found = settings(vaults=["vault-b", "vault-a"])

    value = await resolver(found, await with_token(found)).resolve(REFERENCE)

    assert value.get_secret_value() == VALUE


async def test_op_s_refusal_is_a_secret_store_error_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_op(tmp_path, monkeypatch)
    found = settings(vaults=["elsewhere"])

    with pytest.raises(SecretStoreError, match="not found") as raised:
        await resolver(found, await with_token(found)).resolve("elsewhere/item/field")

    assert raised.value.hint is not None


async def test_op_that_does_not_answer_in_time_is_a_secret_store_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_op(tmp_path, monkeypatch, script=SLOW_OP)
    found = settings(timeout=timedelta(milliseconds=50))

    with pytest.raises(SecretStoreError, match="did not answer in time"):
        await resolver(found, await with_token(found)).resolve(REFERENCE)


# ---------------------------------------------------------------------------------------------
# Checking it
# ---------------------------------------------------------------------------------------------


async def test_a_check_says_what_op_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_op(tmp_path, monkeypatch)
    found = settings()

    health = await resolver(found, await with_token(found)).check()

    assert health.ok is True
    assert health.problems == []
    assert health.vaults == ["vault-a", "vault-b"]


async def test_a_check_without_a_token_says_so_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = fake_op(tmp_path, monkeypatch)

    health = await resolver(settings()).check()

    assert health.ok is False
    assert health.problems == ["there is no service account token"]
    assert not (folder / "args").exists()


async def test_a_check_with_no_vault_allowed_says_so_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = fake_op(tmp_path, monkeypatch)
    found = settings(vaults=[])

    health = await resolver(found, await with_token(found)).check()

    assert (health.ok, health.vaults) == (False, [])
    assert health.problems == ["no vault is allowed: add one to [secrets.onepassword] vaults"]
    assert not (folder / "args").exists()


async def test_a_check_says_which_call_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_op(
        tmp_path,
        monkeypatch,
        script="""#!/bin/sh
case "$1" in
    whoami) printf '%s' '{}' ;;
    vault) echo "you are not signed in" >&2; exit 1 ;;
esac
""",
    )
    found = settings()

    health = await resolver(found, await with_token(found)).check()

    assert health.ok is False
    assert health.problems == ["op vault list exited with 1"]


async def test_a_vault_with_no_name_is_its_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_op(
        tmp_path,
        monkeypatch,
        script="""#!/bin/sh
case "$1" in
    whoami) printf '%s' '{}' ;;
    vault) printf '%s' '[{"id": "v1"}, {"id": "v2", "name": 7}, "v3", {"name": "vault-d"}]' ;;
esac
""",
    )
    found = settings()

    health = await resolver(found, await with_token(found)).check()

    assert health.vaults == ["v1", "v2", "vault-d"]


async def test_a_vault_list_that_is_not_json_is_no_vaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_op(
        tmp_path,
        monkeypatch,
        script="""#!/bin/sh
case "$1" in
    whoami) printf '%s' '{}' ;;
    vault) printf '%s' 'not json at all' ;;
esac
""",
    )
    found = settings()

    health = await resolver(found, await with_token(found)).check()

    assert health.ok is True
    assert health.vaults == []


# ---------------------------------------------------------------------------------------------
# What it is
# ---------------------------------------------------------------------------------------------


def test_it_is_the_op_scheme_and_needs_the_op_binary() -> None:
    assert OnePasswordResolver.scheme == "op"
    assert OnePasswordResolver.name == "op"
    assert [requirement.name for requirement in OnePasswordResolver.requires] == ["op"]


def test_it_is_enabled_only_where_its_settings_table_says_so() -> None:
    assert OnePasswordResolver(settings()).enabled is True
    assert OnePasswordResolver(SecretsSettings(store="memory")).enabled is False


def test_the_token_is_kept_under_secrets_onepassword_token() -> None:
    assert str(TOKEN_REF) == "secrets/onepassword/token"
    assert isinstance(SecretRef.of("secrets", "onepassword", "token"), SecretRef)
