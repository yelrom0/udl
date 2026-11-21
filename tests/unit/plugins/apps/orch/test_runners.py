"""
-------------------
- test_runners.py -
-------------------

Orch's harness operations (25.11, as orch offers them): `orch_runner_setup` writes a host's
policies and agent files, and gives the service the keys of the AI providers udl is set up with.

25.13's part is the keys: the `openai` and `openrouter` connections udl has (by their plugin,
not their name) send their keys as `OPENAI_API_KEY` and `OPENROUTER_API_KEY`, read the way the
provider reads them — a reference is read from the store or resolved (an `op://` reference goes
through the resolvers, O-D5), never sent as a reference; and a key udl has not been given is not
invented, because a harness signed in with a plan needs none.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final

# Package Imports
import httpx
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.engine import databases, registry, settings
from support.opencode import FakeOpenCode, FakeShell
from support.secrets import FakeResolver
from udl.clock import Clock, SystemClock
from udl.config import SecretsSettings
from udl.core.service import UDLService
from udl.db.manager import DBManager
from udl.hosts.shell import Host, HostShell
from udl.plugins.ai.base import AIProviderConnection
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.registry import RUNNERS, PluginRegistry
from udl.plugins.runners.manage import SetupRunnerRequest
from udl.plugins.runners.opencode import OpenCode, OpenCodeOptions
from udl.plugins.secrets.base import SecretRef, SecretStore
from udl.plugins.secrets.memory import MemorySecretStore
from udl.plugins.secrets.resolvers import ResolvingStore

OPENROUTER_REF: Final = "ai/providers/openrouter/api_key"
"""Where a connection's options hold OpenRouter's key."""
OPENAI_REF: Final = "ai/providers/openai/api_key"
"""Where a connection's options hold OpenAI's key."""
WORK_REF: Final = "ai/providers/work-openai/api_key"
"""A connection named for a project rather than the provider: its options say where its key is."""
REFERENCE: Final = "fake://vault-a/item-1/field-1"
"""A key that is a reference (what `op://…` would be): only the resolver may read it."""


async def connection(db: DBManager, *, name: str, plugin: str, ref: str) -> None:
    """An AI provider connection udl is set up with: its key kept at `ref`."""
    await db.upsert_record(
        AIProviderConnection(
            name=name,
            description="",
            plugin=plugin,
            options={"api_key": {"secret": ref}},
        )
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DBManager]:
    """A database with nothing in it (as tests/support/engine.py makes one), closed after."""
    made = databases(settings(tmp_path), registry())
    await made.connect()
    yield made
    await made.close()


@pytest.fixture
def app(db: DBManager) -> Orch:
    """Orch, with its records (its harnesses are the real manager's)."""
    return Orch(OrchSettings(), clock=SystemClock(), records=AppRecords(db))


async def store(*, kept: dict[str, str]) -> SecretStore:
    """A secret store with the keys a test kept (what a connection's options name)."""
    made = MemorySecretStore(SecretsSettings())
    for ref, value in kept.items():
        await made.set(SecretRef(ref), SecretStr(value))
    return made


async def resolving(*, kept: dict[str, str]) -> SecretStore:
    """The store udl uses when a resolver is enabled: the kept keys, with references resolved."""
    return ResolvingStore(
        await store(kept=kept), [FakeResolver(SecretsSettings())], clock=FakeClock()
    )


# ---------------------------------------------------------------------------------------------
# The keys a setup is given
# ---------------------------------------------------------------------------------------------


async def test_both_providers_keys_are_sent_when_both_are_set(app: Orch, db: DBManager) -> None:
    """Both providers' keys are sent when both connections have one (25.13's own case)."""
    await connection(db, name="or", plugin="openrouter", ref=OPENROUTER_REF)
    await connection(db, name="oa", plugin="openai", ref=OPENAI_REF)
    secrets = await store(kept={OPENROUTER_REF: "sk-or", OPENAI_REF: "sk-oai"})

    keys = await app.provider_keys(db, secrets)

    assert keys == {"OPENROUTER_API_KEY": "sk-or", "OPENAI_API_KEY": "sk-oai"}


async def test_one_key_is_sent_when_one_is_set(app: Orch, db: DBManager) -> None:
    """A harness signed in with a plan needs no key: one udl has not is not invented."""
    await connection(db, name="oa", plugin="openai", ref=OPENAI_REF)

    keys = await app.provider_keys(db, await store(kept={OPENAI_REF: "sk-oai"}))

    assert keys == {"OPENAI_API_KEY": "sk-oai"}


async def test_no_key_at_all_sends_nothing(app: Orch, db: DBManager) -> None:
    assert await app.provider_keys(db, await store(kept={})) == {}


async def test_a_connection_that_is_off_sends_nothing(app: Orch, db: DBManager) -> None:
    """A connection that is off is not used by udl at all, so its key is not set."""
    await db.upsert_record(
        AIProviderConnection(
            name="off",
            description="",
            plugin="openai",
            options={"api_key": {"secret": OPENAI_REF}},
            enabled=False,
        )
    )

    keys = await app.provider_keys(db, await store(kept={OPENAI_REF: "sk-oai"}))

    assert keys == {}


async def test_a_connection_of_another_provider_sends_nothing(app: Orch, db: DBManager) -> None:
    """A connection of a provider that takes no key (one udl runs itself) is not asked for one,
    however its options look."""
    await connection(db, name="local", plugin="ollama", ref=OPENAI_REF)

    keys = await app.provider_keys(db, await store(kept={OPENAI_REF: "sk-oai"}))

    assert keys == {}


async def test_a_connection_named_for_a_project_still_sends_its_key(
    app: Orch, db: DBManager
) -> None:
    """A connection is the user's name for it: what decides is the plugin it is, not the name."""
    await connection(db, name="work-openai", plugin="openai", ref=WORK_REF)

    keys = await app.provider_keys(db, await store(kept={WORK_REF: "sk-work"}))

    assert keys == {"OPENAI_API_KEY": "sk-work"}


async def test_a_key_that_is_a_reference_is_resolved_not_sent(app: Orch, db: DBManager) -> None:
    """An `op://` reference (here a resolver's own scheme) is read through the resolvers, so what
    the harness is given is the key, never the reference."""
    await connection(db, name="oa", plugin="openai", ref=WORK_REF)
    secrets = await resolving(kept={WORK_REF: REFERENCE})

    keys = await app.provider_keys(db, secrets)

    assert keys == {"OPENAI_API_KEY": "resolved:vault-a/item-1/field-1"}


async def test_a_connection_without_a_key_sends_nothing(app: Orch, db: DBManager) -> None:
    """A connection whose options hold no key sends none: the harness may be signed in with a
    plan, and a setup is not the place to fail about a provider udl is not using."""
    await db.upsert_record(
        AIProviderConnection(name="oa", description="", plugin="openai", options={})
    )

    assert await app.provider_keys(db, await store(kept={})) == {}


# ---------------------------------------------------------------------------------------------
# Through the harnesses: the keys reach the service's credential store, never an argv
# ---------------------------------------------------------------------------------------------


def _opencode_against(fake: FakeOpenCode) -> type[OpenCode]:
    """The real `opencode` runner, against a fake service (a test's transport, as
    tests/unit/plugins/runners/test_opencode.py gives it one)."""

    class Runner(OpenCode):
        def __init__(self, options: OpenCodeOptions, *, shell: HostShell, clock: Clock) -> None:
            super().__init__(
                options, shell=shell, clock=clock, transport=httpx.ASGITransport(app=fake.app)
            )

    return Runner


async def test_a_setup_gives_the_harness_the_keys_and_no_argv(tmp_path: Path) -> None:
    """`orch_runner_setup` end to end: the key udl keeps for a connection reaches the service's
    own credential store (`POST /api/credential`), and is in no command on the host."""
    fake = FakeOpenCode()
    shell = FakeShell()
    registry = PluginRegistry.discover(builtin_only=True)
    registry.register(RUNNERS, _opencode_against(fake))
    service = UDLService(
        settings(tmp_path),
        registry=registry,
        clock=FakeClock(),
        secrets=MemorySecretStore(SecretsSettings()),
    )

    def shell_for(found: Host) -> FakeShell:
        """Every host answers through the one fake shell."""
        return shell

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("udl.hosts.manager.shell_for", shell_for)
        async with service:
            await service.db.upsert_record(Host.local("/w"))
            await connection(service.db, name="oa", plugin="openai", ref=OPENAI_REF)
            await service.secrets.set(SecretRef(OPENAI_REF), SecretStr("sk-openai"))
            app = await service.apps.get("orch")
            assert isinstance(app, Orch)

            setup = await app.runner_setup(
                SetupRunnerRequest(host="local", runner="opencode"), service=service
            )

    assert setup.files[0].endswith("opencode.json")
    assert all("sk-openai" not in " ".join(argv) for argv in shell.runs)
    assert fake.credentials["udl-openai"]["value"] == {"type": "key", "key": "sk-openai"}
