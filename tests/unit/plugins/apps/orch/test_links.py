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
import asyncio
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Local Imports
from udl.clock import SystemClock
from udl.config import Settings
from udl.db.models import JsonObject
from udl.plugins.apps.base import AppEvent, AppRecords
from udl.plugins.apps.orch.links import LinkRecords
from udl.plugins.apps.orch.models import Link as ShapeLink
from udl.plugins.apps.orch.models import OwnerKind, PullState
from udl.plugins.apps.orch.records import (
    FeatureRecords,
    Link,
    Project,
    ProjectRecords,
    TaskRecords,
)


def links_in_proj(text: str, *, project: str = "udl") -> list[ShapeLink]:
    """`links_in`, in a project's namespace (what a save's scan calls it with)."""
    from udl.plugins.apps.orch.links import links_in

    return links_in(text, project=project)


class _FakeCheck(BaseModel):
    """One of a pull's checks, as udl's github integration answers it."""

    name: str = ""
    status: str = ""
    conclusion: str | None = None


class PullDetail(BaseModel):
    """A `github_get_pull` answer, as far as a link's state goes (the github integration's own
    shape, with the fields it has)."""

    model_config = ConfigDict(extra="ignore")

    state: str = "open"
    head_sha: str = ""
    checks: list[_FakeCheck] = Field(default_factory=list[_FakeCheck])


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
        await links.add(
            project="udl",
            owner_kind=OwnerKind.TASK,
            owner="26.2",
            kind="commit",
            url="udl:task/udl/26.2",
        )


async def test_a_link_is_read_back(links: LinkRecords) -> None:
    link = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        title="Projects and repos",
        state=PullState(state="open", head_sha="abc", checks="pending"),
        added_by="ernie-c",
    )

    found = await links.get(link.id)
    assert found is not None
    assert found.url == "https://github.com/yelrom0/udl/pull/33"
    assert found.state == PullState(state="open", head_sha="abc", checks="pending", review="none")


async def test_a_link_can_be_added_twice_as_the_same_url(links: LinkRecords) -> None:
    url = "https://github.com/yelrom0/udl/pull/33"
    first = await links.add(
        project="udl", owner_kind=OwnerKind.TASK, owner="26.2", kind="pull", url=url
    )
    again = await links.add(
        project="udl", owner_kind=OwnerKind.TASK, owner="26.2", kind="pull", url=url
    )

    assert again.id == first.id  # (one URL, one link: adding it again is the same link)


async def test_a_task_is_findable_from_its_prs_url(links: LinkRecords) -> None:
    await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    found = await links.for_url("https://github.com/yelrom0/udl/pull/33/")

    assert [link.owner for link in found] == ["26.2"]


async def test_deleting_a_link(links: LinkRecords) -> None:
    link = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    assert await links.delete(link) is True
    assert await links.delete(link) is False
    assert await links.for_url("https://github.com/yelrom0/udl/pull/33") == []


async def test_a_saved_link_is_replaced(links: LinkRecords) -> None:
    link = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )

    saved = await links.save(link.model_copy(update={"title": "Projects and repos"}))

    found = await links.get(link.id)
    assert found is not None
    assert found.title == "Projects and repos"
    assert saved.title == "Projects and repos"


async def test_two_projects_each_link_the_same_pr(links: LinkRecords) -> None:
    """A link is a project's own: two projects that each have a task `1.1` each link the same
    PR, and deleting one project's task leaves the other's link."""
    one = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="1.1",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )
    two = await links.add(
        project="liively",
        owner_kind=OwnerKind.TASK,
        owner="1.1",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    assert (one.project, two.project) == ("udl", "liively")
    assert len(await links.for_url("https://github.com/yelrom0/udl/pull/33")) == 2
    assert await links.delete_for("udl", OwnerKind.TASK, "1.1") == 1
    assert [
        link.project for link in await links.for_url("https://github.com/yelrom0/udl/pull/33")
    ] == ["liively"]


async def test_a_feature_and_a_task_of_one_key_do_not_share_links(links: LinkRecords) -> None:
    feature = await links.add(
        project="udl",
        owner_kind=OwnerKind.FEATURE,
        owner="26",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )
    task = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    assert feature.id != task.id
    assert len(await links.for_owner("udl", OwnerKind.FEATURE, "26")) == 1


async def test_a_link_is_got_by_its_id(links: LinkRecords) -> None:
    link = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    found = await links.get(link.id)
    assert found is not None
    assert found.id == link.id


async def test_deleting_things_that_hang_on_nobody_touches_nothing(links: LinkRecords) -> None:
    link = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )

    assert await links.delete_for("udl", OwnerKind.FEATURE, "26") == 0
    assert [
        found.id for found in await links.for_url("https://github.com/yelrom0/udl/pull/33")
    ] == [link.id]


async def test_a_deleted_task_takes_its_links(links: LinkRecords) -> None:
    await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
    )
    await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.3",
        kind="issue",
        url="https://github.com/yelrom0/udl/issues/7",
    )

    await links.delete_for("udl", OwnerKind.TASK, "26.2")

    assert [
        link.owner for link in await links.for_url("https://github.com/yelrom0/udl/pull/33")
    ] == []
    assert [
        link.owner for link in await links.for_url("https://github.com/yelrom0/udl/issues/7")
    ] == ["26.3"]


async def test_a_links_owner_can_be_a_feature_a_question_or_an_action(links: LinkRecords) -> None:
    from udl.plugins.apps.orch.models import OwnerKind

    for owner_kind, owner in [
        ("feature", "26"),
        ("task", "26.2"),
        ("question", "Q-7"),
        ("action", "A-3"),
    ]:
        await links.add(
            project="udl",
            owner_kind=OwnerKind(owner_kind),
            owner=owner,
            kind="url",
            url=f"https://example.com/{owner_kind}/{owner}",
        )

    assert len(await links.for_owner("udl", OwnerKind.FEATURE, "26")) == 1
    assert len(await links.for_owner("udl", OwnerKind.TASK, "26.2")) == 1
    assert len(await links.for_owner("udl", OwnerKind.QUESTION, "Q-7")) == 1
    assert len(await links.for_owner("udl", OwnerKind.ACTION, "A-3")) == 1


async def test_an_internal_kind_has_a_udl_url(links: LinkRecords) -> None:
    link = await links.add(
        project="udl",
        owner_kind=OwnerKind.FEATURE,
        owner="26",
        kind="task",
        url="udl:task/udl/26.2",
    )

    assert link.url == "udl:task/udl/26.2"
    assert [found.owner for found in await links.for_url("udl:task/udl/26.2")] == ["26"]


async def test_a_link_to_something_that_is_not_there_is_refused(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a GitHub URL"):
        await links.add(
            project="udl", owner_kind=OwnerKind.TASK, owner="26.2", kind="pull", url="not a url"
        )


async def test_a_link_with_no_url_is_refused(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a URL"):
        await links.add(project="udl", owner_kind=OwnerKind.TASK, owner="26.2", kind="url", url="")


async def test_a_url_link_must_be_a_url(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a URL"):
        await links.add(
            project="udl", owner_kind=OwnerKind.TASK, owner="26.2", kind="url", url="not a url"
        )


# ---------------------------------------------------------------------------------------------
# Text scanned on save: a PR URL or a task key in backticks becomes a link
# ---------------------------------------------------------------------------------------------


async def test_a_github_url_in_a_spec_becomes_a_link() -> None:

    found = links_in_proj("The PR is https://github.com/yelrom0/udl/pull/33, merged.")

    assert [(link.link_kind, link.url) for link in found] == [
        ("pull", "https://github.com/yelrom0/udl/pull/33")
    ]


async def test_a_task_key_in_backticks_becomes_a_link() -> None:

    found = links_in_proj("Needs `26.2` and `22.4` done first.")

    assert [(link.link_kind, link.url) for link in found] == [
        ("task", "udl:task/udl/26.2"),
        ("task", "udl:task/udl/22.4"),
    ]


@pytest.mark.parametrize("key", ["F-1.1", "PROJ-1.4", "26.2", "26.12", "18.7"])
def test_a_task_key_is_a_counter_s_key_or_a_plan_s(key: str) -> None:

    found = links_in_proj(f"Needs `{key}` first.")

    assert [link.url for link in found] == [f"udl:task/udl/{key}"]


@pytest.mark.parametrize(
    "key",
    # (A version: one digit before the dot, three parts, or a part with a leading zero.)
    ["v1.2.3", "python3.14", "3.14", "0.5", "1.2.3", "10.2.3", "20.04", "26.2abc", "F-1"],
)
def test_what_is_not_a_task_key(key: str) -> None:

    found = links_in_proj(f"A `{key}` is not a task.")

    assert found == []


async def test_text_with_nothing_to_link_has_no_links() -> None:

    assert links_in_proj("Nothing to link here.") == []


async def test_the_same_url_twice_is_one_link() -> None:

    found = links_in_proj("See https://github.com/yelrom0/udl/pull/33 and again /33.")

    assert len(found) == 1


def test_a_sentence_punctuation_is_not_part_of_the_url() -> None:

    found = links_in_proj("See (https://github.com/yelrom0/udl/pull/33).")

    assert [link.url for link in found] == ["https://github.com/yelrom0/udl/pull/33"]


@pytest.mark.parametrize(
    "words",
    [
        {"state": "closed!"},
        {"checks": "ok"},
        {"review": "fine"},
        {"state": "merged", "checks": "pass", "review": "approved"},
    ],
)
def test_a_pull_s_words_are_the_ones_a_link_keeps(words: dict[str, str]) -> None:
    # (Three words, each one of a few: a word outside them is refused when the link is stored,
    # not kept as a string that reads as nothing.)
    if words.get("state") == "merged":
        assert PullState(**words).model_dump() == {
            "state": "merged",
            "head_sha": "",
            "checks": "pass",
            "review": "approved",
        }
    else:
        with pytest.raises(ValidationError):
            PullState(**words)


# ---------------------------------------------------------------------------------------------
# A pull's state is refreshed on github.pr.*, and orch.link.changed is published
# ---------------------------------------------------------------------------------------------


class FakeGithub:
    """What orch's service needs of github_get_pull: a service whose call answers the pull's
    detail (what the operation answers, as the hub's service has it)."""

    def __init__(self, detail: PullDetail) -> None:
        self.detail = detail
        self.calls: list[tuple[str, int]] = []

    async def call(self, name: str, request: BaseModel | Mapping[str, object]) -> PullDetail:
        repo, number = _repo_and_number(request)
        self.calls.append((repo, number))
        return self.detail


def _repo_and_number(request: BaseModel | Mapping[str, object]) -> tuple[str, int]:
    """A `github_get_pull` request's repository and number (a model's or a dict's)."""
    repo = _field(request, "repo")
    number = _field(request, "number")
    try:
        digits = int(str(number))
    except ValueError:
        digits = 0
    return str(repo), digits


def _field(request: BaseModel | Mapping[str, object], name: str) -> object:
    """A request's field (a model's or a dict's)."""
    if isinstance(request, BaseModel):
        return getattr(request, name, "")
    return request.get(name, "")


async def test_a_pull_link_is_refreshed_on_a_github_pr_event() -> None:
    from udl.plugins.apps.orch.links import refresh_pull

    link = Link(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )
    github = FakeGithub(
        PullDetail(
            state="merged",
            head_sha="abc",
            checks=[_FakeCheck(name="check", status="completed", conclusion="success")],
        )
    )

    refreshed = await refresh_pull(link, github=github)

    assert refreshed.state.state == "merged"
    assert refreshed.state.checks == "pass"
    # (udl's github_get_pull has no reviews: `review` is `none` until it does — O-C5.)
    assert refreshed.state.review == "none"
    assert github.calls == [("yelrom0/udl", 33)]


async def test_a_link_that_is_not_a_pull_is_left_alone() -> None:
    from udl.plugins.apps.orch.links import refresh_pull

    link = Link(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        link_kind="issue",
        url="https://github.com/yelrom0/udl/issues/7",
    )
    github = FakeGithub(PullDetail(state="open"))

    assert await refresh_pull(link, github=github) == link
    assert github.calls == []


async def test_a_pull_link_with_a_url_that_is_not_a_pulls_is_left_alone() -> None:
    from udl.plugins.apps.orch.links import refresh_pull

    link = Link(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )
    other = link.model_copy(update={"url": "https://github.com/yelrom0/udl/issues/7"})
    github = FakeGithub(PullDetail(state="merged"))

    assert await refresh_pull(other, github=github) == other
    assert github.calls == []


async def test_a_pull_link_is_refreshed_only_when_its_url_matches() -> None:
    from udl.plugins.apps.orch.links import on_github_pr

    link = Link(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )
    github = FakeGithub(PullDetail(state="closed"))

    changed = await on_github_pr(
        [link],
        event={
            "action": "closed",
            "pull_request": {"html_url": "https://github.com/yelrom0/udl/pull/33"},
        },
        github=github,
    )

    assert [link.state.state for link in changed] == ["closed"]
    assert github.calls == [("yelrom0/udl", 33)]


async def test_a_github_pr_event_with_no_url_changes_nothing() -> None:
    from udl.plugins.apps.orch.links import on_github_pr

    link = Link(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )
    github = FakeGithub(PullDetail(state="merged"))

    assert await on_github_pr([link], event={"action": "closed"}, github=github) == []
    assert github.calls == []


async def test_a_github_pr_event_for_another_pr_changes_nothing() -> None:
    from udl.plugins.apps.orch.links import on_github_pr

    link = Link(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        link_kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )
    github = FakeGithub(PullDetail(state="merged"))

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

    assert [
        (link.link_kind, link.url)
        for link in await links.for_owner("udl", OwnerKind.TASK, task.key)
    ] == [("pull", "https://github.com/yelrom0/udl/pull/33")]


async def test_a_task_key_in_a_spec_is_a_link_in_its_project(links: LinkRecords) -> None:
    records = await _task_records(links)

    task = await records.create_task("udl", "F-1", "The task", spec="Needs `26.2` first.")

    assert [link.url for link in await links.for_owner("udl", OwnerKind.TASK, task.key)] == [
        "udl:task/udl/26.2"
    ]


async def test_a_task_with_nothing_to_link_has_none(links: LinkRecords) -> None:
    records = await _task_records(links)

    await records.create_task("udl", "F-1", "The task", spec="Nothing to link.")

    assert await links.for_owner("udl", OwnerKind.TASK, "F-1.1") == []


async def _task_records(links: LinkRecords) -> TaskRecords:
    """orch's tasks over the same records, with a feature to hang them on."""
    from udl.plugins.apps.orch.records import Feature

    records = TaskRecords(links.records)
    await FeatureRecords(links.records).save_feature(Feature(project="udl", key="F-1", title="f"))
    return records


# ---------------------------------------------------------------------------------------------
# The service: orch publishes orch.link.changed
# ---------------------------------------------------------------------------------------------


async def test_a_url_link_needs_a_page(links: LinkRecords) -> None:
    with pytest.raises(ValueError, match="needs a URL"):
        await links.add(
            project="udl", owner_kind=OwnerKind.TASK, owner="26.2", kind="url", url="not a url"
        )


async def test_a_pull_link_that_is_not_a_pull_is_left_alone(links: LinkRecords) -> None:
    link = await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/issues/7",
    )

    assert await links.refresh(link, github=FakeGithub(PullDetail(state="open"))) == link


async def test_deleting_a_link_that_is_not_there_is_false(links: LinkRecords) -> None:
    link = Link(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        link_kind="url",
        url="https://x.dev/",
    )

    assert await links.delete(link) is False


async def test_the_service_leaves_a_non_pull_link_alone(links: LinkRecords) -> None:
    """A `github.pr.*` event for a link that is not a pull changes nothing and publishes
    nothing (the loop's own branch)."""
    from udl.plugins.apps.orch.plugin import Orch
    from udl.plugins.apps.orch.settings import OrchSettings

    await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="issue",
        url="https://github.com/yelrom0/udl/issues/7",
    )
    sink = _Sink()
    app = Orch(OrchSettings(), clock=SystemClock(), records=links.records)

    await app.follow(
        links,
        sink,
        FakeGithub(PullDetail(state="open")),
        _OneEvent({"html_url": "https://github.com/yelrom0/udl/issues/7"}),
        _waiting(),
    )

    assert sink.emitted == []


async def test_a_github_pr_event_with_no_url_is_ignored(links: LinkRecords) -> None:
    """An event with no pull page (a null pull request) changes nothing (the loop's guard)."""
    from udl.plugins.apps.orch.plugin import Orch
    from udl.plugins.apps.orch.settings import OrchSettings

    sink = _Sink()
    app = Orch(OrchSettings(), clock=SystemClock(), records=links.records)

    await app.follow(
        links, sink, FakeGithub(PullDetail()), _OneEvent({"action": "closed"}), _waiting()
    )

    assert sink.emitted == []


async def test_a_stopped_service_ends_before_the_next_event(links: LinkRecords) -> None:
    """When `stop` is set while an event is waited for, the loop ends (not the subscription's
    end, which is the other way out)."""
    from udl.plugins.apps.orch.plugin import Orch
    from udl.plugins.apps.orch.settings import OrchSettings

    sink = _Sink()
    app = Orch(OrchSettings(), clock=SystemClock(), records=links.records)
    waiting: asyncio.Future[object] = asyncio.get_running_loop().create_future()
    waiting.set_result(None)

    await app.follow(links, sink, FakeGithub(PullDetail()), _NoEvents(), waiting)

    assert sink.emitted == []


async def test_a_pull_that_cannot_be_read_is_said_and_the_loop_goes_on(
    links: LinkRecords,
) -> None:
    """A pull that has gone (or one udl cannot read) is logged, and the loop keeps going."""
    from udl.errors import DoesNotExist
    from udl.plugins.apps.orch.plugin import Orch
    from udl.plugins.apps.orch.settings import OrchSettings

    class Gone:
        """A service whose `github_get_pull` answers that the pull is not there."""

        async def call(self, name: str, request: BaseModel | Mapping[str, object]) -> BaseModel:
            raise DoesNotExist("There is no pull 33 in yelrom0/udl")

    await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )
    sink = _Sink()
    app = Orch(OrchSettings(), clock=SystemClock(), records=links.records)

    await app.follow(
        links,
        sink,
        Gone(),
        _OneEvent({"html_url": "https://github.com/yelrom0/udl/pull/33"}),
        _waiting(),
    )

    assert sink.emitted == []


async def test_the_scan_drops_a_link_the_text_no_longer_has(links: LinkRecords) -> None:
    """A spec that no longer names a PR loses its link (a scan's own, not one a person added)."""
    records = await _task_records(links)
    task = await records.create_task(
        "udl", "F-1", "The task", spec="The PR is https://github.com/yelrom0/udl/pull/33."
    )
    await records.save_task(task.model_copy(update={"spec": "Nothing to link."}))

    assert await links.for_owner("udl", OwnerKind.TASK, task.key) == []


async def test_a_scan_never_breaks_the_save(links: LinkRecords) -> None:
    """A text a scan cannot read is saved anyway (the scan says so and goes on)."""
    records = await _task_records(links)

    task = await records.create_task("udl", "F-1", "The task", spec="\x00 bad text")

    assert task.spec == "\x00 bad text"


async def test_the_link_changed_event_is_one_of_orchs() -> None:
    from udl.plugins.apps.orch.plugin import Orch

    names = {event_type.name for event_type in Orch.event_types()}

    assert "orch.link.changed" in names


async def test_the_service_refreshes_a_pull_link_and_publishes(
    links: LinkRecords,
) -> None:
    """The service's own loop (26.4): a `github.pr.*` event refreshes the link and the app
    publishes `orch.link.changed` (here: the sink it was given)."""
    from udl.plugins.apps.orch.plugin import Orch
    from udl.plugins.apps.orch.settings import OrchSettings

    await links.add(
        project="udl",
        owner_kind=OwnerKind.TASK,
        owner="26.2",
        kind="pull",
        url="https://github.com/yelrom0/udl/pull/33",
        state=PullState(state="open"),
    )
    github = FakeGithub(PullDetail(state="merged", head_sha="abc"))
    sink = _Sink()
    app = Orch(OrchSettings(), clock=SystemClock(), records=links.records)

    await app.follow(
        links,
        sink,
        github,
        _OneEvent({"html_url": "https://github.com/yelrom0/udl/pull/33"}),
        _waiting(),
    )

    assert [
        link.state.state for link in await links.for_url("https://github.com/yelrom0/udl/pull/33")
    ] == ["merged"]
    assert [(name, getattr(data, "url", "")) for name, data in sink.emitted] == [
        ("orch.link.changed", "https://github.com/yelrom0/udl/pull/33")
    ]


class _NoEvents:
    """A subscription that never ends (a runtime that has not closed it)."""

    async def __anext__(self) -> AppEvent:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")  # pragma: no cover


class _Sink:
    """What an app publishes, kept (what the loop's `orch.link.changed` goes to)."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, object]] = []

    async def emit(
        self,
        event_type: str,
        data: object,
        *,
        dedupe_key: str,
        occurred_at: object = None,
    ) -> bool:
        self.emitted.append((event_type, data))
        return True


def _waiting() -> asyncio.Future[object]:
    """What the loop waits on for a stop: a future no test completes (the loop ends with the
    subscription, which is what a runtime closing it does)."""
    waiting: asyncio.Future[object] = asyncio.get_running_loop().create_future()
    return waiting


class _OneEvent:
    """A subscription of one event, then the end (what a service's loop reads)."""

    def __init__(self, data: JsonObject) -> None:
        self._data: JsonObject | None = data

    async def __aenter__(self) -> _OneEvent:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def __aiter__(self) -> _OneEvent:
        return self

    async def __anext__(self) -> AppEvent:

        if self._data is None:
            raise StopAsyncIteration
        data, self._data = self._data, None
        return AppEvent(type="github.pr.opened", data=data, occurred_at=datetime(2026, 1, 1))


async def test_the_link_changed_event_describes_the_link() -> None:
    from udl.plugins.apps.orch.links import LINK_CHANGED

    event = LINK_CHANGED.data(
        owner_kind=OwnerKind.TASK, owner="26.2", url="https://github.com/yelrom0/udl/pull/33"
    )

    assert LINK_CHANGED.subject(event) == (
        "A link on task 26.2: https://github.com/yelrom0/udl/pull/33"
    )


async def test_a_link_changed_event_carries_the_links_owner_and_url() -> None:
    from udl.plugins.apps.orch.links import LinkChanged

    event = LinkChanged(
        owner_kind=OwnerKind.TASK, owner="26.2", url="https://github.com/yelrom0/udl/pull/33"
    )

    assert event.model_dump()["owner"] == "26.2"
