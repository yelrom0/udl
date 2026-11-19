"""
---------
- ai.py -
---------

FakeAI: an AI provider (the udl.ai kind) that answers as it is scripted — a list of
ChatResponses, or a function of the request — and keeps every request it is given. It is the
provider the AI provider contract runs over, and the one the agent loop (17.5) and the ai action
(17.8) are tried against.

By default it answers with a tool call for the first tool it is offered (so a chat with tools has
a call to go on with), else with a line of text; a script or a responder replaces that.
"""

# System Imports
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

# Package Imports
from pydantic import JsonValue

# Local Imports
from udl.clock import Clock, SystemClock
from udl.db.models import JsonObject
from udl.errors import ProviderUnavailable
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
    ProviderOptions,
    PullProgress,
    ToolCall,
    Usage,
)

DEFAULT_URL = "http://fake.ai/v1"
"""Where a fake provider is, unless its options say otherwise."""

DEFAULT_ANSWER = "Hello from the fake"
"""What a fake provider answers when it is not asked for a tool."""

FAKE_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(name="fake-1", size=1024, context_length=8192, tools=True),
    ModelInfo(name="fake-2", size=2048, context_length=4096, tools=False),
)
"""The models a fake provider has."""

type ChatResponder = Callable[[ChatRequest], Awaitable[ChatResponse]]
"""What answers a request: a scripted function of it."""

_USAGE = Usage(prompt_tokens=11, completion_tokens=3, total_tokens=14)


class FakeProviderOptions(ProviderOptions):
    """A fake provider's options: where it is, and whether it is down."""

    down: bool = False
    """Whether it is down: every call to it is then a ProviderUnavailable, naming its url."""


class FakeAI(AIProvider[FakeProviderOptions]):
    """A fake provider: it answers from `replies` (in order), or with `respond(request)`, or as
    the default reply says, and keeps every request in `requests`."""

    name = "fake"
    title = "Fake AI"
    summary = "An AI provider that answers as it is scripted (for tests)."
    options_model = FakeProviderOptions
    supports_pull = True  # (a fake has every model, so it can always pull one)

    def __init__(
        self,
        options: FakeProviderOptions,
        *,
        http: HttpClientFactory = api_client,
        clock: Clock | None = None,
        replies: Sequence[ChatResponse] = (),
        respond: ChatResponder | None = None,
    ) -> None:
        super().__init__(options, http=http, clock=clock or SystemClock())
        self.requests: list[ChatRequest] = []
        """The requests it has been given, oldest first."""
        self.pulled: list[str] = []
        """The models it has been asked to pull."""
        self._replies = list(replies)
        self._respond = respond

    async def health(self) -> ProviderHealth:
        """Whether it is up (it is, unless its options say it is down)."""
        if self.options.down:
            return ProviderHealth(
                ok=False,
                problems=[f"nothing answers at {self.options.url}"],
                detail="down",
            )
        return ProviderHealth(ok=True, detail="the fake is up")

    async def models(self) -> list[ModelInfo]:
        """The models a fake provider has. Raises ProviderUnavailable when it is down."""
        self._refuse_when_down()
        return list(FAKE_MODELS)

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """The next scripted answer, the responder's, or the default reply. Raises
        ProviderUnavailable when it is down."""
        self._refuse_when_down()
        self.requests.append(request)
        if self._respond is not None:
            return await self._respond(request)
        if self._replies:
            return self._replies.pop(0)
        return _default_reply(request)

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """The answer, a piece at a time, so a streamed answer can be told from a whole one."""
        response = await self.chat(request)
        for piece in _pieces(response.message.content):
            yield ChatChunk(content=piece)
        yield ChatChunk(
            tool_calls=response.message.tool_calls,
            done_reason=response.done_reason,
            usage=response.usage,
        )

    async def pull(self, model: str) -> AsyncIterator[PullProgress]:
        """Pull `model`: two steps, ending with it there. Raises ProviderUnavailable when it is
        down."""
        self._refuse_when_down()
        self.pulled.append(model)
        yield PullProgress(status="pulling manifest", total=100, completed=0)
        yield PullProgress(status=f"pulled {model}", total=100, completed=100)

    def _refuse_when_down(self) -> None:
        """Raise ProviderUnavailable, naming its url, when the fake is down."""
        if self.options.down:
            raise ProviderUnavailable(
                f"The {self.name} provider at {self.options.url} did not answer",
                hint=f"Start it (a provider with a `command` is started on first use), or check "
                f"{self.options.url}.",
            )


def _default_reply(request: ChatRequest) -> ChatResponse:
    """What the fake answers when nothing is scripted: JSON that fits the schema it was given,
    a call of the first tool it is offered, or a line of text."""
    if request.format is not None:
        return ChatResponse(
            message=ChatMessage(role=ChatRole.ASSISTANT, content=json.dumps(_fits(request.format))),
            done_reason=DoneReason.STOP,
            usage=_USAGE,
        )
    if request.tools:
        [tool, *_] = request.tools
        return ChatResponse(
            message=ChatMessage(
                role=ChatRole.ASSISTANT,
                tool_calls=[ToolCall(id="call-1", name=tool.name, arguments={"x": 1})],
            ),
            done_reason=DoneReason.TOOL_CALLS,
            usage=_USAGE,
        )
    return ChatResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=DEFAULT_ANSWER),
        done_reason=DoneReason.STOP,
        usage=_USAGE,
    )


def _fits(schema: JsonObject) -> JsonObject:
    """An object that fits `schema`: each property it names, with a value of the JSON type it
    asks for (the fake's line for a string, and an empty one for the rest)."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return {}
    return {name: _value(spec) for name, spec in properties.items()}


def _value(spec: JsonValue) -> JsonValue:
    """A value of the JSON type `spec` asks for."""
    if not isinstance(spec, dict):
        return DEFAULT_ANSWER
    kind = spec.get("type")
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object":
        return {}
    return DEFAULT_ANSWER


def _pieces(text: str) -> list[str]:
    """`text` split into pieces that join back into it (each word with the space after it)."""
    return re.findall(r"\S+\s*", text)
