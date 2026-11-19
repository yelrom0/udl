"""
------------------
- test_openrouter.py -
------------------

The openrouter provider (udl.plugins.ai.openrouter): OpenRouter's API is OpenAI's, with prices on
its models, a key's label and credit, what each chat cost, and two refusals of its own (no
credit, and too many requests).

Its tests run against FakeOpenRouter (tests/support/ai_servers.py), which answers as OpenRouter
does: nothing here goes near the network (tests/smoke/test_openrouter.py is the one that may).
"""

# System Imports
from decimal import Decimal
from typing import Final

# Package Imports
import pytest

# Local Imports
from support.ai_servers import OPENROUTER_BASE, OPENROUTER_KEY, FakeOpenRouter
from support.clock import FakeClock
from udl.errors import NotAllowed, RateLimited
from udl.plugins.ai.base import (
    ChatMessage,
    ChatRequest,
    ChatRole,
    ModelInfo,
    ModelPricing,
    ProviderHealth,
)
from udl.plugins.ai.openrouter import (
    DEFAULT_URL,
    REFERER,
    OpenRouterOptions,
    OpenRouterProvider,
)

MODEL: Final = "fake-1"
"""The model these chats ask for."""


def options(**fields: object) -> OpenRouterOptions:
    """An OpenRouter provider's options, pointed at the fake."""
    return OpenRouterOptions.model_validate({"url": OPENROUTER_BASE, **fields})


def provider(fake: FakeOpenRouter, **fields: object) -> OpenRouterProvider:
    """The provider, against `fake`."""
    return OpenRouterProvider(
        options(api_key=OPENROUTER_KEY, **fields), http=fake.http(), clock=FakeClock()
    )


def chat() -> ChatRequest:
    """A chat: one user message."""
    return ChatRequest(
        model=MODEL, messages=[ChatMessage(role=ChatRole.USER, content="What is the weather?")]
    )


# ---------------------------------------------------------------------------------------------
# What it is
# ---------------------------------------------------------------------------------------------


def test_it_is_openrouter_and_says_where_it_is() -> None:
    assert (OpenRouterProvider.name, OpenRouterProvider.title) == ("openrouter", "OpenRouter")
    assert OpenRouterProvider.options_model is OpenRouterOptions


def test_its_url_is_openrouters_unless_the_options_say_otherwise() -> None:
    assert OpenRouterOptions().url == DEFAULT_URL == "https://openrouter.ai/api/v1"
    assert options(url="http://elsewhere.test/api/v1").url == "http://elsewhere.test/api/v1"


async def test_every_request_sends_its_key_and_says_who_it_is() -> None:
    # (OpenRouter asks who is calling: every request carries the referer and udl's name.)
    fake = FakeOpenRouter()
    openrouter = provider(fake)

    await openrouter.health()
    await openrouter.models()
    await openrouter.chat(chat())

    assert len(fake.headers_seen) == 3
    for sent in fake.headers_seen:
        assert sent["authorization"] == f"Bearer {OPENROUTER_KEY}"
        assert (sent["http-referer"], sent["x-title"]) == (REFERER, "udl")


# ---------------------------------------------------------------------------------------------
# What it has
# ---------------------------------------------------------------------------------------------


async def test_its_models_come_with_their_price_per_million_tokens() -> None:
    found = {model.name: model for model in await provider(FakeOpenRouter()).models()}

    priced = {name: model.pricing for name, model in found.items()}
    assert priced[MODEL] == ModelPricing(prompt=Decimal("1.5"), completion=Decimal("3"))
    assert priced["fake-2"] == ModelPricing(prompt=Decimal("0"), completion=Decimal("0"))


async def test_a_model_says_how_much_text_it_takes_and_whether_it_takes_tools() -> None:
    found = {model.name: model for model in await provider(FakeOpenRouter()).models()}

    assert found[MODEL].context_length == 128_000
    assert found[MODEL].tools
    assert not found["fake-2"].tools


async def test_a_variable_priced_model_has_no_price() -> None:
    # (OpenRouter lists a router, openrouter/auto, at "-1": not a price udl can show.)
    fake = FakeOpenRouter(prices={"fake-1": ("-1", "-1")})

    found = {model.name: model for model in await provider(fake).models()}

    assert found[MODEL].pricing is None


async def test_a_price_that_is_not_a_number_is_no_price() -> None:
    # (NaN and Infinity parse as Decimals, and Pydantic then refuses them: one odd model must
    # not lose the whole list.)
    fake = FakeOpenRouter(prices={"fake-1": ("NaN", "Infinity"), "fake-2": ("0", "0")})

    found = {model.name: model for model in await provider(fake).models()}

    assert found[MODEL].pricing is None
    assert found["fake-2"].pricing is not None


async def test_a_model_without_a_price_has_none() -> None:
    # (A model whose `pricing` says nothing udl can read is a model with no price.)
    fake = FakeOpenRouter(prices={})

    found = {model.name: model for model in await provider(fake).models()}

    assert isinstance(found[MODEL], ModelInfo)
    assert found[MODEL].pricing is None


# ---------------------------------------------------------------------------------------------
# What it used, and what that cost
# ---------------------------------------------------------------------------------------------


async def test_a_chat_says_what_it_cost() -> None:
    response = await provider(FakeOpenRouter()).chat(chat())

    assert response.usage is not None
    assert response.usage.cost == Decimal("0.00042")
    assert response.usage.total_tokens == 14


async def test_a_chat_asks_openrouter_to_say_what_it_used() -> None:
    fake = FakeOpenRouter()

    await provider(fake).chat(chat())

    assert fake.requests[-1]["usage"] == {"include": True}


async def test_a_streamed_answer_says_what_it_cost_too() -> None:
    chunks = [chunk async for chunk in provider(FakeOpenRouter()).stream(chat())]

    assert chunks[-1].usage is not None
    assert chunks[-1].usage.cost == Decimal("0.00042")


# ---------------------------------------------------------------------------------------------
# Its key
# ---------------------------------------------------------------------------------------------


async def test_its_health_says_its_labels_usage_and_limit() -> None:
    health = await provider(FakeOpenRouter()).health()

    assert health == ProviderHealth(
        ok=True,
        detail="deepseek-b: used 1.25 of 10 USD (8.75 left)",
        credit_left=Decimal("8.75"),
    )


async def test_a_key_that_resets_says_what_is_left_now() -> None:
    # (A key with limit_reset spends up to its limit each day: usage is all-time, so what is
    # left is OpenRouter's own limit_remaining, not limit - usage.)
    fake = FakeOpenRouter(used="40", limit="10", limit_remaining="4")

    health = await provider(fake).health()

    assert health.credit_left == Decimal("4")
    # (All-time usage and a per-period limit are not the same period: say each plainly.)
    assert health.detail == "deepseek-b: used 40 USD; 4 of 10 USD left"


async def test_a_key_with_a_limit_but_no_usage_still_says_its_limit() -> None:
    fake = FakeOpenRouter(used=None, limit="10")

    health = await provider(fake).health()

    assert health.credit_left == Decimal("10")
    assert health.detail == "deepseek-b: 10 of 10 USD left"


async def test_a_key_with_no_limit_has_no_credit_left() -> None:
    health = await provider(FakeOpenRouter(limit=None)).health()

    assert (health.ok, health.detail, health.credit_left) == (
        True,
        "deepseek-b: used 1.25 USD",
        None,
    )


async def test_a_key_it_refuses_is_not_healthy() -> None:
    health = await provider(FakeOpenRouter(status=401)).health()

    assert not health.ok
    assert health.problems


async def test_a_provider_that_does_not_answer_is_not_healthy() -> None:
    health = await provider(FakeOpenRouter(refuse=True)).health()

    assert not health.ok
    assert OPENROUTER_BASE in health.problems[0]


# ---------------------------------------------------------------------------------------------
# When it refuses
# ---------------------------------------------------------------------------------------------


async def test_no_credit_is_a_refusal_to_spend() -> None:
    fake = FakeOpenRouter(status=402, detail="Insufficient credits")

    with pytest.raises(NotAllowed, match="out of credit"):
        await provider(fake).chat(chat())


async def test_too_many_requests_is_rate_limited_with_its_retry_after() -> None:
    fake = FakeOpenRouter(status=429, detail="Rate limit exceeded", headers={"Retry-After": "7"})

    with pytest.raises(RateLimited) as asked:
        await provider(fake).chat(chat())

    assert asked.value.retry_after == 7
