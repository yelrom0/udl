"""
----------------------
- test_connecting.py -
----------------------

Connecting accounts, against the fake authorization server: over OAuth from the CLI (udl opens
the page; a listener on 127.0.0.1 in that process takes the answer), the API (the page sends the
browser back to the server's /accounts/callback, which needs no API key: its one-time state is
the check) and MCP (the agent gets the page to open, and follows the account up); with tokens;
and a source with its options alone. A name that is taken is refused unless the account is
connected again, which keeps it; an authorization not answered within 10 minutes expires.
"""

# System Imports
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# Package Imports
import httpx
import pytest
from pydantic import SecretStr

# Local Imports
from support.accounts import Browser, Fakes, callback_query, with_fake_integration
from support.api import client_for
from support.cli import CLI
from support.clock import FakeClock
from support.integrations import REFUSED_KEY, fake_integration
from support.interfaces import Harness
from support.mcp import client_for as mcp_client_for
from support.tasks import held
from udl.automations.events import EventRecord
from udl.automations.subscriptions import Subscription
from udl.config import APISettings, AutomationsSettings, WebSettings
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import AlreadyExists, ConfigError, DoesNotExist, DuplicateKey, NotAllowed
from udl.integrations.models import ConnectAccountRequest
from udl.integrations.oauth import Tokens
from udl.plugins.actions.base import Action
from udl.plugins.integrations.base import Account, AccountStatus, ConnectMethod
from udl.plugins.registry import INTEGRATIONS

CALLBACK = "http://127.0.0.1:8765/accounts/callback"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def harness_of(tmp_path: Path, clock: FakeClock, **fields: Any) -> Harness:
    fields.setdefault(
        "automations", AutomationsSettings(enabled=False, scripts_dir=tmp_path / "scripts")
    )
    return Harness(tmp_path, clock=clock, **fields)


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock) -> Harness:
    return harness_of(tmp_path, clock)


@pytest.fixture
def fakes(harness: Harness) -> Fakes:
    return with_fake_integration(harness)


@pytest.fixture
def browser(fakes: Fakes, monkeypatch: pytest.MonkeyPatch) -> Browser:
    found = Browser(fakes.server)
    monkeypatch.setattr("webbrowser.open", found.open)
    return found


@pytest.fixture
async def service(harness: Harness, fakes: Fakes) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        yield found


@pytest.fixture
async def api(harness: Harness, fakes: Fakes) -> AsyncIterator[httpx.AsyncClient]:
    async with held(client_for(harness)) as client:
        yield client


async def stored(harness: Harness, name: str = "work") -> Account:
    found = await harness.db.list_records(Account, RecordQuery(where={"name": name}))
    assert len(found) == 1, found
    return found[0]


async def connected_events(harness: Harness) -> list[EventRecord]:
    return await harness.db.list_records(
        EventRecord, RecordQuery(where={"type": "udl.account.connected"})
    )


async def kept_credentials(harness: Harness, account: Account) -> dict[str, Any]:
    assert account.secret_ref is not None
    kept = await harness.secrets.get(account.secret_ref)
    assert kept is not None
    return json.loads(kept.get_secret_value())


def path_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


# ---------------------------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------------------------


def test_the_cli_connects_an_oauth_account_in_the_browser(
    harness: Harness, fakes: Fakes, browser: Browser
) -> None:
    result = CLI(harness)("accounts", "add", "fake", "work", "--access", "read,write")

    assert result.exit_code == 0, result.output
    assert "fake:work" in result.stdout
    assert "connected" in result.stdout
    [page] = browser.opened
    assert page in result.output  # (printed too, for when no browser opens)
    [asked] = fakes.server.authorized
    assert asked["scope"] == "fake.read fake.write"
    assert asked["redirect_uri"].startswith("http://127.0.0.1:")
    account = asyncio.run(_stored_now(harness))
    assert account.status is AccountStatus.CONNECTED
    assert account.access == ["read", "write"]
    assert account.identity is not None
    assert account.identity.display == "me@example.com"


async def _stored_now(harness: Harness) -> Account:
    async with harness.db:
        return await stored(harness)


def test_the_cli_says_when_the_authorization_is_refused(
    harness: Harness, fakes: Fakes, browser: Browser
) -> None:
    fakes.server.deny = True

    result = CLI(harness)("accounts", "add", "fake", "work")

    assert result.exit_code == 1
    assert "The authorization was refused (access_denied)" in result.output
    assert asyncio.run(_stored_now(harness)).status is AccountStatus.ERROR


def test_the_cli_asks_for_tokens_without_showing_them(harness: Harness, fakes: Fakes) -> None:
    result = CLI(harness)(
        "accounts", "add", "fake", "bot", "--method", "tokens", stdin="key-1234\n"
    )

    assert result.exit_code == 0, result.output
    assert "The fake service's API key" in result.output  # (asked for)
    assert "key-1234" not in result.output
    assert "fake:bot" in result.stdout


def test_the_cli_reads_tokens_from_stdin(harness: Harness, fakes: Fakes) -> None:
    result = CLI(harness)(
        "accounts",
        "add",
        "fake",
        "bot",
        "--tokens-stdin",
        "--json",
        stdin='{"api_key": "key-1234"}',
    )

    assert result.exit_code == 0, result.output
    shown = json.loads(result.stdout)
    assert (shown["account"], shown["method"], shown["status"]) == (
        "fake:bot",
        "tokens",
        "connected",
    )


def test_the_cli_refuses_tokens_on_stdin_that_are_not_an_object(
    harness: Harness, fakes: Fakes
) -> None:
    result = CLI(harness)("accounts", "add", "fake", "bot", "--tokens-stdin", stdin="[1]")

    assert result.exit_code == 2
    assert "one JSON object" in result.output


def test_the_cli_connects_a_source_with_its_options(harness: Harness, fakes: Fakes) -> None:
    result = CLI(harness)(
        "accounts",
        "add",
        "fake",
        "blog",
        "--method",
        "none",
        "-O",
        "source=https://example.com/blog",
    )

    assert result.exit_code == 0, result.output
    assert "https://example.com/blog" in result.stdout


def test_the_cli_refuses_a_name_that_is_not_an_account_s(harness: Harness, fakes: Fakes) -> None:
    result = CLI(harness)("accounts", "add", "fake", "has:colon", "--method", "none")

    assert result.exit_code == 2


# ---------------------------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------------------------


async def test_the_api_connects_an_oauth_account_through_its_callback(
    harness: Harness, fakes: Fakes, api: httpx.AsyncClient
) -> None:
    answer = await api.post("/accounts", json={"integration": "fake", "name": "work"})

    assert answer.status_code == 201, answer.text
    connection = answer.json()
    assert connection["account"]["status"] == "pending"
    back = fakes.server.approve(connection["authorization_url"])
    assert back.startswith(f"{CALLBACK}?")
    page = await api.get(path_of(back))

    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "fake:work is connected" in page.text
    account = await stored(harness)
    assert account.status is AccountStatus.CONNECTED
    assert account.pending_until is None
    tokens = Tokens.model_validate(await kept_credentials(harness, account))
    assert tokens.scopes == ["fake.read"]
    [said] = await connected_events(harness)
    assert said.data["account"] == "fake:work"


async def test_the_callback_needs_no_api_key(tmp_path: Path, clock: FakeClock) -> None:
    harness = harness_of(tmp_path, clock, api=APISettings(api_key=SecretStr("api-secret")))
    fakes = with_fake_integration(harness)
    key = {"X-API-Key": "api-secret"}
    async with client_for(harness) as api:
        refused = await api.post("/accounts", json={"integration": "fake", "name": "work"})
        answer = await api.post(
            "/accounts", json={"integration": "fake", "name": "work"}, headers=key
        )
        page = await api.get(path_of(fakes.server.approve(answer.json()["authorization_url"])))

    assert refused.status_code == 401
    assert page.status_code == 200, page.text


async def test_the_callback_is_under_the_public_url(tmp_path: Path, clock: FakeClock) -> None:
    harness = harness_of(tmp_path, clock, web=WebSettings(public_url="https://udl.example/"))
    fakes = with_fake_integration(harness)
    async with client_for(harness) as api:
        answer = await api.post("/accounts", json={"integration": "fake", "name": "work"})

    back = fakes.server.approve(answer.json()["authorization_url"])
    assert back.startswith("https://udl.example/accounts/callback?")


async def test_the_callback_sends_the_browser_back_where_it_came_from(
    fakes: Fakes, api: httpx.AsyncClient
) -> None:
    answer = await api.post(
        "/accounts", json={"integration": "fake", "name": "work", "return_to": "/ui/accounts"}
    )

    page = await api.get(path_of(fakes.server.approve(answer.json()["authorization_url"])))

    assert page.status_code == 303
    assert page.headers["location"] == "/ui/accounts"


@pytest.mark.parametrize("where", ["https://elsewhere.example/", "//elsewhere.example/x", "ui"])
async def test_the_browser_is_sent_back_only_to_this_udl(
    api: httpx.AsyncClient, where: str
) -> None:
    answer = await api.post(
        "/accounts", json={"integration": "fake", "name": "work", "return_to": where}
    )

    assert answer.status_code == 422


async def test_a_callback_to_no_authorization_is_refused(
    fakes: Fakes, api: httpx.AsyncClient
) -> None:
    answer = await api.post("/accounts", json={"integration": "fake", "name": "work"})
    back = path_of(fakes.server.approve(answer.json()["authorization_url"]))

    unknown = await api.get("/accounts/callback?state=forged&code=x")
    first = await api.get(back)
    used = await api.get(back)

    assert unknown.status_code == 403
    assert "udl could not connect the account" in unknown.text
    assert first.status_code == 200
    assert used.status_code == 403


async def test_a_refused_authorization_leaves_the_account_failed(
    harness: Harness, fakes: Fakes, api: httpx.AsyncClient
) -> None:
    fakes.server.deny = True
    answer = await api.post("/accounts", json={"integration": "fake", "name": "work"})

    page = await api.get(path_of(fakes.server.approve(answer.json()["authorization_url"])))

    assert page.status_code == 403
    assert "access_denied" in page.text
    account = await stored(harness)
    assert account.status is AccountStatus.ERROR
    assert account.last_error is not None
    assert "access_denied" in account.last_error.message


async def test_an_authorization_not_answered_within_10_minutes_expires(
    harness: Harness, fakes: Fakes, api: httpx.AsyncClient, clock: FakeClock
) -> None:
    answer = await api.post("/accounts", json={"integration": "fake", "name": "work"})
    back = path_of(fakes.server.approve(answer.json()["authorization_url"]))
    await clock.advance(timedelta(minutes=10))

    page = await api.get(back)

    assert page.status_code == 403
    assert "within 10 minutes" in page.text
    assert (await stored(harness)).status is AccountStatus.ERROR


async def test_a_pending_account_expires_even_unanswered(
    service: UDLService, clock: FakeClock
) -> None:
    await service.connect_account(ConnectAccountRequest(integration="fake", name="work"))
    await clock.advance(timedelta(minutes=11))

    [account] = await service.accounts.list()

    assert account.status is AccountStatus.ERROR
    assert account.pending_until is None
    assert account.last_error is not None
    assert "did not finish within 10 minutes" in account.last_error.message


async def test_the_api_connects_with_tokens(harness: Harness, api: httpx.AsyncClient) -> None:
    answer = await api.post(
        "/accounts", json={"integration": "fake", "name": "bot", "tokens": {"api_key": "key-1234"}}
    )

    assert answer.status_code == 201, answer.text
    assert "key-1234" not in answer.text
    shown = answer.json()["account"]
    assert (shown["method"], shown["status"]) == ("tokens", "connected")
    assert shown["identity"]["display"] == "API key ...1234"
    assert answer.json()["authorization_url"] is None
    assert await kept_credentials(harness, await stored(harness, "bot")) == {"api_key": "key-1234"}


async def test_tokens_the_service_refuses_connect_nothing(
    harness: Harness, api: httpx.AsyncClient
) -> None:
    answer = await api.post(
        "/accounts", json={"integration": "fake", "name": "bot", "tokens": {"api_key": REFUSED_KEY}}
    )

    assert answer.status_code == 403
    assert await harness.db.list_records(Account, RecordQuery()) == []


@pytest.mark.parametrize(
    ("given", "status", "problem"),
    [
        ({"integration": "nope", "name": "x"}, 404, "No integration called 'nope'"),
        ({"integration": "fake", "name": "x", "access": ["send"]}, 400, "not connected for send"),
        ({"integration": "fake", "name": "x", "method": "tokens"}, 400, "takes them"),
        ({"integration": "fake", "name": "x", "tokens": {}}, 400, "api_key"),
        ({"integration": "fake", "name": "x", "method": "none"}, 400, "names what it watches"),
        ({"integration": "fake", "name": "x", "options": {"colour": 1}}, 400, "colour"),
    ],
)
async def test_what_cannot_connect_an_account_is_refused(
    api: httpx.AsyncClient, given: dict[str, Any], status: int, problem: str
) -> None:
    answer = await api.post("/accounts", json=given)

    assert answer.status_code == status, answer.text
    assert problem in answer.json()["message"]


# ---------------------------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------------------------


async def test_an_agent_connects_an_account_and_follows_it_up(
    harness: Harness, fakes: Fakes
) -> None:
    async with held(mcp_client_for(harness)) as client:
        result = await client.call_tool("connect_account", {"integration": "fake", "name": "work"})
        assert not result.is_error, result.content
        assert result.structured_content is not None
        url = str(result.structured_content["authorization_url"])
        assert urlsplit(url).netloc == "auth.example"

        Browser(fakes.server).open(url)  # (the user opens it: udl mcp's listener takes the answer)
        account = await _until_connected(harness)

    assert account.identity is not None
    assert account.identity.display == "me@example.com"


async def _until_connected(harness: Harness) -> Account:
    async with asyncio.timeout(5):
        # (Another task, the listener's, connects it: what it stores is polled.)
        while (account := await stored(harness)).status is not AccountStatus.CONNECTED:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
    return account


async def test_an_agent_connects_with_tokens(harness: Harness, fakes: Fakes) -> None:
    async with held(mcp_client_for(harness)) as client:
        result = await client.call_tool(
            "connect_account",
            {"integration": "fake", "name": "bot", "tokens": {"api_key": "key-1234"}},
        )

    assert not result.is_error, result.content
    assert result.structured_content is not None
    assert result.structured_content["account"]["status"] == "connected"


async def test_connecting_says_it_reaches_outside_udl(harness: Harness, fakes: Fakes) -> None:
    async with held(mcp_client_for(harness)) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}

    annotations = tools["connect_account"].annotations
    assert annotations is not None
    assert (annotations.open_world_hint, annotations.read_only_hint) == (True, False)


# ---------------------------------------------------------------------------------------------
# Names that are taken, and connecting again
# ---------------------------------------------------------------------------------------------


def tokens(key: str = "key-1234", **fields: Any) -> ConnectAccountRequest:
    return ConnectAccountRequest.model_validate(
        {"integration": "fake", "name": "bot", "tokens": {"api_key": key}, **fields}
    )


async def test_a_name_that_is_taken_is_refused(service: UDLService) -> None:
    await service.connect_account(tokens())

    with pytest.raises(AlreadyExists, match="fake:bot is connected already"):
        await service.connect_account(tokens())


async def test_connecting_again_keeps_the_account_and_replaces_its_credentials(
    harness: Harness, service: UDLService
) -> None:
    first = await service.connect_account(tokens(options={"label": "Bot"}))
    await service.db.upsert_record(Action(name="hello", plugin="fake"))
    await service.db.upsert_record(
        Subscription(name="bots", events=["fake.*"], accounts=["fake:bot"], actions=["hello"])
    )

    again = await service.connect_account(tokens("key-5678", reconnect=True))

    assert again.account.id == first.account.id
    assert again.account.options == {"label": "Bot"}  # (kept: none were given)
    assert again.account.identity is not None
    assert again.account.identity.display == "API key ...5678"
    account = await stored(harness, "bot")
    assert await kept_credentials(harness, account) == {"api_key": "key-5678"}
    assert [sub.name for sub in await service.db.list_records(Subscription, RecordQuery())] == [
        "bots"
    ]
    assert len(await connected_events(harness)) == 2


async def test_an_account_being_connected_can_be_connected_afresh(
    harness: Harness, fakes: Fakes, service: UDLService
) -> None:
    first = await service.connect_account(ConnectAccountRequest(integration="fake", name="work"))
    assert first.authorization_url is not None

    again = await service.connect_account(ConnectAccountRequest(integration="fake", name="work"))

    assert again.account.id == first.account.id
    with pytest.raises(NotAllowed):  # (the first authorization is forgotten)
        await service.accounts.complete(
            callback_query(fakes.server.approve(first.authorization_url))
        )
    assert again.authorization_url is not None
    done = await service.accounts.complete(
        callback_query(fakes.server.approve(again.authorization_url))
    )
    assert done.account.status is AccountStatus.CONNECTED


async def test_a_source_that_needs_no_login_has_no_credentials(
    harness: Harness, service: UDLService
) -> None:
    connection = await service.connect_account(
        ConnectAccountRequest(
            integration="fake",
            name="blog",
            method=ConnectMethod.NONE,
            options={"source": "https://example.com/blog"},
        )
    )

    account = await stored(harness, "blog")
    assert connection.account.status is AccountStatus.CONNECTED
    assert account.secret_ref is None
    assert account.identity is not None
    assert account.identity.id == "https://example.com/blog"


async def test_waiting_for_an_account_that_is_not_being_connected_does_not_exist(
    service: UDLService,
) -> None:
    connection = await service.connect_account(tokens())

    with pytest.raises(DoesNotExist):
        await service.accounts.finished(connection.account.id)


async def test_connecting_an_integration_that_does_not_connect_that_way_is_refused(
    tmp_path: Path, clock: FakeClock
) -> None:
    only_tokens = fake_integration("bots")
    only_tokens.connect = (ConnectMethod.TOKENS,)
    harness = harness_of(tmp_path, clock)
    harness.registry.register(INTEGRATIONS, only_tokens)
    async with harness.service() as service:
        with pytest.raises(ConfigError, match="bots accounts connect with tokens, not oauth"):
            await service.connect_account(
                ConnectAccountRequest(integration="bots", name="x", method=ConnectMethod.OAUTH)
            )


@pytest.mark.parametrize("refused", [False, True])
async def test_an_account_removed_while_being_connected_is_not_connected(
    harness: Harness, fakes: Fakes, service: UDLService, refused: bool
) -> None:
    connection = await service.connect_account(
        ConnectAccountRequest(integration="fake", name="work")
    )
    assert connection.authorization_url is not None
    await service.accounts.remove(await stored(harness))
    fakes.server.deny = refused

    with pytest.raises(NotAllowed, match="refused" if refused else "was removed"):
        await service.accounts.complete(
            callback_query(fakes.server.approve(connection.authorization_url))
        )

    assert await harness.db.list_records(Account, RecordQuery()) == []


async def test_connecting_again_that_fails_leaves_the_account_as_it_was(
    harness: Harness, fakes: Fakes, service: UDLService
) -> None:
    first = await service.connect_account(ConnectAccountRequest(integration="fake", name="work"))
    assert first.authorization_url is not None
    await service.accounts.complete(callback_query(fakes.server.approve(first.authorization_url)))
    again = await service.connect_account(
        ConnectAccountRequest(integration="fake", name="work", reconnect=True)
    )
    assert again.authorization_url is not None
    assert again.account.status is AccountStatus.CONNECTED  # (while it is connected again)
    fakes.server.deny = True

    with pytest.raises(NotAllowed):
        await service.accounts.complete(
            callback_query(fakes.server.approve(again.authorization_url))
        )

    account = await stored(harness)
    assert account.status is AccountStatus.CONNECTED
    assert account.last_error is not None
    assert (await service.accounts.handle(account)) is not None


async def test_an_authorization_the_browser_never_answers_fails(
    harness: Harness, service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("udl.integrations.accounts.PENDING_FOR", timedelta(milliseconds=50))
    connection = await service.connect_account(
        ConnectAccountRequest(integration="fake", name="work")
    )

    with pytest.raises(NotAllowed, match="No answer from the authorization page"):
        await service.accounts.finished(connection.account.id)

    assert (await stored(harness)).status is AccountStatus.ERROR


async def test_accounts_are_connected_side_by_side(
    harness: Harness, fakes: Fakes, service: UDLService
) -> None:
    home = await service.connect_account(ConnectAccountRequest(integration="fake", name="home"))
    await service.connect_account(ConnectAccountRequest(integration="fake", name="work"))
    await service.connect_account(ConnectAccountRequest(integration="fake", name="work"))
    assert home.authorization_url is not None

    done = await service.accounts.complete(
        callback_query(fakes.server.approve(home.authorization_url))
    )

    assert done.account.ref == "fake:home"  # (connecting work afresh left home's as it was)


@pytest.mark.parametrize(
    ("method", "callback"),
    [(ConnectMethod.OAUTH, None), (ConnectMethod.OAUTH, CALLBACK), (ConnectMethod.TOKENS, None)],
)
async def test_an_account_of_the_name_connected_meanwhile_is_refused(
    service: UDLService,
    monkeypatch: pytest.MonkeyPatch,
    method: ConnectMethod,
    callback: str | None,
) -> None:
    async def clash(account: Account) -> Account:
        raise DuplicateKey("taken")

    monkeypatch.setattr(service.accounts, "save", clash)
    service.accounts.callback_url = callback  # (the server's, else a listener here)
    given = {"api_key": "key-1234"} if method is ConnectMethod.TOKENS else None

    with pytest.raises(AlreadyExists, match="was connected meanwhile"):
        await service.connect_account(
            ConnectAccountRequest.model_validate(
                {"integration": "fake", "name": "work", "method": method, "tokens": given}
            )
        )


def test_an_integration_that_takes_tokens_it_does_not_describe_is_a_plugin_error(
    tmp_path: Path, clock: FakeClock
) -> None:
    broken = fake_integration("broken")
    broken.token_model = None
    harness = harness_of(tmp_path, clock)
    harness.registry.register(INTEGRATIONS, broken)

    result = CLI(harness)("accounts", "add", "broken", "x", "--method", "tokens")

    assert result.exit_code == 1
    assert "The broken integration has no token_model" in result.output


def test_the_cli_refuses_tokens_on_stdin_that_are_not_json(harness: Harness, fakes: Fakes) -> None:
    result = CLI(harness)("accounts", "add", "fake", "bot", "--tokens-stdin", stdin="key-1234")

    assert result.exit_code == 2
    assert "The tokens on stdin are not JSON" in result.output


async def test_closing_udl_stops_waiting_for_an_authorization(
    harness: Harness, fakes: Fakes
) -> None:
    async with harness.service() as service:
        connection = await service.connect_account(
            ConnectAccountRequest(integration="fake", name="work")
        )
        assert connection.authorization_url is not None

    async with harness.service() as again:
        account = await again.accounts.get("fake:work")
    assert account.status is AccountStatus.PENDING  # (it expires in its time)
