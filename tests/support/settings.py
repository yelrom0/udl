"""
---------------
- settings.py -
---------------

Configurable: a site plugin with settings of its own (a settings_model), of the kinds udl treats
differently: a secret (kept in the secret store), a file-only field (only the config file sets
it) and plain ones
"""

# System Imports
from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, SecretStr

# Local Imports
from udl.config import ExpandedPath
from udl.plugins.settings import FileOnly
from udl.plugins.sites.base import SitePlugin


class Speed(StrEnum):
    SLOW = "slow"
    FAST = "fast"


class ConfigurableSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    speed: Speed = Speed.SLOW
    """How fast to go."""
    limit: Annotated[int, Field(ge=1, le=100)] = 10
    """How many at most."""
    client_id: str | None = None
    """The client's id."""
    client_secret: SecretStr | None = None
    """The client's secret."""
    roots: Annotated[list[ExpandedPath], FileOnly()] = Field(default_factory=list[Path])
    """The folders it may read files from (only the config file widens them)."""


class Configurable(SitePlugin):
    """A site plugin (for no sites) with settings."""

    name = "configurable"
    settings_model: ClassVar[type[BaseModel]] = ConfigurableSettings
