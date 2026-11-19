"""
--------------
- test_openai.py -
--------------

The OpenAI-compatible provider (udl.plugins.ai.openai): what it sends (tools as
`{"type": "function", …}`, a JSON Schema for a structured answer, its key), what it makes of the
answer (a tool call's arguments read from a JSON string — or why they could not be), what it
makes of a streamed answer (SSE `data:` chunks up to [DONE]), and what each status it gets
means.
"""

# System Imports
import json
from pathlib import Path

# Package Imports
import pytest
from pydantic import JsonValue, SecretStr

# Local Imports
from support.ai_servers import BASE, KEY, FakeOpenAI
from support.clock import FakeClock

# Local Imports
from udl.db.models import JsonObject
from udl.errors import ConfigError, NotAllowed, ProviderUnavailable, RateLimited
from udl.plugins.ai.base import (
    ChatRequest,
    ChatRole,
    DoneReason,
    ToolCall,
    ToolSpec,
)
from udl.plugins.ai.openai import OpenAICompatibleProvider, OpenAIOptions
from udl.plugins.ai.process import ProcessPool
from udl.plugins.secrets.base import KeptSecret, SecretRef

TOOL = ToolSpec(
    name="get_weather",
    description="Look up the weather in a place.",
    parameters={
        "type": "object",
        "properties": {"place": {"type": "string"}, "days": {"type": "integer"}},
        "required": ["place"],
    },
)


def provider(fake: FakeOpenAI, options: OpenAIOptions | None = None) -> OpenAICompatibleProvider:
    """A provider of the fake (its own options unless it is given some)."""
    return OpenAICompatibleProvider(
        options or OpenAICompatibleProvider.options_of({"url": BASE}),
        http=fake.http(),
        clock=FakeClock(),
    )


def chat(
    *,
    tools: list[ToolSpec] | None = None,
    schema: JsonObject | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    messages: list[JsonObject] | None = None,
) -> ChatRequest:
    """A chat: one user message, and what is given besides."""
    return ChatRequest.model_validate(
        {
            "model": "fake-1",
            "messages": messages
            or [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "What is the weather in X?"},
            ],
            "tools": tools or [],
            "format": schema,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
    )


def sent(fake: FakeOpenAI) -> JsonObject:
    """The last chat call the fake was given."""
    return _object(fake.requests[-1])


def _with_key(key: SecretStr | KeptSecret) -> OpenAIOptions:
    """The provider's options, with `key` as its key."""
    given: dict[str, JsonValue] = {"url": BASE}
    options = OpenAIOptions.model_validate({**given, "api_key": key})
    assert isinstance(options, OpenAIOptions)
    return options


def _at(value: JsonValue, *path: str | int) -> JsonValue:
    """The value at `path` in `value` (a test's own JSON)."""
    found = value
    for part in path:
        if isinstance(part, int):
            assert isinstance(found, list)
            found = found[part]
        else:
            assert isinstance(found, dict)
            found = found[part]
    return found


def _object(value: JsonValue) -> JsonObject:
    """`value` as a JSON object (a test's own JSON)."""
    assert isinstance(value, dict)
    return value


# ---------------------------------------------------------------------------------------------
# What it sends
# ---------------------------------------------------------------------------------------------


async def test_it_asks_the_provider_for_its_models() -> None:
    fake = FakeOpenAI()

    models = await provider(fake).models()

    assert [model.name for model in models] == ["fake-1", "fake-2"]


async def test_it_sends_the_messages_and_the_model() -> None:
    fake = FakeOpenAI()

    await provider(fake).chat(chat())

    given = sent(fake)
    assert given["model"] == "fake-1"
    assert given["messages"] == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "What is the weather in X?"},
    ]


async def test_it_leaves_out_the_limits_it_was_not_given() -> None:
    fake = FakeOpenAI()

    await provider(fake).chat(chat(temperature=0.5, max_tokens=64))

    assert (_at(sent(fake), "temperature"), _at(sent(fake), "max_tokens")) == (0.5, 64)

    await provider(fake).chat(chat())

    assert "temperature" not in sent(fake)
    assert "max_tokens" not in sent(fake)


async def test_it_sends_tools_as_functions() -> None:
    fake = FakeOpenAI()

    await provider(fake).chat(chat(tools=[TOOL]))

    assert sent(fake)["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Look up the weather in a place.",
                "parameters": TOOL.parameters,
            },
        }
    ]


async def test_it_sends_a_json_schema_for_a_structured_answer() -> None:
    fake = FakeOpenAI()
    schema: JsonObject = {"type": "object", "properties": {"summary": {"type": "string"}}}

    await provider(fake).chat(chat(schema=schema))

    assert sent(fake)["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "answer", "schema": schema},
    }


async def test_it_sends_a_tool_answer_with_the_call_it_answers() -> None:
    fake = FakeOpenAI()
    request = chat(
        messages=[
            {"role": "user", "content": "Weather?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call-1", "name": "get_weather", "arguments": {"place": "X"}}
                ],
            },
            {"role": "tool", "content": "sunny", "tool_call_id": "call-1"},
        ]
    )

    await provider(fake).chat(request)

    given = sent(fake)
    assert _at(given, "messages", -1) == {
        "role": "tool",
        "content": "sunny",
        "tool_call_id": "call-1",
    }
    # (The assistant's call goes back as OpenAI has it: a function, its arguments a JSON string.)
    assert _at(given, "messages", 1) == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"place": "X"}'},
            }
        ],
    }


async def test_it_sends_its_key_when_it_has_one() -> None:
    fake = FakeOpenAI()

    with_key = provider(fake, _with_key(SecretStr(KEY)))

    await with_key.models()

    assert fake.keys == [f"Bearer {KEY}"]


async def test_a_key_that_is_still_a_reference_is_refused() -> None:
    fake = FakeOpenAI()
    provider_with_key = provider(fake, _with_key(KeptSecret(secret=SecretRef.of("ai", "local"))))

    with pytest.raises(ConfigError, match="was not read from the secret store"):
        await provider_with_key.models()


# ---------------------------------------------------------------------------------------------
# What it makes of the answer
# ---------------------------------------------------------------------------------------------


async def test_it_reads_the_message_it_answered_with() -> None:
    fake = FakeOpenAI(answer=_answer("sunny"))

    response = await provider(fake).chat(chat())

    assert response.message.role is ChatRole.ASSISTANT
    assert response.message.content == "sunny"
    assert response.done_reason is DoneReason.STOP
    assert response.usage is not None
    assert response.usage.total_tokens == 14


async def test_it_reads_a_tool_call_and_its_arguments() -> None:
    fake = FakeOpenAI(answer=_with_calls("get_weather", {"place": "X", "days": 2}))

    response = await provider(fake).chat(chat(tools=[TOOL]))

    [call] = response.message.tool_calls
    assert isinstance(call, ToolCall)
    assert (call.id, call.name, call.arguments) == (
        "call-1",
        "get_weather",
        {"place": "X", "days": 2},
    )
    assert call.arguments_error is None
    assert response.done_reason is DoneReason.TOOL_CALLS


async def test_a_tool_call_whose_arguments_do_not_parse_goes_back_as_an_error() -> None:
    fake = FakeOpenAI(answer=_with_calls("get_weather", "{not json"))

    response = await provider(fake).chat(chat(tools=[TOOL]))

    [call] = response.message.tool_calls
    assert call.arguments == {}
    assert "not JSON" in (call.arguments_error or "")
    assert response.done_reason is DoneReason.TOOL_CALLS


async def test_a_length_answer_says_it_ran_out_of_room() -> None:
    fake = FakeOpenAI(answer=_answer("cut", finish="length"))

    response = await provider(fake).chat(chat())

    assert response.done_reason is DoneReason.LENGTH


# ---------------------------------------------------------------------------------------------
# What it makes of a streamed answer
# ---------------------------------------------------------------------------------------------


async def test_it_streams_the_text_a_piece_at_a_time() -> None:
    fake = FakeOpenAI(answer=_answer("Hello from the fake"))

    chunks = [chunk async for chunk in provider(fake).stream(chat())]

    assert "".join(chunk.content for chunk in chunks) == "Hello from the fake"
    assert [chunk.content for chunk in chunks[:-1]] == ["Hello ", "from ", "the ", "fake"]
    assert chunks[-1].done_reason is DoneReason.STOP
    assert chunks[-1].usage is not None


async def test_it_streams_a_tool_call_that_arrives_in_pieces() -> None:
    fake = FakeOpenAI(answer=_with_calls("get_weather", {"place": "X", "days": 2}))

    chunks = [chunk async for chunk in provider(fake).stream(chat(tools=[TOOL]))]

    calls = [call for chunk in chunks for call in chunk.tool_calls]
    assert len(calls) == 1
    assert (calls[0].id, calls[0].name, calls[0].arguments) == (
        "call-1",
        "get_weather",
        {"place": "X", "days": 2},
    )
    assert chunks[-1].done_reason is DoneReason.TOOL_CALLS


async def test_a_models_answer_that_is_not_json_is_a_provider_that_cannot_be_read() -> None:
    # (A url missing /v1 lands on a web page, which answers 200 with HTML.)
    fake = FakeOpenAI(status=200, text="<html><body>a web page</body></html>")

    with pytest.raises(ProviderUnavailable, match="did not answer with JSON"):
        await provider(fake).models()


async def test_a_chat_answer_that_is_not_json_is_a_provider_that_cannot_be_read() -> None:
    fake = FakeOpenAI(status=200, text="<html><body>a web page</body></html>")

    with pytest.raises(ProviderUnavailable, match="did not answer with JSON"):
        await provider(fake).chat(chat())


async def test_it_reads_what_the_provider_says_of_a_refusal() -> None:
    fake = FakeOpenAI(status=500, detail="the model is loading")

    with pytest.raises(ProviderUnavailable, match="the model is loading"):
        await provider(fake).chat(chat())


# ---------------------------------------------------------------------------------------------
# What each status means
# ---------------------------------------------------------------------------------------------


async def test_a_refused_key_is_not_allowed() -> None:
    fake = FakeOpenAI(status=401, detail="invalid api key")

    with pytest.raises(NotAllowed, match="invalid api key"):
        await provider(fake).chat(chat())


async def test_an_empty_key_is_not_allowed() -> None:
    fake = FakeOpenAI(status=402, detail="out of credits")

    with pytest.raises(NotAllowed, match="out of credit"):
        await provider(fake).chat(chat())


async def test_a_model_it_has_not_is_a_configuration_error() -> None:
    fake = FakeOpenAI(status=404, detail="model not found")

    with pytest.raises(ConfigError, match="model not found"):
        await provider(fake).chat(chat())


async def test_being_throttled_is_rate_limited_with_its_retry_after() -> None:
    fake = FakeOpenAI(status=429, detail="slow down", headers={"Retry-After": "7"})

    with pytest.raises(RateLimited) as asked:
        await provider(fake).chat(chat())

    assert asked.value.retry_after == 7
    assert asked.value.tool == "openai-compatible"


async def test_a_provider_that_cannot_be_reached_does_not_answer() -> None:
    fake = FakeOpenAI(refuse=True)

    with pytest.raises(ProviderUnavailable, match="did not answer") as asked:
        await provider(fake).chat(chat())

    assert BASE in asked.value.message
    assert asked.value.hint


# ---------------------------------------------------------------------------------------------
# What it says of itself, and what udl may start for it
# ---------------------------------------------------------------------------------------------


async def test_its_health_is_its_models() -> None:
    fake = FakeOpenAI()

    healthy = await provider(fake).health()

    assert healthy.ok

    broken = await provider(FakeOpenAI(status=500, detail="no")).health()

    assert not broken.ok
    assert broken.problems


async def test_a_provider_that_is_down_is_unhealthy_rather_than_raising() -> None:
    health = await provider(FakeOpenAI(refuse=True)).health()

    assert not health.ok


async def test_a_command_outside_the_allowed_places_is_refused_when_it_is_saved(
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    allowed = OpenAICompatibleProvider.options_of(
        {"url": BASE, "command": ["/bin/sh"], "idle_timeout": 1}
    )

    refused = OpenAICompatibleProvider(
        allowed, clock=FakeClock(), processes=ProcessPool(scripts_dir=scripts)
    )

    with pytest.raises(NotAllowed, match="allowed_commands"):
        refused.check()


async def test_a_command_in_scripts_dir_is_accepted_when_it_is_saved(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    program = scripts / "llama-server"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o755)
    allowed = OpenAICompatibleProvider.options_of({"url": BASE, "command": [str(program)]})

    accepted = OpenAICompatibleProvider(
        allowed, clock=FakeClock(), processes=ProcessPool(scripts_dir=scripts)
    )

    accepted.check()  # (it does not raise)


# ---------------------------------------------------------------------------------------------


def _answer(
    content: str, *, finish: str = "stop", usage: bool = True, calls: list[JsonObject] | None = None
) -> JsonObject:
    """A chat answer with `content` (and what it used, unless `usage` is off)."""
    message: JsonObject = {"role": "assistant", "content": content}
    choice: JsonObject = {"index": 0, "message": message, "finish_reason": finish}
    answer: JsonObject = {
        "id": "chat-1",
        "object": "chat.completion",
        "model": "fake-1",
        "choices": [choice],
    }
    if usage:
        answer["usage"] = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
    return answer


def _with_calls(name: str, arguments: object) -> JsonObject:
    """A chat answer that asks for `name` with `arguments` (as the provider sends them)."""
    given = arguments if arguments is None or isinstance(arguments, str) else json.dumps(arguments)
    function: JsonObject = {"name": name, "arguments": given}
    call: JsonObject = {
        "index": 0,
        "id": "call-1",
        "type": "function",
        "function": function,
    }
    message: JsonObject = {"role": "assistant", "content": None, "tool_calls": [call]}
    choice: JsonObject = {"index": 0, "message": message, "finish_reason": "tool_calls"}
    usage: JsonObject = {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}
    answer: JsonObject = {
        "id": "chat-1",
        "object": "chat.completion",
        "model": "fake-1",
        "choices": [choice],
        "usage": usage,
    }
    return answer


# ---------------------------------------------------------------------------------------------
# The edges
# ---------------------------------------------------------------------------------------------


async def test_a_provider_without_a_url_is_refused() -> None:
    with pytest.raises(ConfigError, match="url"):
        OpenAICompatibleProvider.options_of({})


async def test_a_request_the_provider_cannot_make_is_a_configuration_error() -> None:
    fake = FakeOpenAI(status=400, detail="model is required")

    with pytest.raises(ConfigError, match="model is required"):
        await provider(fake).chat(chat())


async def test_a_rate_limit_without_a_retry_after_is_rate_limited() -> None:
    fake = FakeOpenAI(status=429, detail="slow down")

    with pytest.raises(RateLimited) as asked:
        await provider(fake).chat(chat())

    assert asked.value.retry_after is None


async def test_a_retry_after_that_is_a_date_is_not_waited_for() -> None:
    fake = FakeOpenAI(
        status=429, detail="slow down", headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
    )

    with pytest.raises(RateLimited) as asked:
        await provider(fake).chat(chat())

    assert asked.value.retry_after is None


async def test_a_failure_that_is_not_json_says_what_it_said() -> None:
    fake = FakeOpenAI(status=502, text="<html>bad gateway</html>")

    with pytest.raises(ProviderUnavailable, match="bad gateway"):
        await provider(fake).chat(chat())


async def test_a_failure_with_no_body_says_its_status() -> None:
    fake = FakeOpenAI(status=500, text="")

    with pytest.raises(ProviderUnavailable, match="500"):
        await provider(fake).chat(chat())


async def test_a_tool_call_with_no_arguments_is_read_as_none() -> None:
    fake = FakeOpenAI(answer=_with_calls("ping", None))

    response = await provider(fake).chat(chat(tools=[TOOL]))

    [call] = response.message.tool_calls
    assert (call.name, call.arguments, call.arguments_error) == ("ping", {}, None)


async def test_a_tool_call_whose_arguments_are_not_an_object_is_an_error() -> None:
    fake = FakeOpenAI(answer=_with_calls("ping", '"a string"'))

    response = await provider(fake).chat(chat(tools=[TOOL]))

    [call] = response.message.tool_calls
    assert call.arguments == {}
    assert "not a JSON object" in (call.arguments_error or "")


async def test_a_provider_that_refuses_its_models_does_not_list_them() -> None:
    fake = FakeOpenAI(status=401, detail="invalid api key")

    with pytest.raises(NotAllowed, match="invalid api key"):
        await provider(fake).models()


async def test_a_streamed_answer_that_fails_says_so() -> None:
    fake = FakeOpenAI(status=500, detail="the model is loading")

    with pytest.raises(ProviderUnavailable, match="the model is loading"):
        async for _ in provider(fake).stream(chat()):
            pass


async def test_a_message_that_only_calls_tools_says_nothing() -> None:
    fake = FakeOpenAI()
    request = chat(
        messages=[
            {"role": "user", "content": "Weather?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call-1", "name": "get_weather", "arguments": {}}],
            },
        ]
    )

    await provider(fake).chat(request)

    assert _at(sent(fake), "messages", 1) == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": "{}"},
            }
        ],
    }


async def test_an_answer_without_usage_has_none() -> None:
    fake = FakeOpenAI(answer=_answer("hi", usage=False))

    response = await provider(fake).chat(chat())

    assert response.usage is None


async def test_a_stream_reads_what_it_used_from_the_chunk_after_the_finish() -> None:
    # (OpenAI sends usage in a trailing chunk with no choices, after the finish_reason one.)
    fake = FakeOpenAI(answer=_answer("hi"))

    chunks = [chunk async for chunk in provider(fake).stream(chat())]

    last = fake.streamed[-1]
    assert (last["choices"], _at(last, "usage")) == (
        [],
        {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14},
    )
    assert len([chunk for chunk in chunks if chunk.done_reason is not None]) == 1
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.total_tokens == 14


async def test_a_stream_asks_what_the_answer_used() -> None:
    fake = FakeOpenAI()

    _ = [chunk async for chunk in provider(fake).stream(chat())]

    assert _at(sent(fake), "stream_options") == {"include_usage": True}
    assert _at(sent(fake), "stream") is True


async def test_a_stream_that_does_not_say_why_still_gives_its_calls() -> None:
    fake = FakeOpenAI(answer=_with_calls("get_weather", {"place": "X"}), no_finish=True)

    chunks = [chunk async for chunk in provider(fake).stream(chat(tools=[TOOL]))]

    calls = [call for chunk in chunks for call in chunk.tool_calls]
    assert len(calls) == 1
    assert (calls[0].name, calls[0].arguments) == ("get_weather", {"place": "X"})
    assert chunks[-1].done_reason is DoneReason.STOP


async def test_a_stream_that_is_not_json_is_a_provider_that_cannot_be_read() -> None:
    fake = FakeOpenAI(garbled=True)

    with pytest.raises(ProviderUnavailable, match="could not be read"):
        async for _ in provider(fake).stream(chat()):
            pass


async def test_a_repeated_tool_name_is_not_doubled() -> None:
    fake = FakeOpenAI(answer=_with_calls("get_weather", {"place": "X"}))

    chunks = [chunk async for chunk in provider(fake).stream(chat(tools=[TOOL]))]

    [call] = [call for chunk in chunks for call in chunk.tool_calls]
    assert call.name == "get_weather"
