"""
------------
- oauth.py -
------------

FakeAuthorizationServer: an OAuth 2.0 authorization server (an ASGI app) for udl's OAuth client
to run against: its authorization page redirects at once (or refuses, when told to), and its
token endpoint checks what a real one does (the client, the code, the PKCE verifier, the
redirect URI, refresh tokens), so the tests see what udl sends
"""

# System Imports
import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit

# Package Imports
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

# Local Imports
from udl.integrations.oauth import OAuthProvider

BASE: Final = "https://auth.example"
CLIENT_ID: Final = "udl-client"
CLIENT_SECRET: Final = "client-secret"  # noqa: S105 - (the fake's)


@dataclass
class _Grant:
    """A code the authorization page gave, and what it was given for."""

    client_id: str
    redirect_uri: str
    challenge: str
    scope: str


@dataclass
class FakeAuthorizationServer:
    """The fake server. Its fields say how it answers, and what it was asked."""

    expires_in: int = 3600
    deny: bool = False  # the user refuses on the authorization page
    rotate_refresh_tokens: bool = False  # a refresh gives a new refresh token
    fail_token_endpoint: bool = False  # the token endpoint answers 500
    grants: dict[str, _Grant] = field(default_factory=dict[str, _Grant])
    refresh_tokens: dict[str, str] = field(default_factory=dict[str, str])  # token: scope
    access_tokens: set[str] = field(default_factory=set[str])
    revoked: list[str] = field(default_factory=list[str])
    authorized: list[dict[str, str]] = field(default_factory=list[dict[str, str]])  # queries
    refreshed: int = 0

    def provider(self, **fields: object) -> OAuthProvider:
        """udl's client of this server."""
        return OAuthProvider.model_validate(
            {
                "name": "fake",
                "authorize_url": f"{BASE}/authorize",
                "token_url": f"{BASE}/token",
                "revoke_url": f"{BASE}/revoke",
                "userinfo_url": f"{BASE}/userinfo",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                **fields,
            }
        )

    def client(self) -> httpx.AsyncClient:
        """An HTTP client that reaches this server in-process."""
        return httpx.AsyncClient(transport=httpx.ASGITransport(self.app()))

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/authorize", self._authorize),
                Route("/token", self._token, methods=["POST"]),
                Route("/revoke", self._revoke, methods=["POST"]),
                Route("/userinfo", self._userinfo),
            ]
        )

    def revoke_refresh_tokens(self) -> None:
        """The user took udl's access back (on the provider's site)."""
        self.revoked.extend(self.refresh_tokens)
        self.refresh_tokens.clear()

    # -- the endpoints -------------------------------------------------------------------------

    def approve(self, url: str) -> str:
        """What the authorization page at `url` does when the user answers it (signing in, or
        refusing when `deny`): the URL it sends the browser back to."""
        return self._answer(dict(parse_qsl(urlsplit(url).query)))

    async def _authorize(self, request: Request) -> Response:
        return RedirectResponse(self._answer(dict(request.query_params)), status_code=302)

    def _answer(self, query: dict[str, str]) -> str:
        self.authorized.append(query)
        redirect = query["redirect_uri"]
        if self.deny:
            answer = {"error": "access_denied", "state": query.get("state", "")}
        else:
            code = secrets.token_urlsafe(16)
            self.grants[code] = _Grant(
                client_id=query["client_id"],
                redirect_uri=redirect,
                challenge=query.get("code_challenge", ""),
                scope=query.get("scope", ""),
            )
            answer = {"code": code, "state": query.get("state", "")}
        return f"{redirect}?{urlencode(answer)}"

    async def _token(self, request: Request) -> Response:
        if self.fail_token_endpoint:
            return Response("upstream trouble", status_code=500)
        form = {key: str(value) for key, value in (await request.form()).items()}
        if (form.get("client_id"), form.get("client_secret")) != (CLIENT_ID, CLIENT_SECRET):
            return _error("invalid_client", 401)
        if form.get("grant_type") == "authorization_code":
            grant = self.grants.pop(form.get("code", ""), None)
            if grant is None or grant.redirect_uri != form.get("redirect_uri"):
                return _error("invalid_grant")
            if _challenge(form.get("code_verifier", "")) != grant.challenge:
                return _error("invalid_grant", description="the code verifier does not match")
            refresh = secrets.token_urlsafe(16)
            self.refresh_tokens[refresh] = grant.scope
            return self._tokens(grant.scope, refresh)
        if form.get("grant_type") == "refresh_token":
            refresh = form.get("refresh_token", "")
            scope = self.refresh_tokens.get(refresh)
            if scope is None:
                return _error("invalid_grant", description="Token has been expired or revoked.")
            self.refreshed += 1
            if self.rotate_refresh_tokens:
                del self.refresh_tokens[refresh]
                rotated = secrets.token_urlsafe(16)
                self.refresh_tokens[rotated] = scope
                return self._tokens(scope, rotated)
            return self._tokens(scope, None)
        return _error("unsupported_grant_type")

    async def _revoke(self, request: Request) -> Response:
        token = str((await request.form()).get("token", ""))
        if token in self.refresh_tokens:
            del self.refresh_tokens[token]
        elif token in self.access_tokens:
            self.access_tokens.discard(token)
        else:
            return _error("invalid_token")
        self.revoked.append(token)
        return Response(status_code=200)

    async def _userinfo(self, request: Request) -> Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in self.access_tokens:
            return _error("invalid_token", 401)
        return JSONResponse({"sub": "1234", "email": "me@example.com"})

    def _tokens(self, scope: str, refresh: str | None) -> Response:
        access = secrets.token_urlsafe(16)
        self.access_tokens.add(access)
        answer: dict[str, object] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.expires_in,
            "scope": scope,
        }
        if refresh is not None:
            answer["refresh_token"] = refresh
        return JSONResponse(answer)


def _error(error: str, status: int = 400, description: str | None = None) -> Response:
    answer = {"error": error} | ({"error_description": description} if description else {})
    return JSONResponse(answer, status_code=status)


def _challenge(verifier: str) -> str:
    """The S256 PKCE challenge of `verifier`."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
