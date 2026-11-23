"""
---------------
- security.py -
---------------

Who the servers (the API, and the MCP server it serves) answer: when api.api_key is set, only
requests that send it. (What they may ask for, such as an output dir under
api.allowed_output_roots, the operations check: udl.core.paths.)

Since 27.2 there is a second kind of caller: an agent with its own token (udl_agent_…, issued by
`udl orch issue-token`), which authenticate() resolves through a book of udl's agent tokens
(orch's OrchTokens) into a Principal. Who a request is is set in current_principal, beside
current_caller, and udl.core.service holds an agent to its role's operations.
"""

# System Imports
import hmac
from collections.abc import Mapping
from contextvars import ContextVar
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

# Local Imports
from pydantic import BaseModel, ConfigDict

from udl.config import Settings
from udl.errors import Unauthorized

API_KEY_HEADER = "X-API-Key"
"""The header a request sends udl's key in (an agent's token goes in it too, or in a bearer)."""


def api_key_of(headers: Mapping[str, str]) -> str | None:
    """The API key a request sends: `X-API-Key`, or `Authorization: Bearer …`.

    The bearer form is what a client that can only send one header uses (Codex's
    `bearer_token_env_var`); it is the same key — udl's own or an agent's — so the same rules
    hold, and the header wins when both are sent."""
    # (Both headers case-insensitively: a request's own headers are, and a plain mapping a test
    # passes may spell either as it is written.)
    given = _header(headers, API_KEY_HEADER)
    if given:
        return given
    scheme, _, token = _header(headers, "Authorization").partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def _header(headers: Mapping[str, str], name: str) -> str:
    """A header's value, whatever case it is spelled in (a request's headers are read this way)."""
    given = headers.get(name)
    if given is not None:
        return given
    return next((value for key, value in headers.items() if key.lower() == name.lower()), "")


AGENT_PREFIX: Final = "udl_agent_"
"""What an agent's token starts with, so a key of udl's own is never taken for one."""

CHAT_PREFIX: Final = "udl_chat_"
"""What a chat's own token starts with (27.11): it reaches one chat, and nothing else."""


class PrincipalKind(StrEnum):
    """What kind of caller udl is answering."""

    OWNER = "owner"  # (the person: the API key, or a request on localhost with no key set)
    AGENT = "agent"  # (an agent with its own token)
    CHAT = "chat"  # (a chat's own token: one chat, and nothing else)


class Principal(BaseModel):
    """Who an operation is being run for: the server's owner, or an agent.

    An owner may call anything; an agent only what its role allows (udl.plugins.apps.orch.tokens
    has the table), and its name is the agent it is."""

    model_config = ConfigDict(frozen=True)

    kind: PrincipalKind
    name: str
    role: str | None = None
    """The agent's role (implementer, manager or reviewer); an owner has none."""
    chat: UUID | None = None
    """The chat a chat's token is for (None for an owner and for an agent)."""

    @property
    def is_owner(self) -> bool:
        """Whether this is udl's owner (the person, with the server's API key)."""
        return self.kind is PrincipalKind.OWNER

    @property
    def is_agent(self) -> bool:
        """Whether this is an agent rather than udl's owner."""
        return self.kind is PrincipalKind.AGENT

    @property
    def is_chat(self) -> bool:
        """Whether this is a chat's own token rather than a person or an agent."""
        return self.kind is PrincipalKind.CHAT


OWNER: Final = Principal(kind=PrincipalKind.OWNER, name="owner")
"""The server's owner: whoever holds its API key, or anyone on localhost when it has none."""

current_principal: ContextVar[Principal | None] = ContextVar("udl_current_principal", default=None)
"""Who the operation that runs is for: set by each interface; None for udl's own Python."""


@runtime_checkable
class AgentTokens(Protocol):
    """Where a token is looked up: orch's AgentToken records, through its book."""

    async def principal_for(self, token: str) -> Principal | None:
        """The agent whose token this is, or None when no token of udl's is it."""
        ...


async def authenticate(
    settings: Settings, given: str | None, tokens: AgentTokens | None = None
) -> Principal:
    """Who a request is: the server's owner (its API key, or nothing when it has none), or an
    agent with its own token.

    Raises Unauthorized for a key that is not the server's, and for a token udl does not know
    (one it rotated, or never issued)."""
    if given is not None and given.startswith((AGENT_PREFIX, CHAT_PREFIX)):
        if tokens is None:
            raise Unauthorized(
                "That is a token udl issues, and this udl keeps none",
                hint="An owner issues one with `udl orch issue-token <agent>`.",
            )
        found = await tokens.principal_for(given)
        if found is None:
            # (Which kind it was, from its prefix: a person reading it can tell what to do.)
            if given.startswith(CHAT_PREFIX):
                raise Unauthorized(
                    "This udl does not know that chat token",
                    hint="Its chat may have been stopped or deleted: start it again.",
                )
            raise Unauthorized(
                "This udl does not know that agent token",
                hint="It may have been rotated: ask for a new one.",
            )
        return found
    expected = settings.api.api_key
    if expected is not None and (
        given is None
        or not hmac.compare_digest(given.encode(), expected.get_secret_value().encode())
    ):
        raise Unauthorized(
            f"This udl server needs its API key, in the {API_KEY_HEADER} header",
            hint="The key is api.api_key in the server's config.",
        )
    return OWNER
