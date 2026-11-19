"""
----------------
- openrouter.py -
----------------

OpenRouter: OpenAI's API, with what its models cost, what a key has left, and what each chat
spent (udl.plugins.ai.openrouter).

Its options are an OpenAI-compatible provider's, with its url defaulting to OpenRouter's own; a
chat asks OpenRouter to say what it used (`usage: {"include": true}`), which it answers with a
`cost` as well as tokens, and every request says who is calling (`HTTP-Referer`, `X-Title`), as
OpenRouter asks. Its health is its key's: the label OpenRouter gives it, how much of its limit is
used, and what is left.

Two refusals are OpenRouter's own, and both are the OpenAI-compatible provider's: a 402 is
NotAllowed ("out of credit"), and a 429 is RateLimited with its Retry-After.
"""

# System Imports
from decimal import Decimal
from typing import ClassVar, Final

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, JsonValue

# Local Imports
from udl.db.models import JsonObject
from udl.errors import UDLError
from udl.plugins.ai.base import ChatRequest, ModelInfo, ModelPricing, ProviderHealth
from udl.plugins.ai.openai import OpenAICompatibleProvider, OpenAIOptions, as_money

DEFAULT_URL: Final = "https://openrouter.ai/api/v1"
"""Where OpenRouter is."""
REFERER: Final = "https://github.com/yelrom0/udl"
"""What udl says it is to OpenRouter (its `HTTP-Referer`: where it is from)."""
_MILLION: Final = Decimal(1_000_000)
"""What OpenRouter's per-token prices are multiplied by (it gives USD per token)."""

__all__ = ["DEFAULT_URL", "REFERER", "OpenRouterOptions", "OpenRouterProvider"]


class OpenRouterOptions(OpenAIOptions):
    """OpenRouter's options: an OpenAI-compatible provider's, whose url is OpenRouter's unless it
    says otherwise."""

    url: str = DEFAULT_URL
    """Where its API is (OpenRouter's, unless a test or a proxy says otherwise)."""


class OpenRouterProvider(OpenAICompatibleProvider):
    """OpenRouter: OpenAI's API, with prices, a key's credit and what a chat cost."""

    name = "openrouter"
    title = "OpenRouter"
    summary = "OpenRouter's models, hosted: prices, credit and cost per chat."
    options_model: ClassVar[type[OpenRouterOptions]] = OpenRouterOptions
    supports_pull = False  # (it hosts the models: udl does not pull them)

    # -- what it is ----------------------------------------------------------------------------

    async def health(self) -> ProviderHealth:
        """Whether it answers, and what its key has: the label OpenRouter gives it, the limit and
        what is used, and what is left. Never raises: what is wrong is in the answer."""
        try:
            async with self._client() as client:
                response = await client.get(self._url("/key"), headers=self._headers())
        except UDLError as e:
            return ProviderHealth(ok=False, problems=[e.message])
        if response.status_code >= 400:
            return ProviderHealth(ok=False, problems=[self._error(response).message])
        key = self._parsed(response, _Key).data
        used, limit = as_money(key.usage), as_money(key.limit)
        # (A key with limit_reset spends up to its limit each period: usage is all-time, so what
        # is left is OpenRouter's own limit_remaining, and limit - usage only when it says none.)
        remaining = as_money(key.limit_remaining)
        resets = remaining is not None  # (a key that resets: usage is all-time, its limit is not)
        credit = remaining
        if credit is None and limit is not None:
            credit = limit - (used or Decimal(0))
        if used is None and limit is None:
            detail = key.label
        elif used is None:
            detail = f"{key.label}: {credit} of {limit} USD left"
        elif limit is None:
            detail = f"{key.label}: used {used} USD"
        elif resets:
            # (All-time usage and a per-period limit are not the same period: say each plainly.)
            detail = f"{key.label}: used {used} USD; {credit} of {limit} USD left"
        else:
            detail = f"{key.label}: used {used} of {limit} USD ({credit} left)"
        return ProviderHealth(ok=True, detail=detail, credit_left=credit)

    async def models(self) -> list[ModelInfo]:
        """The models it has (GET /models): what each takes, whether it takes tools, and what it
        costs per million tokens. Raises ProviderUnavailable when it does not answer."""
        async with self._holding(), self._client() as client:
            response = await client.get(self._url("/models"), headers=self._headers())
        if response.status_code >= 400:
            raise self._error(response)
        answer = self._parsed(response, _Models)
        return [
            ModelInfo(
                name=model.id,
                context_length=model.context_length,
                tools="tools" in model.supported_parameters,
                pricing=_pricing(model.pricing),
            )
            for model in answer.data
            if model.id
        ]

    # -- what it sends -------------------------------------------------------------------------

    def _body(self, request: ChatRequest) -> JsonObject:
        """`request` as OpenRouter takes it: OpenAI's body, asking it to say what the answer
        used (which is where its `cost` comes from)."""
        body: JsonObject = super()._body(request)
        body["usage"] = {"include": True}
        return body

    def _headers(self) -> dict[str, str]:
        """Its key, and who is calling: OpenRouter asks for a referer and a title."""
        return {**super()._headers(), "HTTP-Referer": REFERER, "X-Title": "udl"}


class _KeyInfo(BaseModel):
    """A key as OpenRouter describes it."""

    model_config = ConfigDict(extra="ignore")

    label: str = ""
    usage: JsonValue = None
    limit: JsonValue = None
    limit_remaining: JsonValue = None


class _Key(BaseModel):
    """What /key answers."""

    model_config = ConfigDict(extra="ignore")

    data: _KeyInfo = Field(default_factory=_KeyInfo)


class _Pricing(BaseModel):
    """A model's price, as OpenRouter gives it: USD per token, as strings."""

    model_config = ConfigDict(extra="ignore")

    prompt: JsonValue = None
    completion: JsonValue = None


class _ModelEntry(BaseModel):
    """A model as OpenRouter lists it."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    context_length: int | None = None
    pricing: _Pricing | None = None
    supported_parameters: list[str] = Field(default_factory=list[str])


class _Models(BaseModel):
    """What /models answers."""

    model_config = ConfigDict(extra="ignore")

    data: list[_ModelEntry] = Field(default_factory=list[_ModelEntry])


def _pricing(given: _Pricing | None) -> ModelPricing | None:
    """What a model costs per million tokens, from OpenRouter's per-token prices.

    None when it gave no price udl can read: a variable-priced router (`openrouter/auto`) is
    listed at "-1", and a price that is not a finite number is no price either."""
    if given is None:
        return None
    prompt, completion = as_money(given.prompt), as_money(given.completion)
    if prompt is None or completion is None or prompt < 0 or completion < 0:
        return None
    return ModelPricing(prompt=prompt * _MILLION, completion=completion * _MILLION)
