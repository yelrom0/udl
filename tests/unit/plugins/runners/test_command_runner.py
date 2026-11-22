"""
--------------------------
- test_command_runner.py -
--------------------------

The `command` runner: any CLI as a harness. Its argv is a template (`{model}`, `{session}`,
`{cwd}`) with the prompt on stdin; its output lines are TextDeltas (plain text, or one JSON object
a line); exit 0 ends the turn as done and anything else ends it as an error with what the program
said; a program that is not in `[orch] allowed_commands` is refused; and a stopped session ends
its turn as stopped.
"""

# System Imports
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.runners import RecordingShell
from udl.errors import NotAllowed
from udl.hosts.shell import Host, LocalShell
from udl.plugins.runners.base import (
    AgentEvent,
    RunnerLimited,
    SessionRef,
    SessionSpec,
    TextDelta,
    TurnEnded,
    TurnReason,
)
from udl.plugins.runners.command import (
    CommandOutput,
    CommandRunner,
    CommandRunnerOptions,
    CommandSessions,
)

ECHO = "import sys; print(sys.stdin.read().upper())"
"""A tiny harness: it says what it was given, in capitals."""


@pytest.fixture
def cwd(tmp_path: Path) -> str:
    """A folder a program can run in."""
    return str(tmp_path)


def _runner(
    cwd: str,
    *argv: str,
    allowed: bool = True,
    sessions: CommandSessions | dict[str, SessionSpec] | None = None,
    **options: object,
) -> CommandRunner:
    program = sys.executable
    return CommandRunner(
        CommandRunnerOptions(
            argv=[program, "-c", *argv],
            **options,  # pyright: ignore[reportArgumentType]  (the test's own options)
        ),
        shell=LocalShell(Host.local(cwd)),
        clock=FakeClock(),
        allowed=(program,) if allowed else (),
        sessions=sessions,
    )


def _session(cwd: str) -> SessionSpec:
    return SessionSpec(cwd=cwd, model="m")


async def _turn(runner: CommandRunner, session: SessionRef, prompt: str) -> list[AgentEvent]:
    return [event async for event in runner.send(session, prompt)]


async def test_the_prompt_goes_on_stdin_and_its_lines_are_text_deltas(cwd: str) -> None:
    runner = _runner(cwd, ECHO)
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "hello there")

    assert events[0] == TextDelta(text="HELLO THERE")
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE


async def test_a_turn_gets_its_options_turn_within(cwd: str) -> None:
    """A turn is one program, and it is given how long it may run: never the shell's 30 s
    default, which cut a real turn off."""
    shell = RecordingShell(LocalShell(Host.local(cwd)))
    runner = CommandRunner(
        CommandRunnerOptions(argv=[sys.executable, "-c", ECHO], turn_within=timedelta(minutes=20)),
        shell=shell,
        clock=FakeClock(),
        allowed=(sys.executable,),
    )
    session = await runner.start(_session(cwd))

    await _turn(runner, session, "hello")

    assert shell.run_withins == [timedelta(minutes=20)]


async def test_a_turn_that_runs_past_its_time_ends_as_an_error(cwd: str) -> None:
    """A turn that runs past its `turn_within` is ended by the runner: the turn says so, and
    nothing raises out of send()."""
    runner = _runner(cwd, "import time; time.sleep(5)", turn_within=timedelta(seconds=0.3))
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "hello")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert events[-1].text == "The turn ran past its 0.3 seconds."


async def test_the_argv_is_a_template_of_the_model_the_session_and_the_folder(cwd: str) -> None:
    runner = CommandRunner(
        CommandRunnerOptions(
            argv=[
                sys.executable,
                "-c",
                "import sys; print('|'.join(sys.argv[1:]))",
                "{model}",
                "{session}",
                "{cwd}",
            ],
        ),
        shell=LocalShell(Host.local(cwd)),
        clock=FakeClock(),
        allowed=(sys.executable,),
    )
    session = await runner.start(SessionSpec(cwd=cwd, model="fast"))

    [line, _] = await _turn(runner, session, "")

    assert line == TextDelta(text=f"fast|{session.id}|{cwd}")


async def test_a_later_turn_uses_the_resume_argv_when_there_is_one(cwd: str) -> None:
    runner = CommandRunner(
        CommandRunnerOptions(
            argv=[sys.executable, "-c", "print('first')"],
            resume_argv=[sys.executable, "-c", "print('resumed {session}')"],
        ),
        shell=LocalShell(Host.local(cwd)),
        clock=FakeClock(),
        allowed=(sys.executable,),
    )
    session = await runner.start(SessionSpec(cwd=cwd))

    first = await _turn(runner, session, "one")
    second = await _turn(runner, session, "two")

    assert first[0] == TextDelta(text="first")
    assert second[0] == TextDelta(text=f"resumed {session.id}")


async def test_jsonl_output_is_read_as_one_object_a_line(cwd: str) -> None:
    runner = _runner(
        cwd,
        "import json; print(json.dumps({'text': 'from json'})); print('not json')",
        output=CommandOutput.JSONL,
    )
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "")

    assert events[0] == TextDelta(text="from json")
    assert events[1] == TextDelta(text="not json")  # (a line it cannot read is said, not fatal)


async def test_a_jsonl_turns_answer_is_what_its_last_line_said(cwd: str) -> None:
    runner = _runner(
        cwd,
        "import json; print(json.dumps({'type': 'result', 'result': 'the answer'}))",
        output=CommandOutput.JSONL,
    )
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].text == "the answer"  # (not the raw JSON line)


async def test_a_limit_line_says_when_it_lifts_and_ends_the_turn_as_limited(cwd: str) -> None:
    runner = _runner(
        cwd,
        "import json; print(json.dumps({'limited': True, 'resets_at': 1800000000,"
        " 'result': 'Your limit resets at 1800000000|1800000000.'}))",
        output=CommandOutput.JSONL,
    )
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "")

    [said] = [event for event in events if isinstance(event, RunnerLimited)]
    assert said.until == datetime(2027, 1, 15, 8, 0, tzinfo=UTC)
    assert "resets at 1800000000" in said.message
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.LIMITED
    assert sum(isinstance(event, TurnEnded) for event in events) == 1


async def test_a_resume_argv_whose_program_is_not_allowed_is_refused(cwd: str) -> None:
    runner = _runner(cwd, "print('first')", resume_argv=["sh", "-c", "echo sneaky"])

    with pytest.raises(NotAllowed, match="sh"):
        await runner.start(_session(cwd))

    health = await runner.health()
    assert not health.installed
    assert "sh" in (health.detail or "")


async def test_health_says_when_the_resume_program_is_not_on_the_host(cwd: str) -> None:
    missing = "not-a-program-anywhere"
    runner = CommandRunner(
        CommandRunnerOptions(
            argv=[sys.executable, "-c", "print('first')"], resume_argv=[missing, "--resume"]
        ),
        shell=LocalShell(Host.local(cwd)),
        clock=FakeClock(),
        allowed=(sys.executable, missing),  # (allowed, but not on this host)
    )

    health = await runner.health()

    assert not health.installed
    assert missing in (health.detail or "")


async def test_a_second_runner_runs_the_resume_argv_of_a_session_it_resumes(cwd: str) -> None:
    sessions = CommandSessions()
    first = _runner(cwd, "print('first')", sessions=sessions)
    session = await first.start(_session(cwd))
    opened = await _turn(first, session, "")
    assert isinstance(opened[-1], TurnEnded)
    assert opened[-1].text == "first"

    second = _runner(
        cwd,
        "print('first')",
        resume_argv=[sys.executable, "-c", "print('resumed')"],
        sessions=sessions,
    )
    events = await _turn(second, session, "")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].text == "resumed"


async def test_a_second_runner_resumes_the_sessions_it_is_given(cwd: str) -> None:
    sessions: dict[str, SessionSpec] = {}
    first = _runner(cwd, "print('first')", sessions=sessions)
    session = await first.start(_session(cwd))

    second = _runner(cwd, "print('second')", sessions=sessions)
    events = await _turn(second, session, "")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE
    assert events[-1].text == "second"


async def test_a_failing_command_ends_the_turn_as_an_error_with_what_it_said(cwd: str) -> None:
    runner = _runner(cwd, "import sys; print('it broke', file=sys.stderr); sys.exit(3)")
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "it broke" in events[-1].text


async def test_a_program_that_is_not_allowed_is_refused(cwd: str) -> None:
    runner = _runner(cwd, ECHO, allowed=False)

    with pytest.raises(NotAllowed, match="allowed_commands"):
        await runner.start(_session(cwd))


async def test_its_health_says_why_it_cannot_run(cwd: str) -> None:
    refused = _runner(cwd, ECHO, allowed=False)
    missing = CommandRunner(
        CommandRunnerOptions(argv=["udl-no-such-program"]),
        shell=LocalShell(Host.local(cwd)),
        clock=FakeClock(),
        allowed=("udl-no-such-program",),
    )
    there = _runner(cwd, ECHO)

    assert not (await refused.health()).installed
    assert "allowed_commands" in ((await refused.health()).detail or "")
    assert not (await missing.health()).installed
    assert (await there.health()).installed


async def test_a_stopped_session_ends_its_turn_as_stopped(cwd: str) -> None:
    runner = _runner(cwd, "import time; time.sleep(5)")
    session = await runner.start(_session(cwd))
    await runner.stop(session)

    events = await _turn(runner, session, "")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED


async def test_a_turn_it_is_told_to_stop_ends_as_stopped(cwd: str) -> None:
    runner = _runner(cwd, "import time; time.sleep(30)")
    session = await runner.start(_session(cwd))

    turn = asyncio.create_task(_turn(runner, session, ""))
    await asyncio.sleep(0.2)  # (the program is running)
    await runner.stop(session)
    events = await turn

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED


async def test_a_runner_with_no_argv_says_so(cwd: str) -> None:
    runner = CommandRunner(
        CommandRunnerOptions(), shell=LocalShell(Host.local(cwd)), clock=FakeClock()
    )

    assert (await runner.health()).installed is False
    assert "No argv" in ((await runner.health()).detail or "")
    with pytest.raises(NotAllowed, match="no argv"):
        await runner.start(_session(cwd))


async def test_an_empty_line_is_not_an_event(cwd: str) -> None:
    runner = _runner(cwd, "print('one'); print(); print('two')")
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "")

    assert [event for event in events if isinstance(event, TextDelta)] == [
        TextDelta(text="one"),
        TextDelta(text="two"),
    ]


async def test_a_session_it_never_started_ends_with_an_error(cwd: str) -> None:
    runner = _runner(cwd, ECHO)
    stranger = SessionRef(runner="command", id="nope", cwd=cwd, host="local")

    events = await _turn(runner, stranger, "")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR


async def test_its_models_are_the_ones_it_is_given(cwd: str) -> None:
    runner = _runner(cwd, ECHO, models=["small", "large"])

    models = await runner.models()

    assert [model.id for model in models] == ["small", "large"]


async def test_it_says_its_name_and_that_it_needs_nothing_installed(cwd: str) -> None:
    assert CommandRunner.name == "command"
    assert CommandRunner.requires == ()
    assert CommandRunner.options_model is CommandRunnerOptions


async def test_a_json_line_it_cannot_read_is_not_fatal(cwd: str) -> None:
    runner = _runner(
        cwd, 'print(\'{"text": "a"\'); print(\'{"other": 1}\')', output=CommandOutput.JSONL
    )
    session = await runner.start(_session(cwd))

    events = await _turn(runner, session, "")

    assert [event for event in events if isinstance(event, TextDelta)] == [
        TextDelta(text='{"text": "a"'),
        TextDelta(text='{"other": 1}'),
    ]
