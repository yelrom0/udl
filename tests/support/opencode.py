"""
-------------
- opencode.py -
-------------

FakeOpenCode: OpenCode 2's HTTP API (an ASGI app) for udl's `opencode` runner to run against. It
answers the calls the runner makes — `GET /api/info`, `GET /api/model`, `POST /api/session`,
`PUT /api/session/{id}/environment`, `POST /api/session/{id}/prompt`,
`GET /api/experimental/session/{id}/log` (the durable log, as SSE),
`POST /api/session/{id}/interrupt`, `GET /api/session/{id}`,
`GET /api/session/{id}/permission` and `POST /api/session/{id}/permission/{id}/reply`,
`PUT /api/experimental/mcp/{server}` — with the shapes of the OpenAPI document OpenCode 2.1.2
serves (`tests/fixtures/opencode/openapi-2.1.2.json`, and the payloads of its events from
opencode.ai/docs/openapi.json), and Basic auth as the service has it.

A test scripts the turn a prompt starts (`script`), can cut the log after N events (`cut_after`, so
a reader must resume from the sequence it stored), asks a permission (`ask`, which waits in the
session's list), and reads what the runner sent (`calls`).
"""

# System Imports
import asyncio
import base64
import json
import re
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final, Literal

# Package Imports
import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

# Local Imports
from udl.errors import PluginFailed
from udl.hosts.shell import Host, HostShell, Interaction, ResourceLimits
from udl.process import CancelToken, ProcessResult


class _CredentialKey(BaseModel):
    """`Credential.Key`, as the committed document has it."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["key"]
    key: str
    metadata: dict[str, object] = Field(default_factory=dict[str, object])


class CreateInput(BaseModel):
    """`Credential.CreateInput`, as the committed document has it: a test compares the two, so the
    fake and the document cannot drift apart."""

    model_config = ConfigDict(extra="forbid")

    integrationID: str  # noqa: N815 - (the API's own name)
    value: _CredentialKey
    activate: bool = True
    id: str | None = None
    label: str = ""


USER: Final = "opencode"
"""The service's Basic auth user (the password is the one in its `service.json`)."""
PASSWORD: Final = "fake-opencode-password"  # noqa: S105 - (the fake's own)
"""The password the fake accepts."""


def event(
    kind: str, seq: int, data: dict[str, object], *, durable: bool = True
) -> dict[str, object]:
    """One item of the durable log, as the service says it."""
    said: dict[str, object] = {
        "id": f"evt_{seq}",
        "created": 1_760_000_000 + seq,
        "type": kind,
        "location": {"directory": "/w"},
        "data": data,
    }
    if durable:
        said["durable"] = {"aggregateID": "ses", "seq": seq, "version": 1}
    return said


class _Environment(BaseModel):
    """What `PUT /api/session/{id}/environment` is given."""

    variables: dict[str, str] = Field(default_factory=dict[str, str])


class _Durable(BaseModel):
    """An event's durable envelope: its sequence number, in the log."""

    seq: int = 0


class _Sequenced(BaseModel):
    """An event, as far as its place in the log goes."""

    durable: _Durable | None = None


_SLOW: float = 0.1
"""How long a slow turn's log takes to answer: a turn that runs until it is interrupted (the
ASGI transport answers when the app's stream ends, so a slow read is a wait, not an endless one)."""


_AGENT_FILE = re.compile(r"agents/([^/\"]+)\.md")


class FakeShell(HostShell):
    """A host's shell: `service status` and `service.json` as a test scripts them, and a URL."""

    def __init__(
        self, *, status: str = "http://127.0.0.1:4096", service: str | None = None
    ) -> None:
        self.host = Host.local("/w")
        self.status = status
        self.service = service if service is not None else json.dumps({"password": PASSWORD})
        self.runs: list[list[str]] = []
        self.given: list[tuple[list[str], bytes | None]] = []
        """Every argv it was asked to run, with what was written to it, in order (a sign-in's
        key is on stdin, and this is how a test reads it)."""
        self.files: dict[str, str] = {}
        """The files written through the shell (an agent file, by its name)."""
        self.writes_fail = False
        """Whether writing a file fails."""
        self.auths = "[]"
        """What `opencode auth list --format json` prints."""
        self.signed_out = False
        """Whether `codex login status` says Not logged in (a runner that signs in with a key)."""
        self.streams: list[tuple[list[str], str]] = []
        """Every argv it was asked to stream, and what it answered (an install's output)."""
        self.started: list[list[str]] = []
        """Every argv it started as a sign-in."""
        self.interactions: list[FakeInteraction] = []
        """Every sign-in it started, in order."""
        self.limits: list[ResourceLimits | None] = []
        """Every memory cap it was asked for, in order (None: a run that was not capped)."""
        self.unit: str | None = None
        """The service's unit, when this host has one (27.12's cap is read from it)."""
        self.cgroup: str = "/user.slice/opencode.service"
        """The service unit's ControlGroup."""
        self.capped: str | None = None
        """What the service's cgroup says its memory limit is (`max`: no cap)."""
        self.oom_policy: str | None = "continue"
        """The service unit's `OOMPolicy`."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        cancel: CancelToken | None = None,
        check: bool = True,
        tail: bool = False,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> ProcessResult:
        self.runs.append(list(argv))
        self.given.append((list(argv), stdin))
        self.limits.append(limits)
        answered = self._answer(argv, stdin=stdin)
        if answered.returncode != 0 and check:
            # (As the real shell does: a command that fails is PluginFailed, not a result.)
            raise PluginFailed(
                f"{argv[0]} failed with exit code {answered.returncode}: {answered.stderr.strip()}"
            )
        return answered

    def _answer(self, argv: Sequence[str], *, stdin: bytes | None = None) -> ProcessResult:
        """What this fake says to `argv` (before the shell's own failure rule is applied)."""
        if argv[0] == "systemctl" and "list-units" in argv:
            said = f"{self.unit} loaded active running It\n" if self.unit else ""
            return ProcessResult(returncode=0, stdout=said, stderr="")
        if argv[0] == "systemctl" and "OOMPolicy" in argv:
            said = f"{self.oom_policy}\n" if self.oom_policy else ""
            return ProcessResult(returncode=0, stdout=said, stderr="")
        if argv[0] == "systemctl" and argv[-1] == "--value":
            return ProcessResult(returncode=0, stdout=f"{self.cgroup}\n", stderr="")
        if argv[0] == "cat" and argv[-1].startswith("/sys/fs/cgroup"):
            if self.capped is None:
                return ProcessResult(returncode=1, stdout="", stderr="cat: No such file")
            return ProcessResult(returncode=0, stdout=f"{self.capped}\n", stderr="")
        if argv[1:4] == ["auth", "list", "--format"]:
            return ProcessResult(returncode=0, stdout=self.auths, stderr="")
        # (Before the generic `status` below: `codex login status` ends in `status` too.)
        if argv[1:3] == ["login", "status"]:
            said = "Not logged in" if self.signed_out else "Logged in using ChatGPT"
            return ProcessResult(returncode=0, stdout=f"{said}\n", stderr="")
        if argv[-1] == "status":
            return ProcessResult(returncode=0, stdout=f"{self.status}\n", stderr="")
        if argv[-1] == "--version":
            return ProcessResult(returncode=0, stdout="2.1.2\n", stderr="")
        if argv[0] == "sh" and "cat > " in argv[-1]:
            # (A file written through the shell: an agent file by its name, else by its path.)
            if self.writes_fail:
                return ProcessResult(returncode=1, stdout="", stderr="no permission")
            path = argv[-1].split("cat > ", 1)[1].split(" 2>", 1)[0].strip().strip('"')
            found = _AGENT_FILE.search(path)
            key = found.group(1) if found is not None else path
            self.files[key] = (stdin or b"").decode()
            return ProcessResult(returncode=0, stdout="", stderr="")
        if (
            argv[0] == "sh"
            and "cat " in argv[-1]
            and "service.json" not in argv[-1]
            and ".config/opencode" in argv[-1]
        ):
            # (A config file read back through the shell, as `cat` does it: a file that is not
            # there is a failure, which is what a setup on a fresh host meets.)
            path = (
                argv[-1]
                .split("cat ", 1)[1]
                .split(" 2>", 1)[0]
                .split(" ||", 1)[0]
                .strip()
                .strip('"')
            )
            found = _AGENT_FILE.search(path)
            key = found.group(1) if found is not None else path
            if key not in self.files and "|| true" in argv[-1]:
                # (The command says a missing file is not a failure: it exits 0, as `sh` does.)
                return ProcessResult(returncode=0, stdout="", stderr="")
            if key not in self.files:
                return ProcessResult(returncode=1, stdout="", stderr=f"cat: {path}: No such file")
            return ProcessResult(returncode=0, stdout=self.files[key], stderr="")
        if argv[0] == "sh":
            return ProcessResult(returncode=0, stdout=self.service, stderr="")
        return ProcessResult(returncode=0, stdout="", stderr="")

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
        limits: ResourceLimits | None = None,
        unit: str | None = None,
    ) -> AsyncIterator[bytes]:
        self.runs.append(list(argv))
        self.limits.append(limits)
        said = next((text for found, text in self.streams if found == list(argv)), "")
        return _chunks(said)

    async def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Interaction:
        self.started.append(list(argv))
        started = FakeInteraction()
        self.interactions.append(started)
        return started

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return nullcontext("http://opencode.test")


@dataclass
class FakeOpenCode:
    """OpenCode 2's API, with a session's log a test writes."""

    password: str = PASSWORD
    """The password Basic auth must send."""
    version: str = "2.1.2"
    """What `GET /api/info` says its version is."""
    models: list[dict[str, object]] = field(
        default_factory=lambda: [
            {
                "id": "deepseek/deepseek-v4.1-flash",
                "name": "DeepSeek V4.1 Flash",
                "providerID": "openrouter",
                "modelID": "deepseek/deepseek-v4.1-flash",
                "family": "deepseek",
                "enabled": True,
            }
        ]
    )
    """What `GET /api/model` lists."""
    script: list[dict[str, object]] = field(
        default_factory=lambda: [
            event(
                "session.text.started",
                1,
                {"sessionID": "ses", "assistantMessageID": "msg", "ordinal": 0},
            ),
            event(
                "session.text.ended",
                2,
                {"sessionID": "ses", "assistantMessageID": "msg", "ordinal": 0, "text": "Hello!"},
            ),
            event("session.execution.succeeded", 3, {"sessionID": "ses"}),
        ]
    )
    """The events a prompt appends to the log (with their own sequence numbers rewritten)."""
    cut_after: int | None = None
    """Close the log after this many events of one read (None: never), as a dropped connection."""
    cut_always: bool = False
    """Whether every read drops (False: only the first, then the log is whole again)."""
    calls: list[tuple[str, str, object]] = field(default_factory=list[tuple[str, str, object]])
    """Every call: (method, path, body)."""
    replies: list[tuple[str, str, str, str]] = field(
        default_factory=list[tuple[str, str, str, str]]
    )
    """Every permission reply: (session id, request id, decision, message)."""
    sessions: dict[str, dict[str, object]] = field(default_factory=dict[str, dict[str, object]])
    """The sessions created, by id."""
    log: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    """The events appended so far."""
    seq: int = 0
    """The last sequence number written."""
    ticks: int = 0
    """How many clock readings it has made (the session's `time` values)."""
    turns: int = 0
    """How many prompts it has been sent."""
    interrupted: bool = False
    """Whether a session was interrupted."""
    pending: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    """The permissions waiting for an answer (what `GET /api/session/{id}/permission` lists)."""
    variables: dict[str, str] = field(default_factory=dict[str, str])
    """The environment sessions were given (`PUT /api/session/{id}/environment`)."""
    credentials: dict[str, dict[str, object]] = field(default_factory=dict[str, dict[str, object]])
    """The credentials stored (`POST /api/credential`), by id."""
    integrations: list[dict[str, object]] = field(
        default_factory=lambda: [
            {
                "id": "openrouter",
                "name": "OpenRouter",
                "methods": [{"type": "env", "names": ["OPENROUTER_API_KEY"]}],
                "connections": [],
            },
            {
                "id": "openai",
                "name": "OpenAI",
                "methods": [{"type": "env", "names": ["OPENAI_API_KEY"]}],
                "connections": [],
            },
            {
                "id": "anthropic",
                "name": "Anthropic",
                "methods": [{"type": "env", "names": ["ANTHROPIC_API_KEY"]}],
                "connections": [],
            },
            {
                "id": "togetherai",
                "name": "Together AI",
                "methods": [{"type": "env", "names": ["TOGETHER_API_KEY"]}],
                "connections": [],
            },
        ]
    )
    """What `GET /api/integration` lists (the provider ids a key's variable can be found under)."""
    credential_bodies: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    """Every body `POST /api/credential` was sent, as it was sent."""
    delete_status: int = 204
    """What `DELETE /api/credential/{id}` answers (204: it is gone; a test's own failure)."""
    credential_status: int = 200
    """What `POST /api/credential` answers (200: it stores it; a test's own failure)."""
    credential_says: str = ""
    """What that failure says (a test's own text, which may repeat what it was sent)."""
    pending_outcome: str | None = None
    """How the turn whose events the stream is serving ended (published with its last event)."""
    silent_reads: int = 0
    """How many subscriptions say nothing before the events are served."""
    keep_log: bool = False
    """Whether the event stream replays what it already sent (the service does not)."""
    parts: int = 1
    """How many reads one turn's events are served over (1: one read)."""
    gap: int = 0
    """How many silent reads sit between those parts (a turn that pauses to think)."""
    omit_started: bool = False
    """Whether the turn's `session.execution.started` is left out (a stream that dropped early)."""
    ends_before_served: bool = False
    """Whether the turn ends on the service at once (False: when its events have been served), as a
    turn that finished while a dropped read was not looking."""
    keep_outcome: bool = False
    """Whether a prompt leaves the previous turn's `outcome` in place (the service does not clear it
    as a new turn starts), so a test can tell a stale outcome from this turn's."""
    report_outcome: bool = True
    """Whether `GET /api/session/{id}` says how the last turn ended."""
    record_into: list[dict[str, str]] | None = None
    """Where the environment a session is given is recorded (None: nowhere)."""
    prompt_status: int = 200
    """What `POST /api/session/{id}/prompt` answers (200: the input is admitted)."""
    permission_list_fails: bool = False
    """Whether `GET /api/session/{id}/permission` fails (a service that is having a bad day)."""
    log_status: int = 200
    """What `GET /api/experimental/session/{id}/log` answers (a test's own failure)."""
    app: Starlette = field(init=False)
    """The ASGI app."""

    def __post_init__(self) -> None:
        self.app = Starlette(
            routes=[
                Route("/api/event", self._events, methods=["GET"]),
                Route("/api/info", self._info, methods=["GET"]),
                Route("/api/model", self._models, methods=["GET"]),
                Route("/api/session", self._create, methods=["POST"]),
                Route("/api/session/{session_id}", self._session, methods=["GET"]),
                Route("/api/session/{session_id}/environment", self._environment, methods=["PUT"]),
                Route("/api/session/{session_id}/prompt", self._prompt, methods=["POST"]),
                Route("/api/session/{session_id}/interrupt", self._interrupt, methods=["POST"]),
                Route(
                    "/api/session/{session_id}/permission",
                    self._permissions,
                    methods=["GET"],
                ),
                Route(
                    "/api/session/{session_id}/permission/{request_id}/reply",
                    self._reply,
                    methods=["POST"],
                ),
                Route(
                    "/api/experimental/session/{session_id}/log", self._read_log, methods=["GET"]
                ),
                Route("/api/integration", self._integration, methods=["GET"]),
                Route("/api/credential", self._credential, methods=["GET", "POST"]),
                Route("/api/credential/{credential_id}", self._credential_one, methods=["DELETE"]),
                Route("/api/experimental/mcp/{server}", self._mcp, methods=["PUT"]),
            ]
        )

    # -- what a test asks it ---------------------------------------------------------------

    def turn(self, *events: dict[str, object]) -> None:
        """Script the next prompts' events (replacing the default turn)."""
        self.script = list(events)

    def ask(self, request_id: str = "per_1", **fields: object) -> dict[str, object]:
        """A permission asked of a session: it waits in the session's list, as the service keeps
        it (a permission is in no durable log)."""
        request: dict[str, object] = {
            "id": request_id,
            "sessionID": "ses",
            "action": "tool.use",
            "resources": ["shell:git push origin main"],
            "save": [],
            "metadata": {},
            "source": {"type": "tool"},
            "message": "git push?",
            **fields,
        }
        self.pending.append(request)
        return request

    # -- auth ------------------------------------------------------------------------------

    def _allowed(self, request: Request) -> bool:
        said = request.headers.get("authorization", "")
        if not said.startswith("Basic "):
            return False
        try:
            user, _, password = base64.b64decode(said[6:]).decode().partition(":")
        except ValueError:
            return False
        return user == USER and password == self.password

    # -- the calls -------------------------------------------------------------------------

    async def _info(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        self.calls.append(("GET", "/api/info", None))
        return JSONResponse(
            {
                "version": self.version,
                "pid": 1234,
                "urls": ["http://127.0.0.1:4096"],
                "paths": {"tmp": "/tmp/opencode"},  # noqa: S108 - (a fake's answer, never made)
                "capabilities": {"persistentPty": True},
            }
        )

    async def _models(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        self.calls.append(("GET", "/api/model", dict(request.query_params)))
        return JSONResponse(
            {
                "location": {"directory": request.query_params.get("location[directory]")},
                "data": self.models,
            }
        )

    async def _integration(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        self.calls.append(("GET", "/api/integration", dict(request.query_params)))
        return JSONResponse(
            {
                "location": {"directory": request.query_params.get("location[directory]")},
                "data": self.integrations,
            }
        )

    async def _credential(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        if request.method == "GET":
            self.calls.append(("GET", "/api/credential", None))
            return JSONResponse({"data": list(self.credentials.values())})
        body = await request.json()
        self.calls.append(("POST", "/api/credential", body))
        if self.credential_status != 200:
            return JSONResponse(
                {"message": self.credential_says}, status_code=self.credential_status
            )
        try:
            made = CreateInput.model_validate(body)  # (the document's shape, as a model)
        except ValidationError as failure:
            return JSONResponse({"message": str(failure)}, status_code=400)
        self.credential_bodies.append(made.model_dump())
        if made.id is not None and made.id in self.credentials:
            return JSONResponse({"message": "that credential exists"}, status_code=409)
        credential_id = made.id if made.id is not None else f"cred_{len(self.credentials) + 1}"
        stored: dict[str, object] = {
            "id": credential_id,
            "integrationID": made.integrationID,
            "label": made.label,
            "active": made.activate,
            "value": {"type": "key", "key": made.value.key},
        }
        self.credentials[credential_id] = stored
        return JSONResponse({"data": stored})

    async def _credential_one(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        credential_id = request.path_params["credential_id"]
        self.calls.append(("DELETE", f"/api/credential/{credential_id}", None))
        if self.delete_status != 204:
            return JSONResponse({"message": "cannot delete"}, status_code=self.delete_status)
        if self.credentials.pop(credential_id, None) is None:
            return JSONResponse({"message": "no such credential"}, status_code=400)
        return Response(status_code=204)

    def _tick(self) -> int:
        """A millisecond that always moves on (a real clock can repeat within one turn)."""
        self.ticks += 1
        return 1_700_000_000_000 + self.ticks

    async def _create(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        body = await request.json()
        self.calls.append(("POST", "/api/session", body))
        identifier = f"ses_{len(self.sessions) + 1}"
        self.sessions[identifier] = {
            "id": identifier,
            "title": body.get("title"),
            "agent": body.get("agent"),
            "model": body.get("model"),
            "location": body.get("location"),
            "metadata": body.get("metadata"),
            "cost": 0.0,
            "tokens": {"input": 0, "output": 0},
            "time": {"created": self._tick(), "updated": self._tick()},
        }
        return JSONResponse({"data": self.sessions[identifier]})

    async def _session(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        identifier = request.path_params["session_id"]
        self.calls.append(("GET", f"/api/session/{identifier}", None))
        if identifier not in self.sessions:
            return JSONResponse({"message": "no session"}, status_code=404)
        return JSONResponse({"data": self.sessions[identifier]})

    async def _environment(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        identifier = request.path_params["session_id"]
        body: object = await request.json()
        self.calls.append(("PUT", f"/api/session/{identifier}/environment", body))
        # (A test that sends nothing udl reads sets no environment.)
        with suppress(ValidationError):
            self.variables |= _Environment.model_validate(body).variables
            if self.record_into is not None:  # (what reached the harness, for the contract)
                self.record_into.append(dict(_Environment.model_validate(body).variables))
        return Response(status_code=204)

    async def _prompt(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        identifier = request.path_params["session_id"]
        body = await request.json()
        self.calls.append(("POST", f"/api/session/{identifier}/prompt", body))
        if self.prompt_status != 200:
            return JSONResponse({"message": "no"}, status_code=self.prompt_status)
        self.turns += 1
        written = self._turn_events()
        if self.omit_started:
            written = [one for one in written if one["type"] != "session.execution.started"]
        # (The outcome is published when the stream has said everything, as a real one is: a turn
        # that is still being read has no outcome yet.)
        if not self.keep_outcome:
            self.sessions[identifier]["outcome"] = None
        self.pending_outcome = _outcome_of(written) if self.report_outcome else None
        if self.ends_before_served:
            self.sessions[identifier]["outcome"] = self.pending_outcome
            when = self.sessions[identifier].get("time")
            if isinstance(when, dict):
                when["idle"] = when["updated"] = self._tick()
            self.pending_outcome = None
        for event in written:
            self.seq += 1
            written = json.loads(json.dumps(event))
            if "durable" in written:
                written["durable"]["seq"] = self.seq
                written["durable"]["aggregateID"] = identifier
            data = written.get("data")
            if isinstance(data, dict):
                data["sessionID"] = identifier
            self.log.append(written)
        return JSONResponse({"data": {"id": f"inb_{self.turns}", "sessionID": identifier}})

    def _turn_events(self) -> list[dict[str, object]]:
        """What a prompt appends: the script, and what the environment asks of it (the runner
        contract's slow and limited turns, as the harnesses have them)."""
        if self.variables.get("FAKE_HARNESS_LIMITED"):
            return [
                event(
                    "session.execution.failed",
                    0,
                    {
                        "sessionID": "ses",
                        "error": {
                            "type": "ProviderError",
                            "message": "The account is out of credit.",
                            "status": 429,
                            "retryAfterMs": 60_000,
                        },
                    },
                )
            ]
        if self.variables.get("FAKE_HARNESS_SLOW"):
            return self.script[:-1]  # (the turn runs until it is interrupted)
        return self.script

    async def _permissions(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        identifier = request.path_params["session_id"]
        self.calls.append(("GET", f"/api/session/{identifier}/permission", None))
        if self.permission_list_fails:
            return JSONResponse({"message": "no"}, status_code=500)
        # (A request asked without a session is the one the test's own session is asked; one
        # asked for another session is that session's.)
        return JSONResponse(
            {
                "data": [
                    said for said in self.pending if said.get("sessionID") in (identifier, "ses")
                ]
            }
        )

    async def _interrupt(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        identifier = request.path_params["session_id"]
        self.calls.append(("POST", f"/api/session/{identifier}/interrupt", None))
        self.interrupted = True
        self.log.append(
            event("session.execution.interrupted", self._next(), {"sessionID": identifier})
        )
        return JSONResponse({"interrupted": True})

    async def _reply(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        identifier = request.path_params["session_id"]
        request_id = request.path_params["request_id"]
        body = await request.json()
        self.calls.append(
            ("POST", f"/api/session/{identifier}/permission/{request_id}/reply", body)
        )
        self.replies.append(
            (identifier, request_id, str(body.get("decision")), str(body.get("message")))
        )
        self.pending = [said for said in self.pending if said.get("id") != request_id]
        return Response(status_code=204)

    async def _mcp(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        server = request.path_params["server"]
        self.calls.append(("PUT", f"/api/experimental/mcp/{server}", await request.json()))
        return Response(status_code=204)

    async def _events(self, request: Request) -> Response:
        """The service's own event stream (`/api/event`): every session's events as they happen,
        which is what a turn is followed on (the session log, in 2.1.2, replays nothing)."""
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        self.calls.append(("GET", "/api/event", dict(request.query_params)))
        if self.log_status != 200:
            return Response(status_code=self.log_status)
        silent = bool(self.silent_reads)
        if silent:  # (a stream that is open and says nothing yet)
            self.silent_reads -= 1
        waiting = [] if silent else list(self.log)
        if not self.keep_log and not silent:
            self.log = []
        if self.parts > 1 and waiting:
            size = -(-len(waiting) // self.parts)  # (a ceiling division: the parts are even)
            self.log = waiting[size:] + self.log
            waiting = waiting[:size]
            self.parts -= 1
            self.silent_reads += self.gap  # (the gap sits between the parts)

        async def lines() -> AsyncGenerator[bytes]:
            # (The test transport runs the app to its end before the runner can send the prompt, so
            # a subscription opened before it serves nothing here; a real service streams it live.)
            for sent, said in enumerate(waiting):
                if self.cut_after is not None and sent >= self.cut_after:
                    if not self.cut_always:
                        self.cut_after = None  # (only the first read drops)
                    return  # (a dropped connection)
                yield _sse(said)
            yield _sse({"type": "log.synced", "aggregateID": "ses", "seq": self.seq})
            # (A turn has no outcome until its events have all been served: a stream that says
            # nothing yet leaves it running.)
            if (outcome := self.pending_outcome) is not None and waiting and self.parts <= 1:
                self.pending_outcome = None
                # (This turn's own outcome replaces the previous one, even where a test asked the
                # prompt to leave it: a later turn that ends says how *it* ended.)
                for identifier in self.sessions:
                    self.sessions[identifier]["outcome"] = outcome
                    when = self.sessions[identifier].get("time")
                    if isinstance(when, dict):
                        when["idle"] = when["updated"] = self._tick()
            if self.variables.get("FAKE_HARNESS_SLOW"):
                await asyncio.sleep(_SLOW)  # (a turn that runs until it is interrupted)

        return StreamingResponse(lines(), media_type="text/event-stream")

    async def _read_log(self, request: Request) -> Response:
        if not self._allowed(request):
            return JSONResponse({"message": "unauthorized"}, status_code=401)
        identifier = request.path_params["session_id"]
        after = int(request.query_params.get("after") or 0)
        follow = request.query_params.get("follow") == "true"
        self.calls.append(("GET", f"/api/experimental/session/{identifier}/log", {"after": after}))
        if self.log_status != 200:
            return Response(status_code=self.log_status)
        waiting = [event for event in self.log if _seq(event) > after]

        async def lines() -> AsyncGenerator[bytes]:
            for sent, said in enumerate(waiting):
                if self.cut_after is not None and sent >= self.cut_after:
                    if not self.cut_always:
                        self.cut_after = None  # (only the first read drops)
                    return  # (a dropped connection)
                yield _sse(said)
            if not follow:
                return
            yield _sse({"type": "log.synced", "aggregateID": identifier, "seq": self.seq})
            if self.variables.get("FAKE_HARNESS_SLOW"):
                await asyncio.sleep(_SLOW)  # (a turn that runs until it is interrupted)

        return StreamingResponse(lines(), media_type="text/event-stream")

    def _next(self) -> int:
        self.seq += 1
        return self.seq


def _outcome_of(written: list[dict[str, object]]) -> str | None:
    """How a turn ended, as `GET /api/session/{id}` reports it (None: it is still running)."""
    ends = {
        "session.execution.succeeded": "succeeded",
        "session.execution.failed": "failed",
        "session.execution.interrupted": "interrupted",
    }
    return next((ends[str(event["type"])] for event in written if str(event["type"]) in ends), None)


def _seq(event: dict[str, object]) -> int:
    """The sequence number an event carries (0 for one that is not durable)."""
    try:
        said = _Sequenced.model_validate(event)
    except ValidationError:
        return 0
    return said.durable.seq if said.durable is not None else 0


class _Live(httpx.AsyncByteStream):
    """A body that stays open, saying what a test gave it (the ASGI transport cannot stream live:
    it runs the app to its end before the runner can send the prompt)."""

    def __init__(self, said: Sequence[dict[str, object]]) -> None:
        self._said = list(said)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            for one in self._said:
                yield _sse(one)
            await asyncio.sleep(0.01)


class BusyHost(httpx.AsyncBaseTransport):
    """The fake's service, but with an event stream that stays open and says another session's
    events forever — a busy host, whose stream never times out while this turn's events are gone.
    Everything else goes to the fake's own ASGI app."""

    def __init__(self, service: FakeOpenCode) -> None:
        self._service = service
        self._inner = httpx.ASGITransport(app=service.app)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/event":
            self._service.calls.append(("GET", "/api/event", dict(request.url.params)))
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=_Live(
                    [
                        {
                            "type": "session.tool.started",
                            "durable": {"aggregateID": "ses_other", "seq": 1},
                            "data": {"sessionID": "ses_other", "callID": "call"},
                        }
                    ]
                ),
            )
        return await self._inner.handle_async_request(request)


def _sse(item: dict[str, object]) -> bytes:
    """One server-sent event: its name, its data (the log item as JSON text), and its id."""
    said = str(item.get("type"))
    return f"event: {said}\ndata: {json.dumps(item)}\n\n".encode()


def as_sequence(events: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    """Events as a test scripts them, in order."""
    return list(events)


async def _chunks(text: str) -> AsyncIterator[bytes]:
    """A stream's text, a line at a time."""
    for line in text.splitlines(keepends=True):
        yield line.encode()


class FakeInteraction:
    """A sign-in that is running: what it says, what it is told, and how it ends."""

    def __init__(self, says: Sequence[str] = (), exit_code: int = 0) -> None:
        self._says = list(says)
        self._ended = asyncio.Event()
        self.exit_code = exit_code
        """What it exits with (0: signed in)."""
        self.written: list[str] = []
        """Every line it was given, in order."""
        self.closed = False
        """Whether it was ended."""
        self.running = True
        """Whether it is still running."""

    async def read(self) -> str:
        """What it has printed since the last read."""
        return self._says.pop(0) if self._says else ""

    async def write(self, text: str) -> None:
        """Give it a line (it ends once it has what it asked for)."""
        self.written.append(text)
        self._ended.set()

    async def close(self) -> None:
        """End it."""
        self.closed = True
        self.running = False
        self._ended.set()

    async def wait(self) -> int:
        """Wait for it to end."""
        await self._ended.wait()
        self.running = False
        return self.exit_code
