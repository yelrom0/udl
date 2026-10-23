"""
-----------------------
- test_interfaces.py -
-----------------------

The three interfaces are one udl: the same probe of a fixture site page, through the CLI, the
API and the MCP server (each on its own service, with udl's own downloaders), gives the same
report
"""

# System Imports
import asyncio
import json
from pathlib import Path
from typing import Any

# Package Imports
import httpx
from mcp import Client
from typer.testing import CliRunner

# Local Imports
from support.engine import connection
from support.fixture_site import FixtureSite, Page
from udl.config import APISettings, BrowserSettings, DBSettings, Settings
from udl.core.jobs import InstanceLock
from udl.core.service import UDLService
from udl.interfaces.api.app import create_app
from udl.interfaces.cli.app import app
from udl.interfaces.mcp.server import create_server


async def test_every_interface_probes_alike(tmp_path: Path, fixture_site: FixtureSite) -> None:
    settings = Settings(
        download_dir=tmp_path / "downloads",
        probe_order=["http", "yt-dlp", "gallery-dl", "scraper"],
        db=DBSettings(connections=[connection(primary=True)]),
        browser=BrowserSettings(profile_dir=tmp_path / "profile"),
        api=APISettings(allowed_output_roots=[tmp_path]),
    )

    def service(_: Settings | None = None) -> UDLService:
        return UDLService(settings, lock=InstanceLock(tmp_path / "udl.lock"))

    url = fixture_site.url(Page.VIDEO)
    request = {"url": url, "all_tools": True}

    # (The CLI runs its own event loop, so in a thread of its own.)
    cli = await asyncio.to_thread(
        CliRunner().invoke, app, ["probe", url, "--all", "--json"], obj=service
    )
    assert cli.exit_code == 0, cli.output
    by_cli: dict[str, Any] = json.loads(cli.stdout)

    api = create_app(settings, make_service=service)
    async with (
        api.router.lifespan_context(api),
        httpx.AsyncClient(transport=httpx.ASGITransport(api), base_url="http://127.0.0.1") as http,
    ):
        response = await http.post("/probe", json=request)
    by_api: dict[str, Any] = response.json()

    async with service() as mcp_service, Client(create_server(lambda: mcp_service)) as client:
        result = await client.call_tool("probe_url", request)
    by_mcp = result.structured_content

    assert by_cli == by_api == by_mcp
    assert [(r["tool"], r["ok"]) for r in by_cli["results"]] == [
        ("http", False),
        ("yt-dlp", True),
        ("gallery-dl", False),
        ("scraper", True),
    ]
