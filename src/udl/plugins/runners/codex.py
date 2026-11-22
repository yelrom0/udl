"""
------------
- codex.py -
------------

Codex: the `codex` runner, driving OpenAI's Codex CLI on a host through that host's HostShell (so
a `codex` on thevault is driven exactly as the one here). It is the twin of `claude-code` (25.8):
a turn is one process, and its JSON lines become AgentEvents.

A turn is one process:
- the first: `codex exec --json --model <model> --cd <worktree> --sandbox workspace-write -c
  approval_policy="never" -c sandbox_workspace_write.network_access=true -`, with the prompt on
  stdin and udl's instructions (the fixed rules, the project's and the agent's brief) opening it
  — 0.171 has no config key for a base instruction file (`model_instructions_file` is for the
  model's own prompt), so the instructions are the first thing in the first prompt;
- a later one: `codex exec resume <thread id> --json -`, the provider and the model given with
  `-c` (resume takes no `--model` and no `--profile`).

The session id is the one `thread.started` gives; udl's own id for the session is a UUID made at
start(), since the ref is given out before the first turn runs, and the thread id is kept beside
it (CodexSessions) so a resume — in this runner or one made later — uses it.

`--sandbox workspace-write` keeps writes inside the worktree, and the deny list (`git push`,
`gh pr merge`) is Codex's own: 25.11's setup() writes `~/.codex/rules/udl.rules`, and health()
reports it as `guards`. The MCP server is given per turn with `-c` keys, its token in the
process's environment (`bearer_token_env_var`), never in an argv.

`health()` reads `codex --version` and `codex login status`. udl never opens Codex's own
`auth.json` (O-C3's rule, for OpenAI's plan too): the account is what the CLI says.
"""

# System Imports
import asyncio
import contextlib
import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import ClassVar, Final
from uuid import uuid4

# Package Imports
from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.clock import Clock
from udl.errors import PluginFailed, ToolUnavailable
from udl.hosts.shell import HostShell, Interaction
from udl.plugins.requirements import Requirement
from udl.plugins.runners.base import (
    AgentEvent,
    AgentRunner,
    RunnerFailed,
    RunnerHealth,
    RunnerLimited,
    RunnerModel,
    RunnerOptions,
    SessionRef,
    SessionSpec,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnReason,
    Usage,
    lines,
)

log = logging.getLogger(__name__)

__all__ = ["Codex", "CodexOptions", "CodexSessions"]

_VERSION: Final = re.compile(r"codex-cli\s+(\S+)")
"""What `codex --version` prints."""
_SIGNED_IN: Final = re.compile(r"logged in using (\w[\w ]*)", re.IGNORECASE)
"""What `codex login status` prints when it is signed in, and how."""
_LIMIT: Final = re.compile(
    r"(?:usage limit|rate limit|out of credit|quota)[^\n]*?\b(?:after|at|on|resets?)\s+"
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.IGNORECASE,
)
"""How a limit's message says when it lifts (an ISO time, or an epoch)."""
_EPOCH: Final = re.compile(r"(\d{10})")
"""The other way it says it: an epoch second."""
_METADATA: Final = "model metadata"
"""The warning a real turn's stream opens with (an `error` item that is only a warning)."""

_RULES: Final = ".codex/rules/udl.rules"
"""Where 25.11's setup writes the deny list, under the user's home (Codex reads it itself)."""
_DEFAULT_DENY: Final = ("git push", "gh pr merge")
"""What udl's own rules refuse, when a session names nothing else."""
_MCP_TOKEN: Final = "UDL_MCP_TOKEN"  # noqa: S105 - (a variable's name, not a key)
"""The environment variable udl's MCP server's token is read from (never an argv)."""


@dataclass
class CodexSessions:
    """A codex runner's sessions, outside any runner object (a service may hold one)."""

    specs: MutableMapping[str, SessionSpec] = field(default_factory=dict[str, SessionSpec])
    """What each session was started with, by id."""
    sent: set[str] = field(default_factory=set[str])
    """The sessions that have had a turn (the next one resumes by thread id)."""
    threads: dict[str, str] = field(default_factory=dict[str, str])
    """The codex thread each session is, by udl's session id (what `thread.started` gave)."""


class CodexOptions(RunnerOptions):
    """The codex runner's options."""

    cli: list[str] = Field(default_factory=lambda: ["codex"])
    """The program and any arguments before the turn's own (a test points it at a fake)."""
    models: list[str] = Field(default_factory=lambda: ["gpt-5.1-codex", "gpt-5.1-codex-mini"])
    """What it can run: the CLI lists none, so they are named here (the config may replace them)."""
    provider: str | None = None
    """The model provider (`-c model_provider=…`, e.g. a profile's): the ChatGPT plan by default."""
    sandbox: str = "workspace-write"
    """`--sandbox`: what it may write (workspace-write keeps it inside the worktree)."""
    network: bool = True
    """Whether the sandbox may reach the network (a tool that fetches a page needs it)."""
    approval_policy: str = "never"
    """`-c approval_policy=…`: udl never sits at a prompt to approve a command."""


class _Item(BaseModel):
    """One item of a turn: a message, a command, a tool call, a file change, or an error."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    type: str | None = None
    text: str | None = None
    message: str | None = None
    server: str | None = None
    tool: str | None = None
    command: str | None = None
    status: str | None = None
    aggregated_output: str | None = None


class _Usage(BaseModel):
    """What a turn used (its input, cached input and output tokens)."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0


class _Line(BaseModel):
    """One line of `codex exec --json`, as far as udl reads it."""

    model_config = ConfigDict(extra="ignore")

    type: str
    thread_id: str | None = None
    item: _Item | None = None
    message: str | None = None
    error: _Failure | None = None
    usage: _Usage | None = None


class _Failure(BaseModel):
    """A failed turn's error (`turn.failed` carries `{"message": …}`)."""

    model_config = ConfigDict(extra="ignore")

    message: str | None = None


class Codex(AgentRunner[CodexOptions]):
    """OpenAI's Codex CLI as a runner."""

    name: ClassVar[str] = "codex"
    options_model: ClassVar[type[RunnerOptions]] = CodexOptions
    requires: ClassVar[tuple[Requirement, ...]] = (Requirement.binary("codex"),)

    def __init__(
        self,
        options: CodexOptions,
        *,
        shell: HostShell,
        clock: Clock,
        sessions: CodexSessions | MutableMapping[str, SessionSpec] | None = None,
    ) -> None:
        super().__init__(options, shell=shell, clock=clock)
        # (A bare mapping is the specs alone: give it a `CodexSessions` for the thread ids and the
        # first turns to outlive the object.)
        self._sessions = (
            sessions
            if isinstance(sessions, CodexSessions)
            else CodexSessions(specs=sessions if sessions is not None else {})
        )
        self._stopped: set[str] = set()
        self._pulls: dict[str, asyncio.Task[None]] = {}

    # -- what it is ----------------------------------------------------------------------------

    async def health(self) -> RunnerHealth:
        """`codex --version` and `codex login status`: whether it is here and signed in, and
        whether udl's rules are on the host."""
        try:
            version = await self.shell.run([*self.options.cli, "--version"])
            # (Signed out, it says so on stderr and exits 1: read what it said either way,
            # rather than taking a non-zero exit for a harness that is not there.)
            status = await self.shell.run([*self.options.cli, "login", "status"], check=False)
        except (ToolUnavailable, PluginFailed) as failure:
            return RunnerHealth(installed=False, detail=str(failure))
        guards = await self._guards()
        return _health(version.stdout, f"{status.stdout}{status.stderr}", guards=guards)

    async def _guards(self) -> bool:
        """Whether udl's rules file is on the host (what 25.11's setup writes)."""
        try:
            await self.shell.run(["sh", "-c", 'test -f "$HOME/$1" || exit 1', "sh", _RULES])
        except ToolUnavailable, PluginFailed, FileNotFoundError:
            return False
        return True

    async def models(self) -> list[RunnerModel]:
        """What it can run: the options name them (the CLI lists none)."""
        return [RunnerModel(id=model, name=model) for model in self.options.models]

    def install(self, version: str | None = None) -> AsyncIterator[str]:
        """Install or update it: the official npm package (`npm install -g @openai/codex@…`)."""
        package = "@openai/codex" if version is None else f"@openai/codex@{version}"
        return lines(self.shell.stream(["npm", "install", "-g", package]))

    @property
    def sign_in_command(self) -> tuple[str, ...]:
        """`codex login --device-auth`: what a person runs on the host to sign in."""
        return (*self.options.cli, "login", "--device-auth")

    async def login(self, provider: str | None = None) -> Interaction:
        """`codex login --device-auth`, relayed: it prints a link and a code, and waits."""
        return await self.shell.interact([*self.options.cli, "login", "--device-auth"])

    async def logout(self) -> None:
        """`codex logout`."""
        await self.shell.run([*self.options.cli, "logout"])

    async def setup(
        self,
        roles: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> list[str]:
        """Write udl's deny list where Codex reads it (`~/.codex/rules/udl.rules`), and give the
        file it wrote. `roles` and `env` are the other harnesses': Codex has no per-role file, and
        a provider's key is its own config's (`env_key`)."""
        del roles, env  # (Codex's rules are the same for every role, and its key is its own)
        rules = "".join(_rule(pattern) for pattern in _DEFAULT_DENY)
        said = f"# udl's rules: never a push or a merge from an agent.\n{rules}"
        # (Written through the shell with the path relative to the home: `$HOME` is expanded
        # there, so udl never has to know where the home is on that host.)
        await self.shell.run(
            [
                "sh",
                "-c",
                'target="$HOME/$1"; umask 077; mkdir -p "$(dirname "$target")" && cat > "$target"',
                "sh",
                _RULES,
            ],
            stdin=said.encode(),
        )
        return [f"~/{_RULES}"]

    # -- a session -----------------------------------------------------------------------------

    async def start(self, spec: SessionSpec) -> SessionRef:
        """A session udl can send turns to (Codex's own thread is named by the first turn)."""
        session = SessionRef(
            runner=self.name, id=str(uuid4()), cwd=spec.cwd, host=self.shell.host.name
        )
        self._sessions.specs[session.id] = spec
        return session

    async def stop(self, session: SessionRef) -> None:
        """Stop a session: nothing it was doing goes on."""
        self._stopped.add(session.id)
        if (pull := self._pulls.pop(session.id, None)) is not None:
            pull.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pull

    async def send(self, session: SessionRef, prompt: str) -> AsyncGenerator[AgentEvent]:
        """One turn: the process's JSON lines as they come, ending with a TurnEnded."""
        spec = self._sessions.specs.get(session.id)
        if spec is None:
            yield TurnEnded(reason=TurnReason.ERROR, text=f"No session {session.id!r}.")
            return
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        first = session.id not in self._sessions.sent
        ended: TurnEnded | None = None
        plain = ""
        """The last line the CLI printed that is not JSON (its own failures read that way)."""
        # (The shell yields fixed-size chunks, not lines: a line longer than one chunk — a long
        # answer, a command's output — arrives in pieces, so the parts are kept until the newline
        # that ends the line comes. Split, neither half reads as JSON and both would be dropped.)
        buffer = ""
        try:
            async for chunk in self._chunks(session, prompt, spec, first=first):
                buffer += chunk.decode(errors="replace")
                lines, _, rest = buffer.rpartition("\n")
                buffer = rest
                plain = self._plain(lines) or plain  # (the last plain line, if it had one)
                for event in self._lines(session, lines):
                    if isinstance(event, TurnEnded):
                        ended = event
                    else:
                        yield event
            for event in self._lines(session, buffer):
                if isinstance(event, TurnEnded):
                    ended = event
                else:
                    yield event
            plain = self._plain(buffer) or plain
        except (ToolUnavailable, PluginFailed) as failure:
            ended = TurnEnded(reason=TurnReason.ERROR, text=str(failure))
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        yield ended or TurnEnded(reason=TurnReason.ERROR, text=_unsaid(plain))

    def _lines(self, session: SessionRef, said: str) -> list[AgentEvent]:
        """What a run of whole lines is: the events they are (the last plain line is kept by
        `_plain`, since only it can say why a turn that never ended failed)."""
        events: list[AgentEvent] = []
        for line in said.splitlines():
            if not line.strip():
                continue
            if not line.lstrip().startswith("{"):
                continue  # (a plain line: the CLI's own failures read that way, kept by _plain)
            events.extend(self._events(session, line))
        return events

    @staticmethod
    def _plain(said: str) -> str:
        """The last line of `said` that is not JSON (the CLI's own failures are plain lines)."""
        plain = ""
        for line in said.splitlines():
            if line.strip() and not line.lstrip().startswith("{"):
                plain = line.strip()
        return plain

    async def _chunks(
        self, session: SessionRef, prompt: str, spec: SessionSpec, *, first: bool
    ) -> AsyncIterator[bytes]:
        """What the turn's process prints, as it prints it.

        (Pulled by a task of its own, so stop() can cancel it: closing a generator that is being
        read cannot, and the shell closes the process when its generator ends.)"""
        turn = self.shell.stream(
            self._argv(session, spec, first=first),
            cwd=session.cwd,
            env=spec.env,
            secret_env={name: value.get_secret_value() for name, value in spec.secret_env.items()},
            stdin=self._prompt(spec, prompt, first=first).encode(),
        )
        queue: asyncio.Queue[bytes | Exception | None] = asyncio.Queue()

        async def pull() -> None:
            try:
                async for chunk in turn:
                    # (Marked once the process is really started — a real shell starts it on the
                    # first `__anext__`, inside here: a turn closed early has still created the
                    # CLI's session, so the next one resumes it, but one that never started
                    # created nothing, so the next turn starts it.)
                    self._sessions.sent.add(session.id)
                    await queue.put(chunk)
                self._sessions.sent.add(session.id)  # (it ended cleanly: the process did run)
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 - (a process that could not start, or broke)
                await queue.put(failure)
            finally:
                await queue.put(None)

        task = asyncio.create_task(pull())
        self._pulls[session.id] = task
        try:
            while (item := await queue.get()) is not None:
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self._pulls.pop(session.id, None)
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def _argv(self, session: SessionRef, spec: SessionSpec, *, first: bool) -> list[str]:
        """The one process a turn is (never a shell: each argument is one argument)."""
        cli = [*self.options.cli]
        if first:
            argv = [*cli, "exec", "--json"]
            argv += ["--model", spec.model] if spec.model else []
            argv += ["--cd", spec.cwd]
            argv += ["--sandbox", self.options.sandbox]
            argv += [
                "-c",
                f"approval_policy={json.dumps(self.options.approval_policy)}",
                "-c",
                f"sandbox_workspace_write.network_access={str(self.options.network).lower()}",
            ]
        else:
            thread = self._thread(session.id)
            if thread is None:
                # (Nothing started this session's thread — a store that forgot it, or a ref made
                # elsewhere: say so, rather than resume a thread called by udl's own id.)
                raise PluginFailed(
                    f"No codex thread is known for session {session.id!r}: it has had no turn here"
                )
            argv = [*cli, "exec", "resume", thread, "--json"]
            # (0.171's resume has no --sandbox/--cd, so the same policy is given as -c keys:
            # every turn runs under it, not only the first.)
            argv += [
                "-c",
                f"sandbox_mode={json.dumps(self.options.sandbox)}",
                "-c",
                f"approval_policy={json.dumps(self.options.approval_policy)}",
                "-c",
                f"sandbox_workspace_write.network_access={str(self.options.network).lower()}",
            ]
        if self.options.provider is not None:
            argv += ["-c", f"model_provider={json.dumps(self.options.provider)}"]
        if not first and spec.model:
            argv += ["-c", f"model={json.dumps(spec.model)}"]
        argv += self._mcp(spec)
        return [*argv, "-"]

    def _mcp(self, spec: SessionSpec) -> list[str]:
        """udl's MCP server, as `-c` keys: its URL, and the environment variable its token is
        read from (the token itself is in the process's environment, never in this argv)."""
        if spec.mcp is None:
            return []
        return [
            "-c",
            f"mcp_servers.udl.url={json.dumps(spec.mcp.url)}",
            "-c",
            f"mcp_servers.udl.bearer_token_env_var={_MCP_TOKEN}",
        ]

    def _prompt(self, spec: SessionSpec, prompt: str, *, first: bool) -> str:
        """What the turn is given on stdin: the instructions first, then the prompt."""
        if not first or not spec.instructions:
            return prompt
        return f"{spec.instructions}\n\n{prompt}"

    def _thread(self, session: str) -> str | None:
        """The codex thread a session is (None until the first turn's `thread.started` names it)."""
        return self._sessions.threads.get(session)

    def _events(self, session: SessionRef, line: str) -> list[AgentEvent]:
        """What one line of the stream is (nothing, when udl does not know it)."""
        try:
            said = _Line.model_validate_json(line)
        except ValueError:
            log.debug("codex said a line udl cannot read: %r", line[:200])
            return []
        if said.type == "thread.started" and said.thread_id:
            self._sessions.threads[session.id] = said.thread_id
            return []
        if said.type == "item.started" and said.item is not None:
            return _started(said.item)
        if said.type == "item.completed" and said.item is not None:
            return self._completed(said.item)
        if said.type == "turn.completed":
            return _ended(said.usage, TurnReason.DONE)
        if said.type == "turn.failed":
            said_error = said.error
            return self._failed((said_error.message if said_error else said.message) or "failed")
        if said.type == "error":
            return self._failed(said.message or "codex failed")
        return []

    def _completed(self, item: _Item) -> list[AgentEvent]:
        """An item that ended: what the model said, the tool that ended, or a warning."""
        if item.type == "agent_message":
            return [TextDelta(text=item.text or "")]
        if item.type == "error":
            if _METADATA in (item.message or "").lower():
                return []  # (a warning about the model's metadata, not a failed turn)
            return self._failed(item.message or "codex reported an error")
        if item.type in _TOOLS:
            return [
                ToolFinished(
                    name=_name(item),
                    ok=_ok(item),
                )
            ]
        return []

    def _failed(self, message: str) -> list[AgentEvent]:
        """A failed turn: a limit when it says it is one (and when it lifts), else a failure."""
        if _is_limited(message):
            return [
                RunnerLimited(until=_reset(message), message=message),
                TurnEnded(reason=TurnReason.LIMITED, text=message),
            ]
        return [
            RunnerFailed(message=message),
            TurnEnded(reason=TurnReason.ERROR, text=message),
        ]


_TOOLS: Final = frozenset({"command_execution", "mcp_tool_call", "file_change"})
"""The item types that are a tool udl can show starting and ending."""


def _rule(pattern: str) -> str:
    """One rule as Codex's rules file has it: a command's words, refused.

    Codex reads Starlark here (an execpolicy rule), not TOML: `prefix_rule(pattern=[…],
    decision="forbidden")`."""
    words = ", ".join(json.dumps(word) for word in pattern.split())
    return f'prefix_rule(pattern=[{words}], decision="forbidden")\n'


def _started(item: _Item) -> list[AgentEvent]:
    """A tool that started, as the item says it."""
    if item.type not in _TOOLS:
        return []
    return [ToolStarted(name=_name(item), summary=_summary(item))]


def _name(item: _Item) -> str:
    """An item's tool's name: a command is a shell, an MCP call names its server and tool."""
    if item.type == "command_execution":
        return "shell"
    if item.type == "mcp_tool_call":
        return f"{item.server or 'mcp'}/{item.tool}" if item.tool else (item.server or "mcp")
    return item.type or "tool"


def _ok(item: _Item) -> bool:
    """Whether an item that ended worked."""
    return (item.status or "completed") in ("completed", "success", "ok")


def _summary(item: _Item) -> str:
    """What a tool is doing, in a line (never a secret): the command it runs, or its output."""
    if item.command:
        return item.command[:200]
    return (item.aggregated_output or item.message or "")[:200]


def _ended(usage: _Usage | None, reason: TurnReason) -> list[AgentEvent]:
    """The turn's end: what it used, and how it ended."""
    events: list[AgentEvent] = []
    if usage is not None:
        events.append(
            Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                # (A ChatGPT plan's turns cost nothing to count; 25.13 prices an API key.)
                cost=None,
            )
        )
    events.append(TurnEnded(reason=reason))
    return events


def _unsaid(plain: str) -> str:
    """Why a turn that never said how it ended failed: the last line it printed, when it printed
    one (the CLI's own errors are plain lines, not JSON)."""
    return plain or "The turn ended without saying how."


def _is_limited(message: str) -> bool:
    """Whether a failure says the account is out of quota."""
    lowered = message.lower()
    return (
        "usage limit" in lowered
        or "rate limit" in lowered
        or "out of credit" in lowered
        or "quota" in lowered
    )


def _reset(message: str) -> datetime | None:
    """When a limit's message says the quota is back, if it says."""
    if (found := _LIMIT.search(message)) is not None:
        said = found.group(1).replace(" ", "T").replace("Z", "+00:00")
        try:
            reset = datetime.fromisoformat(said)
        except ValueError:
            reset = None
        if reset is not None:
            return reset if reset.tzinfo is not None else reset.replace(tzinfo=UTC)
    if _is_limited(message) and (epoch := _EPOCH.search(message)) is not None:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=UTC)
    return None


def _health(version: str, status: str, *, guards: bool) -> RunnerHealth:
    """What `--version` and `login status` say about the harness."""
    found = _VERSION.search(version)
    if found is None:
        return RunnerHealth(
            installed=False,
            detail=version.strip() or "codex says no version.",
            guards=guards,
        )
    signed = _SIGNED_IN.search(status)
    said = _shown(status.strip())
    if signed is None:
        said = ""  # (signed out: nothing to show, and a key must never be shown)
    return RunnerHealth(
        installed=True,
        version=found.group(1),
        signed_in=signed is not None,
        plan=_plan(signed.group(1) if signed else ""),
        account=None,  # (the CLI says how it is signed in, not who: udl never opens auth.json)
        detail=said or None,
        guards=guards,
    )


def _shown(said: str) -> str:
    """`said` with anything that could be a credential left out: a parenthesised tail (where the
    CLI puts the key it uses) and a token-looking word."""
    without = re.sub(r"\([^)]*\)", "", said)
    return " ".join(word for word in without.split() if not _TOKEN.match(word))


_TOKEN: Final = re.compile(r"^(?:sk-|ghp_|github_pat_|udl_agent_)\S*$", re.IGNORECASE)
"""A word that is a credential, whatever the rest of the line says."""


def _plan(said: str) -> str | None:
    """How it is signed in, as a word or two: the CLI may name the key it uses, and a key is
    never shown ("an API key (sk-…)" is "an api key")."""
    head = re.split(r"[\s(]*[-(]", said.strip().lower(), maxsplit=1)[0].strip()
    return head or None
