"""
-------------------------------------
- test_projects_operations.py -
-------------------------------------

The catalogue's operations (26.6): the projects and their repos, on the CLI, the API and MCP
through udl's generated adapters. `orch_list_projects` and `orch_get_project` read;
`orch_set_project`, `orch_delete_project`, `orch_set_repo` and `orch_delete_repo` are an admin's.

A project is set by its name: one that is there is edited, and what is not given is kept.
"""

# System Imports
from typing import Any

# Package Imports
import pytest

# Local Imports
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.orch.models import Deleted as DeletedShape
from udl.plugins.apps.orch.models import (
    DeleteProjectRequest,
    DeleteRepoRequest,
    GetProjectRequest,
    ListProjectsRequest,
    Project,
    ProjectInfo,
    Projects,
    Repo,
    SetProjectRequest,
    SetRepoRequest,
)
from udl.plugins.apps.orch.plugin import Orch

# (The operations this file is of, in the order the app offers them.)
PROJECT_OPERATIONS: list[str] = [
    "list_projects",
    "get_project",
    "set_project",
    "delete_project",
    "set_repo",
    "delete_repo",
]


def test_orch_offers_the_projects_operations() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert [name for name in offered if name in PROJECT_OPERATIONS] == PROJECT_OPERATIONS
    # (Reading a project changes nothing; setting one again replaces it; a delete is a delete.)
    assert offered["list_projects"].hints == OperationHints(read_only=True)
    assert offered["get_project"].hints == OperationHints(read_only=True)
    assert offered["set_project"].hints == OperationHints(admin=True)
    assert offered["delete_project"].hints == OperationHints(
        destructive=True, idempotent=True, admin=True
    )
    assert offered["set_repo"].hints == OperationHints(admin=True)
    assert offered["delete_repo"].hints == OperationHints(
        destructive=True, idempotent=True, admin=True
    )
    # (Every operation's request and answer are its own models, so the API and MCP have schemas.)
    assert offered["set_project"].request is SetProjectRequest
    assert offered["set_project"].response is Project
    assert offered["list_projects"].request is ListProjectsRequest
    assert offered["list_projects"].response is Projects
    assert offered["get_project"].response is ProjectInfo
    assert offered["delete_project"].request is DeleteProjectRequest
    assert offered["delete_project"].response is DeletedShape
    assert offered["set_repo"].request is SetRepoRequest
    assert offered["set_repo"].response is Repo
    assert offered["delete_repo"].request is DeleteRepoRequest


async def test_the_projects_operations_are_offered_as_orch_s(service: UDLService) -> None:
    names = [op.name for op in service.operations.in_namespace("orch")]
    offered = {f"orch_{name}" for name in PROJECT_OPERATIONS}

    assert [name for name in names if name in offered] == [
        f"orch_{name}" for name in PROJECT_OPERATIONS
    ]


# ---------------------------------------------------------------------------------------------
# Reading them
# ---------------------------------------------------------------------------------------------


async def test_the_projects_are_listed(service: UDLService) -> None:
    found = await service.call("orch_list_projects", {})

    assert isinstance(found, Projects)
    assert [project.name for project in found.projects] == ["udl"]
    assert found.projects[0].review_bot == "claude[bot]"


async def test_a_project_is_read_with_its_repos(service: UDLService) -> None:
    found = await service.call("orch_get_project", {"name": "udl"})

    assert isinstance(found, ProjectInfo)
    assert found.project.title == "udl"
    assert [(repo.name, repo.github) for repo in found.repos] == [("udl", "yelrom0/udl")]


async def test_a_project_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"liively"):
        await service.call("orch_get_project", {"name": "liively"})


# ---------------------------------------------------------------------------------------------
# Setting them
# ---------------------------------------------------------------------------------------------


async def a_project(service: UDLService, **request: Any) -> Project:
    """The answer of `orch_set_project`, as any interface's caller gets it."""
    found = await service.call("orch_set_project", request)

    assert isinstance(found, Project)
    return found


async def test_a_project_is_created(service: UDLService) -> None:
    made = await a_project(
        service,
        name="liively",
        title="Liively",
        description="The house's own work.",
        merge="agent",
        promotion=["rc", "main"],
    )

    assert (made.name, made.merge, made.promotion) == ("liively", "agent", ["rc", "main"])
    read = await service.call("orch_get_project", {"name": "liively"})
    assert isinstance(read, ProjectInfo)
    assert read.project.description == "The house's own work."


async def test_setting_a_project_again_edits_what_is_given(service: UDLService) -> None:
    # (A project is set by its name: what the caller does not give stays as it was, so changing
    # a title is not also clearing its rules.)
    edited = await a_project(service, name="udl", title="udl, the downloader")

    assert edited.title == "udl, the downloader"
    assert edited.rules == "Never push to main."
    assert edited.review_bot == "claude[bot]"
    assert edited.description == "What udl is."


async def test_a_new_project_needs_a_title(service: UDLService) -> None:
    with pytest.raises(NotAllowed, match="title"):
        await a_project(service, name="liively")


async def test_a_project_is_deleted_with_its_repos(service: UDLService) -> None:
    found = await service.call("orch_delete_project", {"name": "udl"})

    assert isinstance(found, DeletedShape)
    assert found.deleted is True
    with pytest.raises(DoesNotExist):
        await service.call("orch_get_project", {"name": "udl"})


async def test_deleting_a_project_that_is_not_there_says_so(service: UDLService) -> None:
    found = await service.call("orch_delete_project", {"name": "liively"})

    assert isinstance(found, DeletedShape)
    assert found.deleted is False


# ---------------------------------------------------------------------------------------------
# Repos
# ---------------------------------------------------------------------------------------------


async def a_repo(service: UDLService, **request: Any) -> Repo:
    """The answer of `orch_set_repo`, as any interface's caller gets it."""
    found = await service.call("orch_set_repo", request)

    assert isinstance(found, Repo)
    return found


async def test_a_repo_is_created_in_the_only_project(service: UDLService) -> None:
    # (A project is optional while udl has one, as an account is, 11.4.)
    made = await a_repo(service, name="docs", github="yelrom0/udl-docs", account="github:yelrom0")

    assert (made.project, made.name, made.base_branch) == ("udl", "docs", "main")


async def test_setting_a_repo_again_edits_what_is_given(service: UDLService) -> None:
    edited = await a_repo(service, name="udl", base_branch="rc")

    assert edited.base_branch == "rc"
    assert edited.github == "yelrom0/udl"
    assert edited.gate == ["make", "check"]


async def test_a_repo_of_a_project_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"liively"):
        await a_repo(service, project="liively", name="udl", github="x/y", account="github:yelrom0")


async def test_a_repo_is_deleted(service: UDLService) -> None:
    found = await service.call("orch_delete_repo", {"project": "udl", "name": "udl"})

    assert isinstance(found, DeletedShape)
    assert found.deleted is True
    read = await service.call("orch_get_project", {"name": "udl"})
    assert isinstance(read, ProjectInfo)
    assert read.repos == []


async def test_deleting_a_repo_that_is_not_there_says_so(service: UDLService) -> None:
    found = await service.call("orch_delete_repo", {"project": "udl", "name": "docs"})

    assert isinstance(found, DeletedShape)
    assert found.deleted is False


async def test_with_two_projects_the_one_to_work_in_is_named(service: UDLService) -> None:
    await a_project(service, name="liively", title="Liively")

    with pytest.raises(NotAllowed, match="Which project"):
        await a_repo(service, name="docs", github="x/y", account="github:yelrom0")


async def test_a_project_that_is_named_is_the_one_used(service: UDLService) -> None:
    await a_project(service, name="liively", title="Liively")

    made = await a_repo(
        service, project="liively", name="site", github="x/y", account="github:yelrom0"
    )

    assert made.project == "liively"


async def test_a_request_asks_for_a_project_by_its_name(service: UDLService) -> None:
    # (Keys, never ids: an operation takes the name its caller knows.)
    assert "name" in GetProjectRequest.model_fields
    assert "id" not in SetProjectRequest.model_fields
