"""
-------------------
- fixture_site.py -
-------------------

A small local website for integration tests: a direct file, a <video> page, an iframe embed,
a JavaScript-rendered page, a cookie-gated (401) page, a page without media, a page with two
videos, an image gallery, a file that downloads slowly (to cancel), a page with its video in
JSON and one whose video loads on a click (for tag chains). The pages live in
tests/fixtures/site.
"""

# System Imports
import mimetypes
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

# Package Imports
from pytest_httpserver import HTTPServer
from werkzeug import Request, Response

SITE_DIR: Final[Path] = Path(__file__).parent.parent / "fixtures" / "site"
COOKIE_NAME: Final[str] = "udl_session"
COOKIE_VALUE: Final[str] = "signed-in"

# The slow file: SLOW_SIZE bytes, sent SLOW_CHUNK bytes at a time every SLOW_DELAY seconds
# (about 13 s in all), so a test can cancel its download long before it finishes.
SLOW_SIZE: Final[int] = 8 * 1024 * 1024
SLOW_CHUNK: Final[int] = 32 * 1024
SLOW_DELAY: Final[float] = 0.05


class Page(StrEnum):
    """The site's paths."""

    DIRECT_FILE = "/media/sample.mp4"  # a plain MP4 (supports HEAD and Range requests)
    VIDEO = "/video.html"  # <video><source> pointing at DIRECT_FILE
    EMBED = "/embed.html"  # <iframe id="player"> showing PLAYER (relative URL)
    PLAYER = "/player.html"  # the embedded player: <video><source> (relative URL)
    JS = "/js.html"  # builds its <video> in JavaScript; the static HTML has no media URL
    PRIVATE = "/private/video.html"  # 401 without the session cookie, else a <video> page
    PRIVATE_FILE = "/private/sample.mp4"  # 401 without the session cookie
    LOGIN = "/login"  # sets the session cookie
    NO_MEDIA = "/text.html"  # a page with nothing to download
    SLOW_FILE = "/media/slow.mp4"  # an MP4 that takes many seconds to download
    SLOW = "/slow.html"  # <video><source> pointing at SLOW_FILE
    OTHER_FILE = "/media/other.mp4"  # a second MP4 (the same bytes as DIRECT_FILE)
    VIDEOS = "/videos.html"  # two <video>s: DIRECT_FILE, then OTHER_FILE
    IMAGE = "/media/pixel.png"  # a small PNG
    GALLERY = "/gallery.html"  # two <img>s: IMAGE, and one that is not found
    JSON = "/json.html"  # DIRECT_FILE's relative URL in <script id="data" type="application/json">
    CLICK = "/click.html"  # clicking <button id="play"> makes a <video> of DIRECT_FILE, which loads


# The file served for each page, and whether it needs the session cookie.
_FILES: Final[dict[Page, tuple[str, bool]]] = {
    Page.DIRECT_FILE: ("media/sample.mp4", False),
    Page.VIDEO: ("video.html", False),
    Page.EMBED: ("embed.html", False),
    Page.PLAYER: ("player.html", False),
    Page.JS: ("js.html", False),
    Page.PRIVATE: ("private/video.html", True),
    Page.PRIVATE_FILE: ("media/sample.mp4", True),
    Page.NO_MEDIA: ("text.html", False),
    Page.SLOW: ("slow.html", False),
    Page.OTHER_FILE: ("media/sample.mp4", False),
    Page.VIDEOS: ("videos.html", False),
    Page.IMAGE: ("media/pixel.png", False),
    Page.GALLERY: ("gallery.html", False),
    Page.JSON: ("json.html", False),
    Page.CLICK: ("click.html", False),
}


class FixtureSite:
    """The fixture site, served by a running local HTTP server. Unknown paths are 404."""

    def __init__(self, server: HTTPServer) -> None:
        self._server = server

    @classmethod
    @contextmanager
    def serve(cls) -> Generator[Self]:
        server = HTTPServer(threaded=True)
        server.no_handler_status_code = 404
        for page, (name, private) in _FILES.items():
            handler = _serve_file(SITE_DIR / name, private=private)
            server.expect_request(page).respond_with_handler(handler)
        server.expect_request(Page.SLOW_FILE).respond_with_handler(_serve_slow)
        server.expect_request(Page.LOGIN).respond_with_handler(_login)
        server.start()
        try:
            yield cls(server)
        finally:
            server.stop()

    def url(self, path: str) -> str:
        """The absolute URL of a page (or of any path, e.g. one that is not found)."""
        return self._server.url_for(path)

    @staticmethod
    def content(page: Page) -> bytes:
        """The exact bytes served for a page."""
        if page is Page.SLOW_FILE:
            return _slow_content()
        return (SITE_DIR / _FILES[page][0]).read_bytes()


def _serve_file(path: Path, *, private: bool) -> Callable[[Request], Response]:
    data = path.read_bytes()
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    def handler(request: Request) -> Response:
        if private and request.cookies.get(COOKIE_NAME) != COOKIE_VALUE:
            return Response("Sign in required", status=401, mimetype="text/plain")
        response = Response(data, mimetype=mimetype)
        return response.make_conditional(request, accept_ranges=True, complete_length=len(data))

    return handler


def _slow_content() -> bytes:
    """The sample MP4, padded out to SLOW_SIZE bytes."""
    sample = (SITE_DIR / "media" / "sample.mp4").read_bytes()
    return sample + bytes(SLOW_SIZE - len(sample))


def _serve_slow(_: Request) -> Response:
    data = _slow_content()

    def trickle() -> Iterator[bytes]:
        for start in range(0, len(data), SLOW_CHUNK):
            yield data[start : start + SLOW_CHUNK]
            time.sleep(SLOW_DELAY)

    response = Response(trickle(), mimetype="video/mp4", direct_passthrough=True)
    response.content_length = len(data)
    return response


def _login(request: Request) -> Response:
    response = Response("<p>Signed in</p>", mimetype="text/html")
    response.set_cookie(COOKIE_NAME, COOKIE_VALUE, httponly=True, max_age=3600)
    return response
