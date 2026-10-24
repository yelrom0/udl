"""
--------------------
- subscriptions.py -
--------------------

Subscriptions: "when an event like this happens, run these actions". A subscription names the
event types it wants (or globs of them: `gmail.*`), the accounts (or every one), a filter on the
event's data, a source filter only its integration can check (11.6), the actions to run (each
matched action is a run of its own) and at most how often (a throttle, counted by the bus).

matches() is a pure function of a subscription and an event; check_subscription() is what
saving one checks: its globs match known types (or custom ones), its filter fits every type it
could get, its actions exist, and its source filter belongs to one integration.
"""

# System Imports
import fnmatch
import re
from collections.abc import Callable, Collection, Mapping
from datetime import timedelta
from typing import Annotated, Any, Final, Self

# Package Imports
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)

# Local Imports
from udl.automations.events import CUSTOM, Event, EventType, EventTypes
from udl.automations.filters import FilterField, check
from udl.automations.filters import matches as matches_filter
from udl.config import Duration, format_duration
from udl.db.models import JsonObject
from udl.db.records import Name, Record
from udl.errors import AlreadyExists, ConfigError

SCHEDULE_PREFIX: Final = "schedule:"
"""What starts the names of the subscriptions schedules keep (10.4)."""

_THROTTLE: Final = re.compile(r"\s*(\d+)\s+per\s+(\S+)\s*")

type Glob = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.*?\[\]!-]+$")]
"""An event type, or a glob of them (fnmatch: *, ?, [...])."""


class Throttle(BaseModel):
    """At most `max_runs` runs `per` this long (also written `10 per 1h`)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    max_runs: PositiveInt
    """How many runs, at most."""
    per: Duration
    """In how long, e.g. 1h."""

    @model_validator(mode="before")
    @classmethod
    def _from_shorthand(cls, value: object) -> object:
        """A throttle written `10 per 1h`, as its fields."""
        if not isinstance(value, str):
            return value
        found = _THROTTLE.fullmatch(value)
        if found is None:
            raise ValueError(f"{value!r} is not a throttle, such as 10 per 1h")
        return {"max_runs": int(found.group(1)), "per": found.group(2)}

    @field_validator("per")
    @classmethod
    def _some_time(cls, per: timedelta) -> timedelta:
        if per <= timedelta(0):
            raise ValueError("a throttle is per some time, e.g. 1h")
        return per

    def __str__(self) -> str:
        return f"{self.max_runs} per {format_duration(self.per)}"


def _none_if_empty(value: object) -> object:
    return None if value == [] else value


class Subscription(Record):
    """When an event like this happens, run these actions."""

    kind = "subscription"
    indexed = ("name", "enabled")
    unique = (("name",),)

    name: Name
    """Its name."""
    description: str = ""
    """What it is for."""
    events: Annotated[list[Glob], Field(min_length=1)]
    """The event types it wants, or globs of them (e.g. gmail.message.received, slack.*)."""
    accounts: Annotated[list[str] | None, BeforeValidator(_none_if_empty)] = None
    """The accounts whose events it wants (e.g. gmail:work); none: every account's."""
    filter: FilterField | None = None
    """Which of those events: a filter on their data, as a tree or the shorthand (e.g.
    `from.address endswith "@bank.com" and not sent_by_udl`); none: all of them."""
    source_filter: JsonObject | None = None
    """What only the events' integration can check (e.g. a Gmail search: {"query":
    "has:attachment"}), after the filter."""
    actions: Annotated[list[Name], Field(min_length=1)]
    """The actions to run, by name: each is a run of its own."""
    throttle: Throttle | None = None
    """At most how often its actions run (e.g. 10 per 1h); the rest are skipped."""
    enabled: bool = True
    """Whether it runs anything."""

    @classmethod
    def example(cls) -> Self:
        return cls.model_validate(
            {
                "name": "bank-mail",
                "events": ["gmail.message.received"],
                "accounts": ["gmail:personal"],
                "filter": 'from.address endswith "@bank.com" and not sent_by_udl',
                "source_filter": {"query": "has:attachment"},
                "actions": ["file-statement"],
                "throttle": "10 per 1h",
            }
        )


def matches(subscription: Subscription, event: Event, account: str | None) -> bool:
    """Whether `subscription` wants `event` (from the account called `account`, if any): it is
    enabled, one of its types or globs is the event's, the account is one of its (or it names
    none) and the event's data passes its filter. (Its source filter and throttle are the
    bus's to check.)"""
    if not subscription.enabled:
        return False
    if not any(fnmatch.fnmatchcase(event.type, glob) for glob in subscription.events):
        return False
    if subscription.accounts is not None and account not in subscription.accounts:
        return False
    return subscription.filter is None or matches_filter(subscription.filter, event.data)


def types_of(subscription: Subscription, types: EventTypes) -> list[EventType[Any]]:
    """The types a subscription could get: those its globs match, and the custom type when one
    may match custom.<name> types."""
    found = {
        event_type.name: event_type
        for glob in subscription.events
        for event_type in types.matching(glob)
    }
    custom = any(types.may_be_custom(glob) for glob in subscription.events)
    return [*found.values(), *([CUSTOM] if custom else [])]


def check_subscription(
    subscription: Subscription,
    types: EventTypes,
    actions: Collection[str],
    *,
    source_filters: Mapping[str, Callable[[JsonObject], None]] | None = None,
    for_schedule: bool = False,
) -> None:
    """Raise what is wrong with `subscription`, to be saved where the actions called `actions`
    exist: a glob that matches no type (ConfigError), a filter that does not fit a type it
    could get (InvalidFilter), an action that does not exist, or a source filter that is not
    one integration's (each checked by its integration's check in `source_filters`, by
    source). A subscription named schedule:<name> belongs to that schedule (AlreadyExists),
    unless it is the schedule's own (`for_schedule`)."""
    if subscription.name.startswith(SCHEDULE_PREFIX) and not for_schedule:
        raise AlreadyExists(
            f"{subscription.name!r} is a schedule's: names that start {SCHEDULE_PREFIX} are kept "
            "for the subscriptions schedules run their actions through",
            hint="Name it otherwise, or set the schedule's actions (`udl schedules set`).",
        )
    for glob in subscription.events:
        if not types.matching(glob) and not types.may_be_custom(glob):
            raise ConfigError(
                f"No event type is {glob!r}",
                hint="`udl events types` (list_event_types) lists them; an event of your own "
                "is called custom.<name>.",
            )
    could_get = types_of(subscription, types)
    if subscription.filter is not None:
        check(subscription.filter, could_get)
    missing = [name for name in subscription.actions if name not in actions]
    if missing:
        raise ConfigError(
            f"There is no action called {', '.join(missing)}",
            hint="`udl actions list` (list_actions) lists them; `udl actions set` makes one.",
        )
    if subscription.source_filter is not None:
        _check_source_filter(subscription.source_filter, could_get, source_filters or {})


def _check_source_filter(
    source_filter: JsonObject,
    could_get: list[EventType[Any]],
    checks: Mapping[str, Callable[[JsonObject], None]],
) -> None:
    sources = {event_type.source for event_type in could_get if not event_type.is_custom}
    custom = any(event_type.is_custom for event_type in could_get)
    check_it = checks.get(next(iter(sources))) if len(sources) == 1 and not custom else None
    if check_it is None:
        raise ConfigError(
            "A source filter is checked by an integration, so every event type the "
            "subscription could get must be one integration's",
            hint="Leave the source filter out (udl's own event types have none), or "
            "subscribe to one integration's events only.",
        )
    check_it(source_filter)
