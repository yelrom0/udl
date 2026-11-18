"""
--------------------------
- test_claude_code.py -
--------------------------

The `claude-code` runner: a turn is one `claude -p --output-format stream-json --verbose` process,
with `--session-id` on the first turn and `--resume` after it, the brief and the deny list in two
files written through the host's shell (0600, in the worktree's `.udl/`), and the MCP token only in
the MCP config file. Its stream's JSON lines become AgentEvents: what it said, the tools it ran,
what a turn cost, and how it ended (done, an error, or a limit). `health` reads `--version` and
`auth status`, and never opens Claude Code's own credential files.
"""

# System Imports
import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support.clock import FakeClock
from udl.errors import PluginFailed, ToolUnavailable
from udl.hosts.shell import Host, Interaction, LocalShell
from udl.plugins.requirements import Requirement
from udl.plugins.runners.base import (
    AgentEvent,
    McpEndpoint,
    RunnerLimited,
    SessionRef,
    SessionSpec,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnReason,
    Usage,
)
from udl.plugins.runners.claude_code import ClaudeCode, ClaudeCodeOptions
from udl.process import CancelToken, ProcessResult


class FakeShell:
    """A host's shell, recording what it is asked to run and answering what a test scripts."""

    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(self, output: Sequence[bytes] = ()) -> None:
        self.host = Host.local("/w")
        self.output = list(output)
        self.runs: list[tuple[list[str], bytes | None]] = []
        self.streams: list[tuple[list[str], bytes | None, Mapping[str, str]]] = []
        self.files: dict[str, str] = {}
        self.answers: dict[str, str] = {}

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
        self.runs.append((list(argv), stdin))
        if argv[0] == "sh" and "umask 077" in argv[2]:
            # (A file written through the shell: its path is the last argument, its content the
            # stdin.)
            self.files[argv[-1]] = (stdin or b"").decode()
            return ProcessResult(returncode=0, stdout="", stderr="")
        if argv[0] == "sh" and "info/exclude" in argv[2]:
            return ProcessResult(returncode=0, stdout="", stderr="")
        answer = self.answers.get(" ".join(argv[1:]), "")
        return ProcessResult(returncode=0, stdout=answer, stderr="")

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
        self.streams.append((list(argv), stdin, dict(secret_env or {})))

        async def chunks() -> AsyncIterator[bytes]:
            for chunk in self.output:
                yield chunk

        return chunks()

    async def interact(self, *args: object, **kwargs: object) -> Interaction:
        raise NotImplementedError

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return nullcontext(f"http://127.0.0.1:{port}")


def _stream(*lines: object) -> list[bytes]:
    """What `claude` prints: one JSON object a line, as bytes."""
    return [(json.dumps(line) + "\n").encode() for line in lines]


INIT = {"type": "system", "subtype": "init", "model": "opus", "session_id": "s"}


def _said(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _tool(name: str, **fields: object) -> dict[str, object]:
    """A tool_use item, as the CLI says one."""
    return {"type": "tool_use", "name": name, **fields}


def _result(**fields: object) -> dict[str, object]:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "total_cost_usd": 0.01,
        **fields,
    }


@pytest.fixture
def spec(tmp_path: Path) -> SessionSpec:
    return SessionSpec(
        cwd=str(tmp_path),
        model="opus",
        instructions="Be brief.",
        deny=["Bash(git push:*)"],
        mcp=McpEndpoint(
            url="http://127.0.0.1:8088/mcp",
            token=SecretStr("tok-123"),
        ),
    )


def _runner(shell: FakeShell, **options: object) -> ClaudeCode:
    return ClaudeCode(
        ClaudeCodeOptions(**options),  # pyright: ignore[reportArgumentType]  (the test's options)
        shell=shell,
        clock=FakeClock(),
    )


async def _turn(runner: ClaudeCode, session: SessionRef, prompt: str) -> list[AgentEvent]:
    return [event async for event in runner.send(session, prompt)]


async def test_a_first_turn_is_one_claude_process_with_its_session_id(spec: SessionSpec) -> None:
    shell = FakeShell(_stream(INIT, _said("hi"), _result()))
    runner = _runner(shell)
    session = await runner.start(spec)

    await _turn(runner, session, "hello")

    [(argv, stdin, _)] = shell.streams
    assert argv[:5] == ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    assert argv[argv.index("--model") : argv.index("--model") + 2] == ["--model", "opus"]
    assert argv[argv.index("--session-id") :][:2] == ["--session-id", session.id]
    assert "--resume" not in argv
    assert stdin == b"hello"


async def test_a_later_turn_resumes_the_session(spec: SessionSpec) -> None:
    shell = FakeShell(_stream(_result()))
    runner = _runner(shell)
    session = await runner.start(spec)

    await _turn(runner, session, "one")
    await _turn(runner, session, "two")

    resumed = shell.streams[1][0]
    assert resumed[resumed.index("--resume") :][:2] == ["--resume", session.id]
    assert "--session-id" not in shell.streams[1][0]


async def test_the_brief_and_the_deny_list_are_written_through_the_shell(
    spec: SessionSpec, tmp_path: Path
) -> None:
    shell = FakeShell(_stream(_result()))
    runner = _runner(shell)

    await runner.start(spec)

    brief = shell.files[str(tmp_path / ".udl" / "system-prompt.md")]
    settings = json.loads(shell.files[str(tmp_path / ".udl" / "settings.json")])
    assert brief == "Be brief."
    assert settings == {"permissions": {"deny": ["Bash(git push:*)"]}}
    # (Written 0600 through the shell, never copied from the hub's disk.)
    assert any(argv[2].startswith("umask 077") for argv, _ in shell.runs)


async def test_the_token_is_only_in_the_mcp_config_file(spec: SessionSpec, tmp_path: Path) -> None:
    shell = FakeShell(_stream(_result()))
    runner = _runner(shell)

    await runner.start(spec)

    mcp = json.loads(shell.files[str(tmp_path / ".udl" / "mcp.json")])
    assert mcp["mcpServers"]["udl"]["url"] == "http://127.0.0.1:8088/mcp"
    assert "tok-123" in json.dumps(mcp)
    for argv, stdin in shell.runs:
        assert "tok-123" not in " ".join(argv)
        if not argv[-1].endswith("mcp.json"):  # (the file itself is where it goes)
            assert "tok-123" not in (stdin or b"").decode()


async def test_the_stream_becomes_events_text_tools_usage_and_the_end(
    spec: SessionSpec,
) -> None:
    shell = FakeShell(
        _stream(
            INIT,
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Looking."},
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        },
                    ]
                },
            },
            {
                "type": "user",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "t1"}]},
            },
            _result(),
        )
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert events[0] == TextDelta(text="Looking.")
    assert events[1] == ToolStarted(name="Bash", summary="ls")
    assert events[2] == ToolFinished(name="Bash", ok=True)
    usages = [event for event in events if isinstance(event, Usage)]
    assert [(usage.input_tokens, usage.output_tokens) for usage in usages] == [(10, 20)]
    assert usages[0].cost is not None
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE


async def test_a_real_result_line_is_read_whatever_else_it_says(spec: SessionSpec) -> None:
    # (Claude Code 2.1.306's own result line: its usage holds nested objects, and there is much
    # else in the line that udl does not read.)
    shell = FakeShell(
        _stream(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Hello!",
                "duration_ms": 874,
                "total_cost_usd": 0.00326847,
                "usage": {
                    "input_tokens": 2,
                    "cache_creation_input_tokens": 15776,
                    "cache_read_input_tokens": 8857,
                    "output_tokens": 49,
                    "output_tokens_details": {"thinking_tokens": 0},
                    "server_tool_use": {"web_search_requests": 0},
                    "iterations": [{"input_tokens": 2, "type": "message"}],
                },
                "modelUsage": {"claude-haiku-5-5": {"costUSD": 0.00326847}},
                "permission_denials": [],
                "terminal_reason": "completed",
            }
        )
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    [usage] = [event for event in events if isinstance(event, Usage)]
    assert (usage.input_tokens, usage.output_tokens) == (2, 49)
    assert str(usage.cost) == "0.00326847"
    assert events[-1] == TurnEnded(reason=TurnReason.DONE, text="Hello!")


async def test_an_unknown_line_is_skipped_never_fatal(spec: SessionSpec) -> None:
    shell = FakeShell(_stream({"type": "something-new"}, _said("ok"), _result()))
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert [event for event in events if isinstance(event, TextDelta)] == [TextDelta(text="ok")]


async def test_a_failed_turn_says_why(spec: SessionSpec) -> None:
    shell = FakeShell(
        _stream(_result(subtype="error_during_execution", is_error=True, result="it broke"))
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "it broke" in events[-1].text


async def test_a_usage_limit_becomes_a_limit_with_its_reset_time(spec: SessionSpec) -> None:
    shell = FakeShell(
        _stream(
            _result(
                subtype="error_limit_reached",
                is_error=True,
                result="Your limit is used up. It resets at 2026-11-20T03:00:00Z.",
            )
        )
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    limited = [event for event in events if isinstance(event, RunnerLimited)]
    assert limited
    assert limited[0].until is not None
    assert limited[0].until.isoformat().startswith("2026-11-20T03:00:00")
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.LIMITED


async def test_health_reads_the_version_and_the_auth_status(spec: SessionSpec) -> None:
    shell = FakeShell()
    shell.answers = {
        "--version": "2.1.306 (Claude Code)\n",
        "auth status": json.dumps(
            {
                "loggedIn": True,
                "authMethod": "oauth",
                "subscriptionType": "max",
                "email": "someone@example.invalid",
            }
        ),
    }
    runner = _runner(shell)

    health = await runner.health()

    assert health.installed
    assert health.signed_in
    assert health.version == "2.1.306 (Claude Code)"
    assert health.account == "someone@example.invalid"
    # (udl never opens Claude Code's own credential files.)
    assert not any(".claude" in " ".join(argv) for argv, _ in shell.runs)


async def test_health_says_a_signed_out_harness(spec: SessionSpec) -> None:
    shell = FakeShell()
    shell.answers = {"--version": "2.1.306", "auth status": json.dumps({"loggedIn": False})}
    runner = _runner(shell)

    health = await runner.health()

    assert health.installed
    assert not health.signed_in


async def test_health_says_a_harness_that_cannot_be_run(spec: SessionSpec) -> None:
    class Broken(FakeShell):
        async def run(self, *args: object, **kwargs: object) -> ProcessResult:
            raise ToolUnavailable("claude is not on this host")

    health = await _runner(Broken()).health()

    assert not health.installed
    assert "not on this host" in (health.detail or "")


async def test_a_session_it_never_started_ends_with_an_error(spec: SessionSpec) -> None:
    runner = _runner(FakeShell(_stream(_result())))
    stranger = SessionRef(runner="claude-code", id="nope", cwd="/w", host="local")

    events = await _turn(runner, stranger, "go")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR


async def test_a_message_that_is_just_text_and_a_result_without_a_tool_it_knows(
    spec: SessionSpec,
) -> None:
    shell = FakeShell(
        _stream(
            {"type": "assistant", "message": {"content": "plain"}},
            {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}},
            _result(),
        )
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert events[0] == TextDelta(text="plain")
    assert events[1] == ToolFinished(name="tool", ok=True)
    assert events[2] == ToolStarted(name="Read", summary="")


async def test_a_tool_is_summarised_by_what_it_is_given(spec: SessionSpec) -> None:
    shell = FakeShell(
        _stream(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        _tool("Bash", input={"command": "make test"}),
                        _tool("Grep", input={"pattern": "x"}),
                        _tool("Odd", input={"b": 1, "a": 2}),
                        _tool("Nothing"),
                        _tool("Empty", input={}),
                    ]
                },
            },
            _result(),
        )
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    started = [
        event for event in await _turn(runner, session, "go") if isinstance(event, ToolStarted)
    ]

    assert [event.summary for event in started] == ["make test", "x", "a, b", "", ""]


async def test_a_limit_that_says_no_reset_time_has_none(spec: SessionSpec) -> None:
    shell = FakeShell(
        _stream(_result(subtype="error_limit_reached", is_error=True, result="out of quota"))
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    [limited] = [event for event in events if isinstance(event, RunnerLimited)]
    assert limited.until is None
    assert limited.message == "out of quota"


async def test_a_limit_whose_time_cannot_be_read_has_none(spec: SessionSpec) -> None:
    shell = FakeShell(
        _stream(
            _result(
                subtype="error_limit_reached",
                is_error=True,
                result="resets at 2026-13-45T99:99:99Z",
            )
        )
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    [limited] = [event for event in events if isinstance(event, RunnerLimited)]
    assert limited.until is None


async def test_an_mcp_endpoint_without_a_token_is_pointed_at_without_one(
    spec: SessionSpec, tmp_path: Path
) -> None:
    shell = FakeShell(_stream(_result()))
    runner = _runner(shell)

    await runner.start(SessionSpec(cwd=spec.cwd, mcp=McpEndpoint(url="http://udl/mcp")))

    mcp = json.loads(shell.files[str(tmp_path / ".udl" / "mcp.json")])
    assert mcp["mcpServers"]["udl"] == {"type": "http", "url": "http://udl/mcp"}


@pytest.mark.parametrize(
    ("status", "signed_in", "account"),
    [
        ("not json", False, None),
        ("[1, 2]", False, None),
        (json.dumps({"loggedIn": True, "email": 3}), False, None),
    ],
)
async def test_health_reads_whatever_the_auth_status_is(
    status: str, signed_in: bool, account: str | None
) -> None:
    shell = FakeShell()
    shell.answers = {"--version": "2.1.306", "auth status": status}

    health = await _runner(shell).health()

    assert (health.signed_in, health.account) == (signed_in, account)


async def test_health_says_a_harness_with_no_version() -> None:
    shell = FakeShell()
    shell.answers = {"--version": "", "auth status": "{}"}

    health = await _runner(shell).health()

    assert not health.installed


async def test_a_bare_spec_runs_the_defaults_with_no_files_but_its_settings(
    spec: SessionSpec, tmp_path: Path
) -> None:
    shell = FakeShell(_stream(_result()))
    runner = _runner(shell)
    session = await runner.start(SessionSpec(cwd=str(tmp_path)))

    await _turn(runner, session, "go")

    [(argv, _, _)] = shell.streams
    assert "--model" not in argv
    assert "--append-system-prompt-file" not in argv
    assert "--mcp-config" not in argv
    assert argv[argv.index("--permission-mode") : argv.index("--permission-mode") + 2] == [
        "--permission-mode",
        "acceptEdits",
    ]
    assert set(shell.files) == {str(tmp_path / ".udl" / "settings.json")}


async def test_a_turn_that_never_says_how_it_ended_ends_as_an_error(spec: SessionSpec) -> None:
    shell = FakeShell(_stream(INIT, _said("hi")))
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "without saying how" in events[-1].text


async def test_a_line_udl_cannot_read_at_all_is_skipped(spec: SessionSpec) -> None:
    shell = FakeShell([b"not json at all\n", b"\n", *(_stream(_result()))])
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert [type(event) for event in events] == [Usage, TurnEnded]


async def test_a_turn_whose_process_cannot_start_says_why(spec: SessionSpec) -> None:
    class Gone(FakeShell):
        def stream(self, *args: object, **kwargs: object) -> AsyncIterator[bytes]:
            async def chunks() -> AsyncIterator[bytes]:
                raise PluginFailed("claude: not found")
                yield b""

            return chunks()

    runner = _runner(Gone())
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "not found" in events[-1].text


async def test_a_turn_nobody_reads_to_the_end_stops_its_process(spec: SessionSpec) -> None:
    class Slow(FakeShell):
        def stream(self, *args: object, **kwargs: object) -> AsyncIterator[bytes]:
            async def chunks() -> AsyncIterator[bytes]:
                yield (json.dumps(_said("one")) + "\n").encode()
                await asyncio.sleep(30)

            return chunks()

    runner = _runner(Slow())
    session = await runner.start(spec)
    turn = runner.send(session, "go")

    first = await anext(turn)
    await turn.aclose()

    assert first == TextDelta(text="one")


async def test_lines_that_say_nothing_udl_reads(spec: SessionSpec) -> None:
    shell = FakeShell(
        _stream(
            {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "x"}]}},
            {"type": "assistant", "message": {}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Odd", "input": "a string"}]},
            },
            {"type": "result", "subtype": "success", "result": "done"},
        )
    )
    runner = _runner(shell)
    session = await runner.start(spec)

    events = await _turn(runner, session, "go")

    assert events == [
        ToolStarted(name="Odd", summary=""),
        TurnEnded(reason=TurnReason.DONE, text="done"),
    ]


async def test_its_models_are_the_aliases_it_is_given() -> None:
    runner = _runner(FakeShell())

    assert [model.id for model in await runner.models()] == ["opus", "sonnet", "haiku"]
    assert [model.id for model in await _runner(FakeShell(), models=["a"]).models()] == ["a"]


async def test_a_stopped_session_ends_its_turn_as_stopped(spec: SessionSpec) -> None:
    shell = FakeShell(_stream(_result()))
    runner = _runner(shell)
    session = await runner.start(spec)
    await runner.stop(session)

    events = await _turn(runner, session, "go")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED


async def test_a_turn_that_is_stopped_while_it_runs_ends(spec: SessionSpec) -> None:
    class Slow(FakeShell):
        def stream(self, *args: object, **kwargs: object) -> AsyncIterator[bytes]:
            async def chunks() -> AsyncIterator[bytes]:
                await asyncio.sleep(30)
                yield b""

            return chunks()

    runner = _runner(Slow())
    session = await runner.start(spec)
    turn = asyncio.create_task(_turn(runner, session, "go"))
    await asyncio.sleep(0.05)
    await runner.stop(session)

    events = await asyncio.wait_for(turn, timeout=5)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED


async def test_the_worktree_s_exclude_lists_the_udl_folder(
    spec: SessionSpec, tmp_path: Path
) -> None:
    shell = FakeShell(_stream(_result()))
    runner = _runner(shell)

    await runner.start(spec)

    assert any("info/exclude" in argv[2] for argv, _ in shell.runs)


async def test_a_local_shell_runs_the_fake_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (The contract runs this over fake_harness.py; here: the same, through a real LocalShell, so
    # the flags and the stream-json are the ones a real claude sees.)
    harness = Path(__file__).resolve().parents[3] / "support" / "fake_harness.py"  # noqa: ASYNC240
    runner = ClaudeCode(
        ClaudeCodeOptions(cli=[str(Path("/usr/bin/env").parent / "python3"), str(harness)]),
        shell=LocalShell(Host.local(str(tmp_path))),
        clock=FakeClock(),
    )
    session = await runner.start(SessionSpec(cwd=str(tmp_path)))

    events = [event async for event in runner.send(session, "hello")]

    assert TextDelta(text="You said: hello") in events
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE
