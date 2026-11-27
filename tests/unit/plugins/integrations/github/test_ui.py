"""
-------------------
- test_ui.py -
-------------------

What the github integration tells the generated page about itself (29.9): the token field says
what goes in it — a token, or a 1Password reference — the repositories field says what it takes,
and the operations are grouped and ordered so the page reads as pulls, checks and merge.
"""

# Package Imports
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

# Local Imports
from udl.core.operations import Operation
from udl.core.schemas import model_schema
from udl.plugins.integrations.github.plugin import GitHubIntegration, GitHubOptions, GitHubToken


def place(operation: Operation) -> tuple[str | None, int]:
    """Where the page puts `operation`: its group and its place in it."""
    found = operation.ui
    assert found is not None
    return found.group, found.order


def hints(model: type, field: str) -> dict[str, JsonValue]:
    """What `model`'s `field` says about how the page shows it."""
    schema = model_schema(model)
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    named = properties[field]
    assert isinstance(named, Mapping)
    said = named.get("x-udl-ui")
    assert isinstance(said, dict)
    return cast(dict[str, JsonValue], said)


def test_the_token_says_it_may_be_a_reference() -> None:
    """A token is typed here, or an `op://` reference is: the box says so, and says the
    reference is read when it is used (never kept)."""
    said = hints(GitHubToken, "token")

    assert said["label"] == "Token"
    assert "op://" in str(said["placeholder"])
    assert "1Password" in str(said["help"])


def test_the_repositories_say_what_they_take() -> None:
    said = hints(GitHubOptions, "repos")

    assert said["label"] == "Repositories"
    assert "owner/name" in str(said["help"])
    assert said["placeholder"] == "acme/widgets"


def test_its_operations_are_grouped_in_the_order_the_page_reads() -> None:
    found = {operation.name: operation for operation in GitHubIntegration.operations()}

    assert [place(found[name]) for name in ("list_pulls", "get_pull", "open_pull", "comment")] == [
        ("Pulls", 0),
        ("Pulls", 1),
        ("Pulls", 2),
        ("Pulls", 3),
    ]
    assert place(found["rerun_checks"])[0] == "Checks"
    assert place(found["merge_pull"])[0] == "Merge"
