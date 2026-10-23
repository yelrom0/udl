"""
-------------------------
- test_site_contract.py -
-------------------------

The behaviour every site plugin must have. Each test runs once per plugin in CASES, with URLs
the plugin handles; a new plugin starts by adding itself there (and watching this suite fail).
Every hook is optional, so a plugin that overrides none of them (Bare) passes too.
"""

# System Imports
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Package Imports
import pytest

# Local Imports
from support.sites import fake_site
from udl.chains.models import TagChain
from udl.config import Settings
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.models import Strategy, StrategySource
from udl.plugins.downloaders.base import DownloadContext, DownloadResult
from udl.plugins.registry import PluginRegistry
from udl.plugins.sites.base import SitePlugin, on_domains

# Hosts no plugin should take for its own: lookalikes of each domain are added to them.
FOREIGN: Final[tuple[str, ...]] = ("example.net", "localhost", "203.0.113.7", "udl.invalid")


@dataclass(frozen=True)
class Case:
    """A site plugin, and URLs it handles."""

    plugin: SitePlugin
    urls: tuple[URL, ...]


class Bare(SitePlugin):
    """A plugin that overrides no hook."""

    name = "bare"


def bare() -> Case:
    return Case(Bare(), ())


def fake() -> Case:
    watch = URL.parse("https://example.com/watch/1")
    plugin = fake_site(
        "example.com",
        "media.example.org",
        strategy=Strategy(
            site_key="example.com",
            tool="scraper",
            chain=TagChain.model_validate("video source@src"),
            source=StrategySource.PLUGIN,
        ),
        urls={watch.value: ["https://example.com/watch/1/a", "https://example.com/watch/1/b"]},
        headers={"Referer": "https://example.com/"},
    )
    return Case(plugin, (watch, URL.parse("https://cdn.media.example.org/v.mp4")))


# How to get each plugin, and the URLs it handles.
CASES: dict[str, Callable[[], Case]] = {
    "bare": bare,
    "fake": fake,
}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]()


def foreign_urls(plugin: SitePlugin) -> list[URL]:
    """URLs off the plugin's domains, some of them made to look like its domains."""
    hosts = [
        *FOREIGN,
        *(f"not{domain}" for domain in plugin.domains),
        *(f"{domain}.example.net" for domain in plugin.domains),
    ]
    urls = [URL.parse(f"https://{host}/watch?v=1") for host in hosts]
    return [url for url in urls if not on_domains(url, plugin.domains)]


def test_a_site_plugin_has_a_name(case: Case) -> None:
    assert isinstance(case.plugin.name, str)
    assert case.plugin.name


def test_it_matches_the_urls_it_handles_which_are_on_its_domains(case: Case) -> None:
    for url in case.urls:
        assert case.plugin.matches(url), url
        assert on_domains(url, case.plugin.domains), url


def test_it_never_matches_a_url_off_its_domains(case: Case) -> None:
    for url in foreign_urls(case.plugin):
        assert not case.plugin.matches(url), url


async def test_transform_urls_never_returns_an_empty_list(case: Case) -> None:
    for url in case.urls:
        found = await case.plugin.transform_urls(url)

        assert found, url
        assert all(isinstance(each, URL) for each in found)
        assert [URL.parse(each.value) for each in found] == found  # (normalised)


def test_its_strategy_names_an_installed_downloader_and_valid_options(case: Case) -> None:
    toolbox = Toolbox(Settings(), PluginRegistry.discover())
    for url in case.urls:
        if (strategy := case.plugin.strategy(url)) is not None:
            toolbox.get(strategy.tool, strategy.options, chain=strategy.chain)


async def test_prepare_keeps_what_is_downloaded_and_where(case: Case, tmp_path: Path) -> None:
    for url in case.urls:
        ctx = DownloadContext(url=url.value, output_dir=tmp_path)

        prepared = await case.plugin.prepare(ctx)

        assert isinstance(prepared, DownloadContext)
        assert (prepared.url, prepared.output_dir) == (ctx.url, ctx.output_dir)


def test_its_output_dir_is_a_folder_or_none(case: Case) -> None:
    for url in case.urls:
        assert isinstance(case.plugin.output_dir(url), Path | None)


async def test_post_process_gives_files_that_exist(case: Case, tmp_path: Path) -> None:
    for number, url in enumerate(case.urls):
        file = tmp_path / f"{number}.mp4"
        await asyncio.to_thread(file.write_bytes, b"media")
        result = DownloadResult(tool="http", url=url.value, files=[file], size_bytes=5)

        processed = await case.plugin.post_process(result)

        assert isinstance(processed, DownloadResult)
        assert all(file.is_absolute() and file.is_file() for file in processed.files)
