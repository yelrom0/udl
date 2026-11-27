"""
----------------
- generated.py -
----------------

What the generated page is told about a plugin, for the tests that read it: where an operation
sits (29.9's groups and their order), and what a field says about how the page shows it.
"""

# Package Imports
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

# Local Imports
from udl.core.operations import Operation
from udl.core.schemas import model_schema


def place(operation: Operation) -> tuple[str | None, int]:
    """Where the page puts `operation`: its group, and its place in it."""
    found = operation.ui
    assert found is not None, f"{operation.name} has no UISpec: the page would put it nowhere"
    return found.group, found.order


def places(operations: list[Operation]) -> list[tuple[str | None, int]]:
    """Where the page puts `operations`, in the order given."""
    return [place(operation) for operation in operations]


def hints(model: type, field: str) -> dict[str, JsonValue]:
    """What `model`'s `field` says about how the page shows it (`ui_field`'s `x-udl-ui`)."""
    schema = model_schema(model)
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    named = properties[field]
    assert isinstance(named, Mapping)
    said = named.get("x-udl-ui")
    assert isinstance(said, dict), f"{model.__name__}.{field} says nothing about how it is shown"
    return cast(dict[str, JsonValue], said)
