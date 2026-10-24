"""
--------------------------------
- test_integration_contract.py -
--------------------------------

The behaviour every integration must have, run over each of them (each a Case: how a test
connects its accounts and makes something happen in one): its event types are its own and their
examples are valid; each operation says what it does to the world, is described, and documents
its fields; connecting each way gives an identity; and its watcher publishes only its own types,
stops at once when cancelled, saves its state after each batch and goes on from it without
publishing anything again.
"""

# System Imports
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qsl, urlsplit

# Package Imports
import pytest
from pydantic import BaseModel, SecretStr

# Local Imports
from support.clock import FakeClock
from support.integrations import (
    FakeAccountOptions,
    FakeSettings,
    FakeTokens,
    MemoryState,
    RecordingSink,
    StubHandle,
    fake_integration,
    http_to,
)
from support.oauth import BASE, FakeAuthorizationServer
from udl.errors import ReauthRequired
from udl.integrations.oauth import OAuthClient
from udl.plugins.integrations.base import (
    Account,
    AccountStatus,
    ConnectMethod,
    Credentials,
    Integration,
)


@dataclass
class Case:
    """An integration, and how a test uses it."""

    integration: Integration[Any, Any]
    clock: FakeClock
    # The credentials and options an account connected each way has.
    connected: Callable[[ConnectMethod], Awaitable[tuple[Credentials, BaseModel]]]
    # Make `count` things happen in the account called `name`.
    happen: Callable[[str, int], Awaitable[None]]


def fake_case() -> Case:
    plugin = fake_integration()
    server = FakeAuthorizationServer()
    clock = FakeClock()
    http = http_to((BASE, server.app()))
    integration = plugin(FakeSettings(), http=http, clock=clock)

    async def connected(method: ConnectMethod) -> tuple[Credentials, BaseModel]:
        if method is ConnectMethod.TOKENS:
            return FakeTokens(api_key=SecretStr("key-1234")), FakeAccountOptions()
        if method is ConnectMethod.NONE:
            return None, FakeAccountOptions(source="https://example.com/feed")
        provider = integration.oauth_request(frozenset({"read"})).provider
        async with http(tool="fake") as client:
            oauth = OAuthClient(provider, http=client, clock=clock)
            authorization = oauth.authorize(["fake.read"], "http://127.0.0.1:9/callback")
            back = server.approve(authorization.url)
            tokens = await oauth.exchange(authorization, dict(parse_qsl(urlsplit(back).query)))
        return tokens, FakeAccountOptions()

    async def happen(name: str, count: int) -> None:
        plugin.feed(name).add(*(f"thing {n}" for n in range(count)))

    return Case(integration, clock, connected, happen)


CASES: dict[str, Callable[[], Case]] = {"fake": fake_case}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]()


def integration_of(case: Case) -> type[Integration[Any, Any]]:
    return type(case.integration)


async def handle_of(case: Case, name: str = "work") -> StubHandle[Any]:
    plugin = integration_of(case)
    method = plugin.connect[0]
    credentials, options = await case.connected(method)
    account = Account(
        integration=plugin.name, name=name, method=method, status=AccountStatus.CONNECTED
    )
    return StubHandle(account, options, credentials)


async def until(check: Callable[[], bool], clock: FakeClock) -> None:
    """Move the clock on, a second at a time, until `check()` (or fail after 5 minutes)."""
    for _ in range(300):
        if check():
            return
        await clock.advance(timedelta(seconds=1))
    raise AssertionError("it never happened")


async def cancelled(task: asyncio.Task[None]) -> None:
    task.cancel()
    async with asyncio.timeout(1):  # (promptly)
        await asyncio.wait([task])
    assert task.cancelled() or task.exception() is None


# ---------------------------------------------------------------------------------------------
# What it is
# ---------------------------------------------------------------------------------------------


def test_it_says_what_it_is(case: Case) -> None:
    plugin = integration_of(case)

    assert plugin.name
    assert plugin.title
    assert plugin.connect
    assert (plugin.token_model is not None) == (ConnectMethod.TOKENS in plugin.connect)
    assert len(set(plugin.access_levels)) == len(plugin.access_levels)


def test_its_event_types_are_its_own_and_their_examples_are_valid(case: Case) -> None:
    plugin = integration_of(case)

    for event_type in plugin.event_types():
        assert event_type.name.startswith(f"{plugin.name}.")
        assert event_type.summary
        example = event_type.example.model_dump(mode="json")
        assert event_type.validate(example) == example
        assert event_type.subject_of(event_type.example)


def test_every_operation_says_what_it_does_and_documents_its_fields(case: Case) -> None:
    for spec in integration_of(case).operation_specs():
        assert spec.title
        assert spec.method.__doc__
        if spec.fan_out:
            assert spec.hints.read_only
        undocumented = [
            name for name, field in spec.request.model_fields.items() if not field.description
        ]
        assert undocumented == [], spec.name


async def test_connecting_each_way_gives_an_identity(case: Case) -> None:
    for method in integration_of(case).connect:
        credentials, options = await case.connected(method)

        identity = await case.integration.identify(credentials, options)

        assert identity.id, method
        assert identity.display, method


async def test_a_connected_account_is_healthy(case: Case) -> None:
    health = await case.integration.check(await handle_of(case))

    assert health.ok
    assert health.problems == []


# ---------------------------------------------------------------------------------------------
# Its watcher
# ---------------------------------------------------------------------------------------------


async def test_its_watcher_publishes_only_its_own_types(case: Case) -> None:
    if not integration_of(case).watches():
        pytest.skip("It has no watcher")
    handle = await handle_of(case)
    sink = RecordingSink(integration_of(case))
    state = MemoryState(integration_of(case).state_model)
    watching = asyncio.create_task(case.integration.watch(handle, sink, state))

    await case.happen("work", 3)
    await until(lambda: len(sink.emitted) >= 3, case.clock)
    await cancelled(watching)

    assert {emitted.event_type for emitted in sink.emitted} <= set(sink.types)
    assert len({emitted.dedupe_key for emitted in sink.emitted}) == len(sink.emitted)


async def test_its_watcher_stops_at_once_when_cancelled(case: Case) -> None:
    if not integration_of(case).watches():
        pytest.skip("It has no watcher")
    sink = RecordingSink(integration_of(case))
    watching = asyncio.create_task(
        case.integration.watch(
            await handle_of(case), sink, MemoryState(integration_of(case).state_model)
        )
    )
    await case.clock.advance(timedelta(seconds=1))

    await cancelled(watching)


async def test_its_watcher_goes_on_from_its_saved_state_without_publishing_anything_again(
    case: Case,
) -> None:
    plugin = integration_of(case)
    if not plugin.watches():
        pytest.skip("It has no watcher")
    handle = await handle_of(case)
    state = MemoryState(plugin.state_model)
    first = RecordingSink(plugin)
    watching = asyncio.create_task(case.integration.watch(handle, first, state))
    await case.happen("work", 2)
    await until(lambda: len(first.emitted) >= 2, case.clock)
    await cancelled(watching)
    assert state.saved  # (after the batch)

    await case.happen("work", 1)
    again = RecordingSink(plugin)
    watching = asyncio.create_task(case.integration.watch(handle, again, state))
    await until(lambda: len(again.emitted) >= 1, case.clock)
    await case.clock.advance(timedelta(minutes=5))
    await cancelled(watching)

    assert len(again.emitted) == 1
    assert again.emitted[0].dedupe_key not in {emitted.dedupe_key for emitted in first.emitted}


async def test_its_watcher_raises_reauth_required_when_the_credentials_are_refused(
    case: Case,
) -> None:
    if not integration_of(case).watches():
        pytest.skip("It has no watcher")
    handle = await handle_of(case)
    handle.refused = True

    with pytest.raises(ReauthRequired):
        async with asyncio.timeout(1):
            await case.integration.watch(
                handle,
                RecordingSink(integration_of(case)),
                MemoryState(integration_of(case).state_model),
            )
