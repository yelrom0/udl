"""
--------------
- opencode.py -
--------------

OpenCode: the `opencode` runner, driving OpenCode 2's HTTP API (O-C11) on a host through that
host's HostShell.

The service is one per user, on a loopback port that changes: `opencode service status` says its
URL (or `stopped`), and its password is in the host's `~/.config/opencode/service.json`. Both are
read through the shell on every connect, and the port is reached through the shell's `forward()` —
this machine's own for a local host, an SSH tunnel for another.

A turn is one prompt (`POST /api/session/{id}/prompt`, `delivery: queue`), then OpenCode's event
stream (`GET /api/event`, filtered to the session). That stream is the volatile one: the session's
own durable log replays nothing in 2.1.2 (a read of it says only `log.synced`), so the session's
`outcome` is the backstop — a stream that drops is opened again, and one that brings nothing three
times asks the session how its turn ended. Its events become AgentEvents;
`session.execution.succeeded`/`failed`/`interrupted` ends the turn, and
a failure that says the account is out of quota (a 429, or a wait it names) is a `RunnerLimited`
first and then the turn's `TurnEnded(limited)`.

A permission is not in the durable log, so while the turn runs the session's pending requests are
read (`GET /api/session/{id}/permission`) and each is refused: the session's ruleset already
denies what udl forbids, and anything else that asks is something the agent was not given.

udl's MCP server is added to the session's location (`PUT /api/experimental/mcp/udl`) with the
token as `{env:UDL_AGENT_TOKEN}`, and the token, the spec's environment and its secrets go to the
session's environment (`PUT /api/session/{id}/environment`): they are in no config file and no
argv.
"""

# System Imports
import asyncio
import hashlib
import json
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, MutableMapping, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar, Final
from urllib.parse import urlparse

# Package Imports
import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

# Local Imports
from udl.clock import Clock
from udl.errors import NotAllowed, PluginFailed, ToolUnavailable
from udl.hosts.shell import HostShell, Interaction
from udl.plugins.requirements import Requirement
from udl.plugins.runners.base import (
    AgentEvent,
    AgentRunner,
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

__all__ = ["OpenCode", "OpenCodeOptions", "OpenCodeSessions"]

_MCP: str = "udl"
"""The name udl's MCP server has in OpenCode."""
_TOKEN: str = "UDL_AGENT_TOKEN"  # noqa: S105 - (a variable's name, not a secret)
"""The environment variable the MCP config's header reads the token from."""
_SECONDS: float = 30.0
"""How long a call waits for the service (the log's own read is longer: a turn can be silent)."""
_EVENTS: Final = "/api/event"
"""The service's event stream: every session's events, as they happen."""
_LOG_SECONDS: float = 120.0
"""How long a read of the event stream waits for an event before it is a dropped connection."""
_LOG_TIMEOUT: Final = httpx.Timeout(
    connect=_SECONDS, read=_LOG_SECONDS, write=_SECONDS, pool=_SECONDS
)
"""How long one call may take before it is a failure."""
_ASK_EVERY: Final = timedelta(seconds=5)
"""How long another session's events may go by before this turn asks how it ended (a busy host's
stream never times out, so a drop is not the only moment to ask)."""
_DROPS: int = 30
"""How many reads of the event stream in a row may bring nothing before the turn is given up (each
waits `_LOG_SECONDS`, so this is an hour of silence from a session that says nothing at all)."""
_AGENT: Final = "udl"
"""The prefix of the agent files udl writes on a host."""
_WRITE_AGENT: Final = (
    'mkdir -p "$HOME/.config/opencode/agents" && cat > "$HOME/.config/opencode/agents/{name}.md"'
)
"""Write an agent file on the host, its text on the command's stdin (in no argv)."""
_POLICIES: Final = "$HOME/.config/opencode/opencode.json"
"""Where udl's deny policies go on a host (the service's own global config)."""
_AGENTS: Final = "$HOME/.config/opencode/agents"
"""Where udl's agent files go on a host."""
_DENIED: Final = ("shell:git push *", "shell:gh pr merge *")
"""What a session may never do, whatever its own rules say (25.9's hard stops)."""
_BRIEFS: Final[dict[str, str]] = {
    "implementer": (
        "You are udl's implementer agent. You build one task at a time, test first, and you"
        " never merge: the PR is Claude's to review and the user's to merge."
    ),
    "reviewer": (
        "You are udl's reviewer agent. You read the diff against its task card, test first, and"
        " you say what is wrong; the merge is the user's."
    ),
}
"""What each agent file says, by role (the session's own brief is added to it)."""
_BRIEF: Final = (
    "You are udl's {role} agent. You work one task at a time, test first, and you report what"
    " you find; the merge is the user's."
)
"""What an agent file says for a role udl has no words for yet."""
_CONFIG: Final[TypeAdapter[dict[str, object]]] = TypeAdapter(dict[str, object])
"""A service config file, as far as udl keeps it: every key, as it was."""
_ENTRIES: Final[TypeAdapter[list[dict[str, object]]]] = TypeAdapter(list[dict[str, object]])
_ANY_LIST: Final[TypeAdapter[list[object]]] = TypeAdapter(list[object])
"""A config's `policies`, when it is a list of objects."""
_KEY_NAME: Final = re.compile(r"^[A-Z_][A-Z0-9_]*$")
"""What a provider's key may be called: the variable the service reads it from."""
_CREDENTIAL: Final = "udl"
"""The prefix of the credentials udl stores, so a setup replaces its own and no one else's."""


_REFUSAL: Final = "Not allowed here: ask with orch_ask."
"""What a refused permission says (so the agent learns the way to ask, not to retry)."""
_PERMISSIONS: timedelta = timedelta(milliseconds=500)
"""How often a running turn looks for a permission asked (they are not in the durable log)."""


def _brief(role: str) -> str:
    """An agent file's text: the role's own, or a neutral one udl has no words for."""
    return _BRIEFS[role] if role in _BRIEFS else _BRIEF.format(role=role)


class OpenCodeOptions(RunnerOptions):
    """The opencode runner's options."""

    cli: list[str] = Field(default_factory=lambda: ["opencode"])
    """The program whose service this is (`opencode service status`)."""
    models: list[str] = Field(default_factory=list)
    """The models it may run (empty: whatever the server lists)."""
    agent: str | None = None
    """The OpenCode agent a session names (`udl-<role>`, written by 25.11)."""
    title: str = "udl"
    """The title a session is created with."""
    task: str | None = None
    """The udl task the session works on (None: not said), for its `metadata.udl`."""
    directory: str | None = None
    """The location `GET /api/model` is listed for (None: the session's cwd)."""


class _Info(BaseModel):
    """`GET /api/info`."""

    model_config = ConfigDict(extra="ignore")

    version: str
    pid: int = 0
    urls: list[str] = Field(default_factory=list)


class _ModelInfo(BaseModel):
    """One of `GET /api/model`'s models."""

    model_config = ConfigDict(extra="ignore")

    id: str
    name: str = ""
    provider_id: str = Field(default="", alias="providerID")


class _Models(BaseModel):
    """`GET /api/model`."""

    model_config = ConfigDict(extra="ignore")

    data: list[_ModelInfo] = Field(default_factory=lambda: list[_ModelInfo]())


class _Method(BaseModel):
    """One way an integration takes its credential (`Integration.Method`, as udl reads it)."""

    model_config = ConfigDict(extra="ignore")

    type: str = ""
    names: list[str] = []


class _Integration(BaseModel):
    """One provider `GET /api/integration` lists."""

    model_config = ConfigDict(extra="ignore")

    id: str = ""
    methods: list[_Method] = []


class _Integrations(BaseModel):
    """`GET /api/integration`."""

    model_config = ConfigDict(extra="ignore")

    data: list[_Integration] = []


class _Auth(BaseModel):
    """One account `opencode auth list` names."""

    model_config = ConfigDict(extra="ignore")

    provider: str = ""
    account: str = ""


class _Auths(BaseModel):
    """`opencode auth list --format json`."""

    model_config = ConfigDict(extra="ignore")

    items: list[_Auth] = Field(default_factory=lambda: list[_Auth]())


class _Policy(BaseModel):
    """One entry of the service's config: what a tool may do, and to what."""

    model_config = ConfigDict(extra="ignore")

    action: str = ""
    resource: str = ""
    effect: str = ""


class _Config(BaseModel):
    """The service's own config on a host (`opencode.json`), as far as udl reads it."""

    model_config = ConfigDict(extra="ignore")

    policies: list[_Policy] = Field(default_factory=lambda: list[_Policy]())


class _When(BaseModel):
    """`Session.Info`'s `time`: when the session was made, touched and last went idle."""

    model_config = ConfigDict(extra="ignore")

    idle: float | None = None
    """When its last turn ended (None: no turn has finished on it yet)."""


class _Session(BaseModel):
    """`Session.Info`, as far as udl reads it."""

    model_config = ConfigDict(extra="ignore")

    id: str
    cost: float = 0.0
    outcome: str | None = None
    """How its last turn ended (`succeeded`, `failed`, `interrupted`, or None while it runs)."""
    time: _When = Field(default_factory=_When)


class _Created(BaseModel):
    """`POST /api/session`'s answer."""

    model_config = ConfigDict(extra="ignore")

    data: _Session


class _Tokens(BaseModel):
    """What a usage event says a turn was given and wrote."""

    model_config = ConfigDict(extra="ignore")

    input: int = 0
    output: int = 0


class _Event(BaseModel):
    """One item of the event stream: its type, its durable marker, and what it says."""

    model_config = ConfigDict(extra="ignore")

    type: str
    data: dict[str, object] = Field(default_factory=dict)
    durable: dict[str, object] | None = None


class _Native(BaseModel):
    """The provider's own blob a tool event carries (it is where the tool's name is)."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None


class _Input(BaseModel):
    """What a tool was given, as far as udl summarises it."""

    model_config = ConfigDict(extra="allow")

    command: str | None = None
    file_path: str | None = Field(default=None, alias="filePath")
    path: str | None = None
    query: str | None = None
    pattern: str | None = None
    url: str | None = None


class _Failure(BaseModel):
    """What a failed execution says (`Session.StructuredError`)."""

    model_config = ConfigDict(extra="ignore")

    type: str | None = None
    message: str | None = None
    status: int | None = None
    retry_after_ms: int | None = Field(default=None, alias="retryAfterMs")

    @property
    def limited(self) -> bool:
        """Whether it says the account is out of quota (a 429, or a wait it names)."""
        return self.status == 429 or self.retry_after_ms is not None


class _Permissions(BaseModel):
    """`GET /api/session/{id}/permission`: the requests waiting for an answer."""

    model_config = ConfigDict(extra="ignore")

    data: list[dict[str, object]] = Field(default_factory=lambda: list[dict[str, object]]())


class _Service(BaseModel):
    """The service's `service.json`."""

    model_config = ConfigDict(extra="ignore")

    password: str


@dataclass
class OpenCodeSessions:
    """An opencode runner's sessions, outside any runner object (a service may hold one).

    A session outlives the runner that started it: OpenCode keeps it, so a runner made later
    resumes it by id."""

    specs: MutableMapping[str, SessionSpec] = field(default_factory=dict[str, SessionSpec])
    """What each session was started with, by id."""


class OpenCode(AgentRunner[OpenCodeOptions]):
    """OpenCode 2 as a runner."""

    name: ClassVar[str] = "opencode"
    options_model: ClassVar[type[RunnerOptions]] = OpenCodeOptions
    requires: ClassVar[tuple[Requirement, ...]] = (Requirement.binary("opencode"),)

    def __init__(
        self,
        options: OpenCodeOptions,
        *,
        shell: HostShell,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
        sessions: OpenCodeSessions | None = None,
    ) -> None:
        super().__init__(options, shell=shell, clock=clock)
        self._transport = transport
        """How a call reaches the service (None: the network; a test gives ASGITransport)."""
        self._sessions = sessions if sessions is not None else OpenCodeSessions()
        """The sessions it knows, shared with the runners made after it (they resume by id)."""
        self._stopped: set[str] = set()
        self._running: dict[str, httpx.Response] = {}

    # -- the service ------------------------------------------------------------------------

    async def health(self) -> RunnerHealth:
        try:
            said = await self.shell.run([*self.options.cli, "--version"])
        except (ToolUnavailable, PluginFailed) as failure:
            return RunnerHealth(installed=False, detail=str(failure))
        version = said.stdout.strip() or None
        accounts = await self._accounts()
        guards = await self.guards()
        try:
            async with self._api() as api:
                info = await self._get(api, "/api/info", _Info)
        except (ToolUnavailable, PluginFailed) as failure:
            return RunnerHealth(
                installed=True,
                version=version,
                detail=str(failure),
                account=accounts[0] if accounts else None,
                signed_in=bool(accounts),
                guards=guards,
            )
        return RunnerHealth(
            installed=True,
            signed_in=bool(accounts),  # (the service's password is not a provider's account)
            version=info.version or version,
            account=accounts[0] if accounts else None,
            plan=None,  # (a provider's plan is not something the service says)
            guards=guards,
            detail=f"pid {info.pid}",
        )

    async def _sign_ins(self) -> list[_Auth]:
        """What its providers are signed in as (`opencode auth list`), in order: an empty list
        when none is, and when the CLI cannot say."""
        try:
            said = await self.shell.run([*self.options.cli, "auth", "list", "--format", "json"])
        except ToolUnavailable, PluginFailed:
            return []
        try:
            listed = _Auths.model_validate_json(said.stdout)
        except ValidationError:
            try:  # (the CLI prints a bare list in some versions)
                return TypeAdapter(list[_Auth]).validate_json(said.stdout)
            except ValidationError:
                return []
        return listed.items

    async def _accounts(self) -> list[str]:
        """The accounts its providers are signed in as, in order."""
        return [sign_in.account for sign_in in await self._sign_ins() if sign_in.account]

    async def guards(self) -> bool:
        """Whether udl's deny policies are in the service's config on the host (25.9's rules,
        written there by setup)."""
        try:
            said = await self.shell.run(["sh", "-c", f"cat {_POLICIES} 2>/dev/null"])
        except ToolUnavailable, PluginFailed:
            return False
        try:
            config = _Config.model_validate_json(said.stdout or "{}")
        except ValidationError:
            return False
        resources = {policy.resource for policy in config.policies if policy.effect == "deny"}
        return all(resource in resources for resource in _DENIED)

    def install(self, version: str | None = None) -> AsyncIterator[str]:
        """Install or update it: `opencode upgrade [<version>]`, then restart its service (the
        caller refuses while a session is running)."""
        argv = [*self.options.cli, "upgrade", *([version] if version is not None else [])]
        return self._install(argv)

    async def _install(self, argv: list[str]) -> AsyncIterator[str]:
        """The upgrade's output, then its service restarted."""
        async for said in lines(self.shell.stream(argv)):
            yield said
        await self.shell.run([*self.options.cli, "service", "restart"])

    async def setup(
        self,
        roles: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> list[str]:
        """Write the deny policies (25.9's) and one agent file per role, then set the service's
        environment (`env`: a provider's key by variable name).

        The config file is read first and written back with udl's two policies merged into it: a
        host that already has providers, models or MCP servers keeps every one of them, and a
        second setup changes nothing. The key's value reaches `opencode` through the shell's
        environment, never through udl's argv (see the 25.11 card's decisions for what `opencode
        service set env` does with it on the host)."""
        written: list[str] = []
        await self._write(_POLICIES, json.dumps(await self._merged_policies(), indent=2))
        written.append(_POLICIES)
        for role in roles:
            name = f"{_AGENT}-{role}"
            await self._write(
                f"{_AGENTS}/{name}.md",
                f"---\nmode: primary\nsteps: 200\n---\n\n{_brief(role)}\n",
            )
            written.append(f"{_AGENTS}/{name}.md")
        if env:
            await self._set_keys(env)
        return written

    async def _set_keys(self, env: Mapping[str, str]) -> None:
        """Store each provider key as the service's own credential (`POST /api/credential`).

        The key never reaches a command: not udl's argv, not an `sh -c` line on the host, and so
        never the host's process list. The service keeps it (its credential store is what
        `opencode auth list` reads), and `activate` makes it the one its sessions use. A setup run
        again replaces the credential it wrote the first time instead of adding another."""
        for name in env:
            if not _KEY_NAME.fullmatch(name):
                raise NotAllowed(
                    f"{name!r} is not an environment variable udl will set",
                    hint="A provider's key is set as a variable named `[A-Z_][A-Z0-9_]*`.",
                )
        async with self._api() as api:
            by_env = await self._providers(api)
            for name, value in env.items():
                providers = by_env.get(name, ())
                if len(providers) > 1:
                    raise NotAllowed(
                        f"{name!r} is the key of more than one provider: {' and '.join(providers)}",
                        hint="Set the key with the CLI (`opencode auth login <provider>`) instead.",
                    )
                if not providers:
                    known = ", ".join(sorted(by_env)) or "none"
                    raise NotAllowed(
                        f"opencode has no provider whose key is called {name!r}",
                        hint=f"Its providers take: {known}.",
                    )
                await self._credential(api, providers[0], value)

    async def _providers(self, api: httpx.AsyncClient) -> dict[str, tuple[str, ...]]:
        """Every provider the service offers, by the variable its key is given in
        (`GET /api/integration`: `OPENROUTER_API_KEY` is `openrouter`, `TOGETHER_API_KEY` is
        `togetherai` — the service's own ids, never a guess from the name).

        A variable two providers take keeps both, in the order they were listed: a key stored under
        whichever came last would be on the wrong provider, so the caller refuses it."""
        listed = await self._get(api, "/api/integration", _Integrations)
        found: dict[str, list[str]] = {}
        for integration in listed.data:
            for method in integration.methods:
                if method.type == "env":
                    for name in method.names:
                        found.setdefault(name, []).append(integration.id)
        return {name: tuple(ids) for name, ids in found.items()}

    async def _credential(self, api: httpx.AsyncClient, integration: str, key: str) -> None:
        """`key` as `integration`'s credential, replacing the one udl stored before.

        The answer's body is never in a failure: a service that repeats what it was sent would put
        the key in udl's log."""
        credential = f"{_CREDENTIAL}-{integration}"
        body: dict[str, object] = {
            "id": credential,
            "integrationID": integration,
            "value": {"type": "key", "key": key},
            "activate": True,
            "label": "udl",
        }
        status = await self._status(api, "POST", "/api/credential", json=body)
        if status == 409:  # (the one udl wrote before: replaced, not added to)
            removed = await self._status(api, "DELETE", f"/api/credential/{credential}")
            if removed >= httpx.codes.INTERNAL_SERVER_ERROR:  # (400: it is already gone)
                raise PluginFailed(
                    f"opencode said {removed} to DELETE /api/credential/{credential}",
                    hint="Its answer is not shown: it may repeat the key.",
                )
            status = await self._status(api, "POST", "/api/credential", json=body)
        if status >= httpx.codes.BAD_REQUEST:
            raise PluginFailed(
                f"opencode said {status} to POST /api/credential for {integration}",
                hint="Its answer is not shown: it may repeat the key.",
            )

    async def _status(
        self,
        api: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> int:
        """One call whose status is the answer (its body is left out, as it may hold a secret)."""
        try:
            answer = await api.request(method, path, json=json)
        except httpx.HTTPError as failure:
            raise PluginFailed(f"opencode could not be reached: {failure}") from failure
        return answer.status_code

    async def _merged_policies(self) -> dict[str, object]:
        """The service's config as it is on the host, with udl's deny policies merged in: every
        other key is kept as it was (a setup never erases a host's own configuration)."""
        # (A host with no config yet is the usual case: `cat` on a missing file is not a failure
        # here, so it is guarded rather than left to exit 1 and stop the setup.)
        said = await self.shell.run(["sh", "-c", f"cat {_POLICIES} 2>/dev/null || true"])
        try:
            config = _CONFIG.validate_json(said.stdout or "{}")
        except ValidationError:
            config = {}
        merged: dict[str, object] = dict(config)
        merged.setdefault("$schema", "https://opencode.ai/config.json")
        kept: list[object] = []
        existing = merged.get("policies")
        if isinstance(existing, list):
            try:
                entries = _ENTRIES.validate_python(existing)
            except ValidationError:
                kept.extend(_ANY_LIST.validate_python(existing))
                entries = []
            for entry in entries:
                ours = (
                    entry.get("action") == "tool.use"
                    and entry.get("effect") == "deny"
                    and entry.get("resource") in _DENIED
                )
                if not ours:
                    kept.append(entry)
        for resource in _DENIED:
            kept.append({"action": "tool.use", "resource": resource, "effect": "deny"})
        merged["policies"] = kept
        return merged

    async def _write(self, path: str, text: str) -> None:
        """Write a file on the host through its shell (0600: a policy and a brief are udl's)."""
        # (`run` raises PluginFailed for a command that fails: there is no code to check here.)
        await self.shell.run(
            ["sh", "-c", f'umask 077 && mkdir -p "$(dirname {path})" && cat > {path}'],
            stdin=text.encode(),
        )

    @property
    def sign_in_command(self) -> tuple[str, ...]:
        """`opencode auth login <provider>`: what a person runs when udl cannot relay it."""
        return (*self.options.cli, "auth", "login")

    async def login(self, provider: str | None = None) -> Interaction:
        """`opencode auth login <provider>`, relayed: it prints a URL and takes the code back. A
        provider is named: without one the CLI opens a picker no relay can drive."""
        if not provider:
            raise NotAllowed(
                "opencode needs the provider to sign in to",
                hint="Name one (`openrouter`), or set a key with `udl orch runners setup`.",
            )
        return await self.shell.interact([*self.options.cli, "auth", "login", provider])

    async def logout(self) -> None:
        """`opencode auth logout <provider> <account>`, for every sign-in it has: the card's
        argv, so it is the account that goes, never a guess at its provider."""
        for sign_in in await self._sign_ins():
            await self.shell.run(
                [*self.options.cli, "auth", "logout", sign_in.provider, sign_in.account]
            )

    async def models(self) -> list[RunnerModel]:
        try:
            async with self._api() as api:
                listed = await self._get(
                    api,
                    "/api/model",
                    _Models,
                    params={"location[directory]": self.options.directory}
                    if self.options.directory
                    else None,
                )
        except (ToolUnavailable, PluginFailed) as failure:
            raise NotAllowed(f"opencode's models cannot be listed: {failure}") from failure
        wanted = set(self.options.models)
        return [
            RunnerModel(id=f"{model.provider_id}/{model.id}", name=model.name or model.id)
            for model in listed.data
            if not wanted or f"{model.provider_id}/{model.id}" in wanted
        ]

    # -- a session --------------------------------------------------------------------------

    async def start(self, spec: SessionSpec) -> SessionRef:
        agent = await self._agent(spec)
        metadata: dict[str, object] = {"agent": agent}
        if self.options.task is not None:
            metadata["task"] = self.options.task
        async with self._api() as api:
            created = await self._post(
                api,
                "/api/session",
                _Created,
                {
                    "title": self.options.title,
                    "model": _model_ref(spec.model),
                    "agent": agent,
                    "location": {"directory": spec.cwd},
                    "permissions": _ruleset(spec.cwd, spec.deny),
                    "metadata": {"udl": metadata},
                },
            )
            session = SessionRef(
                runner=self.name, id=created.data.id, cwd=spec.cwd, host=self.shell.host.name
            )
            if spec.mcp is not None:
                await self._add_mcp(api, spec)
            variables = self._variables(spec)
            if variables:
                await self._call(
                    api,
                    "PUT",
                    f"/api/session/{session.id}/environment",
                    json={"variables": variables},
                )
        self._sessions.specs[session.id] = spec
        return session

    async def _agent(self, spec: SessionSpec) -> str | None:
        """The OpenCode agent a session runs as: the one the options name, or — when the spec
        brings its brief — one written to the host's own `~/.config/opencode/agents/` (named after
        the brief's hash, so two sessions with different briefs never share one)."""
        if not spec.instructions:
            return self.options.agent
        name = f"{_AGENT}-{hashlib.sha256(spec.instructions.encode()).hexdigest()[:12]}"
        # (A failed write is the shell's PluginFailed, not a result to check here.)
        await self.shell.run(
            ["sh", "-c", _WRITE_AGENT.format(name=name)],
            stdin=f"---\nmode: primary\n---\n\n{spec.instructions}\n".encode(),
        )
        return name

    def _variables(self, spec: SessionSpec) -> dict[str, str]:
        """The session's environment: the spec's own, its secrets, and udl's MCP token. They are
        set on the session, never in a file, and a secret is in no argv."""
        variables = dict(spec.env)
        variables |= {name: secret.get_secret_value() for name, secret in spec.secret_env.items()}
        if spec.mcp is not None and spec.mcp.token is not None:
            variables[_TOKEN] = spec.mcp.token.get_secret_value()
        return variables

    async def stop(self, session: SessionRef) -> None:
        self._stopped.add(session.id)
        if (running := self._running.pop(session.id, None)) is not None:
            await running.aclose()
        async with self._api() as api:
            await self._call(api, "POST", f"/api/session/{session.id}/interrupt")

    async def send(self, session: SessionRef, prompt: str) -> AsyncGenerator[AgentEvent]:
        if session.id not in self._sessions.specs:
            yield TurnEnded(reason=TurnReason.ERROR, text=f"No session {session.id!r}.")
            return
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        ended: TurnEnded | None = None
        try:
            async with self._api() as api:
                watching = asyncio.create_task(self._watch_permissions(api, session))
                try:
                    async for event in self._follow(api, session, prompt):
                        if isinstance(event, TurnEnded):
                            ended = event
                            break
                        yield event
                finally:
                    watching.cancel()
                    with suppress(asyncio.CancelledError):
                        await watching
                    # (A turn that ended before the watcher's first look still refuses what it
                    # asked.)
                    await self._refuse_pending(api, session)
        except (ToolUnavailable, PluginFailed) as failure:
            # (The service cannot be reached, the prompt is refused, or the stream cannot be read:
            # the turn ends, and it never raises out of `send`.)
            ended = TurnEnded(reason=TurnReason.ERROR, text=str(failure))
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        yield ended or TurnEnded(reason=TurnReason.ERROR, text="The turn ended without saying how.")

    # -- the events -------------------------------------------------------------------------

    async def _follow(
        self, api: httpx.AsyncClient, session: SessionRef, prompt: str
    ) -> AsyncIterator[AgentEvent]:
        """The service's event stream, as the session's own events, until the turn ends.

        The stream is OpenCode's volatile one (`/api/event`): its session log, in 2.1.2, replays
        nothing (a read of it says only `log.synced`), so the subscription is opened **before** the
        prompt is sent — otherwise a fast turn's own events are gone before udl listens — and a
        stream that drops is opened again. Nothing is said twice: text streamed as deltas is not
        repeated by the message's own `ended`.

        The session's own `outcome` is the backstop for a stream that says nothing: once this turn
        has started (`session.execution.started`, so the *previous* turn's outcome is never taken
        for this one) and the stream brings nothing, the session says how it ended. Silence alone
        does not end a turn that is still sending events, however long it runs."""
        idle = 0
        was_idle: float | None = None  # (when the session was last idle before this turn)
        asked = self.clock.now()  # (when the session was last asked how the turn ended)
        prompted = False
        streamed: set[str] = set()
        while True:
            try:
                async with api.stream("GET", _EVENTS, timeout=_LOG_TIMEOUT) as answer:
                    if answer.status_code != 200:
                        await answer.aread()
                        raise PluginFailed(f"opencode's events said {answer.status_code}")
                    self._running[session.id] = answer
                    try:
                        if not prompted:
                            before = await self._get(api, f"/api/session/{session.id}", _Created)
                            was_idle = before.data.time.idle
                            await self._call(
                                api,
                                "POST",
                                f"/api/session/{session.id}/prompt",
                                json={
                                    "text": prompt,
                                    "delivery": "queue",
                                    "metadata": {"udl": True},
                                },
                            )
                            prompted = True
                        async for said in _sse(answer):
                            if _session_of(said) != session.id:
                                # (One stream, every session on the host: a busy one never times
                                # out, so the session is asked on a timer too, not only on a drop.)
                                if (now := self.clock.now()) - asked >= _ASK_EVERY:
                                    asked = now
                                    if (
                                        ended := await self._outcome(api, session, was_idle)
                                    ) is not None:
                                        yield ended
                                        return
                                continue
                            idle = 0  # (progress: only a stream that says nothing runs out)
                            asked = self.clock.now()
                            for event in await self._events(said, streamed):
                                yield event
                                if isinstance(event, TurnEnded):
                                    return
                    finally:
                        self._running.pop(session.id, None)
            except httpx.HTTPError as failure:
                # (A read that timed out, or a stream `stop` closed under it, is a dropped read.)
                log.debug("opencode's events for %s dropped: %s", session.id, failure)
            if session.id in self._stopped:
                return
            # (The session is asked after every read, not only after a drop: the stream is
            # volatile, so a turn can start and end inside a gap and never say either.)
            asked = self.clock.now()
            if (ended := await self._outcome(api, session, was_idle)) is not None:
                yield ended
                return
            idle += 1
            if idle >= _DROPS:
                raise PluginFailed(
                    f"opencode's events for {session.id} keep dropping without an answer."
                )
            await asyncio.sleep(0)  # (a yield between tries is enough: the reads time out)

    async def _outcome(
        self, api: httpx.AsyncClient, session: SessionRef, was_idle: float | None
    ) -> TurnEnded | None:
        """How this turn ended, if it has (None: it is still running).

        The session's `outcome` is its **last** turn's, so it is only this turn's when the session
        has gone idle since this turn's prompt: a stream event that says this turn started is
        volatile, and the service may not clear `outcome` as a new turn starts.
        """
        said = await self._get(api, f"/api/session/{session.id}", _Created)
        if said.data.outcome is None:
            return None
        idle = said.data.time.idle
        if idle is None or (was_idle is not None and idle <= was_idle):
            return None  # (the previous turn's ending, still: this one has not finished)
        return {
            "succeeded": TurnEnded(reason=TurnReason.DONE),
            "interrupted": TurnEnded(reason=TurnReason.STOPPED, text="The turn was interrupted."),
        }.get(
            said.data.outcome,
            TurnEnded(reason=TurnReason.ERROR, text=f"The turn {said.data.outcome}."),
        )

    async def _events(self, said: _Event, streamed: set[str]) -> list[AgentEvent]:
        """What one event is (nothing, when udl does not read it)."""
        data = said.data
        if said.type == "session.text.delta":
            message = _text(data.get("assistantMessageID")) or ""
            streamed.add(message)
            return [TextDelta(text=_text(data.get("delta")) or "")]
        if said.type == "session.text.ended":
            if (_text(data.get("assistantMessageID")) or "") in streamed:
                return []  # (its deltas already said it)
            return [TextDelta(text=_text(data.get("text")) or "")]
        if said.type == "session.tool.called":
            return [ToolStarted(name=_tool(data), summary=_summary(data.get("input")))]
        if said.type in ("session.tool.success", "session.tool.failed"):
            return [ToolFinished(name=_tool(data), ok=said.type == "session.tool.success")]
        if said.type in ("session.usage.recorded", "session.usage.updated"):
            tokens = _tokens(data.get("tokens"))
            return [
                Usage(
                    input_tokens=tokens.input,
                    output_tokens=tokens.output,
                    cost=_decimal(data.get("cost")),
                )
            ]
        if said.type == "session.execution.succeeded":
            return [TurnEnded(reason=TurnReason.DONE)]
        if said.type == "session.execution.failed":
            return self._failed(data.get("error"))
        if said.type == "session.execution.interrupted":
            return [TurnEnded(reason=TurnReason.STOPPED, text="The turn was interrupted.")]
        log.debug("opencode said an event udl does not read: %s", said.type)
        return []

    def _failed(self, error: object) -> list[AgentEvent]:
        """A failed execution: the turn ended, or — out of quota — the limit and then its end."""
        failure = _failure(error)
        if failure.limited:
            until = None
            if failure.retry_after_ms is not None:
                until = self.clock.now() + timedelta(milliseconds=failure.retry_after_ms)
            return [
                RunnerLimited(until=until, message=_failure_text(failure)),
                TurnEnded(reason=TurnReason.LIMITED, text=_failure_text(failure)),
            ]
        return [TurnEnded(reason=TurnReason.ERROR, text=_failure_text(failure))]

    async def _watch_permissions(self, api: httpx.AsyncClient, session: SessionRef) -> None:
        """Refuse every permission the session asks, for as long as its turn runs.

        A permission is not in the durable log (the log has no `permission.*` events), and the
        volatile `/api/event` stream drops a slow reader, so the pending requests are read from
        the session's own list: a session never hangs on a prompt nobody sees."""
        while True:
            await self._refuse_pending(api, session)
            await self.clock.sleep(_PERMISSIONS)

    async def _refuse_pending(self, api: httpx.AsyncClient, session: SessionRef) -> None:
        """Refuse every permission waiting for the session (a list that cannot be read is said in
        the log, and is not the turn's failure)."""
        try:
            listed = await self._get(api, f"/api/session/{session.id}/permission", _Permissions)
        except PluginFailed as failure:
            log.debug("opencode's permissions cannot be read: %s", failure)
            return
        for request in listed.data:
            with suppress(PluginFailed):  # (it may have been answered or dropped meanwhile)
                await self._refuse_asking(api, session, _text(request.get("id")))

    async def _refuse_asking(
        self, api: httpx.AsyncClient, session: SessionRef, request_id: str | None
    ) -> None:
        """Refuse a permission the session's ruleset did not answer (it asked, so it is refused),
        and say where an agent does ask: udl's own `orch_ask`."""
        if request_id is None:
            return
        await self._call(
            api,
            "POST",
            f"/api/session/{session.id}/permission/{request_id}/reply",
            json={"decision": "reject", "message": _REFUSAL},
        )

    # -- the connection ---------------------------------------------------------------------

    @asynccontextmanager
    async def _api(self) -> AsyncGenerator[httpx.AsyncClient]:
        """A client for the service, reached through the shell, on every connect."""
        url = await self._url()
        password = await self._password()
        port = urlparse(url).port
        if port is None:
            raise ToolUnavailable(f"opencode's service says no port: {url!r}")
        async with (
            self.shell.forward(port) as base,
            httpx.AsyncClient(
                base_url=base,
                transport=self._transport,
                auth=(  # (Basic auth, user `opencode`: a Bearer header is refused)
                    "opencode",
                    password,
                ),
                timeout=_SECONDS,
            ) as client,
        ):
            yield client

    async def _url(self) -> str:
        """The service's URL, as `opencode service status` says it (or why it has none)."""
        said = await self.shell.run([*self.options.cli, "service", "status"])
        url = said.stdout.strip()
        if not url.startswith("http"):
            raise ToolUnavailable(
                f"opencode's service is not running ({url or 'no answer'}): "
                f"`{' '.join([*self.options.cli, 'service', 'start'])}` starts it."
            )
        return url

    async def _password(self) -> str:
        """The service's password, from its `service.json` on the host."""
        said = await self.shell.run(
            ["sh", "-c", 'cat "$HOME/.config/opencode/service.json"'], stdin=None
        )
        try:
            return _Service.model_validate_json(said.stdout).password
        except ValidationError as failure:
            raise ToolUnavailable(
                "opencode's service.json cannot be read: `opencode service start` writes it."
            ) from failure

    async def _add_mcp(self, api: httpx.AsyncClient, spec: SessionSpec) -> None:
        """Add udl's MCP server to the session's own location (`spec.cwd`), its token read from
        the session's environment."""
        if spec.mcp is None:
            return
        await self._call(
            api,
            "PUT",
            f"/api/experimental/mcp/{_MCP}",
            params={"location[directory]": spec.cwd},
            json={
                "config": {
                    "type": "remote",
                    "url": spec.mcp.url,
                    "headers": {"X-API-Key": "{env:" + _TOKEN + "}"},
                    "oauth": False,  # (a header key needs OAuth off, since it is on by default)
                }
            },
        )

    # -- calls ------------------------------------------------------------------------------

    async def _get[T: BaseModel](
        self,
        api: httpx.AsyncClient,
        path: str,
        model: type[T],
        *,
        params: Mapping[str, str] | None = None,
    ) -> T:
        answer = await self._call(api, "GET", path, params=params)
        return _parse(model, answer)

    async def _post[T: BaseModel](
        self, api: httpx.AsyncClient, path: str, model: type[T], body: Mapping[str, object]
    ) -> T:
        answer = await self._call(api, "POST", path, json=body)
        return _parse(model, answer)

    async def _call(
        self,
        api: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Mapping[str, object] | None = None,
    ) -> str:
        """One call to the service: its body, or the failure it is."""
        try:
            answer = await api.request(method, path, params=params, json=json)
        except httpx.HTTPError as failure:
            raise PluginFailed(f"opencode could not be reached: {failure}") from failure
        if answer.status_code >= httpx.codes.BAD_REQUEST:
            raise PluginFailed(
                f"opencode said {answer.status_code} to {method} {path}: {answer.text[:200]}"
            )
        return answer.text


def _parse[T: BaseModel](model: type[T], body: str) -> T:
    """A body as a model, or the failure it is."""
    try:
        return model.model_validate_json(body)
    except ValidationError as failure:
        raise PluginFailed(f"opencode's answer is not what udl reads: {failure}") from failure


async def _sse(answer: httpx.Response) -> AsyncIterator[_Event]:
    """A server-sent event stream's events, one at a time (its data, as the service says it)."""
    async for line in answer.aiter_lines():
        if not line.startswith("data:"):
            continue
        said = line[len("data:") :].strip()
        if not said:
            continue
        try:
            event = _Event.model_validate_json(said)
        except ValidationError:
            log.debug("opencode said an event udl cannot read: %r", said[:200])
            continue
        if event.type == "log.synced":
            continue
        yield event


def _session_of(event: _Event) -> str | None:
    """Which session an event is about (None: it is not about one)."""
    return _text(event.data.get("sessionID"))


def _ruleset(cwd: str, deny: list[str]) -> list[dict[str, str]]:
    """A session's permissions (V2's `permissions`, where the last match wins): everything is
    allowed, then what udl forbids is denied — the spec's shell commands, any directory outside
    the worktree, any edit outside it, and any question (nobody is there to answer one)."""
    rules = [{"action": "*", "resource": "*", "effect": "allow"}]
    rules += [{"action": "shell", "resource": refused, "effect": "deny"} for refused in deny]
    return [
        *rules,
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": f"{cwd}/*", "effect": "allow"},
        {"action": "question", "resource": "*", "effect": "deny"},
    ]


def _model_ref(model: str | None) -> dict[str, str] | None:
    """A model as the API names one (`provider/id#variant` in the CLI).

    A name with no provider is sent as both (`gpt-5` → provider `gpt-5`, id `gpt-5`): the service
    answers with what it has, and a wrong guess fails there rather than here. The variant is kept
    either way."""
    if not model:
        return None
    said, _, variant = model.partition("#")
    provider, _, identifier = said.partition("/")
    ref = {"providerID": provider, "id": identifier or said}
    if variant:
        ref["variant"] = variant
    return ref


def _tool(data: Mapping[str, object]) -> str:
    """A tool's name: the event does not say it, the provider's own blob does."""
    try:
        return _Native.model_validate(data.get("native")).name or "tool"
    except ValidationError:
        return "tool"


def _summary(tool_input: object) -> str:
    """What a tool is doing, in a line."""
    try:
        said = _Input.model_validate(tool_input)
    except ValidationError:
        return ""
    for value in (said.command, said.file_path, said.path, said.query, said.pattern, said.url):
        if value:
            return value[:200]
    return ", ".join(sorted(said.model_extra or {}))[:200]


def _tokens(value: object) -> _Tokens:
    """A usage event's tokens."""
    try:
        return _Tokens.model_validate(value)
    except ValidationError:
        return _Tokens()


def _text(value: object) -> str | None:
    """A field of an event, when it is a string."""
    return value if isinstance(value, str) else None


def _decimal(value: object) -> Decimal | None:
    """A cost, as the API says it (a number in USD)."""
    return Decimal(str(value)) if isinstance(value, int | float) else None


def _failure(value: object) -> _Failure:
    """What a failed execution said, as far as udl reads it."""
    try:
        return _Failure.model_validate(value)
    except ValidationError:
        return _Failure(message=json.dumps(value)[:500] if value is not None else None)


def _failure_text(failure: _Failure) -> str:
    """A failure in words a person reads (never a secret)."""
    if failure.message:
        return failure.message
    if failure.type:
        return failure.type
    return "opencode said the execution failed."
