"""
-----------------
- operations.py -
-----------------

Gizmo: a site plugin that adds operations of its own, whose requests use every kind of field
the generated adapters (the CLI's, the API's and the MCP server's) have to handle
"""

# System Imports
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

# Local Imports
from udl.core.operations import Operation, OperationHints
from udl.errors import DoesNotExist
from udl.plugins.sites.base import SitePlugin

if TYPE_CHECKING:
    from udl.core.service import UDLService


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Colour(StrEnum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class Point(_Model):
    x: int
    """Across."""
    y: int = 0
    """Down."""


class EchoRequest(_Model):
    """Echo a request back."""

    text: str
    """Some text."""
    count: int = 1
    """A number."""
    flag: bool = False
    """A yes or no."""
    colour: Colour = Colour.RED
    """One of a few."""
    path: Path | None = None
    """A file."""
    tags: list[str] = Field(default_factory=list[str])
    """Some words."""
    sizes: list[int] = Field(default_factory=list[int])
    """Some numbers."""
    point: Point | None = None
    """A model in the model."""
    token: SecretStr | None = None
    """A secret."""
    extra: JsonValue = None
    """Any JSON."""
    limit: Annotated[int, Field(ge=1, le=10)] = 5
    """A number with bounds."""
    points: list[Point] = Field(default_factory=list[Point])
    """Models in a list."""
    either: int | str | None = None
    """A number or some text."""


class Echoed(_Model):
    text: str
    count: int
    flag: bool
    colour: Colour
    path: Path | None
    tags: list[str]
    sizes: list[int]
    point: Point | None
    token_length: int | None
    """How long the secret was (the secret itself is never answered)."""
    extra: JsonValue
    limit: int
    points: list[Point]
    either: int | str | None


def echoed(request: EchoRequest) -> Echoed:
    """What echo answers to `request`."""
    fields: dict[str, Any] = request.model_dump(exclude={"token"})
    token = request.token.get_secret_value() if request.token is not None else None
    return Echoed.model_validate({**fields, "token_length": None if token is None else len(token)})


class LookupRequest(_Model):
    """Look something up."""

    key: str
    """What to look up."""


class Found(_Model):
    value: str


async def echo(service: UDLService, request: EchoRequest) -> Echoed:
    """Echo a request back: every field it was given, as udl understood it.

    For testing the adapters udl makes for a plugin's operations."""
    return echoed(request)


async def lookup(service: UDLService, request: LookupRequest) -> Found:
    """Look a key up in the gizmo's table; an unknown key does not exist."""
    if request.key != "known":
        raise DoesNotExist(f"No {request.key!r} here", hint="Only 'known' is known.")
    return Found(value="yes")


class Gizmo(SitePlugin):
    """A site plugin (for no sites) that only adds operations."""

    name = "gizmo"

    @classmethod
    def operations(cls) -> list[Operation]:
        return [
            Operation.of(echo, name="echo", title="Echo", hints=OperationHints(read_only=True)),
            Operation.of(lookup, name="lookup", title="Look up", hints=OperationHints()),
        ]
