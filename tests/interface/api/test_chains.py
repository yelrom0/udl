"""
------------------
- test_chains.py -
------------------

POST /chains/suggest, POST /chains/test, PUT and GET /sites/{key}/chain, POST /chains/import
"""

# System Imports
from pathlib import Path

# Package Imports
import httpx
import pytest

# Local Imports
from support.api import client_for
from support.downloaders import FakeMedia
from support.engine import registry, script
from support.interfaces import Harness
from support.tasks import held
from udl.chains.files import read_chains
from udl.chains.models import TagChain
from udl.config import APISettings
from udl.core.models import ChainFile, ChainReport, ChainSuggestions, ImportedChains
from udl.db.models import Strategy, StrategySource
from udl.errors import ErrorCode, ErrorInfo

PAGE = "https://example.com/watch/1"
MEDIA = "https://cdn.example.com/v.mp4"


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(
        tmp_path,
        api=APISettings(allowed_output_roots=[tmp_path]),
        tools={
            "scraper": script(suggested={PAGE: {"video@src": MEDIA}}, chained={PAGE: "x"}),
            "http": script({MEDIA: FakeMedia(content=b"x" * 10)}),
        },
    )


async def test_suggesting_chains(client: httpx.AsyncClient) -> None:
    response = await client.post("/chains/suggest", json={"url": PAGE, "limit": 3})

    assert response.status_code == 200
    found = ChainSuggestions.model_validate(response.json())
    assert [(s.shorthand, s.media.size_bytes) for s in found.suggestions] == [("video@src", 10)]


async def test_testing_a_chain_given_as_its_shorthand_or_as_a_chain(
    client: httpx.AsyncClient,
) -> None:
    shorthand = await client.post(
        "/chains/test", json={"url": PAGE, "chain": "a@href >> video@src"}
    )
    chain = await client.post(
        "/chains/test", json={"url": PAGE, "chain": {"steps": [{"select": "video", "attr": "src"}]}}
    )

    assert (shorthand.status_code, chain.status_code) == (200, 200)
    report = ChainReport.model_validate(shorthand.json())
    assert report.ok
    assert [step.step for step in report.steps] == ["a@href", "follow", "video@src"]
    assert ChainReport.model_validate(chain.json()).chain == TagChain.model_validate("video@src")


async def test_testing_a_chain_that_is_not_one_is_unprocessable(client: httpx.AsyncClient) -> None:
    response = await client.post("/chains/test", json={"url": PAGE, "chain": "video source"})

    assert response.status_code == 422
    assert "end the select step" in response.text


async def test_the_chain_argument_is_documented_as_shorthand_or_a_chain(
    client: httpx.AsyncClient,
) -> None:
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]

    chain = schemas["ChainTestRequest"]["properties"]["chain"]
    assert "shorthand" in chain["description"]
    assert chain["$ref"] == "#/components/schemas/ChainInput"
    assert schemas["ChainInput"]["anyOf"] == [
        {"type": "string"},
        {"$ref": "#/components/schemas/TagChain"},
    ]


async def test_saving_and_exporting_a_site_s_chain(client: httpx.AsyncClient) -> None:
    saved = await client.put(
        "/sites/www.example.com/chain",
        json={"chain": "click:#play >> video@src", "pattern": "^/watch/", "priority": 2},
    )
    exported = await client.get("/sites/example.com/chain")

    assert saved.status_code == 200
    strategy = Strategy.model_validate(saved.json())
    assert (strategy.site_key, strategy.tool, strategy.source, strategy.priority) == (
        "example.com",
        "browser",
        StrategySource.MANUAL,
        2,
    )
    assert exported.status_code == 200
    chain_file = ChainFile.model_validate(exported.json())
    assert [chain.chain() for chain in read_chains(chain_file.yaml)] == [strategy.chain]


async def test_a_site_without_chains_has_none_to_export(client: httpx.AsyncClient) -> None:
    response = await client.get("/sites/example.com/chain")

    assert response.status_code == 404
    assert ErrorInfo.model_validate(response.json()).code is ErrorCode.DOES_NOT_EXIST


async def test_importing_chains(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/chains/import", json={"yaml": "site: example.com\nsteps: video@src\n"}
    )
    broken = await client.post("/chains/import", json={"yaml": "site: [unclosed"})

    assert response.status_code == 200
    [strategy] = ImportedChains.model_validate(response.json()).strategies
    assert (strategy.site_key, strategy.source) == ("example.com", StrategySource.IMPORT)
    assert broken.status_code == 400
    assert ErrorInfo.model_validate(broken.json()).code is ErrorCode.CONFIG_ERROR


async def test_a_chain_for_a_tool_that_is_not_installed_is_a_bad_request(tmp_path: Path) -> None:
    harness = Harness(tmp_path)
    harness.registry = registry("http")  # (no scraper)
    async with held(client_for(harness)) as client:
        response = await client.put("/sites/example.com/chain", json={"chain": "video@src"})

    assert response.status_code == 400
    assert ErrorInfo.model_validate(response.json()).code is ErrorCode.PLUGIN_ERROR
