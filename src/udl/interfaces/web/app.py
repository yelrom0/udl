"""
----------
- app.py -
----------

The web UI at /ui: the shell's build (`src/udl/interfaces/web/dist/`, from `make web`), each UI
plugin's bundle at /ui/plugins/<name>/, and / sending a browser there.

The build is package data, so an installed udl serves the shell it was built with; without one
(a checkout that has not run `make web`), /ui/ says to. The files need no API key, since they
hold no data, and none of these routes are operations: the OpenAPI schema is the API's.

The headers on every /ui answer keep the page to udl's own scripts and connections
(Content-Security-Policy), out of another site's frame, and out of another site's Referer; and
the caching: Vite puts a hash of a file's content in its name under /ui/assets/, so those are
cached forever, while everything else (index.html, a plugin's bundle, which is not hashed) is
revalidated each load, by its ETag.
"""

# System Imports
from pathlib import Path
from typing import Final

# Package Imports
from fastapi import FastAPI, Request
from starlette.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from starlette.routing import Route

# Local Imports
from udl.config import Settings

# The shell's build: where `make web` puts it (and where the package holds it once installed).
DIST: Final = Path(__file__).parent / "dist"

# A file whose name holds a hash of its content never changes: cache it for a year.
_IMMUTABLE: Final = "max-age=31536000, immutable"
# Everything else is revalidated: a new build (or bundle) is seen on the next load.
_NO_CACHE: Final = "no-cache"

HEADERS: Final[dict[str, str]] = {
    # (Styles are adopted stylesheets, so `style-src 'self'` holds; a plugin's are too.)
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; frame-ancestors 'none'",
    "Referrer-Policy": "same-origin",
    "X-Content-Type-Options": "nosniff",
}

NO_BUILD: Final = """\
<!doctype html>
<html lang="en">
	<head>
		<meta charset="utf-8" />
		<title>udl</title>
	</head>
	<body style="font-family: system-ui, sans-serif; margin: 3rem">
		<h1>udl</h1>
		<p>The web UI is not built.</p>
		<p>Run <code>make web</code>, then load this page again.</p>
	</body>
</html>
"""


def add_web_routes(app: FastAPI, settings: Settings, dist: Path | None = None) -> None:
    """Serve the web UI on `app` (its API routes are its own), from `dist` (the shell's build;
    the package's, unless a test gives another)."""
    built = DIST if dist is None else dist

    async def root(request: Request) -> Response:
        # (A browser's page is sent on; a script asking for JSON gets no page.)
        if "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/ui/", 307, headers=HEADERS)
        return PlainTextResponse("not found", 404, headers=HEADERS)

    async def asset(request: Request) -> Response:
        return _file(built / "assets", request.path_params["path"], cache=_IMMUTABLE)

    async def plugin_file(request: Request) -> Response:
        bundle = plugin_dir(settings, request.path_params["name"])
        if bundle is None:
            return PlainTextResponse("not found", 404, headers=HEADERS)
        return _file(
            bundle,
            request.path_params["path"],
            cache=_NO_CACHE,
            if_none_match=request.headers.get("if-none-match"),
        )

    async def shell(request: Request) -> Response:
        # (A file of the build that is not hashed — a favicon, the manifest — is answered; so is
        # any other path, with index.html, for the router's own routes.)
        relative = request.path_params["path"]
        if relative:
            answer = _file(built, relative, cache=_NO_CACHE)
            if answer.status_code == 200:
                return answer
        index = built / "index.html"
        if not index.is_file():
            return HTMLResponse(NO_BUILD, headers={**HEADERS, "Cache-Control": _NO_CACHE})
        return FileResponse(index, headers={**HEADERS, "Cache-Control": _NO_CACHE})

    for route in (
        Route("/", root, methods=["GET"]),
        Route("/ui/assets/{path:path}", asset, methods=["GET"]),
        Route("/ui/plugins/{name}/{path:path}", plugin_file, methods=["GET"]),
        Route("/ui/{path:path}", shell, methods=["GET"]),
    ):
        app.router.routes.append(route)


def plugin_dir(settings: Settings, name: str) -> Path | None:
    """A UI plugin's bundle by name: `<ui_plugin_dirs>/<name>`, when it holds a manifest.json.
    (18.7 makes the manifest's `name` the name, and adds the plugins' own `ui` and the `udl.ui`
    entry points to what is found.)"""
    if not name or name.startswith(".") or "/" in name or "\\" in name:
        return None
    for folder in settings.web.ui_plugin_dirs:
        bundle = folder / name
        if (bundle / "manifest.json").is_file():
            return bundle
    return None


def _file(root: Path, relative: str, *, cache: str, if_none_match: str | None = None) -> Response:
    """`relative` in `root` with its headers, a 304 when the client's ETag is still current, or
    a 404 when it is not there (or leads outside `root`)."""
    path = _inside(root, relative)
    if path is None or not path.is_file():
        return PlainTextResponse("not found", 404, headers=HEADERS)
    # (The stat is given, so the ETag is there before the answer is sent: it is what a plugin's
    # bundle is revalidated by.)
    answer = FileResponse(
        path, headers={**HEADERS, "Cache-Control": cache}, stat_result=path.stat()
    )
    etag = answer.headers["etag"]
    if if_none_match is not None and etag in {part.strip() for part in if_none_match.split(",")}:
        return Response(status_code=304, headers={**HEADERS, "Cache-Control": cache, "ETag": etag})
    return answer


def _inside(root: Path, relative: str) -> Path | None:
    """`relative` in `root`, or None when it leads outside it (a `..` in the path, or a symlink
    to elsewhere): a bundle's files are a plugin's, and only its own."""
    try:
        path = (root / relative).resolve()
        return path if path.is_relative_to(root.resolve()) else None
    except OSError:  # (a name too long, or a loop of symlinks)
        return None
