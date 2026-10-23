"""
------------------
- test_chains.py -
------------------

The MCP server's tag chain tools: how an AI agent teaches udl a site no downloader handles
(suggest_chain, test_chain, set_site_chain), and moves chains about (export_chain, import_chains)
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
from support.downloaders import FakeMedia
from support.engine import script
from support.interfaces import Harness
from udl.chains.models import FetchMode, TagChain
from udl.config import APISettings
from udl.core.models import (
    ChainFile,
    ChainReport,
    ChainSuggestions,
    DownloadBatch,
    ImportedChains,
)
from udl.db.models import JobStatus, Strategy, StrategySource
from udl.interfaces.mcp.server import INSTRUCTIONS

PAGE = "https://example.com/watch/1"
MEDIA = "https://cdn.example.com/v.mp4"


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(
        tmp_path,
        api=APISettings(allowed_output_roots=[tmp_path]),
        tools={
            "scraper": script(
                suggested={PAGE: {"video#clip@src": MEDIA}},
                chained={PAGE: FakeMedia(content=b"the video", title="A film")},
            ),
            "http": script({MEDIA: FakeMedia(content=b"x" * 10), PAGE: "x"}),
        },
    )


def text(result: CallToolResult) -> str:
    [content] = result.content
    assert isinstance(content, TextContent)
    return content.text


async def call(client: Client, name: str, /, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(name, arguments)
    assert not result.is_error, text(result)
    assert result.structured_content is not None
    assert json.loads(text(result)) == result.structured_content
    return result.structured_content


async def test_an_agent_teaches_udl_a_site_with_a_chain(client: Client) -> None:
    found = ChainSuggestions.model_validate(await call(client, "suggest_chain", url=PAGE))
    [suggestion] = found.suggestions

    report = ChainReport.model_validate(
        await call(client, "test_chain", url=PAGE, chain=suggestion.shorthand)
    )
    saved = Strategy.model_validate(
        await call(client, "set_site_chain", site=PAGE, chain=suggestion.shorthand)
    )
    batch = DownloadBatch.model_validate(await call(client, "download", urls=[PAGE]))

    assert (suggestion.shorthand, suggestion.media.size_bytes) == ("video#clip@src", 10)
    assert report.ok
    assert (saved.tool, saved.source) == ("scraper", StrategySource.MANUAL)
    [job] = batch.jobs
    assert (job.status, job.strategy_id) == (JobStatus.FINISHED, saved.id)
    assert [path.read_bytes() for path in job.files] == [b"the video"]


async def test_a_chain_can_be_given_as_a_chain(client: Client) -> None:
    chain = {"fetch": "browser", "steps": [{"select": "video", "attr": "src"}]}

    report = ChainReport.model_validate(await call(client, "test_chain", url=PAGE, chain=chain))

    assert report.chain == TagChain.model_validate({**chain})
    assert report.chain.fetch is FetchMode.BROWSER


async def test_a_chain_that_is_not_one_is_a_tool_error(client: Client) -> None:
    result = await client.call_tool("test_chain", {"url": PAGE, "chain": "video source"})

    assert result.is_error
    assert "end the select step with @attr" in text(result)


async def test_chains_are_exported_and_imported_as_yaml(client: Client) -> None:
    await call(client, "set_site_chain", site="example.com", chain="video@src", pattern="^/a/")

    exported = ChainFile.model_validate(await call(client, "export_chain", site="example.com"))
    imported = ImportedChains.model_validate(
        await call(client, "import_chains", yaml=exported.yaml.replace("example.com", "other.org"))
    )

    [strategy] = imported.strategies
    assert (strategy.site_key, strategy.url_pattern, strategy.source) == (
        "other.org",
        "^/a/",
        StrategySource.IMPORT,
    )


async def test_exporting_a_site_without_chains_is_a_tool_error(client: Client) -> None:
    result = await client.call_tool("export_chain", {"site": "example.com"})

    assert result.is_error
    assert "does_not_exist" in text(result)
    assert "suggest_chain" in text(result)


def test_the_instructions_tell_an_agent_about_chains() -> None:
    for tool in ("suggest_chain", "test_chain", "set_site_chain"):
        assert f"`{tool}`" in INSTRUCTIONS
