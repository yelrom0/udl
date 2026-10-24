"""
-------------
- models.py -
-------------

The automations' operations' requests and responses (events, subscriptions, actions, runs and
the runtime's status). They are what the API and the MCP server show, so their fields are
described by the docstrings under them (what an AI agent reads about a tool).
"""

# System Imports
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Self
from uuid import UUID

# Package Imports
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

# Local Imports
from udl.automations.events import CUSTOM_PREFIX, EventRecord, EventStatus, EventTypes
from udl.automations.filters import Filter, FilterField, shorthand
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.schedules import (
    AtSpec,
    CronSpec,
    CronText,
    EverySpec,
    Interval,
    Length,
    Misfire,
    Schedule,
    ScheduleName,
    ScheduleSpec,
    ZoneName,
    spec_text,
)
from udl.automations.subscriptions import Glob, Subscription, Throttle
from udl.config import Duration
from udl.db.models import JsonObject
from udl.db.records import Name
from udl.errors import ErrorInfo
from udl.plugins.actions.base import Action

_PAGE = 50  # (what a list gives, unless asked for more)
_MOST = 1000  # (what a list gives at most)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class _Paged(_Frozen):
    limit: Annotated[PositiveInt, Field(le=_MOST)] = _PAGE
    """At most this many (newest first)."""
    offset: NonNegativeInt = 0
    """Skip this many first."""


# ---------------------------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------------------------


class ListEventTypesRequest(_Frozen):
    """Which event types to describe."""

    match: str = "*"
    """Only those whose names this glob matches (e.g. udl.action.*)."""


class EventInfo(_Frozen):
    """An event, and how it was dispatched."""

    id: UUID
    """Its id."""
    type: str
    """Its event type."""
    source: str
    """What published it."""
    account_id: UUID | None
    """The account it happened in, if any."""
    occurred_at: AwareDatetime
    """When it happened."""
    stored_at: AwareDatetime
    """When udl stored it."""
    subject: str
    """A line saying what happened."""
    data: JsonObject
    """What happened, as its type's model has it."""
    dedupe_key: str | None
    """What makes it the same event as another of its type."""
    caused_by: UUID | None
    """The run that led to it, if one did."""
    depth: NonNegativeInt
    """How many runs led to it (0: it came from outside)."""
    status: EventStatus
    """pending (not dispatched yet), dispatched, or dropped (never dispatched: see why)."""
    dispatched_at: AwareDatetime | None
    """When it was dispatched (or dropped)."""
    dropped_reason: str | None
    """Why it was dropped."""

    @classmethod
    def of(cls, record: EventRecord) -> Self:
        return cls.model_validate(
            {
                **record.model_dump(exclude={"created_at", "updated_at"}),
                "stored_at": record.created_at,
            }
        )


class EventList(_Frozen):
    events: list[EventInfo]
    """The events, newest first."""


class ListEventsRequest(_Paged):
    """Which events to list."""

    type: Glob = "*"
    """Only events whose type this glob matches (e.g. gmail.*)."""
    source: str | None = None
    """Only those this published (e.g. udl, cli, api, mcp, gmail)."""
    account_id: UUID | None = None
    """Only those of this account."""
    status: EventStatus | None = None
    """Only those pending, dispatched or dropped."""
    since: AwareDatetime | None = None
    """Only those stored at this time or later."""
    until: AwareDatetime | None = None
    """Only those stored before this time."""


class EventRequest(_Frozen):
    """An event, by id."""

    id: UUID
    """The event's id."""


def _custom(named: str) -> str:
    if not named.startswith(CUSTOM_PREFIX) or EventTypes().find(named) is None:
        raise ValueError(f"{named!r} is not an event of your own: custom.<name>")
    return named


class EmitEventRequest(_Frozen):
    """An event of your own to publish."""

    type: Annotated[str, AfterValidator(_custom)]
    """Its type: custom.<name> (e.g. custom.hello)."""
    data: JsonObject = Field(default_factory=dict)
    """What happened: any JSON object (a `subject` in it is its subject)."""
    dedupe_key: str | None = None
    """What makes it the same event as another of its type: it is stored, and dispatched, once."""


class ReplayEventRequest(_Frozen):
    """An event to dispatch again."""

    id: UUID
    """The event's id."""
    subscription: Name | None = None
    """Only to this subscription (else to every one that matches it)."""


# ---------------------------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------------------------


class RunInfo(_Frozen):
    """A run: one action running for one event (or for none)."""

    id: UUID
    """Its id."""
    action: str
    """The action that runs."""
    plugin: str
    """The action's kind."""
    event_id: UUID | None
    """The event it runs for."""
    event_type: str | None
    """That event's type."""
    subscription: str | None
    """The subscription that ran it (none: it was asked for)."""
    status: RunStatus
    """queued, running, awaiting_approval, succeeded, failed, cancelled or skipped."""
    attempt: NonNegativeInt
    """How many times it was tried."""
    next_try_at: AwareDatetime | None
    """When a queued retry is tried."""
    queued_at: AwareDatetime
    """When it was queued."""
    started_at: AwareDatetime | None
    """When it started."""
    finished_at: AwareDatetime | None
    """When it ended."""
    summary: str
    """A line saying what it did."""
    error: ErrorInfo | None
    """Why it failed (or why its last try did, for a queued retry)."""
    skipped_reason: str | None
    """Why it was skipped."""

    @classmethod
    def of(cls, run: ActionRun) -> Self:
        fields = run.model_dump(include=set(cls.model_fields))
        return cls.model_validate({**fields, "queued_at": run.created_at})


class RunDetail(RunInfo):
    """A run, with what it gave and logged."""

    output: JsonValue
    """What it gave."""
    log: str
    """What it logged (its last 16 KiB)."""
    depth: NonNegativeInt
    """Its event's depth (what it publishes is one deeper)."""
    runner: str | None
    """The runtime that ran it (none: the udl that asked for it)."""

    @classmethod
    def of(cls, run: ActionRun) -> Self:
        fields = run.model_dump(include=set(cls.model_fields))
        return cls.model_validate({**fields, "queued_at": run.created_at})


class RunList(_Frozen):
    runs: list[RunInfo]
    """The runs, newest first."""


class EventDetail(_Frozen):
    event: EventInfo
    """The event."""
    runs: list[RunInfo]
    """Its runs, newest first."""


class ListRunsRequest(_Paged):
    """Which runs to list."""

    status: RunStatus | None = None
    """Only those queued, running, awaiting_approval, succeeded, failed, cancelled or skipped."""
    action: Name | None = None
    """Only this action's."""
    subscription: Name | None = None
    """Only those this subscription ran."""
    event: UUID | None = None
    """Only those for this event."""
    since: AwareDatetime | None = None
    """Only those queued at this time or later."""


class RunRequest(_Frozen):
    """A run, by id."""

    id: UUID
    """The run's id."""


# ---------------------------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------------------------


class _SubscriptionFields(_Frozen):
    description: str = ""
    """What it is for."""
    events: Annotated[list[Glob], Field(min_length=1)]
    """The event types it wants, or globs of them (e.g. gmail.message.received, custom.*)."""
    accounts: list[str] | None = None
    """The accounts whose events it wants (e.g. gmail:work); none: every account's."""
    filter: FilterField | None = None
    """Which of those events: a filter on their data, as the shorthand (e.g. `from.address
    endswith "@bank.com" and not sent_by_udl`) or a tree; none: all of them."""
    source_filter: JsonObject | None = None
    """What only the events' integration can check (e.g. a Gmail search)."""
    actions: Annotated[list[Name], Field(min_length=1)]
    """The actions to run, by name: each is a run of its own."""
    throttle: Throttle | None = None
    """At most how often its actions run (e.g. 10 per 1h); the rest are skipped."""
    enabled: bool = True
    """Whether it runs anything."""


class SetSubscriptionRequest(_SubscriptionFields):
    """A subscription to save (a new one, or the one of its name changed)."""

    name: Name
    """Its name."""


class SubscriptionInfo(_SubscriptionFields):
    """A subscription."""

    name: Name
    """Its name."""
    filter: Filter | None = None
    """Which of those events: a filter on their data (filter_text has it as the shorthand);
    none: all of them."""
    id: UUID
    """Its id."""
    filter_text: str | None
    """Its filter, as the shorthand."""
    created_at: AwareDatetime
    """When it was made."""
    updated_at: AwareDatetime
    """When it was last changed."""

    @classmethod
    def of(cls, subscription: Subscription) -> Self:
        return cls.model_validate(
            {
                **subscription.model_dump(),
                "filter_text": None
                if subscription.filter is None
                else shorthand(subscription.filter),
            }
        )


class SubscriptionList(_Frozen):
    subscriptions: list[SubscriptionInfo]
    """The subscriptions, by name."""


class ListSubscriptionsRequest(_Frozen):
    """Which subscriptions to list."""

    enabled: bool | None = None
    """Only those enabled (true), or disabled (false)."""


class SubscriptionRequest(_Frozen):
    """A subscription, by name."""

    name: Name
    """The subscription's name."""


class SubscriptionTestRequest(_Frozen):
    """A subscription to try on events: a saved one (name), or one given here, without saving it
    (its fields here beat the saved one's)."""

    name: Name | None = None
    """A saved subscription's name."""
    events: list[Glob] | None = None
    """The event types (or globs) to try."""
    accounts: list[str] | None = None
    """The accounts."""
    filter: FilterField | None = None
    """The filter (the shorthand, or a tree)."""
    event: UUID | None = None
    """Try it on this event (else on the latest events of its types)."""
    recent: Annotated[PositiveInt, Field(le=_MOST)] = _PAGE
    """How many of the latest events of its types to try it on."""


class EventMatch(_Frozen):
    """Whether a subscription would have matched an event."""

    event: EventInfo
    """The event."""
    matched: bool
    """Whether it would have matched."""
    failed: str | None
    """What did not match: its type, its account, or the part of the filter (as the
    shorthand)."""


class SubscriptionTestReport(_Frozen):
    """What a subscription would have matched."""

    filter: str | None
    """Its filter, as the shorthand."""
    tree: Filter | None
    """Its filter, as a tree."""
    problems: list[str]
    """What saving it would refuse, or should know (e.g. that it is disabled)."""
    events: list[EventMatch]
    """The events it was tried on, newest first, and whether it matched each."""


# ---------------------------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------------------------


def _some_time(timeout: timedelta) -> timedelta:
    if timeout <= timedelta(0):
        raise ValueError("an action's timeout is some time, e.g. 5m")
    return timeout


class _ActionFields(_Frozen):
    description: str = ""
    """What it is for."""
    plugin: str = Field(min_length=1)
    """Its kind: the action plugin that runs it (script, operation, webhook, emit, notify...:
    `udl plugins list`)."""
    options: JsonObject = Field(default_factory=dict)
    """Its kind's options (`udl actions show` gives their JSON Schema); texts in them are
    templates, rendered with each event (e.g. "{{ event.data.url }}")."""
    timeout: Annotated[Duration, AfterValidator(_some_time)] = timedelta(minutes=5)
    """How long a run may take before it fails (e.g. 5m)."""
    retries: Annotated[NonNegativeInt, Field(le=10)] = 0
    """How many more tries a run gets after a failure that trying again may help."""
    retry_timeouts: bool = False
    """Whether a run that timed out is tried again too."""
    max_concurrent: PositiveInt = 1
    """How many of its runs may run at once."""
    enabled: bool = True
    """Whether it runs (a disabled action's runs are skipped)."""


class SetActionRequest(_ActionFields):
    """An action to save (a new one, or the one of its name changed)."""

    name: Name
    """Its name."""

    @field_validator("name")
    @classmethod
    def _not_scripts(cls, name: str) -> str:
        if name == "scripts":
            raise ValueError("an action is not called scripts (/actions/scripts lists them)")
        return name


class ActionInfo(_ActionFields):
    """An action (its secrets hidden: each kept secret shows where the store keeps it)."""

    name: Name
    """Its name."""
    id: UUID
    """Its id."""
    options_schema: JsonObject
    """Its kind's options' JSON Schema."""
    used_by: list[str]
    """The subscriptions that run it."""
    warnings: list[str] = Field(default_factory=list[str])
    """What may keep it from running (e.g. its kind needs a program that is not installed)."""
    created_at: AwareDatetime
    """When it was made."""
    updated_at: AwareDatetime
    """When it was last changed."""

    @classmethod
    def of(
        cls,
        action: Action,
        *,
        options_schema: JsonObject,
        used_by: list[str],
        warnings: list[str] | None = None,
    ) -> Self:
        return cls.model_validate(
            {
                **action.model_dump(),
                "options_schema": options_schema,
                "used_by": used_by,
                "warnings": warnings or [],
            }
        )


class ActionList(_Frozen):
    actions: list[ActionInfo]
    """The actions, by name."""


class ListActionsRequest(_Frozen):
    """Which actions to list."""

    plugin: str | None = None
    """Only those of this kind (e.g. script)."""


class ActionRequest(_Frozen):
    """An action, by name."""

    name: Name
    """The action's name."""


class RunActionRequest(_Frozen):
    """An action to run now, here (not through a subscription)."""

    name: Name
    """The action's name."""
    event: UUID | None = None
    """The event to run it for (its templates see it), if any."""
    dry_run: bool = False
    """Only render its options with the event, without running it."""


class RunActionResult(_Frozen):
    run: RunDetail | None
    """The run, as it ended (none for a dry run)."""
    options: JsonObject | None
    """With dry_run: its options, rendered with the event (secrets hidden)."""


class ListScriptsRequest(_Frozen):
    """The programs script actions may run."""


class ScriptInfo(_Frozen):
    name: str
    """Its path in scripts_dir (what a script action's command names)."""
    executable: bool
    """Whether it may be run (chmod +x)."""
    size: NonNegativeInt
    """Its size, in bytes."""
    modified: AwareDatetime
    """When it was last changed."""


class ScriptList(_Frozen):
    dir: Path
    """scripts_dir: the only folder script actions run programs from."""
    exists: bool
    """Whether it is there."""
    scripts: list[ScriptInfo]
    """The programs in it (and in its folders), by name."""


class StatusRequest(_Frozen):
    """The automations' runtime's status."""


# ---------------------------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------------------------


class ScheduleRequest(_Frozen):
    """A schedule, by name."""

    name: ScheduleName
    """The schedule's name."""


class SetScheduleRequest(_Frozen):
    """A schedule to save (a new one, or the one of its name changed): when it fires is
    exactly one of cron, every and at."""

    name: ScheduleName
    """Its name."""
    description: str = ""
    """What it is for."""
    cron: CronText | None = None
    """A cron expression, in its time zone: five fields (minute hour day month weekday, e.g.
    `0 8 * * 1-5`), or @hourly, @daily, @weekly, @monthly or @yearly."""
    every: Interval | None = None
    """Or how often it fires, from start (e.g. 15m: a minute or more)."""
    at: datetime | None = None
    """Or when it fires, once (without a zone: in its time zone)."""
    start: datetime | None = None
    """When `every` counts from (without a zone: in its time zone); none: when it was first
    saved with that interval."""
    timezone: ZoneName = None
    """The time zone its times are in (an IANA name, e.g. Europe/London); none:
    [automations] timezone, else the system's."""
    data: JsonObject = Field(default_factory=dict)
    """What it passes on, in each schedule.fired event's data."""
    misfire: Misfire = Misfire.SKIP
    """What it does about the times it missed while udl was stopped: skip them, or fire the
    latest of them once (late), within misfire_grace."""
    misfire_grace: Length = timedelta(hours=1)
    """How late a missed time may still fire (when misfire is once)."""
    jitter: Length = timedelta(0)
    """At most how long after each time it fires (a random delay)."""
    enabled: bool = True
    """Whether it fires."""
    actions: list[Name] | None = None
    """The actions it runs when it fires (through its subscription, schedule:<name>); [] runs
    none; leaving it out keeps those it runs."""

    @model_validator(mode="after")
    def _one_spec(self) -> Self:
        given = [name for name in ("cron", "every", "at") if getattr(self, name) is not None]
        if len(given) != 1:
            raise ValueError(
                "a schedule fires by exactly one of cron, every and at"
                + (f" (not {' and '.join(given)})" if given else "")
            )
        if self.start is not None and self.every is None:
            raise ValueError("start is when `every` counts from: it goes with every")
        return self

    @property
    def spec(self) -> ScheduleSpec:
        """When it fires, as a spec."""
        if self.cron is not None:
            return CronSpec(cron=self.cron)
        if self.every is not None:
            return EverySpec(every=self.every, start=self.start)
        return AtSpec.model_validate({"at": self.at})


class ScheduleInfo(_Frozen):
    """A schedule."""

    name: str
    """Its name."""
    id: UUID
    """Its id."""
    description: str
    """What it is for."""
    spec: ScheduleSpec
    """When it fires: {"cron": ...}, {"every": ..., "start": ...} or {"at": ...}."""
    spec_text: str
    """When it fires, as a line (e.g. `every 15m`, as preview_schedule takes it)."""
    timezone: str | None
    """Its own time zone (none: the default)."""
    zone: str
    """The time zone it runs in: its own, else [automations] timezone, else the system's."""
    data: JsonObject
    """What it passes on, in each schedule.fired event's data."""
    misfire: Misfire
    """What it does about the times it missed while udl was stopped (skip, or once)."""
    misfire_grace: Duration
    """How late a missed time may still fire (when misfire is once)."""
    jitter: Duration
    """At most how long after each time it fires."""
    enabled: bool
    """Whether it fires."""
    next_run_at: AwareDatetime | None
    """When it fires next (without its jitter)."""
    last_run_at: AwareDatetime | None
    """When it last fired."""
    next_times: list[AwareDatetime]
    """Its next five times (none while it is disabled)."""
    actions: list[str]
    """The actions it runs when it fires."""
    subscription: SubscriptionInfo | None
    """The subscription it runs them through (schedule:<name>), when it runs any."""
    created_at: AwareDatetime
    """When it was made."""
    updated_at: AwareDatetime
    """When it was last changed."""

    @classmethod
    def of(
        cls,
        schedule: Schedule,
        *,
        zone: str,
        next_times: list[datetime],
        subscription: Subscription | None,
    ) -> Self:
        return cls.model_validate(
            {
                **schedule.model_dump(),
                "spec_text": spec_text(schedule.spec),
                "zone": zone,
                "next_times": next_times,
                "actions": subscription.actions if subscription is not None else [],
                "subscription": SubscriptionInfo.of(subscription)
                if subscription is not None
                else None,
            }
        )
