"""
------------------
- test_sessions.py -
------------------

Web sessions (udl.interfaces.web.sessions): signing in with the API key gives the browser a
cookie that the API accepts in place of the header, while another site still cannot drive udl —
a write with the cookie needs the CSRF header, and a write from another origin is refused
whoever it is. The MCP server takes the key alone.
"""

# System Imports
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import httpx
from pydantic import SecretStr

# Local Imports
from support.api import client_for
from support.interfaces import A, Harness
from udl.config import APISettings, WebSettings
from udl.interfaces.web.sessions import CSRF_HEADER, SESSION_COOKIE, SessionStore

KEY = "s3cret"


def secured(tmp_path: Path, *, session_hours: int = 12) -> Harness:
    """A udl that needs its API key (and serves the UI, where sessions live)."""
    return Harness(
        tmp_path,
        api=APISettings(api_key=SecretStr(KEY)),
        web=WebSettings(enabled=True, session_hours=session_hours),
    )


async def sign_in(client: httpx.AsyncClient, key: str = KEY) -> httpx.Response:
    return await client.post("/session", json={"api_key": key})


def keep(client: httpx.AsyncClient, token: str) -> None:
    """The browser keeps the session's cookie. (httpx's per-request `cookies=` is deprecated.)"""
    client.cookies.set(SESSION_COOKIE, token)


def session_cookie(answer: httpx.Response) -> str:
    """The session's token, from the answer's Set-Cookie."""
    for header in answer.headers.get_list("set-cookie"):
        if header.startswith(f"{SESSION_COOKIE}="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"no {SESSION_COOKIE} cookie in {answer.headers.get_list('set-cookie')}")


async def test_signing_in_with_the_key_sets_a_session_cookie(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        answer = await sign_in(client)

    assert answer.status_code == 204
    assert session_cookie(answer)
    # (A script cannot read it, and another site's request does not send it.)
    cookie = answer.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    # (Over plain HTTP it cannot be `Secure`, or the browser would drop it.)
    assert "secure" not in cookie


async def test_the_cookie_signs_in_the_api(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        token = session_cookie(await sign_in(client))
        keep(client, token)
        sites = await client.get("/sites")
        client.cookies.clear()
        keyed = await client.get("/sites", headers={"X-API-Key": KEY})

    assert sites.status_code == 200
    assert keyed.status_code == 200


async def test_a_wrong_key_is_a_401_and_no_cookie(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        answer = await sign_in(client, "wrong")

    assert answer.status_code == 401
    assert "set-cookie" not in answer.headers


async def test_a_new_token_on_each_sign_in(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        first = session_cookie(await sign_in(client))
        second = session_cookie(await sign_in(client))

    assert first != second
    assert len(first) >= 43  # (32 bytes, base64url: a 256-bit token)


async def test_ten_tries_a_minute_and_the_eleventh_is_a_429(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        answers = [await sign_in(client, "wrong") for _ in range(10)]
        eleventh = await sign_in(client)

    assert [answer.status_code for answer in answers] == [401] * 10
    assert eleventh.status_code == 429


async def test_signing_out_ends_the_session(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        token = session_cookie(await sign_in(client))
        keep(client, token)
        signed_out = await client.delete("/session")
        sites = await client.get("/sites")

    assert signed_out.status_code == 204
    # (The browser is told to drop it too.)
    assert "set-cookie" in signed_out.headers
    assert sites.status_code == 401


async def test_a_write_with_a_session_needs_the_csrf_header(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        token = session_cookie(await sign_in(client))
        keep(client, token)
        refused = await client.post("/probe", json={"url": A})
        allowed = await client.post("/probe", json={"url": A}, headers={CSRF_HEADER: "1"})

    # (Forbidden, not Unauthorized: the session is good, the write is not one udl's page made.)
    assert refused.status_code == 403
    assert allowed.status_code != 403
    assert allowed.status_code != 401


async def test_a_read_with_a_session_needs_no_csrf_header(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        token = session_cookie(await sign_in(client))
        keep(client, token)
        sites = await client.get("/sites")

    assert sites.status_code == 200


async def test_a_write_from_another_origin_is_refused_with_a_session(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        token = session_cookie(await sign_in(client))
        keep(client, token)
        answer = await client.post(
            "/probe",
            json={"url": A},
            headers={CSRF_HEADER: "1", "Origin": "https://evil.example"},
        )

    assert answer.status_code == 403


async def test_a_write_from_another_origin_is_refused_with_the_key(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        answer = await client.post(
            "/probe",
            json={"url": A},
            headers={"X-API-Key": KEY, "Origin": "https://evil.example"},
        )

    assert answer.status_code == 403


async def test_a_write_from_this_origin_is_allowed(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        answer = await client.post(
            "/probe",
            json={"url": A},
            headers={"X-API-Key": KEY, "Origin": "http://127.0.0.1:8765"},
        )

    assert answer.status_code != 403
    assert answer.status_code != 401


async def test_a_read_from_another_origin_is_allowed(tmp_path: Path) -> None:
    # (Another site cannot read the answer: the browser's CORS rule refuses it, and udl sends
    # no Access-Control-Allow-Origin. A read is not what CSRF drives.)
    async with client_for(secured(tmp_path)) as client:
        answer = await client.get(
            "/sites", headers={"X-API-Key": KEY, "Origin": "https://evil.example"}
        )

    assert answer.status_code == 200


async def test_without_a_key_signing_in_sets_no_cookie_and_the_api_is_open(
    tmp_path: Path,
) -> None:
    async with client_for(Harness(tmp_path, web=WebSettings(enabled=True))) as client:
        answer = await client.post("/session", json={"api_key": "anything"})
        sites = await client.get("/sites")

    assert answer.status_code == 204
    assert "set-cookie" not in answer.headers
    assert sites.status_code == 200


async def test_a_body_that_is_not_a_sign_in_is_a_400(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        answer = await client.post("/session", json={"key": KEY})

    assert answer.status_code == 400


async def test_the_mcp_server_takes_the_key_and_not_the_cookie(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        token = session_cookie(await sign_in(client))
        keep(client, token)
        by_cookie = await client.post("/mcp", json={}, headers={"Accept": "application/json"})
        client.cookies.clear()
        by_key = await client.post(
            "/mcp", json={}, headers={"X-API-Key": KEY, "Accept": "application/json"}
        )

    assert by_cookie.status_code == 401
    assert by_key.status_code != 401


async def test_the_mcp_server_refuses_a_write_from_another_origin(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        answer = await client.post(
            "/mcp",
            json={},
            headers={
                "X-API-Key": KEY,
                "Origin": "https://evil.example",
                "Accept": "application/json",
            },
        )
        ours = await client.post(
            "/mcp",
            json={},
            headers={
                "X-API-Key": KEY,
                "Origin": str(client.base_url).rstrip("/"),  # (this server's own origin)
                "Accept": "application/json",
            },
        )

    assert answer.status_code == 403
    assert ours.status_code != 403


async def test_an_expired_session_is_gone_after_the_next_sign_in(tmp_path: Path) -> None:
    clock = [datetime(2026, 11, 19, 12, 0, tzinfo=UTC)]
    store = SessionStore(timedelta(hours=1), now=lambda: clock[0])
    gone = store.sign_in()
    clock[0] += timedelta(hours=2)  # (the browser never comes back: nothing presents `gone`)

    again = store.sign_in()

    assert len(store) == 1  # (the expired one was swept when the next one signed in)
    assert store.is_live(again)
    assert not store.is_live(gone)


async def test_a_delete_with_a_session_needs_the_csrf_header(tmp_path: Path) -> None:
    async with client_for(secured(tmp_path)) as client:
        token = session_cookie(await sign_in(client))
        keep(client, token)
        refused = await client.delete("/sites/alpha.example")
        allowed = await client.delete("/sites/alpha.example", headers={CSRF_HEADER: "1"})

    assert refused.status_code == 403
    assert allowed.status_code != 403
    assert allowed.status_code != 401
