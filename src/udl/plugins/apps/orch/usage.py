"""
------------
- usage.py -
------------

27.14: how much each agent has left — a paid key's money, or a free model's requests today.

Paid: the provider key an agent spends from, read through the provider's `health()` (25.3's
`ProviderHealth.key`). Free: the requests the agent's own sessions sent since the allowance's
last reset, counted from the transcript's `Usage` events (27.7), against the allowance the
account has (50 a day, or 1,000 once it has bought $10 of credits), reset at 00:00 UTC.

`state` is `ok`, `low` (under `LOW_RATIO` of the limit or allowance) or `out` (nothing left,
or its session is limited). The key itself never leaves udl: only its label, what it has
spent, its limit and what is left.

The allowance is the account's, so `left` is what is left after every agent on it: each
agent's own `requests_today` is its own, but the account's total is what is subtracted.
"""

# System Imports
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.clock import Clock
from udl.db.records import Name
from udl.plugins.ai.base import ProviderHealth
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import Agent
from udl.plugins.apps.orch.records import SessionRecords
from udl.plugins.runners.base import Usage

__all__ = [
    "ALLOWANCE_DEFAULT",
    "ALLOWANCE_PAID",
    "RESET_AT",
    "AgentUsage",
    "FreeUsage",
    "KeyUsage",
    "UsageList",
    "UsageRequest",
    "UsageSource",
    "UsageState",
    "next_reset",
    "provider_of",
    "usage_of",
    "usage_of_all",
]

ALLOWANCE_DEFAULT: Final = 50
"""A free model's requests a day, unless the account has bought $10 of credits."""

ALLOWANCE_PAID: Final = 1_000
"""A free model's requests a day once the account has bought credits (OpenRouter's rule)."""

RESET_AT: Final = timedelta(hours=0)
"""When a day's allowance resets: 00:00 UTC (11:00 AEDT, as the ERNIE agents saw)."""

LOW_RATIO: Final = 0.1
"""What `low` is under: this much of the limit or allowance left."""


UsageState = Literal["ok", "low", "out"]
"""Where an agent's usage is: fine, running low, or out."""

UsageSource = Literal["counted", "provider", "none"]
"""Where a figure came from: `counted` from the transcript's `Usage` events, `provider` from
the provider's own count (27.15), or `none` when there was nothing to measure."""


class KeyUsage(BaseModel):
    """What a provider key has left, as its provider says (25.3's `ProviderHealth.key`)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    connection: str = ""
    """The udl connection the key was read through (27.15): the key is named by it, never by
    the provider's label, which is a masked preview of the key itself."""
    spent: Decimal | None = None
    """What it has spent, in USD, when the provider says."""
    limit: Decimal | None = None
    """What it may spend, in USD (None: no limit)."""
    left: Decimal | None = None
    """What is left, in USD (the provider's `limit_remaining`, or `limit - spent`)."""
    spent_today: Decimal | None = None
    """What it has spent today, when the provider says (`usage_daily`)."""
    free_tier: bool | None = None
    """Whether the account is on the free tier (OpenRouter's `is_free_tier`); None when the
    provider does not say."""
    free_model_daily_requests: int | None = None
    """How many free-model requests the account has sent today, when the provider counts them
    (OpenRouter's `free_model_daily_requests`); None when it does not say (27.15)."""


class FreeUsage(BaseModel):
    """A free model's allowance: what the account has, what this agent has sent, and when the
    allowance resets."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    requests_today: int = 0
    """The requests this agent's sessions sent today (each `Usage` event is one)."""
    allowance: int = ALLOWANCE_DEFAULT
    """What the account may send a day: 50, or 1,000 once it has bought credits."""
    left: int = 0
    """What is left of the allowance today (the account's, shared by every agent on it)."""
    resets_at: datetime
    """When the allowance resets (00:00 UTC)."""


class AgentUsage(BaseModel):
    """What one agent has left (27.14)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    agent: Name
    """The agent it is of."""
    runner: Name
    """The runner it runs."""
    model: str = ""
    """The model it runs (empty: its runner's default)."""
    paid: KeyUsage | None = None
    """Its provider key's usage, for a paid model (None: a free one, or no provider)."""
    free: FreeUsage | None = None
    """Its free allowance's usage, for a free model (None: a paid one)."""
    requests_today: int = 0
    """The requests its sessions sent today, whatever it runs."""
    tokens_in: int = 0
    """The tokens it was given today."""
    tokens_out: int = 0
    """The tokens it wrote today."""
    cost: Decimal | None = None
    """What its turns cost today, when its harness says."""
    limited_until: datetime | None = None
    """When its harness's quota is back, when its session says (27.7)."""
    state: UsageState = "ok"
    """Where its usage is: ok, low, or out."""
    source: UsageSource = "none"
    """Where its figures came from: `counted` from its transcript, `provider` from the
    provider's own count, `none` when there was nothing to measure (27.15)."""

    @property
    def line(self) -> str:
        """It as one line: `$3.36 of $8` or `412 of 1,000 requests left, resets 11:00`."""
        if self.paid is not None and self.paid.left is not None:
            of = f" of ${self.paid.limit:,}" if self.paid.limit is not None else ""
            return f"${self.paid.left}{of} left"
        if self.free is not None:
            when = self.free.resets_at
            return (
                f"{self.free.left:,} of {self.free.allowance:,} requests left, "
                f"resets {when.astimezone():%Y-%m-%d %H:%M}"
            )
        return "nothing to measure"


def next_reset(clock: Clock) -> datetime:
    """When the day's allowance resets: 00:00 UTC after `clock`."""
    now = clock.now().astimezone(UTC)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight + RESET_AT


def _state(
    left: Decimal | int | None, limit: Decimal | int | None, *, out: bool = False
) -> UsageState:
    """Where usage is: `out` when nothing is left (or it is limited), `low` under LOW_RATIO of
    the limit, `ok` otherwise."""
    if out or left is None:
        return "out" if out or left == 0 else "ok"
    if limit in (None, 0):
        return "ok" if left > 0 else "out"
    if Decimal(left) <= Decimal(limit) * Decimal(str(LOW_RATIO)):
        return "low"
    return "ok" if left > 0 else "out"


async def _counts(
    sessions: SessionRecords, agent: Agent, since: datetime
) -> tuple[int, int, int, Decimal]:
    """`agent`'s day: (requests, tokens in, tokens out, cost) since `since` (00:00 UTC). Each
    `Usage` event is one request; the tokens and cost are the harness's own figures."""
    today = tokens_in = tokens_out = 0
    cost = Decimal(0)
    for session in await sessions.sessions(agent.name):
        for event in await sessions.transcript(session.id):
            if not isinstance(event.event, Usage):
                continue
            if event.created_at < since:
                continue
            today += 1
            tokens_in += event.event.input_tokens
            tokens_out += event.event.output_tokens
            cost += event.event.cost or Decimal(0)
    return today, tokens_in, tokens_out, cost


async def usage_of(
    records: AppRecords,
    agent: Agent,
    *,
    health: ProviderHealth | None = None,
    sessions: SessionRecords | None = None,
    clock: Clock,
    account_today: int | None = None,
    connection: str = "",
    allowance: int | None = None,
) -> AgentUsage:
    """`agent`'s usage (27.14): its paid key from `health` (25.3's `ProviderHealth.key`), or
    its free allowance counted from its sessions' `Usage` events today. Never raises: an agent
    with nothing to measure is `ok` and says so.

    `account_today` is the account's free requests today across every agent (the allowance is
    one account's); None means this agent's own count, which is right when it is the only
    one."""
    sessions = sessions or SessionRecords(records)
    since = clock.now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = await _counts(sessions, agent, since)
    limited = await _limited_until(sessions, agent, clock)
    return await _row(agent, health, clock, counts, account_today, limited, connection, allowance)


async def usage_of_all(
    records: AppRecords,
    agents: list[Agent],
    *,
    health: ProviderHealth | None = None,
    health_for: Mapping[str, ProviderHealth | None] | None = None,
    connection_for: Mapping[str, str] | None = None,
    allowance_for: Mapping[str, int | None] | None = None,
    sessions: SessionRecords | None = None,
    clock: Clock,
) -> list[AgentUsage]:
    """Every agent's usage (27.14), read in one pass: each agent's transcript is read once, and
    the account's free total today (the allowance is shared by the free agents on it) is what
    each free agent's `left` is taken from.

    `health` is the provider's answer for every agent; `health_for` gives a different one per
    provider prefix (an agent on another provider spends another account's key), and wins where
    it has an entry."""
    sessions = sessions or SessionRecords(records)
    since = clock.now().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = [(one, *await _counts(sessions, one, since)) for one in agents]
    # (The allowance is one account's, and an account is its provider: free agents on different
    # providers (different `health_for` entries) each count their own account's requests.)
    per_account: dict[str, int] = {}
    for one, today, *_ in counts:
        if _is_free(one.model):
            key = provider_of(one.model)
            per_account[key] = per_account.get(key, 0) + today
    out: list[AgentUsage] = []
    for one, today, tokens_in, tokens_out, cost in counts:
        own = health if health_for is None else health_for.get(provider_of(one.model), health)
        limited = await _limited_until(sessions, one, clock)
        account_today = per_account.get(provider_of(one.model)) if _is_free(one.model) else None
        connection = (
            "" if connection_for is None else connection_for.get(provider_of(one.model), "")
        )
        out.append(
            await _row(
                one,
                own,
                clock,
                (today, tokens_in, tokens_out, cost),
                account_today,
                limited,
                connection,
                None if allowance_for is None else allowance_for.get(one.name),
            )
        )
    return out


def provider_of(model: str) -> str:
    """The provider prefix of a model id (`openrouter/deepseek/free` gives `openrouter`); empty
    when the id has no prefix (the runner's own default)."""
    return model.split("/", 1)[0] if "/" in model else ""


def _is_free(model: str) -> bool:
    """Whether `model` is a free one (its id ends `:free` or `/free`); an empty model is the
    runner's default, which is paid."""
    return bool(model) and model.rsplit(":", 1)[-1].rsplit("/", 1)[-1] == "free"


async def _row(
    agent: Agent,
    health: ProviderHealth | None,
    clock: Clock,
    counts: tuple[int, int, int, Decimal],
    account_today: int | None,
    limited: datetime | None,
    connection: str = "",
    allowance: int | None = None,
) -> AgentUsage:
    """One agent's row from its counts: the paid key, or the free allowance (the account's
    `left`), plus its own turns and its session's limit.

    A free agent's `requests_today` is the provider's own count when it gives one (27.15), and
    the transcript's count otherwise; `source` says which."""
    today, tokens_in, tokens_out, cost = counts
    key = _key_usage(health, connection)
    provider_requests = key.free_model_daily_requests if key is not None else None
    requests = provider_requests if provider_requests is not None else today
    free = _free_usage(
        requests, key, clock, model=agent.model, account_today=account_today, allowance=allowance
    )
    if free is not None:
        key = None  # (a free agent's key is the account's, not its own: it is not reported as paid)
    source: UsageSource = (
        "provider"
        if provider_requests is not None
        else ("counted" if free is not None else ("provider" if key is not None else "none"))
    )
    return AgentUsage(
        agent=agent.name,
        runner=agent.runner,
        model=agent.model,
        paid=key,
        free=free,
        requests_today=requests,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
        limited_until=limited,
        state=_state_of(key, free, limited=limited is not None),
        source=source,
    )


async def _limited_until(sessions: SessionRecords, agent: Agent, clock: Clock) -> datetime | None:
    """When the agent's harness quota is back, when its current session says (27.7): a session
    the runner limited carries the time it said, and only a future one counts."""
    session = await sessions.current(agent.name)
    if session is None or session.limited_until is None:
        return None
    return session.limited_until if session.limited_until > clock.now() else None


def _key_usage(health: ProviderHealth | None, connection: str = "") -> KeyUsage | None:
    """`health`'s key as a `KeyUsage`, or None when the provider gave none (a free provider).
    The provider's `label` is a masked preview of the key (27.15), so it is not carried: the
    key is named by the udl `connection` it was read through."""
    if health is None or health.key is None:
        return None
    key = health.key
    return KeyUsage(
        connection=connection,
        spent=key.spent,
        limit=key.limit,
        left=key.left,
        spent_today=key.spent_today,
        free_tier=key.is_free_tier,
        free_model_daily_requests=key.free_model_daily_requests,
    )


def _free_usage(
    today: int,
    key: KeyUsage | None,
    clock: Clock,
    *,
    model: str = "",
    account_today: int | None = None,
    allowance: int | None = None,
) -> FreeUsage | None:
    """A free model's allowance: 50 a day, or 1,000 once the account has bought credits. A paid
    model — one whose id does not end `:free` or `/free`, and an empty model (the runner's
    default, which is paid) — has none to measure.

    `left` is the account's: the allowance less the account's requests today, or this agent's
    own when the account's is not known."""
    # (OpenRouter's free-tier rule: 50 a day, 1,000 once the account has bought $10 of credits.
    # The account's key says `is_free_tier` when it is on the free tier; a paid key has none.)
    if not model or model.rsplit(":", 1)[-1].rsplit("/", 1)[-1] != "free":
        return None
    # (The allowance is 1,000 once the account has bought credits — a key that is not on the
    # free tier — 50 otherwise. A key that does not say is a free one.)
    allowance = (
        allowance
        if allowance is not None
        else (ALLOWANCE_PAID if key is not None and key.free_tier is False else ALLOWANCE_DEFAULT)
    )
    spent = account_today if account_today is not None else today
    return FreeUsage(
        requests_today=today,
        allowance=allowance,
        left=max(allowance - spent, 0),
        resets_at=next_reset(clock),
    )


def _state_of(key: KeyUsage | None, free: FreeUsage | None, *, limited: bool = False) -> UsageState:
    """Where the agent is: a paid key's left against its limit, else the free allowance; a
    limited session is `out` whatever is left."""
    if key is not None and key.left is not None and key.limit is not None:
        return _state(key.left, key.limit, out=limited or key.left <= 0)
    if free is not None:
        return _state(free.left, free.allowance, out=limited or free.left <= 0)
    return "out" if limited else "ok"


class UsageRequest(BaseModel):
    """Who to report usage for (27.14): one agent, or every agent."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    agent: Name | None = None
    """Only this agent (None: every agent udl has)."""


class UsageList(BaseModel):
    """What each agent has left (27.14), as `orch_usage` answers."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    items: list[AgentUsage] = Field(default_factory=list[AgentUsage])
    """One row per agent, by name."""
