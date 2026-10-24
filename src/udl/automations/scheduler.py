"""
----------------
- scheduler.py -
----------------

Scheduler: what fires the schedules (udl.automations.schedules), in the automations' runtime.
It is one loop, not a job per schedule: it reads the enabled schedules (there are few), sleeps
until the earliest is due (its next time plus its jitter), and fires it: it publishes
schedule.fired (keyed by the schedule and the time it was for, so a time fired twice, as by a
udl that stopped before saving what came next, is stored once), then saves when it fired and
its next time. It sleeps a minute at most, so it sees what another udl saved within a minute,
and wakes at once when this udl saves a schedule (changed()).

A time it finds more than LATE_AFTER late was missed: udl was stopped then (or the machine was
asleep), and the schedule's misfire policy says what to do (due_after_downtime()). When it
starts, it plans again the next times still to come, in case the time zone they are in (or its
rules) changed while it was stopped.
"""

# System Imports
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Final, cast

# Local Imports
from udl.automations.bus import EventBus, NewEvent
from udl.automations.events import EventRecord, ScheduleFiredData
from udl.automations.schedules import Schedule, due_after_downtime
from udl.clock import Clock, pause
from udl.config import AutomationsSettings
from udl.db.manager import DBManager
from udl.db.records import RecordQuery

log = logging.getLogger(__name__)

FIRED: Final = "schedule.fired"
"""The event type a schedule fires."""
SOURCE: Final = "scheduler"
"""What publishes the events schedules fire (as their source)."""
RECHECK: Final = timedelta(minutes=1)
"""The longest the scheduler sleeps before it reads the schedules again."""
LATE_AFTER: Final = timedelta(minutes=1)
"""How late the scheduler may get to a time and still fire it as usual: later, it was missed,
and the schedule's misfire policy says what to do."""


def dedupe_key(schedule: Schedule, scheduled_for: datetime) -> str:
    """What makes a schedule's event for a time the same event, however often it fires."""
    return f"schedule:{schedule.id}:{scheduled_for.isoformat()}"


class Scheduler:
    """Fires the schedules in `db` as they come due (on `clock`), publishing on `bus`."""

    def __init__(
        self,
        db: DBManager,
        bus: EventBus,
        clock: Clock,
        settings: AutomationsSettings,
        *,
        recheck: timedelta = RECHECK,
    ) -> None:
        self.db = db
        self.bus = bus
        self.clock = clock
        self.settings = settings
        self.recheck = recheck
        self._changed = asyncio.Event()

    def changed(self) -> None:
        """Read the schedules again now (this udl saved one)."""
        self._changed.set()

    async def serve(self, stop: asyncio.Event) -> None:
        """Fire the schedules as they come due, until `stop` is set."""
        starting = True
        while not stop.is_set():
            self._changed.clear()
            now = self.clock.now()
            schedules = [
                await self._planned(schedule, now, again=starting)
                for schedule in await self.db.list_records(
                    Schedule, RecordQuery(where={"enabled": True})
                )
            ]
            starting = False
            due = [
                (schedule, time, at)
                for schedule in schedules
                if (time := schedule.next_run_at) is not None
                and (at := schedule.due_at()) is not None
                and at <= now
            ]
            for schedule, time, at in due:
                await self._run(schedule, time, at, now)
            if due:
                continue
            soonest = [at - now for schedule in schedules if (at := schedule.due_at()) is not None]
            await pause(self.clock, min([self.recheck, *soonest]), self._changed, stop)

    async def fire(
        self,
        schedule: Schedule,
        scheduled_for: datetime,
        *,
        now: datetime,
        late: bool = False,
        manual: bool = False,
        source: str = SOURCE,
    ) -> EventRecord | None:
        """Publish `schedule`'s schedule.fired for the time `scheduled_for` (one run by hand,
        `manual`, is never one seen before). Returns it; None when it was published before."""
        data = ScheduleFiredData(
            schedule=schedule.name,
            scheduled_for=scheduled_for,
            fired_at=now,
            late=late,
            manual=manual,
            data=schedule.data,
        )
        return await self.bus.publish(
            NewEvent(
                type=FIRED,
                data=data.model_dump(mode="json"),
                source=source,
                occurred_at=now,
                dedupe_key=None if manual else dedupe_key(schedule, scheduled_for),
            )
        )

    async def run_now(self, schedule: Schedule, *, source: str) -> EventRecord:
        """Publish `schedule`'s schedule.fired now, run by hand (by `source`), leaving its next
        time as it is."""
        now = self.clock.now()
        fired = await self.fire(schedule, now, now=now, manual=True, source=source)
        return cast(EventRecord, fired)  # (one run by hand is never one seen before)

    async def _planned(self, schedule: Schedule, now: datetime, *, again: bool) -> Schedule:
        """`schedule`, with a next time when it has none (and, `again`, its next time planned
        again when that is still to come)."""
        if schedule.next_run_at is not None and not (again and schedule.next_run_at > now):
            return schedule
        planned = schedule.planned(now, schedule.zone(self.settings.timezone))
        if planned.next_run_at == schedule.next_run_at and planned.enabled:
            return schedule
        return await self.db.upsert_record(planned)

    async def _run(
        self, schedule: Schedule, scheduled_for: datetime, due: datetime, now: datetime
    ) -> None:
        """Fire `schedule` for its next time, `scheduled_for` (`due` with its jitter, by
        `now`), or, when that was missed, do what its misfire policy says; then plan its next
        time."""
        if now - due <= LATE_AFTER:
            log.info("The %s schedule fires (for %s)", schedule.name, scheduled_for)
            await self.fire(schedule, scheduled_for, now=now)
            await self._advance(schedule, after=scheduled_for, fired_at=now)
            return
        missed = due_after_downtime(schedule, now, schedule.zone(self.settings.timezone))
        if missed.fire is None:
            log.info("The %s schedule skips the time it missed (%s)", schedule.name, due)
        else:
            log.info("The %s schedule fires late (for %s)", schedule.name, missed.fire)
            await self.fire(schedule, missed.fire, now=now, late=True)
        await self._advance(schedule, after=now, fired_at=now if missed.fire else None)

    async def _advance(
        self, schedule: Schedule, *, after: datetime, fired_at: datetime | None
    ) -> None:
        """Save `schedule`'s next time after `after` (and when it fired), as it is stored now:
        a schedule changed meanwhile keeps its changes, and a deleted one stays deleted."""
        stored = await self.db.get_record(Schedule, schedule.id)
        if stored is None:
            return
        planned = stored.planned(after, stored.zone(self.settings.timezone))
        if fired_at is not None:
            planned = planned.model_copy(update={"last_run_at": fired_at})
        await self.db.upsert_record(planned)
