"""
------------------
- sessions.py -
------------------

Web sessions: signing in with the API key gives a browser a cookie (a 256-bit token, kept in
this process) that the API accepts in place of the header, so that a page can call it and an
`EventSource` can stream from it, which cannot send a header of its own.

The cookie alone must not let another site drive udl: a write that comes with the cookie also
needs the CSRF header, and a write whose Origin names another origin is refused, with the key or
with the cookie. The MCP server takes the key alone: no page speaks MCP with a cookie.

Sessions live in memory: restarting `udl serve` means signing in again.
"""

# System Imports
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

# Package Imports
from fastapi import FastAPI
from pydantic import BaseModel, SecretStr
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

# Local Imports
from udl.config import Settings
from udl.errors import NotAllowed, RateLimited, Unauthorized
from udl.interfaces.security import API_KEY_HEADER, check_api_key

# The cookie the browser keeps, and the header a script must add to a write that uses it.
SESSION_COOKIE: Final = "udl_session"
CSRF_HEADER: Final = "X-UDL-CSRF"
CSRF_VALUE: Final = "1"

# A write, which another site's page must not be able to make udl do.
WRITES: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# How many sign-ins one client may try in a minute (a wrong key is a guess at the key).
ATTEMPTS: Final = 10
ATTEMPT_WINDOW: Final = timedelta(minutes=1)


class SignInRequest(BaseModel):
    """What a browser sends to sign in: the API key the user typed."""

    api_key: SecretStr


class SessionStore:
    """Live sessions: a token to the moment it expires. (In memory: this process's.)"""

    def __init__(
        self,
        ttl: timedelta,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.ttl = ttl
        self._now = now
        self._expires: dict[str, datetime] = {}

    def sign_in(self) -> str:
        """A new token, for a browser that gave the key (and the expired ones are swept)."""
        self._sweep()
        token = secrets.token_urlsafe(32)  # (256 bits)
        self._expires[token] = self._now() + self.ttl
        return token

    def is_live(self, token: str | None) -> bool:
        """Whether `token` is a session that has not expired (which drops it when it has)."""
        if not token:
            return False
        expires = self._expires.get(token)
        if expires is None:
            return False
        if expires <= self._now():
            del self._expires[token]
            return False
        return True

    def sign_out(self, token: str | None) -> None:
        """End a session (an unknown token is nothing to do)."""
        if token:
            self._expires.pop(token, None)

    @property
    def live(self) -> int:
        """How many sessions have not expired (the expired ones are dropped first)."""
        self._sweep()
        return len(self._expires)

    def __len__(self) -> int:
        """How many tokens it holds (the expired ones too, until something sweeps them)."""
        return len(self._expires)

    def _sweep(self) -> None:
        """Drop the sessions that have expired (so a browser that never comes back is not kept)."""
        now = self._now()
        for token, expires in list(self._expires.items()):
            if expires <= now:
                del self._expires[token]


class Attempts:
    """How many sign-ins a client may try in a window (by the address it comes from)."""

    def __init__(
        self,
        limit: int = ATTEMPTS,
        window: timedelta = ATTEMPT_WINDOW,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.limit = limit
        self.window = window
        self._now = now
        self._tries: dict[str, list[datetime]] = {}

    def allowed(self, client: str) -> bool:
        """Whether `client` may try another sign-in now."""
        return len(self._recent(client)) < self.limit

    def record(self, client: str) -> None:
        """Count a try by `client`."""
        self._tries[client] = [*self._recent(client), self._now()]

    def _recent(self, client: str) -> list[datetime]:
        since = self._now() - self.window
        recent = [at for at in self._tries.get(client, []) if at > since]
        if recent:
            self._tries[client] = recent
        else:
            self._tries.pop(client, None)
        return recent


def session_store(settings: Settings) -> SessionStore:
    """The store for `settings`: a session lives [web] session_hours."""
    return SessionStore(timedelta(hours=settings.web.session_hours))


def add_session_routes(app: FastAPI, settings: Settings) -> None:
    """`POST /session` (sign in) and `DELETE /session` (sign out) on `app`.

    (Starlette routes rather than operations: signing in cannot need the key it is given, and
    these are the page's, not the API's — so they are out of the schema and out of the key's
    dependency.)"""
    store: SessionStore = app.state.sessions
    attempts = Attempts()
    secure = _is_https(settings)

    async def sign_in(request: Request) -> Response:
        client = request.client.host if request.client else "unknown"
        if not attempts.allowed(client):
            raise RateLimited(
                "Too many sign-in attempts: try again in a minute",
                hint="A wrong API key is a guess at it, so udl slows the guesses down.",
            )
        attempts.record(client)
        try:
            given = SignInRequest.model_validate_json(await request.body()).api_key
        except ValueError:
            return JSONResponse({"detail": 'A sign-in is {"api_key": "..."}'}, 400)
        check_api_key(settings, given.get_secret_value())
        # (Without a key the server is open: there is nothing to sign in to, so no cookie.)
        if settings.api.api_key is None:
            return Response(status_code=204)
        answer = Response(status_code=204)
        answer.set_cookie(
            SESSION_COOKIE,
            store.sign_in(),
            max_age=int(store.ttl.total_seconds()),
            httponly=True,
            samesite="strict",
            secure=secure,
            path="/",
        )
        return answer

    async def sign_out(request: Request) -> Response:
        store.sign_out(request.cookies.get(SESSION_COOKIE))
        answer = Response(status_code=204)
        answer.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        return answer

    for route in (
        Route("/session", sign_in, methods=["POST"]),
        Route("/session", sign_out, methods=["DELETE"]),
    ):
        app.router.routes.insert(0, route)


def authenticate(settings: Settings, request: Request, *, given: str | None = None) -> None:
    """Who a request is: the API key, or a live session's cookie — and a write needs the CSRF
    header with the cookie. A write from another origin is refused, whoever it is."""
    check_write_origin(settings, request.method, request.headers)
    given = given if given is not None else request.headers.get(API_KEY_HEADER)
    if given is not None:
        check_api_key(settings, given)
        return
    if settings.api.api_key is None:
        return  # (no key: the server is open, as it was before the UI)
    store: SessionStore = request.app.state.sessions
    if not store.is_live(request.cookies.get(SESSION_COOKIE)):
        raise Unauthorized(
            f"This udl server needs its API key, in the {API_KEY_HEADER} header, or a session",
            hint="Sign in at /ui/login, or send the key as api.api_key has it.",
        )
    if request.method in WRITES and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise NotAllowed(
            f"A write with a session's cookie also needs {CSRF_HEADER}: {CSRF_VALUE}",
            hint="The cookie alone is another site's to send, so a script says it is udl's own.",
        )


def check_write_origin(settings: Settings, method: str, headers: Mapping[str, str]) -> None:
    """Refuse a write whose `Origin` names another origin: a browser's page from elsewhere."""
    if method not in WRITES:
        return
    origin = headers.get("origin")
    if origin is None:
        return
    host = origin.split("://", 1)[-1].split("/", 1)[0]
    ours = {headers.get("host")}
    if settings.web.public_url:
        ours.add(settings.web.public_url.split("://", 1)[-1].split("/", 1)[0])
    if host not in ours:
        raise NotAllowed(
            f"A write from {origin} is refused: only udl's own pages may change it",
            hint="Sign in at /ui/login on this server's own address.",
        )


def _is_https(settings: Settings) -> bool:
    """Whether the UI is reached over HTTPS (then the cookie may not travel over plain HTTP)."""
    return bool(settings.web.public_url and settings.web.public_url.startswith("https://"))
