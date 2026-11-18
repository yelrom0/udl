"""
-----------
- apps.py -
-----------

FakeApp: an app plugin (the udl.apps kind) whose service ticks on the clock, adds two operations
(one of them saying how it is shown), publishes an event type, and keeps a record of its own, so
the app contract, the runtime's app tests and the interface tests have one app to run against
"""

# System Imports
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

# Package Imports
from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.automations.events import EventType
from udl.clock import Clock, pause
from udl.config import Duration
from udl.core.operations import Operation, OperationHints, UISpec
from udl.db.models import utcnow
from udl.db.records import Record
from udl.plugins.apps.base import AppContext, AppPlugin, AppSettings
from udl.plugins.requirements import Requirement


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class Tick(_Model):
    """One tick of a fake app."""

    at: datetime
    """When it ticked."""
    count: int
    """Which tick it was (from zero)."""


class TickRequest(_Model):
    """Ask for the ticks a fake app has done."""

    page: str | None = None
    """Which page (as the last answer's next_tick)."""
    limit: int = 10
    """How many at most."""


class Ticks(_Model):
    """The ticks a fake app has done."""

    items: list[Tick] = Field(default_factory=list[Tick])
    """The ticks, oldest first."""
    next_tick: str | None = None
    """What gives the next page of them, if there is one."""


class Ticked(_Model):
    """What a fake app answers when it ticks once."""

    at: datetime
    """When it ticked."""
    count: int
    """Which tick it was."""


class FakeAppSettings(AppSettings):
    """A fake app's settings."""

    every: Duration = timedelta(seconds=1)
    """How often it ticks."""
    loud: bool = False
    """Whether it counts what it said (what a loud app would print)."""


class TickRecord(Record):
    """A tick a fake app kept (one record kind, so the record contract can run over it)."""

    kind = "fake_tick"
    indexed = ("count",)

    at: datetime
    """When it ticked."""
    count: int
    """Which tick it was."""

    @classmethod
    def example(cls) -> TickRecord:
        return cls(at=utcnow(), count=1)


class FakeApp(AppPlugin[FakeAppSettings]):
    """A fake app: a service that ticks on the clock, and two operations over what it did."""

    name = "fakeapp"
    title = "Fake app"
    summary = "A fake app (for tests)."
    settings_model = FakeAppSettings
    requires = (Requirement.binary("sh"),)

    def __init__(self, settings: FakeAppSettings, *, clock: Clock) -> None:
        super().__init__(settings, clock=clock)
        self.done: list[Tick] = []
        """The ticks it has done, oldest first."""
        self.stopped = False
        """Whether its service was told to stop and did."""
        self.started = 0
        """How many times its service was started (the runtime's restarts)."""
        self.said: list[str] = []
        """What a loud app would have printed."""

    @classmethod
    def event_types(cls) -> Sequence[EventType[Any]]:
        return (
            EventType(
                name=f"{cls.name}.tick",
                data=Tick,
                summary="A fake app ticked.",
                example=Tick(at=utcnow(), count=1),
                subject=lambda data: f"Tick: {data.count}",
            ),
        )

    @classmethod
    def record_kinds(cls) -> Sequence[type[Record]]:
        return (TickRecord,)

    async def serve(self, ctx: AppContext) -> None:
        """Tick on the clock until ctx.stop is set, publishing and keeping each tick."""
        self.started += 1
        while not ctx.stop.is_set():
            tick = Tick(at=ctx.clock.now(), count=len(self.done))
            await ctx.events.emit(
                f"{self.name}.tick",
                tick,
                dedupe_key=f"tick-{tick.count}",
                occurred_at=tick.at,
            )
            await ctx.records.save(TickRecord(at=tick.at, count=tick.count))
            self.done.append(tick)
            if self.settings.loud:
                self.said.append(str(tick.count))
            await pause(ctx.clock, self.settings.every, ctx.stop)
        self.stopped = True

    # -- operations ----------------------------------------------------------------------------

    @classmethod
    def operations(cls) -> list[Operation]:
        return [
            cls.operation(
                "ticks",
                title="List the ticks",
                hints=OperationHints(read_only=True),
                ui=UISpec(view="list"),
            ),
            cls.operation("tick", title="Tick once"),
        ]

    async def ticks(self, request: TickRequest) -> Ticks:
        """List the ticks this fake app has done, a page at a time."""
        start = int(request.page or 0)
        end = start + request.limit
        return Ticks(
            items=self.done[start:end],
            next_tick=str(end) if end < len(self.done) else None,
        )

    async def tick(self, request: TickRequest) -> Ticked:
        """Make the fake app tick once now."""
        tick = Tick(at=self.clock.now(), count=len(self.done))
        self.done.append(tick)
        return Ticked(at=tick.at, count=tick.count)
