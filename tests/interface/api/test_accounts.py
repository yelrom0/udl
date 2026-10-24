"""
--------------------
- test_accounts.py -
--------------------

/accounts: listing, showing, changing, checking and disconnecting accounts (connecting them:
tests/integration/test_connecting.py), on two fake accounts
"""

# Package Imports
import httpx
import pytest

# Local Imports
from support.accounts import Fakes, with_two_accounts
from support.interfaces import Harness


@pytest.fixture
def fakes(harness: Harness) -> Fakes:
    return with_two_accounts(harness)


async def test_the_accounts_are_listed(fakes: Fakes, client: httpx.AsyncClient) -> None:
    every = await client.get("/accounts")
    some = await client.get("/accounts", params={"integration": "other"})

    assert every.status_code == 200
    assert [found["account"] for found in every.json()["accounts"]] == ["fake:home", "fake:work"]
    assert some.json() == {"accounts": []}


async def test_an_account_is_shown(fakes: Fakes, client: httpx.AsyncClient) -> None:
    shown = await client.get("/accounts/fake:work")
    unknown = await client.get("/accounts/fake:play")
    nameless = await client.get("/accounts/work")

    assert shown.status_code == 200
    assert (shown.json()["status"], shown.json()["access"]) == ("connected", ["read", "write"])
    assert (unknown.status_code, unknown.json()["code"]) == (404, "does_not_exist")
    assert nameless.status_code == 400


async def test_an_account_is_changed(fakes: Fakes, client: httpx.AsyncClient) -> None:
    changed = await client.patch(
        "/accounts/fake:work", json={"options": {"label": None}, "enabled": False}
    )
    refused = await client.patch("/accounts/fake:work", json={"options": {"colour": "red"}})

    assert changed.status_code == 200, changed.text
    assert (changed.json()["options"], changed.json()["enabled"]) == ({}, False)
    assert refused.status_code == 400


async def test_an_account_is_checked(fakes: Fakes, client: httpx.AsyncClient) -> None:
    answer = await client.post("/accounts/fake:home/check")

    assert answer.status_code == 200
    assert answer.json() == {"ok": True, "problems": [], "scopes": []}


async def test_an_account_is_disconnected(fakes: Fakes, client: httpx.AsyncClient) -> None:
    answer = await client.delete("/accounts/fake:work")
    gone = await client.get("/accounts/fake:work")

    assert answer.status_code == 200
    assert answer.json() == {"deleted": True, "revoked": True, "subscriptions": ["work-things"]}
    assert gone.status_code == 404
