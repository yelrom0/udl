"""
------------
- media.py -
------------

What kind of media a URL or a content type is: a media file (video, audio, image) or a stream
playlist (HLS, DASH). Guesses use mimetypes' built-in table only (not the system's), so they are
the same on every system.
"""

# System Imports
import mimetypes
from pathlib import PurePosixPath
from typing import Final
from urllib.parse import unquote, urlsplit

# Local Imports
from udl.plugins.downloaders.base import MediaKind

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
