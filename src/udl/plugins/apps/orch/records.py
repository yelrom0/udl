"""
--------------
- records.py -
--------------

The orchestrator's records: a Project (a body of work and its merge policy) and a Repo (a
GitHub repository of a project). Each is a udl record (udl.db.records), so every database plugin
stores it the same way, and the record contract round-trips its example.

A Project is unique by its name; a Repo by its project and its name together (two projects may
each have a repo called udl), and repos are also looked up by GitHub (`yelrom0/udl`), which is
how a webhook's event finds its repo.

The fields are typed with the same aliases orch.models uses (udl.plugins.apps.orch.models), so
what a shape refuses a record refuses too — one rule, in one place.

ProjectRecords and RepoRecords are the typed way to read and write them: an app holds an
AppRecords, and these sit over it (orch.plugin's service builds them), so nothing here knows
about a database.
"""

# System Imports
from typing import Self

# Package Imports
from pydantic import Field

# Local Imports
from udl.db.records import Name, Record, RecordQuery
from udl.errors import DoesNotExist
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import (
    BranchName,
    CheckedBranchPattern,
    Gate,
    GitHubAccount,
    GitHubSlug,
    HostName,
    MergePolicy,
    Promotion,
    Title,
)


class Project(Record):
    """A project: a body of work, its repos (Repo) and its merge policy.

    `promotion` names the branches after a repo's base, in order (["rc", "main"]); a project
    whose base is its last branch has none (udl), and its reviewer's merge is its review.
    `rules` is the markdown every agent on this project is given: the ground rules."""

    kind = "orch_project"
    indexed = ("name",)
    unique = (("name",),)

    name: Name
    title: Title
    description: str = ""
    merge: MergePolicy = MergePolicy.HUMAN
    """Who merges a ready PR (O-D3)."""
    promotion: Promotion = Field(default_factory=list)
    """The branches after the repo's base, in order; empty for udl."""
    review_bot: str | None = None
    """The review bot's login (e.g. claude[bot]), whose verdict a ready PR waits on."""
    rules: str = ""
    """The ground rules, as markdown, given to every agent on this project."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            name="udl",
            title="udl",
            description="What udl is: the local model server and the work it tracks.",
            merge=MergePolicy.HUMAN,
            review_bot="claude[bot]",
            rules="Never push to main.",
        )


class Repo(Record):
    """A repo of a project: its GitHub repository, its account, its base branch, its gate argv,
    its main checkout on each host, and how its task branches are named."""

    kind = "orch_repo"
    indexed = ("project", "name", "github")
    unique = (("project", "name"),)

    project: Name
    name: Name
    github: GitHubSlug
    """owner/repo, as GitHub names it."""
    account: GitHubAccount
    """The github:<name> account udl reaches it through (25.4)."""
    base_branch: BranchName = "main"
    """The branch PRs go into."""
    gate: Gate = Field(default_factory=lambda: ["make", "check"])
    """The argv that is its gate, run before a push (e.g. ["make", "check"])."""
    checkouts: dict[HostName, str] = Field(default_factory=dict)
    """Host name → the main checkout's path there (empty: udl does not know where it is yet)."""
    branch_pattern: CheckedBranchPattern = "{type}/{key}-{slug}"
    """How its task branches are named (23.1)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            project="udl",
            name="udl",
            github="yelrom0/udl",
            account="github:yelrom0",
            base_branch="main",
            gate=["make", "check"],
            checkouts={"local": "~/Documents/code/udl"},
            branch_pattern="{type}/{key}-{slug}",
        )


class ProjectRecords:
    """Where orch keeps its projects: over an app's records (udl.db.records), so they are
    stored as any other record is."""

    def __init__(self, records: AppRecords) -> None:
        self._records = records

    @property
    def records(self) -> AppRecords:
        """The records the projects are kept in (what a RepoRecords is made over too)."""
        return self._records

    async def require(self, name: str) -> Project:
        """The project called `name`, or DoesNotExist (what saving a repo of it needs)."""
        found = await self.project(name)
        if found is None:
            raise DoesNotExist(f"There is no project called {name!r}")
        return found

    async def save_project(self, project: Project) -> Project:
        """Store `project` (by its id, so storing one again replaces it). Raises DuplicateKey
        for another project with the same name."""
        return await self._records.save(project)

    async def project(self, name: str) -> Project | None:
        """The project called `name`, or None when there is none."""
        found = await self._records.list(Project, RecordQuery(where={"name": name}))
        return found[0] if found else None

    async def projects(self) -> list[Project]:
        """Every project, by name."""
        return sorted(await self._records.list(Project, RecordQuery()), key=lambda p: p.name)

    async def delete_project(self, name: str) -> bool:
        """Delete the project called `name` and its repos. Returns whether there was one."""
        project = await self.project(name)
        if project is None:
            return False
        for repo in await self._records.list(Repo, RecordQuery(where={"project": name})):
            await self._records.delete(Repo, repo.id)
        return await self._records.delete(Project, project.id)


class RepoRecords:
    """Where orch keeps a project's repos: looked up by project and name, and by GitHub."""

    def __init__(self, records: AppRecords, projects: ProjectRecords | None = None) -> None:
        self._records = records
        self._projects = projects if projects is not None else ProjectRecords(records)

    async def save_repo(self, repo: Repo) -> Repo:
        """Store `repo` (by its id, so storing one again replaces it). Raises DoesNotExist when
        its project is not there, and DuplicateKey when the project already has a repo of that
        name."""
        await self._projects.require(repo.project)
        return await self._records.save(repo)

    async def repo(self, project: str, name: str) -> Repo | None:
        """The repo called `name` of `project`, or None when there is none."""
        found = await self._records.list(
            Repo, RecordQuery(where={"project": project, "name": name})
        )
        return found[0] if found else None

    async def repos(self, project: str) -> list[Repo]:
        """Every repo of `project`, by name."""
        found = await self._records.list(Repo, RecordQuery(where={"project": project}))
        return sorted(found, key=lambda repo: repo.name)

    async def repos_for(self, github: str) -> list[Repo]:
        """Every repo that is the GitHub repository `github` (owner/repo), by project then name."""
        found = await self._records.list(Repo, RecordQuery(where={"github": github}))
        return sorted(found, key=lambda repo: (repo.project, repo.name))

    async def delete_repo(self, project: str, name: str) -> bool:
        """Delete the repo called `name` of `project`. Returns whether there was one."""
        repo = await self.repo(project, name)
        return repo is not None and await self._records.delete(Repo, repo.id)
