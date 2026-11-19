"""
-----------
- base.py -
-----------

AIProvider: the interface every AI provider implements (the udl.ai kind), the models a chat is
made of, and AIProviderConnection, a provider udl has been set up with.

A provider is a service that answers chats: it says whether it is well, lists its models, answers
a chat (with tools to call, and with a JSON Schema for a structured answer), streams an answer
for the playground, and — where it runs models itself — pulls one. It is code, not data: it sets
`name`, `title`, `summary`, its `options_model` and what it `requires`, and implements health(),
models() and chat(). Its options are a connection's, validated by its options_model: a secret in
them (an API key) is kept in udl's secret store as a reference, so a record's options never hold
the key itself.

A provider that is not running, or has nothing at its URL, is a ProviderUnavailable naming the
URL or the `command` that starts it.

tests/contract/test_ai_provider_contract.py is the behaviour every provider must have.
"""

# System Imports
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Self

# Package Imports
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    ValidationError,
    model_validator,
)

# Local Imports
from udl.clock import Clock
from udl.db.models import JsonObject
from udl.db.records import Name, Record
from udl.errors import ConfigError, PluginError, ProviderUnavailable
from udl.http import HttpClientFactory
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.base import SecretField

if TYPE_CHECKING:
    from udl.plugins.ai.process import ProcessPool

# Any provider: a provider of any options, as the registry holds them. (Not
# AIProvider[ProviderOptions]: O is invariant — a provider is made with its own options model and
# hands them out again — so an AIProvider[FakeProviderOptions] is not an
# AIProvider[ProviderOptions]. The alias is what AnApp does for AppPlugin[Any].)
type AnAI = AIProvider[Any]

__all__ = [
    "AIProvider",
    "AIProviderConnection",
    "AnAI",
    "ChatChunk",
    "ChatMessage",
    "ChatRequest",
    "ChatResponse",
    "ChatRole",
    "DoneReason",
    "ModelInfo",
    "ModelPricing",
    "ProviderHealth",
    "ProviderOptions",
    "ProviderUnavailable",
    "PullProgress",
    "ToolCall",
    "ToolSpec",
    "Usage",
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


# ---------------------------------------------------------------------------------------------
# What a chat is made of
# ---------------------------------------------------------------------------------------------


class ChatRole(StrEnum):
    """Who a message of a chat is from."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"  # (the answer a tool gave to the call before it)


class DoneReason(StrEnum):
    """Why an answer ended."""

    STOP = "stop"  # (the model had said what it had to)
    LENGTH = "length"  # (it ran out of the tokens it was allowed)
    TOOL_CALLS = "tool_calls"  # (it asked for tools instead of answering)


class ToolCall(_Model):
    """A tool a model asked to run: which one, with what arguments (a JSON object)."""

    id: str
    """What the call is called, so the tool's answer can name it."""
    name: str
    """The tool to run (one of the request's tools)."""
    arguments: JsonObject = Field(default_factory=dict)
    """What to run it with."""
    arguments_error: str | None = None
    """Why the provider could not read its arguments (the raw text), when it could not: the call
    is then not run, and this goes back to the model as the tool's answer."""


class ChatMessage(_Model):
    """One message of a chat: who it is from, what it says, and — when it is a tool call or a
    tool's answer — which call it is about."""

    role: ChatRole
    content: str = ""
    """What it says."""
    tool_calls: list[ToolCall] = Field(default_factory=list[ToolCall])
    """The tools the assistant asks to run (its messages only)."""
    tool_call_id: str | None = None
    """The call this answers (a tool's message only)."""

    @model_validator(mode="after")
    def _a_tool_message_names_its_call(self) -> Self:
        if self.role is ChatRole.TOOL and not self.tool_call_id:
            raise ValueError("a tool's message names the call it answers: set tool_call_id")
        return self


class ToolSpec(_Model):
    """A tool a chat is offered: what it is called, what it does, and what it takes."""

    name: str
    """What it is called (the model calls it by this)."""
    description: str = ""
    """What it does, in a line."""
    parameters: JsonObject = Field(default_factory=lambda: {"type": "object", "properties": {}})
    """Its parameters as a JSON Schema."""


class ChatRequest(_Model):
    """A chat to answer: which model, the messages so far, and how it may answer."""

    model: str
    """The model to answer with (the provider's own name for it)."""
    messages: Annotated[list[ChatMessage], Field(min_length=1)]
    """The messages so far, oldest first."""
    tools: list[ToolSpec] = Field(default_factory=list[ToolSpec])
    """The tools it may call (none: it can only answer)."""
    temperature: Annotated[float, Field(ge=0, le=2)] | None = None
    """How much it may vary its answer (the provider's default when None)."""
    max_tokens: Annotated[PositiveInt, Field(le=1_000_000)] | None = None
    """How many tokens it may write at most (the provider's default when None)."""
    format: JsonObject | None = None
    """A JSON Schema the answer must fit (structured output), when one is asked for."""


class Usage(_Model):
    """What an answer used: the tokens it was given, and the ones it wrote."""

    prompt_tokens: int = 0
    """The tokens it was given."""
    completion_tokens: int = 0
    """The tokens it wrote."""
    total_tokens: int = 0
    """Both together."""
    cost: Decimal | None = None
    """What it cost, in USD, when the provider says (OpenRouter does)."""


class ChatResponse(_Model):
    """A provider's answer: the message it answered with, why it ended, and what it used."""

    message: ChatMessage
    """Its answer (a message from the assistant, with the tool calls it asks for)."""
    done_reason: DoneReason = DoneReason.STOP
    """Why it ended."""
    usage: Usage | None = None
    """What it used, when the provider says."""


class ChatChunk(_Model):
    """One piece of a streamed answer: the text it adds, and — on the last piece — the tool calls
    it ended with, why it ended and what it used."""

    content: str = ""
    """The text this piece adds."""
    tool_calls: list[ToolCall] = Field(default_factory=list[ToolCall])
    """The calls that are complete in this piece."""
    done_reason: DoneReason | None = None
    """Why the answer ended, on the last piece."""
    usage: Usage | None = None
    """What it used, on the last piece (when the provider says)."""


# ---------------------------------------------------------------------------------------------
# What a provider says of itself
# ---------------------------------------------------------------------------------------------


class ModelPricing(_Model):
    """What a model costs: in USD per million tokens, as a provider that charges says."""

    prompt: Decimal
    """What a million tokens given to it cost."""
    completion: Decimal
    """What a million tokens it wrote cost."""


class ModelInfo(_Model):
    """A model a provider has: what it is called, and what it can do when the provider says."""

    name: str
    """Its name (what a chat's `model` is set to)."""
    size: int | None = None
    """Its size on disk, in bytes, when the provider says."""
    context_length: int | None = None
    """How much text it takes at once, in tokens, when the provider says."""
    tools: bool = False
    """Whether it can be given tools (a chat may call them)."""
    pricing: ModelPricing | None = None
    """What it costs, when the provider charges for it (OpenRouter does)."""


class ProviderHealth(_Model):
    """Whether a provider answers, as checking it found."""

    ok: bool
    """Whether it answers."""
    problems: list[str] = Field(default_factory=list[str])
    """What is wrong with it."""
    detail: str = ""
    """What it says of itself (its version, its key's label and limit), shown as it is."""
    credit_left: Decimal | None = None
    """What is left to spend, in USD, when the provider says (OpenRouter's key does)."""


class PullProgress(_Model):
    """How far pulling a model has got."""

    status: str
    """What is happening (e.g. "pulling manifest", "downloading")."""
    digest: str | None = None
    """What is being pulled, when the provider names it."""
    total: int | None = None
    """How much there is, in bytes, when the provider says."""
    completed: int | None = None
    """How much is done, in bytes, when the provider says."""


class ProviderOptions(_Model):
    """An AI provider's options (each provider's model extends it): where it is, and the key it
    is used with.

    The key is a SecretField: it is given as a secret, and store_secrets() leaves a reference to
    it here, so a connection's options hold where the key is, never the key.
    """

    url: str = ""
    """Where its API is; empty means the provider's own default."""
    api_key: SecretField | None = None
    """The key it is used with, or where udl's secret store has it."""


# ---------------------------------------------------------------------------------------------
# The kind
# ---------------------------------------------------------------------------------------------


class AIProvider[O: ProviderOptions](ABC):
    """An AI provider: a service that answers chats, lists its models and — where it runs models
    itself — pulls one. Made with its options (an AIProviderConnection's), udl's HTTP client and
    its clock.

    Subclasses set `name`, `title`, `summary`, `options_model`, `requires` and, when they run
    models themselves, `supports_pull`; they implement health(), models() and chat(), and
    override stream() or pull() where they can do better than the default.
    """

    name: ClassVar[str]  # e.g. "ollama": what a connection's `plugin` names it by
    title: ClassVar[str]  # e.g. "Ollama"
    summary: ClassVar[str] = ""  # what it is, in a line
    # What it needs installed (`udl plugins list` says what is missing).
    requires: ClassVar[tuple[Requirement, ...]] = ()
    options_model: ClassVar[type[ProviderOptions]] = ProviderOptions
    # Whether it can pull a model (it runs models itself: ollama). The pull operation is offered
    # for a provider that says so, and no other.
    supports_pull: ClassVar[bool] = False

    def __init__(
        self,
        options: O,
        *,
        http: HttpClientFactory,
        clock: Clock,
        processes: ProcessPool | None = None,
    ) -> None:
        self.options = options
        """Its options, as its options_model has them."""
        self.http = http
        """What makes its HTTP clients (udl's, or a test's fake servers')."""
        self.clock = clock
        """The clock (udl.clock.Clock): nothing here calls datetime.now() or asyncio.sleep()."""
        self.processes = processes
        """Where the programs of a provider that udl starts are kept (udl.plugins.ai.process's
        ProcessPool), or None to use the one this udl shares."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"

    @classmethod
    def options_of(cls, values: JsonObject) -> O:
        """`values` (a connection's options, as JSON) as this provider's options. Raises
        ConfigError when they are not."""
        try:
            found = cls.options_model.model_validate(values)
        except ValidationError as e:
            raise ConfigError(
                f"The {cls.name} provider's options are not valid: {_problems(e)}",
                hint=f"Its options: {', '.join(cls.options_model.model_fields)} "
                "(the API's schema gives their types).",
            ) from e
        return found  # pyright: ignore[reportReturnType] - (its options_model is an O)

    def check(self) -> None:
        """Raise what is wrong with its options, before they are saved (17.9's set_ai_provider
        calls it): a URL it cannot use, or a program udl may not start. The default checks
        nothing."""
        return

    @abstractmethod
    async def health(self) -> ProviderHealth:
        """Whether it answers: its URL, and its key when it has one. Never raises: what is wrong
        is in the answer (a provider that is down is ProviderUnavailable when it is used)."""

    @abstractmethod
    async def models(self) -> list[ModelInfo]:
        """The models it has. Raises ProviderUnavailable when it does not answer."""

    @abstractmethod
    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Answer `request` with a model of it. Raises ProviderUnavailable when it does not
        answer, and ConfigError when it has no such model."""

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """`request`'s answer as it is written (the playground's SSE sends these). The default is
        the whole answer in one piece, so a provider that cannot stream still works."""
        response = await self.chat(request)
        yield ChatChunk(
            content=response.message.content,
            tool_calls=response.message.tool_calls,
            done_reason=response.done_reason,
            usage=response.usage,
        )

    async def pull(self, model: str) -> AsyncIterator[PullProgress]:
        """The progress of pulling `model`, for a provider that runs models itself. The default
        refuses: the pull operation is only offered where supports_pull says otherwise."""
        raise PluginError(
            f"{self.name} cannot pull models",
            hint="Only a provider that runs models itself (ollama) can pull one.",
        )
        yield  # pragma: no cover - (never reached; this is what makes pull an async generator)


class AIProviderConnection(Record):
    """A named AI provider udl is set up with: which provider plugin it is, its options (a secret
    in them as a reference into the secret store), the model to use when a chat names none, and
    how many chats it may answer at once (one, by default: one GPU runs one model at a time)."""

    kind = "ai_provider"
    indexed = ("name", "plugin", "enabled")
    unique = (("name",),)

    name: Name
    """Its name."""
    description: str = ""
    """What it is for."""
    plugin: Annotated[str, StringConstraints(min_length=1)]
    """Which provider plugin answers for it (its name, e.g. ollama)."""
    options: JsonObject = Field(default_factory=dict)
    """Its plugin's options, with a secret kept as a reference ({"secret": "<ref>"})."""
    default_model: str | None = None
    """The model to answer with when one is not named (none: one is always named)."""
    max_concurrent: PositiveInt = 1
    """How many chats it may answer at once."""
    enabled: bool = True
    """Whether udl may use it."""

    @classmethod
    def example(cls) -> Self:
        return cls(
            name="local",
            description="The Ollama on this machine",
            plugin="ollama",
            options={
                "url": "http://127.0.0.1:11434",
                "api_key": {"secret": "ai/providers/local/api_key"},
            },
            default_model="llama3.2",
        )


def _problems(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'options'}: {problem['msg']}"
        for problem in error.errors()
    )
