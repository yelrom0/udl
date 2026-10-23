"""
-----------------
- test_sites.py -
-----------------

SiteHooks: how the engine calls a site plugin. A hook that only advises (matches,
transform_urls, strategy, output_dir) and fails is logged and treated as having nothing to say;
prepare and post_process take part in the download, so their failures end it (a bug as a
PluginFailed naming the plugin). load_site_plugins: the registered plugins udl uses.
"""

# System Imports
import asyncio
import logging
from pathlib import Path
from typing import Final

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.engine import cookie
from support.sites import FakeSitePlugin, fake_site
from udl.core.sites import SiteHooks, load_site_plugins
from udl.core.urls import URL
from udl.db.models import Strategy, StrategySource
from udl.errors import AuthRequired, PluginError, PluginFailed
from udl.plugins.downloaders.base import Cookie, DownloadContext, DownloadResult
from udl.plugins.registry import SITES, PluginRegistry

PAGE: Final = URL.parse("https://example.com/watch/1")
STRATEGY: Final = Strategy(site_key="example.com", tool="http", source=StrategySource.PLUGIN)


class Broken(FakeSitePlugin):
    """Every hook fails, as a bug in it would."""

    name = "broken"

    def matches(self, url: URL) -> bool:
        raise RuntimeError("matches is broken")

    async def transform_urls(self, url: URL) -> list[URL]:
        raise RuntimeError("transform_urls is broken")

    def strategy(self, url: URL) -> Strategy | None:
        raise RuntimeError("strategy is broken")

    async def prepare(self, ctx: DownloadContext) -> DownloadContext:
        raise KeyError("prepare is broken")

    def output_dir(self, url: URL) -> Path | None:
        raise RuntimeError("output_dir is broken")

    async def post_process(self, result: DownloadResult) -> DownloadResult:
        raise KeyError("post_process is broken")


class Wrong(FakeSitePlugin):
    """Every hook gives something of the wrong type."""

    name = "wrong"

    def matches(self, url: URL) -> bool:
        return "yes"  # pyright: ignore[reportReturnType]

    async def transform_urls(self, url: URL) -> list[URL]:
        return [url.value]  # pyright: ignore[reportReturnType]

    def strategy(self, url: URL) -> Strategy | None:
        return {"tool": "http"}  # pyright: ignore[reportReturnType]

    async def prepare(self, ctx: DownloadContext) -> DownloadContext:
        return None  # pyright: ignore[reportReturnType]

    def output_dir(self, url: URL) -> Path | None:
        return "/srv/media"  # pyright: ignore[reportReturnType]

    async def post_process(self, result: DownloadResult) -> DownloadResult:
        return result.files  # pyright: ignore[reportReturnType]


async def downloaded(tmp_path: Path, content: bytes = b"media") -> DownloadResult:
    file = tmp_path / "media.mp4"
    await asyncio.to_thread(file.write_bytes, content)
    return DownloadResult(tool="http", url=PAGE.value, files=[file], size_bytes=len(content))


# ---------------------------------------------------------------------------------------------
# Hooks that advise
# ---------------------------------------------------------------------------------------------


async def test_the_hooks_give_what_the_plugin_gives(tmp_path: Path) -> None:
    plugin = fake_site(
        strategy=STRATEGY,
        output_dir=tmp_path,
        urls={PAGE.value: ["https://example.com/a", "https://example.com/b"]},
    )
    hooks = SiteHooks(plugin)

    assert hooks.name == "fake"
    assert hooks.plugin is plugin
    assert hooks.matches(PAGE)
    assert not hooks.matches(URL.parse("https://other.com/"))
    assert await hooks.transform_urls(PAGE) == [
        URL.parse("https://example.com/a"),
        URL.parse("https://example.com/b"),
    ]
    assert hooks.strategy(PAGE) == STRATEGY
    assert hooks.output_dir(PAGE) == tmp_path


async def test_a_hook_that_advises_and_fails_is_logged_and_has_nothing_to_say(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hooks = SiteHooks(Broken())

    assert not hooks.matches(PAGE)
    assert await hooks.transform_urls(PAGE) == [PAGE]
    assert hooks.strategy(PAGE) is None
    assert hooks.output_dir(PAGE) is None
    for hook in ("matches", "transform_urls", "strategy", "output_dir"):
        assert f"{hook} is broken" in caplog.text  # (the traceback)
    assert caplog.text.count("The broken site plugin failed") == 4


async def test_a_hook_that_advises_with_the_wrong_type_is_logged_and_has_nothing_to_say(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hooks = SiteHooks(Wrong())

    assert not hooks.matches(PAGE)
    assert await hooks.transform_urls(PAGE) == [PAGE]
    assert hooks.strategy(PAGE) is None
    assert hooks.output_dir(PAGE) is None
    assert caplog.text.count("The wrong site plugin") == 4
    assert "'/srv/media'" in caplog.text


async def test_transform_urls_that_gives_no_urls_downloads_the_url_as_it_is(
    caplog: pytest.LogCaptureFixture,
) -> None:
    hooks = SiteHooks(fake_site(urls={PAGE.value: []}))

    assert await hooks.transform_urls(PAGE) == [PAGE]
    assert "gave no URLs" in caplog.text


async def test_transform_urls_that_raises_a_udl_error_downloads_the_url_as_it_is(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Gone(FakeSitePlugin):
        async def transform_urls(self, url: URL) -> list[URL]:
            raise AuthRequired("The list is private")

    with caplog.at_level(logging.WARNING):
        assert await SiteHooks(Gone()).transform_urls(PAGE) == [PAGE]

    [record] = caplog.records
    assert "The list is private" in record.getMessage()
    assert record.exc_info is None  # (an error raised on purpose is no bug)


async def test_transformed_urls_are_normalised() -> None:
    class Sloppy(FakeSitePlugin):
        async def transform_urls(self, url: URL) -> list[URL]:
            return [URL(value="HTTPS://Example.com/a?utm_source=x", site_key="wrong")]

    assert await SiteHooks(Sloppy()).transform_urls(PAGE) == [URL.parse("https://example.com/a")]


# ---------------------------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------------------------


async def test_prepare_gives_the_plugin_s_headers_and_cookies(tmp_path: Path) -> None:
    extra = Cookie(name="consent", value=SecretStr("yes"), domain="example.com")
    plugin = fake_site(headers={"Referer": "https://example.com/"}, cookies=[extra])
    ctx = DownloadContext(url=PAGE.value, output_dir=tmp_path, cookies=[cookie()])

    prepared = await SiteHooks(plugin).prepare(ctx)

    assert prepared.headers == {"Referer": "https://example.com/"}
    assert prepared.cookies == [cookie(), extra]
    assert plugin.prepared == [ctx]


async def test_prepare_cannot_change_what_is_downloaded_or_where(tmp_path: Path) -> None:
    class Greedy(FakeSitePlugin):
        async def prepare(self, ctx: DownloadContext) -> DownloadContext:
            return ctx.model_copy(
                update={
                    "url": "https://elsewhere.com/x",
                    "output_dir": Path("/"),
                    "filename_template": "{title}.{ext}",
                    "headers": {"X-Token": "t"},
                }
            )

    ctx = DownloadContext(url=PAGE.value, output_dir=tmp_path, filename_template="a/{title}")

    prepared = await SiteHooks(Greedy()).prepare(ctx)

    assert prepared == ctx.model_copy(update={"headers": {"X-Token": "t"}})


async def test_an_error_prepare_raises_on_purpose_is_the_download_s(tmp_path: Path) -> None:
    class Private(FakeSitePlugin):
        async def prepare(self, ctx: DownloadContext) -> DownloadContext:
            raise AuthRequired("Sign in first")

    with pytest.raises(AuthRequired, match="Sign in first"):
        await SiteHooks(Private()).prepare(DownloadContext(url=PAGE.value, output_dir=tmp_path))


@pytest.mark.parametrize(
    ("plugin", "problem"),
    [
        (Broken(), "The broken site plugin failed to prepare the download of "),
        (Wrong(), "The wrong site plugin's prepare gave NoneType, not a DownloadContext"),
    ],
)
async def test_a_prepare_that_fails_is_a_plugin_failure_naming_the_plugin(
    tmp_path: Path, plugin: FakeSitePlugin, problem: str
) -> None:
    ctx = DownloadContext(url=PAGE.value, output_dir=tmp_path)

    with pytest.raises(PluginFailed) as caught:
        await SiteHooks(plugin).prepare(ctx)

    assert caught.value.message.startswith(problem)
    assert isinstance(caught.value, PluginError)
    assert caught.value.hint is not None
    assert "udl plugins list" in caught.value.hint


# ---------------------------------------------------------------------------------------------
# post_process
# ---------------------------------------------------------------------------------------------


async def test_post_process_gives_the_plugin_s_files_measured(tmp_path: Path) -> None:
    plugin = fake_site(move_to=tmp_path / "moved")
    result = await downloaded(tmp_path)

    processed = await SiteHooks(plugin).post_process(result)

    assert processed.files == [tmp_path / "moved" / "media.mp4"]
    assert processed.size_bytes == 5
    assert plugin.post_processed == [result]


async def test_post_process_measures_the_files_again(tmp_path: Path) -> None:
    class Extra(FakeSitePlugin):
        async def post_process(self, result: DownloadResult) -> DownloadResult:
            notes = tmp_path / "notes.txt"
            await asyncio.to_thread(notes.write_text, "notes")
            return result.model_copy(update={"files": [*result.files, notes]})

    processed = await SiteHooks(Extra()).post_process(await downloaded(tmp_path))

    assert processed.size_bytes == len(b"media") + len("notes")


@pytest.mark.parametrize("file", [Path("relative.mp4"), Path("/udl/no/such/file.mp4")])
async def test_post_process_must_give_files_that_exist(tmp_path: Path, file: Path) -> None:
    class Lost(FakeSitePlugin):
        async def post_process(self, result: DownloadResult) -> DownloadResult:
            return result.model_copy(update={"files": [file]})

    with pytest.raises(PluginFailed, match="not a file"):
        await SiteHooks(Lost()).post_process(await downloaded(tmp_path))


async def test_an_error_post_process_raises_on_purpose_is_the_download_s(tmp_path: Path) -> None:
    class Strict(FakeSitePlugin):
        async def post_process(self, result: DownloadResult) -> DownloadResult:
            raise AuthRequired("Tagging needs a login")

    with pytest.raises(AuthRequired, match="Tagging needs a login"):
        await SiteHooks(Strict()).post_process(await downloaded(tmp_path))


@pytest.mark.parametrize(
    ("plugin", "problem"),
    [
        (Broken(), "The broken site plugin failed to post-process https://example.com/watch/1: "),
        (Wrong(), "The wrong site plugin's post_process gave list, not a DownloadResult"),
    ],
)
async def test_a_post_process_that_fails_is_a_plugin_failure_naming_the_plugin(
    tmp_path: Path, plugin: FakeSitePlugin, problem: str
) -> None:
    with pytest.raises(PluginFailed) as caught:
        await SiteHooks(plugin).post_process(await downloaded(tmp_path))

    assert caught.value.message.startswith(problem)


# ---------------------------------------------------------------------------------------------
# Which plugins udl uses
# ---------------------------------------------------------------------------------------------


def test_udl_uses_an_instance_of_each_registered_site_plugin_by_name() -> None:
    registry = PluginRegistry([SITES])
    registry.register(SITES, type(fake_site(called="b")))
    registry.register(SITES, type(fake_site(called="a")))

    assert [plugin.name for plugin in load_site_plugins(registry)] == ["a", "b"]


def test_a_site_plugin_that_cannot_start_is_left_out_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Fussy(FakeSitePlugin):
        name = "fussy"

        def __init__(self) -> None:
            raise RuntimeError("no config")

    registry = PluginRegistry([SITES])
    registry.register(SITES, Fussy)
    registry.register(SITES, FakeSitePlugin)

    assert [plugin.name for plugin in load_site_plugins(registry)] == ["fake"]
    assert "Could not start the fussy site plugin" in caplog.text
    assert "no config" in caplog.text
