"""
------------------
- test_system.py -
------------------

GET /databases, POST /databases/sync, GET /plugins, GET /config
"""

# System Imports
from pathlib import Path

# Package Imports
import httpx
import pytest

# Local Imports
from support.api import client_for
from support.engine import connection
from support.interfaces import Harness
from udl.core.models import DatabaseList, PluginList, SyncReport
from udl.db.models import Site
from udl.db.sync import SyncCounts
from udl.errors import ErrorCode, ErrorInfo


async def test_listing_the_databases(tmp_path: Path) -> None:
    shared = connection("shared", priority=10, read_only=True)
    shared = shared.model_copy(update={"url": "memory://udl:s3cret@nas/udl"})
    harness = Harness(tmp_path, connections=[connection("local", primary=True), shared])
    async with client_for(harness) as client:
        response = await client.get("/databases")

    listed = DatabaseList.model_validate(response.json())
    assert [(db.name, db.url, db.connected) for db in listed.databases] == [
        ("local", "memory://", True),
        ("shared", "memory://udl:***@nas/udl", True),
    ]


def two_databases(tmp_path: Path, **shared: object) -> Harness:
    return Harness(
        tmp_path,
        connections=[
            connection("local", primary=True),
            connection("shared", priority=10).model_copy(update=shared),
        ],
    )


async def test_syncing_the_databases(tmp_path: Path) -> None:
    harness = two_databases(tmp_path)
    async with client_for(harness) as client:
        await harness.db.database("local").upsert_site(Site(key="example.com"))
        response = await client.post(
            "/databases/sync", json={"source": "local", "targets": ["shared"]}
        )
        shared = await harness.db.database("shared").get_site("example.com")

    assert response.status_code == 200, response.text
    report = SyncReport.model_validate(response.json())
    assert report.targets[0].sites == SyncCounts(added=1)
    assert shared is not None


@pytest.mark.parametrize(
    ("request_", "status", "code"),
    [
        ({"source": "local", "targets": ["shared"]}, 403, ErrorCode.NOT_ALLOWED),
        ({"source": "local", "targets": ["nas"]}, 404, ErrorCode.DOES_NOT_EXIST),
    ],
)
async def test_a_sync_that_cannot_be_done_is_an_error(
    tmp_path: Path, request_: dict[str, object], status: int, code: ErrorCode
) -> None:
    async with client_for(two_databases(tmp_path, read_only=True)) as client:
        response = await client.post("/databases/sync", json=request_)

    assert response.status_code == status
    assert ErrorInfo.model_validate(response.json()).code is code


async def test_a_sync_into_its_own_source_is_invalid(client: httpx.AsyncClient) -> None:
    response = await client.post("/databases/sync", json={"source": "local", "targets": ["local"]})

    assert response.status_code == 422


async def test_listing_the_plugins(client: httpx.AsyncClient) -> None:
    listed = PluginList.model_validate((await client.get("/plugins")).json())

    assert {plugin.name for plugin in listed.plugins} >= {"memory", "http", "browser"}


async def test_the_config_is_shown_with_its_secrets_hidden(tmp_path: Path) -> None:
    harness = Harness(tmp_path, tools={"yt-dlp": {"params": {"password": "pw-secret"}}})
    async with client_for(harness) as client:
        response = await client.get("/config")

    assert response.status_code == 200
    assert "pw-secret" not in response.text
    params = response.json()["settings"]["tools"]["yt-dlp"]["params"]
    assert params == {"password": "***"}
