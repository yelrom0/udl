"""
-----------
- base.py -
-----------

SecretStore: the interface every secret store implements, and SecretRef, how a secret is named.

A secret is named by a path (e.g. `accounts/<account id>/refresh_token`), which a record keeps
in place of the secret. Values are SecretStr, so they are never shown by accident (in a repr, a
log line or an answer). tests/contract/test_secret_contract.py is the behaviour every store must
have.
"""

# System Imports
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Self

# Package Imports
from pydantic import ConfigDict, RootModel, SecretStr, StringConstraints

# Local Imports
from udl.config import SecretsSettings
from udl.errors import ConfigError
from udl.plugins.requirements import Requirement, unmet

if TYPE_CHECKING:
    from udl.plugins.registry import PluginRegistry

# A part of a reference: letters, digits, `.`, `_` and `-`, not starting with a dot (so never `.`
# or `..`).
_PART: Final = r"[A-Za-z0-9_-][A-Za-z0-9._-]*"


class SecretRef(RootModel[Annotated[str, StringConstraints(pattern=rf"^{_PART}(/{_PART})*$")]]):
    """What names a secret: a path of plain parts, e.g. `accounts/<account id>/refresh_token`."""

    model_config = ConfigDict(frozen=True)

    @classmethod
    def of(cls, *parts: object) -> Self:
        """The reference made of `parts`, e.g. of("accounts", account.id, "refresh_token")."""
        return cls("/".join(str(part) for part in parts))

    def __str__(self) -> str:
        return self.root


class SecretStore(ABC):
    """Where secrets are kept, by reference."""

    name: ClassVar[str]  # what [secrets] store names it by, e.g. "keyring"
    # What it needs installed (`udl plugins list` says what is missing).
    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(self, settings: SecretsSettings) -> None:
        self.settings = settings

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"

    def unavailable(self) -> str | None:
        """Why the store cannot keep secrets on this machine (e.g. there is no keyring), or None
        when it can."""
        return None

    @abstractmethod
    async def get(self, ref: SecretRef) -> SecretStr | None:
        """The secret `ref` names, if it is kept. Raises SecretStoreError when the store fails."""

    @abstractmethod
    async def set(self, ref: SecretRef, value: SecretStr) -> None:
        """Keep `value` as the secret `ref` names (replacing any). Raises SecretStoreError."""

    @abstractmethod
    async def delete(self, ref: SecretRef) -> bool:
        """Forget the secret `ref` names. Returns whether there was one."""


_AUTO: Final = "auto"


def secret_store(settings: SecretsSettings, plugins: PluginRegistry) -> SecretStore:
    """The secret store [secrets] store names: `auto` is the keyring when there is one, else the
    file. Raises PluginError for a store udl does not have, and ConfigError for one that cannot
    be used here."""
    from udl.plugins.registry import SECRETS  # (the registry imports this module)

    if settings.store == _AUTO:
        keyring = plugins.get(SECRETS, "keyring")(settings)
        if not (unmet(keyring.requires) or keyring.unavailable()):
            return keyring
        return plugins.get(SECRETS, "file")(settings)
    store = plugins.get(SECRETS, settings.store)(settings)
    missing = unmet(store.requires)
    reason = (
        f"it needs {', '.join(requirement.name for requirement in missing)}"
        if missing
        else store.unavailable()
    )
    if reason is not None:
        raise ConfigError(
            f"The {settings.store} secret store cannot be used here: {reason}",
            hint='Set [secrets] store = "auto" (the keyring when there is one, else a file) '
            "in the config.",
        )
    return store
