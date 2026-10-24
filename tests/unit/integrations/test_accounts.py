"""
--------------------
- test_accounts.py -
--------------------

Accounts: named per integration (gmail:work), each with credentials of its own in the secret
store; a handle gives them fresh (an expired OAuth token refreshed once, however many ask at
once) or raises ReauthRequired (refused, or missing as on an account synced from another
machine), marking the account needs_reauth and saying so once per streak; and each integration
is made once per service, from its plugin settings
"""

# System Imports
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.accounts import Fakes, connected, issued, with_fake_integration
from support.actions import FakeAction, FakeActionOptions
from support.clock import FakeClock
from support.integrations import FakeAccountOptions, FakeSettings, FakeTokens
from support.interfaces import Harness
from udl.automations.bus import NewEvent
from udl.automations.events import AccountEventData, EventRecord
from udl.automations.models import RunActionRequest, SubscriptionTestRequest
from udl.automations.subscriptions import Subscription
from udl.core.models import SetPluginSettingsRequest
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.errors import ConfigError, DoesNotExist, DuplicateKey, ReauthRequired
from udl.integrations.accounts import AccountRef
from udl.plugins.actions.base import Action
from udl.plugins.integrations.base import Account, AccountStatus, ConnectMethod
from udl.plugins.registry import ACTIONS
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


async def needs_reauth_events(service: UDLService) -> list[AccountEventData]:
    found = await service.db.list_records(
        EventRecord, RecordQuery(where={"type": "udl.account.needs_reauth"})
    )
    return [AccountEventData.model_validate(event.data) for event in found]


# ---------------------------------------------------------------------------------------------
# Naming accounts
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "integration", "found"),
    [
        ("gmail:work", None, ("gmail", "work")),
        ("gmail:work", "gmail", ("gmail", "work")),
        ("work", "gmail", ("gmail", "work")),
        ("slack:acme-corp.1", None, ("slack", "acme-corp.1")),
    ],
)
def test_an_account_is_integration_colon_name_or_its_name_where_the_integration_is_known(
    text: str, integration: str | None, found: tuple[str, str]
) -> None:
    ref = AccountRef.parse(text, integration)

    assert (ref.integration, ref.name) == found
    assert str(ref) == f"{found[0]}:{found[1]}"


@pytest.mark.parametrize(
    ("text", "integration", "problem"),
    [
        ("work", None, "names no integration"),
        ("slack:acme", "gmail", "is not a gmail account"),
        ("gmail:", None, "is not an account"),
        ("gmail:a:b", None, "is not an account"),
        (":work", None, "is not an account"),
    ],
)
def test_what_is_not_an_account_is_a_config_error(
    text: str, integration: str | None, problem: str
) -> None:
    with pytest.raises(ConfigError, match=problem):
        AccountRef.parse(text, integration)


async def test_account_names_are_unique_per_integration(service: UDLService) -> None:
    await connected(service, "work")
    await connected(service, "work", integration="other")

    with pytest.raises(DuplicateKey):
        await connected(service, "work")


async def test_an_account_is_found_by_its_name(service: UDLService) -> None:
    work = await connected(service, "work")

    assert (await service.accounts.get("fake:work")).id == work.id
    assert (await service.accounts.get("work", integration="fake")).id == work.id
    assert await service.accounts.find("fake", "home") is None


async def test_an_unknown_account_does_not_exist_and_the_error_names_the_others(
    service: UDLService,
) -> None:
    await connected(service, "work")
    await connected(service, "home")

    with pytest.raises(DoesNotExist, match="No fake account called 'play'") as raised:
        await service.accounts.get("fake:play")

    assert raised.value.hint is not None
    assert "fake:home, fake:work" in raised.value.hint


async def test_accounts_are_listed_by_integration_then_name(service: UDLService) -> None:
    for integration, name in (("fake", "work"), ("other", "a"), ("fake", "home")):
        await connected(service, name, integration=integration)

    every = [account.ref for account in await service.accounts.list()]
    fake = [account.ref for account in await service.accounts.list("fake")]

    assert every == ["fake:home", "fake:work", "other:a"]
    assert fake == ["fake:home", "fake:work"]


async def test_the_bus_knows_accounts_by_name(service: UDLService) -> None:
    work = await connected(service, "work")
    home = await connected(service, "home")
    await service.db.upsert_record(Action(name="hello", plugin="fake"))
    await service.db.upsert_record(
        Subscription(
            name="work-only",
            events=["fake.thing.happened"],
            accounts=["fake:work"],
            actions=["hello"],
        )
    )

    assert await service.accounts.names() == {work.id: "fake:work", home.id: "fake:home"}
    queued: dict[str, int] = {}
    for account in (work, home):
        event = await service.bus.publish(
            NewEvent(
                type="fake.thing.happened",
                data={"thing": "t1", "text": "Hi"},
                source="fake",
                account_id=account.id,
                dedupe_key=f"{account.id}:t1",
            )
        )
        assert event is not None
        queued[account.name] = len(await service.bus.dispatch(event))
    assert queued == {"work": 1, "home": 0}


async def publish_thing(service: UDLService, account: Account, thing: str = "t1") -> EventRecord:
    event = await service.bus.publish(
        NewEvent(
            type="fake.thing.happened",
            data={"thing": thing, "text": "Hi"},
            source="fake",
            account_id=account.id,
            dedupe_key=f"{account.id}:{thing}",
        )
    )
    assert event is not None
    return event


async def test_an_account_connected_after_a_dispatch_is_known_at_the_next(
    service: UDLService,
) -> None:
    await service.db.upsert_record(Action(name="hello", plugin="fake"))
    await service.db.upsert_record(
        Subscription(name="work-only", events=["fake.*"], accounts=["fake:work"], actions=["hello"])
    )
    home = await connected(service, "home")
    await service.bus.dispatch(await publish_thing(service, home))  # (it knows home now)

    work = await connected(service, "work")

    assert len(await service.bus.dispatch(await publish_thing(service, work))) == 1


async def test_a_subscription_is_tried_on_the_events_of_its_accounts(service: UDLService) -> None:
    work = await connected(service, "work")
    home = await connected(service, "home")
    await publish_thing(service, work, "t1")
    await publish_thing(service, home, "t2")

    report = await service.try_subscription(
        SubscriptionTestRequest(events=["fake.thing.happened"], accounts=["fake:work"])
    )

    assert sorted((found.event.data["thing"], found.failed) for found in report.events) == [
        ("t1", None),
        ("t2", "its account"),
    ]


async def test_a_run_s_templates_see_its_event_s_account(
    harness: Harness, service: UDLService
) -> None:
    harness.registry.register(ACTIONS, FakeAction)
    work = await connected(service, "work")
    action = await service.db.upsert_record(
        Action(name="say", plugin="fake", options={"output": "{{ event.account }}"})
    )

    run = await service.runner.run_now(action, await publish_thing(service, work))
    shown = await service.run_action(RunActionRequest(name="say", event=run.event_id, dry_run=True))

    assert run.output == "fake:work"
    assert shown.options == {**FakeActionOptions(output="fake:work").model_dump(mode="json")}


# ---------------------------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------------------------


async def test_credentials_are_kept_in_the_secret_store_never_in_the_account(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    tokens = issued(fakes.server, clock)

    account = await connected(service, credentials=tokens)

    ref = SecretRef.of("accounts", account.id, "credentials")
    assert account.secret_ref == ref
    kept = await service.secrets.get(ref)
    assert kept is not None
    assert (
        json.loads(kept.get_secret_value())["access_token"]
        == tokens.access_token.get_secret_value()
    )
    stored = await service.db.get_record(Account, account.id)
    assert stored is not None
    assert tokens.access_token.get_secret_value() not in stored.model_dump_json()


async def test_each_account_has_credentials_of_its_own(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    work_tokens, home_tokens = issued(fakes.server, clock), issued(fakes.server, clock)
    work = await connected(service, "work", credentials=work_tokens)
    home = await connected(service, "home", credentials=home_tokens)

    assert await (await service.accounts.handle(work)).tokens() == work_tokens
    assert await (await service.accounts.handle(home)).tokens() == home_tokens


async def test_tokens_an_account_was_given_are_its_credentials(service: UDLService) -> None:
    given = FakeTokens(api_key=SecretStr("key-1234"))
    account = await connected(service, method=ConnectMethod.TOKENS, credentials=given)

    assert await (await service.accounts.handle(account)).credentials() == given


async def test_an_account_that_needs_no_login_has_no_credentials(service: UDLService) -> None:
    account = await connected(service, method=ConnectMethod.NONE, options={"source": "x"})

    assert await (await service.accounts.handle(account)).credentials() is None


async def test_a_handle_s_options_are_its_integration_s(service: UDLService) -> None:
    account = await connected(service, options={"label": "Work"})

    handle = await service.accounts.handle(account)

    assert handle.options == FakeAccountOptions(label="Work")


async def test_a_handle_on_options_its_integration_refuses_is_a_config_error(
    service: UDLService,
) -> None:
    account = await connected(service, options={"colour": "red"})

    with pytest.raises(ConfigError, match="options are not valid"):
        await service.accounts.handle(account)


async def test_an_expired_token_is_refreshed_once_however_many_ask(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    tokens = issued(fakes.server, clock, expires_in=timedelta(seconds=30))
    account = await connected(service, credentials=tokens)
    handle = await service.accounts.handle(account)

    found = await asyncio.gather(*(handle.tokens() for _ in range(5)))

    assert fakes.server.refreshed == 1
    assert len({tokens.access_token.get_secret_value() for tokens in found}) == 1
    assert found[0].access_token != tokens.access_token
    assert found[0].refresh_token == tokens.refresh_token  # (kept: none came)
    again = await (await service.accounts.handle(account)).tokens()
    assert again == found[0]  # (saved)


async def test_a_token_that_does_not_expire_soon_is_not_refreshed(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    tokens = issued(fakes.server, clock, expires_in=timedelta(minutes=5))
    account = await connected(service, credentials=tokens)

    assert await (await service.accounts.handle(account)).tokens() == tokens
    assert fakes.server.refreshed == 0


async def test_a_refused_refresh_marks_the_account_needs_reauth_and_says_so_once(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    account = await connected(
        service, credentials=issued(fakes.server, clock, expires_in=timedelta(0))
    )
    fakes.server.revoke_refresh_tokens()  # (the user took the access back)
    handle = await service.accounts.handle(account)

    for _ in range(2):
        with pytest.raises(ReauthRequired):
            await handle.credentials()

    found = await service.accounts.get(account.ref)
    assert found.status is AccountStatus.NEEDS_REAUTH
    assert found.last_error is not None
    assert found.last_error.code == "reauth_required"
    [said] = await needs_reauth_events(service)
    assert (said.account, said.account_id, said.integration) == ("fake:work", account.id, "fake")


async def test_missing_credentials_are_as_good_as_refused(service: UDLService) -> None:
    # (An account synced from another machine has no credentials in this one's store.)
    account = await connected(
        service, secret_ref=SecretRef("accounts/00000000-0000-4000-8000-000000000009/credentials")
    )

    with pytest.raises(ReauthRequired, match="has no credentials for fake:work"):
        await (await service.accounts.handle(account)).credentials()

    assert (await service.accounts.get(account.ref)).status is AccountStatus.NEEDS_REAUTH
    assert len(await needs_reauth_events(service)) == 1


async def test_an_account_connected_again_starts_a_new_streak(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    account = await connected(service)  # (no credentials)
    with pytest.raises(ReauthRequired):
        await (await service.accounts.handle(account)).credentials()

    again = await service.accounts.keep_credentials(
        (await service.accounts.get(account.ref)).model_copy(
            update={"status": AccountStatus.CONNECTED}
        ),
        issued(fakes.server, clock, expires_in=timedelta(0)),
    )
    await service.accounts.save(again)
    fakes.server.revoke_refresh_tokens()
    with pytest.raises(ReauthRequired):
        await (await service.accounts.handle(again)).credentials()

    assert len(await needs_reauth_events(service)) == 2


async def test_an_integration_that_finds_the_credentials_refused_marks_the_account(
    service: UDLService,
) -> None:
    account = await connected(service, method=ConnectMethod.NONE, options={"source": "x"})

    await service.accounts.needs_reauth(account, ReauthRequired("Refused"))
    await service.accounts.needs_reauth(account, ReauthRequired("Refused again"))

    found = await service.accounts.get(account.ref)
    assert found.status is AccountStatus.NEEDS_REAUTH
    assert found.last_error is not None
    assert found.last_error.message == "Refused"
    assert len(await needs_reauth_events(service)) == 1


async def test_a_handle_on_an_account_that_is_gone_is_refused(service: UDLService) -> None:
    account = await connected(service, method=ConnectMethod.TOKENS)
    handle = await service.accounts.handle(account)
    await service.accounts.remove(account)

    with pytest.raises(ReauthRequired, match="no longer connected"):
        await handle.credentials()


async def test_removing_an_account_forgets_its_credentials(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    account = await connected(service, credentials=issued(fakes.server, clock))
    assert account.secret_ref is not None

    assert await service.accounts.remove(account)

    assert await service.secrets.get(account.secret_ref) is None
    assert await service.accounts.find("fake", "work") is None
    assert not await service.accounts.remove(account)


# ---------------------------------------------------------------------------------------------
# Integrations
# ---------------------------------------------------------------------------------------------


async def test_an_integration_is_made_once_per_service_from_its_settings(
    tmp_path: Path, clock: FakeClock
) -> None:
    harness = Harness(tmp_path, clock=clock, integrations={"fake": {"client_id": "from-the-file"}})
    with_fake_integration(harness)
    async with harness.service() as service:
        made = await service.integrations.get("fake")

        assert made is await service.integrations.get("fake")
        assert made.settings == FakeSettings(client_id="from-the-file")
        assert made.clock is clock

        await service.set_plugin_settings(
            SetPluginSettingsRequest(name="fake", values={"client_id": "set-here"})
        )
        again = await service.integrations.get("fake")

        assert again is not made
        assert again.settings.client_id == "set-here"


async def test_an_unknown_integration_does_not_exist(service: UDLService) -> None:
    with pytest.raises(DoesNotExist, match="No integration called 'nope'") as raised:
        await service.integrations.get("nope")

    assert raised.value.hint is not None
    assert "fake" in raised.value.hint
