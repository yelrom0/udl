"""
------------------
- onepassword.py -
------------------

OnePasswordResolver: reads a secret from 1Password when it is used, as `op read` says — the
reference is `op://<vault>/<item>/<field>`, and the value is never written to disk.

The service account's token is kept in udl's own store (at secrets/onepassword/token), given to
`op` in the child's environment only and never in its argv. `op` reads only the vaults udl was
allowed (`[secrets.onepassword] vaults`), and a reference to a vault outside that list is refused
when the value is saved, so a reference that widens what udl reads never reaches the store.
"""

# System Imports
import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Final

# Package Imports
from pydantic import JsonValue, SecretStr

# Local Imports
from udl.config import Duration, OnePasswordSettings
from udl.errors import ConfigError, SecretStoreError
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.base import SecretRef
from udl.plugins.secrets.resolvers import ResolverHealth, SecretResolver

if TYPE_CHECKING:
    from udl.process import ProcessResult

log = logging.getLogger(__name__)

__all__ = ["OnePasswordResolver"]

TOKEN_REF: Final = SecretRef.of("secrets", "onepassword", "token")
"""Where the service account's token is kept in udl's own store."""
TOKEN_ENV: Final = "OP_SERVICE_ACCOUNT_TOKEN"  # noqa: S105 - (a variable name, not a secret)
"""The variable `op` reads it from (the child's environment only, never its argv)."""


class OnePasswordResolver(SecretResolver):
    """Reads a secret from 1Password with the `op` CLI."""

    scheme = "op"
    requires = (Requirement.binary("op", hint="Install 1Password's CLI (op)."),)

    @property
    def table(self) -> OnePasswordSettings:
        """The [secrets.onepassword] table (what udl may read, and how long it keeps an answer)."""
        return self.settings.onepassword

    @property
    def enabled(self) -> bool:
        """Whether [secrets.onepassword] says udl reads references through it."""
        return self.table.enabled

    @property
    def cache_ttl(self) -> Duration:
        """How long a value `op` gave is kept."""
        return self.table.cache_ttl

    async def resolve(self, reference: str) -> SecretStr:
        """The secret `op://<vault>/<item>/<field>` names. Raises SecretStoreError when `op` is
        missing, has no token or cannot read it, and ConfigError for a vault udl may not read."""
        if not self.allows(reference):
            raise ConfigError(
                f"{reference!r} is not in a vault udl may read",
                hint="Add its vault to [secrets.onepassword] vaults in the config.",
            )
        token = await self._token()
        found = await _op(
            "read", "--no-newline", f"op://{reference}", token=token, within=self.table.timeout
        )
        if found.returncode != 0:
            reason = found.stderr.strip().splitlines()[-1] if found.stderr.strip() else ""
            raise SecretStoreError(
                f"op could not read {reference!r}" + (f": {reason}" if reason else ""),
                hint="Check the service account token (`udl secrets onepassword login`), and that "
                "the vault is one udl may read ([secrets.onepassword] vaults).",
            )
        return SecretStr(found.stdout)

    async def check(self) -> ResolverHealth:
        """Whether `op` works (a token, a sign-in) and which vaults it can read."""
        token = await self._token()
        if token is None:
            return ResolverHealth(ok=False, problems=["there is no service account token"])
        who = await _op("whoami", "--format", "json", token=token, within=self.table.timeout)
        vaults = await _op(
            "vault", "list", "--format", "json", token=token, within=self.table.timeout
        )
        problems = [
            f"{what} exited with {result.returncode}"
            for what, result in (("op whoami", who), ("op vault list", vaults))
            if result.returncode != 0
        ]
        named = [
            label
            for vault in _json(vaults.stdout)
            if isinstance(vault, dict)
            if (label := _label(vault)) is not None
        ]
        return ResolverHealth(ok=not problems, problems=problems, vaults=named)

    # -- what it may read ------------------------------------------------------------------------

    def allows(self, reference: str) -> bool:
        """Whether `reference`'s vault is one udl was allowed (its `vaults`; empty means any)."""
        allowed = list(self.table.vaults)
        return not allowed or reference.partition("/")[0] in allowed

    async def _token(self) -> SecretStr | None:
        """The service account's token, from udl's own store (None when there is none)."""
        store = self.store
        if store is None:
            return None
        return await store.get(TOKEN_REF)


async def _op(*args: str, token: SecretStr | None, within: Duration) -> ProcessResult:
    """`op` with `args`, its token in the child's environment only, run through udl.process
    (argv, no shell). Raises SecretStoreError when it does not answer within `within`, and
    ToolUnavailable when it is not installed."""
    from udl.process import CancelToken, run_process

    env = {"PATH": os.environ.get("PATH", os.defpath)}
    # (op keeps its config under HOME: without it, a real op cannot find its account.)
    if "HOME" in os.environ:
        env["HOME"] = os.environ["HOME"]
    if token is not None:
        env[TOKEN_ENV] = token.get_secret_value()
    try:
        async with asyncio.timeout(within.total_seconds()):
            return await run_process(
                ["op", *args],
                cancel=CancelToken(),
                env=env,
                max_output=64 * 1024,
            )
    except TimeoutError as e:  # (a program that does not answer)
        raise SecretStoreError(
            "op did not answer in time",
            hint=f"It gets {within}. Raise [secrets.onepassword] timeout, or check 1Password.",
        ) from e


def _label(vault: dict[str, JsonValue]) -> str | None:
    """A vault's name (or its id, when it has none, or a name that is not a name)."""
    for key in ("name", "id"):
        found = vault.get(key)
        if isinstance(found, str) and found:
            return found
    return None


def _json(text: str) -> list[JsonValue]:
    """`text` as a JSON list (an empty one when it is not)."""
    try:
        found: JsonValue = json.loads(text)
    except ValueError:
        return []
    return found if isinstance(found, list) else [found]
