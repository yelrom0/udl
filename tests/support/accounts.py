"""
---------------
- accounts.py -
---------------

Accounts in the tests: a Harness with a fake integration (support.integrations) and the fake
authorization server its OAuth accounts connect against, accounts stored as connected (without
going through connecting), tokens that server issued, and a browser that answers an
authorization page
"""

# System Imports
import secrets
import threading
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

# Package Imports
import httpx
from pydantic import BaseModel, SecretStr

# Local Imports
from support.clock import FakeClock
from support.integrations import FakeIntegration, FakeTokens, fake_integration, http_to
from support.interfaces import Harness
from support.oauth import BASE, FakeAuthorizationServer
from udl.automations.subscriptions import Subscription
from udl.clock import SystemClock
from udl.core.service import UDLService
from udl.db.manager import DBManager
from udl.integrations.oauth import Tokens
from udl.plugins.actions.base import Action
from udl.plugins.integrations.base import (
    Account,
    AccountIdentity,
    AccountStatus,
    ConnectMethod,
)
from udl.plugins.registry import INTEGRATIONS


@dataclass
class Fakes:
    """A fake integration, and the authorization server its OAuth accounts connect against."""

    plugin: type[FakeIntegration]
    server: FakeAuthorizationServer


def with_fake_integration(harness: Harness, called: str = "fake") -> Fakes:
    """`harness`'s services, with a fake integration (with feeds of its own) called `called`,
    whose HTTP clients reach the fake authorization server."""
    fakes = Fakes(fake_integration(called), FakeAuthorizationServer())
    harness.registry.register(INTEGRATIONS, fakes.plugin)
    harness.http = http_to((BASE, fakes.server.app()))
    return fakes


def issued(
    server: FakeAuthorizationServer,
    clock: FakeClock | SystemClock,
    *,
    scope: str = "fake.read",
    expires_in: timedelta = timedelta(hours=1),
) -> Tokens:
    """Tokens `server` issued (as if an authorization gave them), expiring in `expires_in`."""
    access, refresh = secrets.token_urlsafe(8), secrets.token_urlsafe(8)
    server.access_tokens.add(access)
    server.refresh_tokens[refresh] = scope
    return Tokens(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        expires_at=clock.now() + expires_in,
        scopes=scope.split(),
    )


async def connected(
    service: UDLService,
    name: str = "work",
    *,
    integration: str = "fake",
    method: ConnectMethod = ConnectMethod.OAUTH,
    credentials: BaseModel | None = None,
    **fields: Any,
) -> Account:
    """An account stored as connected (with `credentials` kept), as connecting leaves one."""
    fields.setdefault("access", ["read"])
    fields.setdefault("connected_at", service.clock.now())
    account = Account.model_validate(
        {
            "integration": integration,
            "name": name,
            "method": method,
            "status": AccountStatus.CONNECTED,
            "identity": AccountIdentity(id=f"id-{name}", display=f"{name}@example.com"),
            **fields,
        }
    )
    saved = await service.accounts.save(account)
    if credentials is None:
        return saved
    return await service.accounts.keep_credentials(saved, credentials)


def callback_query(url: str) -> dict[str, str]:
    """The query of a URL an authorization page sent the browser back to."""
    return dict(parse_qsl(urlsplit(url).query))


class Browser:
    """A browser that answers authorization pages at once (as the user signing in would),
    following the redirect back to udl."""

    def __init__(self, server: FakeAuthorizationServer) -> None:
        self.server = server
        self.opened: list[str] = []

    def open(self, url: str) -> bool:
        """What webbrowser.open does: answer the page at `url` (on another thread, as the user
        does while udl waits)."""
        self.opened.append(url)
        back = self.server.approve(url)
        threading.Thread(target=httpx.get, args=(back,), kwargs={"timeout": 5}, daemon=True).start()
        return True


def with_two_accounts(harness: Harness) -> Fakes:
    """`harness`, with a fake integration and two of its accounts: fake:work (connected with
    OAuth, for read and write, labelled Work) and fake:home (with an API key), and a
    subscription (work-things) that names fake:work."""
    fakes = with_fake_integration(harness)

    async def seed(db: DBManager) -> None:
        service = harness.service()
        tokens = issued(fakes.server, SystemClock(), scope="fake.read fake.write")
        await connected(
            service, "work", credentials=tokens, access=["read", "write"], options={"label": "Work"}
        )
        await connected(
            service,
            "home",
            method=ConnectMethod.TOKENS,
            credentials=FakeTokens(api_key=SecretStr("key-1234")),
        )
        await db.upsert_record(Action(name="hello", plugin="fake"))
        await db.upsert_record(
            Subscription(
                name="work-things", events=["fake.*"], accounts=["fake:work"], actions=["hello"]
            )
        )

    harness.seed(seed)
    return fakes
