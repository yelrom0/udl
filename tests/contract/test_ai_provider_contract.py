"""
--------------------------------
- test_ai_provider_contract.py -
--------------------------------

The behaviour every AI provider (udl.ai) must have, run over each of them (each a Case: the
plugin class, a provider made from it, and the same kind made unreachable): it is named, titled
and described, and its options are its own model's; it is registered as a plugin of its kind; it
is healthy, lists its models, and answers a chat; a chat with tools gets tool calls back as typed
ToolCalls; a streamed answer is the same answer; a provider that does not answer is a
ProviderUnavailable naming its URL; and pulling a model reports progress where the provider can.

17.2 and 17.3 add their providers here, against fake servers (tests/support/ai_servers.py).
"""

# System Imports
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.ai import DEFAULT_URL, FakeAI, FakeProviderOptions
from support.ai_servers import BASE, FakeOpenAI
from support.clock import FakeClock
from udl.errors import PluginError, ProviderUnavailable
from udl.plugins.ai.base import (
    AIProvider,
    ChatMessage,
    ChatRequest,
    ChatRole,
    DoneReason,
    ModelInfo,
    ProviderHealth,
    ProviderOptions,
    PullProgress,
    ToolSpec,
)
from udl.plugins.ai.openai import OpenAICompatibleProvider
from udl.plugins.registry import AI, PluginRegistry

# The tool a chat is offered, and the arguments the fake's call carries for it.
TOOL = ToolSpec(
    name="get_weather",
    description="Look up the weather in a place.",
    parameters={
        "type": "object",
        "properties": {"x": {"type": "integer", "description": "Which place."}},
    },
)


@dataclass
class Case:
    """An AI provider plugin, a provider of it, and the same kind made unreachable."""

    plugin: type[AIProvider[Any]]
    provider: AIProvider[Any]
    broken: AIProvider[Any]
    url: str
    """Where the broken provider is (nothing answers there)."""
    model: str
    """The model its chats ask for."""


def fake_case() -> Case:
    """The fake provider, and a fake provider that is down."""
    return Case(
        plugin=FakeAI,
        provider=FakeAI(FakeProviderOptions(url=DEFAULT_URL), clock=FakeClock()),
        broken=FakeAI(FakeProviderOptions(url="http://127.0.0.1:9/v1", down=True)),
        url="http://127.0.0.1:9/v1",
        model="fake-1",
    )


def openai_case() -> Case:
    """The OpenAI-compatible provider, against the fake OpenAI API (in-process)."""
    return Case(
        plugin=OpenAICompatibleProvider,
        provider=OpenAICompatibleProvider(
            OpenAICompatibleProvider.options_of({"url": BASE}),
            http=FakeOpenAI().http(),
            clock=FakeClock(),
        ),
        broken=OpenAICompatibleProvider(
            OpenAICompatibleProvider.options_of({"url": BASE}),
            http=FakeOpenAI(refuse=True).http(),
            clock=FakeClock(),
        ),
        url=BASE,
        model="fake-1",
    )


CASES: dict[str, Callable[[], Case]] = {"fake": fake_case, "openai": openai_case}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]()


def _chat(case: Case, *, tools: bool = False) -> ChatRequest:
    """A chat request: one user message, and the tool when `tools`."""
    return ChatRequest(
        model=case.model,
        messages=[ChatMessage(role=ChatRole.USER, content="What is the weather in X?")],
        tools=[TOOL] if tools else [],
    )


# ---------------------------------------------------------------------------------------------
# What it is
# ---------------------------------------------------------------------------------------------


def test_a_provider_is_named_titled_and_described(case: Case) -> None:
    assert case.plugin.name
    assert case.plugin.title
    assert case.plugin.summary


def test_a_provider_has_options_of_its_own(case: Case) -> None:
    assert issubclass(case.plugin.options_model, ProviderOptions)
    assert isinstance(case.provider.options, case.plugin.options_model)


def test_a_provider_is_registered_as_a_plugin_of_its_kind(case: Case) -> None:
    registry = PluginRegistry()
    registry.register(AI, case.plugin)

    [info] = registry.plugins(AI)

    assert (info.kind, info.name) == (AI.group, case.plugin.name)
    assert registry.get(AI, case.plugin.name) is case.plugin


def test_what_is_not_a_provider_is_refused() -> None:
    class NotAProvider:
        name = "notaprovider"

    registry = PluginRegistry()

    with pytest.raises(PluginError, match=r"udl\.ai"):
        registry.register(AI, NotAProvider)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------------------------
# What it answers
# ---------------------------------------------------------------------------------------------


async def test_a_provider_is_healthy(case: Case) -> None:
    health = await case.provider.health()

    assert isinstance(health, ProviderHealth)
    assert health.ok


async def test_a_provider_lists_its_models(case: Case) -> None:
    found = await case.provider.models()

    assert found
    names = [model.name for model in found]
    assert len(names) == len(set(names))
    assert case.model in names
    for model in found:
        assert isinstance(model, ModelInfo)
        assert model.name


async def test_a_chat_answers_in_the_assistants_voice(case: Case) -> None:
    response = await case.provider.chat(_chat(case))

    assert response.message.role is ChatRole.ASSISTANT
    assert response.message.content
    assert response.done_reason is DoneReason.STOP
    assert response.message.tool_calls == []


async def test_a_chat_with_tools_returns_typed_tool_calls(case: Case) -> None:
    response = await case.provider.chat(_chat(case, tools=True))

    calls = response.message.tool_calls
    assert calls, "a chat with tools should get a tool call back"
    assert response.done_reason is DoneReason.TOOL_CALLS
    for call in calls:
        assert call.id
        assert call.name == TOOL.name
        assert isinstance(call.arguments, dict)


async def test_a_streamed_answer_is_the_same_as_a_whole_one(case: Case) -> None:
    whole = await case.provider.chat(_chat(case))

    chunks = [chunk async for chunk in case.provider.stream(_chat(case))]

    assert chunks
    assert "".join(chunk.content for chunk in chunks) == whole.message.content
    assert chunks[-1].done_reason is whole.done_reason
    assert [call for chunk in chunks for call in chunk.tool_calls] == whole.message.tool_calls


async def test_a_chat_that_asks_for_a_json_schema_is_answered_with_that_json(case: Case) -> None:
    schema = {
        "type": "object",
        "properties": {"summary": {"type": "string"}},
        "required": ["summary"],
    }
    request = _chat(case).model_copy(update={"format": schema})

    response = await case.provider.chat(request)

    answer = json.loads(response.message.content)  # (it is JSON, not prose)
    assert isinstance(answer, dict)
    assert set(schema["required"]) <= answer.keys()


async def test_a_provider_pulls_a_model_where_it_can(case: Case) -> None:
    if not case.plugin.supports_pull:
        with pytest.raises(PluginError):
            async for _ in case.provider.pull(case.model):
                pass
        return
    progress = [item async for item in case.provider.pull(case.model)]

    assert progress
    assert all(isinstance(item, PullProgress) for item in progress)
    last = progress[-1]
    assert last.total is None or last.completed == last.total


# ---------------------------------------------------------------------------------------------
# When it does not answer
# ---------------------------------------------------------------------------------------------


async def test_a_provider_that_is_down_is_unavailable_naming_its_url(case: Case) -> None:
    with pytest.raises(ProviderUnavailable) as asked:
        await case.broken.chat(_chat(case))

    assert case.url in asked.value.message
    assert asked.value.hint
    assert asked.value.retryable
    assert not (await case.broken.health()).ok


async def test_a_provider_that_is_down_says_so_for_its_models_too(case: Case) -> None:
    with pytest.raises(ProviderUnavailable) as asked:
        await case.broken.models()

    assert case.url in asked.value.message
