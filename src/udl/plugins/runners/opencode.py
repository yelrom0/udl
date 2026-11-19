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

A turn is one prompt (`POST /api/session/{id}/prompt`, `delivery: queue`), then the session's
durable log (`GET /api/experimental/session/{id}/log?after=<seq>&follow=true`), resumed from the
last sequence number udl stored, so a dropped connection loses nothing and repeats nothing. Its
events become AgentEvents; `session.execution.succeeded`/`failed`/`interrupted` ends the turn, and
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
import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, MutableMapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar
from urllib.parse import urlparse

# Package Imports
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Local Imports
from udl.clock import Clock
from udl.errors import NotAllowed, PluginFailed, ToolUnavailable
from udl.hosts.shell import HostShell
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
)

log = logging.getLogger(__name__)

__all__ = ["OpenCode", "OpenCodeOptions", "OpenCodeSessions"]

_MCP: str = "udl"
"""The name udl's MCP server has in OpenCode."""
_TOKEN: str = "UDL_AGENT_TOKEN"  # noqa: S105 - (a variable's name, not a secret)
"""The environment variable the MCP config's header reads the token from."""
_SECONDS: float = 30.0
"""How long one call may take before it is a failure."""
_DROPS: int = 3
"""How many reads of a session's log in a row may bring nothing new before the turn fails."""
_PERMISSIONS: timedelta = timedelta(milliseconds=500)
"""How often a running turn looks for a permission asked (they are not in the durable log)."""


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
    directory: str | None = None
    """The location `GET /api/model` and the MCP server are added for (None: the session's cwd)."""


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


class _Session(BaseModel):
    """`Session.Info`, as far as udl reads it."""

    model_config = ConfigDict(extra="ignore")

    id: str
    cost: float = 0.0


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
    """One item of the session's log: its type, its sequence number, and what it says."""

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
    resumes it by id, from the sequence number udl stored."""

    specs: MutableMapping[str, SessionSpec] = field(default_factory=dict[str, SessionSpec])
    """What each session was started with, by id."""
    seq: MutableMapping[str, int] = field(default_factory=dict[str, int])
    """The last log sequence number udl read, by session id."""
    secret_envs: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    """The environments sessions were given (a secret never in a file, kept to resume)."""


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
        try:
            async with self._api() as api:
                info = await self._get(api, "/api/info", _Info)
        except (ToolUnavailable, PluginFailed) as failure:
            return RunnerHealth(installed=True, version=version, detail=str(failure))
        return RunnerHealth(
            installed=True,
            signed_in=True,  # (the API takes the service's own password, never an account)
            version=info.version or version,
            detail=f"pid {info.pid}",
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
        async with self._api() as api:
            created = await self._post(
                api,
                "/api/session",
                _Created,
                {
                    "title": self.options.title,
                    "model": _model_ref(spec.model),
                    "agent": self.options.agent,
                    "location": {"directory": spec.cwd},
                    "permissions": _ruleset(spec.cwd, spec.deny),
                    "metadata": {"udl": {"agent": self.options.agent, "task": self.options.title}},
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
        self._sessions.seq[session.id] = 0
        if spec.secret_env:
            self._sessions.secret_envs.append(
                {name: secret.get_secret_value() for name, secret in spec.secret_env.items()}
            )
        return session

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
        async with self._api() as api:
            await self._call(
                api,
                "POST",
                f"/api/session/{session.id}/prompt",
                json={"text": prompt, "delivery": "queue", "metadata": {"udl": True}},
            )
            watching = asyncio.create_task(self._watch_permissions(api, session))
            ended: TurnEnded | None = None
            try:
                async for event in self._follow(api, session):
                    if isinstance(event, TurnEnded):
                        ended = event
                        break
                    yield event
            except PluginFailed as failure:
                # (A log udl cannot read, or a service that stopped answering, ends the turn.)
                ended = TurnEnded(reason=TurnReason.ERROR, text=str(failure))
            finally:
                watching.cancel()
                with suppress(asyncio.CancelledError):
                    await watching
                # (A turn that ended before the watcher's first look still refuses what it asked.)
                await self._refuse_pending(api, session)
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        yield ended or TurnEnded(reason=TurnReason.ERROR, text="The turn ended without saying how.")

    # -- the log ----------------------------------------------------------------------------

    async def _follow(
        self, api: httpx.AsyncClient, session: SessionRef
    ) -> AsyncIterator[AgentEvent]:
        """The session's log from the last sequence udl stored, as events, until the turn ends.

        A connection that drops is opened again from the sequence udl stored: nothing is lost and
        nothing is said twice. Three reads in a row that bring nothing new is a failure."""
        path = f"/api/experimental/session/{session.id}/log"
        idle = 0
        while True:
            before = self._sessions.seq.get(session.id, 0)
            params = {"after": str(before), "follow": "true"}
            async with api.stream("GET", path, params=params) as answer:
                if answer.status_code != 200:
                    await answer.aread()
                    raise PluginFailed(f"opencode's log said {answer.status_code}")
                self._running[session.id] = answer
                try:
                    async for said in _sse(answer):
                        self._sessions.seq[session.id] = _sequence(
                            said, self._sessions.seq.get(session.id, 0)
                        )
                        for event in await self._events(api, session, said):
                            yield event
                            if isinstance(event, TurnEnded):
                                return
                finally:
                    self._running.pop(session.id, None)
            if session.id in self._stopped:
                return
            idle = 0 if self._sessions.seq.get(session.id, 0) > before else idle + 1
            if idle >= _DROPS:
                raise PluginFailed(
                    f"opencode's log for {session.id} keeps dropping without an answer."
                )

    async def _events(
        self, api: httpx.AsyncClient, session: SessionRef, said: _Event
    ) -> list[AgentEvent]:
        """What one item of the log is (nothing, when udl does not read it)."""
        data = said.data
        if said.type == "session.text.ended":
            return [TextDelta(text=_text(data.get("text")) or "")]
        if said.type == "session.tool.called":
            return [ToolStarted(name=_tool(data), summary=_summary(data.get("input")))]
        if said.type in ("session.tool.success", "session.tool.failed"):
            return [ToolFinished(name=_tool(data), ok=said.type == "session.tool.success")]
        if said.type == "session.usage.recorded":
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
                await self._refuse_asking(
                    api, session, _text(request.get("id")), _text(request.get("message"))
                )

    async def _refuse_asking(
        self,
        api: httpx.AsyncClient,
        session: SessionRef,
        request_id: str | None,
        message: str | None,
    ) -> None:
        """Refuse a permission the session's ruleset did not answer (it asked, so it is refused)."""
        if request_id is None:
            return
        await self._call(
            api,
            "POST",
            f"/api/session/{session.id}/permission/{request_id}/reply",
            json={"decision": "reject", "message": message or "udl does not allow this."},
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
        """Add udl's MCP server to the location, its token read from the session's environment."""
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
    """A server-sent event stream's events, one at a time (its data, as the log says it)."""
    async for line in answer.aiter_lines():
        if not line.startswith("data:"):
            continue
        said = line[len("data:") :].strip()
        if not said:
            continue
        try:
            event = _Event.model_validate_json(said)
        except ValidationError:
            log.debug("opencode said a log item udl cannot read: %r", said[:200])
            continue
        if event.type == "log.synced":
            continue
        yield event


def _sequence(event: _Event, after: int) -> int:
    """The sequence number to resume from, after reading `event`."""
    durable = event.durable
    if durable is not None and isinstance(seq := durable.get("seq"), int):
        return max(after, seq)
    return after


def _ruleset(cwd: str, deny: list[str]) -> list[dict[str, str]]:
    """A session's permissions (V2's `permissions`, where the last match wins): everything is
    allowed, then what udl forbids is denied — the spec's shell commands, any directory outside
    the worktree, any edit outside it, and any question (nobody is there to answer one)."""
    rules = [{"action": "*", "resource": "*", "effect": "allow"}]
    rules += [{"action": "shell", "resource": refused, "effect": "deny"} for refused in deny]
    return [
        *rules,
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": f"{cwd}/*", "effect": "allow"},
        {"action": "edit", "resource": "*", "effect": "deny"},
        {"action": "question", "resource": "*", "effect": "deny"},
    ]


def _model_ref(model: str | None) -> dict[str, str] | None:
    """A model as the API names one (`provider/id#variant` in the CLI)."""
    if not model:
        return None
    said, _, variant = model.partition("#")
    provider, _, identifier = said.partition("/")
    if not identifier:
        return {"providerID": provider, "id": said}
    ref = {"providerID": provider, "id": identifier}
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
