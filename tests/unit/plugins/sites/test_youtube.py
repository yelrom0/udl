"""
-------------------
- test_youtube.py -
-------------------

The youtube site plugin, udl's example: every form of a video's link becomes its watch URL, and
yt-dlp downloads them
"""

# Package Imports
import pytest

# Local Imports
from udl.core.urls import URL
from udl.db.models import StrategySource
from udl.plugins.downloaders.ytdlp import YtDlpDownloader
from udl.plugins.registry import SITES, PluginRegistry, PluginSource
from udl.plugins.sites.youtube import YouTubePlugin, watch_url

VIDEO = "dQw4w9WgXcQ"
WATCH = f"https://www.youtube.com/watch?v={VIDEO}"


@pytest.mark.parametrize(
    "link",
    [
        WATCH,
        f"https://youtube.com/watch?v={VIDEO}",
        f"https://m.youtube.com/watch?v={VIDEO}&feature=share",
        f"https://www.youtube.com/watch?feature=youtu.be&v={VIDEO}&t=42s&pp=ygUE",
        f"https://youtu.be/{VIDEO}",
        f"https://youtu.be/{VIDEO}?si=AbCdEfGh&t=10",
        f"https://www.youtube.com/shorts/{VIDEO}",
        f"https://youtube.com/shorts/{VIDEO}?feature=share",
        f"https://www.youtube.com/live/{VIDEO}?si=x",
        f"https://www.youtube.com/embed/{VIDEO}?autoplay=1",
        f"https://www.youtube-nocookie.com/embed/{VIDEO}",
        f"https://www.youtube.com/v/{VIDEO}",
    ],
)
def test_a_video_s_links_become_its_watch_url(link: str) -> None:
    assert watch_url(URL.parse(link)) == URL.parse(WATCH)


@pytest.mark.parametrize(
    "link",
    [
        f"https://www.youtube.com/watch?v={VIDEO}&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
        f"https://youtu.be/{VIDEO}?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG&index=2",
    ],
)
def test_a_video_s_link_in_a_playlist_keeps_the_playlist(link: str) -> None:
    assert watch_url(URL.parse(link)) == URL.parse(
        f"{WATCH}&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"
    )


@pytest.mark.parametrize(
    "link",
    [
        "https://www.youtube.com/",
        "https://www.youtube.com/@udl",
        "https://www.youtube.com/channel/UCuAXFkgsw1L7xaCfnd5JJOw",
        "https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
        "https://www.youtube.com/embed/videoseries?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch",
        f"https://music.youtube.com/watch?v={VIDEO}",
        "https://youtu.be/",
        f"https://youtu.be/{VIDEO}/extra",
    ],
)
def test_other_links_are_not_a_video_s(link: str) -> None:
    assert watch_url(URL.parse(link)) is None


async def test_the_plugin_downloads_a_video_from_its_watch_url_and_leaves_other_links() -> None:
    plugin = YouTubePlugin()
    channel = URL.parse("https://www.youtube.com/@udl")

    assert await plugin.transform_urls(URL.parse(f"https://youtu.be/{VIDEO}")) == [URL.parse(WATCH)]
    assert await plugin.transform_urls(channel) == [channel]


@pytest.mark.parametrize(
    "link",
    [
        WATCH,
        f"https://youtu.be/{VIDEO}",
        "https://music.youtube.com/",
        "https://www.youtube.com/@a",
    ],
)
def test_yt_dlp_downloads_everything_on_youtube(link: str) -> None:
    url = URL.parse(link)

    strategy = YouTubePlugin().strategy(url)

    assert strategy is not None
    assert (strategy.site_key, strategy.tool, strategy.source) == (
        url.site_key,
        "yt-dlp",
        StrategySource.PLUGIN,
    )
    assert (strategy.options, strategy.chain) == ({}, None)


def test_yt_dlp_recognises_the_watch_url_offline() -> None:
    match = YtDlpDownloader().match(WATCH)

    assert match is not None
    assert match.detail == "Youtube"


def test_the_plugin_is_one_of_udl_s_own() -> None:
    registry = PluginRegistry.discover()

    assert registry.get(SITES, "youtube") is YouTubePlugin
    [info] = registry.plugins(SITES)
    assert info.source is PluginSource.BUILTIN
