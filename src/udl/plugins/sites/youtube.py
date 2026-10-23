"""
--------------
- youtube.py -
--------------

YouTubePlugin: udl's example site plugin.

A YouTube video is linked in many forms: youtu.be short links, shorts, live streams, embeds (also
from youtube-nocookie.com), the mobile site, and share and tracking parameters. The plugin turns
each into the video's watch URL (keeping the playlist it was linked from), so the same video is
downloaded once however it was linked, and has yt-dlp, which handles YouTube, download it.
"""

# System Imports
import re
from typing import Final
from urllib.parse import parse_qs, urlencode, urlsplit

# Local Imports
from udl.core.urls import URL
from udl.db.models import Strategy, StrategySource
from udl.plugins.sites.base import SitePlugin

_VIDEO_ID: Final = re.compile(r"[A-Za-z0-9_-]{11}")
# The paths that show one video, by its id: /shorts/<id>, /live/<id>, /embed/<id>, /v/<id>.
_VIDEO_PATH: Final = re.compile(r"/(?:shorts|live|embed|v)/([^/]+)/?")
_PLAYLIST_EMBED: Final[str] = "videoseries"  # (/embed/videoseries?list=… is a playlist's player)


def _video_id(host: str, path: str, query: dict[str, list[str]]) -> str | None:
    """The id of the video a link on `host` is to, if it is to one."""
    if host == "youtu.be":
        return path.removeprefix("/").removesuffix("/")
    if host not in ("youtube.com", "youtube-nocookie.com"):
        return None  # (e.g. YouTube Music, whose watch pages yt-dlp reads as they are)
    if path == "/watch":
        return query.get("v", [None])[0]
    found = _VIDEO_PATH.fullmatch(path)
    return found[1] if found else None


def watch_url(url: URL) -> URL | None:
    """The watch URL of the video `url` links to (with the playlist it was linked from), or None
    when it links to no single video (a channel, a playlist, YouTube Music)."""
    parts = urlsplit(url.value)
    host = url.host.removeprefix("www.").removeprefix("m.")
    query = parse_qs(parts.query)
    video = _video_id(host, parts.path, query)
    if video is None or not _VIDEO_ID.fullmatch(video) or video == _PLAYLIST_EMBED:
        return None
    params = {"v": video}
    if playlist := query.get("list"):
        params["list"] = playlist[0]
    return URL.parse(f"https://www.youtube.com/watch?{urlencode(params)}")


class YouTubePlugin(SitePlugin):
    """YouTube: a video's links become its watch URL, and yt-dlp downloads everything."""

    name = "youtube"
    domains = ("youtube.com", "youtu.be", "youtube-nocookie.com")

    async def transform_urls(self, url: URL) -> list[URL]:
        return [watch_url(url) or url]

    def strategy(self, url: URL) -> Strategy | None:
        return Strategy(site_key=url.site_key, tool="yt-dlp", source=StrategySource.PLUGIN)
