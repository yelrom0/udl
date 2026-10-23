"""
-----------------
- test_tools.py -
-----------------

The MCP server's tools: one per operation, whose arguments are the operation's request fields
and whose result is its response (structured), with udl's errors as tool errors an agent can
read and act on
"""

# System Imports
import json
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from mcp import Client
from mcp_types import CallToolResult, TextContent

# Local Imports
from support.engine import script
from support.interfaces import A, B, Harness
from support.mcp import client_for
from udl.core.models import (
    ConfigInfo,
    DatabaseList,
    Deleted,
    DownloadBatch,
    DownloadList,
    JobInfo,
    PluginList,
    ProbeReport,
    SiteInfo,
    SiteList,
)
from udl.core.service import operations
from udl.db.models import JobStatus, Strategy, StrategySource
from udl.interfaces.mcp.server import DEFAULT_WAIT

GONE = "https://example.com/gone"


async def call(client: Client, name: str, /, **arguments: Any) -> dict[str, Any]:
    """The tool's structured result (it must not fail)."""
    result = await client.call_tool(name, arguments)
    assert not result.is_error, text(result)
    assert result.structured_content is not None
    assert json.loads(text(result)) == result.structured_content  # (the same, as text)
    return result.structured_content


def text(result: CallToolResult) -> str:
    [content] = result.content
    assert isinstance(content, TextContent)
    return content.text


async def failure(client: Client, name: str, /, **arguments: Any) -> str:
    """The tool's error, as the agent reads it."""
    result = await client.call_tool(name, arguments)
    assert result.is_error
    return text(result)


# ---------------------------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------------------------


async def test_every_tool_is_an_operation_and_documented_as_one(client: Client) -> None:
    tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    for name, op in operations().items():
        tool = tools[name]
        assert tool.description == op.description
        properties = tool.input_schema.get("properties", {})
        assert set(properties) == set(op.request.model_fields)
        for field, schema in properties.items():
            assert schema.get("description"), f"{name}.{field}"
        required = {f for f, info in op.request.model_fields.items() if info.is_required()}
        assert set(tool.input_schema.get("required", [])) == required
        assert tool.output_schema is not None
        assert tool.annotations is not None
        assert tool.annotations.title


@pytest.mark.parametrize(
    ("name", "read_only", "destructive"),
    [
        ("list_sites", True, False),
        ("probe_url", True, False),
        ("download", False, False),
        ("set_site_strategy", False, False),
        ("delete_site", False, True),
        ("cancel_download", False, True),
    ],
)
async def test_the_tools_say_what_they_change(
    client: Client, name: str, read_only: bool, destructive: bool
) -> None:
    [tool] = [tool for tool in (await client.list_tools()).tools if tool.name == name]

    assert tool.annotations is not None
    assert tool.annotations.read_only_hint is read_only
    assert bool(tool.annotations.destructive_hint) is destructive


# ---------------------------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------------------------


async def test_download_waits_and_returns_what_came_of_each_url(client: Client) -> None:
    batch = DownloadBatch.model_validate(await call(client, "download", urls=[A, GONE]))

    finished, failed = batch.jobs
    assert finished.status is JobStatus.FINISHED
    assert [p.read_bytes() for p in finished.files] == [b"a"]
    assert failed.error is not None
    assert failed.error.code == "no_strategy_found"


async def test_download_waits_no_longer_than_its_timeout_by_default(client: Client) -> None:
    [tool] = [tool for tool in (await client.list_tools()).tools if tool.name == "download"]

    assert tool.input_schema["properties"]["timeout"]["default"] == DEFAULT_WAIT


async def test_download_without_waiting_returns_the_queued_jobs(client: Client) -> None:
    batch = DownloadBatch.model_validate(await call(client, "download", urls=[A], wait=False))

    [job] = batch.jobs
    assert job.status is JobStatus.QUEUED
    shown = JobInfo.model_validate(await call(client, "get_download", id=str(job.id)))
    assert shown.id == job.id


async def test_download_reports_its_progress(client: Client) -> None:
    reported: list[tuple[float, float | None]] = []

    async def report(progress: float, total: float | None, message: str | None) -> None:
        reported.append((progress, total))

    result = await client.call_tool("download", {"urls": [A, B]}, progress_callback=report)

    assert not result.is_error
    assert reported
    assert all(total == 2 for _, total in reported)
    done = [done for done, _ in reported]
    assert done == sorted(set(done))  # (it only ever goes up)
    assert done[-1] == 2


async def test_download_outside_the_allowed_output_roots_is_not_allowed(
    client: Client, tmp_path: Path
) -> None:
    error = await failure(client, "download", urls=[A], output_dir=str(tmp_path.parent / "out"))

    assert "not_allowed" in error
    assert "allowed output roots" in error


async def test_download_passes_the_request_on(client: Client, tmp_path: Path) -> None:
    batch = DownloadBatch.model_validate(
        await call(
            client, "download", urls=[A], tool="http", output_dir=str(tmp_path / "o"), record=False
        )
    )

    [job] = batch.jobs
    assert (job.tool, job.output_dir) == ("http", tmp_path / "o")
    assert DownloadList.model_validate(await call(client, "list_downloads")).downloads == []


async def test_a_bad_download_request_is_a_tool_error(client: Client) -> None:
    assert "urls" in await failure(client, "download", urls=[])
    assert "ftp://example.com" in await failure(client, "download", urls=["ftp://example.com"])
    assert "nope" in await failure(client, "download", urls=[A], tool="nope")


async def test_listing_and_cancelling_downloads(client: Client) -> None:
    [job] = DownloadBatch.model_validate(await call(client, "download", urls=[A])).jobs

    listed = DownloadList.model_validate(
        await call(client, "list_downloads", site_key="example.com")
    )
    cancelled = DownloadList.model_validate(await call(client, "cancel_download", id=str(job.id)))

    assert [shown.id for shown in listed.downloads] == [job.id]
    assert [shown.status for shown in cancelled.downloads] == [JobStatus.FINISHED]


async def test_an_unknown_download_is_a_tool_error_that_says_what_to_do(client: Client) -> None:
    error = await failure(client, "get_download", id="00000000-0000-0000-0000-000000000000")

    assert "does_not_exist" in error
    assert "No download" in error
    assert "history" in error  # (the hint)


# ---------------------------------------------------------------------------------------------
# Probing and sites
# ---------------------------------------------------------------------------------------------


async def test_probe_url(tmp_path: Path) -> None:
    async with client_for(Harness(tmp_path, tools={"yt-dlp": script({A: "video"})})) as client:
        report = ProbeReport.model_validate(await call(client, "probe_url", url=A))

    assert [(r.tool, r.ok) for r in report.results] == [("http", False), ("yt-dlp", True)]


async def test_teaching_udl_a_site(client: Client) -> None:
    strategy = Strategy.model_validate(
        await call(
            client,
            "set_site_strategy",
            site="https://www.example.com/watch",
            tool="http",
            pattern="^/watch",
            options={"confidence": 0.5},
        )
    )
    shown = SiteInfo.model_validate(await call(client, "get_site", site="example.com"))
    listed = SiteList.model_validate(await call(client, "list_sites", search="example"))
    deleted = Deleted.model_validate(await call(client, "delete_site", site="example.com"))

    assert (strategy.site_key, strategy.source, strategy.url_pattern) == (
        "example.com",
        StrategySource.MANUAL,
        "^/watch",
    )
    assert shown.strategies == [strategy]
    assert [site.key for site in listed.sites] == ["example.com"]
    assert deleted.deleted is True
    assert "does_not_exist" in await failure(client, "get_site", site="example.com")


async def test_a_strategy_that_cannot_work_is_a_tool_error(client: Client) -> None:
    assert "plugin_error" in await failure(client, "set_site_strategy", site="a.com", tool="nope")
    error = await failure(
        client, "set_site_strategy", site="a.com", tool="http", options={"nope": 1}
    )
    assert "config_error" in error


# ---------------------------------------------------------------------------------------------
# udl itself
# ---------------------------------------------------------------------------------------------


async def test_udl_itself(client: Client) -> None:
    databases = DatabaseList.model_validate(await call(client, "list_databases"))
    plugins = PluginList.model_validate(await call(client, "list_plugins"))
    config = ConfigInfo.model_validate(await call(client, "get_config"))

    assert [db.name for db in databases.databases] == ["local"]
    assert {plugin.name for plugin in plugins.plugins} >= {"memory", "http"}
    assert config.settings.max_concurrent == 4
