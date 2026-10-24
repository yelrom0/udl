"""
-----------------
- test_fetch.py -
-----------------

/fetches and /browser/cookies: reading pages, the fetches, their pages, the checks that wait and
the cookies of udl's browser profile, against the anti-bot fixture site
"""

# System Imports
from datetime import timedelta
from pathlib import Path
from typing import Any

# Package Imports
import httpx
import pytest

# Local Imports
from support.antibot import SESSION, AntiBotSite
from support.browser import needs_chromium
from support.interfaces import Harness
from udl.config import APISettings, FetchSettings


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(
        tmp_path,
        api=APISettings(allowed_output_roots=[tmp_path]),
        fetch=FetchSettings(site_delay=timedelta(0), timeout=timedelta(seconds=10), max_answer=30),
    )


async def fetched(client: httpx.AsyncClient, *urls: str, **fields: Any) -> dict[str, Any]:
    response = await client.post("/fetches", json={"urls": list(urls), **fields})
    assert response.status_code == 200, response.text
    return response.json()


async def test_pages_are_read(client: httpx.AsyncClient, antibot: AntiBotSite) -> None:
    shown = await fetched(client, antibot.url("/docs"), max_chars=20, label="docs")

    assert (shown["status"], shown["label"], shown["format"]) == ("finished", "docs", "markdown")
    [item] = shown["items"]
    assert (item["status"], item["via"], item["title"]) == (
        "succeeded",
        "static",
        "Widgets: a guide",
    )
    assert item["content"] == "# Widgets: a guide\n\n"
    assert item["next_offset"] == 20
    assert shown["hint"] is None


async def test_an_answer_holds_at_most_max_answer_characters_of_pages(
    client: httpx.AsyncClient, antibot: AntiBotSite
) -> None:
    shown = await fetched(client, antibot.url("/docs"), antibot.url("/article"), max_chars=25)

    first, second = shown["items"]
    assert len(first["content"]) == 25
    assert (second["content"], second["left_out"]) == (None, True)


async def test_a_fetch_need_not_be_waited_for(
    client: httpx.AsyncClient, antibot: AntiBotSite
) -> None:
    shown = await fetched(client, antibot.url("/docs"), wait=False)

    assert shown["status"] in ("queued", "running", "finished")


async def test_a_fetch_that_sends_a_secret_is_refused(
    client: httpx.AsyncClient, antibot: AntiBotSite
) -> None:
    refused = await client.post(
        "/fetches", json={"urls": [antibot.url("/docs")], "headers": {"Authorization": "x"}}
    )
    no_person = await client.post(
        "/fetches", json={"urls": [antibot.url("/docs")], "via": "person"}
    )

    assert refused.status_code == 422
    assert "import_cookies" in refused.text
    assert no_person.status_code == 422


async def test_the_fetches_are_listed_and_shown(
    client: httpx.AsyncClient, antibot: AntiBotSite
) -> None:
    shown = await fetched(client, antibot.url("/docs"))

    listed = await client.get("/fetches", params={"status": "finished"})
    one = await client.get(f"/fetches/{shown['id']}", params={"max_chars": 0})
    unknown = await client.get("/fetches/00000000-0000-4000-8000-000000000009")

    assert [found["id"] for found in listed.json()["fetches"]] == [shown["id"]]
    assert one.status_code == 200
    assert one.json()["items"][0]["content"] is None
    assert (unknown.status_code, unknown.json()["code"]) == (404, "does_not_exist")


async def test_a_fetched_page_is_given_from_an_offset_in_any_format(
    client: httpx.AsyncClient, antibot: AntiBotSite
) -> None:
    shown = await fetched(client, antibot.url("/docs"))
    item = shown["items"][0]["id"]

    page = await client.get(
        f"/fetches/pages/{item}", params={"format": "text", "offset": 0, "max_chars": 16}
    )
    selected = await client.get(f"/fetches/pages/{item}", params={"select": "table th"})

    assert page.status_code == 200, page.text
    found = page.json()
    assert (found["content"], found["format"], found["next_offset"]) == (
        "Widgets: a guide",
        "text",
        16,
    )
    assert found["attempts"][0]["rung"] == "static"
    assert selected.json()["content"] == "Size\n\nTeeth\n\nPrice"


async def test_a_page_that_was_never_fetched_does_not_exist(client: httpx.AsyncClient) -> None:
    response = await client.get("/fetches/pages/00000000-0000-4000-8000-000000000009")

    assert response.status_code == 404


async def test_a_fetch_is_cancelled_and_retried(
    client: httpx.AsyncClient, antibot: AntiBotSite
) -> None:
    shown = await fetched(client, antibot.url("/gone"))

    cancelled = await client.delete(f"/fetches/{shown['id']}")
    retried = await client.post(f"/fetches/{shown['id']}/retry", json={"via": "static"})

    assert cancelled.json()["status"] == "finished"
    assert retried.status_code == 200, retried.text
    assert retried.json()["items"][0]["status"] in ("queued", "running", "failed")


async def test_the_checks_that_wait_are_listed_before_a_fetch_is_looked_up(
    client: httpx.AsyncClient,
) -> None:
    listed = await client.get("/fetches/challenges")
    solved = await client.post("/fetches/challenges/solve", json={})

    assert listed.json() == {"challenges": []}
    assert solved.json() == {"passed": [], "waiting": []}


async def test_cookies_are_imported_listed_and_deleted(
    client: httpx.AsyncClient, antibot: AntiBotSite
) -> None:
    needs_chromium()
    secret = antibot.issue_session()

    imported = await client.post(
        "/browser/cookies",
        json={"cookies": f"{SESSION}={secret}", "site": "a.localhost", "user_agent": "UA/1"},
    )
    listed = await client.get("/browser/cookies", params={"site": "a.localhost"})
    read = await fetched(client, antibot.url("/members/x"))
    deleted = await client.delete("/browser/cookies/a.localhost")

    assert imported.json() == {"sites": {"a.localhost": 1}, "user_agent": True}
    assert listed.json()["sites"][0]["cookies"][0]["name"] == SESSION
    assert secret not in listed.text
    assert read["items"][0]["via"] == "cookies"
    assert deleted.json() == {"deleted": True}


async def test_cookies_that_are_not_cookies_are_a_bad_request(client: httpx.AsyncClient) -> None:
    response = await client.post("/browser/cookies", json={"cookies": "words"})

    assert (response.status_code, response.json()["code"]) == (400, "config_error")
