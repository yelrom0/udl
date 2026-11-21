"""
------------------
- test_importer.py -
------------------

The markdown importer (26.7): a plan file's tracker and its cards, read as features and tasks
(`parse_plan`), and the import that puts them in udl (`orch_import_plan`).

The parser's tests run on copies of udl's own two plan files, in `tests/fixtures/plans/`, and on
small plans written for the case at hand: nothing in a tracker is silently dropped (every `- [`
line is in the report as placed or unplaced), and a dry run over the two copies places every one
of them.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.core.service import UDLService
from udl.errors import ConfigError, DoesNotExist
from udl.plugins.apps.orch.importer import (
    PLAN_KEY,
    ParsedFeature,
    ParsedPlan,
    ParsedTask,
    merge,
    parse_plan,
    read_plan_file,
)
from udl.plugins.apps.orch.models import FeatureInfo, ImportReport, Suggestions, TaskInfo

PLANS = Path(__file__).resolve().parents[4] / "fixtures" / "plans"
"""Where udl's own plan files are copied, for the parser to be tested on a file that does not
change under the test."""

_MARKS = """
# A plan

## Progress tracker

- [ ] **Phase 30: Everything, marked**

  - [ ] 30.1 open
  - [x] 30.2 done
  - [~] 30.3 merged
  - [r] 30.4 ready
  - [!] 30.5 blocked, BLOCKED: waiting on the user
  - [m] 30.6 moved
"""


def a_plan(*lines: str) -> str:
    """A plan file of the lines given, under a tracker."""
    return "# A plan\n\n## Progress tracker\n\n" + "\n".join(lines) + "\n"


def feature_of(plan: ParsedPlan, key: str) -> ParsedFeature:
    """The feature `key` of `plan`."""
    for feature in plan.features:
        if feature.key == key:
            return feature
    raise AssertionError(f"{key} is not a feature of the plan")


def task_of(plan: ParsedPlan, key: str) -> ParsedTask:
    """The task `key` of `plan`, whichever feature it is of."""
    for feature in plan.features:
        for task in feature.tasks:
            if task.key == key:
                return task
    raise AssertionError(f"{key} is not a task of the plan")


# ---------------------------------------------------------------------------------------------
# The tracker: a phase is a feature, its children are its tasks
# ---------------------------------------------------------------------------------------------


def test_a_phase_is_a_feature_and_its_children_are_its_tasks() -> None:
    plan = parse_plan((PLANS / "planning.md").read_text(), source="planning.md")

    feature = feature_of(plan, "26")
    assert feature.title == "Work tracking: projects, features, tasks, links"
    assert [task.key for task in feature.tasks][:3] == ["26.1", "26.2", "26.3"]
    assert task_of(plan, "26.6").state == "merged"


def test_a_dry_run_over_udl_s_plan_places_every_marked_line() -> None:
    plan = parse_plan((PLANS / "planning.md").read_text(), source="planning.md")

    assert plan.unplaced == []
    assert {feature.key for feature in plan.features} >= {"8", "23", "26", "30"}


def test_every_marked_line_of_the_tracker_is_placed_or_reported() -> None:
    # (Nothing in a tracker is silently dropped: a line it cannot read is in the report, with
    # its line number, and every other `- [` line is a feature or a task.)
    text = a_plan(
        "- [ ] **Phase 30: A phase**",
        "",
        "  - [ ] 30.1 a task",
        "  - [ ] not a task at all",
        "- [ ] **Phase 31: Another**",
        "  - [x] 31.1 another task",
    )

    plan = parse_plan(text, source="plan.md")

    assert [feature.key for feature in plan.features] == ["30", "31"]
    assert [task.key for task in plan.features[1].tasks] == ["31.1"]
    assert [(line.line, line.text, line.why) for line in plan.unplaced] == [
        (8, "  - [ ] not a task at all", "not a task's line: no key")
    ]


def test_a_line_before_any_phase_has_no_feature_to_be_of() -> None:
    plan = parse_plan(a_plan("  - [ ] 30.1 a task before its phase"), source="plan.md")

    assert plan.features == []
    assert [line.why for line in plan.unplaced] == ["a task before any phase"]


def test_a_phase_s_own_section_is_its_spec() -> None:
    text = (
        "# A plan\n\n## Progress tracker\n\n- [ ] **Phase 30: A phase**\n  - [ ] 30.1 one\n"
        "\n## Phase 30: A phase\n\nWhat the phase is for.\n\n### Tasks\n\n30.1 does one thing.\n"
        "\n## Phase 31: Another\n\nAnother phase's own words.\n"
    )

    plan = parse_plan(text, source="plan.md")

    assert feature_of(plan, "30").spec.startswith("What the phase is for.")
    assert "30.1 does one thing." in feature_of(plan, "30").spec
    assert feature_of(plan, "31").spec.strip() == "Another phase's own words."


def test_a_multi_phase_file_s_other_sections_are_the_plan_s() -> None:
    # (Decisions, Concepts, Spec: what is not a phase's is the plan's own, under the key `plan`.)
    text = (
        "# A plan\n\n## Progress tracker\n\n- [ ] **Phase 30: A phase**\n  - [ ] 30.1 one\n"
        "\n## Decisions\n\nD-1: it is decided.\n\n## Phase 30: A phase\n\nIts own words.\n"
    )

    plan = parse_plan(text, source="plan.md")

    assert feature_of(plan, PLAN_KEY).title == "The plan"
    assert "D-1: it is decided." in feature_of(plan, PLAN_KEY).spec


# ---------------------------------------------------------------------------------------------
# The marks
# ---------------------------------------------------------------------------------------------


def test_the_marks_are_the_states() -> None:
    plan = parse_plan(_MARKS, source="plan.md")

    states = {task.key: task.state for task in plan.features[0].tasks}

    assert states == {
        "30.1": "open",
        "30.2": "done",
        "30.3": "merged",
        "30.4": "ready",
        "30.5": "blocked",
        "30.6": "moved",
    }


def test_a_blocked_line_keeps_what_it_waits_on() -> None:
    plan = parse_plan(_MARKS, source="plan.md")

    assert task_of(plan, "30.5").blocked_on == "waiting on the user"


def test_a_mark_the_parser_does_not_know_is_reported() -> None:
    plan = parse_plan(
        a_plan("- [ ] **Phase 30: A phase**", "  - [?] 30.1 a strange mark"), source="plan.md"
    )

    assert [line.why for line in plan.unplaced] == ["not a mark I know: '?'"]


def test_a_tracker_s_sub_bullets_join_the_task_s_spec() -> None:
    plan = parse_plan(
        a_plan(
            "- [ ] **Phase 30: A phase**",
            "  - [ ] 30.1 a task:",
            "    - one thing",
            "    - another thing",
        ),
        source="plan.md",
    )

    assert task_of(plan, "30.1").spec == "one thing\nanother thing"


# ---------------------------------------------------------------------------------------------
# A single-phase file: the heading is the feature
# ---------------------------------------------------------------------------------------------


def test_a_single_phase_file_s_heading_is_the_feature() -> None:
    plan = parse_plan((PLANS / "03-proxmox.md").read_text(), source="03-proxmox.md")

    assert [feature.key for feature in plan.features] == ["22"]
    assert feature_of(plan, "22").title == "Proxmox plugin"
    assert [task.key for task in feature_of(plan, "22").tasks][:3] == ["22.0", "22.1", "22.2"]
    assert plan.unplaced == []


def test_a_single_phase_file_s_other_sections_are_the_feature_s() -> None:
    text = (
        "# UDL Phase 22: Proxmox plugin (`proxmox`)\n\n## Progress tracker\n\n"
        "- [ ] **22.0** Workspace:\n  - a checkout\n\n## Decisions\n\nP-D1: a decision.\n"
    )

    plan = parse_plan(text, source="03-proxmox.md")

    assert "P-D1: a decision." in feature_of(plan, "22").spec


# ---------------------------------------------------------------------------------------------
# A card: a task's spec and its needs
# ---------------------------------------------------------------------------------------------


def test_a_card_is_a_task_s_spec_and_its_needs() -> None:
    text = (
        "# The orchestrator\n\n## Phase 26: Work tracking\n\n"
        "**26.7 Markdown import** · needs 26.6\n"
        "- Build: the importer reads a plan file.\n"
        "  - And its cards.\n"
        "\n**26.8 Task sizes** · needs 26.3, 26.6, 26.7\n"
        "- Build: a size.\n"
    )

    plan = parse_plan(text, source="02-agent-orchastrator.md")

    assert task_of(plan, "26.7").needs == ["26.6"]
    assert task_of(plan, "26.7").spec.startswith("Build: the importer reads a plan file.")
    assert "And its cards." in task_of(plan, "26.7").spec
    assert task_of(plan, "26.8").needs == ["26.3", "26.6", "26.7"]
    assert "a size" in task_of(plan, "26.8").spec


def test_a_card_is_of_the_phase_it_is_under() -> None:
    text = (
        "## Phase 26: Work tracking\n\n**26.7 Markdown import** · needs 26.6\n- Build: one.\n"
        "\n## Phase 27: Agents\n\n**27.4 Suggest** · needs 26.3\n- Build: another.\n"
    )

    plan = parse_plan(text, source="02-agent-orchastrator.md")

    assert [feature.key for feature in plan.features] == ["26", "27"]
    assert [task.key for task in plan.features[0].tasks] == ["26.7"]


def test_a_range_of_needs_is_each_of_them() -> None:
    text = (
        "## Phase 17: Hosts\n\n**17.8 One** · needs 17.5\u201317.7\n- Build: one.\n"
        "\n**17.9 Two** · needs 8.5\n- Build: two.\n"
    )

    plan = parse_plan(text, source="02-agent-orchastrator.md")

    assert task_of(plan, "17.8").needs == ["17.5", "17.6", "17.7"]
    assert task_of(plan, "17.9").needs == ["8.5"]


# ---------------------------------------------------------------------------------------------
# Merging the files: the tracker's titles and marks, the cards' specs and needs
# ---------------------------------------------------------------------------------------------


def test_a_card_fills_in_what_the_tracker_only_names() -> None:
    tracker = parse_plan(
        a_plan("- [ ] **Phase 26: Work tracking**", "  - [~] 26.7 Markdown import"),
        source="planning.md",
    )
    cards = parse_plan(
        "## Phase 26: Work tracking\n\n**26.7 Markdown import** · needs 26.6\n- Build: one.\n",
        source="02-agent-orchastrator.md",
    )

    plan = merge(tracker, cards)

    task = task_of(plan, "26.7")
    assert task.state == "merged"
    assert task.needs == ["26.6"]
    assert task.spec.startswith("Build: one.")
    assert len(plan.features) == 1


# ---------------------------------------------------------------------------------------------
# The import: `orch_import_plan`
# ---------------------------------------------------------------------------------------------


def a_file(tmp_path: Path, text: str, name: str = "plan.md") -> Path:
    """A plan file at `tmp_path`."""
    path = tmp_path / name
    path.write_text(text)
    return path


async def test_an_import_creates_the_features_and_their_tasks(
    service: UDLService, tmp_path: Path
) -> None:
    path = a_file(
        tmp_path,
        "# A plan\n\n## Progress tracker\n\n- [ ] **Phase 30: A new phase**\n"
        "  - [ ] 30.1 A new task\n  - [x] 30.2 A done one\n\n## Phase 30: A new phase\n"
        "\nWhat it is for.\n",
    )

    report = await service.call("orch_import_plan", {"files": [str(path)]})

    assert isinstance(report, ImportReport)
    assert report.created == ["30", "30.1", "30.2"]
    assert (report.updated, report.unchanged, report.kept, report.unplaced) == ([], [], [], [])
    feature = await service.call("orch_get_feature", {"key": "30"})
    assert isinstance(feature, FeatureInfo)
    assert feature.feature.title == "A new phase"
    assert "What it is for." in feature.feature.spec
    task = await service.call("orch_get_task", {"key": "30.2"})
    assert isinstance(task, TaskInfo)
    assert task.task.state == "done"


async def test_a_second_import_changes_nothing(service: UDLService, tmp_path: Path) -> None:
    path = a_file(
        tmp_path,
        a_plan("- [ ] **Phase 30: A new phase**", "  - [ ] 30.1 A new task"),
    )
    await service.call("orch_import_plan", {"files": [str(path)]})

    report = await service.call("orch_import_plan", {"files": [str(path)]})

    assert isinstance(report, ImportReport)
    assert (report.created, report.updated) == ([], [])
    assert report.unchanged == ["30", "30.1"]


async def test_an_edit_updates_what_changed(service: UDLService, tmp_path: Path) -> None:
    path = a_file(tmp_path, a_plan("- [ ] **Phase 30: A phase**", "  - [ ] 30.1 A task"))
    await service.call("orch_import_plan", {"files": [str(path)]})

    path = a_file(tmp_path, a_plan("- [ ] **Phase 30: A phase**", "  - [ ] 30.1 A better task"))
    report = await service.call("orch_import_plan", {"files": [str(path)]})

    assert isinstance(report, ImportReport)
    assert (report.created, report.unchanged) == ([], ["30"])
    assert report.updated == ["30.1"]
    task = await service.call("orch_get_task", {"key": "30.1"})
    assert isinstance(task, TaskInfo)
    assert task.task.title == "A better task"


async def test_a_task_that_moved_on_in_udl_keeps_its_state(
    service: UDLService, tmp_path: Path
) -> None:
    # (The plan says open; udl has the task ready. The import says so and does not undo it.)
    path = a_file(tmp_path, a_plan("- [ ] **Phase 26: Work tracking**", "  - [ ] 26.1 Projects"))
    await service.call("orch_move_task", {"key": "26.1", "to": "queued"})

    report = await service.call("orch_import_plan", {"files": [str(path)]})

    assert isinstance(report, ImportReport)
    assert report.kept == ["26.1: udl has it queued, the plan says open"]
    task = await service.call("orch_get_task", {"key": "26.1"})
    assert isinstance(task, TaskInfo)
    assert task.task.state == "queued"


async def test_a_dry_run_writes_nothing(service: UDLService, tmp_path: Path) -> None:
    path = a_file(tmp_path, a_plan("- [ ] **Phase 31: Not yet**", "  - [ ] 31.1 A task"))

    report = await service.call("orch_import_plan", {"files": [str(path)], "dry_run": True})

    assert isinstance(report, ImportReport)
    assert report.created == ["31", "31.1"]
    with pytest.raises(DoesNotExist):
        await service.call("orch_get_feature", {"key": "31"})


async def test_an_import_reports_the_lines_it_could_not_place(
    service: UDLService, tmp_path: Path
) -> None:
    path = a_file(
        tmp_path,
        a_plan("- [ ] **Phase 30: A phase**", "  - [ ] this line is not a task"),
    )

    report = await service.call("orch_import_plan", {"files": [str(path)]})

    assert isinstance(report, ImportReport)
    assert report.created == ["30"]
    assert [(line.source, line.line, line.why) for line in report.unplaced] == [
        (str(path), 6, "not a task's line: no key")
    ]


async def test_a_file_that_is_not_there_is_refused(service: UDLService, tmp_path: Path) -> None:
    with pytest.raises(DoesNotExist, match=r"nowhere\.md"):
        await service.call("orch_import_plan", {"files": [str(tmp_path / "nowhere.md")]})


async def test_what_an_import_leaves_suggests_the_plan_s_own_order(
    service: UDLService,
) -> None:
    # (27.4's Done when, over the imported plan: the suggestions put 22.1 before 22.2 — the
    # import leaves tasks a suggestion can rank, not a pile of keys.)
    await service.call(
        "orch_import_plan",
        {"files": [str(PLANS / "planning.md"), str(PLANS / "03-proxmox.md")]},
    )

    answer = await service.call("orch_suggest", {"limit": 100})

    assert isinstance(answer, Suggestions)
    keys = [one.task for one in answer.suggestions if one.feature == "22"]
    assert keys.index("22.1") < keys.index("22.2")


async def test_the_two_plan_files_of_udl_import_with_nothing_unplaced(
    service: UDLService,
) -> None:
    # (26.7's Done when: a dry run over udl's two plan files places every marked line.)
    report = await service.call(
        "orch_import_plan",
        {
            "files": [str(PLANS / "planning.md"), str(PLANS / "03-proxmox.md")],
            "dry_run": True,
        },
    )

    assert isinstance(report, ImportReport)
    assert report.unplaced == []
    assert "22" in report.created


# ---------------------------------------------------------------------------------------------
# The lines the review found misread
# ---------------------------------------------------------------------------------------------


def test_a_card_whose_key_ends_in_a_letter_is_read() -> None:
    # (27.3a is a task of its own: its card is not 27.3's, and 27.3's bullets stop at it.)
    plan = parse_plan(
        "# A plan\n\n"
        "**27.3 Queues** · needs 27.2\n"
        "- Its own bullet.\n\n"
        "**27.3a One write** · needs 27.3\n"
        "- Its own too.\n",
        source="a.md",
    )

    assert task_of(plan, "27.3").needs == ["27.2"]
    assert task_of(plan, "27.3").spec == "Its own bullet."
    assert task_of(plan, "27.3a").needs == ["27.3"]
    assert task_of(plan, "27.3a").spec == "Its own too."


def test_a_phase_with_an_unknown_mark_files_its_children_nowhere() -> None:
    # (A mark the parser does not know leaves no phase: its children are not the last one's.)
    plan = parse_plan(
        a_plan(
            "- [ ] **Phase 26: Work tracking**",
            "  - [ ] 26.1 open",
            "- [?] **Phase 27: Agents**",
            "  - [ ] 27.1 tokens",
        ),
        source="a.md",
    )

    assert [task.key for task in feature_of(plan, "26").tasks] == ["26.1"]
    assert "27.1" not in {task.key for feature in plan.features for task in feature.tasks}
    assert [(line.line, line.why) for line in plan.unplaced] == [
        (7, "not a mark I know: '?'"),
        (8, "a task before any phase"),
    ]


def test_a_blocked_bold_line_keeps_its_reason() -> None:
    plan = parse_plan(
        "# UDL Phase 22: Proxmox plugin\n\n"
        "## Progress tracker\n\n"
        "- [!] **22.0** Workspace: BLOCKED: Q-3, the account\n",
        source="a.md",
    )

    found = task_of(plan, "22.0")
    assert found.state == "blocked"
    assert found.blocked_on == "Q-3, the account"


def test_a_plan_file_is_markdown(tmp_path: Path) -> None:
    # (A record keeps a file's sections, so only a plan's own kind of file is read.)
    given = tmp_path / "notes.txt"
    given.write_text("# A plan\n\n## Progress tracker\n\n- [ ] **Phase 26: Work**\n")

    with pytest.raises(ConfigError, match="markdown"):
        read_plan_file(str(given))
