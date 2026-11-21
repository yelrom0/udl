"""
------------------
- conftest.py -
------------------

The orch app's operations (26.6): a service over a fresh in-memory database, with one project,
its repo, a feature, two tasks and an agent, for the files that test what they answer
(test_projects_operations.py and the rest). A file that needs its own says so in its own
fixture.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import settings
from support.orch import orch_registry
from udl.core.service import UDLService
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import MergePolicy
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
    TaskRecords,
)
from udl.plugins.apps.orch.records import Task as TaskRecord


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service over a fresh in-memory database, with a project, its repo, a feature, two
    tasks and an agent."""
    async with UDLService(settings(tmp_path), registry=orch_registry()) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(
                name="udl",
                title="udl",
                description="What udl is.",
                merge=MergePolicy.HUMAN,
                review_bot="claude[bot]",
                rules="Never push to main.",
            )
        )
        await RepoRecords(records).save_repo(
            Repo(
                project="udl",
                name="udl",
                github="yelrom0/udl",
                account="github:yelrom0",
                checkouts={"local": "~/Documents/code/udl"},
            )
        )
        await FeatureRecords(records).save_feature(
            Feature(project="udl", key="26", title="Work tracking", spec="Phase 26.", order=26)
        )
        for key, title, spec in (
            ("26.1", "Projects and repos", "The plan's 26.1 card."),
            ("26.2", "Features and tasks", "The plan's 26.2 card."),
        ):
            await TaskRecords(records).save_task(
                TaskRecord(project="udl", feature="26", key=key, title=title, spec=spec)
            )
        await AgentRecords(records).save_agent(
            Agent(name="deepseek-a", title="deepseek-a", runner="fake")
        )
        yield running
