"""
-------------
- events.py -
-------------

/events/stream: what happens, as it happens (server-sent events), for the web UI and any client
that wants to follow events and runs live

/orch/sessions/{session}/stream: one agent session, as it happens (27.9): the same notices, kept
to the session's own, so a client watching a turn does not sift every event udl has.
"""

# System Imports
from collections.abc import AsyncIterable
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

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


@router.get(
    "/orch/sessions/{session}/stream",
    response_class=EventSourceResponse,
    operation_id="orch_session_stream",
    summary="One agent's session, as it happens.",
    description="Server-sent events, from now on, of one session's own: `event` (an EventInfo) "
    "for each `orch.session.*` event it published — that it started, each thing its runner said, "
    "and that it failed or ended. A limit is `orch.agent.limited` (the agent's, not the "
    "session's), so it is not on this stream: the session's state and `limited_until` are. Live "
    "only: a client that reconnects reloads the transcript (`orch_get_session`) rather than "
    "resuming.",
)
async def orch_session_stream(service: ServiceDep, session: UUID) -> AsyncIterable[ServerSentEvent]:
    async with aclosing(service.bus.notices(types="orch.session.*", runs=False)) as notices:
        async for notice in notices:
            if notice.event is None or notice.event.data.get("session") != str(session):
                continue
            yield _sent(notice)


def _sent(notice: BusNotice) -> ServerSentEvent:
    """A notice, as the server-sent event it is."""
    if notice.event is not None:
        return ServerSentEvent(event="event", data=EventInfo.of(notice.event))
    if notice.run is not None:
        return ServerSentEvent(event="run", data=RunInfo.of(notice.run))
    return ServerSentEvent(event=NoticeKind.LAGGED, data={"lagged": True})
