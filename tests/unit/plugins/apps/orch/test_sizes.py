"""
------------------------------
- test_sizes.py -
------------------------------

Task sizes (26.8): S, M, L and XL are 1, 3, 5 and 8 points, in the one place a size becomes
points (`POINTS`); a task's points are its size's, and nothing without a size; a feature's and a
project's totals come from their tasks, with the unsized ones counted and the cancelled ones left
out; an agent sets a size only with the `plan` power, and never on a task it is the assignee of;
and a card's header says its size (`· size M`, in any order with `needs`), which the importer
reads (a size it cannot read is a line it could not place).
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

from udl.clock import SystemClock

# Local Imports
from udl.core.service import UDLService
from udl.errors import NotAllowed
from udl.interfaces.security import Principal as Caller
from udl.interfaces.security import PrincipalKind, current_principal
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers
from udl.plugins.apps.orch.importer import parse_plan
from udl.plugins.apps.orch.models import (
    OPEN,
    POINTS,
    FeatureInfo,
    Features,
    SetTaskRequest,
    Size,
    State,
    Task,
    TaskInfo,
    Totals,
)
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import Agent, AgentRecords, TaskRecords
from udl.plugins.apps.orch.settings import OrchSettings

PLAN = Path(__file__).resolve().parents[5] / "planning" / "02-agent-orchastrator.md"
"""The plan this task's own card is in (its Done when: importing it sizes 26.8)."""


def _shape(key: str, size: Size | None, state: State = OPEN) -> Task:
    """A task, as an operation answers it, with `size` and `state`."""
    return Task(project="udl", feature="26", key=key, title=key, size=size, state=state)


async def _sized(service: UDLService, key: str, size: Size | None, state: State) -> None:
    """Task `key` (one of the fixture's) given `size` and moved to `state` as a record writes it
    (a move would say the state is not reachable from `open`, and a total is about the state)."""
    records = AppRecords(service.db)
    found = await TaskRecords(records).task("udl", key)
    assert found is not None
    await TaskRecords(records).save_all([found.model_copy(update={"size": size, "state": state})])


def _as(caller: Caller):
    """The caller an agent's request is made as."""
    return current_principal.set(caller)


def _app(service: UDLService) -> Orch:
    """The orch app over the service's records: its operations, without the role table that
    the interface in front of it applies (27.2), so a rule of its own is tested on its own."""
    return Orch(OrchSettings(), clock=SystemClock(), records=AppRecords(service.db))


# ---------------------------------------------------------------------------------------------
# What a size is worth
# ---------------------------------------------------------------------------------------------


def test_each_size_is_its_points() -> None:
    assert POINTS == {"S": 1, "M": 3, "L": 5, "XL": 8}


def test_a_task_s_points_are_its_size_s() -> None:
    assert _shape("26.1", "XL").points == 8
    assert _shape("26.2", "S").points == 1


def test_a_task_without_a_size_has_no_points() -> None:
    assert _shape("26.3", None).points is None


def test_a_size_there_is_not_is_refused() -> None:
    with pytest.raises(ValueError, match="size"):
        _shape("26.4", "XXL")  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------------------------


def test_totals_count_sized_tasks_and_leave_the_cancelled_ones_out() -> None:
    found = Totals.of(
        [
            _shape("26.1", "M", "merged"),
            _shape("26.2", "L", "ready"),
            _shape("26.3", "XL", "cancelled"),
            _shape("26.4", None, "open"),
        ]
    )

    assert found == Totals(points=8, points_merged=3, points_ready=5, unsized=1)


def test_totals_of_nothing_are_zero() -> None:
    assert Totals.of([]) == Totals()


async def test_a_feature_s_answer_has_its_totals(service: UDLService) -> None:
    await _sized(service, "26.1", "M", "merged")
    await _sized(service, "26.2", "L", "ready")

    found = await service.call("orch_get_feature", {"key": "26"})

    assert isinstance(found, FeatureInfo)
    assert found.totals == Totals(points=8, points_merged=3, points_ready=5, unsized=0)
    assert [(task.key, task.size, task.points) for task in found.tasks] == [
        ("26.1", "M", 3),
        ("26.2", "L", 5),
    ]


async def test_listing_a_project_s_features_has_the_project_s_totals(
    service: UDLService,
) -> None:
    await _sized(service, "26.1", "S", "done")
    await _sized(service, "26.2", None, "open")

    found = await service.call("orch_list_features", {})

    assert isinstance(found, Features)
    assert found.totals == Totals(points=1, points_merged=1, points_ready=0, unsized=1)


# ---------------------------------------------------------------------------------------------
# Who sets one
# ---------------------------------------------------------------------------------------------


async def test_a_task_is_sized_when_it_is_set(service: UDLService) -> None:
    sized = await service.call("orch_set_task", {"key": "26.2", "size": "L"})

    assert isinstance(sized, Task)
    assert (sized.size, sized.points) == ("L", 5)
    stored = await TaskRecords(AppRecords(service.db)).task("udl", "26.2")
    assert stored is not None
    assert stored.size == "L"


async def test_a_new_task_is_sized_as_it_is_made(service: UDLService) -> None:
    made = await service.call(
        "orch_set_task", {"feature": "26", "title": "Another piece", "size": "S"}
    )

    assert isinstance(made, Task)
    assert (made.key, made.size, made.points) == ("26.3", "S", 1)


async def _manager_with_plan(service: UDLService) -> None:
    """A manager with the `plan` power, and its report: `deepseek-a`, who works 26.1."""
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    await agents.save_agent(
        Agent(
            name="deepseek-m",
            title="deepseek-m",
            runner="fake",
            role=AgentRole.MANAGER,
            manages=ManagerPowers(plan=True),
        )
    )
    found = await agents.agent("deepseek-a")
    assert found is not None
    await agents.save_agent(found.model_copy(update={"manager": "deepseek-m"}))
    task = await TaskRecords(records).task("udl", "26.1")
    assert task is not None
    await TaskRecords(records).save_all([task.model_copy(update={"assignee": "deepseek-a"})])


async def test_an_implementer_may_not_size_a_task(service: UDLService) -> None:
    # (The operation's own rule: 27.2's role table is the interface's, and 27.8 is what lets a
    # manager with `plan` call this operation at all — so this is the rule, called directly.)
    with (
        pytest.raises(NotAllowed, match="plan"),
        _as(Caller(kind=PrincipalKind.AGENT, name="deepseek-a", role="implementer")),
    ):
        await _app(service).set_task(SetTaskRequest(key="26.1", size="M"))


async def test_a_manager_with_plan_sizes_its_report_s_task(service: UDLService) -> None:
    await _manager_with_plan(service)

    with _as(Caller(kind=PrincipalKind.AGENT, name="deepseek-m", role="manager")):
        sized = await _app(service).set_task(SetTaskRequest(key="26.1", size="XL"))

    assert (sized.size, sized.points) == ("XL", 8)


async def test_a_manager_with_plan_may_not_size_its_own_task(service: UDLService) -> None:
    await _manager_with_plan(service)
    records = AppRecords(service.db)
    task = await TaskRecords(records).task("udl", "26.2")
    assert task is not None
    await TaskRecords(records).save_all([task.model_copy(update={"assignee": "deepseek-m"})])

    with (
        pytest.raises(NotAllowed, match="own task"),
        _as(Caller(kind=PrincipalKind.AGENT, name="deepseek-m", role="manager")),
    ):
        await _app(service).set_task(SetTaskRequest(key="26.2", size="S"))


async def test_a_manager_without_plan_may_not_size_a_task(service: UDLService) -> None:
    await _manager_with_plan(service)
    records = AppRecords(service.db)
    agents = AgentRecords(records)
    found = await agents.agent("deepseek-m")
    assert found is not None
    await agents.save_agent(found.model_copy(update={"manages": ManagerPowers()}))

    with (
        pytest.raises(NotAllowed, match="plan"),
        _as(Caller(kind=PrincipalKind.AGENT, name="deepseek-m", role="manager")),
    ):
        await _app(service).set_task(SetTaskRequest(key="26.1", size="S"))


async def test_an_agent_may_still_edit_a_task_without_sizing_it(service: UDLService) -> None:
    # (The power is about the size: what an agent may already change it may still change.)
    await _manager_with_plan(service)

    with _as(Caller(kind=PrincipalKind.AGENT, name="deepseek-m", role="manager")):
        edited = await _app(service).set_task(SetTaskRequest(key="26.1", spec="What it is."))

    assert edited.spec == "What it is."


# ---------------------------------------------------------------------------------------------
# The importer reads it
# ---------------------------------------------------------------------------------------------


def test_a_card_s_header_says_its_size() -> None:
    plan = parse_plan(
        "## Phase 26: Work tracking\n"
        "**26.8 Task sizes** · needs 26.3, 26.6 · size M\n"
        "- Build: the size.\n",
        source="plan.md",
    )

    found = next(task for task in plan.features[0].tasks if task.key == "26.8")

    assert (found.size, found.needs) == ("M", ["26.3", "26.6"])


def test_a_card_s_size_and_needs_read_in_either_order() -> None:
    plan = parse_plan(
        "## Phase 26: Work tracking\n"
        "**26.8 Task sizes** · size XL · needs 26.7\n"
        "- Build: the size.\n",
        source="plan.md",
    )

    found = next(task for task in plan.features[0].tasks if task.key == "26.8")

    assert (found.size, found.needs) == ("XL", ["26.7"])


def test_a_card_without_a_size_leaves_its_task_unsized() -> None:
    plan = parse_plan(
        "## Phase 26: Work tracking\n**26.8 Task sizes** · needs 26.7\n- Build: the size.\n",
        source="plan.md",
    )

    found = next(task for task in plan.features[0].tasks if task.key == "26.8")

    assert found.size is None


def test_a_size_the_plan_does_not_have_is_a_line_it_could_not_place() -> None:
    plan = parse_plan(
        "## Phase 26: Work tracking\n**26.8 Task sizes** · size XXL\n- Build: the size.\n",
        source="plan.md",
    )

    found = next(task for task in plan.features[0].tasks if task.key == "26.8")

    assert found.size is None
    assert [(one.line, one.why) for one in plan.unplaced] == [(2, "not a size I know: 'XXL'")]


def test_a_section_below_the_cards_is_not_a_size() -> None:
    # (A `size` in a card's bullets is prose: only the header line's `· size M` is the size.)
    plan = parse_plan(
        "## Phase 26: Work tracking\n"
        "**26.8 Task sizes** · needs 26.7\n"
        "- Build: `size M` is the header's, not this line's.\n",
        source="plan.md",
    )

    found = next(task for task in plan.features[0].tasks if task.key == "26.8")

    assert found.size is None
    assert plan.unplaced == []


def test_the_plan_s_own_cards_are_sized() -> None:
    # (26.8's Done when: this task's card, imported from the plan as it is written.)
    plan = parse_plan(PLAN.read_text(), source=str(PLAN))

    found = next(task for feature in plan.features for task in feature.tasks if task.key == "26.8")

    assert found.size == "M"
    assert [one.why for one in plan.unplaced if "size" in one.why] == []


async def test_an_imported_card_s_size_reaches_its_task(
    service: UDLService, tmp_path: Path
) -> None:
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(
        "## Phase 26: Work tracking\n**26.8 Task sizes** · size L\n- Build: the size.\n"
    )

    await service.call("orch_import_plan", {"files": [str(plan_file)], "project": "udl"})

    found = await service.call("orch_get_task", {"key": "26.8"})

    assert isinstance(found, TaskInfo)
    assert found.task.size == "L"


async def test_an_imported_task_s_size_survives_a_second_import(
    service: UDLService, tmp_path: Path
) -> None:
    # (A file that no longer says the size keeps the one udl has: an import fills in, never
    # clears.)
    plan_file = tmp_path / "plan.md"
    plan_file.write_text(
        "## Phase 26: Work tracking\n**26.8 Task sizes** · size L\n- Build: the size.\n"
    )
    await service.call("orch_import_plan", {"files": [str(plan_file)], "project": "udl"})
    plan_file.write_text("## Phase 26: Work tracking\n**26.8 Task sizes** · needs 26.7\n- Build.\n")

    await service.call("orch_import_plan", {"files": [str(plan_file)], "project": "udl"})

    found = await service.call("orch_get_task", {"key": "26.8"})

    assert isinstance(found, TaskInfo)
    assert found.task.size == "L"
