"""
-----------
- base.py -
-----------

SecretStore: the interface every secret store implements, and SecretRef, how a secret is named.

A secret is named by a path (e.g. `accounts/<account id>/refresh_token`), which a record keeps
in place of the secret. Values are SecretStr, so they are never shown by accident (in a repr, a
log line or an answer). tests/contract/test_secret_contract.py is the behaviour every store must
have.

A model that udl keeps (an action's options, an account's credentials...) types its secret
fields SecretField: store_secrets() moves each SecretStr in it into the store and leaves a
KeptSecret (`{"secret": "<ref>"}`) in its place, and load_secrets() brings them back.
"""

# System Imports
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Final, Self

# Package Imports
from pydantic import BaseModel, ConfigDict, RootModel, SecretStr, StringConstraints

# Local Imports
from udl.config import SecretsSettings
from udl.errors import ConfigError, SecretStoreError
from udl.plugins.requirements import Requirement, unmet

if TYPE_CHECKING:
    from udl.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

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

    def within(self, prefix: SecretRef) -> bool:
        """Whether this reference is `prefix` or under it (`accounts/1` holds `accounts/1/x`,
        not `accounts/12`)."""
        return self.root == prefix.root or self.root.startswith(f"{prefix.root}/")


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

    @abstractmethod
    async def delete_prefix(self, prefix: SecretRef) -> int:
        """Forget every secret under `prefix` (e.g. an account's). Returns how many."""


_AUTO: Final = "auto"


def open_secret_store(settings: SecretsSettings, plugins: PluginRegistry) -> SecretStore:
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
    if store.name == "memory":
        log.warning("[secrets] store is memory: the secrets udl is given are forgotten on exit")
    return store


# ---------------------------------------------------------------------------------------------
# A model's secrets
# ---------------------------------------------------------------------------------------------


class KeptSecret(BaseModel):
    """What a model keeps in place of a secret the store has: its reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    secret: SecretRef


type SecretField = SecretStr | KeptSecret
"""A secret field of a model udl keeps: the secret (given as text), or, once kept, where the
store has it."""


async def store_secrets[M: BaseModel](model: M, store: SecretStore, prefix: SecretRef) -> M:
    """`model` with each secret in it (its own fields', and its models') kept in `store`, at
    `<prefix>/<field>`, and a KeptSecret in its place."""
    changes: dict[str, Any] = {}
    for name, value in model:
        if isinstance(value, SecretStr):
            ref = SecretRef(f"{prefix}/{name}")
            await store.set(ref, value)
            changes[name] = KeptSecret(secret=ref)
        elif isinstance(value, BaseModel) and not isinstance(value, KeptSecret):
            changes[name] = await store_secrets(value, store, SecretRef(f"{prefix}/{name}"))
    return model.model_copy(update=changes)


async def load_secrets[M: BaseModel](model: M, store: SecretStore) -> M:
    """`model` with each KeptSecret in it (its own fields', and its models') replaced by the
    secret `store` has. Raises SecretStoreError when the store has lost one."""
    changes: dict[str, Any] = {}
    for name, value in model:
        if isinstance(value, KeptSecret):
            secret = await store.get(value.secret)
            if secret is None:
                raise SecretStoreError(
                    f"The secret store has no {value.secret} (it was kept on another machine, or "
                    "deleted)",
                    hint="Set it again (connect the account again).",
                )
            changes[name] = secret
        elif isinstance(value, BaseModel):
            changes[name] = await load_secrets(value, store)
    return model.model_copy(update=changes)
