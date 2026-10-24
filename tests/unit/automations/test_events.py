"""
------------------
- test_events.py -
------------------

Events and their types: each event type's data is validated by its model (but custom.* events,
whose data is any JSON object), udl's own types are registered with examples that validate, and
list_event_types describes each type: its JSON Schema, an example and its fields, as the dotted
paths a filter names
"""

# System Imports
import subprocess
import sys
from datetime import UTC, datetime
from typing import Any, Self, cast
from uuid import uuid4

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

# Local Imports
from support.engine import registry
from udl.automations.events import (
    CUSTOM,
    EventField,
    EventRecord,
    EventStatus,
    EventType,
    EventTypes,
    event_fields,
)
from udl.db.records import RECORD_KINDS, record_kinds
from udl.errors import ConfigError, PluginError
from udl.plugins.registry import SITES
from udl.plugins.sites.base import SitePlugin

UDL_TYPES = {
    "udl.download.finished",
    "udl.download.failed",
    "udl.action.succeeded",
    "udl.action.failed",
    "udl.action.awaiting_approval",
    "udl.account.connected",
    "udl.account.needs_reauth",
    "udl.account.error",
}


class _Data(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Address(_Data):
    address: str
    """Where it goes."""
    name: str | None = None
    """Who it is."""


class Mail(_Data):
    subject: str
    """The subject line."""
    sender: Address
    """Who sent it."""
    to: list[Address] = Field(default_factory=list[Address])
    """Who it went to."""
    labels: list[str] = Field(default_factory=list[str])
    """Its labels."""
    size: int = 0
    """In bytes."""
    received_at: datetime | None = None
    """When it came."""
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    """Any headers."""
    extra: JsonValue = None
    """Anything."""
    either: int | str | None = None
    """A number or some text."""

    @classmethod
    def example(cls) -> Self:
        return cls(subject="Hello", sender=Address(address="a@example.com"))


MAIL = EventType(
    name="test.mail.received",
    data=Mail,
    summary="A test mail came.",
    example=Mail.example(),
    subject=lambda mail: f"Mail: {mail.subject}",
    source="test",
)


def mail_types() -> EventTypes:
    types = EventTypes.default()
    types.register(MAIL)
    return types


# ---------------------------------------------------------------------------------------------
# udl's own types
# ---------------------------------------------------------------------------------------------


def test_udl_s_own_types_are_registered() -> None:
    names = {found.name for found in EventTypes.default().all()}

    assert names == UDL_TYPES | {"custom.*"}


@pytest.mark.parametrize("name", sorted(UDL_TYPES))
def test_each_of_udl_s_types_has_an_example_that_validates(name: str) -> None:
    found = EventTypes.default().get(name)

    data = found.validate(found.example.model_dump(mode="json"))

    assert data == found.example.model_dump(mode="json")
    assert found.subject_of(found.example)
    assert found.summary
    assert found.source == "udl"


def test_a_type_s_data_is_validated_by_its_model() -> None:
    finished = EventTypes.default().get("udl.download.finished")

    with pytest.raises(ValidationError):
        finished.validate({"url": "https://example.com/a"})


def test_validated_data_is_plain_json() -> None:
    found = mail_types().get("test.mail.received")

    data = found.validate(
        {"subject": "Hi", "sender": {"address": "x@y"}, "received_at": "2026-01-02T03:04:05Z"}
    )

    assert data["received_at"] == "2026-01-02T03:04:05Z"
    assert data["to"] == []


def test_an_unknown_type_is_refused() -> None:
    with pytest.raises(ConfigError, match=r"udl\.nothing") as raised:
        EventTypes.default().get("udl.nothing")

    assert raised.value.hint is not None
    assert "custom." in raised.value.hint


@pytest.mark.parametrize("name", ["custom.hello", "custom.a.b", "custom.my-thing_2"])
def test_a_custom_type_takes_any_json_object(name: str) -> None:
    found = EventTypes.default().get(name)

    assert found.name == name
    assert found.validate({"a": [1, None, {"b": True}]}) == {"a": [1, None, {"b": True}]}
    assert found.is_custom


@pytest.mark.parametrize("name", ["custom.", "custom", "custom..x", "Custom.x", "custom.x y"])
def test_a_custom_type_s_name_is_a_dotted_name(name: str) -> None:
    with pytest.raises(ConfigError):
        EventTypes.default().get(name)


def test_a_custom_event_s_subject_is_its_own_or_its_type() -> None:
    hello = EventTypes.default().get("custom.hello")

    assert hello.subject_of(hello.data.model_validate({"subject": "Hi there"})) == "Hi there"
    assert hello.subject_of(hello.data.model_validate({"name": "x"})) == "custom.hello"


def test_a_subject_that_fails_is_the_type_s_name() -> None:
    broken = EventType(
        name="test.broken",
        data=Mail,
        summary="Broken.",
        example=Mail.example(),
        subject=lambda mail: str(1 / 0),
        source="test",
    )

    assert broken.subject_of(Mail.example()) == "test.broken"


def test_types_are_found_by_glob() -> None:
    types = mail_types()

    assert {found.name for found in types.matching("udl.action.*")} == {
        "udl.action.succeeded",
        "udl.action.failed",
        "udl.action.awaiting_approval",
    }
    assert [found.name for found in types.matching("test.*")] == ["test.mail.received"]
    assert types.matching("nothing.*") == []


@pytest.mark.parametrize(
    ("glob", "custom"),
    [("custom.*", True), ("custom.hello", True), ("*", True), ("udl.*", False), ("gmail.*", False)],
)
def test_a_glob_may_match_custom_types(glob: str, custom: bool) -> None:
    assert EventTypes.default().may_be_custom(glob) is custom


def test_a_type_is_registered_once() -> None:
    types = mail_types()

    with pytest.raises(PluginError, match=r"test\.mail\.received"):
        types.register(MAIL)


@pytest.mark.parametrize("name", ["custom.mine", "nodots", "UPPER.case", "a..b"])
def test_a_type_s_name_is_dotted_and_not_custom(name: str) -> None:
    with pytest.raises(PluginError):
        EventTypes().register(
            EventType(
                name=name,
                data=Mail,
                summary="x",
                example=Mail.example(),
                subject=lambda mail: "x",
            )
        )


# ---------------------------------------------------------------------------------------------
# Event types from plugins
# ---------------------------------------------------------------------------------------------


class Mailer(SitePlugin):
    """A plugin that brings an event type."""

    name = "test"

    @classmethod
    def event_types(cls) -> list[EventType[Any]]:
        return [MAIL]


def test_a_plugin_s_event_types_are_discovered() -> None:
    plugins = registry()
    plugins.register(SITES, Mailer)

    types = EventTypes.discover(plugins)

    assert types.get("test.mail.received") == MAIL
    assert plugins.problems == []


def test_a_plugin_s_types_are_named_after_it() -> None:
    class Thief(SitePlugin):
        name = "thief"

        @classmethod
        def event_types(cls) -> list[EventType[Any]]:
            return [MAIL, "not a type"]  # pyright: ignore[reportReturnType]

    plugins = registry()
    plugins.register(SITES, Thief)

    types = EventTypes.discover(plugins)

    assert types.find("test.mail.received") is None
    assert len(plugins.problems) == 2
    assert "thief." in plugins.problems[0].error


def test_a_plugin_whose_event_types_fail_is_reported() -> None:
    class Broken(SitePlugin):
        name = "broken"

        @classmethod
        def event_types(cls) -> list[EventType[Any]]:
            raise RuntimeError("oops")

    plugins = registry()
    plugins.register(SITES, Broken)

    types = EventTypes.discover(plugins)

    assert {found.name for found in types.all()} == UDL_TYPES | {"custom.*"}
    [problem] = plugins.problems
    assert "oops" in problem.error


# ---------------------------------------------------------------------------------------------
# What list_event_types says about a type
# ---------------------------------------------------------------------------------------------


def test_a_type_s_info_has_its_schema_and_example() -> None:
    [info] = mail_types().info("test.mail.*").types

    assert info.name == "test.mail.received"
    assert info.summary == "A test mail came."
    assert info.source == "test"
    properties = cast(dict[str, dict[str, object]], info.json_schema["properties"])
    assert properties["subject"]["description"] == "The subject line."
    assert info.example == Mail.example().model_dump(mode="json")


def test_every_type_is_listed_by_default() -> None:
    listed = {info.name for info in EventTypes.default().info().types}

    assert listed == UDL_TYPES | {"custom.*"}


def fields_by_path(model: type[BaseModel]) -> dict[str, EventField]:
    return {found.path: found for found in event_fields(model)}


def test_fields_flatten_nested_models() -> None:
    fields = fields_by_path(Mail)

    assert fields["subject"] == EventField(
        path="subject", type="string", description="The subject line.", optional=False
    )
    assert fields["sender"].type == "object"
    assert fields["sender.address"].type == "string"
    assert fields["sender.address"].description == "Where it goes."
    assert fields["sender.name"].optional is True
    assert fields["sender.address"].optional is False


def test_a_list_s_items_are_under_the_list_s_own_path() -> None:
    fields = fields_by_path(Mail)

    assert fields["to"].type == "array"
    assert fields["to"].items == "object"
    assert fields["to.address"].in_list is True
    assert fields["to.address"].type == "string"
    assert fields["labels"].items == "string"
    assert fields["labels"].in_list is False
    assert "labels.0" not in fields


def test_fields_say_their_formats_choices_and_whether_they_are_open() -> None:
    fields = fields_by_path(Mail)
    failed = fields_by_path(EventTypes.default().get("udl.download.failed").data)

    assert fields["received_at"].format == "date-time"
    assert fields["received_at"].optional is True
    assert fields["size"].type == "integer"
    assert fields["headers"].type == "object"
    assert fields["headers"].open is True
    assert fields["sender"].open is False
    assert fields["extra"].type == "any"
    assert fields["extra"].open is True
    assert (fields["either"].type, fields["either"].optional) == ("any", True)
    assert failed["error.code"].choices is not None
    assert "network_error" in failed["error.code"].choices
    assert failed["error"].optional is True
    assert failed["error.code"].optional is True  # (its object may be missing)


def test_a_custom_type_s_fields_are_open() -> None:
    [info] = EventTypes.default().info("custom.*").types

    assert info.fields == []
    assert info.json_schema["type"] == "object"
    assert CUSTOM.is_custom


# ---------------------------------------------------------------------------------------------
# Stored events
# ---------------------------------------------------------------------------------------------


def test_a_stored_event_is_its_event_and_how_it_was_dispatched() -> None:
    record = EventRecord(
        type="custom.hello",
        source="api",
        occurred_at=datetime(2026, 1, 2, tzinfo=UTC),
        subject="custom.hello",
        data={"name": "x"},
        dedupe_key="k",
        caused_by=uuid4(),
        depth=2,
    )

    event = record.event()

    assert record.status is EventStatus.PENDING
    assert record.ended() is False
    assert record.model_copy(update={"status": EventStatus.DISPATCHED}).ended() is True
    assert event.id == record.id
    assert (event.type, event.data, event.depth, event.dedupe_key) == (
        "custom.hello",
        {"name": "x"},
        2,
        "k",
    )
    assert event.caused_by == record.caused_by


def test_events_are_a_runtime_s_records_synced_once_dispatched() -> None:
    assert RECORD_KINDS["event"] is EventRecord
    assert EventRecord.scope == "runtime"
    assert EventRecord.sync == "ended"
    assert ("type", "dedupe_key") in EventRecord.unique


def test_udl_s_record_kinds_are_known_without_importing_them() -> None:
    # (The databases make a table for each kind: they are all known as soon as one is asked for.)
    code = (
        "from udl.db.records import record_kinds; "
        "print(' '.join(kind.kind for kind in record_kinds()))"
    )
    found = subprocess.run(  # noqa: S603 - (this Python, running udl)
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.split()

    assert {"event", "lease", "plugin_settings"} <= set(found)
    assert {kind.kind for kind in record_kinds()} >= set(found)
