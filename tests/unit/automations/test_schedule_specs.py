"""
---------------------------
- test_schedule_specs.py -
---------------------------

A schedule's spec: a cron expression, an interval (`every`, anchored at its start) or a time
(`at`, once), in a time zone. Their next times are strictly after `after`, in order, and match
the spec in the zone's wall-clock time: a time a spring-forward skips runs once, at the first
instant after the gap, and a time a fall-back repeats runs once, at its first occurrence. A
brute-force oracle (every minute, by hypothesis) checks it near every DST change of 2026-2027 in
London, New York, Lord Howe (a 30-minute shift) and Chatham (+12:45)
"""

# System Imports
import functools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

# Package Imports
import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

# Local Imports
from udl.automations.schedules import (
    AtSpec,
    CronExpression,
    CronSpec,
    EverySpec,
    ScheduleSpec,
    next_time,
    next_times,
    parse_spec,
    schedule_zone,
    spec_text,
    system_timezone,
)

ZONES = ("Europe/London", "America/New_York", "Australia/Lord_Howe", "Pacific/Chatham")
MINUTE = timedelta(minutes=1)


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def cron(text: str, zone: str, after: datetime, count: int = 5) -> list[datetime]:
    return next_times(CronSpec(cron=text), ZoneInfo(zone), after, count)


# ---------------------------------------------------------------------------------------------
# Cron expressions
# ---------------------------------------------------------------------------------------------


def test_a_cron_expression_gives_the_next_wall_clock_time_strictly_after() -> None:
    expression = CronExpression("0 8 * * 1-5")

    assert expression.next_after(datetime(2026, 1, 2, 7, 59)) == datetime(2026, 1, 2, 8, 0)
    assert expression.next_after(datetime(2026, 1, 2, 8, 0)) == datetime(2026, 1, 5, 8, 0)


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("@hourly", utc(2026, 1, 7, 11, 0)),
        ("@daily", utc(2026, 1, 8)),
        ("@midnight", utc(2026, 1, 8)),
        ("@weekly", utc(2026, 1, 11)),  # (a Sunday)
        ("@monthly", utc(2026, 2, 1)),
        ("@yearly", utc(2027, 1, 1)),
        ("@annually", utc(2027, 1, 1)),
        ("@DAILY", utc(2026, 1, 8)),
    ],
)
def test_each_alias(alias: str, expected: datetime) -> None:
    assert cron(alias, "UTC", utc(2026, 1, 7, 10, 20), 1) == [expected]


def test_names_of_days_and_months_in_any_case() -> None:
    assert cron("0 9 1 jan mon-fri", "UTC", utc(2026, 1, 1), 2) == [
        utc(2026, 1, 1, 9),  # (the 1st of January, or a weekday in it)
        utc(2026, 1, 2, 9),
    ]


def test_either_day_matching_is_enough_when_both_are_restricted() -> None:
    # (cron's rule: the 13th, or a Friday)
    assert cron("0 0 13 * 5", "UTC", utc(2026, 1, 1), 4) == [
        utc(2026, 1, 2),
        utc(2026, 1, 9),
        utc(2026, 1, 13),
        utc(2026, 1, 16),
    ]


def test_a_stepped_range_of_one_value_is_that_value() -> None:
    assert cron("5-5/3,40-40/7 * * * *", "UTC", utc(2026, 1, 1), 3) == [
        utc(2026, 1, 1, 0, 5),
        utc(2026, 1, 1, 0, 40),
        utc(2026, 1, 1, 1, 5),
    ]


def test_both_days_must_match_when_one_starts_with_a_star() -> None:
    # (An odd day that is a Friday.)
    assert cron("0 0 */2 * 5", "UTC", utc(2026, 1, 1), 2) == [utc(2026, 1, 9), utc(2026, 1, 23)]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "* * * *",  # four fields
        "0 * * * * *",  # six (seconds are not offered)
        "61 * * * *",
        "0 25 * * *",
        "0 0 30 2 *",  # (never comes)
        "0 0 */3 2 1#5",  # (Feb 29 is the only fifth Monday, and no */3 day)
        "@reboot",
        "every 5m",
    ],
)
def test_what_is_not_a_cron_expression_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="cron"):
        CronExpression(text)
    with pytest.raises(ValidationError):
        CronSpec(cron=text)


def test_a_cron_spec_keeps_its_expression_as_written_but_for_spaces() -> None:
    assert CronSpec(cron="  0   8 * *  1-5 ").cron == "0 8 * * 1-5"


# ---------------------------------------------------------------------------------------------
# Daylight saving time: 2026's changes, by hand
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "text", "after", "expected"),
    [
        # London springs forward at 01:00 GMT (to 02:00 BST): 01:30 is skipped, so it runs at
        # the first instant after the gap, once.
        (
            "Europe/London",
            "30 1 * * *",
            utc(2026, 3, 28, 12),
            [utc(2026, 3, 29, 1), utc(2026, 3, 30, 0, 30)],
        ),
        (
            "Europe/London",
            "*/30 * * * *",
            utc(2026, 3, 29, 0, 10),
            [utc(2026, 3, 29, 0, 30), utc(2026, 3, 29, 1), utc(2026, 3, 29, 1, 30)],
        ),
        # ...and falls back at 02:00 BST (to 01:00 GMT): 01:30 comes twice, and runs once.
        (
            "Europe/London",
            "30 1 * * *",
            utc(2026, 10, 24, 12),
            [utc(2026, 10, 25, 0, 30), utc(2026, 10, 26, 1, 30)],
        ),
        (
            "Europe/London",
            "*/30 * * * *",
            utc(2026, 10, 24, 23, 10),
            [
                utc(2026, 10, 24, 23, 30),  # 00:30 BST
                utc(2026, 10, 25, 0, 0),  # 01:00 BST
                utc(2026, 10, 25, 0, 30),  # 01:30 BST
                utc(2026, 10, 25, 2, 0),  # 02:00 GMT (01:00 and 01:30 GMT came before)
            ],
        ),
        (  # (from the second 01:10)
            "Europe/London",
            "*/30 * * * *",
            utc(2026, 10, 25, 1, 10),
            [utc(2026, 10, 25, 2), utc(2026, 10, 25, 2, 30)],
        ),
        (
            "America/New_York",
            "30 2 * * *",
            utc(2026, 3, 7, 12),
            [utc(2026, 3, 8, 7), utc(2026, 3, 9, 6, 30)],
        ),
        (
            "America/New_York",
            "30 1 * * *",
            utc(2026, 10, 31, 12),
            [utc(2026, 11, 1, 5, 30), utc(2026, 11, 2, 6, 30)],
        ),
        # Lord Howe moves half an hour: back from 02:00 to 01:30, forward from 02:00 to 02:30.
        (
            "Australia/Lord_Howe",
            "45 1 * * *",
            utc(2026, 4, 4),
            [utc(2026, 4, 4, 14, 45), utc(2026, 4, 5, 15, 15)],
        ),
        (
            "Australia/Lord_Howe",
            "15 2 * * *",
            utc(2026, 10, 3),
            [utc(2026, 10, 3, 15, 30), utc(2026, 10, 4, 15, 15)],
        ),
        # Chatham (+12:45) moves at 02:45 standard time.
        (
            "Pacific/Chatham",
            "0 3 * * *",
            utc(2026, 4, 4),
            [utc(2026, 4, 4, 13, 15), utc(2026, 4, 5, 14, 15)],
        ),
        (
            "Pacific/Chatham",
            "0 3 * * *",
            utc(2026, 9, 26),
            [utc(2026, 9, 26, 14), utc(2026, 9, 27, 13, 15)],
        ),
    ],
)
def test_daylight_saving_time_changes(
    zone: str, text: str, after: datetime, expected: list[datetime]
) -> None:
    assert cron(text, zone, after, len(expected)) == expected


# ---------------------------------------------------------------------------------------------
# Daylight saving time: every change in 2026-2027, against a brute-force oracle
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cron:
    """A cron expression, and the values each of its fields allows (what the oracle reads)."""

    fields: tuple[tuple[str, frozenset[int]], ...]  # minute, hour, day, month, weekday

    @property
    def text(self) -> str:
        return " ".join(text for text, _ in self.fields)

    def matches(self, wall: datetime) -> bool:
        (_, minutes), (_, hours), (day_text, days), (_, months), (weekday_text, weekdays) = (
            self.fields
        )
        day, weekday = wall.day in days, wall.isoweekday() % 7 in weekdays
        either = not day_text.startswith("*") and not weekday_text.startswith("*")
        return (
            wall.minute in minutes
            and wall.hour in hours
            and wall.month in months
            and ((day or weekday) if either else (day and weekday))
        )


def oracle(expression: Cron, zone: tzinfo, after: datetime, until: datetime) -> list[datetime]:
    """Its times in (after, until], minute by minute: a wall-clock time that matches, at its
    first occurrence; and the first instant after a gap when a time in the gap matches."""
    found: list[datetime] = []
    at = after.replace(second=0, microsecond=0) + MINUTE
    offset = (at - MINUTE).astimezone(zone).utcoffset()
    while at <= until:
        local = at.astimezone(zone)
        wall = local.replace(tzinfo=None, fold=0)
        changed = local.utcoffset()
        assert changed is not None
        assert offset is not None
        if changed > offset:  # (the first instant after a gap)
            skipped = wall - (changed - offset)
            matched = any(
                expression.matches(skipped + n * MINUTE)
                for n in range(int((changed - offset) / MINUTE) + 1)
            )
        else:
            matched = local.fold == 0 and expression.matches(wall)
        if matched:
            found.append(at)
        offset = changed
        at += MINUTE
    return found


@functools.cache
def changes(zone: str) -> tuple[datetime, ...]:
    """The instants `zone`'s offset changed at in 2026-2027."""
    found: list[datetime] = []
    tz = ZoneInfo(zone)
    at = utc(2026, 1, 1)
    offset = at.astimezone(tz).utcoffset()
    while at < utc(2028, 1, 1):
        later = at + timedelta(minutes=15)
        if later.astimezone(tz).utcoffset() != offset:
            found.append(later)
            offset = later.astimezone(tz).utcoffset()
        at = later
    return tuple(found)


def test_each_zone_changes_four_times_in_2026_and_2027() -> None:
    assert {zone: len(changes(zone)) for zone in ZONES} == dict.fromkeys(ZONES, 4)


def _field(low: int, high: int, star: int) -> st.SearchStrategy[tuple[str, frozenset[int]]]:
    """A cron field over low..high (a star `star` times in 7, to keep times coming)."""
    every = frozenset(range(low, high + 1))
    one = st.integers(low, high).map(lambda value: (str(value), frozenset({value})))
    many = st.sets(st.integers(low, high), min_size=2, max_size=4).map(
        lambda values: (",".join(map(str, sorted(values))), frozenset(values))
    )
    span = st.tuples(st.integers(low, high), st.integers(low, high)).map(sorted)
    ranged = span.map(lambda ends: (f"{ends[0]}-{ends[1]}", frozenset(range(ends[0], ends[1] + 1))))
    stepped = st.integers(2, max(2, (high - low) // 2)).map(
        lambda step: (f"*/{step}", frozenset(range(low, high + 1, step)))
    )
    stepped_range = st.tuples(span, st.integers(2, 5)).map(
        lambda found: (
            f"{found[0][0]}-{found[0][1]}/{found[1]}",
            frozenset(range(found[0][0], found[0][1] + 1, found[1])),
        )
    )
    others = [one, many, ranged, stepped, stepped_range]
    return st.one_of(*([st.just(("*", every))] * star), *others[: 7 - star])


_crons = st.builds(
    lambda *fields: Cron(fields),
    _field(0, 59, 2),
    _field(0, 23, 4),
    _field(1, 31, 5),
    _field(1, 12, 5),
    _field(0, 6, 5),
)


@st.composite
def _near_a_change(draw: st.DrawFn) -> tuple[str, datetime]:
    zone = draw(st.sampled_from(ZONES))
    change = draw(st.sampled_from(changes(zone)))
    seconds = draw(st.integers(-36 * 3600, 36 * 3600))
    return zone, change + timedelta(seconds=seconds, microseconds=draw(st.integers(0, 999_999)))


@settings(max_examples=300, deadline=None)
@given(expression=_crons, near=_near_a_change())
def test_next_times_agree_with_the_oracle_near_every_change(
    expression: Cron, near: tuple[str, datetime]
) -> None:
    zone, after = near
    try:
        spec = CronSpec(cron=expression.text)
    except ValidationError:  # (e.g. the 31st of a month without one)
        assume(False)
        raise
    until = after + timedelta(days=2)

    found = next_times(spec, ZoneInfo(zone), after, 5)

    assert all(time > after for time in found)
    assert found == sorted(set(found))
    assert all(time.tzinfo is UTC for time in found)
    within = [time for time in found if time <= until]
    expected = oracle(expression, ZoneInfo(zone), after, until)
    assert within == expected[: len(within)]
    assert len(within) == len(found) or within == expected


@pytest.mark.parametrize(
    ("zone", "change"),
    [(zone, change) for zone in ZONES for change in changes(zone)],
    ids=lambda found: found if isinstance(found, str) else found.date().isoformat(),
)
@pytest.mark.parametrize("text", ["*/15 * * * *", "0 * * * *", "30 * * * *", "@daily"])
def test_next_times_are_right_across_every_change_in_2026_and_2027(
    zone: str, change: datetime, text: str
) -> None:
    tz = ZoneInfo(zone)
    fields = text if not text.startswith("@") else "0 0 * * *"
    minute, hour, *_ = fields.split()
    expression = Cron(
        (
            (minute, frozenset(range(0, 60, 15)) if minute == "*/15" else frozenset({int(minute)})),
            (hour, frozenset(range(24)) if hour == "*" else frozenset({int(hour)})),
            ("*", frozenset(range(1, 32))),
            ("*", frozenset(range(1, 13))),
            ("*", frozenset(range(7))),
        )
    )
    after = change - timedelta(hours=6)
    until = change + timedelta(hours=6)

    found = [time for time in cron(text, zone, after, 50) if time <= until]

    assert found == oracle(expression, tz, after, until)


@pytest.mark.parametrize(
    ("zone", "change"), [(zone, change) for zone in ZONES for change in changes(zone)]
)
def test_a_daily_time_inside_each_change_runs_once(zone: str, change: datetime) -> None:
    tz = ZoneInfo(zone)
    before, after = (change - MINUTE).astimezone(tz), change.astimezone(tz)
    # (A wall-clock time the change skips, or repeats: just after the earlier of the two.)
    inside = min(before, after).replace(tzinfo=None) + MINUTE
    text = f"{inside.minute} {inside.hour} * * *"

    found = [
        time
        for time in cron(text, zone, change - timedelta(hours=12), 3)
        if time < change + timedelta(hours=12)
    ]

    assert len(found) == 1
    if after.utcoffset() > before.utcoffset():  # type: ignore[operator]
        assert found == [change]  # skipped: at the first instant after the gap
    else:
        assert found[0] < change  # repeated: at its first occurrence


# ---------------------------------------------------------------------------------------------
# Every, at
# ---------------------------------------------------------------------------------------------


def test_every_is_anchored_at_its_start_without_drifting() -> None:
    spec = EverySpec(every=timedelta(minutes=15), start=utc(2026, 1, 1, 0, 0, 7))

    found = next_times(spec, ZoneInfo("UTC"), utc(2026, 3, 1, 10, 31), 3)

    assert found == [
        utc(2026, 3, 1, 10, 45, 7),
        utc(2026, 3, 1, 11, 0, 7),
        utc(2026, 3, 1, 11, 15, 7),
    ]
    assert next_times(spec, ZoneInfo("UTC"), found[0], 1) == [found[1]]


def test_every_starts_at_its_start_when_that_is_to_come() -> None:
    spec = EverySpec(every=timedelta(hours=1), start=utc(2026, 5, 1, 9))

    assert next_times(spec, ZoneInfo("UTC"), utc(2026, 1, 1), 2) == [
        utc(2026, 5, 1, 9),
        utc(2026, 5, 1, 10),
    ]


def test_every_s_start_without_a_zone_is_in_the_schedule_s() -> None:
    spec = EverySpec(every="1d", start=datetime(2026, 1, 10, 9))  # type: ignore[arg-type]

    found = next_times(spec, ZoneInfo("America/New_York"), utc(2026, 1, 1), 1)

    assert found == [utc(2026, 1, 10, 14)]


def test_every_is_a_length_of_time_not_a_wall_clock_one() -> None:
    # (Across a change, 24h is 24h: the wall-clock time moves by the change.)
    spec = EverySpec(every="24h", start=utc(2026, 3, 28, 12))  # type: ignore[arg-type]

    [found] = next_times(spec, ZoneInfo("Europe/London"), utc(2026, 3, 29), 1)

    assert found.astimezone(ZoneInfo("Europe/London")).hour == 13


def test_every_without_a_start_counts_from_after() -> None:
    spec = EverySpec(every=timedelta(minutes=5))

    assert next_times(spec, ZoneInfo("UTC"), utc(2026, 1, 1, 0, 1), 2) == [
        utc(2026, 1, 1, 0, 6),
        utc(2026, 1, 1, 0, 11),
    ]


@pytest.mark.parametrize("every", ["59s", "0s", 30])
def test_every_is_a_minute_or_more(every: object) -> None:
    with pytest.raises(ValidationError, match="minute"):
        EverySpec.model_validate({"every": every})


def test_at_comes_once() -> None:
    spec = AtSpec(at=utc(2026, 11, 1, 9))

    assert next_times(spec, ZoneInfo("UTC"), utc(2026, 1, 1), 5) == [utc(2026, 11, 1, 9)]
    assert next_times(spec, ZoneInfo("UTC"), utc(2026, 11, 1, 9), 5) == []
    assert next_time(spec, ZoneInfo("UTC"), utc(2026, 11, 2)) is None


@pytest.mark.parametrize(
    ("at", "expected"),
    [
        (datetime(2026, 7, 1, 9), utc(2026, 7, 1, 8)),  # (BST)
        (datetime(2026, 3, 29, 1, 30), utc(2026, 3, 29, 1)),  # skipped: after the gap
        (datetime(2026, 10, 25, 1, 30), utc(2026, 10, 25, 0, 30)),  # repeated: the first
    ],
)
def test_at_without_a_zone_is_in_the_schedule_s(at: datetime, expected: datetime) -> None:
    assert next_times(AtSpec(at=at), ZoneInfo("Europe/London"), utc(2026, 1, 1), 1) == [expected]


def test_next_time_is_the_first_of_next_times() -> None:
    spec = CronSpec(cron="0 8 * * *")

    assert next_time(spec, ZoneInfo("UTC"), utc(2026, 1, 1)) == utc(2026, 1, 1, 8)


# ---------------------------------------------------------------------------------------------
# A spec as a line, and as JSON
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "spec"),
    [
        ("0 8 * * 1-5", CronSpec(cron="0 8 * * 1-5")),
        ("@daily", CronSpec(cron="@daily")),
        ("every 15m", EverySpec(every=timedelta(minutes=15))),
        ("every 1h30m", EverySpec(every=timedelta(minutes=90))),
        (
            "every 1d from 2026-01-10T09:00:00",
            EverySpec(every=timedelta(days=1), start=datetime(2026, 1, 10, 9)),
        ),
        ("at 2026-11-01T09:00:00", AtSpec(at=datetime(2026, 11, 1, 9))),
        ("at 2026-11-01T09:00:00+00:00", AtSpec(at=utc(2026, 11, 1, 9))),
    ],
)
def test_a_spec_as_a_line_round_trips(text: str, spec: ScheduleSpec) -> None:
    assert parse_spec(text) == spec
    assert parse_spec(spec_text(spec)) == spec


def test_a_spec_as_a_line_may_be_spaced_and_in_any_case() -> None:
    assert parse_spec("  EVERY   15m ") == EverySpec(every=timedelta(minutes=15))
    assert parse_spec("At 2026-11-01T09:00") == AtSpec(at=datetime(2026, 11, 1, 9))


@pytest.mark.parametrize("text", ["", "every", "every soon", "at noon", "at", "* * *"])
def test_a_line_that_is_no_spec_is_refused(text: str) -> None:
    with pytest.raises(ValueError, match="schedule"):
        parse_spec(text)


def test_specs_are_told_apart_by_their_key() -> None:
    specs = TypeAdapter[ScheduleSpec](ScheduleSpec)

    assert specs.validate_python({"cron": "@hourly"}) == CronSpec(cron="@hourly")
    assert specs.validate_python({"every": "5m"}) == EverySpec(every=timedelta(minutes=5))
    assert specs.validate_python({"at": "2026-11-01T09:00"}) == AtSpec(at=datetime(2026, 11, 1, 9))
    assert specs.dump_python(EverySpec(every=timedelta(minutes=5)), mode="json") == {
        "every": "5m",
        "start": None,
    }
    with pytest.raises(ValidationError):
        specs.validate_python({"cron": "@hourly", "every": "5m"})


# ---------------------------------------------------------------------------------------------
# Time zones
# ---------------------------------------------------------------------------------------------


def test_a_schedule_s_zone_is_its_own_else_the_settings_else_the_system_s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TZ", "Asia/Tokyo")

    assert schedule_zone("Europe/London", "America/New_York") == ZoneInfo("Europe/London")
    assert schedule_zone(None, "America/New_York") == ZoneInfo("America/New_York")
    assert schedule_zone(None, "") == ZoneInfo("Asia/Tokyo")


@pytest.mark.parametrize("tz", ["Asia/Tokyo", ":Asia/Tokyo"])
def test_the_system_s_zone_is_tz_first(monkeypatch: pytest.MonkeyPatch, tz: str) -> None:
    monkeypatch.setenv("TZ", tz)

    assert system_timezone() == ZoneInfo("Asia/Tokyo")


def test_the_system_s_zone_is_where_localtime_links_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TZ", raising=False)
    zones = tmp_path / "usr" / "share" / "zoneinfo" / "Europe"
    zones.mkdir(parents=True)
    (zones / "Paris").write_bytes(b"")
    localtime = tmp_path / "localtime"
    localtime.symlink_to(zones / "Paris")

    assert system_timezone(localtime) == ZoneInfo("Europe/Paris")


def test_the_system_s_zone_is_read_from_localtime_when_it_is_no_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TZ", "not a zone")
    localtime = tmp_path / "localtime"
    tzfile = Path("/usr/share/zoneinfo/Asia/Tokyo")
    if not tzfile.exists():  # pragma: no cover - (every system udl runs on has tzdata)
        pytest.skip("no tzdata")
    localtime.write_bytes(tzfile.read_bytes())

    zone = system_timezone(localtime)

    assert zone.utcoffset(datetime(2026, 1, 1)) == timedelta(hours=9)
    assert str(zone) == "localtime"


def test_the_system_s_zone_is_utc_when_nothing_says(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("TZ", raising=False)

    assert system_timezone(tmp_path / "nothing") == ZoneInfo("UTC")
