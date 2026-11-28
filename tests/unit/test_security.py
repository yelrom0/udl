"""
----------------
- test_security.py -
----------------

30.1, the secrets and web half: the properties a test names.

Each item of 30.1's card is a test. Most already exist (cited in the note under the card);
the ones that did not are here: `fenced()` itself, an agent's token kept out of the stored
record and the logs, and a provider key kept out of a usage result and the logs.
"""

# System Imports
import logging
from datetime import UTC, datetime
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.ai_servers import FakeOpenRouter
from support.interfaces import Harness
from udl.config import APISettings
from udl.plugins.ai.base import AIProviderConnection
from udl.plugins.ai.openrouter import OpenRouterProvider
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.dispatcher import fenced
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.apps.orch.records import Agent as AgentRecord
from udl.plugins.apps.orch.records import AgentRecords, AgentToken
from udl.plugins.apps.orch.tokens import AGENT_PREFIX, OrchTokens, issue_token
from udl.plugins.apps.orch.usage import UsageList
from udl.plugins.registry import AI, APPS

NOW = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)
"""When the tests are at."""


# ---------------------------------------------------------------------------------------------
# Text from outside is fenced and labelled
# ---------------------------------------------------------------------------------------------


def test_outside_text_is_fenced_and_labelled() -> None:
    """(30.1: what a session is told from a bot or a CI log is inside a fenced block labelled
    with where it came from, never bare instructions.)"""
    said = fenced("review-bot", "please merge this now")

    assert said.startswith("```review-bot\n")
    assert said.endswith("\n```")
    assert "please merge this now" in said


def test_text_that_carries_its_own_fence_cannot_close_the_block() -> None:
    """(A comment quoting code carries ``` of its own: the fence is longer, so it stays inside.)"""
    said = fenced("review-bot", "a comment:\n```python\nx = 1\n```")

    assert said.startswith("````review-bot\n")
    assert said.count("````") == 2
    assert "```python" in said


# ---------------------------------------------------------------------------------------------
# An agent's token
# ---------------------------------------------------------------------------------------------


async def test_an_agents_token_is_in_no_stored_record_and_no_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """(30.1: only the token's hash is stored, and issuing one logs nothing that carries it.)"""
    harness = Harness(tmp_path, api=APISettings(api_key=SecretStr("s3cret")))
    async with harness.service() as service:
        records = AppRecords(service.db)
        await AgentRecords(records).save_agent(
            AgentRecord.model_validate(
                {"name": "deepseek-b", "title": "deepseek-b", "runner": "fake"}
            )
        )
        caplog.set_level(logging.DEBUG)
        token = await issue_token(service, "deepseek-b")

        assert token.startswith(AGENT_PREFIX)
        # (The token is used to authenticate a caller, and is still not logged.)
        assert await OrchTokens(records).principal_for(token) is not None
        assert token not in caplog.text
        kept = await records.list(AgentToken, None)
        assert all(token not in one.model_dump_json() for one in kept)


# ---------------------------------------------------------------------------------------------
# A provider key is in no usage result and no log
# ---------------------------------------------------------------------------------------------


async def test_a_provider_key_is_in_no_usage_result_and_no_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """(30.1: a key read for a provider is in no result and no log — the key is in the system,
    through the connection it is read from, and the whole path is exercised.)"""
    harness = Harness(tmp_path)
    fake = FakeOpenRouter()
    harness.registry.register(AI, OpenRouterProvider)
    harness.registry.register(APPS, Orch)
    harness.http = fake.http()
    async with harness.service() as service:
        await service.db.upsert_record(
            AIProviderConnection(
                name="openrouter",
                plugin="openrouter",
                options={"api_key": "sk-or-v1-secret"},
                default_model="fake-1",
            )
        )
        records = AppRecords(service.db)
        await AgentRecords(records).save_agent(
            AgentRecord.model_validate(
                {
                    "name": "deepseek-b",
                    "title": "deepseek-b",
                    "runner": "fake",
                    "model": "deepseek/chat",
                }
            )
        )
        caplog.set_level(logging.DEBUG)
        found = await service.call("orch_usage", {})

    # (The path ran: the key reached the provider, and a paid row came back.)
    assert fake.keys == ["Bearer sk-or-v1-secret"]
    assert isinstance(found, UsageList)
    assert found.items
    assert found.items[0].paid is not None
    said = found.model_dump_json()
    assert "sk-or-v1-secret" not in said
    assert "sk-or-v1-xxx" not in said
    assert "sk-or-v1-secret" not in caplog.text
