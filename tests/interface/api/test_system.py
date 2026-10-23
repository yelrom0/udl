"""
------------------
- test_system.py -
------------------

GET /databases, GET /plugins, GET /config
"""

# System Imports
from pathlib import Path

# Package Imports
import httpx

# Local Imports
from support.api import client_for
from support.engine import connection
from support.interfaces import Harness
from udl.core.models import DatabaseList, PluginList


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
