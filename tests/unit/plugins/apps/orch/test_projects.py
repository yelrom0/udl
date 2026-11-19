"""
------------------------------
- test_projects.py -
------------------------------

Projects and repos (udl.plugins.apps.orch.models): what a Project says (its merge policy, its
promotion branches, its review bot and the rules every agent on it is given), what a Repo says
(GitHub, its account, its base branch, the gate argv, the checkouts, the branch pattern), and
what each of them refuses. Stored, they are udl records (orch.records).
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest
from pydantic import BaseModel, ValidationError

from udl.config import Settings
from udl.db.records import Record
from udl.errors import DoesNotExist, DuplicateKey
from udl.plugins.apps.base import AppRecords

# Local Imports
from udl.plugins.apps.orch import models as data
from udl.plugins.apps.orch.models import DEFAULT_BRANCH_PATTERN, MergePolicy
from udl.plugins.apps.orch.models import Project as ProjectData
from udl.plugins.apps.orch.models import Repo as RepoData
from udl.plugins.apps.orch.records import Counter as CounterRecord
from udl.plugins.apps.orch.records import Feature as FeatureRecord
from udl.plugins.apps.orch.records import Project as ProjectRecord
from udl.plugins.apps.orch.records import ProjectRecords, RepoRecords
from udl.plugins.apps.orch.records import Repo as RepoRecord
from udl.plugins.apps.orch.records import Task as TaskRecord

# ---------------------------------------------------------------------------------------------
# A project
# ---------------------------------------------------------------------------------------------


def test_a_project_holds_its_name_title_and_description() -> None:
    project = ProjectData(name="udl", title="udl", description="What we are building.")

    assert (project.name, project.title) == ("udl", "udl")
    assert project.description == "What we are building."
    # (A human merges, unless a project says otherwise: O-D4.)
    assert project.merge is MergePolicy.HUMAN
    assert project.promotion == []
    assert project.review_bot is None
    assert project.rules == ""


def test_a_project_needs_a_name() -> None:
    with pytest.raises(ValidationError):
        ProjectData.model_validate({"title": "udl"})


@pytest.mark.parametrize("name", ["", "has space", "a/b", "-nope", "x" * 101])
def test_a_projects_name_is_refused_when_it_cannot_name_one(name: str) -> None:
    with pytest.raises(ValidationError):
        ProjectData(name=name, title="x")


def test_a_projects_merge_policy_is_human_or_agent() -> None:
    assert (
        ProjectData(name="liively", title="Liively", merge="agent").merge  # pyright: ignore[reportArgumentType]
        is MergePolicy.AGENT
    )
    with pytest.raises(ValidationError):
        ProjectData(name="x", title="x", merge="robot")  # pyright: ignore[reportArgumentType]


def test_a_projects_promotion_branches_are_branches_after_the_base_in_order() -> None:
    project = ProjectData(name="liively", title="Liively", promotion=["rc", "main"])

    assert project.promotion == ["rc", "main"]
    assert [str(branch) for branch in project.promotion] == ["rc", "main"]


def test_a_project_names_its_review_bot_and_the_rules_agents_get() -> None:
    project = ProjectData(
        name="udl",
        title="udl",
        review_bot="claude[bot]",
        rules="Never push to main.",
    )

    assert project.review_bot == "claude[bot]"
    assert project.rules == "Never push to main."


# ---------------------------------------------------------------------------------------------
# A repo
# ---------------------------------------------------------------------------------------------


def repo(**fields: object) -> RepoData:
    """A repo of udl, with `fields` replacing what is set (its identity included)."""
    given: dict[str, object] = {
        "project": "udl",
        "name": "udl",
        "github": "yelrom0/udl",
        "account": "github:yelrom0",
        "base_branch": "main",
        **fields,
    }
    return RepoData.model_validate(given)


def test_a_repo_says_which_project_it_is_and_where_it_lives() -> None:
    found = repo()

    assert (found.project, found.name, found.github) == ("udl", "udl", "yelrom0/udl")
    assert (found.account, found.base_branch) == ("github:yelrom0", "main")
    assert found.gate == ["make", "check"]
    # (A repo says where its checkout is: udl does not guess it (the card gives no default).)
    assert found.checkouts == {}
    assert found.branch_pattern == DEFAULT_BRANCH_PATTERN


def test_a_repos_gate_is_an_argv() -> None:
    assert RepoData.model_validate(repo(gate=["uv", "run", "pytest"]).model_dump()).gate == [
        "uv",
        "run",
        "pytest",
    ]
    with pytest.raises(ValidationError):
        RepoRecord.model_validate(repo(gate=["make check"]).model_dump())


def test_a_repos_checkouts_name_a_host_and_a_path_on_it() -> None:
    found = repo(checkouts={"local": "/srv/udl", "thevault": "/home/x/udl"})

    assert found.checkouts == {"local": "/srv/udl", "thevault": "/home/x/udl"}


@pytest.mark.parametrize("github", ["", "udl", "/udl", "yelrom0/", "a/b/c"])
def test_a_repos_github_is_owner_and_repo(github: str) -> None:
    with pytest.raises(ValidationError):
        repo(github=github)


def test_a_repos_account_is_one_of_a_github_accounts() -> None:
    with pytest.raises(ValidationError):
        repo(account="yelrom0")


def test_a_repos_branch_pattern_is_the_blocks_agents_fill_in() -> None:
    found = repo(branch_pattern="{type}/{key}-{slug}")

    assert found.branch(type="feat", key="26.2", slug="projects") == "feat/26.2-projects"
    # (The slug is normalised: it is what a branch can be named.)
    assert found.branch(type="feat", key="26.2", slug="Projects & repos!") == (
        "feat/26.2-projects-repos"
    )


def test_a_repos_branch_pattern_may_not_rename_the_parts() -> None:
    with pytest.raises(ValidationError):
        repo(branch_pattern="{type}/{name}-{slug}")
    with pytest.raises(ValidationError):
        repo(branch_pattern="feat/{key}-{slug}/{extra}")


def test_a_repos_branch_pattern_may_not_nest() -> None:
    with pytest.raises(ValidationError):
        RepoRecord.model_validate(repo(branch_pattern="feat/{key}-{slug}/x").model_dump())


def test_a_repos_branch_pattern_may_not_start_or_end_with_a_slash() -> None:
    with pytest.raises(ValidationError):
        repo(branch_pattern="/feat/{key}-{slug}")
    with pytest.raises(ValidationError):
        repo(branch_pattern="feat/{key}-{slug}/")


# ---------------------------------------------------------------------------------------------
# Examples, as the record contract stores them
# ---------------------------------------------------------------------------------------------


# (The record kinds' examples are what the record contract stores in every database; here we
# check that the shapes and the records say the same thing about the fields they share.)
@pytest.mark.parametrize("model", [data.Project, data.Repo, data.Feature, data.Task])
def test_a_shape_and_its_record_agree_on_every_field(model: type[BaseModel]) -> None:
    # (The same fields, and the same type for each: the record is not looser than the shape.)
    record = record_model(model)

    assert set(record.model_fields) >= set(model.model_fields)
    for name, field in model.model_fields.items():
        assert record.model_fields[name].annotation == field.annotation, name


def test_a_repos_example_says_where_its_checkouts_are() -> None:
    example = RepoRecord.example()

    assert example.checkouts
    assert example.gate
    assert example.branch_pattern == DEFAULT_BRANCH_PATTERN


def record_model(model: type[BaseModel]) -> type[Record]:
    """The record kind whose fields are `model`'s."""
    by_shape: dict[type[BaseModel], type[Record]] = {
        data.Project: ProjectRecord,
        data.Repo: RepoRecord,
        data.Feature: FeatureRecord,
        data.Task: TaskRecord,
    }
    return by_shape[model]


# ---------------------------------------------------------------------------------------------
# Storing them
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def records(tmp_path: Path) -> ProjectRecords:
    """Orch's records over a fresh in-memory database (as tests/support/engine.py makes one)."""
    from support.engine import databases, registry, settings

    plugins = registry()
    config: Settings = settings(tmp_path)
    db = databases(config, plugins)
    await db.connect()
    return ProjectRecords(AppRecords(db))


@pytest.fixture
async def repos(records: ProjectRecords) -> RepoRecords:
    """Orch's repos, over the same records (so a repo's project is the one saved above)."""
    return RepoRecords(records.records, records)


async def test_a_saved_project_is_read_back(records: ProjectRecords) -> None:
    project = ProjectRecord(name="udl", title="udl", rules="Never push to main.")
    saved = await records.save_project(project)

    # (The stored one is stamped with when it was made and last changed; the caller's is not.)
    assert await records.project("udl") == saved
    assert saved.updated_at >= project.created_at
    assert [found.name for found in await records.projects()] == ["udl"]
    assert await records.project("liively") is None


async def test_a_second_project_with_the_same_name_is_refused(records: ProjectRecords) -> None:
    await records.save_project(ProjectRecord(name="udl", title="udl"))

    with pytest.raises(DuplicateKey):
        await records.save_project(ProjectRecord(name="udl", title="again"))


async def test_a_saved_repo_is_read_back(records: ProjectRecords, repos: RepoRecords) -> None:
    await records.save_project(ProjectRecord(name="udl", title="udl"))
    found = repo()
    await repos.save_repo(RepoRecord.model_validate(found.model_dump()))

    stored = await repos.repo("udl", "udl")
    assert stored is not None
    assert stored.model_dump(exclude={"id", "created_at", "updated_at"}) == found.model_dump()
    assert [item.name for item in await repos.repos("udl")] == ["udl"]
    assert await repos.repos_for("yelrom0/udl") == [stored]
    assert await repos.repo("udl", "other") is None


async def test_a_repo_of_a_project_that_is_not_there_is_refused(
    records: ProjectRecords, repos: RepoRecords
) -> None:
    with pytest.raises(DoesNotExist):
        await repos.save_repo(RepoRecord.model_validate(repo().model_dump()))


async def test_two_repos_of_one_project_may_not_share_a_name(
    records: ProjectRecords, repos: RepoRecords
) -> None:
    await records.save_project(ProjectRecord(name="udl", title="udl"))
    await repos.save_repo(RepoRecord.model_validate(repo().model_dump()))

    with pytest.raises(DuplicateKey):
        await repos.save_repo(RepoRecord.model_validate(repo(github="yelrom0/other").model_dump()))


async def test_two_projects_each_have_a_repo_of_the_same_name(
    records: ProjectRecords, repos: RepoRecords
) -> None:
    await records.save_project(ProjectRecord(name="udl", title="udl"))
    await records.save_project(ProjectRecord(name="liively", title="Liively"))
    await repos.save_repo(RepoRecord.model_validate(repo().model_dump()))
    await repos.save_repo(RepoRecord.model_validate(repo(project="liively").model_dump()))

    assert (await repos.repo("liively", "udl")) is not None
    assert (await repos.repo("liively", "udl")).name == "udl"  # pyright: ignore[reportOptionalMemberAccess]
    assert len(await repos.repos("udl")) == 1


async def test_deleting_a_project_deletes_its_repos(
    records: ProjectRecords, repos: RepoRecords
) -> None:
    await records.save_project(ProjectRecord(name="udl", title="udl"))
    await repos.save_repo(RepoRecord.model_validate(repo().model_dump()))

    assert await records.delete_project("udl") is True
    assert await records.delete_project("udl") is False
    assert await repos.repos("udl") == []


async def test_deleting_a_repo(records: ProjectRecords, repos: RepoRecords) -> None:
    await records.save_project(ProjectRecord(name="udl", title="udl"))
    await repos.save_repo(RepoRecord.model_validate(repo().model_dump()))

    assert await repos.delete_repo("udl", "udl") is True
    assert await repos.delete_repo("udl", "udl") is False
    assert await repos.repos("udl") == []


async def test_a_repos_checkouts_are_kept(records: ProjectRecords, repos: RepoRecords) -> None:
    await records.save_project(ProjectRecord(name="udl", title="udl"))
    found = repo(checkouts={"local": "/srv/udl", "thevault": "/srv/udl"})
    await repos.save_repo(RepoRecord.model_validate(found.model_dump()))

    again = await repos.repo("udl", "udl")
    assert again is not None
    assert again.checkouts == found.checkouts


async def test_a_saved_project_keeps_its_rules_and_policy(
    records: ProjectRecords,
) -> None:
    project = ProjectRecord(
        name="liively",
        title="Liively",
        description="The site builder.",
        merge=MergePolicy.AGENT,
        promotion=["rc", "main"],
        review_bot="claude[bot]",
        rules="Never push to main.",
    )
    await records.save_project(project)

    assert await records.project("liively") is not None
    assert (await records.project("liively")).model_dump(  # pyright: ignore[reportOptionalMemberAccess]
        exclude={"id", "created_at", "updated_at"}
    ) == project.model_dump(exclude={"id", "created_at", "updated_at"})


# ---------------------------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------------------------


def test_the_orch_app_brings_its_records() -> None:
    from udl.plugins.apps.orch.plugin import Orch

    assert Orch.name == "orch"
    assert Orch.title
    assert Orch.summary
    assert set(Orch.record_kinds()) == {
        ProjectRecord,
        RepoRecord,
        FeatureRecord,
        TaskRecord,
        CounterRecord,
    }


def test_the_orch_app_has_settings_of_its_own() -> None:
    from udl.plugins.apps.base import AppSettings
    from udl.plugins.apps.orch.settings import OrchSettings

    assert issubclass(OrchSettings, AppSettings)


# ---------------------------------------------------------------------------------------------
# The rules the records make of their own fields
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"github": "udl"}, "String should match pattern"),
        ({"github": "yelrom0/"}, "String should match pattern"),
        ({"github": "a b/c"}, "String should match pattern"),
        ({"github": "o/r!"}, "String should match pattern"),
        ({"account": "github:"}, "String should match pattern"),
        ({"account": "yelrom0"}, "String should match pattern"),
        ({"account": "slack:x"}, "String should match pattern"),
        ({"account": "github:yelrom0/x"}, "String should match pattern"),
        ({"base_branch": ""}, "String should match pattern"),
        ({"base_branch": "feat/x y"}, "String should match pattern"),
        ({"base_branch": "feat/x~1"}, "String should match pattern"),
        ({"base_branch": "feat/x^"}, "String should match pattern"),
        ({"branch_pattern": "{type}/{key}-x y-{slug}"}, "no spaces"),
        ({"branch_pattern": "feat/{name}/x"}, "may only name"),
        ({"branch_pattern": "{type}/{type}"}, "twice"),
        ({"checkouts": {"not a host": "/srv/udl"}}, "String should match pattern"),
    ],
)
def test_a_record_refuses_what_the_shape_refuses(fields: dict[str, str], why: str) -> None:
    given = {
        "project": "udl",
        "name": "udl",
        "github": "yelrom0/udl",
        "account": "github:x",
        **fields,
    }
    with pytest.raises(ValidationError, match=why):
        RepoRecord.model_validate(given)


def test_a_records_branch_pattern_is_checked_as_the_shapes_is() -> None:
    # (A dict, not a shape: the shape would refuse it first, and the record must refuse it too.)
    given = {
        "project": "udl",
        "name": "udl",
        "github": "yelrom0/udl",
        "account": "github:x",
        "branch_pattern": "feat/{key}-{slug}/x",
    }
    with pytest.raises(ValidationError, match="branch pattern"):
        RepoRecord.model_validate(given)


def test_a_repos_gate_may_not_be_empty() -> None:
    with pytest.raises(ValidationError):
        RepoRecord.model_validate(repo(gate=[]).model_dump())


@pytest.mark.parametrize("pattern", ["{type}/{key}/{slug}/x", "x/{type}/{key}-{slug}"])
def test_a_repos_branch_pattern_is_the_parts_and_nothing_around_them(pattern: str) -> None:
    with pytest.raises(ValidationError, match="branch pattern"):
        RepoRecord.model_validate(repo(branch_pattern=pattern).model_dump())


@pytest.mark.parametrize(
    "pattern",
    ["{type}/{key}-{slug}.lock", "{type}/x/.lock-{key}-{slug}", "{type}/.", "{type}/.."],
)
def test_a_repos_branch_pattern_may_not_hold_what_a_branch_may_not(pattern: str) -> None:
    with pytest.raises(ValidationError, match="branch pattern"):
        RepoRecord.model_validate(repo(branch_pattern=pattern).model_dump())


def test_a_repos_branch_pattern_with_bad_braces_is_refused() -> None:
    with pytest.raises(ValidationError, match="braces do not match"):
        RepoRecord.model_validate(repo(branch_pattern="{type/{key}-{slug}").model_dump())


def test_a_repos_branch_pattern_naming_no_part_is_refused() -> None:
    with pytest.raises(ValidationError, match="names none"):
        RepoRecord.model_validate(repo(branch_pattern="feat/x-y").model_dump())


def test_a_repos_branch_pattern_naming_an_unknown_part_is_refused() -> None:
    with pytest.raises(ValidationError, match="may only name"):
        RepoRecord.model_validate(repo(branch_pattern="feat/x-{y}").model_dump())


def test_a_repos_branch_pattern_naming_a_part_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="twice"):
        RepoRecord.model_validate(repo(branch_pattern="{type}/{type}-{key}-{slug}").model_dump())


def test_a_repos_branch_pattern_with_spaces_is_refused() -> None:
    with pytest.raises(ValidationError, match="no spaces"):
        RepoRecord.model_validate(repo(branch_pattern="{type}/feat x-{key}-{slug}").model_dump())


def test_a_repos_branch_pattern_that_starts_or_ends_with_a_dot_is_refused() -> None:
    with pytest.raises(ValidationError, match="text before its first part"):
        RepoRecord.model_validate(repo(branch_pattern=".{type}/{key}-{slug}").model_dump())
    with pytest.raises(ValidationError, match=r"start or end with / or \."):
        RepoRecord.model_validate(repo(branch_pattern="{type}/{key}-.").model_dump())


def test_a_repos_branch_pattern_with_an_empty_part_is_refused() -> None:
    with pytest.raises(ValidationError, match="empty part"):
        RepoRecord.model_validate(repo(branch_pattern="{type}//{key}-{slug}").model_dump())


def test_a_repos_branch_pattern_with_at_braces_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"starting with \. or @"):
        RepoRecord.model_validate(repo(branch_pattern="{type}/@{key}-{slug}").model_dump())
    with pytest.raises(ValidationError, match="no empty part"):
        RepoRecord.model_validate(repo(branch_pattern="{type}//{key}-{slug}").model_dump())


def test_a_repos_branch_pattern_may_not_have_a_hidden_part() -> None:
    with pytest.raises(ValidationError, match=r"starting with \. or @"):
        RepoRecord.model_validate(repo(branch_pattern="{type}/x/.hidden-{key}").model_dump())


def test_a_repos_branch_pattern_may_not_have_two_dots_in_a_part() -> None:
    with pytest.raises(ValidationError, match=r"with \.\. in it"):
        RepoRecord.model_validate(repo(branch_pattern="{type}/{key}..{slug}").model_dump())


def test_a_repos_branch_pattern_may_have_a_part_that_only_starts_like_a_lock() -> None:
    found = repo(branch_pattern="{type}/x.lock-{key}-{slug}")

    assert found.branch(type="feat", key="26.2", slug="projects") == "feat/x.lock-26.2-projects"


@pytest.mark.parametrize(
    "pattern",
    ["{type}/{key}.v2-{slug}", "{type}/a.{key}.{slug}", "{type}/{key}@x-{slug}"],
)
def test_a_repos_branch_pattern_may_have_text_after_a_part(pattern: str) -> None:
    found = repo(branch_pattern=pattern)

    assert found.branch(type="feat", key="26.2", slug="projects")


def test_a_projects_promotion_branches_are_all_different() -> None:
    with pytest.raises(ValidationError, match="twice"):
        ProjectData(name="liively", title="Liively", promotion=["rc", "rc"])
    with pytest.raises(ValidationError, match="twice"):
        ProjectRecord(name="liively", title="Liively", promotion=["rc", "rc"])


def test_a_projects_title_is_not_empty() -> None:
    with pytest.raises(ValidationError):
        ProjectData(name="x", title="")
    with pytest.raises(ValidationError):
        ProjectRecord(name="x", title="")
