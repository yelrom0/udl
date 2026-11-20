"""
----------------
- plugin.py -
----------------

Orch: udl's orchestrator (Phase 26), an app plugin (udl.apps) whose data is the work udl
tracks — projects and their repos, features and their tasks, links and notes.

Its records are in orch.records (a Project, a Repo, a Feature, a Task, a Note); its shapes are in
orch.models. Its queue operations are here (27.3), its issue_token (27.2) and the rest of its
operations come in 26.6: importing this module registers them, as importing any other app's
module does.
"""

# System Imports
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

# Local Imports
from udl.automations.events import EventType
from udl.core.operations import Operation, OperationHints
from udl.db.records import Record
from udl.errors import DoesNotExist, NotAllowed
from udl.plugins.apps.base import AppContext, AppPlugin
from udl.plugins.apps.orch import queue as queues
from udl.plugins.apps.orch import records as stored
from udl.plugins.apps.orch import suggest as suggested
from udl.plugins.apps.orch.models import (
    AssignTaskRequest,
    MyTaskNote,
    MyTaskRequest,
    MyTaskResponse,
    QueueEntry,
    QueueRequest,
    ReorderQueueRequest,
    Suggestions,
    SuggestRequest,
    TaskQueue,
    UnassignTaskRequest,
)
from udl.plugins.apps.orch.settings import OrchSettings
from udl.plugins.apps.orch.tokens import (
    IssueTokenRequest,
    IssueTokenResponse,
)
from udl.plugins.apps.orch.tokens import (
    issue_token as issue,
)

if TYPE_CHECKING:
    from udl.core.service import UDLService

__all__ = ["Orch"]


class Orch(AppPlugin[OrchSettings]):
    """udl's orchestrator: the projects and repos it tracks, and the agents that work them.

    It brings its records (a Project, a Repo) and its shapes (orch.models); its queue operations
    are here (27.3), the rest are 26.6's, and its service the dispatcher of Phase 27."""

    name = "orch"
    title = "Orchestrator"
    summary = "The work udl tracks: projects, repos, features, tasks, links and notes."
    settings_model = OrchSettings
    # (Its UI is Phase 29's, a udl.ui plugin's manifest; until then the generated page.)

    @classmethod
    def record_kinds(cls) -> Sequence[type[Record]]:
        """The kinds of record orch defines: a project, a repo, a feature, a task, a note and the
        counter their keys come from (26.2-26.5), an agent, its session and the events of it
        (27.1), and the tokens its agents call udl with (27.2)."""
        return (
            stored.Project,
            stored.Repo,
            stored.Feature,
            stored.Task,
            stored.Note,
            stored.Counter,
            stored.Agent,
            stored.AgentSession,
            stored.SessionEvent,
            stored.AgentToken,
        )

    async def issue_token(
        self, request: IssueTokenRequest, *, service: UDLService
    ) -> IssueTokenResponse:
        """Issue a new token for the agent called `agent` (rotating the one it had, which stops
        working at once). The token is given here, once, and kept in udl's secret store, so the
        dispatcher can hand it to the agent's runner."""
        return IssueTokenResponse(agent=request.agent, token=await issue(service, request.agent))

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        """The events orch publishes (its links' and tasks' changes, from 26.4 on)."""
        return ()

    @classmethod
    def operations(cls) -> list[Operation]:
        """The operations it adds: the queue's (27.3), what to do next (27.4) and the agent
        token's (27.2), each one of its methods (the rest are 26.6's)."""
        return [
            cls.operation(
                "issue_token",
                title="Issue an agent's token",
                hints=OperationHints(admin=True),
            ),
            cls.operation(
                "assign",
                title="Assign a task",
                hints=OperationHints(idempotent=True),
            ),
            cls.operation(
                "reorder_queue",
                title="Reorder a queue",
                hints=OperationHints(idempotent=True),
            ),
            cls.operation("unassign", title="Unassign a task"),
            cls.operation(
                "get_queue",
                title="An agent's queue",
                hints=OperationHints(read_only=True),
            ),
            cls.operation(
                "my_task",
                title="What an agent is on",
                hints=OperationHints(read_only=True),
            ),
            cls.operation(
                "suggest",
                title="What to do next",
                hints=OperationHints(read_only=True),
            ),
        ]

    async def serve(self, ctx: AppContext) -> None:
        """Orch's service: nothing to run until the dispatcher (Phase 27).

        An app that overrides no hook has no service of its own (AppPlugin's serve() returns at
        once); this one waits on `ctx.stop`, so it is a service that runs while the runtime
        holds its lease and ends when the lease goes."""
        await ctx.stop.wait()

    # -- what to do next (27.4) ----------------------------------------------------------------

    async def suggest(self, request: SuggestRequest) -> Suggestions:
        """The tasks to do next, best first, each with the agent it suits and why.

        A task is a candidate once everything it needs is merged (or done) — or `ready`, when it
        may start on that task's branch instead. An active feature, the agent's last task and the
        tasks it unblocks all count. With no agent, each task is paired with the idle agent it
        suits best."""
        project = await self._project(request.project)
        # (An agent on a task of another project is not idle here: a task of this project
        # cannot know about another's, so they are read across every project.)
        on = [one for one in await self.records.list(stored.Task) if one.state == "in_progress"]
        found = suggested.suggest(
            await stored.TaskRecords(self.records).tasks(project),
            await stored.FeatureRecords(self.records).features(project),
            await stored.AgentRecords(self.records).agents(),
            agent=request.agent,
            limit=request.limit,
            busy=sorted({one.assignee for one in on if one.assignee}),
        )
        return Suggestions(project=project, suggestions=found)

    # -- the queue (27.3) ----------------------------------------------------------------------

    async def assign(self, request: AssignTaskRequest) -> TaskQueue:
        """Assign a task to an agent: it joins that agent's queue (at its end, or at a position).

        A task another agent holds leaves that agent's queue, and a system note says who must
        stop. A person's task is assigned to `human:<name>` (28.2's To action), never to an
        agent. The answer is the agent's queue, in the order it will work it."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        await queues.assign(
            tasks, request.key, request.agent, project=project, position=request.position
        )
        return await self._queue(tasks, project=project, agent=request.agent)

    async def reorder_queue(self, request: ReorderQueueRequest) -> TaskQueue:
        """Set an agent's queue to exactly these keys, in this order.

        Every task on the queue is named once: one that is not on it is not the reorder's to
        move, and one left out would be lost. The answer is the queue as it now is."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        await queues.reorder(tasks, project=project, agent=request.agent, keys=request.keys)
        return await self._queue(tasks, project=project, agent=request.agent)

    async def unassign(self, request: UnassignTaskRequest) -> TaskQueue:
        """Take a task out of its agent's queue: it is open again, and nobody's.

        The answer is the queue it left, with no gap in it."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        task = await tasks.task(project, request.key)
        if task is None:
            raise DoesNotExist(f"There is no task called {request.key!r} in {project!r}")
        agent = task.assignee or ""
        await queues.unassign(tasks, project=project, key=request.key)
        return await self._queue(tasks, project=project, agent=agent)

    async def get_queue(self, request: QueueRequest) -> TaskQueue:
        """What an agent's queue is: its tasks, in the order it will work them.

        A person's own list is asked for the same way, by `human:<name>`."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        return await self._queue(tasks, project=project, agent=request.agent)

    async def my_task(self, request: MyTaskRequest) -> MyTaskResponse:
        """What an agent is to work next: the task it is in progress on, else the first on its
        queue, and nothing when it has none.

        The dispatcher asks this when it starts a session (27.7); an agent asks it when it is
        told to go on."""
        tasks = stored.TaskRecords(self.records)
        project = await self._project(request.project)
        found = await queues.next_task(tasks, project=project, agent=request.agent)
        if found is None:
            return MyTaskResponse()
        feature = await stored.FeatureRecords(self.records).feature(project, found.feature)
        project_record = await stored.ProjectRecords(self.records).project(project)
        notes = await stored.NoteRecords(self.records).notes("task", found.id)
        return MyTaskResponse(
            task=_entry(found),
            feature_spec="" if feature is None else feature.spec,
            spec=found.spec,
            rules="" if project_record is None else project_record.rules,
            notes=[
                MyTaskNote(note_kind=note.note_kind, text=note.text, author=note.author, at=note.at)
                for note in notes
            ],
        )

    async def _project(self, project: str | None) -> str:
        """The project an operation is of: `project`, or the only one udl has. Raises NotAllowed
        when there is none, or more than one and none is named."""
        if project is not None:
            return project
        projects = await stored.ProjectRecords(self.records).projects()
        if len(projects) == 1:
            return projects[0].name
        if not projects:
            raise NotAllowed(
                "There is no project to work in",
                hint="Add one first: a project is what features, tasks and queues are of.",
            )
        named = ", ".join(found.name for found in projects)
        raise NotAllowed(
            f"Which project? udl has {len(projects)} ({named})",
            hint="Name the project its task is in.",
        )

    async def _queue(self, tasks: stored.TaskRecords, *, project: str, agent: str) -> TaskQueue:
        """`agent`'s queue, as the operations answer with it."""
        found = await queues.queue_of(tasks, project=project, agent=agent)
        return TaskQueue(project=project, agent=agent, tasks=[_entry(task) for task in found])


def _entry(task: stored.Task) -> QueueEntry:
    """`task` as the queue's operations answer with it."""
    return QueueEntry(
        key=task.key,
        feature=task.feature,
        title=task.title,
        state=task.state,
        position=task.position,
        assignee=task.assignee,
        repo=task.repo,
    )
