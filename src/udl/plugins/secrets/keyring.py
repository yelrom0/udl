"""
--------------
- keyring.py -
--------------

A secret store in the system's keyring: the default, where there is one
"""

# System Imports
import asyncio
import contextlib
import fcntl
import json
import os
from collections.abc import Callable, Generator, Sequence
from pathlib import Path
from typing import Final

# Package Imports
from pydantic import SecretStr

# Local Imports
from udl.config import SecretsSettings
from udl.errors import SecretStoreError
from udl.plugins.requirements import Requirement

from .base import SecretRef, SecretStore

# Where the store keeps the list of its references (no reference has a #).
_REFS: Final = "#refs"


@contextlib.contextmanager
def _locked(path: Path) -> Generator[None]:
    """Hold an exclusive lock on the file at `path` (made, its owner's alone, if new)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


class KeyringSecretStore(SecretStore):
    """Secrets in the system's keyring (the Secret Service, such as GNOME Keyring or KeePassXC,
    or KWallet), through the keyring package, under the service [secrets] keyring_service
    ("udl"), each named by its reference. keyring is synchronous, so it runs in a thread.

    A keyring cannot list what it holds, so the store keeps the list of its references beside
    them (as `#refs`, which no reference can be), changed under a lock file beside the file
    store's, for delete_prefix()."""

    name = "keyring"
    requires = (Requirement.module("keyring", hint="Install keyring: `uv add keyring`."),)

    def unavailable(self) -> str | None:
        import keyring
        from keyring.backends import fail, null

        if isinstance(keyring.get_keyring(), fail.Keyring | null.Keyring):
            return "there is no keyring running here (such as GNOME Keyring or KWallet)"
        return None

    def __init__(self, settings: SecretsSettings) -> None:
        super().__init__(settings)
        self._listing = asyncio.Lock()  # (the lock file is per process)

    async def get(self, ref: SecretRef) -> SecretStr | None:
        import keyring

        value = await self._run(keyring.get_password, str(ref))
        return SecretStr(value) if value is not None else None

    async def set(self, ref: SecretRef, value: SecretStr) -> None:
        import keyring

        await self._run(keyring.set_password, str(ref), value.get_secret_value())
        await self._list(add=[str(ref)])

    async def delete(self, ref: SecretRef) -> bool:
        deleted = await self._delete(str(ref))
        await self._list(remove=[str(ref)])
        return deleted

    async def delete_prefix(self, prefix: SecretRef) -> int:
        under = [ref for ref in await self._refs() if SecretRef(ref).within(prefix)]
        deleted = [ref for ref in under if await self._delete(ref)]
        await self._list(remove=under)
        return len(deleted)

    async def _delete(self, ref: str) -> bool:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            await self._run(keyring.delete_password, ref, wanted=PasswordDeleteError)
        except PasswordDeleteError:
            return False
        return True

    async def _refs(self) -> list[str]:
        """The references the store holds (as its list of them says)."""
        import keyring

        listed = await self._run(keyring.get_password, _REFS)
        return [str(ref) for ref in json.loads(listed)] if listed else []

    async def _list(self, *, add: Sequence[str] = (), remove: Sequence[str] = ()) -> None:
        """Change the list of the store's references, under the lock."""
        import keyring

        async with self._listing:
            with _locked(self.settings.file.with_name("keyring.lock")):
                refs = dict.fromkeys(await self._refs())
                refs |= dict.fromkeys(add)
                for ref in remove:
                    refs.pop(ref, None)
                await self._run(keyring.set_password, _REFS, json.dumps(list(refs)))

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
