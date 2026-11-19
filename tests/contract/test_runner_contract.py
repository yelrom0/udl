"""
--------------------------
- test_runner_contract.py -
--------------------------

The behaviour every runner plugin (udl.runners) must have, run over each of them (each a Case: the
plugin class, the options, and a factory that makes a runner with the shell and session store the
case shares): it is named and its options are its own model's; its health says whether the harness
is there, signed in and which version; its models are its own, each with an id; a session starts
with the spec it was given and names its host; a prompt gives agent events and ends in exactly one
TurnEnded; a session resumes by id after the runner object is thrown away; stop() ends a turn that
is running; a rate limit is a RunnerLimited that says when it lifts, followed by that turn's
TurnEnded; secrets given as environment never reach an argv; and a runner that cannot install or
sign in itself says that, instead of pretending it did.
"""

# System Imports
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from support.runners import FakeRunner, FakeRunnerOptions, RecordingShell, Sessions, wait_for_stop
from udl.errors import PluginError
from udl.hosts.shell import Host, LocalShell
from udl.plugins.requirements import Requirement
from udl.plugins.runners.base import (
    AgentEvent,
    AnAgentRunner,
    RunnerHealth,
    RunnerLimited,
    RunnerOptions,
    SessionRef,
    SessionSpec,
    TurnEnded,
    TurnReason,
)

type Build = Callable[[RecordingShell, Sessions], AnAgentRunner]
"""A runner of a plugin class, made with a shell and a session store."""


@dataclass(frozen=True)
class Case:
    """A runner plugin, the options it is made with, and how to make one."""

    plugin: type[AnAgentRunner]
    options: RunnerOptions
    shell: RecordingShell
    """The host's shell the case's runners act through (it records every argv)."""
    store: Sessions
    """The sessions the case's runners share (a session outlives a runner object)."""
    build: Build

    def make(self) -> AnAgentRunner:
        """Another runner of this plugin, on the same host and with the same sessions."""
        return self.build(self.shell, self.store)


def _case(plugin: type[AnAgentRunner], options: RunnerOptions, build: Build) -> Case:
    shell = RecordingShell(LocalShell(Host.local(str(Path("/tmp")))))  # noqa: S108 - (named only)
    return Case(plugin=plugin, options=options, shell=shell, store=Sessions(), build=build)


def _fake(shell: RecordingShell, store: Sessions) -> AnAgentRunner:
    return FakeRunner(
        FakeRunnerOptions(),
        shell=shell,
        clock=FakeClock(),
        script={"wait": wait_for_stop},
        store=store,
    )


def _fake_case() -> Case:
    return _case(FakeRunner, FakeRunnerOptions(), _fake)


CASES: dict[str, Callable[[], Case]] = {
    "fake": _fake_case,
}
"""Each plugin's case, made for each test (no two tests share a shell or a session store)."""


@pytest.fixture(params=sorted(CASES))
def case(request: pytest.FixtureRequest) -> Case:
    return CASES[request.param]()


@pytest.fixture
def runner(case: Case) -> AnAgentRunner:
    return case.make()


@pytest.fixture
def spec(tmp_path: Path) -> SessionSpec:
    return SessionSpec(cwd=str(tmp_path), model="test-model", instructions="Be brief.")


async def _turn(runner: AnAgentRunner, session: SessionRef, prompt: str) -> list[AgentEvent]:
    return [event async for event in runner.send(session, prompt)]


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
async def test_a_prompt_gives_agent_events_and_ends_in_exactly_one_turn_ended(
    runner: AnAgentRunner, spec: SessionSpec
) -> None:
    session = await runner.start(spec)

    events = await _turn(runner, session, "hello")

    assert events
    assert all(isinstance(event, AgentEvent) for event in events)
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE
    assert sum(isinstance(event, TurnEnded) for event in events) == 1


@pytest.mark.asyncio
async def test_a_session_resumes_by_id_after_the_runner_object_is_thrown_away(
    case: Case, spec: SessionSpec
) -> None:
    session = await case.make().start(spec)

    events = await _turn(case.make(), session, "again")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE
    assert sum(isinstance(event, TurnEnded) for event in events) == 1


@pytest.mark.asyncio
async def test_stop_ends_a_turn_that_is_running(runner: AnAgentRunner, spec: SessionSpec) -> None:
    session = await runner.start(spec)
    reading = asyncio.create_task(_turn(runner, session, "wait"))
    await asyncio.sleep(0.05)  # (let the turn start)

    await runner.stop(session)

    events = await reading
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED
    assert sum(isinstance(event, TurnEnded) for event in events) == 1


@pytest.mark.asyncio
async def test_a_rate_limit_is_a_runner_limited_that_says_when_it_lifts(
    runner: AnAgentRunner, spec: SessionSpec
) -> None:
    limited = spec.model_copy(update={"env": {"FAKE_HARNESS_LIMITED": "1"}})
    session = await runner.start(limited)

    events = await _turn(runner, session, "hello")

    [said] = [event for event in events if isinstance(event, RunnerLimited)]
    assert said.until is not None
    assert said.message
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.LIMITED
    assert sum(isinstance(event, TurnEnded) for event in events) == 1


@pytest.mark.asyncio
async def test_secrets_given_as_environment_never_reach_an_argv(
    case: Case, spec: SessionSpec
) -> None:
    secret = "s3cret-never-in-an-argv"  # noqa: S105 - (a test s own)
    spec = spec.model_copy(update={"secret_env": {"UDL_TEST_TOKEN": SecretStr(secret)}})

    session = await case.make().start(spec)
    await _turn(case.make(), session, "hello")

    # (It reached the harness — through the shell's secret environment or the fake's own — and it
    # is in no argv: a runner that loses it, or puts it in an argument, fails either way.)
    passed = [
        env for env in (*case.shell.secret_envs, *case.store.secret_envs) if secret in env.values()
    ]
    assert passed
    assert all(secret not in " ".join(argv) for argv in case.shell.calls)


@pytest.mark.asyncio
async def test_a_stopped_session_says_so_rather_than_hanging(
    runner: AnAgentRunner, spec: SessionSpec
) -> None:
    session = await runner.start(spec)
    await runner.stop(session)
    await runner.stop(session)  # (Stopping twice is not an error.)

    events = await _turn(runner, session, "again")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED


@pytest.mark.asyncio
async def test_a_runner_that_cannot_install_or_sign_in_says_so(runner: AnAgentRunner) -> None:
    with pytest.raises(PluginError, match="cannot be installed"):
        async for _ in runner.install(None):
            pass

    with pytest.raises(PluginError, match="cannot sign in"):
        await runner.login()
