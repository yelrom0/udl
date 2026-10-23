"""
----------------
- test_base.py -
----------------

SitePlugin: every hook is optional (the base class does nothing), a plugin matches the URLs on
its domains, and its domains must be written as hosts
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.core.urls import URL
from udl.errors import PluginError
from udl.plugins.downloaders.base import DownloadContext, DownloadResult
from udl.plugins.sites.base import SitePlugin, on_domains

PAGE = URL.parse("https://example.com/watch/1")


class Bare(SitePlugin):
    name = "bare"


class Example(SitePlugin):
    name = "example"
    domains = ("example.com", "media.example.org")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch/1",
        "https://www.example.com/",
        "https://m.example.com/watch/1",
        "http://example.com:8080/a",
        "https://media.example.org/v/1",
        "https://cdn.media.example.org/v/1",
    ],
)
def test_a_plugin_matches_the_urls_on_its_domains_and_their_subdomains(url: str) -> None:
    assert Example().matches(URL.parse(url))


@pytest.mark.parametrize(
    "url",
    [
        "https://example.net/",
        "https://notexample.com/",
        "https://example.com.evil.test/",
        "https://example.org/",  # media.example.org is the plugin's, not the rest of the site
        "https://www.example.org/",
        "https://localhost/",
    ],
)
def test_a_plugin_does_not_match_urls_off_its_domains(url: str) -> None:
    assert not Example().matches(URL.parse(url))


def test_on_domains_is_whether_the_url_s_host_is_one_of_them_or_under_one() -> None:
    assert on_domains(URL.parse("https://a.b.example.com/"), ("example.com",))
    assert not on_domains(URL.parse("https://example.com/"), ("a.example.com",))
    assert not on_domains(PAGE, ())


async def test_the_base_class_is_a_plugin_that_does_nothing(tmp_path: Path) -> None:
    plugin = Bare()
    ctx = DownloadContext(url=PAGE.value, output_dir=tmp_path, headers={"Referer": PAGE.value})
    result = DownloadResult(tool="http", url=PAGE.value, files=[tmp_path / "a.mp4"])

    assert Bare.domains == ()
    assert Bare.priority == 0
    assert not plugin.matches(PAGE)
    assert await plugin.transform_urls(PAGE) == [PAGE]
    assert plugin.strategy(PAGE) is None
    assert plugin.output_dir(PAGE) is None
    assert await plugin.prepare(ctx) is ctx
    assert await plugin.post_process(result) is result


@pytest.mark.parametrize(
    "domain",
    [
        "Example.com",
        "https://example.com",
        "example.com/watch",
        "example.com:8080",
        "example.com.",
        "user@example.com",
        "",
        "exa mple.com",
    ],
)
def test_a_domain_must_be_written_as_a_host(domain: str) -> None:
    with pytest.raises(PluginError, match="host") as caught:
        type("Wrong", (SitePlugin,), {"name": "wrong", "domains": (domain,)})

    assert repr(domain) in caught.value.message
    assert "Wrong" in caught.value.message


def test_domains_may_be_hosts_with_no_registrable_domain() -> None:
    class Local(SitePlugin):
        name = "local"
        domains = ("localhost", "127.0.0.1", "nas")

    assert Local().matches(URL.parse("http://localhost:8080/a"))
    assert Local().matches(URL.parse("http://127.0.0.1/a"))
