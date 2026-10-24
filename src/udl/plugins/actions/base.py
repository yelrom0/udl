"""
-----------
- base.py -
-----------

ActionPlugin: the interface every kind of action implements, and the Action record, a named
action of a kind with its options.

An action's options may hold templates (udl.automations.templates), rendered for each run with
the event that set it off, then validated by its kind's options model. check() is what saving
an action checks: its templates rendered with an event type's example (or, when there is none,
only parsed), and the options validated. A run gets an ActionContext and gives an ActionOutput,
or raises ActionFailed (retryable or not) or Cancelled, and nothing else.
tests/contract/test_action_contract.py is the behaviour every action plugin must have.
"""

# System Imports
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Final, Self
from uuid import UUID

# Package Imports
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    StringConstraints,
    ValidationError,
)

# Local Imports
from udl.automations.events import Event, EventType
from udl.automations.templates import Templates
from udl.clock import Clock
from udl.config import Duration
from udl.db.models import JsonObject
from udl.db.records import Name, Record
from udl.errors import ActionFailed, ConfigError
from udl.plugins.downloaders.base import CancelToken
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.base import SecretStore

if TYPE_CHECKING:
    from udl.core.service import UDLService

__all__ = [
    "MAX_LOG",
    "Action",
    "ActionContext",
    "ActionFailed",
    "ActionOptions",
    "ActionOutput",
    "ActionPlugin",
    "RunLog",
    "example_event",
    "has_template",
    "template_context",
    "untemplated_problems",
]

MAX_LOG: Final = 16 * 1024
"""Characters of a run's log that are kept (its last)."""
_LEFT_OUT: Final = "[earlier lines left out]\n"
_TEMPLATE_MARKS: Final = ("{{", "{%", "{#")
_EXAMPLE_ID: Final = UUID("00000000-0000-4000-8000-000000000002")
_EXAMPLE_TIME: Final = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


class ActionOptions(BaseModel):
    """The options of a kind of action (each kind's model extends it). Unknown options are
    refused, so a misspelt one is never ignored."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class ActionOutput(BaseModel):
    """What a run gave."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    output: JsonValue = None
    """What the action gave (JSON)."""
    summary: str = ""
    """A line saying what it did."""


class RunLog:
    """A run's log: the lines it wrote, of which the last MAX_LOG characters are kept."""

    def __init__(self) -> None:
        self._lines: deque[str] = deque()
        self._length = 0
        self._dropped = False

    def write(self, text: str) -> None:
        """Add `text` (one line or several)."""
        for line in text.splitlines():
            self._lines.append(f"{line}\n")
            self._length += len(line) + 1
        while self._lines and self._length + len(_LEFT_OUT) > MAX_LOG:
            self._length -= len(self._lines.popleft())
            self._dropped = True

    @property
    def text(self) -> str:
        return (_LEFT_OUT if self._dropped else "") + "".join(self._lines)


@dataclass(frozen=True)
class ActionContext[O: ActionOptions]:
    """What a run is given."""

    run_id: UUID
    event: Event | None  # the event it runs for (None: it was asked for without one)
    options: O  # rendered with the event, and validated
    service: UDLService  # (to call operations)
    secrets: SecretStore
    clock: Clock
    cancel: CancelToken  # cancelled: the run stops, raising Cancelled
    log: RunLog
    action: str = ""  # the action's name
    subscription: str | None = None  # the subscription that ran it, if one did
    depth: int = 0  # its event's depth (what it publishes is one deeper)


class ActionPlugin[O: ActionOptions](ABC):
    """A kind of action (e.g. script): what its options are and how it runs.

    Made for a service, which it may use (to call operations, read the settings).
    """

    name: ClassVar[str]  # its kind's name, e.g. "script"
    options_model: ClassVar[type[ActionOptions]]
    # What it needs installed (`udl plugins list` says what is missing).
    requires: ClassVar[tuple[Requirement, ...]] = ()
    summary: ClassVar[str] = ""  # what it does, in a line

    def __init__(self, service: UDLService) -> None:
        self.service = service

    def options(self, values: Mapping[str, JsonValue]) -> O:
        """`values` (rendered) as this kind's options. Raises ConfigError."""
        try:
            found = self.options_model.model_validate(values)
        except ValidationError as e:
            raise ConfigError(
                f"The {self.name} action's options are not valid: {_problems(e)}",
                hint=f"Its options: {', '.join(self.options_model.model_fields)} "
                "(`udl actions show` gives their JSON Schema).",
            ) from e
        return found  # pyright: ignore[reportReturnType] - (its options_model is an O)

    async def check(self, options: JsonObject, event_type: EventType[Any] | None) -> None:
        """Raise what is wrong with an action's options (ConfigError, TemplateFailed), for
        events of `event_type` (or of no known type): see checked()."""
        self.checked(options, event_type)

    def checked(self, options: JsonObject, event_type: EventType[Any] | None) -> O | None:
        """An action's options rendered with `event_type`'s example and validated. Without an
        example (a custom type, or none) its templates are only parsed, the options without
        templates validated, and None is the answer. Raises ConfigError (TemplateFailed)."""
        templates = Templates()
        if event_type is not None and not event_type.is_custom:
            context = template_context(
                example_event(event_type),
                run={"id": str(_EXAMPLE_ID), "action": "example", "attempt": 1},
                now=_EXAMPLE_TIME,
            )
            return self.options(_object(templates.render_value(options, context)))
        _parse_templates(templates, options)
        if not has_template(options):
            return self.options(options)
        problems = untemplated_problems(self.options_model, options)
        if problems is not None:
            raise ConfigError(f"The {self.name} action's options are not valid: {problems}")
        return None

    @abstractmethod
    async def run(self, ctx: ActionContext[O]) -> ActionOutput:
        """Do the action. Raises ActionFailed or Cancelled (when ctx.cancel is), nothing else."""


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'options'}: {problem['msg']}"
        for problem in error.errors()
    )


def _object(value: JsonValue) -> JsonObject:
    return value if isinstance(value, dict) else {}


def has_template(value: JsonValue) -> bool:
    """Whether a text in `value` is a template (rather than plain text)."""
    if isinstance(value, str):
        return any(mark in value for mark in _TEMPLATE_MARKS)
    if isinstance(value, list):
        return any(has_template(item) for item in value)
    if isinstance(value, dict):
        return any(has_template(item) for item in value.values())
    return False


def untemplated_problems(model: type[BaseModel], values: JsonObject) -> str | None:
    """What is wrong with `values` as `model`, leaving out the fields that hold templates
    (which only an event can tell), or None when nothing is."""
    templated = {name for name, value in values.items() if has_template(value)}
    try:
        model.model_validate(values)
    except ValidationError as e:
        problems = [p for p in e.errors() if not p["loc"] or p["loc"][0] not in templated]
        if problems:
            return "; ".join(f"{'.'.join(map(str, p['loc']))}: {p['msg']}" for p in problems)
    return None


def _parse_templates(templates: Templates, value: JsonValue) -> None:
    """Raise TemplateFailed when a text in `value` is not a template."""
    if isinstance(value, str):
        templates.check(value)
    elif isinstance(value, list):
        for item in value:
            _parse_templates(templates, item)
    elif isinstance(value, dict):
        for item in value.values():
            _parse_templates(templates, item)


# ---------------------------------------------------------------------------------------------
# What templates see
# ---------------------------------------------------------------------------------------------


def template_context(
    event: Event | None,
    *,
    run: JsonObject | None,
    now: datetime,
    account: str | None = None,
) -> dict[str, JsonValue]:
    """What a template is rendered with, as plain JSON (so it has nothing private to reach):
    `event` (with its `account`'s name), `run` and `now`."""
    shown: JsonObject | None = None
    if event is not None:
        data = event.model_dump(mode="json")
        shown = {"id": data.pop("id"), "type": data.pop("type"), "source": data.pop("source")}
        shown |= {"account": account, **data}
    return {"event": shown, "run": run, "now": _iso(now)}


def _iso(when: datetime) -> str:
    return when.isoformat().replace("+00:00", "Z")


def example_event(event_type: EventType[Any]) -> Event:
    """An event of `event_type`, made of its example (what actions are checked with)."""
    return Event(
        id=_EXAMPLE_ID,
        type=event_type.name,
        source=event_type.source,
        occurred_at=_EXAMPLE_TIME,
        subject=event_type.subject_of(event_type.example),
        data=event_type.example.model_dump(mode="json"),
    )


# ---------------------------------------------------------------------------------------------
# The Action record
# ---------------------------------------------------------------------------------------------


def _some_time(timeout: timedelta) -> timedelta:
    if timeout <= timedelta(0):
        raise ValueError("an action's timeout is some time, e.g. 5m")
    return timeout


class Action(Record):
    """A named action: what to run (its kind, the action plugin) and with what options."""

    kind = "action"
    indexed = ("name", "plugin", "enabled")
    unique = (("name",),)

    name: Name
    """Its name."""
    description: str = ""
    """What it is for."""
    plugin: Annotated[str, StringConstraints(min_length=1)]
    """Its kind: the action plugin that runs it (script, operation, webhook, emit, notify...)."""
    options: JsonObject = Field(default_factory=dict)
    """Its kind's options; texts in them may be templates, rendered with each event."""
    timeout: Annotated[Duration, AfterValidator(_some_time)] = timedelta(minutes=5)
    """How long a run may take before it fails."""
    retries: Annotated[NonNegativeInt, Field(le=10)] = 0
    """How many more tries a run gets after a failure that trying again may help (with
    backoff: 10s, 20s, 40s... at most 10m)."""
    retry_timeouts: bool = False
    """Whether a run that timed out is tried again too."""
    max_concurrent: PositiveInt = 1
    """How many of its runs may run at once."""
    enabled: bool = True
    """Whether it runs (a disabled action's runs are skipped)."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            name="file-statement",
            description="Files a bank statement",
            plugin="script",
            options={"command": ["file-statement.py", "{{ event.data.subject }}"]},
            retries=2,
        )
