"""
------------------------
- test_setup_pages.py -
------------------------

How the setup operations read on the generated pages (29.9): udl's own hosts and secrets, and
the orchestrator's projects, repos, machines and import, each in its own group, in the order a
person does them — and the fields that take a token or a path say what goes in them.
"""

# Package Imports
from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

# Local Imports
from udl.core.operations import Operation, operations
from udl.core.schemas import model_schema
from udl.hosts.models import SetHostRequest
from udl.plugins.apps.orch.models import ImportPlanRequest, SetProjectRequest, SetRepoRequest
from udl.plugins.secrets.models import SetToken


def place(operation: Operation) -> tuple[str | None, int]:
    """Where the page puts `operation`: its group and its place in it."""
    found = operation.ui
    assert found is not None
    return found.group, found.order


def places(names: list[str]) -> list[tuple[str | None, int]]:
    """Where udl's own page puts `names`."""
    found = operations()
    return [place(found[name]) for name in names]


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


def test_a_host_is_added_and_checked_in_one_group() -> None:
    assert places(["list_hosts", "set_host", "check_host", "delete_host"]) == [
        ("Hosts", 0),
        ("Hosts", 1),
        ("Hosts", 2),
        ("Hosts", 3),
    ]


def test_the_host_field_says_which_host() -> None:
    assert hints(SetHostRequest, "host")["label"] == "Host"
    assert hints(SetHostRequest, "update")["widget"] == "json"


def test_the_secrets_are_one_group_and_the_token_says_what_it_is() -> None:
    assert places(["get_secrets_status", "set_onepassword_token"]) == [
        ("Secrets", 0),
        ("Secrets", 1),
    ]
    said = hints(SetToken, "token")
    assert said["label"] == "Service account token"
    assert said["widget"] == "password"


def test_the_projects_and_repos_are_two_groups() -> None:
    from udl.plugins.apps.orch.plugin import Orch

    found = {operation.name: operation for operation in Orch.operations()}

    assert [
        place(found[name])
        for name in (
            "list_projects",
            "get_project",
            "set_project",
            "delete_project",
        )
    ] == [("Projects", 0), ("Projects", 1), ("Projects", 2), ("Projects", 3)]
    assert [place(found[name]) for name in ("set_repo", "delete_repo")] == [
        ("Repos", 0),
        ("Repos", 1),
    ]


def test_the_machines_and_the_import_are_groups_of_their_own() -> None:
    from udl.plugins.apps.orch.plugin import Orch

    found = {operation.name: operation for operation in Orch.operations()}

    assert place(found["list_runners"]) == ("Machines", 0)
    assert place(found["runner_install"])[0] == "Machines"
    assert place(found["import_plan"]) == ("Import", 0)


def test_a_provider_s_key_may_be_a_reference() -> None:
    # (An OpenRouter key, or the `op://` reference where 1Password keeps it: the box says so.)
    from udl.plugins.ai.base import ProviderOptions

    said = hints(ProviderOptions, "api_key")
    assert said["label"] == "API key"
    assert said["widget"] == "password"
    assert "op://" in str(said["placeholder"])


def test_a_project_and_a_repo_say_what_they_take() -> None:
    assert hints(SetProjectRequest, "name")["label"] == "Project"
    assert hints(SetRepoRequest, "github")["label"] == "On GitHub"
    assert hints(SetRepoRequest, "github")["placeholder"] == "yelrom0/udl"
    assert hints(ImportPlanRequest, "files")["label"] == "Plan files"
    assert hints(ImportPlanRequest, "dry_run")["label"] == "Only report what it would do"
