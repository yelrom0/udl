"""
--------------------------
- test_service_chains.py -
--------------------------

UDLService's tag chain operations, on fakes: suggesting chains for a page (static, then in the
browser), testing one, saving one for a site, and importing and exporting chain files
"""

# System Imports
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import JsonValue, ValidationError

# Local Imports
from support.downloaders import FakeMedia
from support.engine import StaticCookies, cookie, registry, script, settings
from udl.chains.files import read_chains
from udl.chains.models import FetchMode, StepTrace, TagChain
from udl.core.executor import RetryPolicy
from udl.core.jobs import InstanceLock
from udl.core.models import (
    ChainTestRequest,
    DownloadRequest,
    ImportChainsRequest,
    SetChainRequest,
    SetStrategyRequest,
    SiteRequest,
    SuggestChainRequest,
)
from udl.core.service import UDLService
from udl.db.models import JobStatus, StrategySource
from udl.errors import ConfigError, DoesNotExist, ErrorCode, PluginError
from udl.plugins.downloaders.base import (
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadResult,
    ProbeContext,
    ProbeResult,
)
from udl.plugins.registry import DOWNLOADERS, PluginRegistry

PAGE = "https://example.com/watch/1"
MEDIA = "https://cdn.example.com/v.mp4"
OTHER = "https://cdn.example.com/other.mp4"

type Make = Callable[..., Awaitable[UDLService]]


@pytest.fixture
async def make(tmp_path: Path) -> AsyncIterator[Make]:
    """Makes started services on fakes, whose tools are scripted by `tools`."""
    services: list[UDLService] = []

    async def make(
        tools: dict[str, dict[str, JsonValue]] | None = None,
        *,
        plugins: PluginRegistry | None = None,
        cookies: StaticCookies | None = None,
        **fields: Any,
    ) -> UDLService:
        service = UDLService(
            settings(tmp_path, tools=tools, **fields),
            registry=plugins or registry(),
            cookies=cookies or StaticCookies(),
            retry=RetryPolicy(retries=0),
            lock=InstanceLock(tmp_path / "udl.lock"),
            progress_interval=0,
        )
        await service.start()
        services.append(service)
        return service

    yield make
    for service in services:
        await service.close()


class Plain(Downloader):
    """A downloader that neither suggests nor follows chains."""

    name = "scraper"

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        raise AssertionError

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        raise AssertionError


class ChainOptions(DownloaderOptions):
    chain: TagChain | None = None


class Unfollowing(Plain):
    """A downloader that takes a chain, but cannot follow it."""

    options_model = ChainOptions


def plain_scraper(scraper: type[Downloader] = Plain) -> PluginRegistry:
    plugins = registry("http", "browser")
    plugins.register(DOWNLOADERS, scraper)
    return plugins


# ---------------------------------------------------------------------------------------------
# suggest_chain
# ---------------------------------------------------------------------------------------------


async def test_suggestions_are_ranked_by_what_the_media_s_server_says(make: Make) -> None:
    service = await make(
        {
            "scraper": script(suggested={PAGE: {"a.small@href": OTHER, "video@src": MEDIA}}),
            "http": script({MEDIA: FakeMedia(content=b"x" * 100), OTHER: "x"}),
        }
    )

    found = await service.suggest_chain(SuggestChainRequest(url=PAGE))

    assert [(s.shorthand, s.media.url, s.reachable) for s in found.suggestions] == [
        ("video@src", MEDIA, True),
        ("a.small@href", OTHER, True),
    ]
    assert found.suggestions[0].media.size_bytes == 100
    assert (found.url, found.site_key, found.fetch, found.error) == (
        PAGE,
        "example.com",
        FetchMode.STATIC,
        None,
    )


async def test_media_that_does_not_answer_goes_last(make: Make) -> None:
    service = await make(
        {
            "scraper": script(suggested={PAGE: {"video@src": MEDIA, "a@href": OTHER}}),
            "http": script({OTHER: "x"}, errors={MEDIA: ErrorCode.NOT_FOUND}),
        }
    )

    found = await service.suggest_chain(SuggestChainRequest(url=PAGE))

    assert [(s.media.url, s.reachable) for s in found.suggestions] == [
        (OTHER, True),
        (MEDIA, False),
    ]


async def test_when_the_page_as_served_has_nothing_the_browser_renders_it(make: Make) -> None:
    service = await make({"browser": script(suggested={PAGE: {"div#player video@src": MEDIA}})})

    found = await service.suggest_chain(SuggestChainRequest(url=PAGE))

    assert found.fetch is FetchMode.BROWSER
    [suggestion] = found.suggestions
    assert suggestion.chain.fetch is FetchMode.BROWSER


@pytest.mark.parametrize("fetch", list(FetchMode))
async def test_fetch_says_how_the_page_is_read(make: Make, fetch: FetchMode) -> None:
    service = await make(
        {
            "scraper": script(suggested={PAGE: {"video@src": MEDIA}}),
            "browser": script(suggested={PAGE: {"div#player video@src": MEDIA}}),
        }
    )

    found = await service.suggest_chain(SuggestChainRequest(url=PAGE, fetch=fetch))

    assert found.fetch is fetch
    assert [s.chain.fetch for s in found.suggestions] == [fetch]


async def test_without_a_browser_the_page_as_served_is_all_there_is(make: Make) -> None:
    service = await make(plugins=registry("http", "scraper"))

    found = await service.suggest_chain(SuggestChainRequest(url=PAGE))

    assert (found.fetch, found.suggestions, found.error) == (FetchMode.STATIC, [], None)


async def test_a_page_that_cannot_be_read_says_why(make: Make) -> None:
    service = await make({"scraper": script(errors={PAGE: ErrorCode.NOT_FOUND})})

    found = await service.suggest_chain(SuggestChainRequest(url=PAGE))

    assert found.suggestions == []
    assert found.error is not None
    assert (found.error.code, found.error.tool) == (ErrorCode.NOT_FOUND, "scraper")


async def test_suggestions_are_limited(make: Make) -> None:
    chains = {f"a.n{n}@href": f"https://cdn.example.com/{n}.mp4" for n in range(5)}
    service = await make({"scraper": script(suggested={PAGE: chains})})

    found = await service.suggest_chain(SuggestChainRequest(url=PAGE, limit=2))

    assert [s.shorthand for s in found.suggestions] == ["a.n0@href", "a.n1@href"]


async def test_without_the_http_downloader_the_media_is_not_asked_for(make: Make) -> None:
    service = await make(
        {"scraper": script(suggested={PAGE: {"video@src": MEDIA}})},
        plugins=registry("scraper", "browser"),
    )

    [suggestion] = (await service.suggest_chain(SuggestChainRequest(url=PAGE))).suggestions

    assert (suggestion.reachable, suggestion.media.size_bytes) == (False, None)


async def test_a_downloader_that_cannot_suggest_chains_says_so(make: Make) -> None:
    service = await make(plugins=plain_scraper())

    with pytest.raises(PluginError, match="scraper"):
        await service.suggest_chain(SuggestChainRequest(url=PAGE, fetch=FetchMode.STATIC))


# ---------------------------------------------------------------------------------------------
# test_chain
# ---------------------------------------------------------------------------------------------


async def test_testing_a_chain_shows_its_steps_and_media(make: Make) -> None:
    service = await make(
        {
            "scraper": script(chained={PAGE: FakeMedia(title="A film")}),
            "http": script({PAGE: "the media"}),
        }
    )

    report = await service.test_chain(
        ChainTestRequest.model_validate({"url": PAGE, "chain": "iframe@src >> video@src"})
    )

    assert report.ok
    assert report.chain == TagChain.model_validate("iframe@src >> video@src")
    assert [step.step for step in report.steps] == ["iframe@src", "follow", "video@src"]
    assert [(m.url, m.size_bytes) for m in report.media] == [(PAGE, len(b"the media"))]
    assert (report.title, report.error, report.signed_in) == ("A film", None, False)


async def test_a_browser_chain_is_tested_in_the_browser(make: Make) -> None:
    service = await make({"browser": script(chained={PAGE: "x"})})

    report = await service.test_chain(
        ChainTestRequest.model_validate({"url": PAGE, "chain": "click:#play >> video@src"})
    )

    assert report.ok
    assert report.chain.fetch is FetchMode.BROWSER


async def test_a_chain_that_finds_nothing_says_why(make: Make) -> None:
    service = await make()

    report = await service.test_chain(
        ChainTestRequest.model_validate({"url": PAGE, "chain": "a@href"})
    )

    assert not report.ok
    assert report.media == []
    assert report.error is not None
    assert report.error.code is ErrorCode.UNSUPPORTED_URL


async def test_a_chain_on_a_page_that_wants_a_login_is_tested_signed_in(make: Make) -> None:
    service = await make(
        {"scraper": script(chained={PAGE: FakeMedia(cookie="session=signed-in")})},
        cookies=StaticCookies([cookie()]),
    )

    report = await service.test_chain(
        ChainTestRequest.model_validate({"url": PAGE, "chain": "a@href"})
    )

    assert (report.ok, report.signed_in) == (True, True)
    assert report.steps == [StepTrace(step="a@href", found=[PAGE], count=1)]


async def test_a_chain_on_a_page_that_wants_a_login_without_cookies_says_so(make: Make) -> None:
    service = await make({"scraper": script(chained={PAGE: FakeMedia(cookie="session=x")})})

    report = await service.test_chain(
        ChainTestRequest.model_validate({"url": PAGE, "chain": "a@href"})
    )

    assert (report.ok, report.signed_in) == (False, False)
    assert report.error is not None
    assert report.error.code is ErrorCode.AUTH_REQUIRED


async def test_a_downloader_that_cannot_follow_chains_says_so(make: Make) -> None:
    plain = await make(plugins=plain_scraper())
    unfollowing = await make(plugins=plain_scraper(Unfollowing))
    request = ChainTestRequest.model_validate({"url": PAGE, "chain": "a@href"})

    with pytest.raises(ConfigError, match="scraper"):
        await plain.test_chain(request)
    with pytest.raises(PluginError, match="cannot follow tag chains"):
        await unfollowing.test_chain(request)


def test_a_chain_to_test_is_checked() -> None:
    with pytest.raises(ValidationError, match="end the select step"):
        ChainTestRequest.model_validate({"url": PAGE, "chain": "video source"})


# ---------------------------------------------------------------------------------------------
# set_site_chain
# ---------------------------------------------------------------------------------------------


async def test_setting_a_chain_stores_a_manual_strategy_for_its_tool(make: Make) -> None:
    service = await make()

    static = await service.set_site_chain(
        SetChainRequest.model_validate({"site": PAGE, "chain": "video@src", "pattern": "^/watch/"})
    )
    browser = await service.set_site_chain(
        SetChainRequest.model_validate({"site": "example.com", "chain": "click:a >> video@src"})
    )

    assert (static.site_key, static.tool, static.source, static.url_pattern) == (
        "example.com",
        "scraper",
        StrategySource.MANUAL,
        "^/watch/",
    )
    assert static.chain == TagChain.model_validate("video@src")
    assert browser.tool == "browser"
    info = await service.get_site(SiteRequest(site="example.com"))
    assert {s.id for s in info.strategies} == {static.id, browser.id}


async def test_setting_a_chain_for_the_same_pattern_replaces_it(make: Make) -> None:
    service = await make({"scraper": script(chained={PAGE: "x"})})
    first = await service.set_site_chain(
        SetChainRequest.model_validate({"site": "example.com", "chain": "video@src"})
    )
    await service.download(DownloadRequest(urls=[PAGE]))

    same = await service.set_site_chain(
        SetChainRequest.model_validate({"site": "example.com", "chain": "video@src", "priority": 2})
    )
    other = await service.set_site_chain(
        SetChainRequest.model_validate({"site": "example.com", "chain": "click:a >> video@src"})
    )

    assert same.id == other.id == first.id
    assert (same.success_count, same.priority) == (1, 2)
    assert (other.tool, other.success_count, other.priority) == ("browser", 0, 0)
    assert len((await service.get_site(SiteRequest(site="example.com"))).strategies) == 1


async def test_setting_a_chain_leaves_the_site_s_other_strategies(make: Make) -> None:
    service = await make()
    plain = await service.set_site_strategy(SetStrategyRequest(site="example.com", tool="scraper"))

    chained = await service.set_site_chain(
        SetChainRequest.model_validate({"site": "example.com", "chain": "video@src"})
    )

    assert chained.id != plain.id
    strategies = (await service.get_site(SiteRequest(site="example.com"))).strategies
    assert {(s.tool, s.chain is None) for s in strategies} == {
        ("scraper", True),
        ("scraper", False),
    }


async def test_a_chain_for_a_downloader_that_cannot_follow_it_is_refused(make: Make) -> None:
    service = await make(plugins=plain_scraper())

    with pytest.raises(ConfigError, match="scraper"):
        await service.set_site_chain(
            SetChainRequest.model_validate({"site": "example.com", "chain": "video@src"})
        )


async def test_a_downloaded_chain_strategy_is_followed(make: Make) -> None:
    service = await make({"scraper": script(chained={PAGE: "found by the chain"})})
    strategy = await service.set_site_chain(
        SetChainRequest.model_validate({"site": "example.com", "chain": "video@src"})
    )

    batch = await service.download(DownloadRequest(urls=[PAGE]))

    [job] = batch.jobs
    assert (job.status, job.strategy_id) == (JobStatus.FINISHED, strategy.id)
    assert job.files[0].read_bytes() == b"found by the chain"


# ---------------------------------------------------------------------------------------------
# import_chains and export_chain
# ---------------------------------------------------------------------------------------------

FILE = """\
site: www.example.com
pattern: ^/watch/
steps: iframe#player@src >> video source@src | best
---
site: other.org
steps: click:#play >> video@src
priority: -1
requires_auth: true
"""


async def test_importing_a_chain_file_stores_its_chains(make: Make) -> None:
    service = await make()

    imported = await service.import_chains(ImportChainsRequest(yaml=FILE))

    first, second = imported.strategies
    assert (first.site_key, first.url_pattern, first.tool, first.source) == (
        "example.com",
        "^/watch/",
        "scraper",
        StrategySource.IMPORT,
    )
    assert (second.site_key, second.tool, second.priority, second.requires_auth) == (
        "other.org",
        "browser",
        -1,
        True,
    )
    assert [s.chain for s in imported.strategies] == [c.chain() for c in read_chains(FILE)]


async def test_importing_again_replaces_the_chains(make: Make) -> None:
    service = await make()
    first = await service.import_chains(ImportChainsRequest(yaml=FILE))

    again = await service.import_chains(ImportChainsRequest(yaml=FILE))

    assert [s.id for s in again.strategies] == [s.id for s in first.strategies]


async def test_importing_a_chain_that_is_already_saved_keeps_it_as_it_is(make: Make) -> None:
    service = await make()
    manual = await service.set_site_chain(
        SetChainRequest.model_validate(
            {"site": "example.com", "chain": "iframe#player@src >> video source@src | best"}
        )
    )
    changed = FILE.replace("video source@src | best", "video@src").replace(
        "pattern: ^/watch/\n", ""
    )

    kept = await service.import_chains(
        ImportChainsRequest(yaml=FILE.replace("pattern: ^/watch/\n", ""))
    )
    replaced = await service.import_chains(ImportChainsRequest(yaml=changed))

    assert (kept.strategies[0].id, kept.strategies[0].source) == (manual.id, StrategySource.MANUAL)
    assert (replaced.strategies[0].id, replaced.strategies[0].source) == (
        manual.id,
        StrategySource.IMPORT,
    )


async def test_a_chain_file_with_a_problem_imports_nothing(make: Make) -> None:
    service = await make()
    broken = FILE + "---\nsite: third.net\nsteps: video source\n"

    with pytest.raises(ConfigError, match="chain 3"):
        await service.import_chains(ImportChainsRequest(yaml=broken))

    with pytest.raises(DoesNotExist):
        await service.get_site(SiteRequest(site="example.com"))


async def test_exporting_a_site_s_chains(make: Make) -> None:
    service = await make()
    await service.set_site_strategy(SetStrategyRequest(site="example.com", tool="http"))
    await service.set_site_chain(
        SetChainRequest.model_validate(
            {"site": "example.com", "chain": "a@href", "pattern": "^/files/", "priority": 1}
        )
    )
    await service.set_site_chain(
        SetChainRequest.model_validate(
            {"site": "example.com", "chain": {"steps": "video@src", "headers": {"X": "1"}}}
        )
    )

    exported = await service.export_chain(SiteRequest(site="https://www.example.com/x"))

    assert exported.site == "example.com"
    assert [(c.pattern, c.priority, c.headers) for c in exported.chains] == [
        (None, 0, {"X": "1"}),
        ("^/files/", 1, {}),
    ]
    assert read_chains(exported.yaml) == exported.chains


async def test_exported_chains_import_as_they_were(make: Make) -> None:
    exporter = await make()
    await exporter.import_chains(ImportChainsRequest(yaml=FILE))
    exported = await exporter.export_chain(SiteRequest(site="example.com"))
    importer = await make()  # (with databases of its own)

    [imported] = (await importer.import_chains(ImportChainsRequest(yaml=exported.yaml))).strategies

    [original] = (await exporter.get_site(SiteRequest(site="example.com"))).strategies
    assert (imported.chain, imported.url_pattern) == (original.chain, original.url_pattern)


async def test_a_site_without_chains_has_none_to_export(make: Make) -> None:
    service = await make()
    await service.set_site_strategy(SetStrategyRequest(site="example.com", tool="http"))

    with pytest.raises(DoesNotExist, match=r"example\.com"):
        await service.export_chain(SiteRequest(site="example.com"))
