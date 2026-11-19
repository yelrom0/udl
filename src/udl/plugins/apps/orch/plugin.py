"""
----------------
- plugin.py -
----------------

Orch: udl's orchestrator (Phase 26), an app plugin (udl.apps) whose data is the work udl
tracks — projects and their repos, features and their tasks, links and notes.

Its records are in orch.records (a Project, a Repo); its operations come in 26.6. Importing this
module registers them, as importing any other app's module does.
"""

# System Imports
from collections.abc import Sequence
from typing import Any

# Local Imports
from udl.automations.events import EventType
from udl.db.records import Record
from udl.plugins.apps.base import AppContext, AppPlugin
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch.settings import OrchSettings

__all__ = ["Orch"]


class Orch(AppPlugin[OrchSettings]):
    """udl's orchestrator: the projects and repos it tracks, and the agents that work them.

    It brings its records (a Project, a Repo) and its shapes (orch.models); its operations are
    26.6's, and its service the dispatcher of Phase 27."""

    name = "orch"
    title = "Orchestrator"
    summary = "The work udl tracks: projects, repos, features, tasks, links and notes."
    settings_model = OrchSettings
    # (Its UI is Phase 29's, a udl.ui plugin's manifest; until then the generated page.)

    @classmethod
    def record_kinds(cls) -> Sequence[type[Record]]:
        """The kinds of record orch defines: a project, a repo, a feature, a task and the counter
        their keys come from (26.2-26.5), and an agent, its session and the events of it (27.1)."""
        return (
            stored.Project,
            stored.Repo,
            stored.Feature,
            stored.Task,
            stored.Counter,
            stored.Agent,
            stored.AgentSession,
            stored.SessionEvent,
        )

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        """The events orch publishes (its links' and tasks' changes, from 26.4 on)."""
        return ()

    async def serve(self, ctx: AppContext) -> None:
        """Orch's service: nothing to run until the dispatcher (Phase 27).

        An app that overrides no hook has no service of its own (AppPlugin's serve() returns at
        once); this one waits on `ctx.stop`, so it is a service that runs while the runtime
        holds its lease and ends when the lease goes."""
        await ctx.stop.wait()
