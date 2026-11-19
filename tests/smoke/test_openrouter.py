"""
--------------------
- test_openrouter.py -
--------------------

OpenRouter itself, against its real API: the one thing the fake cannot tell us — that its models
still carry the prices udl reads, and that its key's endpoint still says what it has left. It
talks to the internet, so it never runs by default: `uv run pytest -m network`, with
`UDL_OPENROUTER_KEY` set to a real key (a key with a limit, so this cannot cost much).
"""

# System Imports
import os
from decimal import Decimal
from typing import Final

# Package Imports
import pytest

# Local Imports
from udl.plugins.ai.base import ChatMessage, ChatRequest, ChatRole
from udl.plugins.ai.openrouter import DEFAULT_URL, OpenRouterProvider

KEY: Final = os.environ.get("UDL_OPENROUTER_KEY", "")
"""The key these tests spend (none: they are skipped)."""

pytestmark = pytest.mark.skipif(not KEY, reason="UDL_OPENROUTER_KEY is not set")


def provider() -> OpenRouterProvider:
    """The real provider, with the key from the environment."""
    return OpenRouterProvider(OpenRouterProvider.options_of({"url": DEFAULT_URL, "api_key": KEY}))


async def test_its_models_still_carry_their_prices() -> None:
    found = await provider().models()

    assert found
    priced = [model for model in found if model.pricing is not None]
    assert len(priced) == len(found), "every model OpenRouter lists has a price"
    for model in priced:
        assert model.pricing is not None
        assert model.pricing.prompt >= Decimal(0)
        assert model.pricing.completion >= Decimal(0)
    assert any(model.context_length for model in found)


async def test_its_key_still_says_what_it_has() -> None:
    health = await provider().health()

    assert health.ok, health.problems
    assert health.detail


async def test_a_chat_still_says_what_it_used_and_cost() -> None:
    response = await provider().chat(
        ChatRequest(
            model="openrouter/auto",
            messages=[ChatMessage(role=ChatRole.USER, content="Say hi in one word.")],
            max_tokens=8,
        )
    )

    assert response.message.content
    assert response.usage is not None
    assert response.usage.total_tokens > 0
    assert response.usage.cost is not None
