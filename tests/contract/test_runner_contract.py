"""
--------------------------
- test_runner_contract.py -
--------------------------

The behaviour every runner plugin (udl.runners) must have, run over each of them (each a Case: the
plugin class, a runner made from it, and the shell and clock it was given): it is named and its
options are its own model's; its health says whether the harness is there, signed in and which
version; its models are its own, each with an id; a session starts with the spec it was given and
names its host; a turn gives agent events and ends with a TurnEnded; a stopped session says so
rather than hanging; and a runner that cannot install or sign in itself says that, instead of
pretending it did.
"""

# System Imports
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.runners import FakeRunner, FakeRunnerOptions
from udl.errors import PluginError
from udl.hosts.shell import Host, LocalShell
from udl.plugins.requirements import Requirement
from udl.plugins.runners.base import (
    AgentEvent,
    AnAgentRunner,
    RunnerHealth,
    RunnerOptions,
    SessionRef,
    SessionSpec,
    TurnEnded,
    TurnReason,
)

type Make = Callable[..., AnAgentRunner]
"""A runner of a plugin class, made with its options, shell and clock (a test's own)."""


@dataclass(frozen=True)
class Case:
    """A runner plugin, and what makes one."""

    plugin: type[AnAgentRunner]
    options: RunnerOptions
    make: Make


def _fake(**kwargs: Any) -> AnAgentRunner:
    return FakeRunner(
        FakeRunnerOptions(),
        shell=LocalShell(Host.local(str(Path("/tmp")))),  # noqa: S108 - (only named here)
        clock=FakeClock(),
        **kwargs,
    )


CASES: dict[str, Case] = {
    "fake": Case(plugin=FakeRunner, options=FakeRunnerOptions(), make=_fake),
}


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]


@pytest.fixture
def runner(case: Case) -> AnAgentRunner:
    return case.make()


@pytest.fixture
def spec(tmp_path: Path) -> SessionSpec:
    return SessionSpec(cwd=str(tmp_path), model="test-model", instructions="Be brief.")


def test_it_is_named_and_its_options_are_its_own_model(case: Case, runner: AnAgentRunner) -> None:
    assert case.plugin.name
    assert runner.options == case.options
    assert isinstance(runner.clock, FakeClock)
    plugin = case.plugin

    assert plugin.name
    assert plugin.name.replace("-", "").replace("_", "").isalnum()
    assert issubclass(plugin.options_model, RunnerOptions)
    assert all(isinstance(requirement, Requirement) for requirement in plugin.requires)


@pytest.mark.asyncio
async def test_its_health_says_whether_the_harness_is_there_and_which_version(
    runner: AnAgentRunner,
) -> None:
    health = await runner.health()

    assert isinstance(health, RunnerHealth)
    assert health.installed
    assert health.version


@pytest.mark.asyncio
async def test_its_models_are_its_own_each_with_an_id(runner: AnAgentRunner) -> None:
    models = await runner.models()

    assert models
    assert len({model.id for model in models}) == len(models)
    assert all(model.id and model.name for model in models)


@pytest.mark.asyncio
async def test_a_session_starts_with_the_spec_it_was_given(
    runner: AnAgentRunner, spec: SessionSpec
) -> None:
    session = await runner.start(spec)

    assert isinstance(session, SessionRef)
    assert session.runner == runner.name
    assert session.id
    assert session.cwd == spec.cwd
    assert session.host == runner.shell.host.name


@pytest.mark.asyncio
async def test_a_turn_gives_agent_events_and_ends_with_a_turn_ended(
    runner: AnAgentRunner, spec: SessionSpec
) -> None:
    session = await runner.start(spec)

    events: list[AgentEvent] = [event async for event in runner.send(session, "hello")]

    assert events
    assert all(isinstance(event, AgentEvent) for event in events)
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE


@pytest.mark.asyncio
async def test_a_stopped_session_says_so_rather_than_hanging(
    runner: AnAgentRunner, spec: SessionSpec
) -> None:
    session = await runner.start(spec)
    await runner.stop(session)
    await runner.stop(session)  # (Stopping twice is not an error.)

    events: list[AgentEvent] = [event async for event in runner.send(session, "again")]

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED


@pytest.mark.asyncio
async def test_a_runner_that_cannot_install_or_sign_in_says_so(runner: AnAgentRunner) -> None:
    with pytest.raises(PluginError, match="cannot be installed"):
        async for _ in runner.install(None):
            pass

    with pytest.raises(PluginError, match="cannot sign in"):
        await runner.login()
