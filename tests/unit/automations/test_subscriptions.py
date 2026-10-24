"""
-------------------------
- test_subscriptions.py -
-------------------------

Subscriptions: which events (types or globs of them, in which accounts, through which filter)
run which actions, and at most how often (a throttle). Matching is a pure function of the
subscription and the event; check_subscription() is what saving one checks
"""

# System Imports
from datetime import UTC, datetime, timedelta
from typing import Any, Self
from uuid import uuid4

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

# Local Imports
from udl.automations.events import Event, EventType, EventTypes
from udl.automations.filters import parse
from udl.automations.subscriptions import (
    Subscription,
    Throttle,
    check_subscription,
    matches,
)
from udl.db.models import JsonObject
from udl.db.records import RECORD_KINDS
from udl.errors import AlreadyExists, ConfigError, ErrorCode, InvalidFilter


class _Data(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Mail(_Data):
    subject: str
    """The subject."""
    size: int = 0
    """Bytes."""

    @classmethod
    def example(cls) -> Self:
        return cls(subject="Hi")


class Label(_Data):
    label: str
    """The label."""


def _type(name: str, data: type[BaseModel], example: BaseModel) -> EventType[Any]:
    return EventType(
        name=name, data=data, summary=name, example=example, subject=lambda _: name, source="mail"
    )


def types() -> EventTypes:
    found = EventTypes.default()
    found.register(_type("mail.message.received", Mail, Mail.example()))
    found.register(_type("mail.message.sent", Mail, Mail.example()))
    found.register(_type("mail.label.added", Label, Label(label="x")))
    return found


def event(type_: str = "mail.message.received", **data: JsonValue) -> Event:
    return Event(
        id=uuid4(),
        type=type_,
        source="mail",
        occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        subject="x",
        data={"subject": "Hello", "size": 10, **data},
    )


def sub(**fields: Any) -> Subscription:
    fields.setdefault("name", "greet")
    fields.setdefault("events", ["mail.message.received"])
    fields.setdefault("actions", ["hello"])
    return Subscription.model_validate(fields)


# ---------------------------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("events", "type_", "expected"),
    [
        (["mail.message.received"], "mail.message.received", True),
        (["mail.message.received"], "mail.message.sent", False),
        (["mail.*"], "mail.label.added", True),
        (["mail.message.*"], "mail.label.added", False),
        (["*"], "custom.hello", True),
        (["custom.*", "mail.label.added"], "custom.hello", True),
        (["mail.message.?ent"], "mail.message.sent", True),
    ],
)
def test_a_subscription_matches_its_types_and_globs(
    events: list[str], type_: str, expected: bool
) -> None:
    assert matches(sub(events=events), event(type_), None) is expected


def test_a_disabled_subscription_never_matches() -> None:
    assert matches(sub(enabled=False), event(), None) is False


def test_a_subscription_for_some_accounts_matches_only_their_events() -> None:
    work = sub(accounts=["mail:work"])

    assert matches(work, event(), "mail:work") is True
    assert matches(work, event(), "mail:home") is False
    assert matches(work, event(), None) is False
    assert matches(sub(), event(), "mail:home") is True  # (every account)


def test_no_accounts_is_every_account() -> None:
    assert sub(accounts=[]).accounts is None


def test_a_subscription_s_filter_must_match() -> None:
    big = sub(filter="size > 100")

    assert matches(big, event(size=1000), None) is True
    assert matches(big, event(size=5), None) is False
    assert matches(sub(filter=None), event(), None) is True


def test_the_filter_is_the_shorthand_or_a_tree() -> None:
    from_text = sub(filter='subject == "x" and size > 1')
    from_tree = sub(filter=from_text.model_dump(mode="json")["filter"])

    assert from_text.filter == from_tree.filter == parse('subject == "x" and size > 1')


# ---------------------------------------------------------------------------------------------
# Throttles
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "runs", "per"),
    [
        ("10 per 1h", 10, timedelta(hours=1)),
        ("  1  per  30s ", 1, timedelta(seconds=30)),
        ({"max_runs": 3, "per": "1d"}, 3, timedelta(days=1)),
        ({"max_runs": 3, "per": 60}, 3, timedelta(minutes=1)),
    ],
)
def test_a_throttle_is_runs_per_time(given: object, runs: int, per: timedelta) -> None:
    throttle = Throttle.model_validate(given)

    assert (throttle.max_runs, throttle.per) == (runs, per)


@pytest.mark.parametrize(
    "given", ["10 an hour", "0 per 1h", "10 per never", "per 1h", "1 per 0s", 5]
)
def test_a_throttle_that_is_not_one_is_refused(given: object) -> None:
    with pytest.raises(ValidationError):
        Throttle.model_validate(given)


def test_a_throttle_is_written_as_its_shorthand() -> None:
    throttle = Throttle.model_validate("10 per 1h")

    assert str(throttle) == "10 per 1h"
    assert throttle.model_dump(mode="json") == {"max_runs": 10, "per": "1h"}
    assert sub(throttle="2 per 5m").throttle == Throttle(max_runs=2, per=timedelta(minutes=5))


# ---------------------------------------------------------------------------------------------
# The record
# ---------------------------------------------------------------------------------------------


def test_subscriptions_are_configuration_records_named_once() -> None:
    assert RECORD_KINDS["subscription"] is Subscription
    assert Subscription.scope == "config"
    assert ("name",) in Subscription.unique
    assert "enabled" in Subscription.indexed


def test_a_stored_subscription_round_trips_as_json() -> None:
    stored = sub(filter="size > 1 or not subject", throttle="1 per 1m", accounts=["mail:work"])

    assert Subscription.model_validate_json(stored.model_dump_json()) == stored


@pytest.mark.parametrize("name", ["", "a b", "-x", "x/y", "a" * 101])
def test_a_subscription_s_name_is_a_plain_word(name: str) -> None:
    with pytest.raises(ValidationError):
        sub(name=name)


@pytest.mark.parametrize("fields", [{"events": []}, {"actions": []}, {"events": ["a b"]}])
def test_a_subscription_needs_events_and_actions(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        sub(**fields)


# ---------------------------------------------------------------------------------------------
# What saving one checks
# ---------------------------------------------------------------------------------------------

ACTIONS = {"hello", "file-it"}


def test_a_subscription_that_fits_passes() -> None:
    check_subscription(sub(events=["mail.message.*"], filter="size > 3"), types(), ACTIONS)
    check_subscription(sub(events=["custom.*"], filter="anything == 1"), types(), ACTIONS)
    check_subscription(sub(events=["*"]), types(), ACTIONS)


def test_a_glob_that_matches_no_type_is_refused() -> None:
    with pytest.raises(ConfigError, match="mial") as raised:
        check_subscription(sub(events=["mial.*"]), types(), ACTIONS)

    assert raised.value.hint is not None
    assert "udl events types" in raised.value.hint


def test_the_filter_is_checked_against_every_type_it_could_get() -> None:
    with pytest.raises(InvalidFilter, match=r"mail\.label\.added"):
        check_subscription(sub(events=["mail.*"], filter="size > 1"), types(), ACTIONS)


def test_its_actions_must_exist() -> None:
    with pytest.raises(ConfigError, match="nope") as raised:
        check_subscription(sub(actions=["hello", "nope"]), types(), ACTIONS)

    assert raised.value.hint is not None
    assert "udl actions" in raised.value.hint


def test_a_source_filter_is_checked_by_its_types_integration() -> None:
    checked: list[JsonObject] = []

    def check_mail(given: JsonObject) -> None:
        if "query" not in given:
            raise ConfigError("A mail source filter has a query")
        checked.append(given)

    good = sub(events=["mail.message.*"], source_filter={"query": "has:attachment"})

    check_subscription(good, types(), ACTIONS, source_filters={"mail": check_mail})

    assert checked == [{"query": "has:attachment"}]
    with pytest.raises(ConfigError, match="query"):
        check_subscription(
            sub(source_filter={"q": 1}), types(), ACTIONS, source_filters={"mail": check_mail}
        )


@pytest.mark.parametrize("events", [["udl.download.*"], ["mail.*", "udl.action.failed"]])
def test_a_source_filter_needs_one_integration_s_types(events: list[str]) -> None:
    with pytest.raises(ConfigError, match="source filter"):
        check_subscription(
            sub(events=events, source_filter={"query": "x"}),
            types(),
            ACTIONS,
            source_filters={"mail": lambda given: None},
        )


def test_names_of_schedules_are_reserved() -> None:
    linked = sub(name="schedule:morning")

    with pytest.raises(AlreadyExists, match="schedule:morning") as raised:
        check_subscription(linked, types(), ACTIONS)

    assert raised.value.code is ErrorCode.ALREADY_EXISTS
    check_subscription(linked, types(), ACTIONS, for_schedule=True)
