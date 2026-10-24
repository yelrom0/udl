"""
---------------
- test_base.py -
---------------

What every action plugin shares (udl.plugins.actions.base): its options, validated by its
model; check(), which renders an action's templates with an event type's example when it is
saved; a run's log, kept to 16 KiB; and the Action record, a named action of a kind. Also which
errors a run may try again after.
"""

# System Imports
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from support.actions import FakeAction, Outcome, fake_action
from support.engine import registry
from support.interfaces import Harness
from udl.automations.events import Event, EventTypes
from udl.core.models import ListPluginsRequest
from udl.db.records import RECORD_KINDS
from udl.errors import (
    ActionFailed,
    ConfigError,
    ErrorCode,
    NetworkError,
    RateLimited,
    TemplateFailed,
    is_retryable,
)
from udl.plugins.actions.base import (
    MAX_LOG,
    Action,
    ActionPlugin,
    RunLog,
    example_event,
    template_context,
)
from udl.plugins.registry import ACTIONS, PLUGIN_KINDS
from udl.plugins.requirements import Requirement

FINISHED = EventTypes.default().get("udl.download.finished")
CUSTOM = EventTypes.default().get("custom.hello")


@pytest.fixture
def plugin(tmp_path: Path) -> FakeAction:
    return FakeAction(Harness(tmp_path).service())


# ---------------------------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------------------------


def test_options_are_validated_by_the_plugin_s_model(plugin: FakeAction) -> None:
    options = plugin.options({"outcome": "fail", "message": "no"})

    assert (options.outcome, options.message) == (Outcome.FAIL, "no")


@pytest.mark.parametrize("given", [{"outcome": "explode"}, {"unknown": 1}, {"retry_after": "x"}])
def test_options_that_are_not_valid_are_a_config_error(
    plugin: FakeAction, given: dict[str, Any]
) -> None:
    with pytest.raises(ConfigError, match="fake") as raised:
        plugin.options(given)

    assert raised.value.hint is not None


async def test_check_renders_templates_with_the_event_type_s_example(plugin: FakeAction) -> None:
    await plugin.check(
        {"output": "{{ event.data.url }}", "summary": "{{ event.subject }}"}, FINISHED
    )

    with pytest.raises(TemplateFailed, match="nope"):
        await plugin.check({"output": "{{ event.data.nope }}"}, FINISHED)
    with pytest.raises(ConfigError, match="retry_after"):
        await plugin.check({"retry_after": "{{ event.data.url }}"}, FINISHED)


@pytest.mark.parametrize("event_type", [None, CUSTOM], ids=["no type", "custom"])
async def test_without_an_example_only_what_has_no_template_is_validated(
    plugin: FakeAction, event_type: Any
) -> None:
    await plugin.check({"retry_after": "{{ event.data.wait }}", "summary": "{{ x }}"}, event_type)

    with pytest.raises(TemplateFailed):
        await plugin.check({"summary": "{{ unclosed"}, event_type)
    with pytest.raises(ConfigError, match="outcome"):
        await plugin.check({"outcome": "explode", "summary": "{{ x }}"}, event_type)


# ---------------------------------------------------------------------------------------------
# What templates see
# ---------------------------------------------------------------------------------------------


def test_a_template_sees_the_event_the_run_and_the_time() -> None:
    event = Event(
        id=uuid4(),
        type="custom.hello",
        source="api",
        occurred_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        subject="Hi",
        data={"name": "x"},
        depth=1,
    )
    run_id = uuid4()

    context = template_context(
        event,
        run={"id": str(run_id), "action": "hello", "attempt": 1},
        now=datetime(2026, 1, 3, tzinfo=UTC),
        account="gmail:work",
    )

    assert context["event"] == {
        "id": str(event.id),
        "type": "custom.hello",
        "source": "api",
        "account": "gmail:work",
        "account_id": None,
        "occurred_at": "2026-01-02T03:04:05Z",
        "subject": "Hi",
        "data": {"name": "x"},
        "dedupe_key": None,
        "caused_by": None,
        "depth": 1,
    }
    assert context["run"] == {"id": str(run_id), "action": "hello", "attempt": 1}
    assert context["now"] == "2026-01-03T00:00:00Z"


def test_without_an_event_a_template_sees_none() -> None:
    context = template_context(None, run=None, now=datetime(2026, 1, 3, tzinfo=UTC))

    assert (context["event"], context["run"]) == (None, None)


def test_an_example_event_is_its_type_s_example() -> None:
    event = example_event(FINISHED)

    assert event.type == FINISHED.name
    assert event.data == FINISHED.example.model_dump(mode="json")
    assert event.subject == FINISHED.subject_of(FINISHED.example)


# ---------------------------------------------------------------------------------------------
# A run's log
# ---------------------------------------------------------------------------------------------


def test_a_run_s_log_keeps_its_lines() -> None:
    log = RunLog()
    log.write("one")
    log.write("two\nthree\n")

    assert log.text == "one\ntwo\nthree\n"


def test_a_run_s_log_keeps_its_last_16_kib() -> None:
    log = RunLog()
    for n in range(2000):
        log.write(f"line {n:04}")

    assert MAX_LOG == 16 * 1024
    assert len(log.text) <= MAX_LOG
    assert log.text.endswith("line 1999\n")
    assert log.text.startswith("[earlier lines left out]\n")
    assert "line 0000" not in log.text


# ---------------------------------------------------------------------------------------------
# The Action record
# ---------------------------------------------------------------------------------------------


def test_an_action_is_a_named_configuration_record() -> None:
    action = Action(name="hello", plugin="script", options={"command": ["hello.sh"]})

    assert RECORD_KINDS["action"] is Action
    assert Action.scope == "config"
    assert ("name",) in Action.unique
    assert (action.timeout, action.retries, action.max_concurrent) == (timedelta(minutes=5), 0, 1)
    assert action.enabled is True
    assert Action.model_validate_json(action.model_dump_json()) == action


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "a b"},
        {"timeout": "0s"},
        {"retries": -1},
        {"retries": 11},
        {"max_concurrent": 0},
        {"plugin": ""},
    ],
)
def test_an_action_s_fields_are_checked(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Action.model_validate({"name": "hello", "plugin": "script", **fields})


# ---------------------------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------------------------


def test_an_action_failed_says_whether_trying_again_may_help() -> None:
    failed = ActionFailed("It failed", retryable=True, retry_after=30)

    assert failed.code is ErrorCode.ACTION_FAILED
    assert failed.may_retry is True
    assert failed.to_info().retry_after == 30
    assert ActionFailed("No").may_retry is False


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ActionFailed("x", retryable=True), True),
        (ActionFailed("x"), False),
        (NetworkError("x"), True),
        (RateLimited("x"), True),
        (ConfigError("x"), False),
    ],
)
def test_which_errors_may_be_tried_again(error: Exception, expected: bool) -> None:
    assert is_retryable(error) is expected


# ---------------------------------------------------------------------------------------------
# The plugin kind
# ---------------------------------------------------------------------------------------------


class NeedsNothingThere(FakeAction):
    name = "needs-nothing-there"
    requires = (Requirement.binary("udl-no-such-program"),)


async def test_action_plugins_are_listed_with_whether_they_can_run(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.registry = registry()
    for plugin in (fake_action("fake"), NeedsNothingThere):
        harness.registry.register(ACTIONS, plugin)

    async with harness.service() as service:
        found = {p.name: p for p in (await service.list_plugins(ListPluginsRequest())).plugins}

    assert ACTIONS in PLUGIN_KINDS
    assert (found["fake"].kind, found["fake"].available) == ("udl.actions", True)
    assert found["needs-nothing-there"].available is False
    assert found["needs-nothing-there"].missing == list(NeedsNothingThere.requires)


def test_an_action_plugin_names_its_kind_and_options() -> None:
    assert issubclass(FakeAction, ActionPlugin)
    assert FakeAction.summary
