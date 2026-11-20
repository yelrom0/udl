"""
---------------------------
- test_links.py -
---------------------------

Links (udl.plugins.apps.orch.links): the phase's second bullet — adding a PR link to a task
makes the task findable from the PR's URL; deleting a task deletes its links; a feature lists
its tasks and each task names its feature — and the canonical forms a URL is stored by (one PR
is one URL, however it was written; canonicalising twice changes nothing).

The hook that refreshes a pull's state on `github.pr.*` is 26.4's service, tested against a
fake service (no GitHub here).
"""

# System Imports
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from udl.config import Settings
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.records import (
    FeatureRecords,
    Link,
    LinkRecords,
    Project,
    ProjectRecords,
    TaskRecords,
)

# ---------------------------------------------------------------------------------------------
# Canonical forms: one PR is one URL
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        "https://github.com/yelrom0/udl/pull/33",
        "https://github.com/yelrom0/udl/pull/33/",
        "https://github.com/yelrom0/udl/pull/33/files",
        "http://github.com/yelrom0/udl/pull/33?diff=split",
        "https://github.com/yelrom0/udl/pull/33#issuecomment-1",
        "https://www.github.com/yelrom0/udl/pull/33#discussion",
        "HTTPS://GitHub.com/yelrom0/udl/pull/33/",
    ],
)
def test_a_github_url_is_one_pr_however_it_was_written(given: str) -> None:
    from udl.plugins.apps.orch.links import canonical

    assert canonical(given) == "https://github.com/yelrom0/udl/pull/33"


@pytest.mark.parametrize(
    ("given", "want"),
    [
        ("https://github.com/yelrom0/udl/issues/7", "https://github.com/yelrom0/udl/issues/7"),
        (
            "https://github.com/yelrom0/udl/commit/abc123",
            "https://github.com/yelrom0/udl/commit/abc123",
        ),
        (
            "https://github.com/yelrom0/udl/commit/abc123.patch",
            "https://github.com/yelrom0/udl/commit/abc123",
        ),
        ("https://github.com/yelrom0/udl/tree/main", "https://github.com/yelrom0/udl/tree/main"),
        (
            "https://github.com/yelrom0/udl/pull/33/files#diff-1",
            "https://github.com/yelrom0/udl/pull/33",
        ),
    ],
)
def test_a_github_url_keeps_what_makes_it_the_thing(given: str, want: str) -> None:
    from udl.plugins.apps.orch.links import canonical

    assert canonical(given) == want


@pytest.mark.parametrize(
    "given",
    [
        "https://udl.local/docs/26",
        "udl:task/udl/26.2",
        "https://example.com/a?b=c#d",
    ],
)
def test_a_url_that_is_not_github_is_left_as_it_is(given: str) -> None:
    from udl.plugins.apps.orch.links import canonical

    assert canonical(given) == given


@pytest.mark.parametrize(
    ("given", "want"),
    [
        (
            "https://github.com/yelrom0/udl/commits/abc123",
            "https://github.com/yelrom0/udl/commit/abc123",
        ),
        ("https://github.com/yelrom0/udl", "https://github.com/yelrom0/udl"),
        ("https://github.com/yelrom0/udl/", "https://github.com/yelrom0/udl"),
        (
            "https://github.com/yelrom0/udl/blob/main/src/x.py",
            "https://github.com/yelrom0/udl/blob/main/src/x.py",
        ),
    ],
)
def test_a_github_url_of_another_shape_is_canonical(given: str, want: str) -> None:
    from udl.plugins.apps.orch.links import canonical

    assert canonical(given) == want


def test_a_github_url_with_no_pull_issue_or_commit_is_not_canonical() -> None:
    from udl.plugins.apps.orch.links import canonical

    assert canonical("https://github.com/yelrom0/udl") == "https://github.com/yelrom0/udl"


def test_canonicalising_twice_changes_nothing() -> None:
    from hypothesis import given
    from hypothesis import strategies as st

    from udl.plugins.apps.orch.links import canonical

    def url(owner: str, repo: str, kind: str, number: int, tail: str) -> str:
        return f"https://github.com/{owner}/{repo}/{kind}/{number}{tail}"

    urls = st.builds(
        url,
        owner=st.text("abcXYZ", min_size=1, max_size=6),
        repo=st.text("abcXYZ", min_size=1, max_size=6),
        kind=st.sampled_from(["pull", "issues", "commit"]),
        number=st.integers(min_value=1, max_value=9999),
        tail=st.sampled_from(["", "/", "/files", "?diff=split", "#diff-1", ".patch"]),
    )

    @given(urls)
    def twice(url: str) -> None:
        once = canonical(url)
        assert canonical(once) == once

    twice()


# ---------------------------------------------------------------------------------------------
# Storing links: indexed by owner and by url
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def links(tmp_path: Path) -> LinkRecords:
    """Orch's links over a fresh in-memory database (as tests/support/engine.py makes one)."""
    from support.engine import databases, registry, settings

    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    records = AppRecords(db)
    await ProjectRecords(records).save_project(Project(name="udl", title="udl"))
    return LinkRecords(records)


async def test_a_github_link_of_another_kind_must_be_a_github_url(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a GitHub URL"):
        await links.add(owner_kind="task", owner="26.2", kind="commit", url="udl:task/udl/26.2")


async def test_a_link_is_read_back(links: LinkRecords) -> None:
    link = await links.add(
        owner_kind="task",
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        title="Projects and repos",
        state={"state": "open", "head_sha": "abc", "checks": "pending", "review": "none"},
        added_by="ernie-c",
    )

    found = await links.get(link.id)
    assert found is not None
    assert found.url == "https://github.com/yelrom0/udl/pull/33"
    assert found.state == {
        "state": "open",
        "head_sha": "abc",
        "checks": "pending",
        "review": "none",
    }


async def test_a_link_can_be_added_twice_as_the_same_url(links: LinkRecords) -> None:
    url = "https://github.com/yelrom0/udl/pull/33"
    first = await links.add(owner_kind="task", owner="26.2", kind="pull", url=url)
    again = await links.add(owner_kind="task", owner="26.2", kind="pull", url=url)

    assert again.id == first.id  # (one URL, one link: adding it again is the same link)


async def test_a_task_is_findable_from_its_prs_url(links: LinkRecords) -> None:
    await links.add(
        owner_kind="task", owner="26.2", kind="pull", url="https://github.com/yelrom0/udl/pull/33"
    )

    found = await links.for_url("https://github.com/yelrom0/udl/pull/33/")

    assert [link.owner for link in found] == ["26.2"]


async def test_deleting_a_link(links: LinkRecords) -> None:
    link = await links.add(
        owner_kind="task",
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    assert await links.delete(link) is True
    assert await links.delete(link) is False
    assert await links.for_url("https://github.com/yelrom0/udl/pull/33") == []


async def test_a_saved_link_is_replaced(links: LinkRecords) -> None:
    link = await links.add(
        owner_kind="task",
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state={"state": "open"},
    )

    saved = await links.save(link.model_copy(update={"title": "Projects and repos"}))

    found = await links.get(link.id)
    assert found is not None
    assert found.title == "Projects and repos"
    assert saved.title == "Projects and repos"


async def test_a_link_is_got_by_its_id(links: LinkRecords) -> None:
    link = await links.add(
        owner_kind="task",
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    found = await links.get(link.id)
    assert found is not None
    assert found.id == link.id


async def test_deleting_things_that_hang_on_nobody_touches_nothing(links: LinkRecords) -> None:
    link = await links.add(
        owner_kind="task",
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    assert await links.delete_for("feature", "26") == 0
    assert [
        found.id for found in await links.for_url("https://github.com/yelrom0/udl/pull/33")
    ] == [link.id]


async def test_a_deleted_task_takes_its_links(links: LinkRecords) -> None:
    await links.add(
        owner_kind="task", owner="26.2", kind="pull", url="https://github.com/yelrom0/udl/pull/33"
    )
    await links.add(
        owner_kind="task", owner="26.3", kind="issue", url="https://github.com/yelrom0/udl/issues/7"
    )

    await links.delete_for("task", "26.2")

    assert [
        link.owner for link in await links.for_url("https://github.com/yelrom0/udl/pull/33")
    ] == []
    assert [
        link.owner for link in await links.for_url("https://github.com/yelrom0/udl/issues/7")
    ] == ["26.3"]


async def test_a_links_owner_can_be_a_feature_a_question_or_an_action(links: LinkRecords) -> None:
    for owner_kind, owner in [
        ("feature", "26"),
        ("task", "26.2"),
        ("question", "Q-7"),
        ("action", "A-3"),
    ]:
        await links.add(
            owner_kind=owner_kind,  # type: ignore[arg-type]
            owner=owner,
            kind="url",
            url=f"https://example.com/{owner_kind}/{owner}",
        )

    assert len(await links.for_owner("feature", "26")) == 1
    assert len(await links.for_owner("task", "26.2")) == 1
    assert len(await links.for_owner("question", "Q-7")) == 1
    assert len(await links.for_owner("action", "A-3")) == 1


async def test_an_internal_kind_has_a_udl_url(links: LinkRecords) -> None:
    link = await links.add(owner_kind="feature", owner="26", kind="task", url="udl:task/udl/26.2")

    assert link.url == "udl:task/udl/26.2"
    assert [found.owner for found in await links.for_url("udl:task/udl/26.2")] == ["26"]


async def test_a_link_to_something_that_is_not_there_is_refused(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a GitHub URL"):
        await links.add(owner_kind="task", owner="26.2", kind="pull", url="not a url")


async def test_a_link_with_no_url_is_refused(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a URL"):
        await links.add(owner_kind="task", owner="26.2", kind="url", url="")


async def test_a_url_link_must_be_a_url(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a URL"):
        await links.add(owner_kind="task", owner="26.2", kind="url", url="not a url")


# ---------------------------------------------------------------------------------------------
# Text scanned on save: a PR URL or a task key in backticks becomes a link
# ---------------------------------------------------------------------------------------------


async def test_a_github_url_in_a_spec_becomes_a_link() -> None:
    from udl.plugins.apps.orch.links import links_in

    found = links_in("The PR is https://github.com/yelrom0/udl/pull/33, merged.")

    assert [(link.link_kind, link.url) for link in found] == [
        ("pull", "https://github.com/yelrom0/udl/pull/33")
    ]


async def test_a_task_key_in_backticks_becomes_a_link() -> None:
    from udl.plugins.apps.orch.links import links_in

    found = links_in("Needs `26.2` and `22.4` done first.")

    assert [(link.link_kind, link.url) for link in found] == [
        ("task", "udl:task/udl/26.2"),
        ("task", "udl:task/udl/22.4"),
    ]


async def test_text_with_nothing_to_link_has_no_links() -> None:
    from udl.plugins.apps.orch.links import links_in

    assert links_in("Nothing to link here.") == []


async def test_the_same_url_twice_is_one_link() -> None:
    from udl.plugins.apps.orch.links import links_in

    found = links_in("See https://github.com/yelrom0/udl/pull/33 and again /33.")

    assert len(found) == 1


def test_a_sentence_punctuation_is_not_part_of_the_url() -> None:
    from udl.plugins.apps.orch.links import links_in

    found = links_in("See (https://github.com/yelrom0/udl/pull/33).")

    assert [link.url for link in found] == ["https://github.com/yelrom0/udl/pull/33"]


# ---------------------------------------------------------------------------------------------
# A pull's state is refreshed on github.pr.*, and orch.link.changed is published
# ---------------------------------------------------------------------------------------------


class FakeGithub:
    """What orch's service needs of github_get_pull (a pull's state, as the client gives it)."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.calls: list[tuple[str, int]] = []

    async def get_pull(self, repo: str, number: int) -> Any:
        self.calls.append((repo, number))
        return self.state


async def test_a_pull_link_is_refreshed_on_a_github_pr_event() -> None:
    from udl.plugins.apps.orch.links import refresh_pull

    link = Link(
        owner_kind="task",
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state={"state": "open"},
    )
    github = FakeGithub(
        {"state": "merged", "head_sha": "abc", "checks": "pass", "review": "approved"}
    )

    refreshed = await refresh_pull(link, github=github)

    assert refreshed.state["state"] == "merged"
    assert refreshed.state["checks"] == "pass"
    assert refreshed.state["review"] == "approved"
    assert github.calls == [("yelrom0/udl", 33)]


async def test_a_link_that_is_not_a_pull_is_left_alone() -> None:
    from udl.plugins.apps.orch.links import refresh_pull

    link = Link(
        owner_kind="task",
        owner="26.2",
        link_kind="issue",
        url="https://github.com/yelrom0/udl/issues/7",
    )
    github = FakeGithub({"state": "open"})

    assert await refresh_pull(link, github=github) == link
    assert github.calls == []


async def test_a_pull_link_with_a_url_that_is_not_a_pulls_is_left_alone() -> None:
    from udl.plugins.apps.orch.links import refresh_pull

    link = Link(
        owner_kind="task",
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state={"state": "open"},
    )
    other = link.model_copy(update={"url": "https://github.com/yelrom0/udl/issues/7"})
    github = FakeGithub({"state": "merged"})

    assert await refresh_pull(other, github=github) == other
    assert github.calls == []


async def test_a_pull_link_is_refreshed_only_when_its_url_matches() -> None:
    from udl.plugins.apps.orch.links import on_github_pr

    link = Link(
        owner_kind="task",
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state={"state": "open"},
    )
    github = FakeGithub({"state": "closed"})

    changed = await on_github_pr(
        [link],
        event={
            "action": "closed",
            "pull_request": {"html_url": "https://github.com/yelrom0/udl/pull/33"},
        },
        github=github,
    )

    assert [link.state["state"] for link in changed] == ["closed"]
    assert github.calls == [("yelrom0/udl", 33)]


async def test_a_github_pr_event_with_no_url_changes_nothing() -> None:
    from udl.plugins.apps.orch.links import on_github_pr

    link = Link(
        owner_kind="task",
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state={"state": "open"},
    )
    github = FakeGithub({"state": "merged"})

    assert await on_github_pr([link], event={"action": "closed"}, github=github) == []
    assert github.calls == []


async def test_a_github_pr_event_for_another_pr_changes_nothing() -> None:
    from udl.plugins.apps.orch.links import on_github_pr

    link = Link(
        owner_kind="task",
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state={"state": "open"},
    )
    github = FakeGithub({"state": "merged"})

    changed = await on_github_pr(
        [link],
        event={
            "action": "closed",
            "pull_request": {"html_url": "https://github.com/yelrom0/udl/pull/34"},
        },
        github=github,
    )

    assert changed == []
    assert github.calls == []


# ---------------------------------------------------------------------------------------------
# A spec is scanned as it is saved: its URLs and task keys become links
# ---------------------------------------------------------------------------------------------


async def test_a_tasks_spec_becomes_links_when_it_is_saved(links: LinkRecords) -> None:
    records = await _task_records(links)

    task = await records.create_task(
        "udl", "F-1", "The task", spec="The PR is https://github.com/yelrom0/udl/pull/33."
    )

    assert [(link.link_kind, link.url) for link in await links.for_owner("task", task.key)] == [
        ("pull", "https://github.com/yelrom0/udl/pull/33")
    ]


async def test_a_task_key_in_a_spec_is_a_link_in_its_project(links: LinkRecords) -> None:
    records = await _task_records(links)

    task = await records.create_task("udl", "F-1", "The task", spec="Needs `26.2` first.")

    assert [link.url for link in await links.for_owner("task", task.key)] == ["udl:task/udl/26.2"]


async def test_a_task_with_nothing_to_link_has_none(links: LinkRecords) -> None:
    records = await _task_records(links)

    await records.create_task("udl", "F-1", "The task", spec="Nothing to link.")

    assert await links.for_owner("task", "F-1.1") == []


async def _task_records(links: LinkRecords) -> Any:
    """orch's tasks over the same records, with a feature to hang them on."""
    from udl.plugins.apps.orch.records import Feature

    records = TaskRecords(links.records)
    await FeatureRecords(links.records).save_feature(Feature(project="udl", key="F-1", title="f"))
    return records


# ---------------------------------------------------------------------------------------------
# The service: orch publishes orch.link.changed
# ---------------------------------------------------------------------------------------------


async def test_the_link_changed_event_is_one_of_orchs() -> None:
    from udl.plugins.apps.orch.plugin import Orch

    names = {event_type.name for event_type in Orch.event_types()}

    assert "orch.link.changed" in names


async def test_the_link_changed_event_describes_the_link() -> None:
    from udl.plugins.apps.orch.links import LINK_CHANGED

    event = LINK_CHANGED.data(
        owner_kind="task", owner="26.2", url="https://github.com/yelrom0/udl/pull/33"
    )

    assert LINK_CHANGED.subject(event) == (
        "A link on task 26.2: https://github.com/yelrom0/udl/pull/33"
    )


async def test_a_link_changed_event_carries_the_links_owner_and_url() -> None:
    from udl.plugins.apps.orch.links import LinkChanged

    event = LinkChanged(
        owner_kind="task", owner="26.2", url="https://github.com/yelrom0/udl/pull/33"
    )

    assert event.model_dump()["owner"] == "26.2"
