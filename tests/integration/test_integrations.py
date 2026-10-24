"""
------------------------
- test_integrations.py -
------------------------

Phase 11 end to end, on a FakeClock: with FakeIntegration as a local plugin (a file in a plugin
dir), two accounts are connected; a subscription on one of them gets runs from that account's
watcher only (the other has none: nothing wants its events); and fake_list_things with account
"*" gives both accounts' items, each tagged with its account
"""

# System Imports
import asyncio
import textwrap
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.actions import FakeAction, fake_action
from support.clock import FakeClock
from support.interfaces import Harness
from udl.automations.events import EventRecord
from udl.automations.models import SetActionRequest, SetSubscriptionRequest
from udl.automations.runs import ActionRun, RunStatus
from udl.automations.runtime import AutomationRuntime
from udl.config import AutomationsSettings
from udl.core.service import UDLService
from udl.db.records import RecordQuery
from udl.integrations.models import ConnectAccountRequest
from udl.plugins.registry import ACTIONS, INTEGRATIONS


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake() -> type[FakeAction]:
    return fake_action("fake")


@pytest.fixture
def feeds(tmp_path: Path) -> Path:
    folder = tmp_path / "feeds"
    folder.mkdir()
    return folder


@pytest.fixture
def harness(tmp_path: Path, clock: FakeClock, fake: type[FakeAction], feeds: Path) -> Harness:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "fake_service.py").write_text(
        textwrap.dedent(
            """
            from support.integrations import FakeIntegration

            class LocalFake(FakeIntegration):
                name = "fake"
            """
        )
    )
    found = Harness(
        tmp_path,
        clock=clock,
        automations=AutomationsSettings(
            scripts_dir=tmp_path / "scripts", lease_ttl=timedelta(hours=1)
        ),
        integrations={"fake": {"feed_dir": str(feeds), "poll_every": "10s"}},
    )
    found.registry.load_dir(plugins)
    found.registry.register(ACTIONS, fake)
    return found


async def runs(service: UDLService) -> list[ActionRun]:
    return await service.db.list_records(ActionRun, RecordQuery())


async def test_a_subscription_on_one_account_gets_runs_from_its_watcher_only(
    harness: Harness, clock: FakeClock, fake: type[FakeAction], feeds: Path
) -> None:
    assert harness.registry.plugins(INTEGRATIONS)[0].origin.endswith("fake_service.py")
    async with harness.service() as service:
        for name in ("work", "home"):
            connection = await service.connect_account(
                ConnectAccountRequest.model_validate(
                    {"integration": "fake", "name": name, "tokens": {"api_key": f"k-{name}"}}
                )
            )
            assert connection.account.status == "connected"
        await service.set_action(SetActionRequest(name="hello", plugin="fake"))
        await service.set_subscription(
            SetSubscriptionRequest(
                name="work-things",
                events=["fake.thing.happened"],
                accounts=["fake:work"],
                actions=["hello"],
            )
        )
        (feeds / "work").write_text("report\nmemo\n")
        (feeds / "home").write_text("recipe\n")

        async with AutomationRuntime(service):
            for _ in range(30):
                await clock.advance(timedelta(seconds=1))
                await asyncio.sleep(0)
                if sum(run.status is RunStatus.SUCCEEDED for run in await runs(service)) >= 2:
                    break

            listed = await service.call("fake_list_things", {"account": "*"})

        events = await service.db.list_records(
            EventRecord, RecordQuery(where={"type": "fake.thing.happened"})
        )
        work = await service.accounts.get("fake:work")
        assert {event.account_id for event in events} == {work.id}
        assert sorted(str(event.data["text"]) for event in events) == ["memo", "report"]
        assert [run.status for run in await runs(service)] == [RunStatus.SUCCEEDED] * 2
        assert len(fake.ran) == 2

    items = listed.model_dump()["items"]
    assert sorted((item["account"], item["item"]["text"]) for item in items) == [
        ("fake:home", "recipe"),
        ("fake:work", "memo"),
        ("fake:work", "report"),
    ]
