"""
-------------------------------
- test_secret_references.py -
-------------------------------

A secret udl does not keep, end to end: a secret udl is given (a webhook's Authorization, the
way an AI provider's api_key will be) as a 1Password reference (`op://vault/item/field`) works —
udl reads it with `op` when it uses it — while the store udl writes holds only that text. This
is 25.1's Done-when: `secrets.json` (or the keyring) never holds the secret itself.
"""

# System Imports
from pathlib import Path
from typing import Final

# Package Imports
import pytest
from pydantic import SecretStr
from pytest_httpserver import HTTPServer
from werkzeug.wrappers import Request, Response

# Local Imports
from support.clock import FakeClock
from support.engine import registry
from support.interfaces import Harness
from udl.automations.models import RunActionRequest, SetActionRequest
from udl.config import OnePasswordSettings, SecretsSettings
from udl.errors import ConfigError
from udl.plugins.actions.webhook import WebhookAction
from udl.plugins.registry import ACTIONS, SECRET_RESOLVERS
from udl.plugins.secrets.base import SecretRef
from udl.plugins.secrets.onepassword import OnePasswordResolver
from udl.plugins.secrets.resolvers import ResolvingStore, open_secret_store

VALUE: Final = "Bearer the-real-token-42"
REFERENCE: Final = "op://vault-a/webhook/token"
STORE_REF: Final = SecretRef.of("actions", 1, "secret_headers", "Authorization")

FAKE_OP = """#!/bin/sh
case "$1" in
    read) printf '%s' '{value}' ;;
    whoami) printf '%s' '{{}}' ;;
    vault) printf '%s' '[{{"id": "v1", "name": "vault-a"}}]' ;;
esac
"""


@pytest.fixture
def op_on_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake `op` on PATH, which reads every reference as VALUE."""
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    program = folder / "op"
    program.write_text(FAKE_OP.format(value=VALUE))
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    return folder


def secrets_settings(*, vaults: list[str] | None = None) -> SecretsSettings:
    """The [secrets] settings with the memory store and 1Password on."""
    return SecretsSettings(
        store="memory",
        onepassword=OnePasswordSettings(enabled=True, vaults=vaults or ["vault-a"]),
    )


def resolving(found: SecretsSettings) -> ResolvingStore:
    """The store udl uses: the memory store, wrapped to read `op://` references."""
    plugins = registry()
    plugins.register(SECRET_RESOLVERS, OnePasswordResolver)
    store = open_secret_store(found, plugins, clock=FakeClock())
    assert isinstance(store, ResolvingStore)
    return store


@pytest.fixture
def harness(tmp_path: Path, op_on_path: Path) -> Harness:
    """A harness whose service keeps its secrets in the resolving store."""
    made = Harness(tmp_path, secrets=secrets_settings())
    made.registry.register(SECRET_RESOLVERS, OnePasswordResolver)
    made.registry.register(ACTIONS, WebhookAction)
    opened = open_secret_store(made.settings.secrets, made.registry, clock=FakeClock())
    assert isinstance(opened, ResolvingStore)  # (what udl opens when a resolver is enabled)
    made.secrets = opened
    return made


# ---------------------------------------------------------------------------------------------
# The store udl writes
# ---------------------------------------------------------------------------------------------


async def test_a_reference_is_resolved_when_it_is_read(op_on_path: Path) -> None:
    store = resolving(secrets_settings())

    await store.set(STORE_REF, SecretStr(REFERENCE))

    assert (await store.get(STORE_REF)).get_secret_value() == VALUE
    kept = await store.wrapped.get(STORE_REF)
    assert kept is not None
    assert kept.get_secret_value() == REFERENCE  # (only the text is written)


async def test_a_vault_outside_the_allow_list_is_refused_when_it_is_saved(
    op_on_path: Path,
) -> None:
    store = resolving(secrets_settings(vaults=["vault-b"]))

    with pytest.raises(ConfigError, match="refused"):
        await store.set(STORE_REF, SecretStr(REFERENCE))

    assert await store.wrapped.get(STORE_REF) is None


async def test_a_plain_secret_is_kept_and_read_as_it_is(op_on_path: Path) -> None:
    store = resolving(secrets_settings())

    await store.set(STORE_REF, SecretStr(VALUE))

    assert (await store.get(STORE_REF)).get_secret_value() == VALUE
    kept = await store.wrapped.get(STORE_REF)
    assert kept is not None
    assert kept.get_secret_value() == VALUE


# ---------------------------------------------------------------------------------------------
# An action's secret header, end to end
# ---------------------------------------------------------------------------------------------


async def test_a_secret_header_that_is_a_reference_works_and_keeps_only_it(
    harness: Harness, httpserver: HTTPServer
) -> None:
    """25.1's Done-when: a secret given as `op://...` is read with op when the action runs, and
    the store holds only the reference."""
    seen: list[str] = []

    def answer(request: Request) -> Response:
        seen.append(request.headers.get("Authorization", ""))
        return Response("", status=204)

    httpserver.expect_request("/hook", "POST").respond_with_handler(answer)

    async with harness.service() as service:
        assert isinstance(service.secrets, ResolvingStore)
        saved = await service.set_action(
            SetActionRequest.model_validate(
                {
                    "name": "hook",
                    "plugin": "webhook",
                    "options": {
                        "url": httpserver.url_for("/hook"),
                        "secret_headers": {"Authorization": REFERENCE},
                    },
                }
            )
        )

        await service.run_action(RunActionRequest(name="hook"))

        assert seen == [VALUE]
        kept = saved.options["secret_headers"]["Authorization"]
        assert isinstance(kept, dict)
        stored = await service.secrets.wrapped.get(SecretRef(str(kept["secret"])))
        assert stored is not None
        assert stored.get_secret_value() == REFERENCE
