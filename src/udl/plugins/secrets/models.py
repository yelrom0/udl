"""
-------------
- models.py -
-------------

What the secrets operations answer and are given: the store udl keeps secrets in, what each
resolver found (never a value), and 1Password's service account token.
"""

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Local Imports
from udl.core.operations import ui_field

__all__ = ["GetSecretsStatusRequest", "SecretsStatus", "SetToken"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class ResolverStatus(_Frozen):
    """One resolver: whether udl reads references through it, and what checking it found."""

    name: str
    """Its scheme (op), which its references start with."""
    enabled: bool
    """Whether udl reads references through it (its settings table says so)."""
    ok: bool | None = None
    """Whether it works (None when udl does not read through it, so nothing was checked)."""
    problems: list[str] = Field(default_factory=list[str])
    """What is wrong with it (a tool that is missing, a sign-in that is needed)."""
    vaults: list[str] = Field(default_factory=list[str])
    """What it can read, by name (never what is in it)."""


class GetSecretsStatusRequest(_Frozen):
    """Show where udl keeps its secrets, and what reads the ones it does not keep."""


class SecretsStatus(_Frozen):
    """Where udl keeps its secrets, and what reads the ones it does not keep."""

    store: str
    """The store [secrets] store names (keyring, file, memory, or auto's answer)."""
    unavailable: str | None = None
    """Why the store cannot keep secrets on this machine (None: it can)."""
    resolvers: list[ResolverStatus] = Field(default_factory=list[ResolverStatus])
    """What reads the secrets udl does not keep, and what checking each found."""


class SetToken(_Frozen):
    """1Password's service account token, which `op` is given (never udl's own reads)."""

    token: SecretStr = Field(
        min_length=1,
        json_schema_extra=ui_field(
            label="Service account token",
            widget="password",
            placeholder="ops_…",
            help="Kept in udl's own store: `op` is given it, and udl's own reads never are.",
        ),
    )
    """The token (asked for hidden, kept in udl's own store, never in a log line)."""
