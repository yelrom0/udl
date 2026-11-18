"""
----------------------
- test_fake_runner.py -
----------------------

FakeRunner and its harness in a file: a scripted turn gives its events and ends with a TurnEnded;
a prompt with no script gets the options' answer; a script may call back into udl as an agent's
tool call would; a session it never started, or one it stopped, ends with a reason rather than
hanging; and fake_harness.py answers `--version`, `auth status` and a turn as the harnesses do.
"""

# System Imports
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.runners import FakeRunner, FakeRunnerOptions, count_sites
from udl.core.models import SiteList
from udl.db.models import SiteQuery
from udl.hosts.shell import Host, LocalShell
from udl.plugins.runners.base import (
    AgentEvent,
    SessionRef,
    SessionSpec,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnReason,
)

HARNESS = Path(__file__).resolve().parents[3] / "support" / "fake_harness.py"
"""The fake harness's file (a real runner's tests point their binary at it)."""


@pytest.fixture
def runner(tmp_path: Path) -> FakeRunner:
    return FakeRunner(
        FakeRunnerOptions(), shell=LocalShell(Host.local(str(tmp_path))), clock=FakeClock()
    )


@pytest.fixture
def spec(tmp_path: Path) -> SessionSpec:
    return SessionSpec(cwd=str(tmp_path))


async def test_a_prompt_with_no_script_gets_the_options_answer(runner: FakeRunner) -> None:
    session = await runner.start(SessionSpec(cwd="/w"))

    events = [event async for event in runner.send(session, "anything")]

    assert events[0] == TextDelta(text="Done.")
    assert isinstance(events[-1], TurnEnded)
    assert runner.turns == 1


async def test_a_scripted_turn_gives_its_events(runner: FakeRunner) -> None:
    async def turn(_: FakeRunner) -> AsyncIterator[AgentEvent]:
        yield ToolStarted(name="git", summary="git status")
        yield ToolFinished(name="git", ok=False)

    scripted = FakeRunner(
        FakeRunnerOptions(),
        shell=runner.shell,
        clock=FakeClock(),
        script={"run git": turn},
    )
    session = await scripted.start(SessionSpec(cwd="/w"))

    events = [event async for event in scripted.send(session, "run git")]

    assert [type(event) for event in events] == [ToolStarted, ToolFinished, TurnEnded]


class Sites:
    """udl, as a scripted turn sees it: the one operation count_sites asks for."""

    async def list_sites(self, request: SiteQuery) -> SiteList:
        return SiteList(sites=[])


async def test_a_script_may_ask_udl_as_an_agent_would() -> None:
    scripted = FakeRunner(
        FakeRunnerOptions(),
        shell=LocalShell(Host.local("/w")),
        clock=FakeClock(),
        script={"how many sites?": count_sites},
        service=Sites(),
    )
    session = await scripted.start(SessionSpec(cwd="/w"))

    events = [event async for event in scripted.send(session, "how many sites?")]

    assert isinstance(events[0], ToolStarted)
    assert isinstance(events[1], ToolFinished)
    assert events[1].ok
    assert isinstance(events[2], TextDelta)
    assert events[2].text == "There are 0 sites."


async def test_a_session_outlives_the_runner_that_started_it(runner: FakeRunner) -> None:
    session = await runner.start(SessionSpec(cwd="/w"))
    other = FakeRunner(
        FakeRunnerOptions(), shell=runner.shell, clock=FakeClock(), store=runner.store
    )

    events = [event async for event in other.send(session, "again")]

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE
    assert other.sessions == {session.id: SessionSpec(cwd="/w")}


async def test_a_session_it_never_started_ends_with_an_error(runner: FakeRunner) -> None:
    stranger = SessionRef(runner="fake", id="nope", cwd="/w", host="local")

    events = [event async for event in runner.send(stranger, "hello")]

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR


async def test_it_says_its_sessions_and_how_many_turns_it_was_sent(runner: FakeRunner) -> None:
    session = await runner.start(SessionSpec(cwd="/w", model="m"))

    assert runner.sessions == {session.id: SessionSpec(cwd="/w", model="m")}
    assert runner.turns == 0
    health = await runner.health()
    assert health.installed
    assert health.signed_in
    assert health.detail == "scripted"


async def test_a_script_that_asks_udl_without_one_says_so(runner: FakeRunner) -> None:
    with pytest.raises(RuntimeError, match="no service"):
        _ = runner.service


def test_the_harness_says_its_version() -> None:
    answer = subprocess.run(  # noqa: S603 - this Python, running the test's own script
        [sys.executable, str(HARNESS), "--version"], capture_output=True, text=True, check=True
    )

    assert answer.stdout.strip() == "fake-harness 1.0.0"


def test_the_harness_answers_a_turn_as_the_stream_json_does() -> None:
    answer = subprocess.run(  # noqa: S603 - this Python, running the test's own script
        [sys.executable, str(HARNESS)],
        input="hello",
        capture_output=True,
        text=True,
        check=True,
    )

    lines = [json.loads(line) for line in answer.stdout.splitlines()]
    assert [line["type"] for line in lines] == ["system", "assistant", "result"]
    assert lines[2]["result"] == "You said: hello"
    assert lines[2]["usage"] == {"input_tokens": 3, "output_tokens": 5}


def test_the_harness_can_be_told_to_fail_or_to_be_limited() -> None:
    failed = subprocess.run(  # noqa: S603 - this Python, running the test's own script
        [sys.executable, str(HARNESS)],
        input="hello",
        capture_output=True,
        text=True,
        env={"FAKE_HARNESS_FAILS": "1", "PATH": "/usr/bin:/bin"},
    )
    limited = subprocess.run(  # noqa: S603 - this Python, running the test's own script
        [sys.executable, str(HARNESS)],
        input="hello",
        capture_output=True,
        text=True,
        env={"FAKE_HARNESS_LIMITED": "1", "PATH": "/usr/bin:/bin"},
        check=True,
    )

    assert failed.returncode == 1
    said = json.loads(limited.stdout.splitlines()[-1])
    assert said["limited"] is True
    assert said["resets_at"] == 1_800_000_000
    assert "resets at 1800000000" in said["result"]


def test_the_harness_says_whether_it_is_signed_in() -> None:
    answer = subprocess.run(  # noqa: S603 - this Python, running the test's own script
        [sys.executable, str(HARNESS), "auth", "status"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(answer.stdout)["loggedIn"] is True
