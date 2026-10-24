"""
----------------------
- test_operations.py -
----------------------

An integration's operations, with FakeIntegration: each takes `account` first (optional while
the integration has one account, required with two, an unknown one naming the others); one that
fans out runs on a list of accounts, or every one ("*"), at once (at most 4 at a time), merging
their items tagged with their accounts and listing the accounts that failed; one that writes
takes one account; the account must have been connected for what the operation needs
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, ClassVar

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from support.accounts import Fakes, connected, issued, with_fake_integration
from support.clock import FakeClock
from support.integrations import (
    DoneThing,
    DoThingRequest,
    FakeAccountOptions,
    FakeIntegration,
    ListThingsRequest,
    Thing,
    VersionRequest,
)
from support.interfaces import Harness
from udl.core.service import UDLService
from udl.errors import (
    AmbiguousAccount,
    ConfigError,
    DoesNotExist,
    NotAllowed,
    ReauthRequired,
)
from udl.integrations.operations import FAN_OUT, FanOutResult
from udl.plugins.integrations.base import (
    AccountHandle,
    AccountStatus,
    ConnectMethod,
    ItemsPage,
    OperationHints,
    integration_operation,
)
from udl.plugins.registry import INTEGRATIONS


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


async def account_with_things(
    service: UDLService, fakes: Fakes, clock: FakeClock, name: str, *things: str, **fields: Any
) -> None:
    fields.setdefault("credentials", issued(fakes.server, clock))
    await connected(service, name, **fields)
    fakes.plugin.feed(name).add(*things)


async def listed(service: UDLService, **request: Any) -> FanOutResult[Thing]:
    found = await service.call("fake_list_things", request)
    return FanOutResult[Thing].model_validate(found.model_dump())


def items_of(result: FanOutResult[Thing]) -> list[tuple[str, str]]:
    return sorted((found.account, found.item.text) for found in result.items)


# ---------------------------------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------------------------------


def test_an_integration_s_operations_are_named_after_it(service: UDLService) -> None:
    ops = service.operations.in_namespace("fake")

    assert [op.name for op in ops] == ["fake_list_things", "fake_do_thing", "fake_version"]
    assert [op.kind for op in ops] == ["udl.integrations"] * 3
    assert ops[0].hints == OperationHints(read_only=True)
    assert ops[0].description == FakeIntegration.list_things.__doc__


def test_an_operation_s_request_has_the_account_first_documented(service: UDLService) -> None:
    listing = service.operations.get("fake_list_things").request
    doing = service.operations.get("fake_do_thing").request

    assert listing.__name__ == "FakeListThingsRequest"
    schema = listing.model_json_schema()
    assert list(schema["properties"]) == ["account", "contains", "page", "limit"]
    assert "* for every one" in schema["properties"]["account"]["description"]
    assert "fake:work" in schema["properties"]["account"]["description"]
    assert issubclass(listing, ListThingsRequest)  # (its validators too)
    assert list(doing.model_json_schema()["properties"]) == ["account", "text"]
    assert service.operations.get("fake_version").request is VersionRequest


def test_an_operation_s_answer_is_its_method_s_or_for_a_fan_out_its_items_by_account(
    service: UDLService,
) -> None:
    assert service.operations.get("fake_list_things").response is FanOutResult[Thing]
    assert service.operations.get("fake_do_thing").response is DoneThing


def test_a_request_with_its_own_account_field_is_a_plugin_problem(tmp_path: Path) -> None:
    class Clashing(FakeIntegration):
        name = "clashing"

        @integration_operation("mine", title="Mine")
        async def mine(
            self, account: AccountHandle[FakeAccountOptions], request: DoneThing
        ) -> DoneThing:
            """Has an account of its own."""
            return request

    harness = Harness(tmp_path)
    harness.registry.register(INTEGRATIONS, Clashing)
    service = harness.service()

    assert "clashing_mine" not in service.operations
    [problem] = service.registry.problems
    assert "has a field called account" in problem.error


# ---------------------------------------------------------------------------------------------
# Which account
# ---------------------------------------------------------------------------------------------


async def test_the_account_may_be_left_out_while_there_is_one(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work", "a", "b")

    result = await listed(service)

    assert items_of(result) == [("fake:work", "a"), ("fake:work", "b")]
    assert result.failures == []


async def test_the_account_is_said_once_there_are_two(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work", "a")
    await account_with_things(service, fakes, clock, "home", "b")

    with pytest.raises(AmbiguousAccount, match="There are 2") as raised:
        await listed(service)

    assert raised.value.hint is not None
    assert "fake:home, fake:work" in raised.value.hint
    assert items_of(await listed(service, account="work")) == [("fake:work", "a")]
    assert items_of(await listed(service, account="fake:home")) == [("fake:home", "b")]


async def test_an_unknown_account_does_not_exist_and_the_error_names_the_others(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work")

    with pytest.raises(DoesNotExist, match="No fake account called 'play'") as raised:
        await listed(service, account="play")

    assert raised.value.hint == "The fake accounts: fake:work."


async def test_an_account_of_another_integration_is_refused(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work")

    with pytest.raises(ConfigError, match="is not a fake account"):
        await listed(service, account="gmail:work")


async def test_without_accounts_there_is_nothing_to_run_on(service: UDLService) -> None:
    for account in (None, "*"):
        with pytest.raises(DoesNotExist, match="No fake account is connected"):
            await listed(service, account=account)


async def test_accounts_not_connected_yet_or_disabled_are_left_out(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work", "a")
    await connected(service, "new", status=AccountStatus.PENDING, connected_at=None)  # (never)
    await account_with_things(service, fakes, clock, "old", "b", enabled=False)

    assert items_of(await listed(service)) == [("fake:work", "a")]
    with pytest.raises(NotAllowed, match="fake:old is disabled"):
        await listed(service, account="old")


async def test_an_operation_that_needs_no_account_takes_none(service: UDLService) -> None:
    answer = await service.call("fake_version", {})

    assert answer.model_dump() == {"version": "1.0"}


# ---------------------------------------------------------------------------------------------
# Several accounts at once
# ---------------------------------------------------------------------------------------------


async def test_a_fan_out_runs_on_every_account_and_tags_what_each_found(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work", "a", "b")
    await account_with_things(service, fakes, clock, "home", "c")

    every = await listed(service, account="*")
    some = await listed(service, account=["work"])

    assert items_of(every) == [("fake:home", "c"), ("fake:work", "a"), ("fake:work", "b")]
    assert items_of(some) == [("fake:work", "a"), ("fake:work", "b")]


async def test_an_account_that_fails_is_listed_while_the_others_items_come_back(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work", "a")
    await connected(service, "home")  # (whose credentials are missing here)

    result = await listed(service, account="*")

    assert items_of(result) == [("fake:work", "a")]
    [failed] = result.failures
    assert (failed.account, failed.error.code) == ("fake:home", "reauth_required")


async def test_one_account_that_fails_is_an_error(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await connected(service, "home")

    with pytest.raises(ReauthRequired):
        await listed(service, account="home")


async def test_a_fan_out_says_each_account_s_next_page(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work", "a", "b", "c")
    await account_with_things(service, fakes, clock, "home", "d")

    result = await listed(service, account="*", limit=2)

    assert result.next_pages == {"fake:work": "2"}
    rest = await listed(service, account="work", page="2", limit=2)
    assert items_of(rest) == [("fake:work", "c")]


class Slow(FakeIntegration):
    """Lists slowly, counting how many accounts it lists at once."""

    name = "slow"
    running: ClassVar[int] = 0
    most: ClassVar[int] = 0

    @integration_operation(
        "list_things", title="List", hints=OperationHints(read_only=True), fan_out=True
    )
    async def list_things(
        self, account: AccountHandle[FakeAccountOptions], request: ListThingsRequest
    ) -> ItemsPage[Thing]:
        """List slowly."""
        Slow.running += 1
        Slow.most = max(Slow.most, Slow.running)
        await asyncio.sleep(0.01)
        Slow.running -= 1
        return ItemsPage(items=[Thing(id="t", text=account.ref)])


async def test_a_fan_out_runs_on_at_most_4_accounts_at_once(
    tmp_path: Path, clock: FakeClock
) -> None:
    harness = Harness(tmp_path, clock=clock)
    harness.registry.register(INTEGRATIONS, Slow)
    async with harness.service() as service:
        for n in range(FAN_OUT + 3):
            await connected(service, f"a{n}", integration="slow", method=ConnectMethod.NONE)

        found = await service.call("slow_list_things", {"account": "*"})

    assert len(found.model_dump()["items"]) == FAN_OUT + 3
    assert Slow.most == FAN_OUT


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------


async def test_an_operation_that_writes_takes_one_account(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work", access=["read", "write"])

    done = await service.call("fake_do_thing", {"account": "work", "text": "hi"})

    assert DoneThing.model_validate(done.model_dump()).account == "fake:work"
    with pytest.raises(ConfigError, match="works on one account at a time"):
        await service.call("fake_do_thing", {"account": "*", "text": "hi"})
    with pytest.raises(ValidationError):
        await service.call("fake_do_thing", {"account": ["work"], "text": "hi"})
    with pytest.raises(ValidationError):
        DoThingRequest.model_validate({"account": "work", "text": "hi"})  # (the method's own)


async def test_an_account_not_connected_for_what_an_operation_needs_is_refused(
    service: UDLService, fakes: Fakes, clock: FakeClock
) -> None:
    await account_with_things(service, fakes, clock, "work")  # (read only)

    with pytest.raises(NotAllowed, match="fake:work was not connected for write") as raised:
        await service.call("fake_do_thing", {"text": "hi"})

    assert raised.value.hint is not None
    assert "`udl accounts add fake work --access read,write --reconnect`" in raised.value.hint


class Refusing(FakeIntegration):
    name = "refusing"

    @integration_operation("do_thing", title="Do", access="write")
    async def do_thing(
        self, account: AccountHandle[FakeAccountOptions], request: DoThingRequest
    ) -> DoneThing:
        """Find the credentials refused."""
        raise ReauthRequired("The service refused the token")


async def test_credentials_an_operation_finds_refused_mark_the_account(
    tmp_path: Path, clock: FakeClock
) -> None:
    harness = Harness(tmp_path, clock=clock)
    harness.registry.register(INTEGRATIONS, Refusing)
    async with harness.service() as service:
        await connected(
            service, integration="refusing", method=ConnectMethod.NONE, access=["write"]
        )

        with pytest.raises(ReauthRequired):
            await service.call("refusing_do_thing", {"text": "hi"})

        [account] = await service.accounts.list()
        assert account.status is AccountStatus.NEEDS_REAUTH
