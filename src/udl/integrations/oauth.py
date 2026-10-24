"""
------------
- oauth.py -
------------

OAuth 2.0, on httpx: how an account is connected to a service that uses it (Google), without a
vendor SDK.

- The authorization code flow with PKCE (S256) and a `state`: authorize() gives the page to send
  the user to, and exchange() checks the callback (its state, an error, that it is the first)
  and trades the code for tokens. userinfo() says who signed in.
- loopback_redirect(): where that page sends the user back to, when udl runs on the user's own
  machine: a listener on 127.0.0.1, on a free port, for one callback (or until its timeout).
- fresh() refreshes tokens a minute before they expire (on the Clock); a refresh the server
  refuses (the user took the access back, or it expired) is ReauthRequired.

What the user refused, or an answer that is not the one asked for, is NotAllowed; a client the
server does not know is a ConfigError; a server that fails or cannot be reached is a
NetworkError (try again later), naming the provider.
- revoke() gives the tokens back, when an account is disconnected.

The client authenticates at the token endpoint with its id and secret in the form
(client_secret_post), which Google and Slack both take.
"""

# System Imports
import asyncio
import base64
import contextlib
import functools
import hashlib
import html
import logging
import secrets
from collections.abc import AsyncGenerator, Mapping, Sequence
from datetime import timedelta
from typing import Final, Self, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Package Imports
import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    ValidationError,
)

# Local Imports
from udl.clock import Clock
from udl.db.models import JsonObject
from udl.errors import ConfigError, NetworkError, NotAllowed, ReauthRequired

log = logging.getLogger(__name__)

REFRESH_BEFORE: Final = timedelta(minutes=1)
"""How long before tokens expire they are refreshed."""
LOOPBACK: Final = "127.0.0.1"
_HEAD_LIMIT: Final = 16 * 1024  # (the most of a request the listener reads)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OAuthProvider(_Frozen):
    """An authorization server, and udl's client of it (the user's own, e.g. a Google Cloud
    project's Desktop app)."""

    name: str
    authorize_url: str
    token_url: str
    revoke_url: str | None = None
    userinfo_url: str | None = None
    client_id: str
    client_secret: SecretStr | None = None


class Authorization(_Frozen):
    """An authorization in progress: the page to send the user to, and what checks the answer."""

    url: str
    state: str
    verifier: SecretStr  # (PKCE's code verifier: only the token endpoint is told it)
    redirect_uri: str
    scopes: list[str]
    started_at: AwareDatetime


class Tokens(_Frozen):
    """What an authorization gives: the access token for requests, and the refresh token for
    new access tokens."""

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    token_type: str = "Bearer"  # noqa: S105 - (a kind of token, not one)
    expires_at: AwareDatetime | None = None
    """When the access token expires; None when it does not."""
    scopes: list[str] = Field(default_factory=list[str])

    def expiring(self, now: AwareDatetime) -> bool:
        """Whether the access token expires within REFRESH_BEFORE of `now` (or has)."""
        return self.expires_at is not None and self.expires_at - REFRESH_BEFORE <= now


class _TokenAnswer(BaseModel):
    """The token endpoint's answer (RFC 6749 5.1)."""

    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: str = "Bearer"  # noqa: S105 - (a kind of token, not one)
    expires_in: float | None = None
    refresh_token: SecretStr | None = None
    scope: str | None = None


class OAuthClient:
    """udl's client of `provider`, sending its requests with `http`."""

    def __init__(self, provider: OAuthProvider, *, http: httpx.AsyncClient, clock: Clock) -> None:
        self.provider = provider
        self._http = http
        self._clock = clock
        self._completed: set[str] = set()  # (the states of the authorizations exchanged)

    def authorize(
        self, scopes: Sequence[str], redirect_uri: str, *, extra: Mapping[str, str] | None = None
    ) -> Authorization:
        """Start an authorization: the page to send the user to (asking for `scopes`, then
        sending them back to `redirect_uri`), with `extra` parameters (e.g. login_hint)."""
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)  # (86 characters, of 43 to 128)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        query = {
            "response_type": "code",
            "client_id": self.provider.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": challenge.rstrip(b"=").decode("ascii"),
            "code_challenge_method": "S256",
            **(extra or {}),
        }
        page = urlsplit(self.provider.authorize_url)
        asked = urlencode([*parse_qsl(page.query), *query.items()])
        return Authorization(
            url=urlunsplit(page._replace(query=asked)),
            state=state,
            verifier=SecretStr(verifier),
            redirect_uri=redirect_uri,
            scopes=list(scopes),
            started_at=self._clock.now(),
        )

    async def exchange(self, authorization: Authorization, callback: Mapping[str, str]) -> Tokens:
        """The tokens for an authorization, from the query its callback came with. Raises
        NotAllowed when the user refused, the callback is not this authorization's (its state),
        the authorization was completed already or the server refuses the code."""
        if "error" in callback:
            described = callback.get("error_description")
            refused = f"{callback['error']}: {described}" if described else callback["error"]
            raise NotAllowed(f"The authorization was refused ({refused})")
        if not secrets.compare_digest(callback.get("state", ""), authorization.state):
            raise NotAllowed(
                "The callback's state is not the authorization's: it was not the answer to it",
                hint="Start connecting again.",
            )
        if authorization.state in self._completed:
            raise NotAllowed("This authorization was already completed", hint=_START_AGAIN)
        code = callback.get("code")
        if not code:
            raise NotAllowed("The callback brought no code", hint=_START_AGAIN)
        self._completed.add(authorization.state)
        try:
            return await self._tokens(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": authorization.redirect_uri,
                    "code_verifier": authorization.verifier.get_secret_value(),
                },
                authorization.scopes,
            )
        except _Refused as e:
            raise NotAllowed(
                f"{self.provider.name} refused the code ({e})", hint=_START_AGAIN
            ) from e

    async def fresh(self, tokens: Tokens) -> Tokens:
        """`tokens`, refreshed when they expire within a minute (else as they are). Raises
        ReauthRequired when they cannot be."""
        return await self.refresh(tokens) if tokens.expiring(self._clock.now()) else tokens

    async def refresh(self, tokens: Tokens) -> Tokens:
        """New tokens for `tokens` (keeping the refresh token, unless a new one comes). Raises
        ReauthRequired when the server refuses (or there is no refresh token)."""
        if tokens.refresh_token is None:
            raise ReauthRequired("The access has expired, and cannot be refreshed")
        try:
            refreshed = await self._tokens(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token.get_secret_value(),
                },
                tokens.scopes,
            )
        except _Refused as e:
            raise ReauthRequired(f"The access was refused ({e})", hint=_RECONNECT) from e
        if refreshed.refresh_token is None:
            refreshed = refreshed.model_copy(update={"refresh_token": tokens.refresh_token})
        return refreshed

    async def revoke(self, tokens: Tokens) -> None:
        """Give `tokens` back (the refresh token, which takes the access tokens with it, else
        the access token). Tokens the server no longer knows are fine."""
        if self.provider.revoke_url is None:
            return
        token = tokens.refresh_token or tokens.access_token
        answer = await self._post(self.provider.revoke_url, {"token": token.get_secret_value()})
        if answer.is_success or _error(answer) == "invalid_token":
            return
        raise NetworkError(
            f"{self.provider.name} did not revoke the tokens: HTTP {answer.status_code}",
            tool=self.provider.name,
        )

    async def userinfo(self, tokens: Tokens) -> JsonObject:
        """Who signed in, as the provider's userinfo endpoint says (e.g. an email address).
        Raises ReauthRequired when it refuses the access token."""
        if self.provider.userinfo_url is None:
            raise ConfigError(f"{self.provider.name} has no userinfo endpoint")
        bearer = {"Authorization": f"Bearer {tokens.access_token.get_secret_value()}"}
        try:
            answer = await self._http.get(self.provider.userinfo_url, headers=bearer)
        except httpx.HTTPError as e:
            raise self._unreachable(self.provider.userinfo_url, e) from e
        if answer.status_code == 401:
            raise ReauthRequired(f"{self.provider.name} refused the access", hint=_RECONNECT)
        if not answer.is_success:
            raise NetworkError(
                f"{self.provider.name}'s userinfo endpoint failed: HTTP {answer.status_code}",
                tool=self.provider.name,
            )
        return _JSON_OBJECT.validate_json(answer.content)

    async def _tokens(self, form: dict[str, str], scopes: list[str]) -> Tokens:
        """Ask the token endpoint for tokens."""
        now = self._clock.now()
        answer = await self._post(
            self.provider.token_url,
            {**form, "client_id": self.provider.client_id, **self._secret()},
        )
        if not answer.is_success:
            error = _error(answer)
            if error == "invalid_grant":
                raise _Refused(_described(answer, error))
            if error == "invalid_client":
                raise ConfigError(
                    f"{self.provider.name} does not know udl's client "
                    f"({_described(answer, error)})",
                    hint="Check the client id and secret udl was given.",
                )
            raise NetworkError(
                f"{self.provider.name}'s token endpoint failed: "
                f"{_described(answer, error or f'HTTP {answer.status_code}')}",
                tool=self.provider.name,
            )
        try:
            found = _TokenAnswer.model_validate_json(answer.content)
        except ValidationError as e:
            raise NetworkError(
                f"{self.provider.name} answered with no tokens", tool=self.provider.name
            ) from e
        return Tokens(
            access_token=found.access_token,
            refresh_token=found.refresh_token,
            token_type=found.token_type,
            expires_at=now + timedelta(seconds=found.expires_in) if found.expires_in else None,
            scopes=found.scope.split() if found.scope else scopes,
        )

    def _secret(self) -> dict[str, str]:
        secret = self.provider.client_secret
        return {"client_secret": secret.get_secret_value()} if secret is not None else {}

    async def _post(self, url: str, form: dict[str, str]) -> httpx.Response:
        try:
            return await self._http.post(url, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise self._unreachable(url, e) from e

    def _unreachable(self, url: str, error: httpx.HTTPError) -> NetworkError:
        return NetworkError(
            f"Could not reach {self.provider.name} ({url}): {error}", tool=self.provider.name
        )


_RECONNECT: Final = "Connect the account again."
_START_AGAIN: Final = "Start connecting again."
_JSON_OBJECT: Final = TypeAdapter[JsonObject](JsonObject)


class _Refused(Exception):
    """The token endpoint refused a grant (invalid_grant): which error it is depends on the
    grant."""


def _error(answer: httpx.Response) -> str | None:
    """The OAuth error code in an error answer, if it has one."""
    try:
        found = answer.json()
    except ValueError:
        return None
    error = cast(dict[str, object], found).get("error") if isinstance(found, dict) else None
    return error if isinstance(error, str) else None


def _described(answer: httpx.Response, error: str) -> str:
    try:
        description = answer.json().get("error_description")
    except ValueError, AttributeError:
        description = None
    return f"{error}: {description}" if isinstance(description, str) else error


# ---------------------------------------------------------------------------------------------
# The loopback listener
# ---------------------------------------------------------------------------------------------

_DONE: Final = "<!doctype html><title>udl</title><p>udl has the answer. You can close this tab.</p>"


class LoopbackRedirect:
    """Where an authorization page sends the user back to: `uri`, on this machine."""

    def __init__(
        self,
        server: asyncio.Server,
        callback: asyncio.Future[dict[str, str]],
        path: str,
        within: float,
    ) -> None:
        self._server = server
        self._callback = callback
        self._within = within
        host, port = server.sockets[0].getsockname()[:2]
        self.address: tuple[str, int] = (host, port)
        self.uri = f"http://{host}:{port}{path}"

    @classmethod
    async def listen(cls, path: str, within: float) -> Self:
        """Listen on 127.0.0.1 (only), on a free port, for a callback to `path`."""
        callback = asyncio.get_running_loop().create_future()
        answer = functools.partial(_answer, path, callback)
        server = await asyncio.start_server(answer, LOOPBACK, 0, limit=_HEAD_LIMIT)
        return cls(server, callback, path, within)

    async def wait(self) -> dict[str, str]:
        """The callback's query, once the page sends the user back. Raises NotAllowed when it
        does not within the time given. Either way, the listener is closed."""
        try:
            async with asyncio.timeout(self._within):
                return await self._callback
        except TimeoutError:
            raise NotAllowed(
                f"No answer from the authorization page within {self._within:g} seconds",
                hint="Start connecting again, and finish in the browser.",
            ) from None
        finally:
            self._server.close()

    async def close(self) -> None:
        self._server.close()
        await self._server.wait_closed()


async def _answer(
    path: str,
    callback: asyncio.Future[dict[str, str]],
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """Answer one request to the listener: the callback (taken once), else 404."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        method, target, _ = head.decode("latin-1").split(" ", 2)
    except asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError:
        writer.close()
        return
    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query))
    if method == "GET" and parts.path == path and ("code" in query or "error" in query):
        if callback.done():
            body, status = "This authorization was already answered.", "409 Conflict"
        else:
            callback.set_result(query)
            body = _DONE if "code" in query else _refused(query["error"])
            status = "200 OK"
    else:
        body, status = "Not found", "404 Not Found"
    payload = body.encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
        + payload
    )
    with contextlib.suppress(ConnectionError):
        await writer.drain()
    writer.close()


def _refused(error: str) -> str:
    return (
        "<!doctype html><title>udl</title>"
        f"<p>The authorization was refused ({html.escape(error)}). You can close this tab.</p>"
    )


@contextlib.asynccontextmanager
async def loopback_redirect(
    *, path: str = "/callback", answer_within: float = 300
) -> AsyncGenerator[LoopbackRedirect]:
    """A listener on 127.0.0.1 (only), on a free port, for one callback to `path`: it closes
    once it has it, when none came within `answer_within` seconds, or when the block ends."""
    redirect = await LoopbackRedirect.listen(path, answer_within)
    try:
        yield redirect
    finally:
        await redirect.close()
