"""
-------------------------------------
- test_links_operations.py -
-------------------------------------

The catalogue's links (26.6): `orch_add_link`, `orch_remove_link` and `orch_find_links`, on the
CLI, the API and MCP through udl's generated adapters.

A link is stored in its canonical form, so a PR written another way is the same link (26.4), and
finding it names the task it hangs on — the other way round from a task's own links.
"""

# System Imports
from typing import Any

# Package Imports
import pytest

# Local Imports
from udl.core.models import Deleted as DeletedShape
from udl.core.operations import OperationHints
from udl.core.service import UDLService
from udl.errors import ConfigError, DoesNotExist
from udl.plugins.apps.orch.models import (
    AddLinkRequest,
    FindLinksRequest,
    Link,
    Links,
    OwnerKind,
    RemoveLinkRequest,
)
from udl.plugins.apps.orch.plugin import Orch

LINK_OPERATIONS: list[str] = ["add_link", "remove_link", "find_links"]


def test_orch_offers_the_links_operations() -> None:
    offered = {op.name: op for op in Orch.operations()}

    assert [name for name in offered if name in LINK_OPERATIONS] == LINK_OPERATIONS
    assert offered["add_link"].hints == OperationHints()
    assert offered["remove_link"].hints == OperationHints()
    assert offered["find_links"].hints == OperationHints(read_only=True)
    assert offered["add_link"].request is AddLinkRequest
    assert offered["add_link"].response is Link
    assert offered["remove_link"].request is RemoveLinkRequest
    assert offered["remove_link"].response is DeletedShape
    assert offered["find_links"].request is FindLinksRequest
    assert offered["find_links"].response is Links


async def a_link(service: UDLService, **request: Any) -> Link:
    """The answer of `orch_add_link`, as any interface's caller gets it."""
    found = await service.call("orch_add_link", {"owner_kind": "task", "owner": "26.2", **request})

    assert isinstance(found, Link)
    return found


async def test_a_link_is_added_in_its_canonical_form(service: UDLService) -> None:
    made = await a_link(
        service,
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/57/?ref=main#discussion",
        title="The proof",
    )

    assert made.url == "https://github.com/yelrom0/udl/pull/57"
    assert (made.owner_kind, made.owner) == (OwnerKind.TASK, "26.2")


async def test_a_link_added_again_is_the_one_link(service: UDLService) -> None:
    first = await a_link(service, link_kind="pull", url="https://github.com/yelrom0/udl/pull/57")
    second = await a_link(
        service, link_kind="pull", url="https://github.com/yelrom0/udl/pull/57/files"
    )

    assert second.url == first.url
    read = await service.call("orch_find_links", {"url": first.url})
    assert isinstance(read, Links)
    assert len(read.links) == 1


async def test_a_link_that_is_not_what_it_says_is_refused(service: UDLService) -> None:
    with pytest.raises(ConfigError, match="GitHub URL"):
        await a_link(service, link_kind="pull", url="https://gitlab.example/x/y/pull/1")


async def test_a_link_is_removed(service: UDLService) -> None:
    await a_link(service, link_kind="pull", url="https://github.com/yelrom0/udl/pull/57")

    found = await service.call(
        "orch_remove_link",
        {
            "owner_kind": "task",
            "owner": "26.2",
            "url": "https://github.com/yelrom0/udl/pull/57/",
        },
    )

    assert isinstance(found, DeletedShape)
    assert found.deleted is True
    read = await service.call("orch_find_links", {"url": "https://github.com/yelrom0/udl/pull/57"})
    assert isinstance(read, Links)
    assert read.links == []


async def test_removing_a_link_that_is_not_there_says_so(service: UDLService) -> None:
    found = await service.call(
        "orch_remove_link",
        {
            "owner_kind": "task",
            "owner": "26.2",
            "url": "https://github.com/yelrom0/udl/pull/57",
        },
    )

    assert isinstance(found, DeletedShape)
    assert found.deleted is False


async def test_finding_a_link_names_its_task(service: UDLService) -> None:
    # (26.4's done when: a PR's URL finds the task it is on, however the URL is written.)
    await a_link(
        service,
        owner_kind="task",
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/57",
        title="The proof",
    )

    found = await service.call(
        "orch_find_links", {"url": "https://www.github.com/yelrom0/udl/pull/57/files"}
    )

    assert isinstance(found, Links)
    assert found.url == "https://github.com/yelrom0/udl/pull/57"
    assert [(link.owner_kind, link.owner, link.title) for link in found.links] == [
        (OwnerKind.TASK, "26.2", "The proof")
    ]


async def test_finding_a_url_nothing_points_at_answers_nothing(service: UDLService) -> None:
    found = await service.call("orch_find_links", {"url": "https://github.com/yelrom0/udl/pull/1"})

    assert isinstance(found, Links)
    assert found.links == []


async def test_a_link_of_a_task_that_is_not_there_is_refused(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match=r"26\.9"):
        await a_link(service, owner="26.9", link_kind="pull", url="https://github.com/x/y/pull/1")


def test_a_link_s_owner_is_a_key_and_its_url_is_bounded() -> None:
    # (A key is a key and a URL has a length, at the edge, as in every other request.)
    with pytest.raises(ValueError, match="40"):
        AddLinkRequest.model_validate(
            {
                "owner_kind": "task",
                "owner": "26." + "1" * 40,
                "link_kind": "pull",
                "url": "https://github.com/yelrom0/udl/pull/1",
            }
        )
    with pytest.raises(ValueError, match="2000"):
        RemoveLinkRequest.model_validate(
            {
                "owner_kind": "task",
                "owner": "26.1",
                "url": "https://github.com/x/y/pull/" + "1" * 2000,
            }
        )
