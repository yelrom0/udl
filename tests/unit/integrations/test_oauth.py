"""
-----------------
- test_oauth.py -
-----------------

OAuth 2.0 (udl.integrations.oauth), against a fake authorization server: the authorization code
flow with PKCE and a state, the loopback listener that catches the redirect, refreshing tokens
before they expire, and revoking them
"""

# System Imports
import asyncio
import base64
import hashlib
import socket
from collections.abc import AsyncIterator
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

# Package Imports
import httpx
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import T0, FakeClock
from support.oauth import CLIENT_ID, FakeAuthorizationServer
from udl.errors import ErrorCode, OAuthError, ReauthRequired
from udl.integrations.oauth import Authorization, OAuthClient, Tokens, loopback_redirect

REDIRECT = "http://127.0.0.1:12345/callback"
SCOPES = ["mail.read", "mail.send"]


@pytest.fixture
def server() -> FakeAuthorizationServer:
    return FakeAuthorizationServer()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
async def oauth(server: FakeAuthorizationServer, clock: FakeClock) -> AsyncIterator[OAuthClient]:
    async with server.client() as http:
        yield OAuthClient(server.provider(), http=http, clock=clock)


async def sign_in(
    oauth: OAuthClient, server: FakeAuthorizationServer, **extra: str
) -> tuple[Authorization, dict[str, str]]:
    """Start an authorization, and follow it as the user's browser would (the fake page
    redirects at once): the callback's query."""
    authorization = oauth.authorize(SCOPES, REDIRECT, extra=extra)
    async with server.client() as browser:
        page = await browser.get(authorization.url)
    location = urlsplit(page.headers["location"])
    callback = {key: values[0] for key, values in parse_qs(location.query).items()}
    return authorization, callback


async def tokens(oauth: OAuthClient, server: FakeAuthorizationServer) -> Tokens:
    authorization, callback = await sign_in(oauth, server)
    return await oauth.exchange(authorization, callback)


# ---------------------------------------------------------------------------------------------
# Authorizing
# ---------------------------------------------------------------------------------------------


async def test_the_authorization_page_is_asked_for_a_code_with_pkce(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    authorization = oauth.authorize(
        SCOPES, REDIRECT, extra={"login_hint": "me@example.com", "access_type": "offline"}
    )

    query = {key: values[0] for key, values in parse_qs(urlsplit(authorization.url).query).items()}
    verifier = authorization.verifier.get_secret_value()
    digest = hashlib.sha256(verifier.encode()).digest()
    assert query == {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "scope": "mail.read mail.send",
        "state": authorization.state,
        "code_challenge": base64.urlsafe_b64encode(digest).rstrip(b"=").decode(),
        "code_challenge_method": "S256",
        "login_hint": "me@example.com",
        "access_type": "offline",
    }
    assert 43 <= len(verifier) <= 128
    assert verifier not in authorization.url
    assert len(authorization.state) >= 32


async def test_each_authorization_has_its_own_state_and_verifier(oauth: OAuthClient) -> None:
    first, second = oauth.authorize(SCOPES, REDIRECT), oauth.authorize(SCOPES, REDIRECT)

    assert first.state != second.state
    assert first.verifier != second.verifier


async def test_the_code_is_exchanged_for_tokens(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    found = await tokens(oauth, server)

    assert found.access_token.get_secret_value() in server.access_tokens
    assert found.refresh_token is not None
    assert found.refresh_token.get_secret_value() in server.refresh_tokens
    assert found.expires_at == T0 + timedelta(hours=1)
    assert found.scopes == SCOPES
    assert found.token_type == "Bearer"  # noqa: S105 - (a kind of token)


async def test_a_callback_whose_state_is_not_the_authorization_s_is_refused(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    authorization, callback = await sign_in(oauth, server)

    with pytest.raises(OAuthError, match="state"):
        await oauth.exchange(authorization, {**callback, "state": "forged"})
    with pytest.raises(OAuthError, match="state"):
        await oauth.exchange(authorization, {"code": callback["code"]})


async def test_a_refused_authorization_says_so(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    server.deny = True
    authorization, callback = await sign_in(oauth, server)

    with pytest.raises(OAuthError, match="access_denied") as raised:
        await oauth.exchange(authorization, callback)

    assert raised.value.code is ErrorCode.OAUTH_ERROR


async def test_a_callback_without_a_code_is_refused(oauth: OAuthClient) -> None:
    authorization = oauth.authorize(SCOPES, REDIRECT)

    with pytest.raises(OAuthError, match="code"):
        await oauth.exchange(authorization, {"state": authorization.state})


async def test_a_code_exchanged_with_another_verifier_is_refused(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    authorization, callback = await sign_in(oauth, server)
    other = authorization.model_copy(update={"verifier": SecretStr("x" * 50)})

    with pytest.raises(OAuthError, match="invalid_grant"):
        await oauth.exchange(other, callback)


async def test_a_client_the_server_does_not_know_is_an_error_that_says_so(
    server: FakeAuthorizationServer, clock: FakeClock
) -> None:
    async with server.client() as http:
        wrong = server.provider(client_secret="wrong")  # noqa: S106 - (a fake's)
        oauth = OAuthClient(wrong, http=http, clock=clock)
        authorization, callback = await sign_in(oauth, server)

        with pytest.raises(OAuthError, match="invalid_client") as raised:
            await oauth.exchange(authorization, callback)

    assert raised.value.hint is not None
    assert "client" in raised.value.hint


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(500, text="upstream trouble"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"token_type": "Bearer"}),  # (no access token)
    ],
)
async def test_a_token_endpoint_that_fails_is_an_oauth_error(
    clock: FakeClock, server: FakeAuthorizationServer, answer: httpx.Response
) -> None:
    def answered(request: httpx.Request) -> httpx.Response:
        return answer

    async with httpx.AsyncClient(transport=httpx.MockTransport(answered)) as http:
        oauth = OAuthClient(server.provider(), http=http, clock=clock)
        authorization = oauth.authorize(SCOPES, REDIRECT)

        with pytest.raises(OAuthError):
            await oauth.exchange(authorization, {"code": "c", "state": authorization.state})


async def test_a_token_endpoint_that_cannot_be_reached_is_an_oauth_error(
    clock: FakeClock, server: FakeAuthorizationServer
) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(unreachable)) as http:
        oauth = OAuthClient(server.provider(), http=http, clock=clock)
        authorization = oauth.authorize(SCOPES, REDIRECT)

        with pytest.raises(OAuthError, match="connection refused"):
            await oauth.exchange(authorization, {"code": "c", "state": authorization.state})


# ---------------------------------------------------------------------------------------------
# Refreshing
# ---------------------------------------------------------------------------------------------


async def test_fresh_tokens_are_used_as_they_are(
    oauth: OAuthClient, server: FakeAuthorizationServer, clock: FakeClock
) -> None:
    found = await tokens(oauth, server)
    await clock.advance(timedelta(minutes=58))

    assert await oauth.fresh(found) is found
    assert server.refreshed == 0


async def test_tokens_are_refreshed_a_minute_before_they_expire(
    oauth: OAuthClient, server: FakeAuthorizationServer, clock: FakeClock
) -> None:
    found = await tokens(oauth, server)
    await clock.advance(timedelta(minutes=59, seconds=1))

    refreshed = await oauth.fresh(found)

    assert server.refreshed == 1
    assert refreshed.access_token != found.access_token
    assert refreshed.refresh_token == found.refresh_token  # (none came back: the old one stays)
    assert refreshed.expires_at == clock.now() + timedelta(hours=1)


async def test_a_new_refresh_token_replaces_the_old_one(
    oauth: OAuthClient, server: FakeAuthorizationServer, clock: FakeClock
) -> None:
    server.rotate_refresh_tokens = True
    found = await tokens(oauth, server)

    refreshed = await oauth.refresh(found)

    assert refreshed.refresh_token is not None
    assert refreshed.refresh_token != found.refresh_token
    assert refreshed.refresh_token.get_secret_value() in server.refresh_tokens


async def test_a_refused_refresh_means_connecting_again(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    found = await tokens(oauth, server)
    server.revoke_refresh_tokens()

    with pytest.raises(ReauthRequired, match="revoked") as raised:
        await oauth.refresh(found)

    assert raised.value.code is ErrorCode.REAUTH_REQUIRED
    assert isinstance(raised.value, OAuthError)


async def test_expired_tokens_without_a_refresh_token_mean_connecting_again(
    oauth: OAuthClient, clock: FakeClock
) -> None:
    found = Tokens(access_token=SecretStr("a"), expires_at=T0 + timedelta(seconds=30))

    with pytest.raises(ReauthRequired):
        await oauth.fresh(found)


async def test_tokens_that_never_expire_are_never_refreshed(
    oauth: OAuthClient, server: FakeAuthorizationServer, clock: FakeClock
) -> None:
    found = Tokens(access_token=SecretStr("a"))
    await clock.advance(timedelta(days=365))

    assert await oauth.fresh(found) is found


async def test_a_refresh_the_server_fails_is_not_a_refusal(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    found = await tokens(oauth, server)
    server.fail_token_endpoint = True

    with pytest.raises(OAuthError) as raised:
        await oauth.refresh(found)

    assert not isinstance(raised.value, ReauthRequired)


# ---------------------------------------------------------------------------------------------
# Revoking
# ---------------------------------------------------------------------------------------------


async def test_tokens_are_revoked(oauth: OAuthClient, server: FakeAuthorizationServer) -> None:
    found = await tokens(oauth, server)
    assert found.refresh_token is not None

    await oauth.revoke(found)

    assert server.revoked == [found.refresh_token.get_secret_value()]
    assert server.refresh_tokens == {}


async def test_revoking_tokens_the_server_no_longer_knows_is_fine(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    found = await tokens(oauth, server)
    server.revoke_refresh_tokens()

    await oauth.revoke(found)


async def test_without_a_refresh_token_the_access_token_is_revoked(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    found = (await tokens(oauth, server)).model_copy(update={"refresh_token": None})

    await oauth.revoke(found)

    assert server.revoked == [found.access_token.get_secret_value()]


async def test_a_provider_without_a_revocation_endpoint_revokes_nothing(
    server: FakeAuthorizationServer, clock: FakeClock
) -> None:
    async with server.client() as http:
        oauth = OAuthClient(server.provider(revoke_url=None), http=http, clock=clock)

        await oauth.revoke(Tokens(access_token=SecretStr("a")))

    assert server.revoked == []


async def test_a_revocation_the_server_fails_is_an_oauth_error(
    oauth: OAuthClient, server: FakeAuthorizationServer
) -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as http:
        broken = OAuthClient(server.provider(), http=http, clock=FakeClock())

        with pytest.raises(OAuthError, match="503"):
            await broken.revoke(Tokens(access_token=SecretStr("a")))


# ---------------------------------------------------------------------------------------------
# The loopback listener
# ---------------------------------------------------------------------------------------------


async def test_the_listener_catches_the_redirect_on_this_machine(
    server: FakeAuthorizationServer, clock: FakeClock
) -> None:
    async with server.client() as http, loopback_redirect(answer_within=5) as redirect:
        oauth = OAuthClient(server.provider(), http=http, clock=clock)
        authorization = oauth.authorize(SCOPES, redirect.uri)
        async with server.client() as browser:
            page = await browser.get(authorization.url)
        async with httpx.AsyncClient() as real:  # (the browser follows the redirect)
            landed = await real.get(page.headers["location"])
        callback = await redirect.wait()
        found = await oauth.exchange(authorization, callback)

    assert urlsplit(redirect.uri).hostname == "127.0.0.1"
    assert landed.status_code == 200
    assert "close" in landed.text.lower()
    assert found.access_token.get_secret_value() in server.access_tokens


async def test_the_listener_listens_on_127_0_0_1_only() -> None:
    async with loopback_redirect(answer_within=5) as redirect:
        host, port = redirect.address

        assert (host, port) == ("127.0.0.1", urlsplit(redirect.uri).port)
        # (Another address of this machine's, loopback or not, is not listened on.)
        with pytest.raises(OSError):  # noqa: PT011 - (refused)
            socket.create_connection(("127.0.0.2", port), timeout=1).close()


async def test_the_listener_closes_after_one_callback() -> None:
    async with loopback_redirect(path="/done", answer_within=5) as redirect:
        async with httpx.AsyncClient() as browser:
            other = await browser.get(redirect.uri.replace("/done", "/favicon.ico"))
            await browser.get(f"{redirect.uri}?code=abc&state=xyz")
        callback = await redirect.wait()

        async with httpx.AsyncClient() as browser:
            with pytest.raises(httpx.ConnectError):
                await browser.get(f"{redirect.uri}?code=again")

    assert other.status_code == 404
    assert callback == {"code": "abc", "state": "xyz"}


async def test_the_listener_gives_up_after_its_timeout() -> None:
    async with loopback_redirect(answer_within=0.05) as redirect:
        with pytest.raises(OAuthError, match=r"0\.05 seconds"):
            await redirect.wait()

        async with httpx.AsyncClient() as browser:
            with pytest.raises(httpx.ConnectError):
                await browser.get(f"{redirect.uri}?code=late")


async def test_the_first_callback_is_the_one_taken() -> None:
    async with loopback_redirect(answer_within=5) as redirect:
        async with httpx.AsyncClient() as browser:
            await browser.get(f"{redirect.uri}?code=first")
            again = await browser.get(f"{redirect.uri}?code=second")

        assert again.status_code == 200
        assert await redirect.wait() == {"code": "first"}


async def test_a_request_that_is_not_http_is_dropped() -> None:
    async with loopback_redirect(answer_within=5) as redirect:
        port = urlsplit(redirect.uri).port
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"\x00\x01 nonsense\r\n\r\n")
        await writer.drain()
        await reader.read()
        writer.close()
        async with httpx.AsyncClient() as browser:
            await browser.get(f"{redirect.uri}?error=access_denied")

        assert await redirect.wait() == {"error": "access_denied"}
