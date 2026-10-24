"""
-------------
- events.py -
-------------

Events: what happened (a mail came, a download finished, a run ended), each of an event type
whose model validates its data. Events are stored (EventRecord) before they are dispatched to
the subscriptions that match them (udl.automations.bus).

An EventType names its data model, a summary (for list_event_types, the UI and AI prompts), an
example (which filters and templates are checked against when they are saved) and the one-line
subject of an event. udl's own types are below; a plugin (of any kind) adds its own through a
classmethod, `event_types()`, each named after the plugin (`gmail.message.received`). Types
called `custom.<name>` need no registering: their data is any JSON object (emit_event, POST
/events, the emit action).

Inside a run, current_run says which run is running, so what it publishes carries the run (as
caused_by) and its depth (its event's, plus one), and integrations can tell what udl itself sent:
their event data has `sent_by_udl` (a mail a run sent, a Slack message a run posted), so a
subscription can say `not sent_by_udl` and never answer itself.
"""

# System Imports
import fnmatch
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Self, cast
from uuid import UUID

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, NonNegativeInt

# Local Imports
from udl.db.models import JsonObject, utcnow
from udl.db.records import Record, RecordScope, SyncRule
from udl.errors import ConfigError, ErrorCode, ErrorInfo, PluginError

if TYPE_CHECKING:
    from udl.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

# A type's name: dotted words of lower case letters, digits, _ and - (at least two).
_NAME: Final = re.compile(r"[a-z0-9_-]+(\.[a-z0-9_-]+)+")
CUSTOM_PREFIX: Final = "custom."
_WILDCARDS: Final = frozenset("*?[")


# ---------------------------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EventType[D: BaseModel]:
    """A kind of event, and the model of its data."""

    name: str  # e.g. "gmail.message.received"
    data: type[D]
    summary: str  # for list_event_types, the UI and AI prompts
    example: D  # what filters and templates are checked against when they are saved
    subject: Callable[[D], str]  # one line: "Mail from bank@example.com: Your statement"
    source: str = "udl"  # what publishes it: "udl", or the plugin that adds it

    @property
    def is_custom(self) -> bool:
        """Whether it is a custom.* type, whose data is any JSON object."""
        return self.name.startswith(CUSTOM_PREFIX)

    def validate(self, data: Mapping[str, JsonValue]) -> JsonObject:
        """`data` as this type's model has it, as plain JSON. Raises ValidationError."""
        return self.data.model_validate(data).model_dump(mode="json")

    def subject_of(self, data: D) -> str:
        """An event's subject, from its data (its type's name, when that fails)."""
        try:
            subject = self.subject(data)
        except Exception:  # (a subject is never worth losing an event over)
            log.warning("The %s type's subject failed", self.name, exc_info=True)
            return self.name
        return " ".join(subject.split()) or self.name


class CustomEventData(BaseModel):
    """Any JSON object: a custom event's data."""

    model_config = ConfigDict(extra="allow", frozen=True)


def _custom_subject(data: CustomEventData) -> str:
    subject = (data.model_extra or {}).get("subject")
    return subject if isinstance(subject, str) else ""


CUSTOM: Final = EventType(
    name="custom.*",
    data=CustomEventData,
    summary="An event of your own (custom.<name>), from emit_event, POST /events or an emit "
    "action. Its data is any JSON object (a `subject` in it is its subject).",
    example=CustomEventData(),
    subject=_custom_subject,
)


class EventField(BaseModel):
    """A field of an event type's data: what a filter names, as a dotted path."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    path: str
    """Its dotted path, e.g. `from.address` (through a list: `to.address`, any item)."""
    type: str
    """Its JSON type: string, integer, number, boolean, object, array, or any."""
    format: str | None = None
    """What its text is, e.g. date-time (compared as times) or uuid."""
    items: str | None = None
    """An array's items' JSON type."""
    description: str = ""
    """What it is."""
    optional: bool = False
    """Whether an event may lack it (a filter on it then does not match, unless under not)."""
    choices: list[JsonValue] | None = None
    """The values it can have, when they are few."""
    in_list: bool = False
    """Whether it is reached through a list: a filter matches when any item does."""
    open: bool = False
    """Whether it holds any fields (a dict, or any JSON): a filter may name paths under it."""


class EventTypeInfo(BaseModel):
    """An event type, as list_event_types describes it."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    name: str
    """The type's name (custom.* stands for every custom.<name>)."""
    summary: str
    """What it is."""
    source: str
    """What publishes it: udl, or the plugin that adds it."""
    json_schema: JsonObject
    """Its data's JSON Schema."""
    example: JsonObject
    """An example of its data."""
    fields: list[EventField]
    """Its data's fields, as the dotted paths a filter names."""


class EventTypeList(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    types: list[EventTypeInfo]
    """The event types, by name."""


class EventTypes:
    """The event types udl knows, by name: its own, and its plugins'."""

    def __init__(self) -> None:
        self._types: dict[str, EventType[Any]] = {}

    @classmethod
    def default(cls) -> Self:
        """udl's own event types."""
        made = cls()
        made._types[CUSTOM.name] = CUSTOM
        for found in UDL_TYPES:
            made.register(found)
        return made

    @classmethod
    def discover(cls, plugins: PluginRegistry) -> Self:
        """udl's own event types, then those each plugin in `plugins` adds (named after it). A
        type that cannot be added is left out and reported as one of the plugins' problems."""
        found = cls.default()
        for info, plugin in plugins.entries():
            provide = getattr(plugin, "event_types", None)
            if not callable(provide):
                continue
            try:
                added = list(cast(Iterable[object], provide()))
            except Exception as e:  # noqa: BLE001 - a broken plugin must never stop udl
                plugins.report(info.kind, info.origin, e)
                continue
            for event_type in added:
                try:
                    found.register(_of_plugin(info.name, event_type))
                except PluginError as e:
                    plugins.report(info.kind, info.origin, e)
        return found

    def register(self, event_type: EventType[Any]) -> None:
        """Add `event_type`. Raises PluginError when its name is taken or is not a type's."""
        name = event_type.name
        if not _NAME.fullmatch(name) or name.startswith(CUSTOM_PREFIX):
            raise PluginError(
                f"{name!r} cannot name an event type: it is dotted lower case words (e.g. "
                "gmail.message.received), and custom.* names are the user's"
            )
        if name in self._types:
            raise PluginError(f"There already is an event type called {name!r}")
        self._types[name] = event_type

    def find(self, name: str) -> EventType[Any] | None:
        """The type called `name` (a custom.<name> one too), or None."""
        if name.startswith(CUSTOM_PREFIX) and _NAME.fullmatch(name):
            return replace(CUSTOM, name=name)
        return self._types.get(name) if name != CUSTOM.name else None

    def get(self, name: str) -> EventType[Any]:
        """The type called `name` (custom.<name>: any JSON object). Raises ConfigError."""
        found = self.find(name)
        if found is None:
            raise ConfigError(
                f"udl has no event type called {name!r}",
                hint="`udl events types` (list_event_types) lists them; an event of your own "
                "is called custom.<name>.",
            )
        return found

    def matching(self, glob: str) -> list[EventType[Any]]:
        """The registered types `glob` matches (fnmatch), by name (custom.* types are not
        registered: see may_be_custom())."""
        return [
            found
            for name, found in sorted(self._types.items())
            if name != CUSTOM.name and fnmatch.fnmatchcase(name, glob)
        ]

    def may_be_custom(self, glob: str) -> bool:
        """Whether `glob` may match custom.<name> types."""
        return glob.startswith(CUSTOM_PREFIX) or glob[:1] in _WILDCARDS

    def all(self) -> list[EventType[Any]]:
        """Every type (custom.* standing for the custom ones), by name."""
        return [found for _, found in sorted(self._types.items())]

    def info(self, match: str = "*") -> EventTypeList:
        """The types whose names `match` (a glob) matches, described."""
        return EventTypeList(
            types=[
                _info(found)
                for found in self.all()
                if fnmatch.fnmatchcase(found.name, match)
                or (found is CUSTOM and self.may_be_custom(match))
            ]
        )


def _of_plugin(plugin: str, event_type: object) -> EventType[Any]:
    """`event_type`, as the plugin called `plugin` adds it (named after it)."""
    if not isinstance(event_type, EventType):
        raise PluginError(f"{plugin}'s event_types() gave {event_type!r}, not an EventType")
    found = cast(EventType[Any], event_type)
    if not found.name.startswith(f"{plugin}."):
        raise PluginError(
            f"The {plugin} plugin's event type {found.name!r} is not named after it: its "
            f"types are called {plugin}.<name>"
        )
    return replace(found, source=plugin)


def _info(event_type: EventType[Any]) -> EventTypeInfo:
    return EventTypeInfo(
        name=event_type.name,
        summary=event_type.summary,
        source=event_type.source,
        json_schema=event_type.data.model_json_schema(),
        example=event_type.example.model_dump(mode="json"),
        fields=event_fields(event_type.data),
    )


# ---------------------------------------------------------------------------------------------
# A type's fields
# ---------------------------------------------------------------------------------------------


def event_fields(model: type[BaseModel]) -> list[EventField]:
    """The fields of `model` (an event type's data), as dotted paths: nested models' fields
    under their own, and a list's items' under the list's."""
    schema = cast(JsonObject, model.model_json_schema())
    definitions = cast(JsonObject, schema.get("$defs", {}))
    found: list[EventField] = []
    _walk(schema, "", definitions, found, optional=False, in_list=False)
    return found


def _walk(
    schema: JsonObject,
    prefix: str,
    definitions: JsonObject,
    found: list[EventField],
    *,
    optional: bool,
    in_list: bool,
) -> None:
    """Add the fields of the object `schema` describes (its properties) to `found`."""
    properties = cast(JsonObject, schema.get("properties", {}))
    required = cast(list[str], schema.get("required", []))
    for name, given in properties.items():
        node, nullable = _resolved(cast(JsonObject, given), definitions)
        path = f"{prefix}{name}"
        missing = optional or nullable or name not in required
        description = str(
            cast(JsonObject, given).get("description") or node.get("description") or ""
        )
        kind = _json_type(node)
        items: str | None = None
        item_node: JsonObject | None = None
        if kind == "array" and isinstance(node.get("items"), dict):
            item_node, _ = _resolved(cast(JsonObject, node["items"]), definitions)
            items = _json_type(item_node)
        choices = node.get("enum")
        found.append(
            EventField(
                path=path,
                type=kind,
                format=cast(str | None, node.get("format")),
                items=items,
                description=description,
                optional=missing,
                choices=cast(list[JsonValue], choices) if isinstance(choices, list) else None,
                in_list=in_list,
                open=kind == "any" or (kind == "object" and "properties" not in node),
            )
        )
        if kind == "object" and "properties" in node:
            _walk(node, f"{path}.", definitions, found, optional=missing, in_list=in_list)
        elif item_node is not None and items == "object" and "properties" in item_node:
            _walk(item_node, f"{path}.", definitions, found, optional=missing, in_list=True)


def _resolved(node: JsonObject, definitions: JsonObject) -> tuple[JsonObject, bool]:
    """The schema `node` stands for (its $ref followed, `| None` taken off), and whether it
    may be null."""
    nullable = False
    while True:
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            node = cast(JsonObject, definitions[name])
            continue
        options = node.get("anyOf") or node.get("oneOf")
        if isinstance(options, list):
            members = [cast(JsonObject, member) for member in options]
            others = [member for member in members if member.get("type") != "null"]
            nullable = nullable or len(others) < len(members)
            if len(others) == 1:
                node = others[0]
                continue
            return {}, nullable  # (one of several types: any)
        return node, nullable


def _json_type(node: JsonObject) -> str:
    kind = node.get("type")
    return kind if isinstance(kind, str) else "any"


# ---------------------------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------------------------


class Event(BaseModel):
    """Something that happened."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    id: UUID
    """The event's id."""
    type: str
    """Its event type, e.g. gmail.message.received."""
    source: str
    """What published it: e.g. gmail, scheduler, udl, cli, api, mcp."""
    account_id: UUID | None = None
    """The account it happened in, if any."""
    occurred_at: AwareDatetime
    """When it happened."""
    subject: str
    """A line saying what happened."""
    data: JsonObject
    """What happened, as its type's model has it."""
    dedupe_key: str | None = None
    """What makes it the same event as another of its type (so it is stored once)."""
    caused_by: UUID | None = None
    """The run that led to it, if a run did."""
    depth: NonNegativeInt = 0
    """0 for an event from outside; one that a run caused is one deeper than the run's."""


class EventStatus(StrEnum):
    """Where an event is in being dispatched."""

    PENDING = "pending"  # stored, not dispatched yet
    DISPATCHED = "dispatched"  # each matching subscription's actions have runs
    DROPPED = "dropped"  # stored, but never dispatched (e.g. too deep)


class EventRecord(Record):
    """An event, as it is stored: with how it was dispatched. Its id is the event's."""

    kind = "event"
    indexed = ("type", "source", "account_id", "status", "dedupe_key", "caused_by")
    unique = (("type", "dedupe_key"),)
    scope = RecordScope.RUNTIME
    sync = SyncRule.ENDED

    type: str
    source: str
    account_id: UUID | None = None
    occurred_at: AwareDatetime
    subject: str
    data: JsonObject = Field(default_factory=dict)
    dedupe_key: str | None = None
    caused_by: UUID | None = None
    depth: NonNegativeInt = 0
    status: EventStatus = EventStatus.PENDING
    dispatched_at: AwareDatetime | None = None
    dropped_reason: str | None = None

    @classmethod
    def example(cls) -> Self:
        return cls(
            type="custom.hello",
            source="api",
            occurred_at=utcnow(),
            subject="Hello",
            data={"name": "x", "tags": ["a"]},
            dedupe_key="hello-1",
        )

    def ended(self) -> bool:
        return self.status is not EventStatus.PENDING

    def event(self) -> Event:
        """The event itself."""
        return Event.model_validate(self.model_dump(include=set(Event.model_fields)))


@dataclass(frozen=True)
class CurrentRun:
    """The run that is running (current_run), as what it publishes needs it."""

    id: UUID
    depth: int  # its event's depth (0 for a run without an event)
    action: str
    subscription: str | None = None


current_run: ContextVar[CurrentRun | None] = ContextVar("udl_current_run", default=None)
"""The run whose action is running (set by the runner around it); None outside a run."""


# ---------------------------------------------------------------------------------------------
# udl's own event types
# ---------------------------------------------------------------------------------------------


class _Data(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class DownloadEventData(_Data):
    """A download that ended: udl.download.finished, udl.download.failed."""

    download: UUID
    """The download's id (`udl jobs show`, get_download)."""
    batch: UUID | None = None
    """The id of the download request it was one URL of."""
    url: str
    """The URL that was downloaded."""
    site: str
    """The URL's site."""
    tool: str | None = None
    """The downloader that downloaded it."""
    title: str | None = None
    """The media's title."""
    files: list[str] = Field(default_factory=list[str])
    """The files it wrote."""
    size: NonNegativeInt | None = None
    """How many bytes it wrote."""
    output_dir: str | None = None
    """The folder it wrote them in."""
    error: ErrorInfo | None = None
    """Why it failed."""


def _download_finished(data: DownloadEventData) -> str:
    return f"Downloaded {data.title or data.url}"


def _download_failed(data: DownloadEventData) -> str:
    why = f": {data.error.message}" if data.error is not None else ""
    return f"The download of {data.url} failed{why}"


class ActionEventData(_Data):
    """A run that ended, or waits: udl.action.succeeded, udl.action.failed,
    udl.action.awaiting_approval."""

    run: UUID
    """The run's id (`udl runs show`, get_run)."""
    action: str
    """The action that ran."""
    plugin: str
    """The action's kind (the action plugin that runs it)."""
    subscription: str | None = None
    """The subscription that ran it (None when it was asked for)."""
    event: UUID | None = None
    """The event it ran for."""
    event_type: str | None = None
    """That event's type."""
    attempt: NonNegativeInt = 1
    """How many times it was tried."""
    output: JsonValue = None
    """What it gave (when it succeeded)."""
    summary: str = ""
    """A line saying what it did."""
    error: ErrorInfo | None = None
    """Why it failed."""
    pending_call: JsonObject | None = None
    """The tool call that waits for approval (an AI action's)."""


def _action_succeeded(data: ActionEventData) -> str:
    return f"{data.action} succeeded" + (f": {data.summary}" if data.summary else "")


def _action_failed(data: ActionEventData) -> str:
    why = f": {data.error.message}" if data.error is not None else ""
    return f"{data.action} failed{why}"


class AccountEventData(_Data):
    """Something that happened to an account: udl.account.connected, udl.account.needs_reauth,
    udl.account.error."""

    account: str
    """The account, as integration:name (e.g. gmail:work)."""
    account_id: UUID
    """Its id."""
    integration: str
    """Its integration (e.g. gmail)."""
    error: ErrorInfo | None = None
    """What went wrong."""


def _account_error(data: AccountEventData) -> str:
    return f"{data.account}: {data.error.message if data.error else 'it failed'}"


class ScheduleFiredData(_Data):
    """A schedule that came due: schedule.fired."""

    schedule: str
    """The schedule's name."""
    scheduled_for: AwareDatetime
    """The time it was due at (without its jitter)."""
    fired_at: AwareDatetime
    """When it fired."""
    late: bool = False
    """Whether it fired late: its time passed while udl was stopped (its misfire is once)."""
    manual: bool = False
    """Whether it was run by hand (run_schedule) rather than by its time."""
    data: JsonObject = Field(default_factory=dict)
    """What the schedule passes on (its data)."""


def _schedule_fired(data: ScheduleFiredData) -> str:
    if data.manual:
        return f"The {data.schedule} schedule was run by hand"
    return f"The {data.schedule} schedule fired" + (", late" if data.late else "")


_EXAMPLE_ID: Final = UUID("00000000-0000-4000-8000-000000000001")
_DOWNLOAD: Final = DownloadEventData(
    download=_EXAMPLE_ID,
    batch=_EXAMPLE_ID,
    url="https://example.com/watch/1",
    site="example.com",
    tool="yt-dlp",
    title="A video",
    files=["/home/me/Downloads/udl/example.com/A video.mp4"],
    size=1_048_576,
    output_dir="/home/me/Downloads/udl",
)
_FAILED: Final = ErrorInfo(code=ErrorCode.NETWORK_ERROR, message="The connection was reset")
_RUN: Final = ActionEventData(
    run=_EXAMPLE_ID,
    action="file-it",
    plugin="script",
    subscription="bank-mail",
    event=_EXAMPLE_ID,
    event_type="custom.hello",
    output={"filed": True},
    summary="Filed",
)
_ACCOUNT: Final = AccountEventData(
    account="gmail:work", account_id=_EXAMPLE_ID, integration="gmail"
)
_SCHEDULED: Final = datetime(2026, 1, 5, 8, tzinfo=UTC)

UDL_TYPES: Final[tuple[EventType[Any], ...]] = (
    EventType(
        name="udl.download.finished",
        data=DownloadEventData,
        summary="A download finished: the files it wrote.",
        example=_DOWNLOAD,
        subject=_download_finished,
    ),
    EventType(
        name="udl.download.failed",
        data=DownloadEventData,
        summary="A download failed: why (a cancelled download is no event).",
        example=_DOWNLOAD.model_copy(update={"files": [], "size": None, "error": _FAILED}),
        subject=_download_failed,
    ),
    EventType(
        name="udl.action.succeeded",
        data=ActionEventData,
        summary="A run of an action succeeded: what it gave.",
        example=_RUN,
        subject=_action_succeeded,
    ),
    EventType(
        name="udl.action.failed",
        data=ActionEventData,
        summary="A run of an action failed (for good: after its retries): why.",
        example=_RUN.model_copy(update={"output": None, "summary": "", "error": _FAILED}),
        subject=_action_failed,
    ),
    EventType(
        name="udl.action.awaiting_approval",
        data=ActionEventData,
        summary="A run waits for a tool call to be approved (an AI action's, in ask mode).",
        example=_RUN.model_copy(
            update={"output": None, "summary": "", "pending_call": {"tool": "gmail_send"}}
        ),
        subject=lambda data: f"{data.action} waits for approval",
    ),
    EventType(
        name="udl.account.connected",
        data=AccountEventData,
        summary="An account was connected.",
        example=_ACCOUNT,
        subject=lambda data: f"{data.account} was connected",
    ),
    EventType(
        name="udl.account.needs_reauth",
        data=AccountEventData,
        summary="An account's login was refused or has expired: it needs connecting again.",
        example=_ACCOUNT.model_copy(
            update={"error": ErrorInfo(code=ErrorCode.REAUTH_REQUIRED, message="The token expired")}
        ),
        subject=lambda data: f"{data.account} needs connecting again",
    ),
    EventType(
        name="udl.account.error",
        data=AccountEventData,
        summary="An account's watcher failed (it is retried with backoff).",
        example=_ACCOUNT.model_copy(update={"error": _FAILED}),
        subject=_account_error,
    ),
    EventType(
        name="schedule.fired",
        data=ScheduleFiredData,
        summary="A schedule came due (or was run by hand): which, and the data it passes on.",
        example=ScheduleFiredData(
            schedule="digest",
            scheduled_for=_SCHEDULED,
            fired_at=_SCHEDULED + timedelta(seconds=2),
            data={"report": "daily"},
        ),
        subject=_schedule_fired,
    ),
)
"""udl's own event types."""
