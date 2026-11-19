"""
---------------
- runners.py -
---------------

FakeRunner: a runner whose turns are scripted, so the runner contract (and, from 26.x, orch's
tests and the e2e ones) run without a model or a harness installed.

A script is a turn's answer per prompt, and a step in it may call back into udl as an agent would:
count_sites() asks the service for its sites, the way a real agent's tool call would, and says what
it found. A prompt with no script gets the options' `answer`.

Its sessions live in a `Sessions` store, not in the runner, so a session outlives the runner object
as a real harness's does: two FakeRunners given one store share its sessions. A spec whose
environment says `FAKE_HARNESS_LIMITED=1` is out of quota (as `fake_harness.py` is), and the
scripted `wait_for_stop` turn waits until the session is stopped, so the contract can stop a turn
while it is running. `RecordingShell` is a real local shell that records every argv it is asked to
run, so a test can say a secret never reached one.
"""

# System Imports
import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar, Protocol

# Package Imports
from pydantic import Field

# Local Imports
from udl.clock import Clock
from udl.core.models import SiteList
from udl.db.models import SiteQuery
from udl.hosts.shell import Host, HostShell, Interaction
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
)
from udl.process import CancelToken, ProcessResult

RESET: datetime = datetime.fromtimestamp(1_800_000_000, tz=UTC)
"""When the fake's quota is back (`FAKE_HARNESS_LIMITED=1`), as `fake_harness.py` says it is."""


class AsksUDL(Protocol):
    """What a scripted turn may ask udl: the operations it calls (a real UDLService is one)."""

    async def list_sites(self, request: SiteQuery) -> SiteList:
        """List the sites udl knows."""
        ...


class FakeRunnerOptions(RunnerOptions):
    """The fake's options: what it can run, and what it says when nothing is scripted."""

    models: list[str] = Field(default_factory=lambda: ["fake-1", "fake-2"])
    """What it can run."""
    answer: str = "Done."
    """What a turn with no script says."""


type Turn = Callable[["FakeRunner", SessionRef], AsyncIterator[AgentEvent]]
"""A scripted turn: the events it gives, one by one (it may await udl in between)."""


@dataclass
class Sessions:
    """The sessions a harness has, outside any runner object: a session outlives the runner."""

    specs: dict[str, SessionSpec] = field(default_factory=dict[str, SessionSpec])
    """What each session was started with, by id."""
    stopped: set[str] = field(default_factory=set[str])
    """The sessions that were stopped."""
    sent: set[str] = field(default_factory=set[str])
    """The sessions that have had a turn (the command runner's resume turns run after one)."""
    turns: int = 0
    """How many turns were sent."""
    gates: dict[str, asyncio.Event] = field(default_factory=dict[str, asyncio.Event])
    """One per session, set when it is stopped: a turn that waits on it is let go."""
    secret_envs: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    """The secret environment each session was started with (what reached the harness)."""
    seq: dict[str, int] = field(default_factory=dict[str, int])
    """The log sequence each session was read up to (a harness with a durable log has one)."""

    def gate(self, session: SessionRef) -> asyncio.Event:
        """The gate a session's waiting turn holds on to (made on first use)."""
        return self.gates.setdefault(session.id, asyncio.Event())


class RecordingShell:
    """A real local shell that records every argv it is asked to run (never a secret in one)."""

    def __init__(self, shell: HostShell) -> None:
        self._shell = shell
        self.host: Host = shell.host
        """The host it acts on (the shell's own)."""
        self.calls: list[list[str]] = []
        """Every argv it was asked to run, in order."""
        self.secret_envs: list[dict[str, str]] = []
        """Every secret environment it was given, in order (a test's own: they stay there)."""

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
    ) -> ProcessResult:
        self.calls.append(list(argv))
        self.secret_envs.append(dict(secret_env or {}))
        return await self._shell.run(
            argv,
            cwd=cwd,
            env=env,
            secret_env=secret_env,
            stdin=stdin,
            within=within,
            cancel=cancel,
        )

    def stream(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        stdin: bytes | None = None,
        within: timedelta | None = None,
    ) -> AsyncIterator[bytes]:
        self.calls.append(list(argv))
        self.secret_envs.append(dict(secret_env or {}))
        stdin = stdin  # (a stream's input is its argv's, and it is recorded above)
        return self._shell.stream(argv, cwd=cwd, env=env, secret_env=secret_env, within=within)

    async def interact(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        within: timedelta | None = None,
    ) -> Interaction:
        self.calls.append(list(argv))
        self.secret_envs.append(dict(secret_env or {}))
        return await self._shell.interact(
            argv, cwd=cwd, env=env, secret_env=secret_env, within=within
        )

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return self._shell.forward(port)


class FakeRunner(AgentRunner[FakeRunnerOptions]):
    """A runner whose turns are scripted (tests/support/fake_harness.py is the binary the real
    runners' tests point at; this one needs no binary at all)."""

    name: ClassVar[str] = "fake"
    options_model: ClassVar[type[RunnerOptions]] = FakeRunnerOptions
    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(
        self,
        options: FakeRunnerOptions,
        *,
        shell: HostShell,
        clock: Clock,
        script: Mapping[str, Turn] | None = None,
        service: AsksUDL | None = None,
        store: Sessions | None = None,
    ) -> None:
        super().__init__(options, shell=shell, clock=clock)
        self._script = dict(script or {})
        self._service = service
        self._store = store if store is not None else Sessions()

    @property
    def service(self) -> AsksUDL:
        """udl, for a scripted turn that calls back into it (a test gives one)."""
        if self._service is None:
            raise RuntimeError("this FakeRunner was given no service")
        return self._service

    @property
    def store(self) -> Sessions:
        """The sessions the harness has (a test gives a second runner the same one)."""
        return self._store

    @property
    def sessions(self) -> Mapping[str, SessionSpec]:
        """The sessions the harness has, by id (they outlive the runner object)."""
        return dict(self._store.specs)

    @property
    def turns(self) -> int:
        """How many turns it was sent."""
        return self._store.turns

    async def waiting(self, session: SessionRef) -> None:
        """Wait until this session is stopped (a scripted turn uses it to be stopped mid-turn)."""
        await self._store.gate(session).wait()

    async def health(self) -> RunnerHealth:
        return RunnerHealth(installed=True, signed_in=True, version="fake-1.0", detail="scripted")

    async def models(self) -> list[RunnerModel]:
        return [RunnerModel(id=model, name=model) for model in self.options.models]

    async def start(self, spec: SessionSpec) -> SessionRef:
        session = SessionRef(
            runner=self.name,
            id=f"fake-{len(self._store.specs) + 1}",
            cwd=spec.cwd,
            host=self.shell.host.name,
        )
        self._store.specs[session.id] = spec
        self._store.secret_envs.append(
            {name: secret.get_secret_value() for name, secret in spec.secret_env.items()}
        )
        return session

    async def stop(self, session: SessionRef) -> None:
        self._store.stopped.add(session.id)
        self._store.gate(session).set()  # (a turn that is waiting for it is let go)

    async def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        spec = self._store.specs.get(session.id)
        if spec is None:
            yield TurnEnded(reason=TurnReason.ERROR, text=f"No session {session.id!r}.")
            return
        if session.id in self._store.stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        if spec.env.get("FAKE_HARNESS_LIMITED") == "1":
            yield RunnerLimited(until=RESET, message="The account is out of quota until then.")
            yield TurnEnded(reason=TurnReason.LIMITED, text="The account is out of quota.")
            return
        self._store.turns += 1
        turn = self._script.get(prompt)
        if turn is None:
            yield TextDelta(text=self.options.answer)
        else:
            async for event in turn(self, session):
                yield event
        if session.id in self._store.stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        yield TurnEnded(reason=TurnReason.DONE, text=self.options.answer)


async def count_sites(runner: FakeRunner, _: SessionRef) -> AsyncIterator[AgentEvent]:
    """A scripted turn that asks udl for its sites, as an agent's tool call would."""
    yield ToolStarted(name="udl.sites.list", summary="List the sites")
    sites = await runner.service.list_sites(SiteQuery())
    yield ToolFinished(name="udl.sites.list", ok=True)
    yield TextDelta(text=f"There are {len(sites.sites)} sites.")


async def wait_for_stop(runner: FakeRunner, session: SessionRef) -> AsyncIterator[AgentEvent]:
    """A scripted turn that waits until the session is stopped (the contract's mid-turn stop)."""
    yield ToolStarted(name="udl.wait", summary="Waiting for udl to stop the turn")
    await runner.waiting(session)
    yield ToolFinished(name="udl.wait", ok=False)
