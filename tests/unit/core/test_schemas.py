"""
-----------------
- test_schemas.py -
-----------------

A model's JSON Schema as the interfaces hand it out: with its `$defs` inlined, so a form or a
table gets one self-contained schema — except where a definition refers to itself (a tree), which
keeps its `$defs` so it still resolves.
"""

# Package Imports
from pydantic import BaseModel, Field, JsonValue

# Local Imports
from udl.core.operations import json_schema
from udl.core.schemas import inlined, model_schema


class Address(BaseModel):
    """Where something is."""

    street: str
    city: str


class Home(BaseModel):
    """A home address, which a person may have a description and a default for."""

    street: str


class Person(BaseModel):
    """Someone."""

    name: str
    address: Address
    other: Address | None = None
    home: Home = Field(
        default_factory=lambda: Home(street="1 Test Street"),
        json_schema_extra={"title": "Their home", "default": {"street": "1 Test Street"}},
        description="Where they live",
    )


class Node(BaseModel):
    """A tree: a node has nodes."""

    label: str
    children: list[Node] = []


def test_a_schema_without_definitions_is_as_it_was() -> None:
    schema: dict[str, JsonValue] = {"type": "object", "properties": {"name": {"type": "string"}}}

    assert inlined(schema) == schema


def test_a_definition_is_put_where_it_is_referred_to() -> None:
    found = inlined(json_schema(Person))

    assert "$defs" not in found
    assert "$ref" not in str(found)
    properties = found["properties"]
    assert isinstance(properties, dict)
    address = properties["address"]
    assert isinstance(address, dict)
    fields = address["properties"]
    assert isinstance(fields, dict)
    assert set(fields) == {"street", "city"}
    # (Both places it was referred to have it, and neither is shared.)
    other = properties["other"]
    assert isinstance(other, dict)
    assert "anyOf" in other


def test_a_definition_that_refers_to_itself_keeps_its_definitions() -> None:
    # (A tree cannot be inlined: the reference stays, and what it needs stays with it.)
    found = inlined(json_schema(Node))

    assert "$ref" in str(found)
    assert "$defs" in found


def test_model_schema_is_the_schema_inlined() -> None:
    assert model_schema(Person) == inlined(json_schema(Person))


def test_a_field_s_keys_beside_its_reference_survive() -> None:
    # (pydantic writes a field's own keywords next to its `$ref`, not in an `allOf`: its
    # description, title and default — and a `ui_field()` hint — are the field's, not the
    # definition's, so inlining must keep them.)
    found = inlined(json_schema(Person))

    properties = found["properties"]
    assert isinstance(properties, dict)
    home = properties["home"]
    assert isinstance(home, dict)
    assert home["description"] == "Where they live"
    assert home["title"] == "Their home"
    assert home["default"] == {"street": "1 Test Street"}
    assert home["properties"] == {"street": {"type": "string", "title": "Street"}}
