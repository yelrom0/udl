"""
-------------------
- test_resolvers.py -
-------------------

Secret references: a value `op://vault/item/field` is stored as the reference text, resolved when
it is read, and never written to disk in place of the secret. A resolver says whether it may be
used (its scheme and its allow-list), and the store that wraps udl's keeps a resolved value for
cache_ttl (on the Clock), never longer
"""

# System Imports
from datetime import timedelta
from typing import ClassVar

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from udl.config import OnePasswordSettings, SecretsSettings
from udl.errors import ConfigError, SecretStoreError
from udl.plugins.registry import SECRET_RESOLVERS, SECRETS, PluginRegistry
from udl.plugins.secrets.base import SecretRef, SecretStore
from udl.plugins.secrets.memory import MemorySecretStore
from udl.plugins.secrets.onepassword import OnePasswordResolver
from udl.plugins.secrets.resolvers import (
    ResolverHealth,
    ResolvingStore,
    SecretResolver,
    open_secret_store,
)

OP = "op://vault-a/item-1/field-1"
OTHER = "op://vault-b/item-2/field-2"
VALUE = "s3cret-Value_42"


class FakeResolver(SecretResolver):
    """A resolver that knows two references and can be told to fail or to refuse one."""

    scheme = "fake"
    on: ClassVar[bool] = True  # (a subclass turns it off, as a settings table does)

    def __init__(
        self,
        settings: SecretsSettings,
        *,
        store: SecretStore | None = None,
        fails: bool = False,
    ) -> None:
        super().__init__(settings, store=store)
        self.fails = fails
        self.asked: list[str] = []

    def allows(self, reference: str) -> bool:
        """Whether it may read `reference` (the part after `fake://`)."""
        return reference.startswith("allowed/")

    @property
    def enabled(self) -> bool:
        """Whether udl reads references through it."""
        return self.on

    async def resolve(self, reference: str) -> SecretStr:
        self.asked.append(reference)
        if self.fails:
            raise SecretStoreError("the fake resolver is down")
        return SecretStr(f"resolved:{reference}")

    async def check(self) -> ResolverHealth:
        if self.fails:
            return ResolverHealth(ok=False, problems=["it is down"])
        return ResolverHealth(ok=True, vaults=["allowed"])


class OffResolver(FakeResolver):
    """A resolver that is registered but not enabled (as [secrets.onepassword] enabled = false
    is)."""

    on = False


def settings(**fields: object) -> SecretsSettings:
    return SecretsSettings(store="memory", **fields)  # pyright: ignore[reportArgumentType]


def resolvers(*found: SecretResolver) -> PluginRegistry:
    """A registry with `found` as the secret resolvers (and the memory store)."""
    registry = PluginRegistry()
    registry.register(SECRETS, MemorySecretStore)
    for resolver in found:
        registry.register(SECRET_RESOLVERS, type(resolver), origin=type(resolver).__qualname__)
    return registry


def resolving(*found: SecretResolver, clock: FakeClock | None = None) -> ResolvingStore:
    """A ResolvingStore over the memory store, with `found` as the resolvers."""
    base = MemorySecretStore(settings())
    return ResolvingStore(
        base, list(found), clock=clock or FakeClock(), cache_ttl=timedelta(minutes=5)
    )


# ---------------------------------------------------------------------------------------------
# What a resolver is
# ---------------------------------------------------------------------------------------------


def test_a_resolver_is_named_and_says_what_it_needs() -> None:
    assert FakeResolver.scheme == "fake"
    assert issubclass(FakeResolver, SecretResolver)
    assert FakeResolver.requires == ()


def test_a_resolver_is_registered_as_a_plugin_of_its_kind() -> None:
    registry = resolvers(FakeResolver(settings()))

    [info] = registry.plugins(SECRET_RESOLVERS)

    assert info.name == "fake"
    assert registry.get(SECRET_RESOLVERS, "fake") is FakeResolver


def test_a_resolver_is_named_after_its_scheme() -> None:
    class Named(SecretResolver):
        scheme = "op"

    assert Named.name == "op"  # (what its entry point is called, and its references' scheme)


# ---------------------------------------------------------------------------------------------
# A value that is a reference
# ---------------------------------------------------------------------------------------------


async def test_a_reference_is_kept_as_its_text() -> None:
    store = resolving(FakeResolver(settings()))

    await store.set(SecretRef("openrouter/key"), SecretStr("fake://allowed/item"))

    assert await store.get(SecretRef("openrouter/key")) == SecretStr("resolved:allowed/item")


async def test_a_plain_value_is_kept_as_it_is() -> None:
    store = resolving(FakeResolver(settings()))

    await store.set(SecretRef("openrouter/key"), SecretStr(VALUE))

    assert await store.get(SecretRef("openrouter/key")) == SecretStr(VALUE)


async def test_a_reference_outside_the_allow_list_is_refused_when_it_is_saved() -> None:
    store = resolving(FakeResolver(settings()))

    with pytest.raises(ConfigError, match="refused"):
        await store.set(SecretRef("openrouter/key"), SecretStr("fake://elsewhere/item"))


async def test_a_scheme_no_resolver_answers_for_is_a_value() -> None:
    # (Only a registered resolver's scheme is a reference: anything else is a value udl keeps.)
    store = resolving(FakeResolver(settings()))

    await store.set(SecretRef("openrouter/key"), SecretStr("nope://a/b"))

    assert await store.get(SecretRef("openrouter/key")) == SecretStr("nope://a/b")


async def test_a_resolver_is_asked_only_once_within_the_cache() -> None:
    clock = FakeClock()
    resolver = FakeResolver(settings())
    store = resolving(resolver, clock=clock)

    await store.set(SecretRef("key"), SecretStr("fake://allowed/item"))
    first = await store.get(SecretRef("key"))
    second = await store.get(SecretRef("key"))

    assert first == second
    assert resolver.asked == ["allowed/item"]  # (resolved once)


async def test_the_cache_expires_on_the_clock() -> None:
    clock = FakeClock()
    resolver = FakeResolver(settings())
    store = resolving(resolver, clock=clock)

    await store.set(SecretRef("key"), SecretStr("fake://allowed/item"))
    await store.get(SecretRef("key"))
    await clock.advance(timedelta(minutes=6))

    await store.get(SecretRef("key"))

    assert resolver.asked == ["allowed/item", "allowed/item"]


async def test_a_failing_resolver_is_a_secret_store_error() -> None:
    store = resolving(FakeResolver(settings(), fails=True))

    await store.set(SecretRef("key"), SecretStr("fake://allowed/item"))

    with pytest.raises(SecretStoreError, match="down"):
        await store.get(SecretRef("key"))


async def test_the_wrapped_store_holds_only_the_reference() -> None:
    resolver = FakeResolver(settings())
    store = resolving(resolver)

    await store.set(SecretRef("key"), SecretStr("fake://allowed/item"))

    assert await store.wrapped.get(SecretRef("key")) == SecretStr("fake://allowed/item")


async def test_deleting_forgets_the_reference_and_what_was_resolved() -> None:
    resolver = FakeResolver(settings())
    store = resolving(resolver)

    await store.set(SecretRef("key"), SecretStr("fake://allowed/item"))
    assert await store.delete(SecretRef("key")) is True

    assert await store.get(SecretRef("key")) is None


async def test_deleting_a_prefix_forgets_everything_under_it() -> None:
    store = resolving(FakeResolver(settings()))

    await store.set(SecretRef("one/key"), SecretStr("fake://allowed/a"))
    await store.set(SecretRef("two/key"), SecretStr("fake://allowed/b"))

    assert await store.delete_prefix(SecretRef("one")) == 1

    assert await store.get(SecretRef("one/key")) is None
    assert await store.get(SecretRef("two/key")) is not None


# ---------------------------------------------------------------------------------------------
# Which store udl opens
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "https://hooks.example.com/services/T000/B000/XXXX",
        "postgresql://udl:hunter2@db.example.com:5432/udl",
        "apprise://mailto:someone@example.com",
    ],
)
async def test_a_url_is_a_value_not_a_reference(value: str) -> None:
    # (A scheme no resolver answers for is part of a value: a webhook's URL is not a reference.)
    fake = FakeResolver(settings())
    store = resolving(fake, clock=FakeClock())
    ref = SecretRef.of("webhook", "service", "url")

    await store.set(ref, SecretStr(value))
    kept = await store.get(ref)

    assert kept is not None
    assert kept.get_secret_value() == value
    assert fake.asked == []


async def test_a_value_the_store_holds_is_read_again_each_time() -> None:
    # (The cache is for what a resolver answered: a token another process wrote — a CLI while the
    # server runs — is seen at once, not after cache_ttl.)
    fake = FakeResolver(settings())
    store = resolving(fake, clock=FakeClock())
    ref = SecretRef.of("webhook", "service", "url")
    await store.wrapped.set(ref, SecretStr("first"))

    first = await store.get(ref)
    await store.wrapped.set(ref, SecretStr("second"))
    second = await store.get(ref)

    assert (first, second) == (SecretStr("first"), SecretStr("second"))
    assert fake.asked == []


def test_the_wrapped_store_is_used_when_no_resolver_is_enabled() -> None:
    registry = resolvers()
    store = open_secret_store(settings(), registry, clock=FakeClock())

    assert not isinstance(store, ResolvingStore)


def test_a_resolver_that_is_off_does_not_wrap_the_store() -> None:
    registry = resolvers(OffResolver(settings()))

    found = open_secret_store(settings(), registry, clock=FakeClock())

    assert not isinstance(found, ResolvingStore)


def test_a_store_is_wrapped_when_a_resolver_is_enabled() -> None:
    registry = resolvers()
    registry.register(SECRET_RESOLVERS, OnePasswordResolver)

    off = open_secret_store(settings(), registry, clock=FakeClock())

    assert not isinstance(off, ResolvingStore)  # ([secrets.onepassword] enabled = false)

    on = open_secret_store(
        settings(onepassword=OnePasswordSettings(enabled=True)), registry, clock=FakeClock()
    )

    assert isinstance(on, ResolvingStore)
    assert [type(resolver) for resolver in on.resolvers] == [OnePasswordResolver]


def test_the_cache_is_kept_as_long_as_the_resolver_says() -> None:
    registry = resolvers()
    registry.register(SECRET_RESOLVERS, OnePasswordResolver)

    found = open_secret_store(
        settings(onepassword=OnePasswordSettings(enabled=True, cache_ttl=timedelta(minutes=1))),
        registry,
        clock=FakeClock(),
    )

    assert isinstance(found, ResolvingStore)
    assert found.cache_ttl == timedelta(minutes=1)
