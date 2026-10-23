"""
-----------------
- test_tools.py -
-----------------

Toolbox: the downloaders, configured from the settings and a strategy's options
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.downloaders import FakeOptions
from support.engine import registry, script, settings
from udl.chains.models import TagChain
from udl.config import BrowserSettings
from udl.core.tools import Toolbox
from udl.errors import ConfigError, PluginError
from udl.plugins.downloaders.browser import BrowserDownloader, BrowserOptions
from udl.plugins.downloaders.scraper import ScraperDownloader, ScraperOptions
from udl.plugins.registry import DOWNLOADERS, PluginRegistry


def test_the_toolbox_knows_the_registered_downloaders(tmp_path: Path) -> None:
    toolbox = Toolbox(settings(tmp_path), registry("http", "yt-dlp"))

    assert toolbox.names == ["http", "yt-dlp"]
    assert toolbox.has("http")
    assert not toolbox.has("browser")


def test_a_downloader_gets_its_config_table_then_the_strategy_s_options(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script(confidence=0.2, matches={"https://a/": 1})})
    toolbox = Toolbox(config, registry("http"))

    downloader = toolbox.get("http", {"confidence": 0.9})

    assert downloader.name == "http"
    assert isinstance(downloader.options, FakeOptions)
    assert downloader.options.confidence == 0.9
    assert downloader.options.matches == {"https://a/": 1}


def test_a_downloader_follows_the_tag_chain_it_is_given(tmp_path: Path) -> None:
    chain = TagChain.model_validate("iframe@src >> video@src")
    plugins = PluginRegistry()
    plugins.register(DOWNLOADERS, ScraperDownloader)
    config = settings(tmp_path, tools={"scraper": {"timeout": 5}})

    options = Toolbox(config, plugins).get("scraper", {"retries": 1}, chain=chain).options

    assert isinstance(options, ScraperOptions)
    assert (options.chain, options.timeout, options.retries) == (chain, 5, 1)


def test_a_downloader_without_a_chain_follows_none(tmp_path: Path) -> None:
    plugins = PluginRegistry()
    plugins.register(DOWNLOADERS, ScraperDownloader)

    options = Toolbox(settings(tmp_path), plugins).get("scraper").options

    assert isinstance(options, ScraperOptions)
    assert options.chain is None


def test_a_downloader_that_follows_no_chains_cannot_be_given_one(tmp_path: Path) -> None:
    chain = TagChain.model_validate("video@src")
    plugins = PluginRegistry()
    plugins.register(DOWNLOADERS, BrowserDownloader)
    plugins.register(DOWNLOADERS, ScraperDownloader)
    toolbox = Toolbox(settings(tmp_path), plugins)

    with pytest.raises(ConfigError, match="browser chain runs in the browser"):
        toolbox.get("scraper", chain=TagChain.model_validate("click:a >> video@src"))
    assert isinstance(toolbox.get("browser", chain=chain).options, BrowserOptions)


def test_an_unknown_downloader_is_a_plugin_error(tmp_path: Path) -> None:
    with pytest.raises(PluginError, match="nope"):
        Toolbox(settings(tmp_path), registry("http")).get("nope")


def test_bad_options_are_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="http"):
        Toolbox(settings(tmp_path), registry("http")).get("http", {"confidence": "high"})


def test_the_browser_runs_as_the_browser_settings_say(tmp_path: Path) -> None:
    plugins = PluginRegistry()
    plugins.register(DOWNLOADERS, BrowserDownloader)
    browser = BrowserSettings(headless=False, profile_dir=tmp_path / "profile")
    toolbox = Toolbox(settings(tmp_path, browser=browser), plugins)

    options = toolbox.get("browser").options

    assert isinstance(options, BrowserOptions)
    assert options.headless is False
    assert options.profile_dir == tmp_path / "profile"


def test_the_browser_s_config_table_and_strategy_override_the_browser_settings(
    tmp_path: Path,
) -> None:
    plugins = PluginRegistry()
    plugins.register(DOWNLOADERS, BrowserDownloader)
    config = settings(
        tmp_path,
        browser=BrowserSettings(headless=False, profile_dir=tmp_path / "profile"),
        tools={"browser": {"headless": True}},
    )

    options = Toolbox(config, plugins).get("browser", {"profile_dir": None}).options

    assert isinstance(options, BrowserOptions)
    assert options.headless is True
    assert options.profile_dir is None


def test_a_downloader_called_browser_without_those_options_gets_none_of_them(
    tmp_path: Path,
) -> None:
    toolbox = Toolbox(settings(tmp_path), registry("browser"))

    assert isinstance(toolbox.get("browser").options, FakeOptions)


def test_handoffs_get_configured_downloaders(tmp_path: Path) -> None:
    config = settings(tmp_path, tools={"http": script(confidence=0.3)})
    toolbox = Toolbox(config, registry("http"))

    downloader = toolbox.downloader_for("http")

    assert isinstance(downloader.options, FakeOptions)
    assert downloader.options.confidence == 0.3
