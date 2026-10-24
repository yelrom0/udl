"""
--------------
- webhook.py -
--------------

The webhook action: sends an HTTP request with udl's HTTP client (udl.http): its method, URL,
headers, secret headers (kept in the secret store, never in a database: an Authorization, an
API key) and a body (JSON, or text with its content type). Its output is the answer's status,
headers and first 64 KiB of body, read as JSON when it is JSON. An answer of 400 or more fails
the run, retryably for 429 and 5xx (after the server's Retry-After), as a connection that fails
does.

As with downloads, anyone who may use the API may have udl send a request anywhere it reaches
(the security model accepts it, and the docs say so).
"""

# System Imports
import importlib.metadata
import json
from datetime import timedelta
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Annotated, Final
from urllib.parse import urlsplit

# Package Imports
import httpx
from pydantic import AfterValidator, Field, JsonValue, SecretStr

# Local Imports
from udl.config import Duration
from udl.errors import ActionFailed, Cancelled, UDLError, is_retryable
from udl.http import http_client
from udl.plugins.actions.base import ActionContext, ActionOptions, ActionOutput, ActionPlugin
from udl.plugins.secrets.base import KeptSecret, SecretField
from udl.process import cancellable

MAX_BODY: Final = 64 * 1024
"""Bytes of an answer's body that are kept (its first)."""
_TOOL: Final = "webhook"
_CANCELLED: Final = "The run was cancelled"
_SHOWN: Final = 200  # characters of an error answer's body its run's error says


class Method(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


def _http_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"{url!r} is not an http(s) URL")
    return url


def _some_time(timeout: timedelta) -> timedelta:
    if timeout <= timedelta(0):
        raise ValueError("a webhook's timeout is some time, e.g. 30s")
    return timeout


class WebhookOptions(ActionOptions):
    url: Annotated[str, AfterValidator(_http_url)]
    """Where to send it (http or https)."""
    method: Method = Method.POST
    """GET, POST, PUT, PATCH or DELETE."""
    headers: dict[str, str] = Field(default_factory=dict[str, str])
    """Headers to send."""
    secret_headers: dict[str, SecretField] = Field(default_factory=dict[str, SecretField])
    """Headers whose values are secrets (an Authorization, an API key): kept in the secret
    store, never in a database or a log."""
    body: JsonValue = None
    """What to send: JSON (sent as application/json), or a text (with content_type); none for
    no body."""
    content_type: str | None = None
    """The body's type, for a text body (by default text/plain)."""
    timeout: Annotated[Duration, AfterValidator(_some_time)] = timedelta(seconds=30)
    """How long to wait to connect, or for more of the answer."""


class WebhookAction(ActionPlugin[WebhookOptions]):
    """Sends an HTTP request."""

    name = "webhook"
    options_model = WebhookOptions
    summary = (
        "Sends an HTTP request (secret headers kept in the secret store); the answer's status, "
        "headers and body are the run's output."
    )

    async def run(self, ctx: ActionContext[WebhookOptions]) -> ActionOutput:
        options = ctx.options
        headers = dict(options.headers)
        for name, value in options.secret_headers.items():
            headers[name] = await _secret(ctx, name, value)
        content: bytes | None = None
        if options.body is not None:
            text = isinstance(options.body, str)
            content = (
                options.body.encode()
                if isinstance(options.body, str)
                else json.dumps(options.body).encode()
            )
            if not any(name.lower() == "content-type" for name in headers):
                kind = "application/json" if not text else "text/plain; charset=utf-8"
                headers["Content-Type"] = options.content_type or kind
        try:
            async with http_client(
                headers=headers,
                seconds=options.timeout.total_seconds(),
                retries=0,
                tool=_TOOL,
                user_agent=f"udl/{importlib.metadata.version('udl')}",
            ) as client:
                status, answered, body, cut = await cancellable(
                    _send(client, options.method, options.url, content),
                    ctx.cancel,
                    message=_CANCELLED,
                )
        except Cancelled:
            raise
        except UDLError as e:
            raise ActionFailed(f"{options.url}: {e.message}", retryable=is_retryable(e)) from e
        text = body.decode(errors="replace")
        if status >= 400:
            retryable = status == 429 or status >= 500
            raise ActionFailed(
                f"{options.url} answered {status}" + (f": {text[:_SHOWN]}" if text else ""),
                retryable=retryable,
                retry_after=_retry_after(answered.get("retry-after"), ctx) if retryable else None,
            )
        return ActionOutput(
            output={
                "status": status,
                "headers": dict(answered),
                "body": _body(text, complete=not cut),
                "truncated": cut,
            },
            summary=f"{options.method} {options.url}: {status}",
        )


async def _secret(ctx: ActionContext[WebhookOptions], name: str, value: SecretField) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    kept: KeptSecret = value
    try:
        found = await ctx.secrets.get(kept.secret)
    except UDLError as e:
        raise ActionFailed(f"The secret header {name} cannot be read: {e.message}") from e
    if found is None:
        raise ActionFailed(
            f"The secret store has no {kept.secret} (the {name} header)",
            hint="Set the action's secret_headers again.",
        )
    return found.get_secret_value()


async def _send(
    client: httpx.AsyncClient, method: str, url: str, content: bytes | None
) -> tuple[int, httpx.Headers, bytes, bool]:
    """The answer to a request: its status, headers, its body's first MAX_BODY bytes, and
    whether it had more."""
    async with client.stream(method, url, content=content) as response:
        kept = bytearray()
        async for chunk in response.aiter_bytes():
            kept += chunk
            if len(kept) > MAX_BODY:
                return response.status_code, response.headers, bytes(kept[:MAX_BODY]), True
        return response.status_code, response.headers, bytes(kept), False


def _body(text: str, *, complete: bool) -> JsonValue:
    """An answer's body: its JSON, when it is (whole) JSON, else its text."""
    if not complete or not text:
        return text
    try:
        found: JsonValue = json.loads(text)
    except ValueError:
        return text
    return found


def _retry_after(value: str | None, ctx: ActionContext[WebhookOptions]) -> float | None:
    """A Retry-After header (seconds, or a date), as seconds from now."""
    if value is None:
        return None
    if value.strip().isdigit():
        return float(value.strip())
    try:
        when = parsedate_to_datetime(value)
    except TypeError, ValueError:
        return None
    return max((when - ctx.clock.now()).total_seconds(), 0.0)
