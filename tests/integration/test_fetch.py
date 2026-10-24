"""
-----------------
- test_fetch.py -
-----------------

The page fetcher end to end: an MCP client whose own fetch failed reads six pages on three of the
anti-bot site's hosts (one that needs JavaScript, one behind a check FakePerson passes in udl's
window, one behind a login wall once its cookies were imported), all as Markdown; asking again
needs nobody and starts in the browser; and a subscription on udl.fetch.challenge runs its
action once a streak, in the automations' runtime
"""

# System Imports
import asyncio
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from typing import Any

# Package Imports
import pytest
from mcp import Client

# Local Imports
from support.actions import FakeAction, fake_action
from support.antibot import HOSTS, SESSION, AntiBotSite, FakePerson
from support.browser import needs_chromium
from support.interfaces import Harness
from support.mcp import client_for
from support.tasks import held
from udl.automations.models import SetActionRequest, SetSubscriptionRequest
from udl.automations.runtime import AutomationRuntime
from udl.config import AutomationsSettings, FetchSettings, PersonMode
from udl.core.service import UDLService
from udl.fetch.models import BatchStatus, FetchRequest
from udl.plugins.registry import ACTIONS

FETCH = FetchSettings(
    site_delay=timedelta(0),
    timeout=timedelta(seconds=15),
    challenge_grace=timedelta(seconds=1),
    challenge_timeout=timedelta(seconds=30),
    browser_idle=timedelta(seconds=1),
)


@pytest.fixture
def person() -> FakePerson:
    return FakePerson()


@pytest.fixture
def harness(tmp_path: Path, person: FakePerson) -> Harness:
    needs_chromium()
    made = Harness(
        tmp_path,
        fetch=FETCH,
        automations=AutomationsSettings(
            scripts_dir=tmp_path / "scripts",
            dispatch_interval=timedelta(seconds=0.1),
            lease_ttl=timedelta(seconds=30),
        ),
    )
    made.person = person
    return made


@pytest.fixture
async def client(harness: Harness) -> AsyncIterator[Client]:
    async with held(client_for(harness)) as client:
        yield client


async def called(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


async def test_an_agent_reads_six_pages_on_three_hosts_as_markdown(
    client: Client, antibot: AntiBotSite, person: FakePerson
) -> None:
    a, b, c = HOSTS
    session = antibot.issue_session()
    await called(client, "import_cookies", cookies=f"{SESSION}={session}", site=c)
    urls = [
        antibot.url("/plain-500/1", a),
        antibot.url("/plain-500/2", a),
        antibot.url("/checked/1", b),
        antibot.url("/checked/2", b),
        antibot.url("/members/1", c),
        antibot.url("/members/2", c),
    ]

    shown = await called(client, "fetch_pages", urls=urls, person="ask", timeout=120)

    assert shown["status"] == "finished", shown
    assert [item["status"] for item in shown["items"]] == ["succeeded"] * 6
    assert [item["via"] for item in shown["items"]] == [
        "browser",
        "browser",
        "person",
        "browser",
        "cookies",
        "cookies",
    ]
    for item, title in zip(
        shown["items"],
        ["Plain 1", "Plain 2", "Checked 1", "Checked 2", "Members 1", "Members 2"],
        strict=True,
    ):
        assert item["content"].startswith(f"# {title}"), item
    assert person.attended == [antibot.url("/checked/1", b)]

    # Asking again on the checked host needs nobody, and starts in the browser.
    again = await called(
        client, "fetch_pages", urls=[antibot.url("/checked/3", b)], person="fail", timeout=60
    )
    [item] = again["items"]
    page = await called(client, "get_fetched_page", id=item["id"])

    assert (item["status"], item["via"]) == ("succeeded", "browser")
    assert [attempt["rung"] for attempt in page["attempts"]] == ["browser"]
    assert len(person.attended) == 1


async def until(done: Any, *, within: float = 60) -> None:
    deadline = time.monotonic() + within
    while not done():
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.1)


async def test_a_subscription_on_a_waiting_site_runs_once_a_streak(
    harness: Harness, antibot: AntiBotSite
) -> None:
    told: type[FakeAction] = fake_action("told")
    harness.registry.register(ACTIONS, told)
    b = HOSTS[1]

    async def fetched(service: UDLService, *paths: str) -> None:
        request = FetchRequest(
            urls=[antibot.url(path, b) for path in paths], person=PersonMode.WAIT, timeout=60
        )
        shown = await service.fetch_pages(request)
        assert shown.status is BatchStatus.WAITING, shown

    async with harness.service() as service:
        await service.set_action(SetActionRequest(name="note", plugin="told"))
        await service.set_subscription(
            SetSubscriptionRequest(name="checks", events=["udl.fetch.challenge"], actions=["note"])
        )
        async with AutomationRuntime(service, holder="here:1:a"):
            await fetched(service, "/checked/1", "/checked/2")
            await fetched(service, "/checked/3")  # (the same streak)
            await until(lambda: len(told.ran) == 1)
            await asyncio.sleep(0.5)
            assert len(told.ran) == 1
            assert told.ran[0].event is not None
            assert told.ran[0].event.data["site"] == b

            report = await service.fetches.solve()
            assert report.passed == [b]
            antibot.reset()  # (the site forgets its clearances: it challenges again)
            await fetched(service, "/checked/4")

            await until(lambda: len(told.ran) == 2)
