"""
---------------------
- test_downloads.py -
---------------------

POST /downloads, GET /downloads, GET and DELETE /downloads/{id}, and a job's events as
server-sent events (GET /downloads/{id}/events)
"""

# System Imports
import uuid
from pathlib import Path

# Package Imports
import httpx

# Local Imports
from support.api import client_for
from support.engine import connection, script
from support.interfaces import SLOW, A, B, Harness
from udl.config import APISettings
from udl.core.models import DownloadBatch, DownloadList, JobEvent, JobEventType, JobInfo
from udl.db.models import DownloadQuery, JobStatus
from udl.errors import ErrorCode, ErrorInfo

GONE = "https://example.com/gone"


def slow(tmp_path: Path) -> Harness:
    return Harness(
        tmp_path,
        probe_order=["http"],
        tools={"http": {**script({A: SLOW, B: SLOW}), "chunk_size": 20}},
        api=APISettings(allowed_output_roots=[tmp_path]),
    )


def error(response: httpx.Response) -> ErrorInfo:
    return ErrorInfo.model_validate(response.json())


async def test_posting_downloads_queues_them(client: httpx.AsyncClient) -> None:
    response = await client.post("/downloads", json={"urls": [A, B]})

    assert response.status_code == 202
    batch = DownloadBatch.model_validate(response.json())
    assert [job.status for job in batch.jobs] == [JobStatus.QUEUED] * 2


async def test_posting_downloads_with_wait_returns_what_came_of_them(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/downloads", params={"wait": True}, json={"urls": [A, GONE]})

    assert response.status_code == 200
    finished, failed = DownloadBatch.model_validate(response.json()).jobs
    assert [p.read_bytes() for p in finished.files] == [b"a"]
    assert failed.error is not None
    assert failed.error.code is ErrorCode.NO_STRATEGY_FOUND


async def test_waiting_gives_up_after_the_timeout(tmp_path: Path) -> None:
    async with client_for(slow(tmp_path)) as client:
        response = await client.post(
            "/downloads", params={"wait": True, "timeout": 0.05}, json={"urls": [A]}
        )

        assert response.status_code == 202
        [job] = DownloadBatch.model_validate(response.json()).jobs
        assert job.status is JobStatus.RUNNING


async def test_a_download_passes_the_request_on(client: httpx.AsyncClient, tmp_path: Path) -> None:
    response = await client.post(
        "/downloads",
        params={"wait": True},
        json={"urls": [A], "tool": "http", "output_dir": str(tmp_path / "out"), "record": False},
    )

    [job] = DownloadBatch.model_validate(response.json()).jobs
    assert (job.tool, job.output_dir) == ("http", tmp_path / "out")
    assert (await client.get("/downloads")).json() == {"downloads": []}


async def test_a_download_outside_the_allowed_output_roots_is_forbidden(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    response = await client.post(
        "/downloads", json={"urls": [A], "output_dir": str(tmp_path.parent / "elsewhere")}
    )

    assert response.status_code == 403
    assert error(response).code is ErrorCode.NOT_ALLOWED
    assert (await client.get("/downloads")).json() == {"downloads": []}


async def test_a_bad_download_request_is_unprocessable(client: httpx.AsyncClient) -> None:
    bodies: list[dict[str, object]] = [
        {"urls": []},
        {"urls": ["ftp://example.com"]},
        {"urls": [A], "nope": 1},
    ]
    for body in bodies:
        response = await client.post("/downloads", json=body)

        assert response.status_code == 422, body


async def test_a_download_with_an_unknown_tool_is_a_bad_request(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/downloads", json={"urls": [A], "tool": "nope"})

    assert response.status_code == 400
    assert error(response).code is ErrorCode.PLUGIN_ERROR


async def test_a_download_with_an_unknown_database_is_not_found(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/downloads", json={"urls": [A], "databases": ["nope"]})

    assert response.status_code == 404
    assert error(response).hint is not None


async def test_a_download_can_be_kept_to_some_databases(tmp_path: Path) -> None:
    harness = Harness(
        tmp_path, connections=[connection("local", primary=True), connection("other", priority=1)]
    )
    async with client_for(harness) as client:
        await client.post(
            "/downloads", params={"wait": True}, json={"urls": [A], "databases": ["other"]}
        )

        listed = DownloadList.model_validate((await client.get("/downloads")).json())

    assert len(listed.downloads) == 1
    assert len(await harness.db.database("other").list_downloads(DownloadQuery())) == 1
    assert await harness.db.database("local").list_downloads(DownloadQuery()) == []


async def test_getting_a_download(client: httpx.AsyncClient) -> None:
    posted = await client.post("/downloads", params={"wait": True}, json={"urls": [A]})
    [job] = DownloadBatch.model_validate(posted.json()).jobs

    response = await client.get(f"/downloads/{job.id}")

    assert response.status_code == 200
    shown = JobInfo.model_validate(response.json())
    assert (shown.id, shown.status) == (job.id, JobStatus.FINISHED)


async def test_getting_an_unknown_download_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/downloads/{uuid.uuid4()}")

    assert response.status_code == 404
    assert error(response).code is ErrorCode.DOES_NOT_EXIST
    assert (await client.get("/downloads/nope")).status_code == 422


async def test_listing_the_downloads(client: httpx.AsyncClient) -> None:
    other = "https://other.org/c"
    for url in (A, GONE, other):
        await client.post("/downloads", params={"wait": True}, json={"urls": [url]})

    def urls(response: httpx.Response) -> list[str]:
        return [job.url for job in DownloadList.model_validate(response.json()).downloads]

    assert urls(await client.get("/downloads")) == [other, GONE, A]
    assert urls(await client.get("/downloads", params={"site_key": "www.other.org"})) == [other]
    assert urls(await client.get("/downloads", params={"status": "finished", "limit": 1})) == [A]
    assert urls(await client.get("/downloads", params={"offset": 2})) == [A]
    assert (await client.get("/downloads", params={"status": "nope"})).status_code == 422
    assert (await client.get("/downloads", params={"nope": 1})).status_code == 422


async def test_deleting_a_download_cancels_it(tmp_path: Path) -> None:
    async with client_for(slow(tmp_path)) as client:
        posted = await client.post("/downloads", json={"urls": [A, B]})
        batch = DownloadBatch.model_validate(posted.json())

        response = await client.delete(f"/downloads/{batch.id}")

        assert response.status_code == 200
        cancelled = DownloadList.model_validate(response.json())
        assert [job.status for job in cancelled.downloads] == [JobStatus.CANCELLED] * 2


async def test_deleting_an_unknown_download_is_not_found(client: httpx.AsyncClient) -> None:
    assert (await client.delete(f"/downloads/{uuid.uuid4()}")).status_code == 404


async def test_a_download_s_events_are_streamed(client: httpx.AsyncClient) -> None:
    posted = await client.post("/downloads", json={"urls": [A]})
    [job] = DownloadBatch.model_validate(posted.json()).jobs

    response = await client.get(f"/downloads/{job.id}/events")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        (fields["event"], JobEvent.model_validate_json(fields["data"]))
        for fields in sse(response.text)
    ]
    assert [name for name, _ in events] == [event.type for _, event in events]
    assert events[0][1].type is JobEventType.QUEUED
    assert events[-1][1].type is JobEventType.FINISHED
    assert events[-1][1].job.id == job.id


async def test_a_batch_s_events_are_streamed_too(client: httpx.AsyncClient) -> None:
    posted = await client.post("/downloads", json={"urls": [A, B]})
    batch = DownloadBatch.model_validate(posted.json())

    response = await client.get(f"/downloads/{batch.id}/events")

    ended = [
        JobEvent.model_validate_json(fields["data"]).job.url
        for fields in sse(response.text)
        if fields["event"] in ("finished", "failed", "cancelled")
    ]
    assert sorted(ended) == [A, B]


async def test_the_events_of_an_unknown_download_are_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/downloads/{uuid.uuid4()}/events")

    assert response.status_code == 404
    assert error(response).code is ErrorCode.DOES_NOT_EXIST


def sse(text: str) -> list[dict[str, str]]:
    """The events in a text/event-stream body (comments, such as keep-alives, left out)."""
    events: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":")
        )
        if fields:
            events.append(fields)
    return events
