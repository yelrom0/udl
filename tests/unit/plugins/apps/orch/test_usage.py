"""
------------
- test_usage.py -
------------

27.14: how much each agent has left.

A paid key's money read from the provider's `health()` (25.3), a free model's requests counted
from the transcript's `Usage` events since 00:00 UTC, 50 a day or 1,000 once the account has
bought credits, `low`/`out` from what is left, and the account's allowance shared by every
agent on it. The key itself never appears.
"""

# System Imports
import asyncio
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# Local Imports
from support.clock import FakeClock
from support.engine import registry, settings
from udl.clock import Clock
from udl.db.manager import DBManager
from udl.plugins.ai.base import KeyStatus, ProviderHealth
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import Agent
from udl.plugins.apps.orch.plugin import pick_connection
from udl.plugins.apps.orch.records import AgentSession, SessionEvent, SessionRecords
from udl.plugins.apps.orch.usage import (
    ALLOWANCE_DEFAULT,
    ALLOWANCE_PAID,
    AgentUsage,
    FreeUsage,
    KeyUsage,
    next_reset,
    provider_of,
    usage_of,
    usage_of_all,
)
from udl.plugins.runners.base import Usage

NOW = datetime(2026, 11, 24, 9, 30, tzinfo=UTC)
"""When the tests are at (a day with an hour left)."""


class _Sessions(SessionRecords):
    """A `SessionRecords` with one session per agent, whose transcript is the events it got."""

    def __init__(self, records: AppRecords, events: dict[str, list[SessionEvent]]) -> None:
        super().__init__(records)
        self._events = events
        self._one = {name: AgentSession.example() for name in events}

    def limited(self, name: str, until: datetime) -> None:
        """(The fake's session for `name`, limited until `until`.)"""
        self._one[name] = self._one[name].model_copy(update={"limited_until": until})

    async def sessions(self, agent: str) -> list[AgentSession]:
        return [self._one[agent]] if agent in self._one else []

    async def current(self, agent: str) -> AgentSession | None:
        return self._one.get(agent)

    async def transcript(self, session: object) -> list[SessionEvent]:
        for name, one in self._one.items():
            if one.id == session:
                return self._events[name]
        raise AssertionError(f"no session {session!r}")


def _records() -> AppRecords:
    """An in-memory AppRecords (an agent's name and model are all this needs of it)."""
    return AppRecords(DBManager(settings(Path(tempfile.mkdtemp())).db, registry()))


def _sessions(records: AppRecords, **events: list[SessionEvent]) -> SessionRecords:
    return _Sessions(records, events)


def clock() -> FakeClock:
    return FakeClock(start=NOW)


def agent(name: str = "deepseek-b", *, model: str = "deepseek/free") -> Agent:
    return Agent(name=name, title=name, runner="fake", model=model)


def usage_event(*, cost: str = "0.001", at: datetime = NOW) -> SessionEvent:
    """One `Usage` event, as a session's transcript holds it."""
    return SessionEvent(
        agent="deepseek-b",
        session=uuid4(),
        turn=1,
        event=Usage(input_tokens=10, output_tokens=5, cost=Decimal(cost)),
        created_at=at,
    )


def health(**key: object) -> ProviderHealth:
    """A provider's `health()` answer, its `key` the provider gave (25.3)."""
    return ProviderHealth(
        ok=True,
        detail="",
        credit_left=None,
        key=KeyStatus(
            label=str(key.get("label") or ""),
            spent=_money(key.get("spent")),
            limit=_money(key.get("limit")),
            left=_money(key.get("left")),
            spent_today=_money(key.get("spent_today")),
            is_free_tier=_flag(key.get("is_free_tier")),
            free_model_daily_requests=_int(key.get("free_model_daily")),
        ),
    )


def _int(value: object) -> int | None:
    """`value` as a count, or None when it is not one."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _flag(value: object) -> bool | None:
    """`value` as a flag, or None when it is not one."""
    return value if isinstance(value, bool) else None


def _money(value: object) -> Decimal | None:
    """`value` as USD, or None when it is not a number."""
    if value is None or isinstance(value, bool):
        return None
    return Decimal(str(value))


def measured(
    *events: SessionEvent,
    who: Agent,
    health: ProviderHealth | None = None,
    clock: Clock | None = None,
    account_today: int | None = None,
    connection: str = "",
) -> AgentUsage:
    """`usage_of` over a fake session whose transcript is `events`."""
    records = _records()
    return asyncio.run(
        usage_of(
            records,
            who,
            health=health,
            sessions=_sessions(records, **{who.name: list(events)}),
            clock=clock or FakeClock(start=NOW),
            account_today=account_today,
            connection=connection,
        )
    )


# ---------------------------------------------------------------------------------------------
# A paid key
# ---------------------------------------------------------------------------------------------


def test_a_paid_key_s_money_left_is_read_from_the_provider() -> None:
    found = measured(
        usage_event(),
        who=agent(model="deepseek/chat"),
        health=health(label="deepseek-b", spent="4.64", limit="8", left="3.36", spent_today="0.42"),
        clock=clock(),
        connection="openrouter",
    )

    assert isinstance(found.paid, KeyUsage)
    assert found.paid.connection == "openrouter"
    assert found.paid.left == Decimal("3.36")
    assert found.paid.limit == Decimal("8")
    assert found.paid.spent == Decimal("4.64")
    assert found.paid.spent_today == Decimal("0.42")


def test_a_paid_agent_s_line_is_its_money_left() -> None:
    found = measured(
        who=agent(model="deepseek/chat"),
        health=health(label="deepseek-b", spent="4.64", limit="8", left="3.36"),
        clock=clock(),
    )

    assert found.line == "$3.36 of $8 left"


def test_a_key_with_no_limit_says_only_what_is_left() -> None:
    found = measured(
        who=agent(model="deepseek/chat"),
        health=health(label="k", left="12.5"),
        clock=clock(),
    )

    assert found.line == "$12.5 left"


def test_a_paid_key_under_a_tenth_is_low_and_at_nothing_is_out() -> None:
    low = measured(
        who=agent(model="deepseek/chat"),
        health=health(label="k", spent="7.5", limit="8", left="0.5"),
        clock=clock(),
    )
    out = measured(
        who=agent(model="deepseek/chat"),
        health=health(label="k", spent="8", limit="8", left="0"),
        clock=clock(),
    )

    assert low.state == "low"
    assert out.state == "out"


def test_the_key_itself_is_never_in_the_result() -> None:
    """Only the label, the spend, the limit and what is left: never the key (27.14's rule)."""
    found = measured(
        who=agent(model="deepseek/chat"),
        health=health(
            label="deepseek-b", spent="1", limit="2", left="1", api_key="sk-or-v1-secret"
        ),
        clock=clock(),
    )

    assert "sk-or-v1-secret" not in found.model_dump_json()
    if found.paid is not None:
        assert "api_key" not in found.paid.model_dump()


# ---------------------------------------------------------------------------------------------
# A free model
# ---------------------------------------------------------------------------------------------


def test_a_free_model_s_requests_are_counted_from_the_transcript() -> None:
    found = measured(
        usage_event(),
        usage_event(),
        usage_event(),
        who=agent(model="deepseek/free"),
        health=None,
        clock=clock(),
    )

    assert isinstance(found.free, FreeUsage)
    assert found.free.requests_today == 3
    assert found.free.allowance == ALLOWANCE_DEFAULT
    assert found.free.left == ALLOWANCE_DEFAULT - 3


def test_a_free_agent_is_never_reported_as_paid() -> None:
    """(The account's key is not the free agent's: its Left and state are its requests, not
    the key's money.)"""
    found = measured(
        who=agent(model="deepseek/free"),
        health=health(label="acct", spent="4", limit="8", left="4"),
        clock=clock(),
    )

    assert found.paid is None
    assert found.free is not None
    assert found.line.startswith("50 of 50 requests")


def test_an_agent_on_a_free_tier_with_credits_has_a_thousand() -> None:
    """The account has bought $10 of credits: `is_free_tier` is false, so the allowance is
    1,000 a day (OpenRouter's rule) — even when the key is capped."""
    found = measured(
        usage_event(),
        who=agent(model="deepseek/free"),
        health=health(label="free", spent="12", limit="10", is_free_tier=False),
        clock=clock(),
    )

    assert found.free is not None
    assert found.free.allowance == ALLOWANCE_PAID
    assert found.free.left == ALLOWANCE_PAID - 1


def test_a_key_that_does_not_say_its_tier_is_read_as_free() -> None:
    """(None, not False: a provider that says nothing has not said it bought credits.)"""
    found = measured(
        usage_event(),
        who=agent(model="deepseek/free"),
        health=health(label="k", spent="1", limit="10"),
        clock=clock(),
    )

    assert found.free is not None
    assert found.free.allowance == ALLOWANCE_DEFAULT


def test_two_agents_on_one_account_share_the_allowance() -> None:
    """The allowance is the account's; each agent's own requests are its own, but the left is
    what is left after all of them."""
    records = _records()
    sessions = _sessions(
        records,
        **{
            "deepseek-b": [usage_event(), usage_event()],
            "ernie-c": [usage_event()],
        },
    )
    one, two = asyncio.run(
        usage_of_all(
            records,
            [agent("deepseek-b", model="deepseek/free"), agent("ernie-c", model="deepseek/free")],
            sessions=sessions,
            clock=clock(),
        )
    )

    assert one.free is not None
    assert two.free is not None
    assert one.free.allowance == two.free.allowance
    assert one.free.requests_today == 2
    assert two.free.requests_today == 1
    # (Each agent counts its own requests, but the allowance is one account's: both see the
    # same left, the allowance less the account's three requests.)
    assert one.free.left == two.free.left == ALLOWANCE_DEFAULT - 3
    assert one.free.resets_at == two.free.resets_at


def test_a_paid_agent_s_turns_do_not_come_off_a_free_agent_s_allowance() -> None:
    """(The allowance is the account's free requests: a paid agent's turns are its own money,
    not the free agent's requests.)"""
    records = _records()
    sessions = _sessions(
        records,
        **{
            "deepseek-b": [usage_event()],
            "ernie-c": [usage_event(), usage_event()],
            "claude": [usage_event()],
        },
    )
    rows = asyncio.run(
        usage_of_all(
            records,
            [
                agent("deepseek-b", model="deepseek/free"),
                agent("ernie-c", model="deepseek/free"),
                agent("claude", model="anthropic/claude"),
            ],
            sessions=sessions,
            clock=clock(),
        )
    )
    by = {one.agent: one for one in rows}

    assert by["deepseek-b"].free is not None
    assert by["ernie-c"].free is not None
    # (Only the two free agents' three requests count: the paid agent's turn is not one of them.)
    assert by["deepseek-b"].free.left == ALLOWANCE_DEFAULT - 3
    assert by["ernie-c"].free.left == ALLOWANCE_DEFAULT - 3
    assert by["claude"].free is None


def test_free_agents_on_two_accounts_each_count_their_own() -> None:
    """(The allowance is one account's, and an account is its provider: two free agents on
    different providers do not share a total.)"""
    records = _records()
    sessions = _sessions(
        records,
        **{
            "deepseek-b": [usage_event(), usage_event()],
            "ernie-c": [usage_event()],
        },
    )
    one, two = asyncio.run(
        usage_of_all(
            records,
            [
                agent("deepseek-b", model="openrouter/deepseek/free"),
                agent("ernie-c", model="x/free"),
            ],
            sessions=sessions,
            clock=clock(),
            health_for={"openrouter": None, "x": None},
        )
    )

    assert one.free is not None
    assert two.free is not None
    assert one.free.left == ALLOWANCE_DEFAULT - 2
    assert two.free.left == ALLOWANCE_DEFAULT - 1


def test_the_provider_s_own_free_count_is_used_when_it_gives_one() -> None:
    """(27.15: `free_model_daily_requests` is the provider's count of the account's free
    requests today; the transcript count is the fallback, and `source` says which.)"""
    found = measured(
        usage_event(),
        usage_event(),
        who=agent(model="deepseek/free"),
        health=health(label="k", spent="1", limit="10", free_model_daily=7),
        clock=clock(),
    )

    assert found.free is not None
    assert found.requests_today == 7
    assert found.source == "provider"


def test_the_transcript_count_is_the_fallback_and_says_counted() -> None:
    found = measured(
        usage_event(),
        who=agent(model="deepseek/free"),
        health=health(label="k", spent="1", limit="10"),
        clock=clock(),
    )

    assert found.source == "counted"
    assert found.requests_today == 1


def test_a_paid_key_is_read_from_the_provider_and_an_unmeasured_agent_is_none() -> None:
    paid = measured(
        who=agent(model="deepseek/chat"),
        health=health(label="k", spent="1", limit="10", left="9"),
        clock=clock(),
    )
    nothing = measured(who=agent(model="local/llama"), health=None, clock=clock())

    assert paid.source == "provider"
    assert nothing.source == "none"


def test_the_masked_label_of_the_key_is_never_in_the_result() -> None:
    """(27.15: OpenRouter's `label` is a masked preview of the key itself, not a name: it is
    not carried into a result, a log or an event.)"""
    preview = "sk-or-v1-xxx…yyy"
    found = measured(
        who=agent(model="deepseek/chat"),
        health=health(label=preview, spent="1", limit="10", left="9"),
        clock=clock(),
        connection="openrouter-ds-a",
    )

    assert preview not in found.model_dump_json()
    assert found.paid is not None
    assert found.paid.connection == "openrouter-ds-a"


def test_a_free_agent_s_line_says_the_allowance_and_the_reset() -> None:
    found = measured(
        usage_event(),
        who=agent(model="deepseek/free"),
        health=None,
        clock=clock(),
    )

    when = next_reset(clock()).astimezone()
    assert found.line == f"49 of 50 requests left, resets {when:%Y-%m-%d %H:%M}"


def test_a_free_agent_out_of_requests_is_out_and_under_a_tenth_is_low() -> None:
    few = measured(
        *([usage_event()] * 46),
        who=agent(model="deepseek/free"),
        health=None,
        clock=clock(),
    )
    none = measured(
        *([usage_event()] * 50),
        who=agent(model="deepseek/free"),
        health=None,
        clock=clock(),
    )

    assert few.state == "low"
    assert none.state == "out"


def test_the_connection_is_picked_by_name_then_plugin_then_first() -> None:
    """(27.14: one key per agent — the connection the model names wins; a plugin match is next;
    otherwise the first enabled one, so a model with no prefix still has a key to read.)"""
    from udl.plugins.ai.base import AIProviderConnection

    def conn(name: str, plugin: str, *, enabled: bool = True) -> AIProviderConnection:
        return AIProviderConnection(
            name=name, plugin=plugin, options={}, default_model="m", enabled=enabled
        )

    first = conn("openrouter-a", "openrouter")
    second = conn("openrouter-b", "openrouter")
    other = conn("openai", "openai")

    assert pick_connection([first, second, other], "openrouter-b") is second
    assert pick_connection([first, second, other], "openrouter") is first
    assert pick_connection([first, second, other], "deepseek") is first
    assert pick_connection([conn("openai", "openai", enabled=False), second], "openai") is second


def test_a_model_s_provider_is_its_prefix() -> None:
    """(`openrouter/deepseek/free` is OpenRouter's; a model with no prefix runs the runner's
    own default, so it has no provider of its own.)"""
    assert provider_of("openrouter/deepseek/free") == "openrouter"
    assert provider_of("deepseek/free") == "deepseek"
    assert provider_of("") == ""


def test_the_allowance_resets_at_midnight_utc() -> None:
    assert next_reset(clock()).isoformat() == "2026-11-25T00:00:00+00:00"


# ---------------------------------------------------------------------------------------------
# The agent's own turns
# ---------------------------------------------------------------------------------------------


def test_the_agent_s_own_turns_are_summed_over_its_sessions() -> None:
    found = measured(
        usage_event(cost="0.25"),
        usage_event(cost="0.25"),
        who=agent(model="deepseek/free"),
        health=None,
        clock=clock(),
    )

    assert found.requests_today == 2
    assert found.tokens_in == 20
    assert found.tokens_out == 10
    assert found.cost == Decimal("0.50")


def test_a_turn_from_before_midnight_is_not_today() -> None:
    """(Today's count, tokens and cost are since 00:00 UTC: a turn from yesterday is in none of
    them.)"""
    yesterday = NOW - timedelta(days=1)
    found = measured(
        usage_event(at=yesterday),
        usage_event(at=NOW),
        who=agent(model="deepseek/free"),
        health=None,
        clock=clock(),
    )

    assert found.requests_today == 1
    assert found.tokens_in == 10
    assert found.tokens_out == 5
    assert found.cost == Decimal("0.001")


# ---------------------------------------------------------------------------------------------
# A limited session
# ---------------------------------------------------------------------------------------------


def test_a_limited_session_is_out_and_says_when_it_is_back() -> None:
    """(A session the runner limited carries the time it said (27.7): the agent is `out` until
    then, whatever it has left.)"""
    records = _records()
    sessions = _Sessions(records, {"deepseek-b": [usage_event()]})
    sessions.limited("deepseek-b", NOW + timedelta(hours=2))
    found = asyncio.run(
        usage_of(
            records,
            agent(model="deepseek/free"),
            sessions=sessions,
            clock=clock(),
        )
    )

    assert found.limited_until == NOW + timedelta(hours=2)
    assert found.state == "out"


def test_a_limited_time_in_the_past_is_not_a_limit() -> None:
    records = _records()
    sessions = _Sessions(records, {"deepseek-b": [usage_event()]})
    sessions.limited("deepseek-b", NOW - timedelta(hours=2))
    found = asyncio.run(
        usage_of(records, agent(model="deepseek/free"), sessions=sessions, clock=clock())
    )

    assert found.limited_until is None
    assert found.state == "ok"


# ---------------------------------------------------------------------------------------------
# A provider that says nothing
# ---------------------------------------------------------------------------------------------


def test_an_agent_on_its_runner_s_default_model_is_paid() -> None:
    """(An empty model is not a free one: the agent runs its runner's default, which is paid,
    so it has no counted allowance and keeps its paid key's money.)"""
    found = measured(
        who=agent(model=""),
        health=health(label="acct", spent="4", limit="8", left="4"),
        clock=clock(),
    )

    assert found.free is None
    assert found.paid is not None
    assert found.paid.left == Decimal("4")


def test_a_provider_with_no_key_leaves_the_agent_unmeasured() -> None:
    found = measured(who=agent(model="local/llama"), health=None, clock=clock())

    assert found.paid is None
    assert found.free is None
    assert found.line == "nothing to measure"


def test_a_provider_with_no_key_says_nothing_of_it() -> None:
    """(A provider whose health has no key: read as saying nothing, not as a key with no
    fields.)"""
    found = measured(
        who=agent(model="deepseek/chat"),
        health=ProviderHealth(ok=True, detail=""),
        clock=clock(),
    )

    assert found.paid is None
    assert found.state == "ok"
