"""
--------------
- antibot.py -
--------------

AntiBotSite: a local website that behaves like the sites the page fetcher (udl.fetch) is for,
served on a port (Chromium has to reach it) under several host names, so tests have several
sites. Each path behaves as a kind of site does:

- /plain-500/...: a 500 to a client that runs no JavaScript; a browser runs the page's script,
  which sets a cookie and reloads, and gets the page;
- /flaky/...: a 500 for the first `flaky` requests of each path, then the page;
- /checked/...: an interstitial with Cloudflare's markers (a 403, cf-mitigated: challenge, "Just
  a moment...") and an "I am human" button. Pressing it sets `clearance`, bound to the
  browser's User-Agent, which every page under /checked/ then needs: the same cookie with
  another User-Agent is challenged again, as cf_clearance is;
- /auto-checked/...: the same check, but its script presses the button itself after a moment (a
  check that clears itself in a browser);
- /blocked: a 403 "Access Denied" with nothing to pass (as Akamai's);
- /slow-down/...: a 429 with a Retry-After for the first `slow_down` requests of each path;
- /built-by-js: an empty shell that a script fills in;
- /members/...: a login wall (401) that the session cookie gets past; with `rotate_sessions`, each
  page it gives sets a new session and the old one stops working;
- /docs, /article, /data.json, /notes.txt, /report.pdf, /cp1252 (its charset in the header) and
  /cp1252-meta (only in a <meta>): content (tests/fixtures/antibot).

`requests` records what each client sent (its User-Agent, its cookies, and whether it ran the
page's JavaScript: every HTML page reports back that it did), so a test can say what was tried.
FakePerson stands in for the user: given a tab on a check, it presses the button (or doesn't,
or closes the window).
"""

# System Imports
import asyncio
import contextlib
import json
import secrets
import socket
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

# Package Imports
import uvicorn
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

CONTENT_DIR: Final = Path(__file__).parent.parent / "fixtures" / "antibot"
HOSTS: Final = ("a.localhost", "b.localhost", "c.localhost")
"""The site's host names: each is a site of its own (its own site key and cookies)."""

JS_COOKIE: Final = "js_ok"  # (what /plain-500/'s script sets)
CLEARANCE: Final = "clearance"  # (what passing /checked/ sets)
AUTO_CLEARANCE: Final = "auto_clearance"  # (what passing /auto-checked/ sets)
SESSION: Final = "session"  # (what gets past /members/)
CHALLENGE_TITLE: Final = "Just a moment..."
AUTO_DELAY_MS: Final = 800  # (how long /auto-checked/'s script takes to press its button)

_CONTENT: Final[dict[str, tuple[str, str]]] = {
    "/docs": ("docs.html", "text/html; charset=utf-8"),
    "/article": ("article.html", "text/html; charset=utf-8"),
    "/data.json": ("data.json", "application/json"),
    "/notes.txt": ("notes.txt", "text/plain; charset=utf-8"),
    "/report.pdf": ("report.pdf", "application/pdf"),
    "/cp1252": ("cp1252.html", "text/html; charset=windows-1252"),
    "/cp1252-meta": ("cp1252-meta.html", "text/html"),
}


@dataclass
class Visit:
    """A request the site answered."""

    number: int
    host: str
    path: str
    status: int
    user_agent: str
    cookies: dict[str, str]
    js: bool = False  # whether the page it was given ran its JavaScript (and said so)


@dataclass
class _Clearances:
    """The clearances a check gave: each cookie's value, and the User-Agent it is bound to."""

    by_token: dict[str, str] = field(default_factory=dict[str, str])

    def issue(self, user_agent: str) -> str:
        token = secrets.token_hex(8)
        self.by_token[token] = user_agent
        return token

    def valid(self, token: str | None, user_agent: str) -> bool:
        return token is not None and self.by_token.get(token) == user_agent


class AntiBotSite:
    """The site, as an ASGI app (app()), and what it saw and gave."""

    def __init__(self) -> None:
        self.requests: list[Visit] = []
        self.flaky = 2
        """How many of each path's first requests /flaky/ answers with a 500."""
        self.slow_down = 1
        """How many of each path's first requests /slow-down/ answers with a 429."""
        self.retry_after = 1
        """The Retry-After (seconds) /slow-down/ answers with."""
        self.rotate_sessions = False
        """Whether each /members/ page sets a new session (the one it was sent stops working)."""
        self.port = 0
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, str, str], int] = {}
        self._clearances = _Clearances()
        self._auto_clearances = _Clearances()
        self._sessions: set[str] = set()

    # -- what tests ask ------------------------------------------------------------------------

    def url(self, path: str, host: str = HOSTS[0]) -> str:
        """The URL of `path` on one of the site's hosts."""
        return f"http://{host}:{self.port}{path}"

    def visits(self, path: str | None = None, host: str | None = None) -> list[Visit]:
        """The requests the site answered (of `path` on `host`), in order."""
        with self._lock:
            return [
                visit
                for visit in self.requests
                if (path is None or visit.path == path) and (host is None or visit.host == host)
            ]

    def issue_session(self) -> str:
        """A session that gets past /members/ (as signing in would give one)."""
        token = secrets.token_hex(8)
        with self._lock:
            self._sessions.add(token)
        return token

    def cleared(self, user_agent: str) -> str:
        """A /checked/ clearance bound to `user_agent` (as passing the check in that browser
        would give one)."""
        with self._lock:
            return self._clearances.issue(user_agent)

    @staticmethod
    def content(path: str) -> bytes:
        """The exact bytes a content path gives."""
        return (CONTENT_DIR / _CONTENT[path][0]).read_bytes()

    # -- serving -------------------------------------------------------------------------------

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/__js/{number:int}", self._ran_js, methods=["GET", "POST"]),
                Route("/plain-500/{rest:path}", self._plain_500),
                Route("/flaky/{rest:path}", self._flaky),
                Route("/checked/__clear", self._clear, methods=["POST"]),
                Route("/checked/{rest:path}", self._checked),
                Route("/auto-checked/__clear", self._auto_clear, methods=["POST"]),
                Route("/auto-checked/{rest:path}", self._auto_checked),
                Route("/cdn-cgi/challenge-platform/{rest:path}", self._challenge_script),
                Route("/blocked", self._blocked),
                Route("/slow-down/{rest:path}", self._slow_down),
                Route("/built-by-js", self._built_by_js),
                Route("/members/{rest:path}", self._members),
                *(Route(path, self._content) for path in _CONTENT),
            ]
        )

    @classmethod
    @contextlib.contextmanager
    def serve(cls) -> Generator[Self]:
        """The site, served on a free port of 127.0.0.1 and ::1 (where its host names lead)
        while the block runs."""
        site = cls()
        sockets = _loopback_sockets()
        site.port = sockets[0].getsockname()[1]
        config = uvicorn.Config(
            site.app(),
            log_level="warning",
            lifespan="off",
            ws="none",
            http="h11",
            server_header=False,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": sockets}, daemon=True)
        thread.start()
        while not server.started:
            if not thread.is_alive():
                raise RuntimeError("The anti-bot site did not start")
            threading.Event().wait(0.01)
        try:
            yield site
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            for opened in sockets:
                opened.close()

    def reset(self) -> None:
        """Forget what was asked and given (between tests sharing the site)."""
        with self._lock:
            self.requests.clear()
            self._counts.clear()
            self._clearances = _Clearances()
            self._auto_clearances = _Clearances()
            self._sessions.clear()
        self.flaky, self.slow_down, self.retry_after, self.rotate_sessions = 2, 1, 1, False

    # -- the paths -----------------------------------------------------------------------------

    def _visit(self, request: Request, status: int) -> Visit:
        with self._lock:
            visit = Visit(
                number=len(self.requests),
                host=request.url.hostname or "",
                path=request.url.path,
                status=status,
                user_agent=request.headers.get("user-agent", ""),
                cookies=dict(request.cookies),
            )
            self.requests.append(visit)
        return visit

    def _count(self, request: Request, kind: str) -> int:
        """How many requests of this path came before this one."""
        key = (kind, request.url.hostname or "", request.url.path)
        with self._lock:
            seen = self._counts.get(key, 0)
            self._counts[key] = seen + 1
        return seen

    def _html(self, request: Request, body: str, *, status: int = 200, **headers: str) -> Response:
        visit = self._visit(request, status)
        # (Every page reports that it ran its JavaScript.)
        script = f'<script>fetch("/__js/{visit.number}", {{method: "POST"}})</script>'
        page = body.replace("</body>", f"{script}</body>") if "</body>" in body else body + script
        return Response(
            page,
            status,
            headers={key.replace("_", "-"): value for key, value in headers.items()},
            media_type="text/html; charset=utf-8",
        )

    async def _ran_js(self, request: Request) -> Response:
        number = int(request.path_params["number"])
        with self._lock:
            if 0 <= number < len(self.requests):
                self.requests[number].js = True
        return Response(status_code=204)

    async def _plain_500(self, request: Request) -> Response:
        if request.cookies.get(JS_COOKIE) == "1":
            return self._html(request, _page(f"Plain {request.path_params['rest']}", "Read."))
        reloads = f'<script>document.cookie = "{JS_COOKIE}=1; path=/"; location.reload();</script>'
        return self._html(request, _page("Error", "Something went wrong.", reloads), status=500)

    async def _flaky(self, request: Request) -> Response:
        if self._count(request, "flaky") < self.flaky:
            return self._html(request, _page("Error", "Try again."), status=500)
        return self._html(request, _page(f"Flaky {request.path_params['rest']}", "At last."))

    async def _checked(self, request: Request) -> Response:
        user_agent = request.headers.get("user-agent", "")
        if self._clearances.valid(request.cookies.get(CLEARANCE), user_agent):
            rest = request.path_params["rest"]
            return self._html(
                request, _page(f"Checked {rest}", f"The page behind the check: {rest}.")
            )
        return self._html(
            request, _interstitial("/checked/__clear"), status=403, **_CLOUDFLARE_HEADERS
        )

    async def _clear(self, request: Request) -> Response:
        token = self.cleared(request.headers.get("user-agent", ""))
        response = Response(status_code=204)
        response.set_cookie(CLEARANCE, token, path="/", httponly=True)
        return response

    async def _auto_checked(self, request: Request) -> Response:
        user_agent = request.headers.get("user-agent", "")
        if self._auto_clearances.valid(request.cookies.get(AUTO_CLEARANCE), user_agent):
            rest = request.path_params["rest"]
            return self._html(request, _page(f"Auto-checked {rest}", "Cleared by itself."))
        presses = f"<script>setTimeout(pass, {AUTO_DELAY_MS});</script>"
        return self._html(
            request,
            _interstitial("/auto-checked/__clear", presses),
            status=403,
            **_CLOUDFLARE_HEADERS,
        )

    async def _auto_clear(self, request: Request) -> Response:
        with self._lock:
            token = self._auto_clearances.issue(request.headers.get("user-agent", ""))
        response = Response(status_code=204)
        response.set_cookie(AUTO_CLEARANCE, token, path="/", httponly=True)
        return response

    async def _challenge_script(self, request: Request) -> Response:
        return Response("/* the check's script */", media_type="application/javascript")

    async def _blocked(self, request: Request) -> Response:
        body = (
            "<!doctype html><html><head><title>Access Denied</title></head><body>"
            "<h1>Access Denied</h1><p>You don't have permission to access this page on this "
            "server.</p><p>Reference #18.4c1e3b17.1761300000.2a9f7d3</p></body></html>"
        )
        return self._html(request, body, status=403, server="AkamaiGHost")

    async def _slow_down(self, request: Request) -> Response:
        if self._count(request, "slow-down") < self.slow_down:
            return self._html(
                request,
                _page("Too many requests", "Slow down."),
                status=429,
                retry_after=str(self.retry_after),
            )
        return self._html(request, _page(f"Slow {request.path_params['rest']}", "Thank you."))

    async def _built_by_js(self, request: Request) -> Response:
        body = (
            "<!doctype html><html><head><title>Loading</title>"
            '<script src="/bundle.js"></script></head><body><div id="app"></div>'
            "<noscript>You need to enable JavaScript to run this app.</noscript>"
            "<script>document.title = 'Built'; document.getElementById('app').innerHTML = "
            "'<h1>Built by JavaScript</h1><p>Nothing of this page is in its HTML: a script "
            "made all of it, once the browser ran it.</p>';</script></body></html>"
        )
        return self._html(request, body)

    async def _members(self, request: Request) -> Response:
        token = request.cookies.get(SESSION)
        with self._lock:
            signed_in = token is not None and token in self._sessions
        if not signed_in:
            return self._html(request, _page("Sign in", "Sign in to see this page."), status=401)
        rest = request.path_params["rest"]
        response = self._html(request, _page(f"Members {rest}", f"For members only: {rest}."))
        if self.rotate_sessions:
            fresh = self.issue_session()
            with self._lock:
                self._sessions.discard(token or "")
            response.set_cookie(SESSION, fresh, path="/", httponly=True)
        return response

    async def _content(self, request: Request) -> Response:
        name, media_type = _CONTENT[request.url.path]
        data = (CONTENT_DIR / name).read_bytes()
        if media_type.startswith("text/html"):
            visit = self._visit(request, 200)
            script = f'<script>fetch("/__js/{visit.number}", {{method: "POST"}})</script>'.encode()
            data = data.replace(b"</body>", script + b"</body>")
            return Response(data, headers={"content-type": media_type})
        self._visit(request, 200)
        if media_type == "application/json":
            return JSONResponse(json.loads(data))
        return Response(data, headers={"content-type": media_type})


_CLOUDFLARE_HEADERS: Final = {"cf_mitigated": "challenge", "server": "cloudflare"}


def _page(title: str, text: str, extra: str = "") -> str:
    return (
        f"<!doctype html><html><head><title>{title}</title></head><body><main><h1>{title}</h1>"
        f"<p>{text}</p></main>{extra}</body></html>"
    )


def _interstitial(clear: str, extra: str = "") -> str:
    """A page like Cloudflare's check: pressing its button clears it, and reloads the page."""
    return (
        f"<!doctype html><html><head><title>{CHALLENGE_TITLE}</title>"
        '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
        '</head><body><div id="challenge-stage"><h1>Checking your browser</h1>'
        '<button id="human">I am human</button></div>'
        f"<script>async function pass() {{ await fetch('{clear}', {{method: 'POST'}}); "
        "location.reload(); }\n"
        "document.getElementById('human').addEventListener('click', pass);</script>"
        f"{extra}</body></html>"
    )


def _loopback_sockets() -> list[socket.socket]:
    """Listening sockets on one free port of 127.0.0.1 and of ::1."""
    for _ in range(20):
        ipv4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ipv4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        ipv4.bind(("127.0.0.1", 0))
        ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        ipv6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            ipv6.bind(("::1", ipv4.getsockname()[1]))
        except OSError:  # (taken on ::1: another port)
            ipv4.close()
            ipv6.close()
            continue
        for opened in (ipv4, ipv6):
            opened.listen(64)
            opened.setblocking(False)
        return [ipv4, ipv6]
    raise RuntimeError("No port is free on both 127.0.0.1 and ::1")


# ---------------------------------------------------------------------------------------------
# The person
# ---------------------------------------------------------------------------------------------


class Manner(StrEnum):
    """What FakePerson does with a check."""

    PASSES = "passes"  # presses its button
    IGNORES = "ignores"  # leaves it
    CLOSES = "closes"  # closes the window


class FakePerson:
    """Stands in for the user at a check: given a tab on one, presses its "I am human" button
    (through Playwright, as a person's click), leaves it, or closes the window. `attended`
    lists the URLs of the tabs it was given."""

    def __init__(self, manner: Manner = Manner.PASSES) -> None:
        self.manner = manner
        self.attended: list[str] = []

    async def attend(self, tab: Page) -> None:
        self.attended.append(tab.url)
        if self.manner is Manner.IGNORES:
            return
        if self.manner is Manner.CLOSES:
            with contextlib.suppress(PlaywrightError):
                await tab.context.close()
            return
        with contextlib.suppress(PlaywrightError):  # (a tab that was passed, or closed, meanwhile)
            button = tab.locator("#human")
            await button.wait_for(timeout=10_000)
            await asyncio.sleep(0.1)  # (as a person looks before clicking)
            await button.click(timeout=10_000)
