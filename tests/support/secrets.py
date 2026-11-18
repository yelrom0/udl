"""
--------------
- secrets.py -
--------------

A keyring backend that lives in memory, so the keyring secret store is tested without touching
the user's real keyring (tests/conftest.py gives every other test a keyring that has none)
"""

# System Imports
from typing import ClassVar

# Package Imports
from keyring.backend import KeyringBackend
from keyring.errors import KeyringLocked, PasswordDeleteError
from pydantic import SecretStr

# Local Imports
from udl.plugins.secrets.resolvers import ResolverHealth, SecretResolver


class MemoryKeyring(KeyringBackend):
    """Passwords by service and username, in memory."""

    priority: ClassVar[float] = 1  # pyright: ignore[reportIncompatibleVariableOverride]

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.passwords.pop((service, username), None) is None:
            raise PasswordDeleteError("not found")


class LockedKeyring(MemoryKeyring):
    """A keyring that refuses (e.g. the user did not unlock it)."""

    def get_password(self, service: str, username: str) -> str | None:
        raise KeyringLocked("the keyring is locked")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeyringLocked("the keyring is locked")

    def delete_password(self, service: str, username: str) -> None:
        raise KeyringLocked("the keyring is locked")


class FakeResolver(SecretResolver):
    """A resolver that knows every reference of its scheme: `fake://<anything>` resolves to
    `resolved:<anything>`, so a test of the store that resolves needs no `op`."""

    scheme = "fake"

    def allows(self, reference: str) -> bool:
        """Whether it may read `reference`: any but the ones called "outside"."""
        return not reference.startswith("outside/")

    async def resolve(self, reference: str) -> SecretStr:
        return SecretStr(f"resolved:{reference}")

    async def check(self) -> ResolverHealth:
        return ResolverHealth(ok=True, vaults=["any"])
