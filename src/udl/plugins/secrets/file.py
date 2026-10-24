"""
-----------
- file.py -
-----------

A secret store in one file: for machines without a keyring
"""

# System Imports
import asyncio
import contextlib
import fcntl
import json
import os
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, cast

# Package Imports
from pydantic import SecretStr

# Local Imports
from udl.config import SecretsSettings
from udl.errors import SecretStoreError

from .base import SecretRef, SecretStore


class FileSecretStore(SecretStore):
    """Secrets in a JSON file ([secrets] file: by default secrets.json in udl's data dir) that
    only its owner can read (0600, in a 0700 folder when udl makes it). They are plain text,
    protected by the file's mode, as the browser profile's cookies are.

    Changes are written to a new file that then takes the old one's place, under a lock on a
    file beside it, so udls that change secrets at once lose none.
    """

    name = "file"

    def __init__(self, settings: SecretsSettings) -> None:
        super().__init__(settings)
        self._changing = asyncio.Lock()  # (the file lock is per process)

    @property
    def path(self) -> Path:
        return self.settings.file

    async def get(self, ref: SecretRef) -> SecretStr | None:
        value = (await asyncio.to_thread(self._read)).get(str(ref))
        return SecretStr(value) if value is not None else None

    async def set(self, ref: SecretRef, value: SecretStr) -> None:
        async with self._changing:
            await asyncio.to_thread(self._change, str(ref), value.get_secret_value())

    async def delete(self, ref: SecretRef) -> bool:
        async with self._changing:
            return await asyncio.to_thread(self._change, str(ref), None)

    def _read(self) -> dict[str, str]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeDecodeError) as e:
            raise SecretStoreError(f"Cannot read the secrets in {self.path}: {e}") from e
        try:
            found: Any = json.loads(text)
        except json.JSONDecodeError:
            found = None
        if not isinstance(found, dict) or not all(
            isinstance(value, str) for value in cast(dict[str, object], found).values()
        ):
            raise SecretStoreError(
                f"{self.path} is not a udl secrets file (a JSON object of text)",
                hint="Move it away and udl starts a new one; the accounts whose secrets it held "
                "then need connecting again.",
            )
        return cast(dict[str, str], found)

    def _change(self, key: str, value: str | None) -> bool:
        """Set (or, when `value` is None, delete) one secret. Returns whether it was kept."""
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self._locked():
                secrets = self._read()
                kept = key in secrets
                if value is not None:
                    secrets[key] = value
                elif kept:
                    del secrets[key]
                else:
                    return False
                self._write(secrets)
        except OSError as e:
            raise SecretStoreError(f"Cannot write the secrets in {self.path}: {e}") from e
        return kept

    @contextlib.contextmanager
    def _locked(self) -> Generator[None]:
        lock = self.path.with_name(f"{self.path.name}.lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)  # (which releases the lock)

    def _write(self, secrets: dict[str, str]) -> None:
        """Write `secrets` to a new file (its owner's alone), which then replaces the file."""
        new = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}")
        descriptor = os.open(new, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(secrets, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            new.replace(self.path)
        except BaseException:
            new.unlink(missing_ok=True)
            raise
