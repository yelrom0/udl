"""
----------------
- schedules.py -
----------------

Schedules: when `schedule.fired` events are fired. A schedule's spec is one of three, told apart
by its key:

    cron: "0 8 * * 1-5"          # minute hour day month weekday, in the schedule's time zone
    every: 15m                   # from `start` (default: when it was saved)
    at: 2026-11-01T09:00         # once

A cron expression is five fields (or @hourly, @daily, @weekly, @monthly, @yearly), with cron's
rule for days: when both the day of the month and the day of the week are restricted (neither
starts with *), either one matching is enough. The expression is matched against the zone's
wall-clock time: a time that a spring-forward skips runs at the first instant after the gap
(once, however many skipped times match), and a time that a fall-back repeats runs at its first
occurrence only. `every` is a length of time, not a wall-clock one (24h is 24h across a change),
counted from its start; `at` (and `every`'s start) without a zone is in the schedule's.

A schedule's zone is its own, else [automations] timezone, else the system's ($TZ, else the zone
/etc/localtime is). The cron expressions are read by cronsim, behind CronExpression, which only
gives wall-clock times: what they are in a zone is udl's own (next_times()).
"""

# System Imports
import itertools
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Annotated, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Package Imports
from cronsim import CronSim, CronSimError
from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError, field_validator

# Local Imports
from udl.config import Duration, format_duration

ALIASES: Final = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}
"""The cron expressions the aliases stand for."""
SHORTEST: Final = timedelta(minutes=1)
"""The shortest interval `every` may be."""
LOCALTIME: Final = Path("/etc/localtime")

# (Where an expression is first tried from, to tell one that never comes.)
_PROBE: Final = datetime(2000, 1, 1)
_ZONEINFO: Final = "zoneinfo"
# A stepped range whose ends are one value (5-5/3): cron's is that value, cronsim's that value
# and every step after it.
_ONE_VALUE_RANGE: Final = re.compile(r"(?<![\w-])(\w+)-\1/\d+(?![\w-])")


# ---------------------------------------------------------------------------------------------
# Cron expressions
# ---------------------------------------------------------------------------------------------


class CronExpression:
    """A cron expression (five fields, or an alias), and the wall-clock times it gives."""

    def __init__(self, text: str) -> None:
        written = " ".join(text.split())
        expanded = _ONE_VALUE_RANGE.sub(r"\1", ALIASES.get(written.lower(), written).upper())
        if len(expanded.split()) != 5:
            raise ValueError(
                f"{text!r} is not a cron expression: five fields (minute hour day month "
                "weekday, e.g. `0 8 * * 1-5`), or @hourly, @daily, @weekly, @monthly or @yearly"
            )
        try:
            CronSim(expanded, _PROBE)
        except CronSimError as e:
            raise ValueError(f"{text!r} is not a cron expression: {e}") from None
        self.text = written
        self._expanded = expanded
        if self.next_after(_PROBE) is None:
            raise ValueError(f"{text!r} is a cron expression whose time never comes")

    def times_after(self, local: datetime) -> Iterator[datetime]:
        """The wall-clock times (naive) it gives after `local` (naive), in order (they end
        when none comes for 50 years)."""
        return iter(CronSim(self._expanded, local))

    def next_after(self, local: datetime) -> datetime | None:
        """The first wall-clock time (naive) it gives after `local` (naive)."""
        return next(self.times_after(local), None)


# ---------------------------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------------------------


class _Spec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


def _cron(text: str) -> str:
    return CronExpression(text).text


class CronSpec(_Spec):
    """A cron expression, in the schedule's time zone."""

    cron: Annotated[str, AfterValidator(_cron)]
    """Five fields (minute hour day month weekday, e.g. `0 8 * * 1-5`), or @hourly, @daily,
    @weekly, @monthly or @yearly."""


class EverySpec(_Spec):
    """An interval, counted from its start."""

    every: Duration
    """How often, e.g. 15m (a minute or more)."""
    start: datetime | None = None
    """When it counts from (without a zone: in the schedule's); none: when it was saved."""

    @field_validator("every")
    @classmethod
    def _a_minute_or_more(cls, every: timedelta) -> timedelta:
        if every < SHORTEST:
            raise ValueError("a schedule fires every minute at most often, e.g. every 1m")
        return every


class AtSpec(_Spec):
    """A time, once."""

    at: datetime
    """When (without a zone: in the schedule's)."""


type ScheduleSpec = CronSpec | EverySpec | AtSpec
"""When a schedule fires: told apart by its key (cron, every or at)."""


def next_times(spec: ScheduleSpec, zone: tzinfo, after: datetime, count: int) -> list[datetime]:
    """The next `count` times (in UTC) `spec` gives in `zone`, strictly after `after`."""
    found: list[datetime] = []
    for time in _times(spec, zone, after):
        if len(found) == count:
            break
        found.append(time)
    return found


def next_time(spec: ScheduleSpec, zone: tzinfo, after: datetime) -> datetime | None:
    """The first time (in UTC) `spec` gives in `zone` after `after`; None when it gives none."""
    return next(_times(spec, zone, after), None)


def _times(spec: ScheduleSpec, zone: tzinfo, after: datetime) -> Iterator[datetime]:
    if isinstance(spec, CronSpec):
        return _cron_times(CronExpression(spec.cron), zone, after)
    if isinstance(spec, EverySpec):
        start = after if spec.start is None else instant_of(spec.start, zone)
        first = 0 if after < start else (after - start) // spec.every + 1
        return (start + n * spec.every for n in itertools.count(first))
    at = instant_of(spec.at, zone)
    return iter([at] if at > after else [])


def _cron_times(expression: CronExpression, zone: tzinfo, after: datetime) -> Iterator[datetime]:
    after = after.astimezone(UTC)
    last = after
    walls = expression.times_after(after.astimezone(zone).replace(tzinfo=None))
    for wall in walls:  # pragma: no branch - (they never end: one that does is refused)
        # (A time repeated by a fall-back that came before `after`, or one more time skipped
        # by a spring-forward, is already gone by.)
        time = instant(wall, zone)
        if time > last:
            yield time
            last = time


def instant_of(time: datetime, zone: tzinfo) -> datetime:
    """`time` in UTC: one without a zone is a wall-clock time in `zone` (see instant())."""
    return instant(time, zone) if time.tzinfo is None else time.astimezone(UTC)


def instant(wall: datetime, zone: tzinfo) -> datetime:
    """The instant (in UTC) the wall-clock time `wall` is in `zone`: its first occurrence, when
    a fall-back repeats it; the first instant after the gap, when a spring-forward skips it."""
    first = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
    second = wall.replace(tzinfo=zone, fold=1).astimezone(UTC)
    if first <= second:
        return first
    # (In a gap, fold 0 reads it with the offset before the change and fold 1 with the one
    # after, so the change is between the two.)
    return _change_between(zone, second.replace(microsecond=0), first)


def _change_between(zone: tzinfo, low: datetime, high: datetime) -> datetime:
    """The instant in (low, high] that `zone`'s offset changes at (changes are on a second)."""
    after = high.astimezone(zone).utcoffset()
    lowest, highest = 0, int((high - low).total_seconds()) + 1
    while lowest < highest:
        middle = (lowest + highest) // 2
        if (low + timedelta(seconds=middle)).astimezone(zone).utcoffset() == after:
            highest = middle
        else:
            lowest = middle + 1
    return low + timedelta(seconds=lowest)


# ---------------------------------------------------------------------------------------------
# A spec as a line
# ---------------------------------------------------------------------------------------------


def parse_spec(text: str) -> ScheduleSpec:
    """A spec written as a line: `every 15m` (`every 1d from 2026-01-10T09:00`), `at
    2026-11-01T09:00`, or else a cron expression. Raises ValueError."""
    word, _, rest = text.strip().partition(" ")
    rest = rest.strip()
    try:
        match word.lower():
            case "every":
                every, _, start = rest.partition(" from ")
                return EverySpec.model_validate(
                    {"every": every.strip(), "start": start.strip() or None}
                )
            case "at":
                return AtSpec.model_validate({"at": rest})
            case _:
                return CronSpec(cron=text)
    except ValidationError as e:
        problems = "; ".join(str(problem["msg"]) for problem in e.errors())
        raise ValueError(
            f"{text!r} is not a schedule (`every 15m`, `at 2026-11-01T09:00`, or a cron "
            f"expression such as `0 8 * * 1-5`): {problems}"
        ) from None


def spec_text(spec: ScheduleSpec) -> str:
    """`spec` as the line parse_spec() reads."""
    if isinstance(spec, CronSpec):
        return spec.cron
    if isinstance(spec, EverySpec):
        start = f" from {spec.start.isoformat()}" if spec.start is not None else ""
        return f"every {format_duration(spec.every)}{start}"
    return f"at {spec.at.isoformat()}"


# ---------------------------------------------------------------------------------------------
# Time zones
# ---------------------------------------------------------------------------------------------


def schedule_zone(own: str | None, default: str) -> ZoneInfo:
    """The time zone a schedule runs in: its own (an IANA name), else `default` ([automations]
    timezone), else the system's."""
    name = own or default
    return ZoneInfo(name) if name else system_timezone()


def system_timezone(localtime: Path = LOCALTIME) -> ZoneInfo:
    """The system's time zone: $TZ's, else the one `localtime` links to (or is), else UTC."""
    for name in (os.environ.get("TZ", "").removeprefix(":"), _linked_zone(localtime)):
        if name:
            try:
                return ZoneInfo(name)
            except ZoneInfoNotFoundError, ValueError:
                pass
    try:
        with localtime.open("rb") as tzfile:
            return ZoneInfo.from_file(tzfile, key="localtime")
    except OSError, ValueError:
        return ZoneInfo("UTC")


def _linked_zone(localtime: Path) -> str:
    """The zone's name `localtime` links to (…/zoneinfo/Europe/Paris), or ""."""
    if not localtime.is_symlink():
        return ""
    parts = localtime.resolve().parts
    return "/".join(parts[parts.index(_ZONEINFO) + 1 :]) if _ZONEINFO in parts else ""
