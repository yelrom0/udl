"""
-------------
- memory.py -
-------------

A secret store that lives in memory: for tests, and for runs that should keep nothing
"""

# Package Imports
from pydantic import SecretStr

# Local Imports
from udl.config import SecretsSettings

from .base import SecretRef, SecretStore


class MemorySecretStore(SecretStore):
    """Secrets kept in memory and lost on exit. Each instance is a separate store."""

    name = "memory"

    def __init__(self, settings: SecretsSettings) -> None:
        super().__init__(settings)
        self._secrets: dict[SecretRef, SecretStr] = {}

    async def get(self, ref: SecretRef) -> SecretStr | None:
        return self._secrets.get(ref)

    async def set(self, ref: SecretRef, value: SecretStr) -> None:
        self._secrets[ref] = value

    async def delete(self, ref: SecretRef) -> bool:
        return self._secrets.pop(ref, None) is not None
