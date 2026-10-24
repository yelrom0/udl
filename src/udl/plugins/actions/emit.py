"""
-----------
- emit.py -
-----------

The emit action: publishes an event of your own (custom.<name>), with data made from the event
that ran it. What it publishes is caused by its run and one deeper than its event, so actions
that emit what they subscribe to stop at [automations] max_event_depth.
"""

# Package Imports
from pydantic import Field, field_validator

# Local Imports
from udl.automations.bus import NewEvent
from udl.automations.events import CUSTOM_PREFIX, EventTypes
from udl.db.models import JsonObject
from udl.errors import ActionFailed, UDLError
from udl.plugins.actions.base import ActionContext, ActionOptions, ActionOutput, ActionPlugin


class EmitOptions(ActionOptions):
    type: str
    """The event's type: custom.<name>."""
    data: JsonObject = Field(default_factory=dict)
    """Its data (texts in it are templates, rendered with the event that ran the action)."""
    dedupe_key: str | None = None
    """What makes it the same event as another of its type, so it is published once (e.g.
    {{ event.id }})."""

    @field_validator("type")
    @classmethod
    def _custom(cls, named: str) -> str:
        if not named.startswith(CUSTOM_PREFIX) or EventTypes().find(named) is None:
            raise ValueError(f"{named!r} is not an event of your own: custom.<name>")
        return named


class EmitAction(ActionPlugin[EmitOptions]):
    """Publishes an event of your own."""

    name = "emit"
    options_model = EmitOptions
    summary = "Publishes an event of your own (custom.<name>), for other subscriptions to match."

    async def run(self, ctx: ActionContext[EmitOptions]) -> ActionOutput:
        options = ctx.options
        new = NewEvent(
            type=options.type,
            data=options.data,
            source="udl",
            dedupe_key=options.dedupe_key,
            caused_by=ctx.run_id,
            depth=ctx.depth + 1,
        )
        try:
            stored = await self.service.bus.publish(new)
        except UDLError as e:
            raise ActionFailed(f"Could not emit {options.type}: {e.message}", hint=e.hint) from e
        return ActionOutput(
            output={
                "event": None if stored is None else str(stored.id),
                "duplicate": stored is None,
            },
            summary=f"Emitted {options.type}",
        )
