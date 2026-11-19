"""
------------------------------------
- orch (the orchestrator app) -
------------------------------------

The orchestrator's data (Phases 26-27): projects and their repos, features and their tasks,
links and notes, and the agents that work them with their sessions. The kinds are udl records
(orch.records), and the shapes they are stored in are orch.models and orch.agents.

Nothing here reaches outside udl: a project is a body of work, and the rules an agent on it is
given are the project's own text.
"""

# Local Imports
from udl.plugins.apps.orch.agents import (
    Agent,
    AgentRole,
    AgentSession,
    AgentStatus,
    ManagerPowers,
    SessionState,
)
from udl.plugins.apps.orch.models import BranchPattern, MergePolicy
from udl.plugins.apps.orch.records import (
    AgentRecords,
    Project,
    ProjectRecords,
    Repo,
    RepoRecords,
    SessionEvent,
    SessionRecords,
)

__all__ = [
    "Agent",
    "AgentRecords",
    "AgentRole",
    "AgentSession",
    "AgentStatus",
    "BranchPattern",
    "ManagerPowers",
    "MergePolicy",
    "Project",
    "ProjectRecords",
    "Repo",
    "RepoRecords",
    "SessionEvent",
    "SessionRecords",
    "SessionState",
]
