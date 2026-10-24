"""
-----------------------
- test_automations.py -
-----------------------

udl events, udl subs, udl actions, udl runs and udl status: the automations' commands (made from
their operations), what they show, and the whole of it: an action, a subscription and an
emitted event make a run, in the runtime of a `udl serve`
"""

# System Imports
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.actions import fake_action
from support.cli import CLI
from support.interfaces import Harness
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.runtime import AutomationRuntime
from udl.automations.schedules import CronSpec, Schedule
from udl.config import AutomationsSettings
from udl.db.manager import DBManager
from udl.db.records import RecordQuery
from udl.plugins.actions.script import ScriptAction
from udl.plugins.registry import ACTIONS

FAKE = fake_action("fake")


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(tmp_path, automations=AutomationsSettings(scripts_dir=tmp_path / "scripts"))
    found.registry.register(ACTIONS, FAKE)
    found.registry.register(ACTIONS, ScriptAction)
    return found


@pytest.fixture
def cli(harness: Harness) -> CLI:
    return CLI(harness)


def ok(cli: CLI, *args: str | Path, stdin: str | None = None) -> str:
    result = cli(*args, stdin=stdin)
    assert result.exit_code == 0, result.output
    return result.stdout


def as_json(cli: CLI, *args: str | Path) -> Any:
    return json.loads(ok(cli, *args, "--json"))


def setup(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake", "-O", "output=1", "-O", "summary=Said hi")
    ok(
        cli,
        "subs",
        "set",
        "greet",
        "--on",
        "custom.hello",
        "--action",
        "hello",
        "--where",
        'name == "x"',
    )


# ---------------------------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------------------------


def test_the_event_types_are_listed(cli: CLI) -> None:
    shown = ok(cli, "events", "types")
    actions = as_json(cli, "events", "types", "udl.action.*")

    assert "udl.download.finished" in shown
    assert "custom.*" in shown
    assert {found["name"] for found in actions["types"]} == {
        "udl.action.succeeded",
        "udl.action.failed",
        "udl.action.awaiting_approval",
    }


def test_an_event_is_emitted_listed_and_shown(cli: CLI) -> None:
    emitted = as_json(cli, "events", "emit", "custom.hello", "--data", '{"name": "x"}')

    listed = ok(cli, "events", "list")
    shown = ok(cli, "events", "show", emitted["id"])

    assert (emitted["type"], emitted["source"], emitted["data"]) == (
        "custom.hello",
        "cli",
        {"name": "x"},
    )
    assert "custom.hello" in listed
    assert emitted["id"] in listed
    assert '"name": "x"' in shown
    assert "pending" in shown


def test_an_emitted_event_is_shown(cli: CLI) -> None:
    shown = ok(cli, "events", "emit", "custom.hello", "--data", '{"subject": "Hi there"}')

    assert "Hi there" in shown


def test_no_events_is_said(cli: CLI) -> None:
    assert "No events." in ok(cli, "events", "list")


def test_only_custom_events_are_emitted(cli: CLI) -> None:
    result = cli("events", "emit", "udl.download.finished")

    assert result.exit_code == 2
    assert "custom" in result.output


def test_an_event_is_replayed(cli: CLI, harness: Harness) -> None:
    setup(cli)
    emitted = as_json(cli, "events", "emit", "custom.hello", "--data", '{"name": "x"}')

    async def dispatch() -> None:
        async with harness.service() as service:
            await service.bus.dispatch_pending()

    asyncio.run(dispatch())
    replayed = ok(cli, "events", "replay", emitted["id"])
    shown = ok(cli, "events", "show", emitted["id"])

    assert "hello" in replayed
    assert "dispatched" in shown
    assert shown.count("hello") >= 2  # (its runs: the first and the replay's)


def test_an_unknown_event_is_said(cli: CLI) -> None:
    result = cli("events", "show", "00000000-0000-4000-8000-000000000009")

    assert result.exit_code == 1
    assert "No event" in result.output


# ---------------------------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------------------------


def test_a_subscription_is_saved_shown_and_listed(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake")

    saved = as_json(
        cli,
        "subs",
        "set",
        "greet",
        "--on",
        "custom.hello",
        "--on",
        "custom.hi",
        "--action",
        "hello",
        "--where",
        'name == "x" and not quiet',
        "--throttle",
        "5 per 1h",
        "--description",
        "Says hello",
    )
    shown = ok(cli, "subs", "show", "greet")
    listed = ok(cli, "subs", "list")

    assert saved["events"] == ["custom.hello", "custom.hi"]
    assert saved["filter_text"] == 'name == "x" and not quiet'
    assert saved["throttle"] == {"max_runs": 5, "per": "1h"}
    assert 'name == "x" and not quiet' in shown
    assert "5 per 1h" in shown
    assert "Says hello" in shown
    assert "greet" in listed


def test_no_subscriptions_is_said(cli: CLI) -> None:
    assert "No subscriptions" in ok(cli, "subs", "list")


def test_a_bad_filter_says_where(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake")

    result = cli("subs", "set", "greet", "--on", "custom.x", "--action", "hello", "--where", "a ==")

    assert result.exit_code == 2
    assert "at 5" in result.output


def test_a_filter_that_does_not_fit_is_refused(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake")

    result = cli(
        "subs",
        "set",
        "greet",
        "--on",
        "udl.download.finished",
        "--action",
        "hello",
        "--where",
        "nope",
    )

    assert result.exit_code == 2
    assert "nope" in result.output
    assert "url" in result.output  # (its fields are listed)


def test_a_subscription_is_tested(cli: CLI) -> None:
    setup(cli)
    ok(cli, "events", "emit", "custom.hello", "--data", '{"name": "x"}')
    ok(cli, "events", "emit", "custom.hello", "--data", '{"name": "y"}')

    tested = ok(cli, "subs", "test", "greet")

    assert "It would have matched 1 of 2 events." in tested
    assert 'name == "x"' in tested


def test_a_subscription_is_deleted(cli: CLI) -> None:
    setup(cli)

    assert "Deleted." in ok(cli, "subs", "rm", "greet")
    assert "nothing to delete" in ok(cli, "subs", "rm", "greet")


# ---------------------------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------------------------


def test_an_action_s_options_are_key_value_pairs(cli: CLI) -> None:
    saved = as_json(
        cli, "actions", "set", "hello", "--kind", "fake", "-O", "output=[1, 2]", "-O", "summary=Hi"
    )

    assert (saved["plugin"], saved["options"]) == ("fake", {"output": [1, 2], "summary": "Hi"})


def test_an_action_comes_from_a_file(cli: CLI, tmp_path: Path) -> None:
    action = tmp_path / "action.yaml"
    action.write_text("name: hello\nplugin: fake\noptions:\n  output: 1\nretries: 2\n")

    saved = as_json(cli, "actions", "set", "-f", action, "-O", "summary=Hi")

    assert saved["options"] == {"output": 1, "summary": "Hi"}
    assert saved["retries"] == 2


def test_an_action_is_shown_and_listed(cli: CLI) -> None:
    setup(cli)

    shown = ok(cli, "actions", "show", "hello")
    listed = ok(cli, "actions", "list")

    assert "fake" in shown
    assert "greet" in shown  # (the subscription that runs it)
    assert "5m" in shown
    assert "hello" in listed


def test_no_actions_is_said(cli: CLI) -> None:
    assert "No actions" in ok(cli, "actions", "list")


def test_an_action_s_warnings_are_shown(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake", "--no-enabled")

    assert "disabled" in ok(cli, "actions", "show", "hello")


def test_an_action_runs_now(cli: CLI) -> None:
    setup(cli)

    ran = as_json(cli, "actions", "run", "hello")
    shown = ok(cli, "actions", "run", "hello")
    dry = ok(cli, "actions", "run", "hello", "--dry-run")

    assert (ran["run"]["status"], ran["run"]["output"]) == ("succeeded", 1)
    assert "succeeded" in shown
    assert "Said hi" in shown
    assert "Dry run" in dry


def test_an_action_a_subscription_runs_is_not_deleted(cli: CLI) -> None:
    setup(cli)

    result = cli("actions", "rm", "hello")

    assert result.exit_code == 1
    assert "greet" in result.output


def test_an_action_is_deleted(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake")

    assert "Deleted." in ok(cli, "actions", "rm", "hello")


def test_the_scripts_are_listed(cli: CLI, tmp_path: Path) -> None:
    missing = ok(cli, "actions", "scripts")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "hello.sh").write_text("#!/bin/sh\necho hi\n")

    listed = ok(cli, "actions", "scripts")

    assert "no scripts_dir" in missing
    assert "hello.sh" in listed


# ---------------------------------------------------------------------------------------------
# Runs and the status
# ---------------------------------------------------------------------------------------------


def test_runs_are_listed_shown_and_cancelled(cli: CLI, harness: Harness) -> None:
    setup(cli)
    ok(cli, "events", "emit", "custom.hello", "--data", '{"name": "x"}')

    async def dispatch() -> None:
        async with harness.service() as service:
            await service.bus.dispatch_pending()

    asyncio.run(dispatch())
    [run] = as_json(cli, "runs", "list", "--status", "queued")["runs"]
    shown = ok(cli, "runs", "show", run["id"])
    cancelled = ok(cli, "runs", "cancel", run["id"])
    listed = ok(cli, "runs", "list")

    assert "queued" in shown
    assert "greet" in shown
    assert "cancelled" in cancelled
    assert "cancelled" in listed


def test_no_runs_is_said(cli: CLI) -> None:
    assert "No runs." in ok(cli, "runs", "list")


def test_a_failed_run_says_why(cli: CLI) -> None:
    ok(
        cli,
        "actions",
        "set",
        "fail",
        "--kind",
        "fake",
        "-O",
        "outcome=fail",
        "-O",
        "message=No paper",
    )

    shown = ok(cli, "actions", "run", "fail")
    listed = ok(cli, "runs", "list")

    assert "No paper" in shown
    assert "No paper" in listed


def test_the_status_is_said(cli: CLI) -> None:
    shown = ok(cli, "status")
    said = as_json(cli, "status")

    assert "stopped" in shown
    assert said["automations"] == "stopped"


def test_the_status_says_the_next_schedules(cli: CLI, harness: Harness) -> None:
    async def schedule(db: DBManager) -> None:
        await db.upsert_record(
            Schedule(
                name="digest",
                spec=CronSpec(cron="0 8 * * *"),
                next_run_at=datetime(2030, 1, 1, 8, tzinfo=UTC),
            )
        )

    harness.seed(schedule)

    shown = ok(cli, "status")

    assert "Next schedules" in shown
    assert "digest" in shown


# ---------------------------------------------------------------------------------------------
# The whole of it
# ---------------------------------------------------------------------------------------------


def test_an_emitted_event_runs_a_script_in_udl_serve_s_runtime(
    cli: CLI, harness: Harness, tmp_path: Path
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    hello = scripts / "hello.sh"
    hello.write_text('#!/bin/sh\necho "{\\"said\\": \\"hello\\"}"\n')
    hello.chmod(0o755)

    ok(cli, "actions", "set", "hello", "--kind", "script", "-O", 'command=["hello.sh"]')
    ok(cli, "subs", "set", "greet", "--on", "custom.hello", "--action", "hello")
    ok(cli, "events", "emit", "custom.hello", "--data", '{"name": "x"}')

    async def serve() -> None:  # (as `udl serve` does)
        async with harness.service() as service, AutomationRuntime(service):
            for _ in range(200):
                runs = await service.db.list_records(ActionRun, RecordQuery())
                if runs and runs[0].status is RunStatus.SUCCEEDED:
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.1)  # (its end is published)

    asyncio.run(serve())
    [run] = as_json(cli, "runs", "list")["runs"]
    events = as_json(cli, "events", "list")["events"]

    assert run["status"] == "succeeded"
    detail = as_json(cli, "runs", "show", run["id"])
    assert detail["output"] == {"said": "hello"}
    assert "udl.action.succeeded" in [found["type"] for found in events]
