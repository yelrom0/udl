"""
---------------------
- test_schedules.py -
---------------------

udl schedules: a cron job is one command (`udl schedules set digest --cron "0 8 * * 1-5"
--action morning-digest`), and schedules are listed, shown, previewed, run by hand and deleted;
a schedule's subscription says how to change it
"""

# System Imports
import json
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.actions import fake_action
from support.cli import CLI
from support.interfaces import Harness
from udl.config import AutomationsSettings
from udl.plugins.registry import ACTIONS

FAKE = fake_action("fake")


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(
        tmp_path,
        automations=AutomationsSettings(scripts_dir=tmp_path / "scripts", timezone="UTC"),
    )
    found.registry.register(ACTIONS, FAKE)
    return found


def ok(cli: CLI, *args: str) -> str:
    result = cli(*args)
    assert result.exit_code == 0, result.output
    return result.stdout


def as_json(cli: CLI, *args: str) -> Any:
    return json.loads(ok(cli, *args, "--json"))


def test_a_cron_job_is_one_command(cli: CLI) -> None:
    ok(cli, "actions", "set", "morning-digest", "--kind", "fake")

    shown = ok(
        cli, "schedules", "set", "digest", "--cron", "0 8 * * 1-5", "--action", "morning-digest"
    )

    assert "0 8 * * 1-5" in shown
    assert "morning-digest" in shown
    [linked] = as_json(cli, "subs", "list")["subscriptions"]
    assert (linked["name"], linked["filter_text"]) == ("schedule:digest", 'schedule == "digest"')


def test_no_actions_is_given_as_input(cli: CLI) -> None:
    ok(cli, "actions", "set", "morning-digest", "--kind", "fake")
    ok(cli, "schedules", "set", "digest", "--cron", "@daily", "--action", "morning-digest")

    result = cli(
        "schedules", "set", "digest", "--cron", "@daily", "-f", "-", stdin='{"actions": []}'
    )

    assert result.exit_code == 0, result.output
    assert as_json(cli, "subs", "list")["subscriptions"] == []
    assert as_json(cli, "schedules", "show", "digest")["actions"] == []


def test_every_with_its_options(cli: CLI) -> None:
    saved = as_json(
        cli,
        "schedules",
        "set",
        "tick",
        "--every",
        "15m",
        "--start",
        "2030-01-01T09:00",
        "--tz",
        "Europe/London",
        "--misfire",
        "once",
        "--misfire-grace",
        "2h",
        "--jitter",
        "30s",
        "--data",
        '{"report": "daily"}',
        "--description",
        "Ticks",
    )

    assert saved["spec"] == {"every": "15m", "start": "2030-01-01T09:00:00"}
    assert (saved["zone"], saved["misfire"], saved["misfire_grace"], saved["jitter"]) == (
        "Europe/London",
        "once",
        "2h",
        "30s",
    )
    assert saved["next_run_at"] == "2030-01-01T09:00:00Z"
    assert saved["data"] == {"report": "daily"}


def test_at_once(cli: CLI) -> None:
    saved = as_json(cli, "schedules", "set", "once", "--at", "2099-11-01T09:00")

    assert saved["spec_text"] == "at 2099-11-01T09:00:00"
    assert saved["next_times"] == ["2099-11-01T09:00:00Z"]


def test_cron_every_and_at_are_one_of(cli: CLI) -> None:
    result = cli("schedules", "set", "x", "--cron", "@daily", "--every", "1h")

    assert result.exit_code == 2
    assert "exactly one of cron, every and at" in result.output


def test_an_at_time_gone_by_says_what_to_do(cli: CLI) -> None:
    result = cli("schedules", "set", "x", "--at", "2001-01-01T09:00")

    assert result.exit_code == 2  # (a ConfigError, as a filter that does not fit is)
    assert "gone by" in result.output
    assert "--no-enabled" in result.output


def test_schedules_are_listed_shown_and_deleted(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake")
    ok(cli, "schedules", "set", "digest", "--cron", "@daily", "--action", "hello")
    ok(cli, "schedules", "set", "weekly", "--cron", "@weekly", "--no-enabled")

    listed = ok(cli, "schedules", "list")
    shown = ok(cli, "schedules", "show", "digest")
    enabled = as_json(cli, "schedules", "list", "--enabled")
    deleted = ok(cli, "schedules", "rm", "digest")

    assert "digest" in listed
    assert "weekly" in listed
    assert "@daily" in shown
    assert "hello" in shown
    assert "Next" in shown
    assert [found["name"] for found in enabled["schedules"]] == ["digest"]
    assert "Deleted." in deleted
    assert as_json(cli, "subs", "list")["subscriptions"] == []


def test_no_schedules_is_said(cli: CLI) -> None:
    assert "No schedules" in ok(cli, "schedules", "list")


def test_an_unknown_schedule_is_said(cli: CLI) -> None:
    result = cli("schedules", "show", "nope")

    assert result.exit_code == 1
    assert "No schedule called 'nope'" in result.output


def test_a_spec_is_previewed(cli: CLI) -> None:
    shown = ok(cli, "schedules", "preview", "30 1 * * *", "-n", "3", "--tz", "Europe/London")
    said = as_json(
        cli,
        "schedules",
        "preview",
        "every 1h",
        "--count",
        "2",
        "--after",
        "2030-01-01T00:00:00Z",
    )

    assert "Europe/London" in shown
    assert "01:30" in shown
    assert [time["utc"] for time in said["times"]] == [
        "2030-01-01T01:00:00Z",
        "2030-01-01T02:00:00Z",
    ]


def test_a_spec_that_is_none_is_refused(cli: CLI) -> None:
    result = cli("schedules", "preview", "now and then")

    assert result.exit_code == 2
    assert "is not a schedule" in result.output


def test_a_schedule_is_run_by_hand(cli: CLI) -> None:
    ok(cli, "schedules", "set", "digest", "--cron", "@daily")

    shown = ok(cli, "schedules", "run", "digest")
    [event] = as_json(cli, "events", "list", "--type", "schedule.fired")["events"]

    assert "The digest schedule was run by hand" in shown
    assert (event["source"], event["data"]["manual"]) == ("cli", True)


def test_a_schedule_s_subscription_says_how_to_change_it(cli: CLI) -> None:
    ok(cli, "actions", "set", "hello", "--kind", "fake")
    ok(cli, "schedules", "set", "digest", "--cron", "@daily", "--action", "hello")

    result = cli("subs", "rm", "schedule:digest")

    assert result.exit_code == 1
    assert "udl schedules rm digest" in result.output
