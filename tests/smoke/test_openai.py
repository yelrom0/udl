"""
-----------------
- test_openai.py -
-----------------

OpenAI itself, against its real API: the one thing the fake cannot tell us — that its models
are still the ones udl lists, and that the key udl keeps is still its own. It talks to the
internet, so it never runs by default: `uv run pytest -m network`, with `UDL_OPENAI_KEY` set to a
real key.
"""

# System Imports
import os
from typing import Final

# Package Imports
import pytest

# Local Imports
from udl.errors import NotAllowed
from udl.plugins.ai.openai import OpenAIProvider, OpenAIProviderOptions

KEY: Final = os.environ.get("UDL_OPENAI_KEY", "")
"""The key these tests spend (none: they are skipped)."""

pytestmark = pytest.mark.skipif(not KEY, reason="UDL_OPENAI_KEY is not set")


def provider() -> OpenAIProvider:
    """The real provider, with the key from the environment."""
    return OpenAIProvider(OpenAIProviderOptions.model_validate({"api_key": KEY}))


async def test_its_models_are_still_the_ones_it_lists() -> None:
    found = await provider().models()

    assert found, "OpenAI lists its models"
    assert all(model.name for model in found)


async def test_its_key_is_still_its_own() -> None:
    health = await provider().health()

    assert health.ok, f"OpenAI did not answer: {health.problems}"
    assert health.credit_left is None  # (it has no credit endpoint: it does not say)


async def test_a_key_that_is_not_its_own_is_refused_as_one() -> None:
    with pytest.raises(NotAllowed, match="the key was refused"):
        await OpenAIProvider(
            OpenAIProviderOptions.model_validate({"api_key": "sk-not-a-real-key"})
        ).models()
