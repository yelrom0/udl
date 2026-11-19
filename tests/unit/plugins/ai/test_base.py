"""
----------------
- test_base.py -
----------------

The udl.ai plugin kind (udl.plugins.ai.base): an AI provider's options (its own model, with a
secret in it kept as a reference and brought back on use), the chat models (a tool message names
the call it answers), the AIProviderConnection record, and what a provider that overrides only
the three abstract methods gets: one streamed chunk, and no pulling.
"""

# System Imports
import json

# Package Imports
import pytest
from pydantic import SecretStr, ValidationError

# Local Imports
from support.ai import DEFAULT_ANSWER, DEFAULT_URL, FakeAI, FakeProviderOptions
from support.clock import FakeClock
from udl.config import SecretsSettings
from udl.db.records import record_kind, record_kinds
from udl.errors import (
    ConfigError,
    ErrorCode,
    PluginError,
    ProviderUnavailable,
    error_from_info,
    is_retryable,
)
from udl.http import api_client
from udl.plugins.ai.base import (
    AIProvider,
    AIProviderConnection,
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
    ToolSpec,
    Usage,
)
from udl.plugins.registry import AI, PLUGIN_KINDS, PluginRegistry
from udl.plugins.secrets.base import KeptSecret, SecretRef, load_secrets, store_secrets
from udl.plugins.secrets.memory import MemorySecretStore


class Chatty(AIProvider[ProviderOptions]):
    """A provider that implements only the three abstract methods, and answers "Hello"."""

    name = "chatty"
    title = "Chatty"
    summary = "A provider that overrides nothing else (for tests)."

    async def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True, detail="chatty")

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(name="fake-1", tools=True)]

    async def chat(self, request: ChatRequest) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role=ChatRole.ASSISTANT, content="Hello"),
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def chatty() -> Chatty:
    return Chatty(ProviderOptions(), http=api_client, clock=FakeClock())


def chat_request(**fields: object) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "model": "fake-1",
            "messages": [{"role": "user", "content": "Hello"}],
            **fields,
        }
    )


# ---------------------------------------------------------------------------------------------
# The kind
# ---------------------------------------------------------------------------------------------


def test_ai_is_a_plugin_kind() -> None:
    assert AI.group == "udl.ai"
    assert AI in PLUGIN_KINDS


def test_a_provider_is_registered_under_the_ai_kind() -> None:
    registry = PluginRegistry()

    registry.register(AI, FakeAI)
    [info] = registry.plugins(AI)

    assert (info.kind, info.name) == (AI.group, FakeAI.name)
    assert registry.get(AI, FakeAI.name) is FakeAI


# ---------------------------------------------------------------------------------------------
# Its options
# ---------------------------------------------------------------------------------------------


def test_a_provider_s_options_are_validated() -> None:
    options = FakeAI.options_of({"url": "http://localhost:1234/v1", "down": True})

    assert options == FakeProviderOptions(url="http://localhost:1234/v1", down=True)


def test_a_misspelt_option_is_refused() -> None:
    with pytest.raises(ConfigError, match="downn"):
        FakeAI.options_of({"downn": True})


def test_a_provider_that_overrides_nothing_has_no_options() -> None:
    assert Chatty.options_model is ProviderOptions
    assert Chatty.options_of({}) == ProviderOptions()


async def test_a_secret_in_a_provider_s_options_is_kept_as_a_reference() -> None:
    store = MemorySecretStore(SecretsSettings(store="memory"))
    options = FakeProviderOptions(url=DEFAULT_URL, api_key=SecretStr("sk-secret"))

    kept = await store_secrets(options, store, SecretRef.of("ai", "providers", "local"))

    assert isinstance(kept.api_key, KeptSecret)
    assert str(kept.api_key.secret) == "ai/providers/local/api_key"
    assert kept.model_dump(mode="json") == {
        "url": DEFAULT_URL,
        "down": False,
        "api_key": {"secret": "ai/providers/local/api_key"},
    }
    assert "sk-secret" not in kept.model_dump_json()
    assert FakeAI.options_of(kept.model_dump(mode="json")) == kept

    loaded = await load_secrets(kept, store)

    assert isinstance(loaded.api_key, SecretStr)
    assert loaded.api_key.get_secret_value() == "sk-secret"


# ---------------------------------------------------------------------------------------------
# The chat models
# ---------------------------------------------------------------------------------------------


def test_a_tool_call_holds_its_arguments_as_an_object() -> None:
    call = ToolCall(id="call-1", name="get_weather", arguments={"place": "X"})

    assert call.arguments == {"place": "X"}
    assert ToolCall(id="call-2", name="no_arguments").arguments == {}


def test_a_tool_spec_parameters_default_to_an_empty_object_schema() -> None:
    assert ToolSpec(name="ping").parameters == {"type": "object", "properties": {}}


def test_a_tool_message_names_the_call_it_answers() -> None:
    answered = ChatMessage(role=ChatRole.TOOL, content="sunny", tool_call_id="call-1")

    assert answered.tool_call_id == "call-1"

    with pytest.raises(ValidationError, match="tool_call_id"):
        ChatMessage(role=ChatRole.TOOL, content="sunny")


def test_a_request_defaults_to_no_tools_and_no_limits() -> None:
    request = chat_request()

    assert request.tools == []
    assert request.temperature is None
    assert request.max_tokens is None
    assert request.format is None


def test_a_request_takes_the_limits_a_provider_understands() -> None:
    request = chat_request(temperature=0, max_tokens=1)

    assert (request.temperature, request.max_tokens) == (0, 1)


@pytest.mark.parametrize(
    "fields",
    [
        {"messages": []},
        {"temperature": -0.5},
        {"temperature": 2.5},
        {"max_tokens": 0},
        {"max_tokens": 2_000_000},
    ],
    ids=["no-messages", "temperature-below", "temperature-above", "no-tokens", "too-many-tokens"],
)
def test_a_request_refuses_what_a_provider_cannot_be_asked(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        chat_request(**fields)


def test_an_answer_says_why_it_ended() -> None:
    response = ChatResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content="Hi"),
        done_reason=DoneReason.LENGTH,
        usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )

    assert response.done_reason is DoneReason.LENGTH
    assert response.usage is not None
    assert response.usage.total_tokens == 3


def test_a_model_says_what_it_can_do() -> None:
    model = ModelInfo(name="fake-1", size=1024, context_length=8192, tools=True)

    assert model.tools
    assert ModelInfo(name="fake-2").context_length is None


def test_a_pull_says_how_far_it_has_got() -> None:
    halfway = PullProgress(status="pulling", total=100, completed=50)

    assert (halfway.completed, halfway.total) == (50, 100)
    assert PullProgress(status="done").completed is None


# ---------------------------------------------------------------------------------------------
# The fake provider
# ---------------------------------------------------------------------------------------------


async def test_the_fake_answers_json_that_fits_the_schema_it_is_given() -> None:
    provider = FakeAI(FakeProviderOptions(), clock=FakeClock())
    request = chat_request(
        format={
            "type": "object",
            "properties": {"summary": {"type": "string"}, "n": {"type": "integer"}},
        }
    )

    response = await provider.chat(request)

    assert json.loads(response.message.content) == {"summary": DEFAULT_ANSWER, "n": 0}
    assert provider.requests == [request]  # (it keeps what it was asked)


async def test_the_fake_refuses_to_pull_when_it_is_down() -> None:
    provider = FakeAI(
        FakeProviderOptions(url="http://127.0.0.1:9/v1", down=True), clock=FakeClock()
    )

    with pytest.raises(ProviderUnavailable):
        async for _ in provider.pull("fake-1"):
            pass


async def test_the_fake_streams_its_answer_a_piece_at_a_time() -> None:
    provider = FakeAI(FakeProviderOptions(), clock=FakeClock())

    chunks = [chunk async for chunk in provider.stream(chat_request())]

    assert [chunk.content for chunk in chunks[:-1]] == ["Hello ", "from ", "the ", "fake"]
    assert chunks[-1].content == ""
    assert chunks[-1].done_reason is DoneReason.STOP


# ---------------------------------------------------------------------------------------------
# The connection record
# ---------------------------------------------------------------------------------------------


def test_a_provider_connection_is_a_record() -> None:
    assert record_kind("ai_provider") is AIProviderConnection
    assert AIProviderConnection in record_kinds()


def test_a_provider_connection_defaults_to_one_at_a_time_and_on() -> None:
    connection = AIProviderConnection(name="local", plugin="fake")

    assert connection.max_concurrent == 1
    assert connection.enabled
    assert connection.default_model is None
    assert connection.options == {}


def test_a_provider_connection_round_trips() -> None:
    example = AIProviderConnection.example()

    assert AIProviderConnection.model_validate(example.model_dump()) == example


def test_a_connection_s_example_holds_a_provider_s_options_with_its_key_as_a_reference() -> None:
    connection = AIProviderConnection.example()

    options = FakeAI.options_of(connection.options)

    assert options.url
    assert isinstance(options.api_key, KeptSecret)


# ---------------------------------------------------------------------------------------------
# What a provider that overrides nothing gets
# ---------------------------------------------------------------------------------------------


async def test_a_chatty_provider_answers_a_chat_and_says_it_is_healthy() -> None:
    provider = chatty()

    health = await provider.health()
    models = await provider.models()
    answer = await provider.chat(chat_request())

    assert repr(provider) == "<Chatty chatty>"
    assert health.ok
    assert [model.name for model in models] == ["fake-1"]
    assert answer.message.content == "Hello"
    assert answer.done_reason is DoneReason.STOP
    assert answer.usage is not None
    assert answer.usage.total_tokens == 2


async def test_a_chatty_provider_streams_its_answer_as_one_chunk() -> None:
    chunks = [chunk async for chunk in chatty().stream(chat_request())]

    assert len(chunks) == 1
    assert chunks[0].content == "Hello"
    assert chunks[0].done_reason is DoneReason.STOP
    assert chunks[0].usage is not None


async def test_a_provider_that_cannot_pull_says_so() -> None:
    assert not Chatty.supports_pull

    with pytest.raises(PluginError, match="chatty"):
        async for _ in chatty().pull("fake-1"):
            pass


def test_a_provider_must_answer_health_models_and_chats() -> None:
    assert AIProvider.__abstractmethods__ == frozenset({"chat", "health", "models"})


# ---------------------------------------------------------------------------------------------
# When it does not answer
# ---------------------------------------------------------------------------------------------


async def test_a_provider_that_is_down_is_a_retryable_error() -> None:
    provider = FakeAI(
        FakeProviderOptions(url="http://127.0.0.1:9/v1", down=True), clock=FakeClock()
    )

    with pytest.raises(ProviderUnavailable) as asked:
        await provider.chat(chat_request())

    error = asked.value
    assert error.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert is_retryable(error)
    assert "http://127.0.0.1:9/v1" in error.message
    assert error.hint

    again = error_from_info(error.to_info())

    assert isinstance(again, ProviderUnavailable)
    assert (again.code, again.message, again.hint) == (error.code, error.message, error.hint)
