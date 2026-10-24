"""
---------------------
- test_schedules.py -
---------------------

The schedules' routes: /schedules (made from their operations), with udl's errors as their
statuses; a schedule's subscription is refused to the subscriptions' routes
"""

# System Imports
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import httpx
import pytest
from fastapi import FastAPI

# Local Imports
from support.actions import fake_action
from support.api import BASE_URL
from support.interfaces import Harness
from support.tasks import held
from udl.config import AutomationsSettings
from udl.errors import ErrorCode
from udl.interfaces.api.app import create_app
from udl.plugins.registry import ACTIONS

FAKE = fake_action("fake")


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    found = Harness(
        tmp_path,
        # (Not running the automations: these tests look at what is saved and published.)
        automations=AutomationsSettings(
            enabled=False, scripts_dir=tmp_path / "scripts", timezone="UTC"
        ),
    )
    found.registry.register(ACTIONS, FAKE)
    return found


@pytest.fixture
async def app(harness: Harness) -> AsyncIterator[FastAPI]:
    made = create_app(harness.settings, make_service=harness.service)
    async with held(made.router.lifespan_context(made)):
        yield made


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=BASE_URL) as found:
        yield found


async def digest(client: httpx.AsyncClient) -> httpx.Response:
    """An action, and a schedule that runs it every weekday at 08:00."""
    put = await client.put("/actions/hello", json={"plugin": "fake"})
    assert put.status_code == 200, put.text
    return await client.put("/schedules/digest", json={"cron": "0 8 * * 1-5", "actions": ["hello"]})


async def test_a_schedule_is_saved_listed_and_shown(client: httpx.AsyncClient) -> None:
    saved = await digest(client)
    listed = await client.get("/schedules", params={"enabled": True})
    shown = await client.get("/schedules/digest")

    assert saved.status_code == 200, saved.text
    assert (saved.json()["spec"], saved.json()["actions"]) == ({"cron": "0 8 * * 1-5"}, ["hello"])
    assert len(saved.json()["next_times"]) == 5
    assert [found["name"] for found in listed.json()["schedules"]] == ["digest"]
    assert shown.json()["subscription"]["name"] == "schedule:digest"


async def test_a_schedule_is_run_by_hand(client: httpx.AsyncClient) -> None:
    await digest(client)

    ran = await client.post("/schedules/digest/run")

    assert ran.status_code == 201, ran.text
    assert (ran.json()["type"], ran.json()["source"], ran.json()["data"]["manual"]) == (
        "schedule.fired",
        "api",
        True,
    )


async def test_a_spec_is_previewed(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/schedules/preview",
        json={"spec": "every 1h", "count": 2, "after": "2030-01-01T00:00:00Z"},
    )

    assert response.status_code == 200, response.text
    assert [time["utc"] for time in response.json()["times"]] == [
        "2030-01-01T01:00:00Z",
        "2030-01-01T02:00:00Z",
    ]


async def test_a_schedule_is_deleted(client: httpx.AsyncClient) -> None:
    await digest(client)

    deleted = await client.delete("/schedules/digest")
    gone = await client.get("/schedules/digest")

    assert deleted.json() == {"deleted": True}
    assert gone.status_code == 404
    assert gone.json()["code"] == ErrorCode.DOES_NOT_EXIST


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"cron": "@daily", "every": "1h"}, 422),
        ({"every": "5s"}, 422),
        ({"at": "2001-01-01T09:00:00Z"}, 400),
        ({"cron": "@daily", "actions": ["nope"]}, 400),
    ],
)
async def test_what_is_no_schedule_is_refused(
    client: httpx.AsyncClient, body: dict[str, object], status: int
) -> None:
    response = await client.put("/schedules/x", json=body)

    assert response.status_code == status, response.text


async def test_a_schedule_s_subscription_is_the_schedule_s(client: httpx.AsyncClient) -> None:
    await digest(client)

    put = await client.put(
        "/subscriptions/schedule:digest",
        json={"events": ["schedule.fired"], "actions": ["hello"]},
    )
    deleted = await client.delete("/subscriptions/schedule:digest")

    assert (put.status_code, put.json()["code"]) == (409, ErrorCode.ALREADY_EXISTS)
    assert (deleted.status_code, deleted.json()["code"]) == (403, ErrorCode.NOT_ALLOWED)


async def test_the_status_says_the_next_schedules(client: httpx.AsyncClient) -> None:
    await digest(client)

    status = await client.get("/status")

    [upcoming] = status.json()["next_schedules"]
    assert upcoming["name"] == "digest"
