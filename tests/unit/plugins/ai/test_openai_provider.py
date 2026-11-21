"""
--------------------------
- test_openai_provider.py -
--------------------------

The openai provider (udl.plugins.ai.openai): OpenAI's API is the compatible one, with its url
filled in, its key udl's to keep (a `op://` reference works), and its refusals named as
OpenAI's — a 401 or a 403 is the key being refused, a 429 is OpenAI throttling, and there is no
credit endpoint, so `credit_left` is None.

Its tests run against FakeOpenAI (tests/support/ai_servers.py), in-process: nothing here goes
near the network (tests/smoke/ is the one that may, and only with a key).

(The compatible provider's own behaviour is tested in test_openai.py, which it shares its code
with: what is checked here is what OpenAI itself adds.)
"""

# System Imports
from decimal import Decimal
from typing import Final

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.ai_servers import BASE, KEY, FakeOpenAI
from support.clock import FakeClock
from support.secrets import FakeResolver
from udl.config import SecretsSettings
from udl.errors import ConfigError, NotAllowed, ProviderUnavailable, RateLimited
from udl.plugins.ai.base import ChatMessage, ChatRequest, ChatRole
from udl.plugins.ai.openai import OPENAI_URL, OpenAIProvider, OpenAIProviderOptions
from udl.plugins.secrets.base import KeptSecret, SecretRef, load_secrets
from udl.plugins.secrets.memory import MemorySecretStore
from udl.plugins.secrets.resolvers import ResolvingStore

MODEL: Final = "fake-1"
"""The model these chats ask for."""
REFERENCE: Final = "fake://vault-a/item-1/field-1"
"""A key that is a reference (what `op://…` would be): only the resolver may read it."""


def options(**fields: object) -> OpenAIProviderOptions:
    """OpenAI's options, pointed at the fake (its url is the only thing a test changes)."""
    return OpenAIProviderOptions.model_validate({"url": BASE, **fields})


def provider(fake: FakeOpenAI, **fields: object) -> OpenAIProvider:
    """The provider, against `fake`."""
    return OpenAIProvider(options(**fields), http=fake.http(), clock=FakeClock())


def chat() -> ChatRequest:
    """A chat: one user message."""
    return ChatRequest(
        model=MODEL, messages=[ChatMessage(role=ChatRole.USER, content="What is the weather?")]
    )


@pytest.fixture
def store() -> MemorySecretStore:
    """A secret store with nothing in it (as udl's own is, before a key is kept)."""
    return MemorySecretStore(SecretsSettings())


# ---------------------------------------------------------------------------------------------
# What it is
# ---------------------------------------------------------------------------------------------


def test_it_is_named_titled_and_described() -> None:
    assert OpenAIProvider.name == "openai"
    assert OpenAIProvider.title == "OpenAI"
    assert OpenAIProvider.summary


def test_its_url_is_openais_unless_it_says_otherwise() -> None:
    assert OpenAIProviderOptions.model_fields["url"].default == OPENAI_URL
    assert options().url == BASE  # (a test's fake, as it says)


def test_its_options_are_an_openai_compatible_providers() -> None:
    """Its options are the compatible provider's, so a connection's options read the same way."""
    from udl.plugins.ai.openai import OpenAIOptions

    assert issubclass(OpenAIProviderOptions, OpenAIOptions)


# ---------------------------------------------------------------------------------------------
# Its models and its health
# ---------------------------------------------------------------------------------------------


async def test_its_models_are_the_models_it_has() -> None:
    found = await provider(FakeOpenAI()).models()

    assert [one.name for one in found] == ["fake-1", "fake-2"]


async def test_its_health_is_the_same_call() -> None:
    """The models are its health check, and OpenAI has no credit endpoint: it does not say."""
    fake = FakeOpenAI()

    health = await provider(fake).health()

    assert health.ok
    assert health.credit_left is None
    assert len(fake.keys) == 1  # (asked once: the models are the health check)


async def test_a_provider_that_does_not_answer_is_unwell() -> None:
    health = await provider(FakeOpenAI(refuse=True)).health()

    assert not health.ok
    assert health.problems


async def test_models_of_a_provider_that_does_not_answer_raise() -> None:
    with pytest.raises(ProviderUnavailable):
        await provider(FakeOpenAI(refuse=True)).models()


# ---------------------------------------------------------------------------------------------
# Its refusals
# ---------------------------------------------------------------------------------------------


async def test_a_429_is_rate_limited_with_its_retry_after() -> None:
    fake = FakeOpenAI(status=429, headers={"Retry-After": "7"})

    with pytest.raises(RateLimited) as refused:
        await provider(fake).models()

    assert refused.value.retry_after == 7


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_key_is_not_allowed_and_says_so(status: int) -> None:
    """OpenAI answers 401 or 403 for a key that is not its own; both are said as the same
    refusal, in OpenAI's own words (the status is in the message)."""
    fake = FakeOpenAI(status=status, detail="Incorrect API key provided")

    with pytest.raises(NotAllowed) as refused:
        await provider(fake).models()

    assert "OpenAI: the key was refused" in refused.value.message
    assert "Incorrect API key provided" in refused.value.message


# ---------------------------------------------------------------------------------------------
# What a chat answers
# ---------------------------------------------------------------------------------------------


async def test_a_chat_answers_and_says_what_it_used() -> None:
    answer = await provider(FakeOpenAI()).chat(chat())

    assert answer.message.content
    assert answer.done_reason is not None


async def test_a_chat_can_be_streamed() -> None:
    chunks = [chunk async for chunk in provider(FakeOpenAI()).stream(chat())]

    assert chunks
    assert chunks[-1].done_reason is not None


async def test_the_price_is_read_when_the_provider_says_one() -> None:
    """OpenAI does not price a chat; a proxy in front of it may, and udl reads it if it does."""
    fake = FakeOpenAI()
    fake.answer = {
        "choices": [{"message": {"role": "assistant", "content": "Hello."}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": "0.0001"},
    }

    answer = await provider(fake).chat(chat())

    assert answer.usage is not None
    assert answer.usage.cost == Decimal("0.0001")


# ---------------------------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------------------------


async def test_the_key_is_sent_as_a_bearer_and_nowhere_else() -> None:
    fake = FakeOpenAI()

    await provider(fake, api_key=KEY).models()

    assert fake.keys == [f"Bearer {KEY}"]


async def test_no_key_is_no_authorization_header() -> None:
    fake = FakeOpenAI()

    await provider(fake).models()

    assert fake.keys == [""]


async def test_a_key_kept_in_the_store_is_read_from_it(store: MemorySecretStore) -> None:
    """A connection's key is kept as a reference: udl reads it (load_secrets) before the
    provider is made, so what is sent is the key, never where it lives."""
    kept = options(api_key=KeptSecret(secret=SecretRef("ai/providers/openai/api_key")))
    await store.set(SecretRef("ai/providers/openai/api_key"), SecretStr(KEY))
    read = await load_secrets(kept, store)

    fake = FakeOpenAI()
    await OpenAIProvider(read, http=fake.http(), clock=FakeClock()).models()

    assert fake.keys == [f"Bearer {KEY}"]


async def test_a_key_that_is_a_reference_is_resolved(store: MemorySecretStore) -> None:
    """O-D5: a key may be a reference (`op://…`, here a resolver's own scheme). The store udl
    uses when a resolver is enabled resolves it, and what is sent is the key it gave."""
    await store.set(SecretRef("ai/providers/openai/api_key"), SecretStr(REFERENCE))
    resolving = ResolvingStore(store, [FakeResolver(SecretsSettings())], clock=FakeClock())

    kept = options(api_key=KeptSecret(secret=SecretRef("ai/providers/openai/api_key")))
    read = await load_secrets(kept, resolving)

    fake = FakeOpenAI()
    await OpenAIProvider(read, http=fake.http(), clock=FakeClock()).models()

    assert fake.keys == ["Bearer resolved:vault-a/item-1/field-1"]


async def test_a_key_that_was_never_read_is_refused() -> None:
    """A kept reference that never went through the store is a mistake, not a key to send."""
    kept = options(api_key=KeptSecret(secret=SecretRef("ai/providers/openai/api_key")))
    made = OpenAIProvider(kept, http=FakeOpenAI().http())

    with pytest.raises(ConfigError, match="was not read from the secret store"):
        await made.models()
