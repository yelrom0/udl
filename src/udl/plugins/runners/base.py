"""
-----------
- base.py -
-----------

AgentRunner: the interface every runner plugin implements (the udl.runners kind), and the models
it works with.

A runner drives a harness — Claude Code, OpenCode, or any command — on a host, through that host's
HostShell, so udl never learns whether the program is here or on thevault: a session is started
once (start), a turn is sent (send) and its events are read as they come, and the session is
stopped (stop). health, models, install and login are the harness's own machine and account,
which 25.11 puts on the API and the CLI.

A turn is a stream of AgentEvents, ending with exactly one TurnEnded that says how it ended, and a
runner that cannot finish says so in the stream rather than raising into whatever is reading it:
a RunnerLimited or a RunnerFailed comes first, then the TurnEnded that ends the turn
(TurnReason.LIMITED or TurnReason.ERROR), which is always the last event a turn gives. Nothing
follows a TurnEnded.

tests/contract/test_runner_contract.py is the behaviour every runner must have.
"""

# System Imports
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar, Literal

# Package Imports
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, NonNegativeInt, SecretStr

# Local Imports
from udl.clock import Clock
from udl.errors import PluginError
from udl.hosts.shell import HostShell, Interaction
from udl.plugins.requirements import Requirement
from udl.plugins.ui.base import UIBundle

__all__ = [
    "AgentEvent",
    "AgentRunner",
    "AnAgentRunner",
    "McpEndpoint",
    "RunnerFailed",
    "RunnerHealth",
    "RunnerLimited",
    "RunnerModel",
    "RunnerOptions",
    "SessionRef",
    "SessionSpec",
    "TextDelta",
    "ToolFinished",
    "ToolStarted",
    "TurnEnded",
    "TurnReason",
    "Usage",
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class RunnerOptions(_Model):
    """A runner's options (each runner's model extends it): what `[runners.<name>]` sets in the
    config, then what set_plugin_settings set (25.11 puts a runner's settings there)."""


class McpEndpoint(_Model):
    """udl's MCP server, as a harness is pointed at it: where it is, and the token it needs."""

    url: str
    """Its URL."""
    token: SecretStr | None = None
    """The token it answers to (never in a file another user can read, and never logged)."""


class SessionSpec(_Model):
    """What a session is started with: where it works, what it runs, and what it may not do."""

    cwd: str
    """The folder it works in (a worktree, for an agent that changes files)."""
    model: str | None = None
    """The model to run (None: the harness's own default)."""
    instructions: str | None = None
    """The system text it is given (the agent's brief)."""
    mcp: McpEndpoint | None = None
    """udl's MCP server, when the session is given one."""
    deny: list[str] = Field(default_factory=list)
    """Commands the harness must refuse (`git push`, `gh pr merge`, …)."""
    env: dict[str, str] = Field(default_factory=dict)
    """Its environment, over the host's own."""
    secret_env: dict[str, SecretStr] = Field(default_factory=dict)
    """Secrets for its environment: never in an argv."""


class SessionRef(_Model):
    """A session that is running: the harness's own name for it, and where it runs."""

    runner: str
    """The runner that started it."""
    id: str
    """The harness's session id (what it resumes with)."""
    cwd: str
    """The folder it works in."""
    host: str
    """The host it runs on."""


class TurnReason(StrEnum):
    """How a turn ended."""

    DONE = "done"  # the model finished its answer
    STOPPED = "stopped"  # the session was stopped, or udl asked it to end
    ERROR = "error"  # the harness failed (see RunnerFailed for why)
    LIMITED = "limited"  # the harness's account is out of quota (see RunnerLimited for until when)


class AgentEvent(_Model):
    """One thing that happened in a turn (each kind is a subclass, below, and says which it is in
    its `kind`)."""


class TextDelta(AgentEvent):
    """What the model said, as it says it."""

    kind: Literal["text_delta"] = "text_delta"
    text: str
    """The text."""


class ToolStarted(AgentEvent):
    """The model started a tool (a file it edits, a command it runs)."""

    kind: Literal["tool_started"] = "tool_started"
    name: str
    """The tool's name."""
    summary: str
    """What it is doing, in a line (never a secret)."""


class ToolFinished(AgentEvent):
    """A tool it started ended."""

    kind: Literal["tool_finished"] = "tool_finished"
    name: str
    """The tool's name."""
    ok: bool
    """Whether it worked."""


class Usage(AgentEvent):
    """What a turn cost, as the harness reports it."""

    kind: Literal["usage"] = "usage"
    input_tokens: NonNegativeInt = 0
    """Tokens it was given."""
    output_tokens: NonNegativeInt = 0
    """Tokens it wrote."""
    cost: Decimal | None = None
    """What it cost, when the harness says (a subscription's turns cost nothing to count)."""


class TurnEnded(AgentEvent):
    """A turn ended."""

    kind: Literal["turn_ended"] = "turn_ended"
    reason: TurnReason
    """How it ended."""
    text: str = ""
    """Its answer, or why it ended (never a secret)."""


class RunnerLimited(AgentEvent):
    """The harness's account is out of quota: nothing runs again until `until` (when the harness
    says; None: it did not say). The turn's TurnEnded(reason=LIMITED) follows it, last."""

    kind: Literal["runner_limited"] = "runner_limited"
    until: AwareDatetime | None = None
    """When its quota is back, if it said."""
    message: str
    """What it said."""


class RunnerFailed(AgentEvent):
    """The harness failed: its message is what it said (or what udl could see), never a secret.
    The turn's TurnEnded(reason=ERROR) follows it, last."""

    kind: Literal["runner_failed"] = "runner_failed"
    message: str
    """What went wrong."""


class RunnerHealth(_Model):
    """Whether a harness is there, and signed in: what `udl runners` says about it (25.11)."""

    installed: bool
    """Whether its program is on the host."""
    signed_in: bool = False
    """Whether its account is signed in."""
    version: str | None = None
    """Its version, when it says."""
    account: str | None = None
    """The account it is signed in as (shown, never logged)."""
    detail: str | None = None
    """Anything else worth showing (a limit, a missing sign-in's hint)."""


class RunnerModel(_Model):
    """A model a runner can run on this host."""

    id: str
    """What it is called in a SessionSpec (what the harness takes)."""
    name: str
    """What a person reads."""
    description: str | None = None
    """What it is for, when the runner says."""


class AgentRunner[O: RunnerOptions](ABC):
    """A harness udl drives, on one host (the kind's interface)."""

    name: ClassVar[str]
    """What it is called (`claude-code`, `opencode`, `command`): the `[runners.<name>]` table's."""
    options_model: ClassVar[type[RunnerOptions]]
    """Its options' model (its own extends RunnerOptions)."""
    requires: ClassVar[tuple[Requirement, ...]] = ()
    """What it needs installed (a program, a module), for `udl plugins list`."""
    ui: ClassVar[UIBundle | None] = None  # its UI plugin, if it has one (18.7)

    def __init__(self, options: O, *, shell: HostShell, clock: Clock) -> None:
        self._options = options
        self._shell = shell
        self._clock = clock

    @property
    def options(self) -> O:
        """The options it was made with."""
        return self._options

    @property
    def shell(self) -> HostShell:
        """The host it acts on (this machine, or another over SSH)."""
        return self._shell

    @property
    def clock(self) -> Clock:
        """Its clock (what it waits with, and what a limit's time is read from)."""
        return self._clock

    @abstractmethod
    async def health(self) -> RunnerHealth:
        """Whether its harness is on the host, signed in, and which version."""

    @abstractmethod
    async def models(self) -> list[RunnerModel]:
        """What it can run on this host."""

    def install(self, version: str | None = None) -> AsyncIterator[str]:
        """Install or update it, giving its output as it comes (25.11). A runner that cannot
        install itself says so, rather than pretending it did."""
        return self._refuse(f"{self.name} cannot be installed by udl")

    async def login(self) -> Interaction:
        """Its own sign-in, relayed to the person (25.11). A runner that cannot sign in itself
        says so."""
        raise PluginError(f"{self.name} cannot sign in by itself")

    @abstractmethod
    async def start(self, spec: SessionSpec) -> SessionRef:
        """Start a session with `spec`, and give what it takes to send it turns."""

    @abstractmethod
    def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        """Send one turn: its events as they come, ending with a TurnEnded."""

    @abstractmethod
    async def stop(self, session: SessionRef) -> None:
        """Stop a session (it ends, and nothing it was doing goes on)."""

    async def _refuse(self, message: str) -> AsyncIterator[str]:
        """An async generator that raises `message` when it is first read."""
        raise PluginError(message)
        yield message  # pragma: no cover  (unreachable: it is what makes this a generator)


type AnAgentRunner = AgentRunner[Any]
"""A runner of any options, as the registry and the API hold it (not AgentRunner[RunnerOptions]:
O is invariant, so an AgentRunner[FakeRunnerOptions] is not one)."""
