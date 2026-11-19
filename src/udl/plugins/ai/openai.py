"""
-------------
- openai.py -
-------------

OpenAICompatibleProvider: any endpoint that speaks OpenAI's API — llama.cpp's `llama-server`,
LM Studio, vLLM, LocalAI, or a hosted service that does the same.

It uses `/v1/chat/completions` and `/v1/models`: tools are sent as
`{"type": "function", "function": …}`, a tool call's `arguments` arrive as a JSON string (one
that does not parse becomes `ToolCall.arguments_error`, so it goes back to the model as an
error), streaming is the SSE `data:` chunks up to `[DONE]`, and structured output is
`response_format: {"type": "json_schema", …}`.

A provider with a `command` is started on its first use and stopped when it has been idle
(udl.plugins.ai.process). A provider that does not answer is a ProviderUnavailable naming its
URL; a refused key is NotAllowed, an empty key's 402 NotAllowed, a 429 RateLimited with its
Retry-After, and a model it has not ConfigError.
"""

# System Imports
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from typing import Final, Self

# Package Imports
import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SecretStr,
    TypeAdapter,
    ValidationError,
    model_validator,
)

# Local Imports
from udl.clock import Clock, SystemClock
from udl.db.models import JsonObject
from udl.errors import (
    ConfigError,
    NetworkError,
    NotAllowed,
    ProviderUnavailable,
    RateLimited,
    ToolUnavailable,
    UDLError,
)
from udl.http import HttpClientFactory, api_client
from udl.plugins.ai.base import (
    AIProvider,
    ChatChunk,
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ChatRole,
    DoneReason,
    ModelInfo,
    ProviderHealth,
    ToolCall,
    Usage,
)
from udl.plugins.ai.process import ProcessOptions, ProcessPool, default_pool
from udl.plugins.secrets.base import KeptSecret

DEFAULT_URL: Final = "http://127.0.0.1:8080/v1"
"""Where a provider is when its options say nowhere (a llama-server's own default)."""
_DONE: Final = "[DONE]"
"""What ends a streamed answer."""
_MAX_DETAIL: Final = 500
"""Characters of a provider's own error message that are shown."""

_OBJECT: Final = TypeAdapter[JsonObject](JsonObject)


class OpenAIOptions(ProcessOptions):
    """An OpenAI-compatible provider's options: where it is, its key, and — when udl starts it —
    its program and how long it may take and stay up. Its url is where it talks: it has to be
    given (there is no default endpoint for "OpenAI-compatible")."""

    @model_validator(mode="after")
    def _it_says_where_it_is(self) -> Self:
        if not self.url:
            raise ValueError(f"it has a url (e.g. {DEFAULT_URL})")
        return self


class _Function(BaseModel):
    """The function a tool call is: its name, and its arguments as the provider sends them."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    arguments: JsonValue = None


class _ToolCall(BaseModel):
    """A tool call as a provider sends it (OpenAI's shape)."""

    model_config = ConfigDict(extra="ignore")

    index: int = 0
    id: str | None = None
    function: _Function | None = None


class _Delta(BaseModel):
    """What a streamed chunk adds to the answer."""

    model_config = ConfigDict(extra="ignore")

    role: str | None = None
    content: str | None = None
    tool_calls: list[_ToolCall] = Field(default_factory=list[_ToolCall])


class _Choice(BaseModel):
    """One choice of a chat: its message or its delta, and why it ended."""

    model_config = ConfigDict(extra="ignore")

    message: _Delta | None = None
    delta: _Delta | None = None
    finish_reason: str | None = None


class _ChatAnswer(BaseModel):
    """What a chat call answers."""

    model_config = ConfigDict(extra="ignore")

    choices: list[_Choice] = Field(default_factory=list[_Choice])
    usage: _Usage | None = None


class _Usage(BaseModel):
    """What a chat used."""

    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: JsonValue = None  # (OpenRouter's, in USD)


class _ModelEntry(BaseModel):
    """One model as /models lists it."""

    model_config = ConfigDict(extra="ignore")

    id: str


class _ModelList(BaseModel):
    """What /models answers: the models it has."""

    model_config = ConfigDict(extra="ignore")

    data: list[_ModelEntry] = Field(default_factory=list[_ModelEntry])


class OpenAICompatibleProvider(AIProvider[OpenAIOptions]):
    """A provider that speaks OpenAI's API: llama-server, LM Studio, vLLM, or a hosted service
    that does the same."""

    name = "openai-compatible"
    title = "OpenAI-compatible"
    summary = "Any endpoint that speaks OpenAI's /v1/chat/completions and /v1/models."
    options_model = OpenAIOptions
    supports_pull = False  # (it runs no models of its own: it serves them)

    def __init__(
        self,
        options: OpenAIOptions,
        *,
        http: HttpClientFactory = api_client,
        clock: Clock | None = None,
        processes: ProcessPool | None = None,
    ) -> None:
        super().__init__(options, http=http, clock=clock or _system(), processes=processes)

    # -- what it is ----------------------------------------------------------------------------

    async def health(self) -> ProviderHealth:
        """Whether it answers: its models are its health check. Never raises: what is wrong is in
        the answer."""
        try:
            async with self._client() as client:
                response = await client.get(self._url("/models"), headers=self._headers())
        except UDLError as e:
            return ProviderHealth(ok=False, problems=[e.message])
        if response.status_code >= 400:
            return ProviderHealth(ok=False, problems=[_detail(response)])
        return ProviderHealth(ok=True, detail=_detail(response) or "answering")

    async def models(self) -> list[ModelInfo]:
        """The models it has (GET /models). Raises ProviderUnavailable when it does not answer,
        and ConfigError when it has no such model."""
        async with self._holding(), self._client() as client:
            response = await client.get(self._url("/models"), headers=self._headers())
        if response.status_code >= 400:
            raise self._error(response)
        answer = self._parsed(response, _ModelList)
        return [ModelInfo(name=model.id) for model in answer.data if model.id]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Answer `request` (POST /chat/completions). Raises ProviderUnavailable when it does not
        answer, NotAllowed for a key it refuses, RateLimited when it is throttling, and
        ConfigError for a model it has not."""
        async with self._holding(), self._client() as client:
            response = await client.post(
                self._url("/chat/completions"),
                json=self._body(request),
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise self._error(response)
        answer = self._parsed(response, _ChatAnswer)
        [choice, *_] = answer.choices or [_Choice()]
        delta = choice.message or choice.delta or _Delta()
        return ChatResponse(
            message=ChatMessage(
                role=ChatRole.ASSISTANT,
                content=delta.content or "",
                tool_calls=self._calls(delta.tool_calls),
            ),
            done_reason=_reason(choice.finish_reason),
            usage=_usage(answer.usage),
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """The answer as it is written: the SSE `data:` chunks up to `[DONE]`, each piece of text
        and each finished tool call as its own chunk, and the last one with why it ended (and what
        it used, which is asked for with `stream_options.include_usage`)."""
        body = self._body(request) | {
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with (
            self._holding(),
            self._client() as client,
            client.stream(
                "POST", self._url("/chat/completions"), json=body, headers=self._headers()
            ) as response,
        ):
            if response.status_code >= 400:
                await response.aread()
                raise self._error(response)
            calls: dict[int, _ToolCall] = {}
            usage: Usage | None = None
            done: DoneReason | None = None
            async for line in response.aiter_lines():
                try:
                    chunk = _chunk(line)
                except ValidationError as e:
                    raise ProviderUnavailable(
                        f"The {self.name} provider's stream could not be read: {e}",
                        hint="Its answer was not the JSON it should be (a proxy may have "
                        "answered instead).",
                    ) from e
                if chunk is None:
                    if line.strip() == _DONE:
                        break
                    continue  # (anything else: a comment, or an empty line)
                if chunk.usage is not None:
                    usage = _usage(chunk.usage)
                if not chunk.choices:
                    continue  # (the trailing chunk that says what the answer used)
                [choice, *_] = chunk.choices
                delta = choice.delta or choice.message or _Delta()
                for call in delta.tool_calls:
                    calls[call.index] = _merge(calls.get(call.index), call)
                if delta.content:
                    yield ChatChunk(content=delta.content)
                if choice.finish_reason is not None:
                    done = _reason(choice.finish_reason)
            # (One chunk ends the answer, at [DONE] or when the stream stops: it carries the calls
            # and what the answer used, which OpenAI sends after the finish_reason chunk.)
            yield ChatChunk(
                tool_calls=self._calls(list(calls.values())),
                done_reason=done or DoneReason.STOP,
                usage=usage,
            )

    def _parsed[M: BaseModel](self, response: httpx.Response, model: type[M]) -> M:
        """`response`'s body as `model`, or a ProviderUnavailable when it is not the JSON the API
        answers with (a url missing /v1 lands on a web page, which answers 200 with HTML)."""
        try:
            return model.model_validate(_OBJECT.validate_json(response.content))
        except ValidationError as e:
            raise ProviderUnavailable(
                f"The {self.name} provider at {self.options.url} did not answer with JSON",
                hint="Its answer was not the API's (a url missing /v1 may land on a web page).",
            ) from e

    def check(self) -> None:
        """Raise what is wrong with its options, before they are saved: a program udl may not
        start (its url has to be given: check the options model)."""
        if self.options.command:
            self._pool().program(self.options)

    # -- what it sends -------------------------------------------------------------------------

    @asynccontextmanager
    async def _client(self) -> AsyncGenerator[httpx.AsyncClient]:
        """A client of the provider: a connection it cannot make is a ProviderUnavailable naming
        its URL (a chat that cannot reach it says so), rather than udl's download error."""
        try:
            async with self.http(tool=self.name) as client:
                yield client
        except (NetworkError, ToolUnavailable) as e:
            raise self._unavailable(e.message) from e
        except httpx.HTTPError as e:  # (a client a test made, which maps nothing)
            raise self._unavailable(str(e)) from e

    def _unavailable(self, why: str) -> ProviderUnavailable:
        """The provider does not answer: its URL in the message, and what to do in the hint."""
        return ProviderUnavailable(
            f"The {self.name} provider at {self.options.url} did not answer: {why}",
            hint="It may not be running: start it (a provider with a `command` is started on "
            "first use), or check its url.",
        )

    def _body(self, request: ChatRequest) -> JsonObject:
        """`request` as OpenAI's API takes it (its messages' tool calls are its own shape, with
        the arguments as the JSON string a tool call carries)."""
        body: JsonObject = {
            "model": request.model,
            "messages": [self._message(message) for message in request.messages],
        }
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
        if request.format is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": request.format},
            }
        return body

    def _message(self, message: ChatMessage) -> JsonObject:
        """One message of a chat as OpenAI's API takes it."""
        sent: JsonObject = {"role": str(message.role)}
        if message.content or not message.tool_calls:
            sent["content"] = message.content
        elif message.role is ChatRole.ASSISTANT:
            sent["content"] = None  # (an assistant that only calls tools says nothing)
        if message.tool_calls:
            sent["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id is not None:
            sent["tool_call_id"] = message.tool_call_id
        return sent

    def _headers(self) -> dict[str, str]:
        """The headers its requests send (its key, when it has one)."""
        key = self.options.api_key
        if isinstance(key, SecretStr):
            return {"Authorization": f"Bearer {key.get_secret_value()}"}
        if isinstance(key, KeptSecret):
            raise ConfigError(
                f"The {self.name} provider's key is a reference ({key.secret}) that was not "
                "read from the secret store",
                hint="Load it first (udl does: load_secrets()).",
            )
        return {}

    def _url(self, path: str) -> str:
        """Where `path` is on the provider."""
        return f"{self.options.url.rstrip('/')}{path}"

    def _pool(self) -> ProcessPool:
        """Where this provider's program is kept: the pool it was given, else the one its udl
        shares."""
        return self.processes if self.processes is not None else default_pool()

    @asynccontextmanager
    async def _holding(self) -> AsyncGenerator[None]:
        """Start the provider's program if it has one and udl starts it, and hold it up while the
        request runs (so the idle timeout does not stop a program that is answering)."""
        process = self._pool().process(self)
        if process is None:
            yield
            return
        await process.ensure()
        async with process.hold():
            yield

    def _calls(self, calls: list[_ToolCall]) -> list[ToolCall]:
        """The tool calls as udl has them: the arguments read (a JSON object), and why they could
        not be when they could not."""
        found: list[ToolCall] = []
        for call in calls:
            arguments, broken = _arguments(call)
            found.append(
                ToolCall(
                    id=call.id or f"call-{call.index}",
                    name=(call.function.name if call.function else None) or "",
                    arguments=arguments,
                    arguments_error=broken,
                )
            )
        return found

    def _error(self, response: httpx.Response) -> UDLError:
        """The error a response's status means, with the provider's own message when it has
        one."""
        status, detail = response.status_code, _detail(response)
        if status in (401, 403):
            return NotAllowed(
                f"{self.name}: {detail}",
                hint="Check the provider's key (a provider's options name it, or "
                "op://… for the secret store).",
            )
        if status == 402:
            return NotAllowed(
                f"{self.name}: out of credit ({detail})",
                hint="Add credit to the account the key belongs to.",
            )
        if status == 404:
            return ConfigError(
                f"{self.name}: {detail}",
                hint=f"`udl ai models --provider {self.name}` lists the models it has.",
            )
        if status == 429:
            return RateLimited(
                f"{self.name}: {detail}",
                retry_after=_retry_after(response),
                tool=self.name,
                hint="Wait for the retry-after, then try again.",
            )
        if status >= 500:
            return ProviderUnavailable(
                f"{self.name} at {self.options.url} failed with {status}: {detail}",
                hint="It may be busy or restarting: try again.",
            )
        return ConfigError(f"{self.name}: {detail}")


def _system() -> Clock:
    """The system clock (a function so a test can pass its own)."""
    return SystemClock()


def _detail(response: httpx.Response) -> str:
    """What a provider says went wrong (its `error.message`, else its body's first line)."""
    body = response.text.strip()
    try:
        parsed = _OBJECT.validate_json(response.content)
    except ValueError:
        return body[:_MAX_DETAIL] or f"HTTP {response.status_code}"
    error = parsed.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message[:_MAX_DETAIL]
    elif isinstance(error, str) and error:
        return error[:_MAX_DETAIL]
    return body[:_MAX_DETAIL] or f"HTTP {response.status_code}"


def _retry_after(response: httpx.Response) -> float | None:
    """The seconds a 429's Retry-After asks udl to wait."""
    given = response.headers.get("Retry-After")
    if given is None:
        return None
    try:
        return float(given)
    except ValueError:
        return None  # (a date, which udl does not wait for)


def _chunk(line: str) -> _ChatAnswer | None:
    """The answer in one SSE line (`data: {…}`), or None for anything else."""
    if not line.startswith("data:"):
        return None
    data = line.removeprefix("data:").strip()
    if not data or data == _DONE:
        return None
    return _ChatAnswer.model_validate(_OBJECT.validate_json(data))


def _merge(sofar: _ToolCall | None, piece: _ToolCall) -> _ToolCall:
    """A streamed tool call with `piece` added: its name and its arguments arrive in pieces, by
    index."""
    if sofar is None:
        return piece
    function = sofar.function or _Function()
    added = piece.function
    return _ToolCall(
        index=sofar.index,
        id=sofar.id or piece.id,
        # (The name arrives whole in the first piece; a server that repeats it must not double
        # it, so the first one is kept.)
        function=_Function(
            name=function.name or (added.name if added else None) or "",
            arguments=_joined(function.arguments, added.arguments if added else None),
        ),
    )


def _joined(sofar: JsonValue, piece: JsonValue) -> JsonValue:
    """Two pieces of a tool call's arguments joined (a JSON string, sent in pieces)."""
    if isinstance(sofar, str) and isinstance(piece, str):
        return sofar + piece
    if sofar is None:
        return piece
    return sofar


def _arguments(call: _ToolCall) -> tuple[JsonObject, str | None]:
    """A tool call's arguments as a JSON object, and why they could not be read when they could
    not: the model is then told, and the call is not run."""
    raw = call.function.arguments if call.function else None
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as e:
            return {}, f"the arguments are not JSON ({e.msg} at position {e.pos}): {raw[:200]}"
    if not isinstance(raw, dict):
        return {}, f"the arguments are not a JSON object: {raw}"
    return _OBJECT.validate_python(raw), None


def _reason(given: str | None) -> DoneReason:
    """Why an answer ended, as udl has it."""
    return {
        "length": DoneReason.LENGTH,
        "tool_calls": DoneReason.TOOL_CALLS,
        "function_call": DoneReason.TOOL_CALLS,
    }.get(given or "", DoneReason.STOP)


def _usage(given: _Usage | None) -> Usage | None:
    """What a chat used, as udl has it (None: the provider said nothing)."""
    if given is None:
        return None
    return Usage(
        prompt_tokens=given.prompt_tokens,
        completion_tokens=given.completion_tokens,
        total_tokens=given.total_tokens,
        cost=_cost(given.cost),
    )


def _cost(given: JsonValue) -> Decimal | None:
    """What a chat cost, in USD, as the provider gave it (None: it said nothing, or something
    that is not a number)."""
    if given is None:
        return None
    try:
        return Decimal(str(given))
    except InvalidOperation:
        return None
