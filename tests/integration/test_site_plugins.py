"""
------------------------
- test_site_plugins.py -
------------------------

Site plugins end to end: a plugin in a single file dropped into the plugin dir (by default
~/.config/udl/plugins, no packaging needed) takes part in downloads from the local fixture site
with udl's own downloaders, and one named like a built-in plugin replaces it
"""

# System Imports
import textwrap
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.engine import connection
from support.fixture_site import COOKIE_NAME, COOKIE_VALUE, FixtureSite, Page
from udl.config import DBSettings, Settings, config_dir
from udl.core.jobs import InstanceLock
from udl.core.models import CandidateSource, DownloadRequest, SiteRequest
from udl.core.service import UDLService
from udl.core.urls import URL
from udl.db.models import JobStatus
from udl.errors import DoesNotExist
from udl.plugins.registry import SITES, PluginSource

# A site plugin for the fixture site: /v/private is its private file, whose download needs the
# session cookie; the plugin's own folder; and the files renamed once downloaded.
FIXTURE_PLUGIN = """
from pathlib import Path

from pydantic import SecretStr

from udl.core.urls import URL
from udl.db.models import Strategy, StrategySource
from udl.plugins.downloaders.base import Cookie, DownloadContext, DownloadResult
from udl.plugins.sites.base import SitePlugin


class Fixture(SitePlugin):
    name = "{name}"
    domains = ("localhost",)

    async def transform_urls(self, url: URL) -> list[URL]:
        if url.path == "/v/private":
            return [URL.parse(url.value.replace("/v/private", "{private}"))]
        return [url]

    def strategy(self, url: URL) -> Strategy | None:
        return Strategy(site_key=url.site_key, tool="http", source=StrategySource.PLUGIN)

    async def prepare(self, ctx: DownloadContext) -> DownloadContext:
        cookie = Cookie(name="{cookie}", value=SecretStr("{value}"), domain="localhost")
        return ctx.model_copy(update={{"cookies": [*ctx.cookies, cookie]}})

    def output_dir(self, url: URL) -> Path | None:
        return Path("{output_dir}")

    async def post_process(self, result: DownloadResult) -> DownloadResult:
        renamed = [file.rename(file.with_stem(f"{{file.stem}}-fixture")) for file in result.files]
        return result.model_copy(update={{"files": renamed}})
"""


def write_plugin(directory: Path, output_dir: Path, *, name: str = "fixture") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.py"
    source = FIXTURE_PLUGIN.format(
        name=name,
        private=Page.PRIVATE_FILE,
        cookie=COOKIE_NAME,
        value=COOKIE_VALUE,
        output_dir=output_dir,
    )
    path.write_text(textwrap.dedent(source))
    return path


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    """A service with udl's own plugins and those in the default plugin dir, which the test
    writes first."""
    write_plugin(config_dir() / "plugins", tmp_path / "fixture")
    config = Settings(
        download_dir=tmp_path / "downloads",
        probe_order=["http", "yt-dlp"],
        db=DBSettings(connections=[connection(primary=True)]),
    )
    async with UDLService(config, lock=InstanceLock(tmp_path / "udl.lock")) as service:
        yield service


async def test_a_plugin_file_in_the_plugin_dir_takes_part_in_downloads(
    fixture_site: FixtureSite, service: UDLService, tmp_path: Path
) -> None:
    [job] = (await service.download(DownloadRequest(urls=[fixture_site.url("/v/private")]))).jobs

    assert job.status is JobStatus.FINISHED, job.error
    assert job.url == fixture_site.url(Page.PRIVATE_FILE)
    assert [(a.tool, a.source, a.error) for a in job.attempts] == [
        ("http", CandidateSource.PLUGIN, None)
    ]
    assert job.files == [tmp_path / "fixture" / "localhost" / "sample-fixture.mp4"]
    assert job.files[0].read_bytes() == fixture_site.content(Page.PRIVATE_FILE)
    assert job.size_bytes == len(fixture_site.content(Page.PRIVATE_FILE))
    with pytest.raises(DoesNotExist):  # (a plugin's choice is not learned)
        await service.get_site(SiteRequest(site="localhost"))


async def test_a_plugin_file_named_like_a_built_in_plugin_replaces_it(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    path = write_plugin(plugin_dir, tmp_path / "fixture", name="youtube")
    config = Settings(plugin_dirs=[plugin_dir], db=DBSettings(connections=[connection()]))
    service = UDLService(config, lock=InstanceLock(tmp_path / "udl.lock"))

    youtube = service.resolver.site_plugin(URL.parse("https://www.youtube.com/watch?v=x"))
    local = service.resolver.site_plugin(URL.parse("http://localhost/v/private"))

    assert youtube is None
    assert local is not None
    assert local.name == "youtube"
    [info] = service.registry.plugins(SITES)
    assert (info.name, info.source, info.origin) == ("youtube", PluginSource.LOCAL, str(path))
