"""
------------
- media.py -
------------

What kind of media a URL or a content type is: a media file (video, audio, image) or a stream
playlist (HLS, DASH). Guesses use mimetypes' built-in table only (not the system's), so they are
the same on every system.

Also Find: media found on a web page (by the scraper or the browser), and its handoff.
"""

# System Imports
import mimetypes
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Final
from urllib.parse import unquote, urlsplit

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.plugins.downloaders.base import Confidence, Cookie, Handoff, MediaCandidate, MediaKind

_MIME: Final = mimetypes.MimeTypes()
_MIME.add_type("application/dash+xml", ".mpd")

MEDIA_KINDS: Final = frozenset({"video", "audio", "image"})

_STREAMS: Final[dict[str, MediaKind]] = {
    "application/vnd.apple.mpegurl": MediaKind.HLS,
    "application/x-mpegurl": MediaKind.HLS,
    "audio/mpegurl": MediaKind.HLS,
    "audio/x-mpegurl": MediaKind.HLS,
    "application/dash+xml": MediaKind.DASH,
}


def url_name(url: str) -> str:
    """The last part of a URL's path (decoded), e.g. "clip.mp4"."""
    return PurePosixPath(unquote(urlsplit(url).path)).name


def media_type(url: str) -> str | None:
    """The media type a URL's extension gives, e.g. "video/mp4", if it is a media file's."""
    kind, _ = _MIME.guess_file_type(url_name(url))
    return kind if kind and kind.partition("/")[0] in MEDIA_KINDS else None


def stream_kind(url: str, content_type: str | None = None) -> MediaKind | None:
    """HLS or DASH when the URL's extension, or its content type, is a stream playlist's."""
    by_extension, _ = _MIME.guess_file_type(url_name(url))
    for kind in (content_type, by_extension):
        if stream := _STREAMS.get((kind or "").partition(";")[0].strip().lower()):
            return stream
    return None


def extension_for(content_type: str) -> str | None:
    """The usual extension for a content type, e.g. ".mp4"."""
    return _MIME.guess_extension(content_type)


class Find(BaseModel):
    """Media found on a web page, by a downloader that hands its download off."""

    model_config = ConfigDict(frozen=True)

    media: MediaCandidate
    confidence: Confidence
    referer: str  # the page (or frame) it is on

    def handoff(
        self, *, cookies: list[Cookie], headers: Mapping[str, str] | None = None
    ) -> Handoff:
        """Its download as if from its page: a file by http, a stream playlist by yt-dlp."""
        return Handoff(
            tool="http" if self.media.kind is MediaKind.FILE else "yt-dlp",
            url=self.media.url,
            headers={**(headers or {}), "Referer": self.referer},
            cookies=cookies,
        )


def unique(finds: Iterable[Find]) -> list[Find]:
    """`finds` with each URL once (the first time it was found)."""
    seen: set[str] = set()
    unique: list[Find] = []
    for find in finds:
        if find.media.url not in seen:
            seen.add(find.media.url)
            unique.append(find)
    return unique
