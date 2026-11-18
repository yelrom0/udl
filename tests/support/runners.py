"""
---------------
- runners.py -
---------------

FakeRunner: a runner whose turns are scripted, so the runner contract (and, from 26.x, orch's
tests and the e2e ones) run without a model or a harness installed.

A script is a turn's answer per prompt, and a step in it may call back into udl as an agent would:
count_sites() asks the service for its sites, the way a real agent's tool call would, and says what
it found. A prompt with no script gets the options' `answer`.
"""

# System Imports
from collections.abc import AsyncIterator, Callable, Mapping
from typing import ClassVar, Protocol

# Package Imports
from pydantic import Field

# Local Imports
from udl.clock import Clock
from udl.core.models import SiteList
from udl.db.models import SiteQuery
from udl.hosts.shell import HostShell
from udl.plugins.requirements import Requirement
from udl.plugins.runners.base import (
    AgentEvent,
    AgentRunner,
    RunnerHealth,
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


type Turn = Callable[["FakeRunner"], AsyncIterator[AgentEvent]]
"""A scripted turn: the events it gives, one by one (it may await udl in between)."""


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
    ) -> None:
        super().__init__(options, shell=shell, clock=clock)
        self._script = dict(script or {})
        self._service = service
        self._sessions: dict[str, SessionSpec] = {}
        self._stopped: set[str] = set()
        self._turns = 0

    @property
    def service(self) -> AsksUDL:
        """udl, for a scripted turn that calls back into it (a test gives one)."""
        if self._service is None:
            raise RuntimeError("this FakeRunner was given no service")
        return self._service

    @property
    def sessions(self) -> Mapping[str, SessionSpec]:
        """The sessions it started, by id."""
        return dict(self._sessions)

    @property
    def turns(self) -> int:
        """How many turns it was sent."""
        return self._turns

    async def health(self) -> RunnerHealth:
        return RunnerHealth(installed=True, signed_in=True, version="fake-1.0", detail="scripted")

    async def models(self) -> list[RunnerModel]:
        return [RunnerModel(id=model, name=model) for model in self.options.models]

    async def start(self, spec: SessionSpec) -> SessionRef:
        session = SessionRef(
            runner=self.name,
            id=f"fake-{len(self._sessions) + 1}",
            cwd=spec.cwd,
            host=self.shell.host.name,
        )
        self._sessions[session.id] = spec
        return session

    async def stop(self, session: SessionRef) -> None:
        self._stopped.add(session.id)

    async def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        if session.id not in self._sessions:
            yield TurnEnded(reason=TurnReason.ERROR, text=f"No session {session.id!r}.")
            return
        if session.id in self._stopped:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        self._turns += 1
        turn = self._script.get(prompt)
        if turn is None:
            yield TextDelta(text=self.options.answer)
        else:
            async for event in turn(self):
                yield event
        yield TurnEnded(reason=TurnReason.DONE, text=self.options.answer)


async def count_sites(runner: FakeRunner) -> AsyncIterator[AgentEvent]:
    """A scripted turn that asks udl for its sites, as an agent's tool call would."""
    sites = await runner.service.list_sites(SiteQuery())
    yield ToolStarted(name="udl.sites.list", summary="List the sites")
    yield ToolFinished(name="udl.sites.list", ok=True)
    yield TextDelta(text=f"There are {len(sites.sites)} sites.")
