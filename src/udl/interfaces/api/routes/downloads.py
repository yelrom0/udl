"""
----------------
- downloads.py -
----------------

/downloads: download URLs, follow and cancel the downloads, and the history
"""

# System Imports
from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import aclosing
from typing import Annotated
from uuid import UUID

# Package Imports
from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent

# Local Imports
from udl.core.models import (
    DownloadBatch,
    DownloadList,
    DownloadRequest,
    DownloadSpec,
    JobEvent,
    JobInfo,
    JobRequest,
)
from udl.db.models import DownloadQuery
from udl.errors import ErrorInfo
from udl.interfaces.api.deps import ServiceDep, documented
from udl.interfaces.security import check_output_dir

router = APIRouter(prefix="/downloads", tags=["downloads"])

JobId = Annotated[
    UUID, Path(alias="id", description="A download job's id (or, to cancel, a batch's).")
]


@router.post("", status_code=202, **documented("download", 400, 403, 404))
async def download(
    spec: DownloadSpec,
    service: ServiceDep,
    response: Response,
    wait: Annotated[
        bool,
        Query(description="Answer once every download has ended (200), not at once (202)."),
    ] = False,
    seconds: Annotated[
        float | None,
        Query(
            alias="timeout",
            gt=0,
            description="With wait: answer after this many seconds at most (202).",
        ),
    ] = None,
) -> DownloadBatch:
    check_output_dir(service.settings, spec.output_dir)
    request = DownloadRequest.model_validate(
        {**spec.model_dump(), "wait": wait, "timeout": seconds}
    )
    batch = await service.download(request)
    if all(job.status.ended for job in batch.jobs):
        response.status_code = 200
    return batch


@router.get("", **documented("list_downloads"))
async def list_downloads(
    query: Annotated[DownloadQuery, Query()], service: ServiceDep
) -> DownloadList:
    return await service.list_downloads(query)


@router.get("/{id}", **documented("get_download", 404))
async def get_download(job_id: JobId, service: ServiceDep) -> JobInfo:
    return await service.get_download(JobRequest(id=job_id))


@router.delete("/{id}", **documented("cancel_download", 404))
async def cancel_download(job_id: JobId, service: ServiceDep) -> DownloadList:
    return await service.cancel_download(JobRequest(id=job_id))


async def _job_events(job_id: JobId, service: ServiceDep) -> AsyncGenerator[JobEvent]:
    # (A dependency, so an unknown id is a 404 before the stream starts.)
    return service.events(job_id)


@router.get(
    "/{id}/events",
    response_class=EventSourceResponse,
    operation_id="download_events",
    summary="What happens to a download, or to every download of a batch, as it happens.",
    description="Server-sent events, one per JobEvent (its type is the event's name): first "
    "what has happened so far (the latest progress only), then each event as it happens, "
    "until every download has ended.",
    responses={404: {"model": ErrorInfo}},
)
async def download_events(
    events: Annotated[AsyncGenerator[JobEvent], Depends(_job_events)],
) -> AsyncIterable[ServerSentEvent]:
    async with aclosing(events):
        async for event in events:
            yield ServerSentEvent(data=event, event=event.type)
