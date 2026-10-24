"""
-------------------
- test_webhook.py -
-------------------

The webhook action: sends an HTTP request (its headers, secret headers from the secret store,
and a JSON or text body) and gives the answer's status, headers and first 64 KiB of body (as
JSON when it is JSON). An answer of 400 or more fails the run: retryably for 429 and 5xx, after
the server's Retry-After.
"""

# System Imports
import asyncio
import email.utils
import importlib.metadata
import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

# Package Imports
import httpx
import pytest
import respx
from pydantic import SecretStr

# Local Imports
from support.interfaces import Harness
from udl.core.service import UDLService
from udl.db.models import JsonObject
from udl.errors import ActionFailed, Cancelled, ConfigError, SecretStoreError
from udl.plugins.actions.base import ActionContext, RunLog
from udl.plugins.actions.webhook import MAX_BODY, WebhookAction
from udl.plugins.secrets.base import SecretRef
from udl.process import CancelToken

HOOK = "https://hooks.example.com/udl"


@pytest.fixture
async def service(tmp_path: Path) -> AsyncIterator[UDLService]:
    async with Harness(tmp_path).service() as found:
        yield found


@pytest.fixture
def plugin(service: UDLService) -> WebhookAction:
    return WebhookAction(service)


def context(plugin: WebhookAction, options: JsonObject) -> ActionContext[Any]:
    service = plugin.service
    return ActionContext(
        run_id=uuid4(),
        event=None,
        options=plugin.options(options),
        service=service,
        secrets=service.secrets,
        clock=service.clock,
        cancel=CancelToken(),
        log=RunLog(),
        action="hook",
    )


async def run(plugin: WebhookAction, **options: Any) -> Any:
    return await plugin.run(context(plugin, {"url": HOOK, **options}))


@respx.mock
async def test_it_posts_its_body_as_json(plugin: WebhookAction) -> None:
    route = respx.post(HOOK).mock(
        return_value=httpx.Response(201, json={"ok": True}, headers={"X-Id": "7"})
    )

    done = await run(plugin, body={"text": "Hello", "n": [1, 2]}, headers={"X-Mine": "yes"})

    request = route.calls.last.request
    assert json.loads(request.content) == {"text": "Hello", "n": [1, 2]}
    assert request.headers["content-type"] == "application/json"
    assert request.headers["x-mine"] == "yes"
    assert request.headers["user-agent"] == f"udl/{importlib.metadata.version('udl')}"
    assert done.output["status"] == 201
    assert done.output["body"] == {"ok": True}
    assert done.output["headers"]["x-id"] == "7"
    assert done.summary == f"POST {HOOK}: 201"


@respx.mock
async def test_a_text_body_is_sent_as_text(plugin: WebhookAction) -> None:
    route = respx.put(HOOK).mock(return_value=httpx.Response(200, text="fine"))

    done = await run(
        plugin, method="PUT", body="a=1", content_type="application/x-www-form-urlencoded"
    )

    request = route.calls.last.request
    assert request.content == b"a=1"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert done.output["body"] == "fine"


@respx.mock
async def test_without_a_body_none_is_sent(plugin: WebhookAction) -> None:
    route = respx.get(HOOK).mock(return_value=httpx.Response(204))

    done = await run(plugin, method="GET")

    assert route.calls.last.request.content == b""
    assert "content-type" not in route.calls.last.request.headers
    assert (done.output["status"], done.output["body"]) == (204, "")


@respx.mock
async def test_secret_headers_come_from_the_secret_store(
    plugin: WebhookAction, service: UDLService
) -> None:
    ref = SecretRef("actions/hook/secret_headers/Authorization")
    await service.secrets.set(ref, SecretStr("Bearer s3cret"))
    route = respx.post(HOOK).mock(return_value=httpx.Response(200))

    ctx = context(plugin, {"url": HOOK, "secret_headers": {"Authorization": {"secret": str(ref)}}})
    done = await plugin.run(ctx)

    assert route.calls.last.request.headers["authorization"] == "Bearer s3cret"
    assert "s3cret" not in json.dumps(done.output) + ctx.log.text


@respx.mock
async def test_a_secret_header_may_be_given_as_it_is(plugin: WebhookAction) -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200))

    await run(plugin, secret_headers={"X-Key": "k3y"})

    assert route.calls.last.request.headers["x-key"] == "k3y"


async def test_a_secret_the_store_has_lost_fails_the_run(plugin: WebhookAction) -> None:
    with pytest.raises(ActionFailed, match="secret store has no"):
        await run(plugin, secret_headers={"X-Key": {"secret": "actions/gone/x"}})


@respx.mock
async def test_it_keeps_64_kib_of_the_body(plugin: WebhookAction) -> None:
    respx.post(HOOK).mock(return_value=httpx.Response(200, text="a" * (MAX_BODY + 1000)))

    done = await run(plugin)

    assert MAX_BODY == 64 * 1024
    assert done.output["body"] == "a" * MAX_BODY
    assert done.output["truncated"] is True


@respx.mock
async def test_a_body_cut_short_is_text_even_if_it_was_json(plugin: WebhookAction) -> None:
    big = json.dumps({"items": ["x" * 100] * 1000})
    respx.post(HOOK).mock(
        return_value=httpx.Response(200, text=big, headers={"Content-Type": "application/json"})
    )

    done = await run(plugin)

    assert isinstance(done.output["body"], str)


@respx.mock
@pytest.mark.parametrize(
    ("status", "retryable", "retry_after"), [(404, False, None), (503, True, 30), (429, True, 30)]
)
async def test_an_error_status_fails_the_run(
    plugin: WebhookAction, status: int, retryable: bool, retry_after: float | None
) -> None:
    respx.post(HOOK).mock(
        return_value=httpx.Response(status, text="nope", headers={"Retry-After": "30"})
    )

    with pytest.raises(ActionFailed, match=str(status)) as raised:
        await run(plugin)

    assert raised.value.may_retry is retryable
    assert raised.value.retry_after == retry_after


@respx.mock
async def test_a_connection_that_fails_is_retryable(plugin: WebhookAction) -> None:
    respx.post(HOOK).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(ActionFailed) as raised:
        await run(plugin)

    assert raised.value.may_retry is True


@respx.mock
async def test_cancelling_it_stops_the_request(plugin: WebhookAction) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    respx.post(HOOK).mock(side_effect=slow)
    ctx = context(plugin, {"url": HOOK})
    running = asyncio.create_task(plugin.run(ctx))
    await asyncio.sleep(0.05)

    ctx.cancel.cancel()

    with pytest.raises(Cancelled):
        await asyncio.wait_for(running, timeout=1)


@pytest.mark.parametrize(
    "options",
    [
        {"url": "ftp://example.com/x"},
        {"url": "not a url"},
        {"url": HOOK, "method": "BREW"},
        {"url": HOOK, "timeout": "0s"},
    ],
)
def test_its_options_are_checked(plugin: WebhookAction, options: JsonObject) -> None:
    with pytest.raises(ConfigError):
        plugin.options(options)


@respx.mock
async def test_its_own_content_type_and_timeout_are_used(plugin: WebhookAction) -> None:
    route = respx.post(HOOK).mock(return_value=httpx.Response(200))

    await run(
        plugin, body={"a": 1}, headers={"content-type": "application/vnd.x+json"}, timeout="5s"
    )

    assert route.calls.last.request.headers["content-type"] == "application/vnd.x+json"


async def test_a_secret_store_that_fails_fails_the_run(
    plugin: WebhookAction, service: UDLService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def locked(ref: SecretRef) -> SecretStr | None:
        raise SecretStoreError("The keyring is locked")

    monkeypatch.setattr(service.secrets, "get", locked)

    with pytest.raises(ActionFailed, match="keyring is locked"):
        await run(plugin, secret_headers={"X-Key": {"secret": "actions/hook/x"}})


@respx.mock
@pytest.mark.parametrize(
    ("given", "seconds"),
    [
        (
            lambda: email.utils.format_datetime(
                datetime.now(UTC) + timedelta(minutes=2), usegmt=True
            ),
            120,
        ),
        (lambda: "Wed, 21 Oct 2015 07:28:00 GMT", 0),
        (lambda: "soon", None),
    ],
)
async def test_retry_after_may_be_a_date(
    plugin: WebhookAction, given: Callable[[], str], seconds: float | None
) -> None:
    respx.post(HOOK).mock(return_value=httpx.Response(503, headers={"Retry-After": given()}))

    with pytest.raises(ActionFailed) as raised:
        await run(plugin)

    if seconds is None:
        assert raised.value.retry_after is None
    else:
        assert raised.value.retry_after == pytest.approx(seconds, abs=5)


@respx.mock
async def test_without_retry_after_a_retry_waits_its_backoff(plugin: WebhookAction) -> None:
    respx.post(HOOK).mock(return_value=httpx.Response(502))

    with pytest.raises(ActionFailed) as raised:
        await run(plugin)

    assert (raised.value.may_retry, raised.value.retry_after) == (True, None)
