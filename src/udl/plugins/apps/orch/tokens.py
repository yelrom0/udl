"""
------------
- tokens.py -
------------

An agent's token: what an agent sends to call udl as itself (27.2), and the one place roles are
compared.

A token is `udl_agent_<random>`; only its SHA-256 is kept (orch.records.AgentToken), and the
token itself is in udl's secret store at `apps/orch/agents/<name>/token`, where the dispatcher
reads it to hand it to the agent's runner. The role table says what each role may call: an
implementer the handful of operations that are its work, a manager the same (27.8 adds the
powers it switches on), a reviewer every orch_* operation that is not admin. Everything else —
and every operation that is not orch's — is refused, whatever its hints.

udl.interfaces.security asks a book (OrchTokens) who a token belongs to; udl.core.service asks
refused() before it runs an operation an agent called. Nothing else compares a role.
"""

# System Imports
import contextlib
import logging
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING, Final

# Package Imports
from pydantic import BaseModel, ConfigDict, SecretStr

# Local Imports
from udl.clock import Clock, SystemClock
from udl.db.records import Name
from udl.errors import DoesNotExist, NotAllowed, PluginError, UDLError
from udl.interfaces.security import AGENT_PREFIX, Principal, PrincipalKind
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.agents import AgentRole, ManagerPowers
from udl.plugins.apps.orch.chat import chat_secret_ref
from udl.plugins.apps.orch.managers import switched_on
from udl.plugins.apps.orch.records import (
    AgentRecords,
    AgentToken,
    AgentTokenRecords,
    ChatRecords,
    ChatToken,
    ChatTokenRecords,
    hash_token,
)
from udl.plugins.secrets.base import SecretRef

if TYPE_CHECKING:
    from udl.core.service import UDLService

__all__ = [
    "AGENT_PREFIX",
    "ROLE_OPERATIONS",
    "IssueTokenRequest",
    "IssueTokenResponse",
    "OrchTokens",
    "allowed",
    "issue_token",
    "refused",
    "refused_for",
    "tokens_for",
]

_SECRET_PATH: Final = "apps/orch/agents"  # noqa: S105 - (a path, not a key)
"""Where an agent's token is kept in udl's secret store (under its name)."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


# ---------------------------------------------------------------------------------------------
# What a role may call
# ---------------------------------------------------------------------------------------------


_IMPLEMENTER: Final = frozenset(
    {
        "orch_my_task",
        "orch_get_task",
        "orch_get_feature",
        "orch_list_tasks",
        "orch_add_note",
        "orch_add_link",
        "orch_ask",
        "orch_block",
        "orch_reply",
        "orch_list_questions",
        "orch_get_question",
        "orch_push",
        "orch_pr_status",
        "orch_ready",
        # (A merge is a person's, or — where the project's `merge` is `agent` — the task's own
        # assignee's: the operation refuses anyone else (28.3).)
        "orch_merge",
        # (A read-only operation of another kind: an implementer reads the PR it pushed.)
        "github_get_pull",
    }
)
"""What an implementer may call: its own work, and nothing that changes udl's setup."""

_MANAGER: Final = _IMPLEMENTER | frozenset(
    {
        # (A manager passes up what it cannot answer, and closes what nobody answered: 28.1.
        # Answering is 27.8's `answer` power, and the subtree check is the operation's.)
        "orch_escalate",
        "orch_close_question",
    }
)
"""What a manager may call: an implementer's work, and what it does for its reports (27.8 adds
the powers it switches on)."""

_CHAT_REFUSED: Final = frozenset(
    {
        # (A chat is the person's own work, done through udl: it may do what they may, but not
        # hand out tokens, change auto merge, or write the secret store. Every admin operation
        # is refused with them — a chat has no business changing accounts, agents or settings —
        # and the two named here are the ones a chat might otherwise reach by not being admin.)
        "orch_issue_token",
        "orch_set_auto_merge",
        "set_onepassword_token",
    }
)
"""What a chat's token is refused, whatever else it may do (27.11)."""

_CHAT_ALWAYS: Final = frozenset({"orch_chat_ask_permission"})
"""The one chat operation a chat's token may call: its harness's own permission prompt (27.11).
Every other `orch_chat_*` is the person's, so a chat cannot start, read, stop or answer one —
its own included: it cannot answer its own prompts."""

ROLE_OPERATIONS: Final[dict[AgentRole, frozenset[str]]] = {
    AgentRole.IMPLEMENTER: _IMPLEMENTER,
    # (A manager may do an implementer's work, and answer its reports' questions; 27.8 adds the
    # powers it switches on, each checked against its own subtree.)
    AgentRole.MANAGER: _MANAGER,
    # (A reviewer may call every orch_* operation that is not admin: see allowed().)
    AgentRole.REVIEWER: frozenset(),
}
"""What each role may call: the only place a role is compared."""


def allowed(
    role: AgentRole, name: str, *, admin: bool, powers: ManagerPowers | None = None
) -> bool:
    """Whether an agent with `role` may call the operation called `name` (its hints' admin).

    A manager's `powers` switch on more (27.8): `assign`, `answer`, `review` and `plan`, each over
    its own subtree (which the operation itself checks)."""
    if name in ROLE_OPERATIONS.get(role, frozenset()):
        return True
    if role is AgentRole.MANAGER and name in switched_on(powers):
        return True
    return role is AgentRole.REVIEWER and name.startswith("orch_") and not admin


def refused(
    principal: Principal, name: str, *, admin: bool, powers: ManagerPowers | None = None
) -> None:
    """Raise NotAllowed when `principal` (an agent) may not call `name`."""
    role = AgentRole(principal.role) if principal.role in tuple(AgentRole) else None
    if role is not None and allowed(role, name, admin=admin, powers=powers):
        return
    named = principal.role or "agent"
    hint = "An agent's role decides which operations it may call (udl's role table, 27.2)."
    if role is AgentRole.MANAGER:
        hint = (
            "A manager's role and its powers decide which operations it may call (udl's role "
            "table, 27.2, and 27.8's powers)."
        )
    raise NotAllowed(f"{name} is not one of the operations a {named} agent may call", hint=hint)


async def refused_for(service: UDLService, principal: Principal, name: str, *, admin: bool) -> None:
    """`refused()`, with the powers the agent's record gives it (27.8).

    It reads the agent's record on every agent call, before the operation runs: one read per
    request is the price of a power that changes without a restart. If agents ever get chatty
    enough for it to show, cache the record per request (the service's principal is the key)."""
    if principal.is_chat:
        if name in _CHAT_ALWAYS:
            return
        if admin or name in _CHAT_REFUSED or name.startswith("orch_chat_"):
            raise NotAllowed(
                f"{name} is not one of the operations a chat's token may call",
                hint="A chat's token does the person's work through udl; it cannot hand out "
                "tokens, change auto merge, write the secret store, change an account or an "
                "agent, or manage a chat (27.11).",
            )
        return
    powers: ManagerPowers | None = None
    if principal.name:
        found = await AgentRecords(AppRecords(service.db)).agent(principal.name)
        powers = found.manages if found is not None else None
    refused(principal, name, admin=admin, powers=powers)


# ---------------------------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------------------------


class IssueTokenRequest(_Model):
    """Ask for an agent's token (a new one: the old one stops working at once)."""

    agent: Name
    """The agent to issue it for."""


class IssueTokenResponse(_Model):
    """An agent's new token. It is given here, once, and kept in udl's secret store."""

    agent: Name
    """The agent it belongs to."""
    token: str
    """The token itself (what the agent sends)."""


log = logging.getLogger(__name__)

CHAT_IDLE: Final = timedelta(hours=24)
"""How long a chat's token keeps working with its chat untouched, unless the settings say
otherwise (`[apps.orch] chat_idle`, 27.11)."""


class OrchTokens:
    """The book of agent tokens: what udl.interfaces.security asks when a request comes with one.
    It hashes the token and looks the hash up in orch's AgentToken records."""

    def __init__(
        self,
        records: AppRecords,
        *,
        service: UDLService | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._records = records
        self._agents = AgentRecords(records)
        self._tokens = AgentTokenRecords(records)
        self._chats = ChatTokenRecords(records)
        self._chat_records = ChatRecords(records)
        self._service = service
        self._clock: Clock = clock or SystemClock()
        self._idle: timedelta | None = None
        """How long a chat's token outlives its chat being untouched (worked out once: the
        settings do not change while a server runs without being asked to)."""

    async def _chat_idle(self) -> timedelta:
        """How long a chat's token keeps working with its chat untouched: `chat_idle` hours from
        the settings, or 24."""
        if self._idle is None:
            hours = CHAT_IDLE.total_seconds() / 3600
            if self._service is not None:
                try:
                    app = await self._service.apps.get("orch")
                except UDLError:
                    app = None  # (a broken app's settings must not stop authentication)
                found = getattr(app, "settings", None)
                given = getattr(found, "chat_idle", None)
                if isinstance(given, int) and given > 0:
                    hours = given
            self._idle = timedelta(hours=hours)
        return self._idle

    async def principal_for(self, token: str) -> Principal | None:
        """Who the token belongs to, or None when no token has that hash (it was rotated, or udl
        never issued it). Using one notes when it was last used.

        A chat's token (27.11) is its own chat and nothing else: it is the person the chat is,
        and the chat it is for."""
        digest = hash_token(token)
        found = await self._tokens.by_hash(digest)
        if found is None:
            kept = await self._chats.by_hash(digest)
            if kept is None:
                return None
            chat = await self._chat_records.by_id(kept.chat)
            if chat is None:
                await self._records.delete(ChatToken, kept.id)  # (the chat went: so does it)
                return None
            if self._clock.now() - chat.updated_at > await self._chat_idle():
                # (27.11: a token outlives its chat being untouched by `chat_idle` hours. It is
                # taken away as it is refused, with what carried it — the secret, and the file
                # its harness reads it from — so a chat someone comes back to starts again.)
                await self._records.delete(ChatToken, kept.id)
                if self._service is not None:
                    with contextlib.suppress(UDLError, PluginError):
                        await self._service.secrets.delete(chat_secret_ref(chat.id))
                log.info(
                    "refused the chat %s's token: untouched since %s", chat.id, chat.updated_at
                )
                return None
            log.info("a call with the chat %s's token, for %s", chat.id, chat.owner)
            return Principal(kind=PrincipalKind.CHAT, name=kept.owner, chat=kept.chat)
        agent = await self._agents.agent(found.agent)
        if agent is None:
            return None  # (the agent is gone: its token goes with it)
        await self._tokens.touch(found)
        return Principal(kind=PrincipalKind.AGENT, name=agent.name, role=str(agent.role))

    async def issue(self, agent: str, token: str) -> AgentToken:
        """Keep `token`'s hash for `agent`, replacing any it had (rotating)."""
        return await self._tokens.issue(agent, token)


def tokens_for(service: UDLService) -> OrchTokens:
    """The book of the agents on `service`'s database (and of the chats', 27.11: its `chat_idle`
    comes from the app's settings)."""
    return OrchTokens(AppRecords(service.db), service=service)


async def issue_token(service: UDLService, agent: str) -> str:
    """Issue a new token for the agent called `agent`, rotating the one it had: the token is
    kept in udl's secret store, and given out here, once.

    Raises DoesNotExist for an agent that is not there, and NotAllowed when the server has no
    `api.api_key`: a token only means something on a server that needs a key, since without one
    every request is the owner's — an agent could drop its token and be the owner, with every
    operation and no limit."""
    if service.settings.api.api_key is None:
        raise NotAllowed(
            f"This udl has no api.api_key, so a {AGENT_PREFIX}… token would limit nothing",
            hint="Set api.api_key in the server's config (and give it to the agents' clients): "
            "without it every request is the owner's, so an agent could drop its token and be "
            "the owner.",
        )
    agents = AgentRecords(AppRecords(service.db))
    found = await agents.agent(agent)
    if found is None:
        raise DoesNotExist(f"There is no agent called {agent!r}")
    token = f"{AGENT_PREFIX}{secrets.token_urlsafe(32)}"
    await agents.tokens.issue(found.name, token)
    await service.secrets.set(secret_ref(found.name), SecretStr(token))
    return token


def secret_ref(agent: str) -> SecretRef:
    """Where an agent's token is kept in the secret store."""
    return SecretRef.of(_SECRET_PATH, agent, "token")
