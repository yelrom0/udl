"""
-----------------
- test_sites.py -
-----------------

POST /probe, GET /sites, GET and DELETE /sites/{key}, PUT /sites/{key}/strategies
"""

# System Imports
from pathlib import Path

# Package Imports
import httpx

# Local Imports
from support.api import client_for
from support.engine import script
from support.interfaces import A, Harness
from udl.core.models import Deleted, ProbeReport, SiteInfo, SiteList
from udl.db.models import Site, Strategy, StrategySource
from udl.errors import ErrorCode, ErrorInfo


def error(response: httpx.Response) -> ErrorInfo:
    return ErrorInfo.model_validate(response.json())


async def test_probing_a_url(tmp_path: Path) -> None:
    async with client_for(Harness(tmp_path, tools={"yt-dlp": script({A: "video"})})) as client:
        response = await client.post("/probe", json={"url": f"{A}?fbclid=1"})
        every = await client.post("/probe", json={"url": A, "all_tools": True})
        one = await client.post("/probe", json={"url": A, "tool": "scraper"})

    assert response.status_code == 200
    report = ProbeReport.model_validate(response.json())
    assert report.url == A
    assert [(r.tool, r.ok) for r in report.results] == [("http", False), ("yt-dlp", True)]
    assert len(ProbeReport.model_validate(every.json()).results) == 5
    assert [r.tool for r in ProbeReport.model_validate(one.json()).results] == ["scraper"]


async def test_probing_with_an_unknown_tool_is_a_bad_request(client: httpx.AsyncClient) -> None:
    response = await client.post("/probe", json={"url": A, "tool": "nope"})

    assert response.status_code == 400
    assert (await client.post("/probe", json={"url": "nope://x"})).status_code == 422


async def test_setting_a_strategy_for_a_site(client: httpx.AsyncClient) -> None:
    response = await client.put(
        "/sites/www.example.com/strategies",
        json={"tool": "http", "pattern": "^/a", "options": {"confidence": 0.5}, "priority": -1},
    )

    assert response.status_code == 200
    strategy = Strategy.model_validate(response.json())
    assert (strategy.site_key, strategy.tool, strategy.url_pattern) == (
        "example.com",
        "http",
        "^/a",
    )
    assert (strategy.options, strategy.priority, strategy.source) == (
        {"confidence": 0.5},
        -1,
        StrategySource.MANUAL,
    )
    shown = SiteInfo.model_validate((await client.get("/sites/example.com")).json())
    assert shown.strategies == [strategy]


async def test_setting_a_strategy_that_cannot_work_is_a_bad_request(
    client: httpx.AsyncClient,
) -> None:
    unknown = await client.put("/sites/example.com/strategies", json={"tool": "nope"})
    options = await client.put(
        "/sites/example.com/strategies", json={"tool": "http", "options": {"nope": 1}}
    )

    assert (unknown.status_code, error(unknown).code) == (400, ErrorCode.PLUGIN_ERROR)
    assert (options.status_code, error(options).code) == (400, ErrorCode.CONFIG_ERROR)


async def test_a_bad_strategy_request_is_unprocessable(client: httpx.AsyncClient) -> None:
    for site, body in (
        ("example.com", {"tool": "http", "pattern": "(unclosed"}),
        ("example.com", {"tool": "http", "site": "other.com"}),
        ("example.com", {}),
        ("not a site", {"tool": "http"}),
    ):
        response = await client.put(f"/sites/{site}/strategies", json=body)

        assert response.status_code == 422, (site, body)


async def test_listing_the_sites(client: httpx.AsyncClient, harness: Harness) -> None:
    for key in ("b.com", "a.com", "example.com"):
        await harness.db.upsert_site(Site(key=key))  # (the service connected the databases)

    def keys(response: httpx.Response) -> list[str]:
        return [site.key for site in SiteList.model_validate(response.json()).sites]

    assert keys(await client.get("/sites")) == ["a.com", "b.com", "example.com"]
    assert keys(await client.get("/sites", params={"search": "exam"})) == ["example.com"]
    assert keys(await client.get("/sites", params={"limit": 1, "offset": 1})) == ["b.com"]


async def test_an_unknown_site_is_not_found(client: httpx.AsyncClient) -> None:
    response = await client.get("/sites/example.com")

    assert response.status_code == 404
    found = error(response)
    assert found.code is ErrorCode.DOES_NOT_EXIST
    assert found.hint is not None


async def test_deleting_a_site(client: httpx.AsyncClient) -> None:
    await client.put("/sites/example.com/strategies", json={"tool": "http"})

    deleted = await client.delete("/sites/www.example.com")
    again = await client.delete("/sites/example.com")

    assert Deleted.model_validate(deleted.json()) == Deleted(deleted=True)
    assert Deleted.model_validate(again.json()) == Deleted(deleted=False)
