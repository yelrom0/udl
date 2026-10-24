"""
-------------
- events.py -
-------------

/events/stream: what happens, as it happens (server-sent events), for the web UI and any client
that wants to follow events and runs live
"""

# System Imports
from collections.abc import AsyncIterable
from contextlib import aclosing
from typing import Annotated

# Package Imports
from fastapi import APIRouter, Query
from fastapi.sse import EventSourceResponse, ServerSentEvent

# Local Imports
from udl.automations.bus import BusNotice, NoticeKind
from udl.automations.models import EventInfo, RunInfo
from udl.interfaces.api.deps import ServiceDep

router = APIRouter(tags=["events"])


@router.get(
    "/events/stream",
    response_class=EventSourceResponse,
    operation_id="events_stream",
    summary="What happens, as it happens: events and runs.",
    description="Server-sent events, from now on: `event` (an EventInfo) for each event as it "
    "is stored and as it is dispatched, `run` (a RunInfo) for each change to a run, and "
    "`lagged` when this client fell behind and missed some (reload what it shows). A comment "
    "every 15 seconds keeps the connection open. The stream is live only: a client that "
    "reconnects reloads its lists rather than resuming.",
)
async def events_stream(
    service: ServiceDep,
    types: Annotated[
        str, Query(description="Only events whose type this glob matches (e.g. gmail.*).")
    ] = "*",
    runs: Annotated[bool, Query(description="Send the runs' changes too.")] = True,
) -> AsyncIterable[ServerSentEvent]:
    async with aclosing(service.bus.notices(types=types, runs=runs)) as notices:
        async for notice in notices:
            yield _sent(notice)


def _sent(notice: BusNotice) -> ServerSentEvent:
    """A notice, as the server-sent event it is."""
    if notice.event is not None:
        return ServerSentEvent(event="event", data=EventInfo.of(notice.event))
    if notice.run is not None:
        return ServerSentEvent(event="run", data=RunInfo.of(notice.run))
    return ServerSentEvent(event=NoticeKind.LAGGED, data={"lagged": True})
