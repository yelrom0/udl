"""
-------------------
- test_managers.py -
-------------------

Managers (27.8): a manager agent's duties over its reports, each one switchable.

A manager is an agent whose `role` is `manager` and whose `manages` says which of the four powers
it has — `assign`, `answer`, `review` and `plan`. Each is off until it is given (O-D10), each
switches on operations in its role's list (27.2), and every one of them reaches only its own
subtree: a manager moves, answers and reviews its reports' work, never another manager's.

These run through `service.call()`, as every interface calls the operations, so what is tested is
the operations and what they write.
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import BaseModel, SecretStr

# Local Imports
from support.engine import registry, settings
from udl.config import APISettings
from udl.core.operations import Operation, OperationHints
from udl.core.service import UDLService
from udl.errors import NotAllowed
from udl.interfaces.security import Principal as Caller
from udl.interfaces.security import PrincipalKind, current_principal
from udl.plugins.apps.base import AppPlugin, AppRecords, AppSettings
from udl.plugins.apps.orch import transitions
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers
from udl.plugins.apps.orch.models import (
    IN_PROGRESS,
    QUEUED,
    READY,
    NoteKind,
    OwnerKind,
    Principal,
    PullState,
    Questions,
    Readiness,
    Suggestions,
)
from udl.plugins.apps.orch.models import (
    Question as Asked,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.readiness import Pull
from udl.plugins.apps.orch.records import (
    Agent,
    AgentRecords,
    Feature,
    FeatureRecords,
    LinkRecords,
    NoteRecords,
    Project,
    ProjectRecords,
    QuestionRecords,
    Review,
    ReviewRecords,
    Task,
    TaskRecords,
)
from udl.plugins.registry import APPS, PluginRegistry

BOSS: str = "ernie-c"
"""The manager the fixture's reports report to."""
OTHER: str = "ernie-d"
"""Another manager, so a test can show one reaching for another's reports."""
ONE: str = "deepseek-b"
"""A report of `BOSS`."""
TWO: str = "deepseek-a"
"""Another report of `BOSS`."""
STRANGER: str = "deepseek-c"
"""A report of `OTHER`."""
HUMANS: str = "humans"
"""What a question goes up to when no manager may answer it."""
PULL: str = "https://github.com/yelrom0/udl/pull/7"
"""The pull the fake `github` answers for."""
HEAD: str = "dc1fd19aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
"""The commit at its head."""


def orch_registry() -> PluginRegistry:
    """The memory database, the secret store, the fake github and orch."""
    plugins = registry()
    plugins.register(APPS, FakeGitHub)
    plugins.register(APPS, Orch)
    return plugins


# ---------------------------------------------------------------------------------------------
# A fake github, so `orch_ready` has a pull to read
# ---------------------------------------------------------------------------------------------


class FakeGitHub(AppPlugin[AppSettings]):
    """A `github` app whose one operation answers a pull (what readiness calls)."""

    name = "github"
    title = "GitHub (fake)"

    def __init__(self, settings_: AppSettings | None = None, **kwargs: object) -> None:
        super().__init__(settings_ or AppSettings(), **kwargs)  # pyright: ignore[reportArgumentType]
        self.pull: dict[str, Any] = a_pull()
        """The pull it answers with (set by a test)."""

    @classmethod
    def operations(cls) -> list[Operation]:
        return [
            cls.operation(
                "get_pull", title="One pull", hints=OperationHints(read_only=True, open_world=True)
            )
        ]

    async def get_pull(self, request: GetPullRequest) -> Pull:
        """The pull the fixture set, as readiness reads it."""
        return Pull.model_validate(self.pull)


class GetPullRequest(BaseModel):
    """`github_get_pull`'s request, as the fake reads it."""

    repo: str
    number: int


def a_pull(head: str = HEAD, *, comments: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A pull that is clean, with its checks passed and the bot's verdict for `head`."""
    return {
        "repo": "yelrom0/udl",
        "number": 7,
        "title": "It",
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "head": "feat/it",
        "head_sha": head,
        "head_committed_at": "2026-11-20T09:00:00Z",
        "base": "main",
        "author": ONE,
        "html_url": PULL,
        "checks": [
            {
                "name": "check",
                "status": "completed",
                "conclusion": "success",
                "started_at": "2026-11-20T09:30:00Z",
            }
        ],
        "comments": comments
        if comments is not None
        else [
            {
                "author": "claude[bot]",
                "body": (
                    f"## Follow-up review: it (#7), after {head[:7]}\n\n**Verdict: all clear**\n"
                ),
                "created_at": "2026-11-20T10:00:00Z",
            }
        ],
    }


# ---------------------------------------------------------------------------------------------
# The service: a project, a manager, its two reports, another manager's report, and four tasks
# ---------------------------------------------------------------------------------------------


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with one project, two managers, three agents and four queued tasks."""
    async with UDLService(
        settings(tmp_path, api=APISettings(api_key=SecretStr("test-key"))),
        registry=orch_registry(),
    ) as running:
        records = AppRecords(running.db)
        await ProjectRecords(records).save_project(
            Project(name="udl", title="udl", review_bot="claude[bot]")
        )
        await FeatureRecords(records).save_feature(Feature(project="udl", key="F-1", title="It"))
        agents = AgentRecords(records)
        await agents.save_agent(
            Agent(
                name=BOSS,
                title=BOSS,
                runner="fake",
                host="local",
                role=AgentRole.MANAGER,
                manages=ManagerPowers(assign=True, answer=True, review=True, plan=True),
                brief="Keep your reports busy.",
            )
        )
        await agents.save_agent(
            Agent(
                name=OTHER,
                title=OTHER,
                runner="fake",
                host="local",
                role=AgentRole.MANAGER,
                manages=ManagerPowers(assign=True, answer=True, review=True, plan=True),
            )
        )
        for name, manager in ((ONE, BOSS), (TWO, BOSS), (STRANGER, OTHER)):
            await agents.save_agent(
                Agent(
                    name=name,
                    title=name,
                    runner="fake",
                    host="local",
                    manager=manager,
                    brief="Build the task on your queue.",
                )
            )
        tasks = TaskRecords(records)
        for number in (1, 2, 3, 4):
            key = f"F-1.{number}"
            task = Task(
                project="udl",
                feature="F-1",
                key=key,
                title=f"It {number}",
                assignee=ONE if number <= 2 else (TWO if number == 3 else STRANGER),
            )
            await tasks.save_task(task)
            await tasks.move(task, QUEUED, by=Principal(kind="system", role="assign"))
            await LinkRecords(records).add(
                project="udl",
                owner_kind=OwnerKind.TASK,
                owner=key,
                kind="pull",
                url=PULL,
                state=PullState(head_sha=HEAD),
            )
        github = await running.apps.get("github")
        assert isinstance(github, FakeGitHub)
        github.pull = a_pull()
        yield running


def as_agent(name: str, *, role: str = "manager") -> Any:
    """The caller is the agent called `name` for a while (a token's principal)."""
    return current_principal.set(Caller(kind=PrincipalKind.AGENT, name=name, role=role))


def as_person() -> Any:
    """The caller is the owner for a while."""
    return current_principal.set(Caller(kind=PrincipalKind.OWNER, name="owner"))


def as_boss() -> Any:
    """The caller is `BOSS` for a while (an agent token's principal)."""
    return as_agent(BOSS)


def as_report() -> Any:
    """The caller is `ONE`, an implementer, for a while."""
    return as_agent(ONE, role="implementer")


async def changed(service: UDLService, name: str, **changes: Any) -> Agent:
    """The agent `name`, with `changes` stored on it."""
    agents = AgentRecords(AppRecords(service.db))
    found = await agents.agent(name)
    assert found is not None
    return await agents.save_agent(found.model_copy(update=changes))


async def started(service: UDLService, key: str) -> Task:
    """The task `key` moved to `in_progress`, as the dispatcher starts it."""
    tasks = TaskRecords(AppRecords(service.db))
    found = await tasks.task("udl", key)
    assert found is not None
    who = Principal(kind="system", role="start")
    return (await tasks.save_moved(found, transitions.move(found, IN_PROGRESS, by=who), by=who))[0]


async def task_of(service: UDLService, key: str) -> Task:
    """The task called `key`."""
    found = await TaskRecords(AppRecords(service.db)).task("udl", key)
    assert found is not None
    return found


async def notes_of(service: UDLService, task: Task) -> list[tuple[str, str, str]]:
    """What the notes on `task` say: each one's kind, author and text, oldest first."""
    found = await NoteRecords(AppRecords(service.db)).for_owner(OwnerKind.TASK, task.id)
    return [(one.note_kind, one.author, one.text) for one in found]


async def review(service: UDLService, **request: Any) -> Any:
    """`orch_review_task`, with the project and a verdict."""
    return await service.call("orch_review_task", {"project": "udl", **request})


# ---------------------------------------------------------------------------------------------
# Each power on and off
# ---------------------------------------------------------------------------------------------


async def test_a_manager_with_the_assign_power_queues_its_reports_work(service: UDLService) -> None:
    """(`assign` on: it moves a task into its report's queue.)"""
    token = as_boss()
    try:
        await service.call("orch_assign", {"project": "udl", "key": "F-1.3", "agent": ONE})
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.3")).assignee == ONE


async def test_a_manager_without_the_assign_power_may_not_assign(service: UDLService) -> None:
    """(`assign` off: the operation is not one its role and powers allow, so it is refused
    before its request is read.)"""
    await changed(service, BOSS, manages=ManagerPowers(answer=True))
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="not one of the operations"):
            await service.call("orch_assign", {"project": "udl", "key": "F-1.3", "agent": ONE})
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.3")).assignee == TWO


async def test_a_manager_cannot_assign_to_another_managers_report(service: UDLService) -> None:
    """(Its powers reach its own subtree: another manager's report is not its to move.)"""
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="not in ernie-c's subtree"):
            await service.call("orch_assign", {"project": "udl", "key": "F-1.1", "agent": STRANGER})
    finally:
        current_principal.reset(token)


async def test_a_manager_without_the_answer_power_may_not_answer(service: UDLService) -> None:
    """(`answer` off: it may not decide its reports' questions — they go up, to the humans.)"""
    await changed(service, BOSS, manages=ManagerPowers(assign=True, review=True, plan=True))
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="not one of the operations"):
            await service.call(
                "orch_answer", {"project": "udl", "key": "Q-1", "answer": "The `api` one."}
            )
    finally:
        current_principal.reset(token)


async def test_a_manager_without_the_plan_power_may_not_set_or_split_a_task(
    service: UDLService,
) -> None:
    """(`plan` off: it may not create or split the tasks its reports work.)"""
    await changed(service, BOSS, manages=ManagerPowers(assign=True, answer=True, review=True))
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="not one of the operations"):
            await service.call(
                "orch_set_task",
                {"project": "udl", "key": "F-1.5", "feature": "F-1", "title": "Another one"},
            )
        with pytest.raises(NotAllowed, match="not one of the operations"):
            await service.call(
                "orch_split_task",
                {
                    "project": "udl",
                    "key": "F-1.1",
                    "into": [{"title": "One", "spec": ""}, {"title": "Two", "spec": ""}],
                },
            )
    finally:
        current_principal.reset(token)


async def test_a_manager_with_the_plan_power_makes_a_task_for_its_reports(
    service: UDLService,
) -> None:
    """(`plan` on: it creates the tasks its reports work, and splits one that has not started.)"""
    token = as_boss()
    try:
        await service.call(
            "orch_set_task",
            {"project": "udl", "key": "F-1.5", "feature": "F-1", "title": "Another one"},
        )
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.5")).title == "Another one"


# ---------------------------------------------------------------------------------------------
# Review: its verdict, and what readiness does with it
# ---------------------------------------------------------------------------------------------


async def test_a_manager_reviews_its_reports_pull_and_says_so_on_the_task(
    service: UDLService,
) -> None:
    """(`review`: a verdict is a `decision` note on the task with the manager as its author, so a
    person sees who decided what.)"""
    token = as_boss()
    try:
        found = await review(service, key="F-1.1", verdict="approve", reasons="It does the card.")
    finally:
        current_principal.reset(token)

    assert found.verdict == "approve"
    assert found.head == HEAD, "the head it read is the pull's"
    said = await notes_of(service, await task_of(service, "F-1.1"))
    assert (NoteKind.DECISION, BOSS, f"review: approve {HEAD[:7]} — It does the card.") in said


async def test_readiness_waits_for_the_manager_s_ok(service: UDLService) -> None:
    """(27.6's readiness asks for the manager's OK for this head when the task's manager has the
    `review` power: without it the task is not ready, and an approval for it is.)"""
    await started(service, "F-1.1")
    token = as_agent(ONE, role="implementer")
    try:
        with pytest.raises(NotAllowed, match="has not reviewed this head"):
            await service.call("orch_ready", {"project": "udl", "key": "F-1.1"})
    finally:
        current_principal.reset(token)

    token = as_boss()
    try:
        await review(service, key="F-1.1", verdict="approve", reasons="Fine.")
    finally:
        current_principal.reset(token)

    token = as_agent(ONE, role="implementer")
    try:
        found = await service.call("orch_ready", {"project": "udl", "key": "F-1.1"})
    finally:
        current_principal.reset(token)

    assert isinstance(found, Readiness)
    assert found.ready
    assert (await task_of(service, "F-1.1")).state == READY


async def test_a_manager_s_changes_block_readiness_until_it_looks_again(
    service: UDLService,
) -> None:
    """(A verdict of `changes` is a blocker, and a review of the commit before a new head says
    nothing about this one: readiness asks again.)"""
    await started(service, "F-1.1")
    token = as_boss()
    try:
        await review(service, key="F-1.1", verdict="changes", reasons="The tests are missing.")
    finally:
        current_principal.reset(token)

    token = as_agent(ONE, role="implementer")
    try:
        with pytest.raises(NotAllowed, match="asked for changes"):
            await service.call("orch_ready", {"project": "udl", "key": "F-1.1"})
    finally:
        current_principal.reset(token)

    # (A new head: the approval for the old one does not carry over.)
    github = await service.apps.get("github")
    assert isinstance(github, FakeGitHub)
    newer = "ab12cd3bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    github.pull = a_pull(newer)
    token = as_boss()
    try:
        await review(service, key="F-1.1", verdict="approve", reasons="It is there now.")
    finally:
        current_principal.reset(token)

    token = as_agent(ONE, role="implementer")
    try:
        with pytest.raises(NotAllowed, match="has not reviewed this head"):
            await service.call("orch_ready", {"project": "udl", "key": "F-1.1"})
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.1")).state == IN_PROGRESS, "it did not move"


async def test_a_manager_cannot_review_a_task_outside_its_subtree(service: UDLService) -> None:
    """(Its `review` is its own reports' work: another manager's report's task is not its to
    judge.)"""
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="not one of ernie-c's reports' tasks"):
            await review(service, key="F-1.4", verdict="approve")
    finally:
        current_principal.reset(token)

    assert await ReviewRecords(AppRecords(service.db)).for_task("udl", "F-1.4") == []


async def test_a_report_may_not_review_its_own_work(service: UDLService) -> None:
    """(Reviewing is a manager's: an implementer has no such power.)"""
    token = as_agent(ONE, role="implementer")
    try:
        with pytest.raises(NotAllowed, match="not one of the operations"):
            await review(service, key="F-1.1", verdict="approve")
    finally:
        current_principal.reset(token)


async def test_a_person_does_not_review_through_the_operation(service: UDLService) -> None:
    """(A person merges, and needs no review of their own: the operation is an agent's.)"""
    token = as_person()
    try:
        with pytest.raises(NotAllowed, match="a review is an agent's"):
            await review(service, key="F-1.1", verdict="approve")
    finally:
        current_principal.reset(token)


# ---------------------------------------------------------------------------------------------
# Escalating what it cannot answer
# ---------------------------------------------------------------------------------------------


async def test_an_escalated_question_reaches_the_humans(service: UDLService) -> None:
    """(What a manager cannot answer goes up, to the humans' list: the card's own test.)"""
    records = AppRecords(service.db)
    questions = QuestionRecords(records)
    from udl.plugins.apps.orch import questions as asked

    found = await asked.ask(
        questions, project="udl", task="F-1.1", title="Which repo?", asked_by=ONE
    )
    assert found.to == BOSS, "it goes to the report's manager, which may answer"

    token = as_boss()
    try:
        up = await service.call("orch_escalate", {"project": "udl", "key": found.key})
    finally:
        current_principal.reset(token)

    assert isinstance(up, Asked)
    assert up.to == HUMANS
    humans = await service.call("orch_list_questions", {"project": "udl", "to": HUMANS})
    assert isinstance(humans, Questions)
    assert [one.key for one in humans.questions] == [found.key]


async def test_a_question_asked_of_a_manager_that_may_not_answer_goes_to_the_humans(
    service: UDLService,
) -> None:
    """(A manager without `answer` is not told: the question skips it for the next that may.)"""
    await changed(service, BOSS, manages=ManagerPowers(assign=True, review=True, plan=True))
    records = AppRecords(service.db)
    from udl.plugins.apps.orch import questions as asked

    found = await asked.ask(
        QuestionRecords(records), project="udl", task="F-1.1", title="Which repo?", asked_by=ONE
    )

    assert found.to == HUMANS


# ---------------------------------------------------------------------------------------------
# The kinds
# ---------------------------------------------------------------------------------------------


async def test_a_review_is_a_kind_of_its_own(service: UDLService) -> None:
    """(The kinds orch keeps, so the app contract and db sync see it.)"""
    kinds = {kind.kind for kind in Orch.record_kinds()}
    assert "orch_review" in kinds
    stored = await ReviewRecords(AppRecords(service.db)).save_review(
        Review(project="udl", task="F-1.1", head=HEAD, verdict="approve", by=BOSS)
    )
    assert stored.verdict == "approve"


async def test_the_questions_of_a_manager_are_only_its_reports(
    service: UDLService,
) -> None:
    """(A question from another manager's report is not the boss's: routing skips it.)"""
    records = AppRecords(service.db)
    from udl.plugins.apps.orch import questions as asked

    found = await asked.ask(
        QuestionRecords(records),
        project="udl",
        task="F-1.3",
        title="Which repo?",
        asked_by=STRANGER,
    )

    assert found.to == OTHER


async def test_a_question_a_manager_answers_is_written_into_the_task(service: UDLService) -> None:
    """(`answer`: the answer lands as a `decision` note on the task it names.)"""
    records = AppRecords(service.db)
    from udl.plugins.apps.orch import questions as asked

    found = await asked.ask(
        QuestionRecords(records), project="udl", task="F-1.1", title="Which repo?", asked_by=ONE
    )
    token = as_boss()
    try:
        await service.call(
            "orch_answer",
            {"project": "udl", "key": found.key, "answer": "The `api` one."},
        )
    finally:
        current_principal.reset(token)

    said = await notes_of(service, await task_of(service, "F-1.1"))
    assert (NoteKind.DECISION, BOSS, f"{found.key}, answered by {BOSS}: The `api` one.") in said


async def test_a_manager_may_not_answer_another_managers_reports_question(
    service: UDLService,
) -> None:
    """(Its `answer` reaches its own subtree: another's question is refused.)"""
    records = AppRecords(service.db)
    from udl.plugins.apps.orch import questions as asked

    found = await asked.ask(
        QuestionRecords(records),
        project="udl",
        task="F-1.3",
        title="Which repo?",
        asked_by=STRANGER,
    )
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="asked of"):
            await service.call(
                "orch_answer", {"project": "udl", "key": found.key, "answer": "The `api` one."}
            )
    finally:
        current_principal.reset(token)


async def test_a_manager_s_suggest_is_its_reports_work(service: UDLService) -> None:
    """(`suggest` is one of the `assign` power's: a manager may ask what to give a report.)"""
    token = as_person()
    try:
        await service.call(
            "orch_set_task",
            {"project": "udl", "key": "F-1.5", "feature": "F-1", "title": "Another one"},
        )
    finally:
        current_principal.reset(token)

    token = as_boss()
    try:
        found = await service.call("orch_suggest", {"project": "udl", "agent": ONE})
    finally:
        current_principal.reset(token)

    assert isinstance(found, Suggestions)
    assert [one.task for one in found.suggestions] == ["F-1.5"]


# ---------------------------------------------------------------------------------------------
# The review's fixes (round 1)
# ---------------------------------------------------------------------------------------------


async def test_a_manager_may_not_review_its_own_work(service: UDLService) -> None:
    """(A review is someone else's: a manager reviewing a task it holds would be its own OK.)"""
    tasks = TaskRecords(AppRecords(service.db))
    mine = await tasks.task("udl", "F-1.1")
    assert mine is not None
    await tasks.save_task(mine.model_copy(update={"assignee": BOSS}))

    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="own work"):
            await review(service, key="F-1.1", verdict="approve")
    finally:
        current_principal.reset(token)


async def test_a_review_under_another_name_is_not_the_manager_s_ok(
    service: UDLService,
) -> None:
    """(Readiness asks the manager above the assignee, and takes only *its* review: one saved
    under another name — a report's own, or another manager's — is not the OK.)"""
    await ReviewRecords(AppRecords(service.db)).save_review(
        Review(project="udl", task="F-1.1", head=HEAD, verdict="approve", by=STRANGER)
    )

    token = as_agent(ONE, role="implementer")
    try:
        with pytest.raises(NotAllowed, match="has not reviewed this head"):
            await service.call("orch_ready", {"project": "udl", "key": "F-1.1"})
    finally:
        current_principal.reset(token)


async def test_a_manager_cannot_take_a_task_another_managers_report_holds(
    service: UDLService,
) -> None:
    """(`assign` reaches its own reports: a task another manager's report is on is not its to
    move — and moving it would stop that report's session.)"""
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="not in ernie-c's subtree"):
            await service.call("orch_assign", {"project": "udl", "key": "F-1.4", "agent": ONE})
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.4")).assignee == STRANGER


async def test_a_manager_plans_only_in_features_its_reports_work_on(
    service: UDLService,
) -> None:
    """(`plan` is its reports' work: a feature none of them has a task in is not its to add to.)"""
    await FeatureRecords(AppRecords(service.db)).save_feature(
        Feature(project="udl", key="F-2", title="Nobody's")
    )

    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="none of ernie-c's reports"):
            await service.call(
                "orch_set_task",
                {"project": "udl", "key": "F-2.1", "feature": "F-2", "title": "Not mine"},
            )
    finally:
        current_principal.reset(token)


async def test_a_manager_may_not_suggest_for_another_managers_report(
    service: UDLService,
) -> None:
    """(`assign` reaches its own reports, so a suggestion for another manager's report is
    refused rather than answered.)"""
    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="not in ernie-c's subtree"):
            await service.call("orch_suggest", {"project": "udl", "agent": STRANGER})
    finally:
        current_principal.reset(token)


async def test_a_manager_edits_an_unassigned_task_only_in_its_reports_features(
    service: UDLService,
) -> None:
    """(A task nobody is on is held by where it is: a manager plans in its reports' features, so
    the one in a feature nobody works in is not its to edit.)"""
    records = AppRecords(service.db)
    await FeatureRecords(records).save_feature(Feature(project="udl", key="F-2", title="Nobody's"))
    tasks = TaskRecords(records)
    for feature, key in (("F-1", "F-1.5"), ("F-2", "F-2.1")):
        await tasks.save_task(
            Task(project="udl", feature=feature, key=key, title="Nobody is on it")
        )

    token = as_boss()
    try:
        await service.call(
            "orch_set_task", {"project": "udl", "key": "F-1.5", "title": "Mine to edit"}
        )
        with pytest.raises(NotAllowed, match="none of ernie-c's reports"):
            await service.call(
                "orch_set_task", {"project": "udl", "key": "F-2.1", "title": "Not mine"}
            )
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.5")).title == "Mine to edit"
    assert (await task_of(service, "F-2.1")).title == "Nobody is on it"


async def test_a_manager_splits_an_unassigned_task_only_in_its_reports_features(
    service: UDLService,
) -> None:
    """(The same rule as an edit: a split is a plan, so it happens where the reports work.)"""
    records = AppRecords(service.db)
    await FeatureRecords(records).save_feature(Feature(project="udl", key="F-2", title="Nobody's"))
    tasks = TaskRecords(records)
    for feature, key in (("F-1", "F-1.5"), ("F-2", "F-2.1")):
        await tasks.save_task(
            Task(project="udl", feature=feature, key=key, title="Nobody is on it")
        )
    into = [{"title": "One", "spec": ""}, {"title": "Two", "spec": ""}]

    token = as_boss()
    try:
        await service.call("orch_split_task", {"project": "udl", "key": "F-1.5", "into": into})
        with pytest.raises(NotAllowed, match="none of ernie-c's reports"):
            await service.call("orch_split_task", {"project": "udl", "key": "F-2.1", "into": into})
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.5")).title == "One"
    assert (await task_of(service, "F-2.1")).title == "Nobody is on it"


async def test_a_persons_task_is_not_an_agents_to_plan(service: UDLService) -> None:
    """(A person's task is theirs: a manager edits and splits its reports' work, never a
    person's, whichever feature it is in.)"""
    tasks = TaskRecords(AppRecords(service.db))
    await tasks.save_task(
        Task(project="udl", feature="F-1", key="F-1.5", title="Dana's", assignee="human:dana")
    )

    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="a person's task"):
            await service.call(
                "orch_set_task", {"project": "udl", "key": "F-1.5", "title": "Not mine"}
            )
        with pytest.raises(NotAllowed, match="a person's task"):
            await service.call(
                "orch_split_task",
                {
                    "project": "udl",
                    "key": "F-1.5",
                    "into": [{"title": "One", "spec": ""}, {"title": "Two", "spec": ""}],
                },
            )
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.5")).title == "Dana's"


async def test_a_feature_whose_only_work_is_a_persons_is_not_a_managers_to_plan_in(
    service: UDLService,
) -> None:
    """(A person is nobody's report: a feature whose only task is a person's is not a feature a
    manager's reports work in.)"""
    records = AppRecords(service.db)
    await FeatureRecords(records).save_feature(Feature(project="udl", key="F-2", title="Dana's"))
    await TaskRecords(records).save_task(
        Task(project="udl", feature="F-2", key="F-2.1", title="Dana's", assignee="human:dana")
    )

    token = as_boss()
    try:
        with pytest.raises(NotAllowed, match="none of ernie-c's reports"):
            await service.call(
                "orch_set_task",
                {"project": "udl", "key": "F-2.2", "feature": "F-2", "title": "Not mine"},
            )
    finally:
        current_principal.reset(token)


async def test_a_manager_sizes_only_its_own_reports_work(service: UDLService) -> None:
    """(`plan` sizes its reports' work, as it assigns it: a task another manager's report is on
    is not its to size.)"""
    token = as_boss()
    try:
        await service.call("orch_set_task", {"project": "udl", "key": "F-1.1", "size": "M"})
        assert (await task_of(service, "F-1.1")).size == "M"
        with pytest.raises(NotAllowed, match="not in ernie-c's subtree"):
            await service.call("orch_set_task", {"project": "udl", "key": "F-1.4", "size": "M"})
    finally:
        current_principal.reset(token)

    assert (await task_of(service, "F-1.4")).size is None
