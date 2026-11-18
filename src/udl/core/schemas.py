"""
-------------
- schemas.py -
-------------

JSON Schemas as the interfaces hand them out: a model's, as `udl.core.operations.json_schema`
draws it (with the field hints checked), and the same with its `$defs` inlined — a form or a
table gets one self-contained schema, without having to resolve references itself. What the
generated UI is given (GET /ui/generated.json), and what 17.4's schema routes will be.
"""

# System Imports
from typing import Final, cast

# Package Imports
from pydantic import BaseModel, JsonValue

# Local Imports
from udl.core.operations import json_schema

_DEFS: Final = "#/$defs/"


def inlined(schema: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """`schema` with every `$ref` into its own `$defs` replaced by the definition it names, so it
    needs no resolving. A definition that refers to itself (a tree) stays a reference, and the
    `$defs` stay with it: that is the one case a schema cannot be inlined."""
    definitions = cast(dict[str, dict[str, JsonValue]], schema.get("$defs") or {})
    cyclic: set[str] = set()

    def expand(node: JsonValue, stack: tuple[str, ...]) -> JsonValue:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith(_DEFS):
                name = reference.removeprefix(_DEFS)
                if name in stack:  # (a cycle: the reference stays, and its definitions with it)
                    cyclic.add(name)
                    return {"$ref": reference}
                return expand(definitions[name], (*stack, name))
            return {key: expand(value, stack) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [expand(item, stack) for item in node]
        return node

    found = cast(dict[str, JsonValue], expand(schema, ()))
    if cyclic:
        found["$defs"] = cast(JsonValue, definitions)
    return found


def model_schema(model: type[BaseModel]) -> dict[str, JsonValue]:
    """`model`'s schema, its `$defs` inlined."""
    return inlined(json_schema(model))
