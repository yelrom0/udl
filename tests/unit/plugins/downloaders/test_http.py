"""
---------------
- test_http.py -
---------------

The http downloader's offline parts: which URLs look like files, and what each backend needs
"""

# Package Imports
import pytest

# Local Imports
from udl.plugins.downloaders.base import Requirement
from udl.plugins.downloaders.http import HttpBackend, HttpDownloader, HttpOptions


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/media/clip.mp4",
        "https://example.com/a/b/song.MP3",
        "https://example.com/photo.jpeg?size=large",
        "https://example.com/film.mkv#t=10",
        "http://localhost:8080/sound.flac",
    ],
)
def test_a_url_with_a_media_extension_matches(url: str) -> None:
    match = HttpDownloader().match(url)

    assert match is not None
    assert 0.5 <= match.confidence < 0.9


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/watch?v=clip.mp4",  # the extension is in the query, not the path
        "https://example.com/video.html",
        "https://example.com/videos/",
        "https://example.com/",
        "https://example.com/live.m3u8",  # a stream playlist: yt-dlp's
        "https://example.com/manifest.mpd",
    ],
)
def test_other_urls_do_not_match(url: str) -> None:
    assert HttpDownloader().match(url) is None


@pytest.mark.parametrize(
    ("backend", "needs"),
    [
        (HttpBackend.HTTPX, [Requirement.module("httpx")]),
        (HttpBackend.CURL, [Requirement.binary("curl")]),
        (HttpBackend.WGET, [Requirement.binary("wget")]),
    ],
)
def test_each_backend_needs_its_program(backend: HttpBackend, needs: list[Requirement]) -> None:
    requirements = HttpDownloader(HttpOptions(backend=backend)).requirements()

    assert [(r.kind, r.name) for r in requirements] == [(r.kind, r.name) for r in needs]
