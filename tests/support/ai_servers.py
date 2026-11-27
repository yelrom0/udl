"""
-----------------
- ai_servers.py -
-----------------

The fake AI server the provider contracts run against (an ASGI app, reached in-process):
FakeOpenAI, which speaks OpenAI's API — /v1/models and /v1/chat/completions, with tools as
functions, a tool call's arguments as a JSON string, SSE streaming up to [DONE], and
response_format — and FakeOpenRouter, which speaks OpenRouter's: /models with a price per model,
/key for the key's label and credit, and the same chats, priced.

It holds what a test changes the way a service would: which models exist, what the next chat
answers, a status to fail with, whether every call fails as a refused connection. Nothing here
goes near the network: a provider is given `http()` (udl's HttpClientFactory) and reaches the
fake in-process.
"""

# System Imports
import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Final

# Package Imports
import httpx
from pydantic import JsonValue, TypeAdapter
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

# Local Imports
from udl.db.models import JsonObject
from udl.http import HttpClientFactory

BASE: Final = "http://fake.ai/v1"
"""Where the fake OpenAI API lives (what a provider is pointed at)."""
KEY: Final = "sk-fake"
"""The key the fake accepts (another is refused as a service refuses a bad one)."""

_OBJECT: Final = TypeAdapter[JsonObject](JsonObject)
"""How the fake reads the JSON a call sends (what udl sent, as data)."""
_MODEL: Final = {"id": "fake-1", "object": "model", "owned_by": "fake"}
"""A model the fake has."""


def _usage() -> JsonObject:
    """What a chat used."""
    return {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}


def _answer(
    content: str, *, finish: str = "stop", calls: list[JsonObject] | None = None
) -> JsonObject:
    """A chat answer: `content`, or the `calls` it makes instead."""
    message: JsonObject = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = list[JsonValue](calls)
    choice: JsonObject = {"index": 0, "message": message, "finish_reason": finish}
    answer: JsonObject = {
        "id": "chat-1",
        "object": "chat.completion",
        "model": "fake-1",
        "choices": [choice],
        "usage": _usage(),
    }
    return answer


def _text(content: str = "Hello from the fake") -> JsonObject:
    """A chat answer with `content`, and nothing asked of udl."""
    return _answer(content)


def _call(name: str, arguments: str, *, index: int = 0) -> JsonObject:
    """A tool call as OpenAI sends it: its arguments a JSON string."""
    function: JsonObject = {"name": name, "arguments": arguments}
    call: JsonObject = {
        "index": index,
        "id": f"call-{index + 1}",
        "type": "function",
        "function": function,
    }
    return call


def _with_calls(calls: list[JsonObject], *, content: str | None = None) -> JsonObject:
    """A chat answer that asks for `calls` instead of answering."""
    return _answer(content or "", finish="tool_calls", calls=calls)


@dataclass
class FakeOpenAI:
    """An OpenAI API with state: the models it has, what a chat answers, and what to fail with.

    Its fields say how it answers (a body to send, a status to fail with, whether to refuse the
    key or every connection); its methods change what a test needs. `requests` keeps the JSON
    body of every chat call, so a test reads what udl sent (its tools, its format)."""

    models: list[str] = field(default_factory=lambda: ["fake-1", "fake-2"])
    """The models it has."""
    answer: JsonObject | None = None
    """What the next chat answers (None: a line of text, or a tool call when it is offered
    one)."""
    status: int | None = None
    """The status to fail every chat with (None: answer)."""
    detail: str = "no"
    """What a failure's error.message says."""
    text: str | None = None
    """A failure's whole body, when it is not the usual JSON (a proxy's HTML, say)."""
    no_finish: bool = False
    """Whether a streamed answer ends without a `finish_reason` (some servers do)."""
    garbled: bool = False
    """Whether a streamed answer has a `data:` line that is not JSON."""
    headers: dict[str, str] = field(default_factory=dict[str, str])
    """Extra headers a failure sends (a 429's Retry-After)."""
    refuse_key: bool = False
    """Whether every call is 401 unless the key is the fake's."""
    refuse: bool = False
    """Whether every call fails as a connection that was refused."""
    requests: list[JsonObject] = field(default_factory=list[JsonObject])
    """The JSON body of every chat call made (what udl sent)."""
    keys: list[str] = field(default_factory=list[str])
    """The Authorization header of every call made."""
    headers_seen: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    """The headers of every call made, lower-cased (what udl sent of itself)."""
    streamed: list[JsonObject] = field(default_factory=list[JsonObject])
    """The chunks every streamed answer sent (as a test reads them)."""

    def http(self) -> HttpClientFactory:
        """udl's client factory, reaching this fake in-process (and nothing else)."""

        @asynccontextmanager
        async def factory(*, tool: str) -> AsyncGenerator[httpx.AsyncClient]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(self.app()), base_url=BASE
            ) as client:
                yield client

        return factory

    def app(self) -> Starlette:
        """The fake as an ASGI app (what http() mounts)."""
        return Starlette(
            routes=[
                Route("/v1/models", self._models, methods=["GET"]),
                Route("/v1/chat/completions", self._chat, methods=["POST"]),
            ]
        )

    # -- the endpoints -------------------------------------------------------------------------

    async def _models(self, request: Request) -> Response:
        if (refused := self._refused(request)) is not None:
            return refused
        if self.status is not None:
            return self._failed()
        models: list[JsonValue] = [{**_MODEL, "id": name} for name in self.models]
        listed: JsonObject = {"object": "list", "data": models}
        return JSONResponse(listed)

    async def _chat(self, request: Request) -> Response:
        if (refused := self._refused(request)) is not None:
            return refused
        body = _OBJECT.validate_python(await request.json())
        self.requests.append(body)
        if self.status is not None:
            return self._failed()
        if body.get("stream"):
            return StreamingResponse(self._stream(body), media_type="text/event-stream")
        return JSONResponse(self._priced(self.answer or self._answer(body)))

    def _priced(self, answer: JsonObject) -> JsonObject:
        """`answer` as this API sends it (a plain OpenAI answer says nothing of its price)."""
        return answer

    def _failed(self) -> Response:
        """The failure a test asked for (its status, its message and its headers)."""
        status = self.status or 500
        if self.text is not None:
            return Response(self.text, status_code=status, headers=self.headers)
        error: JsonObject = {"message": self.detail, "type": "fake_error"}
        failed: JsonObject = {"error": error}
        return JSONResponse(failed, status_code=status, headers=self.headers)

    async def _stream(self, body: JsonObject) -> AsyncGenerator[bytes]:
        """A streamed answer: the pieces of text as `data:` chunks, then the tool calls (each in
        two pieces, as a provider sends them), then [DONE]."""
        answer = self._priced(self.answer or self._answer(body))
        choices = _list(answer.get("choices"))
        [choice, *_] = choices or [{}]
        message = _object(choice.get("message"))
        content = message.get("content")
        for piece in _pieces(content) if isinstance(content, str) and content else []:
            delta: JsonObject = {"content": piece}
            yield self._chunk(delta, None)
        calls = _list(message.get("tool_calls"))
        for index, call in enumerate(calls):
            function = _object(call.get("function"))
            name = function.get("name")
            arguments = function.get("arguments")
            text = arguments if isinstance(arguments, str) else ""
            first_function: JsonObject = {
                "name": name,
                "arguments": text[: len(text) // 2],
            }
            first: JsonObject = {
                "index": index,
                "id": call.get("id"),
                "function": first_function,
            }
            # (A server that repeats the name in every piece, as several do.)
            second_function: JsonObject = {"name": name, "arguments": text[len(text) // 2 :]}
            second: JsonObject = {"index": index, "function": second_function}
            yield self._chunk({}, first)
            yield self._chunk({}, second)
        finish = None if self.no_finish else ("tool_calls" if calls else "stop")
        yield self._chunk({}, None, finish_reason=finish)
        # (What it used comes in a chunk of its own, after the finish, as OpenAI sends it.)
        asked = body.get("stream_options") == {"include_usage": True} or body.get("usage") == {
            "include": True
        }
        if asked:
            yield self._usage(answer.get("usage"))
        if self.garbled:
            yield b"data: {not json\n\n"
        yield b"data: [DONE]\n\n"

    def _chunk(
        self,
        delta: JsonObject,
        call: JsonObject | None,
        *,
        finish_reason: str | None = None,
    ) -> bytes:
        """One `data:` line of a streamed answer."""
        sent: JsonObject = {"tool_calls": [call]} if call is not None else delta
        choice: JsonObject = {"index": 0, "delta": sent, "finish_reason": finish_reason}
        chunk: JsonObject = {
            "id": "chat-1",
            "object": "chat.completion.chunk",
            "model": "fake-1",
            "choices": [choice],
            "usage": None,
        }
        self.streamed.append(chunk)
        return f"data: {json.dumps(chunk)}\n\n".encode()

    def _usage(self, usage: JsonValue | None) -> bytes:
        """The trailing `data:` line that says what the answer used (no choices, as OpenAI's)."""
        chunk: JsonObject = {
            "id": "chat-1",
            "object": "chat.completion.chunk",
            "model": "fake-1",
            "choices": [],
            "usage": usage,
        }
        self.streamed.append(chunk)
        return f"data: {json.dumps(chunk)}\n\n".encode()

    # -- what it answers -----------------------------------------------------------------------

    def _answer(self, body: JsonObject) -> JsonObject:
        """What a chat answers when a test has not said: JSON that fits the schema it was asked
        for, a tool call for the first tool it is offered (with arguments that fit the schema),
        or a line of text."""
        given = body.get("response_format")
        if isinstance(given, dict):
            schema = given.get("json_schema")
            if isinstance(schema, dict) and isinstance(schema.get("schema"), dict):
                return _text(json.dumps(_fit(schema["schema"])))
        tools = body.get("tools")
        if not isinstance(tools, list) or not tools:
            return _text()
        [tool, *_] = tools
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str):
            return _text()
        arguments = _fit(function.get("parameters") if isinstance(function, dict) else None)
        return _with_calls([_call(name, json.dumps(arguments))])

    def _refused(self, request: Request) -> Response | None:
        """The response a call that is refused gets, or None when it is let through."""
        key = request.headers.get("Authorization", "")
        self.keys.append(key)
        self.headers_seen.append({name.lower(): value for name, value in request.headers.items()})
        if self.refuse:
            raise httpx.ConnectError("the fake server is down", request=httpx.Request("GET", BASE))
        if self.refuse_key and key != f"Bearer {KEY}":
            return JSONResponse(
                {"error": {"message": "invalid api key", "type": "auth_error"}}, status_code=401
            )
        return None


def _fit(schema: JsonValue) -> JsonObject:
    """An object that fits a tool's `parameters` schema (each property, with a value of the type
    it asks for)."""
    given = _object(schema)
    properties = _object(given.get("properties"))
    if not properties:
        return {"x": 1}
    return {name: _value(spec) for name, spec in properties.items()}


def _value(spec: JsonValue) -> JsonValue:
    """A value of the JSON type a property's schema asks for."""
    kind = _object(spec).get("type")
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        return ["x"]
    if kind == "object":
        return {}
    return "x"


def _object(value: JsonValue) -> JsonObject:
    """`value` as a JSON object (an empty one when it is not)."""
    return value if isinstance(value, dict) else {}


def _list(value: JsonValue) -> list[JsonObject]:
    """`value` as a list of JSON objects (an empty one when it is not)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _pieces(text: str) -> list[str]:
    """`text` split into the pieces a streamed answer sends (words, with the space after)."""
    return re.findall(r"\S+\s*", text)


# ---------------------------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------------------------

OPENROUTER_BASE: Final = "http://fake.ai/api/v1"
"""Where the fake OpenRouter API lives (what an OpenRouter provider is pointed at)."""
OPENROUTER_KEY: Final = "sk-or-v1-fake"
"""The key the fake accepts."""


@dataclass
class FakeOpenRouter(FakeOpenAI):
    """OpenRouter's API with state: its models and what each costs, the key's label, usage and
    limit, and the same chats as OpenAI's — priced, and with what a chat used.

    A model's price is per token, as OpenRouter gives it (a string of USD); a chat's `cost` is
    what it cost, as OpenRouter says when `usage: {"include": true}` is asked for."""

    prices: dict[str, tuple[str, str]] = field(
        default_factory=lambda: {
            "fake-1": ("0.0000015", "0.000003"),  # (USD per token: prompt, completion)
            "fake-2": ("0", "0"),
        }
    )
    """Each model's price per token, as OpenRouter gives it."""
    contexts: dict[str, int] = field(default_factory=lambda: {"fake-1": 128_000, "fake-2": 8_192})
    """Each model's context length."""
    tool_models: tuple[str, ...] = ("fake-1",)
    """The models that take tools (the others do not say so in `supported_parameters`)."""
    label: str = "sk-or-v1-xxx…yyy"
    """The key's label, as OpenRouter sends it: a masked preview of the key itself, not a name
    (27.15)."""
    used: str | None = "1.25"
    """How much of the key's limit is used, in USD (None: it does not say)."""
    limit: str | None = "10"
    """The key's limit, in USD (None: no limit)."""
    limit_remaining: str | None = None
    """What is left of the limit now, in USD, when the key resets (None: it does not say)."""
    cost: str = "0.00042"
    """What a chat costs, as OpenRouter says."""
    used_daily: str | None = None
    """What the key has spent today, in USD (None: it does not say)."""
    free_model_daily: int | None = None
    """How many free-model requests the account has sent today (None: it does not say)."""

    def http(self) -> HttpClientFactory:
        """udl's client factory, reaching this fake in-process (and nothing else)."""

        @asynccontextmanager
        async def factory(*, tool: str) -> AsyncGenerator[httpx.AsyncClient]:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(self.app()), base_url=OPENROUTER_BASE
            ) as client:
                yield client

        return factory

    def app(self) -> Starlette:
        """The fake as an ASGI app (what http() mounts)."""
        return Starlette(
            routes=[
                Route("/api/v1/models", self._models, methods=["GET"]),
                Route("/api/v1/key", self._key, methods=["GET"]),
                Route("/api/v1/chat/completions", self._chat, methods=["POST"]),
            ]
        )

    async def _models(self, request: Request) -> Response:
        if (refused := self._refused(request)) is not None:
            return refused
        if self.status is not None:
            return self._failed()
        listed: JsonObject = {
            "data": [
                {
                    "id": name,
                    "name": name,
                    "context_length": self.contexts.get(name),
                    "pricing": (
                        {
                            "prompt": self.prices[name][0],
                            "completion": self.prices[name][1],
                            "request": "0",
                        }
                        if name in self.prices
                        else {}
                    ),
                    "supported_parameters": (
                        ["tools", "temperature"] if name in self.tool_models else ["temperature"]
                    ),
                }
                for name in self.models
            ]
        }
        return JSONResponse(listed)

    async def _key(self, request: Request) -> Response:
        if (refused := self._refused(request)) is not None:
            return refused
        if self.status is not None:
            return self._failed()
        key: JsonObject = {
            "data": {
                "label": self.label,
                "usage": self.used,
                "limit": self.limit,
                "limit_remaining": self.limit_remaining,
                "is_free_tier": self.limit is None,
                "usage_daily": self.used_daily,
                "free_model_daily_requests": self.free_model_daily,
            }
        }
        return JSONResponse(key)

    def _priced(self, answer: JsonObject) -> JsonObject:
        """`answer` with what it cost, as OpenRouter says of a chat's usage (a copy: a test's own
        answer is left as it was)."""
        usage = answer.get("usage")
        if not isinstance(usage, dict):
            return answer
        return {**answer, "usage": {**usage, "cost": float(self.cost)}}
