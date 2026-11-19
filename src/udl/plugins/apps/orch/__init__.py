"""
------------------------------------
- orch (the orchestrator app) -
------------------------------------

The orchestrator's data (Phase 26): projects and their repos, features and their tasks, links
and notes. The kinds are udl records (orch.records), and the shapes they are stored in are
orch.models.

Nothing here reaches outside udl: a project is a body of work, and the rules an agent on it is
given are the project's own text.
"""

# Local Imports
from udl.plugins.apps.orch.models import BranchPattern, MergePolicy
from udl.plugins.apps.orch.records import (
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
)

__all__ = [
    "BranchPattern",
    "MergePolicy",
    "Project",
    "ProjectRecords",
    "Repo",
    "RepoRecords",
]
