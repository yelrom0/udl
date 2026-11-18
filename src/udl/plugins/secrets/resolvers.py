"""
----------------
- resolvers.py -
----------------

SecretResolver: a way to read a secret udl does not keep (a value `op://vault/item/field` is
read from 1Password when it is used, and never stored), and ResolvingStore: the store udl uses
when a resolver is enabled, which wraps the configured store and resolves such a value as it is
read.

A value that names a reference is kept as its text, so `secrets.json` and the keyring hold only
`op://vault/item/field`. Reading it asks the resolver, and keeps the answer for cache_ttl (on
the Clock), so a page of secrets is one call, not one per field.

A resolver only answers for the references its allow-list covers, and a value that no resolver
may read — another resolver's scheme, or a vault outside the allow-list — is refused when it is
saved, not when it is used.
"""

# System Imports
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, Final

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Local Imports
from udl.clock import Clock
from udl.config import Duration, SecretsSettings
from udl.errors import ConfigError
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.base import SecretRef, SecretStore

if TYPE_CHECKING:
    from udl.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

__all__ = [
    "ResolverHealth",
    "ResolvingStore",
    "SecretResolver",
    "open_resolvers",
    "open_secret_store",
]

_REFERENCE: Final = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://(.+)$")
"""A value that names a reference: its resolver's scheme, then the reference."""

CACHE_TTL: Final = timedelta(minutes=5)
"""How long a resolved value is kept, when the resolvers' settings do not say."""


class ResolverHealth(BaseModel):
    """Whether a resolver can be used, as checking it found."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    ok: bool
    """Whether it works."""
    problems: list[str] = Field(default_factory=list[str])
    """What is wrong with it."""
    vaults: list[str] = Field(default_factory=list[str])
    """What it may read (its allow-list, as the resolver knows it)."""


class SecretResolver(ABC):
    """A way to read a secret by reference (`op://vault/item/field`), when it is used.

    Made with udl's [secrets] settings, and published as a plugin of the udl.secret_resolvers
    kind, named after its scheme (`op`)."""

    scheme: ClassVar[str]  # e.g. "op": what names its references (`op://vault/item/field`)
    # What it needs installed (`udl plugins list` says what is missing).
    requires: ClassVar[tuple[Requirement, ...]] = ()
    name: ClassVar[str]  # its scheme: what its entry point is called (set by __init_subclass__)

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        cls.name = cls.scheme

    def __init__(self, settings: SecretsSettings, *, store: SecretStore | None = None) -> None:
        self.settings = settings
        self.store = store
        """udl's own store, where a resolver keeps what it needs (a token), never the reverse."""

    @property
    def enabled(self) -> bool:
        """Whether udl reads references through it: a resolver with a settings table of its own
        says so there (e.g. [secrets.onepassword] enabled), and one without is on when it is
        registered."""
        return True

    @property
    def cache_ttl(self) -> Duration:
        """How long a value it resolved is kept (the resolver's own setting, or the default)."""
        return CACHE_TTL

    def allows(self, reference: str) -> bool:
        """Whether this resolver may read `reference` (a reference without its scheme): its
        allow-list decides, so a vault udl was not told about is never read. By default any."""
        return True

    @abstractmethod
    async def resolve(self, reference: str) -> SecretStr:
        """The secret `reference` (without its scheme) names.

        Raises SecretStoreError when it cannot be read (a tool that failed, a sign-in that is
        needed), and ConfigError when udl may not read it."""

    @abstractmethod
    async def check(self) -> ResolverHealth:
        """Whether the resolver works (its tool, its sign-in, its vaults), reading no secret."""


def open_resolvers(
    plugins: PluginRegistry, settings: SecretsSettings, *, store: SecretStore
) -> list[SecretResolver]:
    """One of each resolver udl has, in the order of their names, each reading what it needs
    (a token) from `store` (udl's own store, never from itself)."""
    from udl.plugins.registry import SECRET_RESOLVERS

    return [
        plugins.get(SECRET_RESOLVERS, info.name)(settings, store=store)
        for info in sorted(plugins.plugins(SECRET_RESOLVERS), key=lambda found: found.name)
    ]


class ResolvingStore(SecretStore):
    """The store udl uses when a resolver is enabled: udl's configured store, with references
    resolved as they are read.

    A value that names a reference is kept as its text (so nothing else ever holds the secret),
    and reading it asks the resolver — keeping the answer for cache_ttl, on the clock."""

    name = "resolving"

    def __init__(
        self,
        wrapped: SecretStore,
        resolvers: list[SecretResolver],
        *,
        clock: Clock,
        cache_ttl: Duration = CACHE_TTL,
    ) -> None:
        super().__init__(wrapped.settings)
        self.wrapped = wrapped
        self.resolvers = list(resolvers)
        """The resolvers it reads references through (the ones that are enabled)."""
        self._clock = clock
        self.cache_ttl = cache_ttl
        """How long a resolved value is kept."""
        self._cache: dict[SecretRef, tuple[datetime, SecretStr | None]] = {}

    # -- references ----------------------------------------------------------------------------

    def _named(self, text: str) -> tuple[SecretResolver, str] | None:
        """(the resolver for `text`, the reference it names) when `text` is a reference, else
        None. Raises ConfigError for a reference no resolver answers for."""
        found = _REFERENCE.match(text)
        if found is None:
            return None
        scheme, reference = found[1], found[2]
        for resolver in self.resolvers:
            if resolver.scheme == scheme:
                return resolver, reference
        raise ConfigError(
            f"{text!r} is a {scheme} reference, and udl has no resolver for {scheme}",
            hint="Enable one in [secrets] (e.g. [secrets.onepassword] enabled = true).",
        )

    def _allowed(self, ref: SecretRef, text: str) -> None:
        """Raise ConfigError when no resolver may read `text`."""
        named = self._named(text)
        if named is not None and not named[0].allows(named[1]):
            resolver, _ = named
            raise ConfigError(
                f"{ref} is a {resolver.scheme} reference outside the allow-list, and is refused",
                hint=f"Add its vault to [secrets.{resolver.scheme}] vaults in the config.",
            )

    # -- the store -----------------------------------------------------------------------------

    async def get(self, ref: SecretRef) -> SecretStr | None:
        kept = self._cache.get(ref)
        if kept is not None and kept[0] > self._clock.now():
            return kept[1]
        found = await self._read(ref)
        self._cache[ref] = (self._clock.now() + self.cache_ttl, found)
        return found

    async def _read(self, ref: SecretRef) -> SecretStr | None:
        text = await self.wrapped.get(ref)
        if text is None:
            return None
        named = self._named(text.get_secret_value())
        if named is None:
            return text
        resolver, reference = named
        if not resolver.allows(reference):
            raise ConfigError(
                f"{ref} is a {resolver.scheme} reference outside the allow-list, and is refused",
                hint=f"Add its vault to [secrets.{resolver.scheme}] vaults in the config.",
            )
        return await resolver.resolve(reference)

    async def set(self, ref: SecretRef, value: SecretStr) -> None:
        self._allowed(ref, value.get_secret_value())
        self._cache.pop(ref, None)
        await self.wrapped.set(ref, value)

    async def delete(self, ref: SecretRef) -> bool:
        self._cache.pop(ref, None)
        return await self.wrapped.delete(ref)

    async def delete_prefix(self, prefix: SecretRef) -> int:
        for ref in [ref for ref in self._cache if ref.within(prefix)]:
            del self._cache[ref]
        return await self.wrapped.delete_prefix(prefix)


def open_secret_store(
    settings: SecretsSettings, plugins: PluginRegistry, *, clock: Clock
) -> SecretStore:
    """The secret store udl uses: the one [secrets] store names, wrapped to resolve references
    when a resolver is enabled (see ResolvingStore)."""
    from udl.plugins.secrets.base import open_secret_store as opened

    store = opened(settings, plugins)
    found = [
        resolver for resolver in open_resolvers(plugins, settings, store=store) if resolver.enabled
    ]
    if not found:
        return store
    return ResolvingStore(
        store, found, clock=clock, cache_ttl=min(resolver.cache_ttl for resolver in found)
    )
