"""
--------------
- keyring.py -
--------------

A secret store in the system's keyring: the default, where there is one
"""

# System Imports
import asyncio
from collections.abc import Callable

# Package Imports
from pydantic import SecretStr

# Local Imports
from udl.errors import SecretStoreError
from udl.plugins.requirements import Requirement

from .base import SecretRef, SecretStore


class KeyringSecretStore(SecretStore):
    """Secrets in the system's keyring (the Secret Service, such as GNOME Keyring or KeePassXC,
    or KWallet), through the keyring package, under the service [secrets] keyring_service
    ("udl"), each named by its reference. keyring is synchronous, so it runs in a thread."""

    name = "keyring"
    requires = (Requirement.module("keyring", hint="Install keyring: `uv add keyring`."),)

    def unavailable(self) -> str | None:
        import keyring
        from keyring.backends import fail, null

        if isinstance(keyring.get_keyring(), fail.Keyring | null.Keyring):
            return "there is no keyring running here (such as GNOME Keyring or KWallet)"
        return None

    async def get(self, ref: SecretRef) -> SecretStr | None:
        import keyring

        value = await self._run(keyring.get_password, str(ref))
        return SecretStr(value) if value is not None else None

    async def set(self, ref: SecretRef, value: SecretStr) -> None:
        import keyring

        await self._run(keyring.set_password, str(ref), value.get_secret_value())

    async def delete(self, ref: SecretRef) -> bool:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            await self._run(keyring.delete_password, str(ref), wanted=PasswordDeleteError)
        except PasswordDeleteError:
            return False
        return True

    async def _run[T](
        self,
        call: Callable[..., T],
        *args: str,
        wanted: type[Exception] | None = None,
    ) -> T:
        """`call(service, *args)` in a thread; the keyring's errors (but the `wanted` one) are
        SecretStoreErrors."""
        from keyring.errors import KeyringError

        try:
            return await asyncio.to_thread(call, self.settings.keyring_service, *args)
        except KeyringError as e:
            if wanted is not None and isinstance(e, wanted):
                raise
            raise SecretStoreError(
                f"The keyring refused: {e}",
                hint='Unlock the keyring, or set [secrets] store = "file" in the config.',
            ) from e
