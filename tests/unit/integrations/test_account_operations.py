"""
------------------------------
- test_account_operations.py -
------------------------------

The accounts' operations: listing them (by integration), showing one (with its watcher),
changing its options or whether udl uses it, checking it, and disconnecting it, which gives its
credentials back, forgets them and its watcher's state, and says which subscriptions named it
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.accounts import Fakes, connected, issued, with_fake_integration
from support.clock import FakeClock
from support.integrations import FakeTokens
from support.interfaces import Harness
from udl.automations.bus import NewEvent
from udl.automations.models import ListEventsRequest
from udl.automations.subscriptions import Subscription
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import ConfigError, DoesNotExist
from udl.integrations.models import (
    AccountRequest,
    ListAccountsRequest,
    SetAccountRequest,
)
from udl.integrations.watchers import PluginState, WatcherState
from udl.plugins.actions.base import Action
from udl.plugins.integrations.base import Account, AccountStatus, ConnectMethod
from udl.plugins.secrets.base import SecretRef


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock) -> Harness:
    return Harness(tmp_path, clock=clock)


@pytest.fixture
def fakes(harness: Harness) -> Fakes:
    return with_fake_integration(harness)


@pytest.fixture
async def service(harness: Harness, fakes: Fakes) -> AsyncIterator[UDLService]:
    async with harness.service() as found:
        yield found


def ref(account: str = "fake:work") -> AccountRequest:
    return AccountRequest(account=account)


async def test_the_accounts_are_listed_by_integration_and_name(service: UDLService) -> None:
    for integration, name in (("fake", "work"), ("other", "a"), ("fake", "home")):
        await connected(service, name, integration=integration)

    every = await service.list_accounts(ListAccountsRequest())
    fake = await service.list_accounts(ListAccountsRequest(integration="fake"))

    assert [found.account for found in every.accounts] == ["fake:home", "fake:work", "other:a"]
    assert [found.account for found in fake.accounts] == ["fake:home", "fake:work"]


async def test_an_account_is_shown_with_its_watcher(service: UDLService) -> None:
    work = await connected(service, options={"label": "Work"})
    await service.db.upsert_record(
        PluginState(plugin="fake", account_id=work.id, watcher=WatcherState.BACKOFF, failures=2)
    )

    shown = await service.get_account(ref())

    assert (shown.account, shown.status, shown.options) == (
        "fake:work",
        AccountStatus.CONNECTED,
        {"label": "Work"},
    )
    assert shown.watcher is not None
    assert (shown.watcher.state, shown.watcher.failures) == (
        WatcherState.STOPPED,
        2,
    )  # (no runtime)
    assert (await service.get_account(ref())).identity == work.identity


async def test_an_account_without_a_watcher_shows_none(service: UDLService) -> None:
    await connected(service)

    assert (await service.get_account(ref())).watcher is None


async def test_an_unknown_account_does_not_exist(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match="No fake account called 'work'"):
        await service.get_account(ref())


# ---------------------------------------------------------------------------------------------
# Changing one
# ---------------------------------------------------------------------------------------------


async def test_setting_an_account_s_options_sets_those_given(service: UDLService) -> None:
    await connected(service, options={"label": "Work", "source": "x"})

    shown = await service.set_account(
        SetAccountRequest(account="fake:work", options={"label": "Office", "source": None})
    )

    assert shown.options == {"label": "Office"}  # (null took source away)
    assert (await service.accounts.get("fake:work")).options == {"label": "Office"}


async def test_an_account_s_options_are_its_integration_s(service: UDLService) -> None:
    await connected(service)

    with pytest.raises(ConfigError, match="colour"):
        await service.set_account(SetAccountRequest(account="fake:work", options={"colour": 1}))


async def test_an_account_can_be_disabled_and_enabled(service: UDLService) -> None:
    await connected(service, options={"label": "Work"})

    off = await service.set_account(SetAccountRequest(account="fake:work", enabled=False))
    on = await service.set_account(SetAccountRequest(account="fake:work", enabled=True))

    assert (off.enabled, on.enabled) == (False, True)
    assert on.options == {"label": "Work"}  # (left as they were)


# ---------------------------------------------------------------------------------------------
# Checking one
# ---------------------------------------------------------------------------------------------


async def test_checking_an_account_says_it_works_and_what_it_was_granted(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await connected(service, credentials=issued(fakes.server, clock, scope="fake.read"))

    health = await service.check_account(ref())

    assert (health.ok, health.problems, health.scopes) == (True, [], ["fake.read"])


async def test_checking_an_account_whose_credentials_are_refused_says_why(
    service: UDLService,
) -> None:
    await connected(service)  # (no credentials here)

    health = await service.check_account(ref())

    assert health.ok is False
    assert "has no credentials for fake:work" in health.problems[0]
    assert (await service.accounts.get("fake:work")).status is AccountStatus.NEEDS_REAUTH


# ---------------------------------------------------------------------------------------------
# Disconnecting one
# ---------------------------------------------------------------------------------------------


async def test_disconnecting_gives_the_credentials_back_and_forgets_the_account(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    tokens = issued(fakes.server, clock)
    work = await connected(service, credentials=tokens)
    await service.db.upsert_record(PluginState(plugin="fake", account_id=work.id, state={}))
    await service.db.upsert_record(Action(name="hello", plugin="fake"))
    for name, accounts in (("work-mail", ["fake:work"]), ("all", None), ("home", ["fake:home"])):
        await service.db.upsert_record(
            Subscription(name=name, events=["fake.*"], accounts=accounts, actions=["hello"])
        )

    answer = await service.disconnect_account(ref())

    assert (answer.deleted, answer.revoked, answer.subscriptions) == (True, True, ["work-mail"])
    assert tokens.refresh_token is not None
    assert fakes.server.revoked == [tokens.refresh_token.get_secret_value()]
    assert work.secret_ref is not None
    assert await service.secrets.get(work.secret_ref) is None
    assert await service.db.get_record(Account, work.id) is None
    assert await service.db.list_records(PluginState, RecordQuery()) == []
    assert len(await service.db.list_records(Subscription, RecordQuery())) == 3  # (kept)


async def test_disconnecting_an_account_that_is_gone_changes_nothing(service: UDLService) -> None:
    answer = await service.disconnect_account(ref())

    assert (answer.deleted, answer.revoked, answer.subscriptions) == (False, None, [])


@pytest.mark.parametrize(
    ("fields", "revoked"),
    [
        ({"method": ConnectMethod.NONE}, None),  # (nothing to give back)
        # (Tokens the user gave are theirs to revoke.)
        ({"method": ConnectMethod.TOKENS, "credentials": FakeTokens(api_key=SecretStr("k"))}, None),
        (  # (its credentials are missing here)
            {"method": ConnectMethod.OAUTH, "secret_ref": SecretRef("accounts/x/credentials")},
            None,
        ),
    ],
)
async def test_disconnecting_gives_back_what_there_is_to_give_back(
    service: UDLService, fields: dict[str, Any], revoked: bool | None
) -> None:
    await connected(service, **fields)

    answer = await service.disconnect_account(ref())

    assert (answer.deleted, answer.revoked) == (True, revoked)


async def test_an_account_whose_credentials_cannot_be_given_back_is_disconnected_still(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await connected(service, credentials=issued(fakes.server, clock))
    fakes.server.fail_revoke = True

    answer = await service.disconnect_account(ref())

    assert (answer.deleted, answer.revoked) == (True, False)
    assert await service.accounts.find("fake", "work") is None


async def test_events_are_listed_by_their_account_s_name(service: UDLService) -> None:
    work = await connected(service, "work")
    home = await connected(service, "home")
    for account in (work, home):
        await service.bus.publish(
            NewEvent(
                type="fake.thing.happened",
                data={"thing": account.name, "text": "x"},
                account_id=account.id,
            )
        )

    found = await service.list_events(ListEventsRequest(account="fake:work"))

    assert [event.data["thing"] for event in found.events] == ["work"]
    with pytest.raises(DoesNotExist):
        await service.list_events(ListEventsRequest(account="fake:play"))


async def test_checking_an_account_its_integration_cannot_use_says_why(
    service: UDLService,
) -> None:
    await connected(service, options={"colour": "red"})

    health = await service.check_account(ref())

    assert health.ok is False
    assert "options are not valid" in health.problems[0]
