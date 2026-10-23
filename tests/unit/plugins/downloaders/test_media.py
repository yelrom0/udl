"""
----------------
- test_media.py -
----------------

What kind of media a URL or content type is
"""

# Package Imports
import pytest

# Local Imports
from udl.plugins.downloaders.base import MediaKind
from udl.plugins.downloaders.media import extension_for, media_type, stream_kind, url_name


@pytest.mark.parametrize(
    ("url", "name"),
    [
        ("https://example.com/media/clip.mp4", "clip.mp4"),
        ("https://example.com/caf%C3%A9.mp4?x=1#t=2", "café.mp4"),
        ("https://example.com/videos/", "videos"),
        ("https://example.com", ""),
    ],
)
def test_a_urls_name_is_the_last_part_of_its_path(url: str, name: str) -> None:
    assert url_name(url) == name


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://example.com/clip.mp4", "video/mp4"),
        ("https://example.com/song.MP3", "audio/mpeg"),
        ("https://example.com/photo.jpg?size=2", "image/jpeg"),
        ("https://example.com/page.html", None),
        ("https://example.com/report.pdf", None),
        ("https://example.com/live.m3u8", None),
        ("https://example.com/", None),
    ],
)
def test_a_media_extension_gives_a_media_type(url: str, kind: str | None) -> None:
    assert media_type(url) == kind


@pytest.mark.parametrize(
    ("url", "content_type", "kind"),
    [
        ("https://example.com/live.m3u8", None, MediaKind.HLS),
        ("https://example.com/manifest.mpd?token=1", None, MediaKind.DASH),
        ("https://example.com/live", "application/vnd.apple.mpegurl", MediaKind.HLS),
        ("https://example.com/live", "application/x-mpegURL; charset=utf-8", MediaKind.HLS),
        ("https://example.com/live", "application/dash+xml", MediaKind.DASH),
        ("https://example.com/live.m3u8", "text/plain", MediaKind.HLS),
        ("https://example.com/clip.mp4", "video/mp4", None),
        ("https://example.com/clip", None, None),
    ],
)
def test_a_stream_playlist_is_known_by_its_extension_or_type(
    url: str, content_type: str | None, kind: MediaKind | None
) -> None:
    assert stream_kind(url, content_type) is kind


@pytest.mark.parametrize(
    ("content_type", "extension"),
    [("video/mp4", ".mp4"), ("audio/mpeg", ".mp3"), ("application/x-udl-unknown", None)],
)
def test_a_type_has_an_extension(content_type: str, extension: str | None) -> None:
    assert extension_for(content_type) == extension
