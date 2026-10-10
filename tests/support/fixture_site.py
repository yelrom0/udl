"""
-------------------
- fixture_site.py -
-------------------

A small local website for integration tests: a direct file, a <video> page, an iframe embed,
a JavaScript-rendered page and a cookie-gated (401) page. The pages live in tests/fixtures/site.
"""

# System Imports
import mimetypes
from collections.abc import Callable, Generator
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


# The file served for each page, and whether it needs the session cookie.
_FILES: Final[dict[Page, tuple[str, bool]]] = {
    Page.DIRECT_FILE: ("media/sample.mp4", False),
    Page.VIDEO: ("video.html", False),
    Page.EMBED: ("embed.html", False),
    Page.PLAYER: ("player.html", False),
    Page.JS: ("js.html", False),
    Page.PRIVATE: ("private/video.html", True),
    Page.PRIVATE_FILE: ("media/sample.mp4", True),
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


def _login(request: Request) -> Response:
    response = Response("<p>Signed in</p>", mimetype="text/html")
    response.set_cookie(COOKIE_NAME, COOKIE_VALUE, httponly=True)
    return response
