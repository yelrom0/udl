"""
-------------------------------
- test_schedule_operations.py -
-------------------------------

The schedules' operations, through the service. Saving a schedule plans it (an `at` time that
has gone by is refused, unless the schedule is saved disabled), and its actions are run
through a subscription it keeps, schedule:<name> (on schedule.fired where schedule is its
name), so one command makes a cron job; that subscription is the schedule's to change and to
delete, never set_subscription's or delete_subscription's
"""

# System Imports
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from support.actions import FakeAction, fake_action
from support.clock import T0, FakeClock
from support.interfaces import Harness
from udl.automations.filters import Condition, Op
from udl.automations.models import (
    ActionRequest,
    ScheduleInfo,
    ScheduleRequest,
    SetActionRequest,
    SetScheduleRequest,
    SetSubscriptionRequest,
    SubscriptionRequest,
)
from udl.automations.runs import ActionRun
from udl.automations.schedules import AtSpec, CronSpec, EverySpec, Schedule
from udl.automations.subscriptions import Subscription
from udl.config import AutomationsSettings
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import AlreadyExists, ConfigError, DoesNotExist, NotAllowed, TemplateFailed
from udl.plugins.registry import ACTIONS

MINUTE = timedelta(minutes=1)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake() -> type[FakeAction]:
    return fake_action("fake")


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock, fake: type[FakeAction]) -> Harness:
    found = Harness(
        tmp_path,
        clock=clock,
        automations=AutomationsSettings(scripts_dir=tmp_path / "scripts", timezone="UTC"),
    )
    found.registry.register(ACTIONS, fake)
    return found


@pytest.fixture
async def service(harness: Harness) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        yield found


async def set_schedule(service: UDLService, name: str = "digest", **fields: Any) -> ScheduleInfo:
    if not {"cron", "every", "at"} & set(fields):
        fields["cron"] = "0 8 * * 1-5"
    return await service.automations.set_schedule(
        SetScheduleRequest.model_validate({"name": name, **fields})
    )


async def get_schedule(service: UDLService, name: str = "digest") -> ScheduleInfo:
    return await service.automations.get_schedule(ScheduleRequest(name=name))


async def action(service: UDLService, name: str = "morning-digest", **fields: Any) -> None:
    fields.setdefault("plugin", "fake")
    await service.set_action(SetActionRequest.model_validate({"name": name, **fields}))


async def subscriptions(service: UDLService) -> dict[str, Subscription]:
    found = await service.db.list_records(Subscription, RecordQuery())
    return {subscription.name: subscription for subscription in found}


# ---------------------------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------------------------


async def test_a_saved_schedule_is_planned(service: UDLService) -> None:
    saved = await set_schedule(service, description="The morning digest", data={"report": 1})

    [stored] = await service.db.list_records(Schedule, RecordQuery())
    assert stored.spec == CronSpec(cron="0 8 * * 1-5")
    assert stored.next_run_at == datetime(2026, 1, 2, 8, tzinfo=UTC)  # (T0 is a Friday, 03:04)
    assert (saved.name, saved.description, saved.data) == (
        "digest",
        "The morning digest",
        {"report": 1},
    )
    assert (saved.spec_text, saved.zone, saved.next_run_at) == (
        "0 8 * * 1-5",
        "UTC",
        stored.next_run_at,
    )
    assert saved.next_times == [datetime(2026, 1, day, 8, tzinfo=UTC) for day in (2, 5, 6, 7, 8)]
    assert saved.subscription is None


async def test_saving_a_schedule_again_changes_it(service: UDLService) -> None:
    first = await set_schedule(service)

    second = await set_schedule(service, every="1h", timezone="Europe/London", enabled=False)

    assert second.id == first.id
    assert second.spec == EverySpec(every=timedelta(hours=1), start=T0)
    assert (second.timezone, second.zone, second.enabled) == (
        "Europe/London",
        "Europe/London",
        False,
    )
    assert second.next_times == []  # (disabled)
    assert len(await service.db.list_records(Schedule, RecordQuery())) == 1


async def test_an_interval_counts_from_when_it_was_first_saved(
    service: UDLService, clock: FakeClock
) -> None:
    first = await set_schedule(service, every="1h")
    await clock.advance(timedelta(minutes=20))

    again = await set_schedule(service, every="1h", data={"n": 2})
    changed = await set_schedule(service, every="2h")

    assert first.spec == again.spec == EverySpec(every=timedelta(hours=1), start=T0)
    assert again.next_run_at == T0 + timedelta(hours=1)
    assert changed.spec == EverySpec(every=timedelta(hours=2), start=T0 + timedelta(minutes=20))


async def test_an_interval_counts_from_the_start_it_is_given(service: UDLService) -> None:
    saved = await set_schedule(service, every="1d", start="2026-03-01T09:00:00")

    assert saved.spec == EverySpec(every=timedelta(days=1), start=datetime(2026, 3, 1, 9))
    assert saved.next_run_at == datetime(2026, 3, 1, 9, tzinfo=UTC)


async def test_an_at_schedule_fires_at_its_time(service: UDLService) -> None:
    saved = await set_schedule(service, at="2026-11-01T09:00")

    assert saved.spec == AtSpec(at=datetime(2026, 11, 1, 9))
    assert saved.next_times == [datetime(2026, 11, 1, 9, tzinfo=UTC)]


async def test_an_at_time_that_has_gone_by_is_refused(service: UDLService) -> None:
    with pytest.raises(ConfigError, match="gone by"):
        await set_schedule(service, at="2025-11-01T09:00")

    assert await service.db.list_records(Schedule, RecordQuery()) == []


async def test_an_at_time_that_has_gone_by_may_be_saved_disabled(service: UDLService) -> None:
    saved = await set_schedule(service, at="2025-11-01T09:00", enabled=False)

    assert (saved.enabled, saved.next_run_at) == (False, None)


@pytest.mark.parametrize(
    "fields",
    [
        {},  # (when is it?)
        {"cron": "@daily", "every": "1h"},
        {"cron": "@daily", "at": "2026-11-01T09:00"},
        {"cron": "@daily", "start": "2026-11-01T09:00"},  # (start is every's)
        {"cron": "61 * * * *"},
        {"every": "10s"},
        {"cron": "@daily", "timezone": "Nowhere/Special"},
        {"cron": "@daily", "jitter": "-1s"},
        {"cron": "@daily", "name": "x" * 92},
    ],
)
def test_a_schedule_is_one_of_cron_every_and_at(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SetScheduleRequest.model_validate({"name": "digest", **fields})


async def test_the_scheduler_is_told_when_a_schedule_changes(
    service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    told: list[bool] = []
    monkeypatch.setattr(service.scheduler, "changed", lambda: told.append(True))

    await set_schedule(service)
    await service.automations.delete_schedule(ScheduleRequest(name="digest"))

    assert told == [True, True]


async def test_an_unknown_schedule_does_not_exist(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match="nope"):
        await get_schedule(service, "nope")


# ---------------------------------------------------------------------------------------------
# One command for a cron job: its actions, through its subscription
# ---------------------------------------------------------------------------------------------


async def test_a_schedule_s_actions_are_run_through_a_subscription_it_keeps(
    service: UDLService,
) -> None:
    await action(service)

    saved = await set_schedule(service, actions=["morning-digest"])

    linked = (await subscriptions(service))["schedule:digest"]
    assert linked.events == ["schedule.fired"]
    assert linked.filter == Condition(field="schedule", op=Op.EQ, value="digest")
    assert linked.actions == ["morning-digest"]
    assert saved.subscription is not None
    assert saved.subscription.name == "schedule:digest"
    assert saved.actions == ["morning-digest"]
    assert (await get_schedule(service)).subscription == saved.subscription


async def test_a_schedule_firing_runs_its_actions(service: UDLService) -> None:
    await action(service)
    await set_schedule(service, actions=["morning-digest"])
    await set_schedule(service, "other", cron="@hourly")
    [digest] = await service.db.list_records(Schedule, RecordQuery(where={"name": "digest"}))
    [other] = await service.db.list_records(Schedule, RecordQuery(where={"name": "other"}))

    await service.scheduler.fire(digest, T0, now=T0)
    await service.scheduler.fire(other, T0, now=T0)
    await service.bus.dispatch_pending()

    [run] = await service.db.list_records(ActionRun, RecordQuery())
    assert (run.action, run.subscription) == ("morning-digest", "schedule:digest")


async def test_leaving_the_actions_out_keeps_them(service: UDLService) -> None:
    await action(service)
    await set_schedule(service, actions=["morning-digest"])

    saved = await set_schedule(service, cron="@daily")

    assert saved.actions == ["morning-digest"]
    assert "schedule:digest" in await subscriptions(service)


async def test_no_actions_takes_the_subscription_away(service: UDLService) -> None:
    await action(service)
    await set_schedule(service, actions=["morning-digest"])

    saved = await set_schedule(service, actions=[])

    assert (saved.subscription, saved.actions) == (None, [])
    assert "schedule:digest" not in await subscriptions(service)


async def test_changing_the_actions_keeps_the_subscription_s_id(service: UDLService) -> None:
    await action(service)
    await action(service, "other")
    first = await set_schedule(service, actions=["morning-digest"])

    second = await set_schedule(service, actions=["other", "morning-digest"])

    assert first.subscription is not None
    assert second.subscription is not None
    assert second.subscription.id == first.subscription.id
    assert second.actions == ["other", "morning-digest"]


async def test_an_action_that_does_not_exist_is_refused_before_anything_is_saved(
    service: UDLService,
) -> None:
    with pytest.raises(ConfigError, match="nope"):
        await set_schedule(service, actions=["nope"])

    assert await service.db.list_records(Schedule, RecordQuery()) == []


async def test_an_action_s_templates_are_checked_against_schedule_fired(
    service: UDLService,
) -> None:
    await action(service, options={"output": "{{ event.data.nope }}"})

    with pytest.raises(TemplateFailed):
        await set_schedule(service, actions=["morning-digest"])


async def test_deleting_a_schedule_deletes_its_subscription(service: UDLService) -> None:
    await action(service)
    await set_schedule(service, actions=["morning-digest"])

    deleted = await service.automations.delete_schedule(ScheduleRequest(name="digest"))
    again = await service.automations.delete_schedule(ScheduleRequest(name="digest"))

    assert (deleted.deleted, again.deleted) == (True, False)
    assert await service.db.list_records(Schedule, RecordQuery()) == []
    assert await subscriptions(service) == {}


async def test_a_schedule_s_subscription_is_not_set_as_a_subscription(
    service: UDLService,
) -> None:
    await action(service)

    with pytest.raises(AlreadyExists, match="schedule") as raised:
        await service.set_subscription(
            SetSubscriptionRequest(
                name="schedule:digest", events=["schedule.fired"], actions=["morning-digest"]
            )
        )

    assert "udl schedules set" in (raised.value.hint or "")


async def test_a_schedule_s_subscription_is_not_deleted_as_a_subscription(
    service: UDLService,
) -> None:
    await action(service)
    await set_schedule(service, actions=["morning-digest"])

    with pytest.raises(NotAllowed, match="digest schedule") as raised:
        await service.delete_subscription(SubscriptionRequest(name="schedule:digest"))

    assert "udl schedules rm digest" in (raised.value.hint or "")

    assert "schedule:digest" in await subscriptions(service)


async def test_an_action_a_schedule_runs_is_in_use(service: UDLService) -> None:
    await action(service)
    await set_schedule(service, actions=["morning-digest"])

    shown = await service.get_action(ActionRequest(name="morning-digest"))

    assert shown.used_by == ["schedule:digest"]
