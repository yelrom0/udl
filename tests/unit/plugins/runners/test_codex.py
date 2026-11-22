"""
------------------
- test_codex.py -
------------------

The `codex` runner (25.12): a turn is one `codex exec --json …` process (a first turn with the
worktree, the sandbox and udl's MCP server; a later one `codex exec resume <thread> --json`),
the instructions open the first prompt, and the JSON lines become AgentEvents — what it said, the
tools it ran, what it used, and how it ended. `health` reads `--version` and `login status` and
says whether udl's rules are on the host, and udl never opens Codex's own `auth.json`.
"""

# System Imports
import asyncio
import json
import re
import sys
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import UTC, datetime, timedelta
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
    RunnerHealth,
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
from udl.plugins.runners.codex import Codex, CodexOptions, CodexSessions
from udl.process import CancelToken, ProcessResult

RESET = "2027-01-15T08:00:00Z"
"""When a limit's message says the quota is back."""


class HomeShell(LocalShell):
    """A real local shell that runs as if the home were a test's folder (so a test can write to
    it without touching the real one)."""

    def __init__(self, home: Path) -> None:
        super().__init__(Host.local(str(home)))
        self.home = home
        """The home directory it runs as."""
        self.calls: list[tuple[list[str], bytes | None]] = []
        """Every argv it was asked to run, with what was written to it, in order."""

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
    ) -> ProcessResult:
        self.calls.append((list(argv), stdin))
        return await super().run(
            argv,
            cwd=cwd,
            env={**(env or {}), "HOME": str(self.home)},
            secret_env=secret_env,
            stdin=stdin,
            within=within,
            cancel=cancel,
            check=check,
            tail=tail,
        )


class FakeShell:
    """A host's shell, recording what it is asked to run and answering what a test scripts."""

    requires: ClassVar[tuple[Requirement, ...]] = ()

    def __init__(self, output: Sequence[bytes] = ()) -> None:
        self.host = Host.local("/w")
        self.output = list(output)
        self.runs: list[tuple[list[str], bytes | None]] = []
        self.streams: list[tuple[list[str], bytes | None, Mapping[str, str]]] = []
        self.stream_withins: list[timedelta | None] = []
        """How long each stream it was asked for got (a turn's `within`, never the default)."""
        self.endless = False
        """Whether its stream never ends (so only a turn's own time can)."""
        self.answers: dict[str, ProcessResult] = {}
        self.status: ProcessResult | None = None
        """What `login status` answers (a signed-out CLI exits 1 and says so on stderr)."""
        self.fails: set[str] = set()
        """The commands that fail as a program that is not there."""

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
    ) -> ProcessResult:
        self.runs.append((list(argv), stdin))
        # (A test points the runner at a Python script, so the program is `python <script>`: the
        # command a test keys on is what follows it.)
        skip = 2 if argv and Path(argv[0]).name.startswith("python") else 1
        command = " ".join(argv[skip:])
        if self.status is not None and argv[skip:] == ["login", "status"]:
            result = self.status
        elif command in self.fails:
            raise ToolUnavailable("codex is not on this host")
        else:
            result = self.answers.get(command, ProcessResult(returncode=0, stdout="", stderr=""))
        if check and result.returncode != 0:
            # (What a real shell does: a non-zero exit is a failure the caller must handle, so a
            # test can tell whether the runner asked for it or not.)
            raise PluginFailed(
                f"{Path(argv[0]).name} failed with exit code {result.returncode}"
                f": {result.stderr.strip() or result.stdout.strip()}"
            )
        return result

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
        self.stream_withins.append(within)

        async def chunks() -> AsyncIterator[bytes]:
            # (As a real shell: `within` is how long the whole stream gets.)
            async with asyncio.timeout(within.total_seconds() if within else None):
                if self.endless:
                    await asyncio.sleep(3600)  # pragma: no cover - (the turn is timed)
                for chunk in self.output:
                    yield chunk

        return chunks()

    async def interact(self, *args: object, **kwargs: object) -> Interaction:
        raise NotImplementedError

    def forward(self, port: int) -> AbstractAsyncContextManager[str]:
        return nullcontext(f"http://127.0.0.1:{port}")


def said(*lines: object) -> list[bytes]:
    """What codex prints: one JSON object a line, as bytes."""
    return [(json.dumps(line) + "\n").encode() for line in lines]


def thread(thread_id: str = "thread-1") -> dict[str, object]:
    """The first line of a turn: which thread it is."""
    return {"type": "thread.started", "thread_id": thread_id}


def item(kind: str, **fields: object) -> dict[str, object]:
    """An item of a turn, as codex sends it."""
    return {"type": "item.completed", "item": {"id": "item_1", "type": kind, **fields}}


def turn_done(**usage: int) -> dict[str, object]:
    """The last line of a turn: what it used."""
    return {"type": "turn.completed", "usage": usage}


def runner(shell: FakeShell, **options: object) -> tuple[Codex, CodexOptions]:
    """A codex runner on `shell`, with the fake CLI a test points it at."""
    given = CodexOptions.model_validate({"cli": [sys.executable, str(CODEX)], **options})
    return Codex(given, shell=shell, clock=FakeClock()), given


CODEX = Path(__file__).resolve().parents[3] / "support" / "codex_harness.py"
"""The fake codex CLI: `--version`, `login status`, and `exec`/`exec resume` printing JSON lines."""
FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "codex" / "0.171.0"
"""The installed CLI's own help texts and recorded turns, as 25.12's card asks them kept."""
_RULES_PATH = "~/.codex/rules/udl.rules"
"""Where the rules are (the shell expands the home, so it is the home directory's)."""


async def turn_of(runner_: Codex, session: SessionRef, prompt: str = "hello") -> list[AgentEvent]:
    """One turn's events."""
    return [event async for event in runner_.send(session, prompt)]


def session(spec: SessionSpec | None = None) -> SessionSpec:
    """What a session is started with (a worktree, a model, and the brief)."""
    return spec or SessionSpec(cwd="/w", model="fake-model", instructions="Be brief.")


# ---------------------------------------------------------------------------------------------
# What it is
# ---------------------------------------------------------------------------------------------


async def test_its_health_says_it_is_there_and_signed_in() -> None:
    shell = FakeShell()
    shell.answers = {
        "--version": ProcessResult(returncode=0, stdout="codex-cli 0.171.0\n", stderr=""),
        "login status": ProcessResult(returncode=0, stdout="Logged in using ChatGPT\n", stderr=""),
    }
    given, _ = runner(shell)

    health = await given.health()

    assert health.installed
    assert health.version == "0.171.0"
    assert health.signed_in
    assert health.plan == "chatgpt"


def test_a_login_status_of_both_kinds_is_read() -> None:
    shell = FakeShell()
    shell.answers = {
        "--version": ProcessResult(returncode=0, stdout="codex-cli 0.171.0\n", stderr=""),
        "login status": ProcessResult(
            returncode=0, stdout="Logged in using an API key\n", stderr=""
        ),
    }

    health = asyncio.run(_health_of(shell))

    assert health.signed_in
    assert health.plan == "an api key"


def test_a_signed_out_login_status_is_read_from_stderr() -> None:
    """Signed out, it says so on stderr and exits 1: the runner must read it as "not signed in",
    not as a harness that is not there."""
    shell = FakeShell()
    shell.answers = {
        "--version": ProcessResult(returncode=0, stdout="codex-cli 0.171.0\n", stderr=""),
    }
    shell.status = ProcessResult(returncode=1, stdout="", stderr="Not logged in\n")

    health = asyncio.run(_health_of(shell))

    assert health.installed
    assert health.version == "0.171.0"
    assert not health.signed_in


def test_a_signed_in_status_read_on_stderr_is_signed_in() -> None:
    """The signed-in line is read wherever it is printed: stdout or stderr."""
    shell = FakeShell()
    shell.answers = {
        "--version": ProcessResult(returncode=0, stdout="codex-cli 0.171.0\n", stderr=""),
    }
    shell.status = ProcessResult(returncode=0, stdout="", stderr="Logged in using ChatGPT\n")

    health = asyncio.run(_health_of(shell))

    assert health.signed_in
    assert health.plan == "chatgpt"


def test_a_signed_in_line_that_names_its_key_shows_neither() -> None:
    """An API-key sign-in may print the key itself: the plan is how it signed in, not the key."""
    shell = FakeShell()
    shell.answers = {
        "--version": ProcessResult(returncode=0, stdout="codex-cli 0.171.0\n", stderr=""),
        "login status": ProcessResult(
            returncode=0,
            stdout="Logged in using an API key (sk-proj-abc123)\n",
            stderr="",
        ),
    }

    health = asyncio.run(_health_of(shell))

    assert health.signed_in
    assert health.plan == "an api key"
    assert "sk-proj" not in (health.detail or "")
    assert "sk-proj" not in (health.plan or "")


def test_not_being_signed_in_is_said() -> None:
    shell = FakeShell()
    shell.answers = {
        "--version": ProcessResult(returncode=0, stdout="codex-cli 0.171.0\n", stderr=""),
        "login status": ProcessResult(returncode=0, stdout="Not logged in\n", stderr=""),
    }

    health = asyncio.run(_health_of(shell))

    assert not health.signed_in


async def _health_of(shell: FakeShell) -> RunnerHealth:
    """The health of a runner on `shell`."""
    given, _ = runner(shell)
    return await given.health()


def test_a_program_that_is_not_there_is_not_installed() -> None:
    shell = FakeShell()
    shell.fails = {"--version"}

    health = asyncio.run(_health_of(shell))

    assert not health.installed


def test_its_models_are_the_options() -> None:
    shell = FakeShell()

    given, _ = runner(shell, models=["gpt-5.1-codex", "other"])

    assert [model.id for model in asyncio.run(given.models())] == [
        "gpt-5.1-codex",
        "other",
    ]


# ---------------------------------------------------------------------------------------------
# A turn's process
# ---------------------------------------------------------------------------------------------


async def test_a_turn_gets_its_options_turn_within() -> None:
    """A turn is one process, and it is given how long it may run: never the shell's 30 s
    default, which cut a real turn off."""
    shell = FakeShell(output=said(thread(), item("agent_message", text="hi"), turn_done()))
    given, _ = runner(shell, turn_within=timedelta(minutes=20))
    started = await given.start(session())

    await turn_of(given, started)

    assert shell.stream_withins == [timedelta(minutes=20)]


async def test_a_turn_that_runs_past_its_time_ends_as_an_error() -> None:
    """A turn that runs past its `turn_within` is ended by the runner: the turn says so, and
    nothing raises out of send()."""
    shell = FakeShell()  # (its stream never ends, so only the turn's own time can)
    shell.endless = True
    given, _ = runner(shell, turn_within=timedelta(seconds=0.05))
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "past" in (events[-1].text or "")


async def test_a_first_turn_is_one_process_with_the_worktree_and_the_sandbox() -> None:
    shell = FakeShell(output=said(thread(), item("agent_message", text="hi"), turn_done()))
    given, _ = runner(shell)
    started = await given.start(session())

    await turn_of(given, started)

    [argv, stdin, _] = shell.streams[-1]
    assert argv[:4] == [sys.executable, str(CODEX), "exec", "--json"]
    assert argv[argv.index("--model") + 1] == "fake-model"
    assert argv[argv.index("--cd") + 1] == "/w"
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "-c" in argv
    assert 'approval_policy="never"' in argv
    assert "sandbox_workspace_write.network_access=true" in argv
    assert argv[-1] == "-"
    assert stdin is not None
    assert stdin.decode().startswith("Be brief.")


async def test_a_later_turn_resumes_the_thread_it_started() -> None:
    shell = FakeShell(output=said(thread("thread-9"), turn_done()))
    given, _ = runner(shell)
    started = await given.start(session())
    await turn_of(given, started)

    await turn_of(given, started, "again")

    argv = shell.streams[-1][0]
    assert argv[argv.index("resume") + 1] == "thread-9"
    assert "--cd" not in argv  # (a resume works in the session it resumes)
    assert "-c" in argv
    assert 'model="fake-model"' in argv


async def test_the_instructions_open_the_first_prompt_only() -> None:
    shell = FakeShell(output=said(thread(), turn_done()))
    given, _ = runner(shell)
    started = await given.start(session())
    await turn_of(given, started)

    first = shell.streams[-1][1]
    assert first is not None
    assert first.decode().startswith("Be brief.")

    shell.output = said(thread(), turn_done())
    await turn_of(given, started, "again")

    assert shell.streams[-1][1] is not None
    assert not shell.streams[-1][1].decode().startswith("Be brief.")


async def test_the_mcp_server_is_given_by_key_and_its_token_is_not_in_the_argv() -> None:
    shell = FakeShell(output=said(thread(), turn_done()))
    given, _ = runner(shell)
    started = await given.start(
        session(
            SessionSpec(
                cwd="/w",
                model="fake-model",
                mcp=McpEndpoint(url="http://hub/mcp", token=SecretStr("s3cret-token")),
            )
        )
    )

    await turn_of(given, started)

    argv = " ".join(shell.streams[-1][0])
    assert "mcp_servers.udl.url=" in argv
    assert "mcp_servers.udl.bearer_token_env_var=UDL_MCP_TOKEN" in argv
    assert "s3cret-token" not in argv


# ---------------------------------------------------------------------------------------------
# What its lines are
# ---------------------------------------------------------------------------------------------


async def test_the_lines_become_agent_events() -> None:
    shell = FakeShell(
        output=said(
            thread(),
            {
                "type": "item.completed",
                "item": {
                    "id": "item_0",
                    "type": "error",
                    "message": "Model metadata for `fake-model` not found.",
                },
            },
            {
                "type": "item.started",
                "item": {"id": "item_2", "type": "command_execution", "command": "ls"},
            },
            item("command_execution", status="completed", command="ls"),
            item("agent_message", text="Done."),
            turn_done(input_tokens=10, output_tokens=2, cached_input_tokens=1),
        )
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert [type(event).__name__ for event in events] == [
        "ToolStarted",
        "ToolFinished",
        "TextDelta",
        "Usage",
        "TurnEnded",
    ]
    said_text = [event for event in events if isinstance(event, TextDelta)]
    assert [event.text for event in said_text] == ["Done."]
    [used] = [event for event in events if isinstance(event, Usage)]
    assert (used.input_tokens, used.output_tokens) == (10, 2)
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE


async def test_a_line_udl_does_not_know_is_skipped() -> None:
    shell = FakeShell(
        output=said(
            thread(),
            {"type": "turn.something_new", "x": 1},
            item("agent_message", text="hi"),
            turn_done(),
        )
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert [type(event).__name__ for event in events] == ["TextDelta", "Usage", "TurnEnded"]


async def test_a_line_that_is_not_json_is_skipped() -> None:
    shell = FakeShell(output=[b"not json at all\n", *said(thread(), turn_done())])
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE


async def test_a_usage_limit_is_a_runner_limited_that_says_when_it_lifts() -> None:
    shell = FakeShell(
        output=said(
            thread(),
            {"type": "error", "message": f"You've hit your usage limit. Try again after {RESET}."},
        )
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    [limited] = [event for event in events if isinstance(event, RunnerLimited)]
    assert limited.until == datetime(2027, 1, 15, 8, 0, tzinfo=UTC)
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.LIMITED


async def test_a_failure_is_a_runner_failed() -> None:
    shell = FakeShell(output=said(thread(), {"type": "error", "message": "the model is not there"}))
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "the model is not there" in events[-1].text


async def test_a_turn_that_prints_a_plain_error_says_why_it_failed() -> None:
    """The CLI's own failures are plain lines, not JSON: a turn that ends without `turn.completed`
    says the last of them, rather than "it ended without saying how"."""
    shell = FakeShell(
        output=[
            b"Reading config from /home/user/.codex/config.toml\n",
            b"Error: Model provider `local` not found\n",
        ]
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "Model provider `local` not found" in (events[-1].text or "")


async def test_the_last_of_the_plain_lines_is_the_one_kept() -> None:
    """A turn that prints several: the last one is where it stopped."""
    shell = FakeShell(
        output=[
            b"first line\n",
            b"Not inside a trusted directory and --skip-git-repo-check was not specified.\n",
            b"\n",
        ]
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert "trusted directory" in (events[-1].text or "")


async def test_a_json_line_split_across_two_chunks_is_read_whole() -> None:
    """The shell yields fixed-size chunks, not lines: a JSON line longer than one chunk (a long
    answer, a command's output) arrives in pieces, and each piece must be joined before it is
    read — otherwise both halves are dropped and the turn says nothing."""
    whole = (
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "i1", "type": "agent_message", "text": "x" * 9000},
            }
        ).encode()
        + b"\n"
    )
    shell = FakeShell(output=[whole[:100], whole[100:5000], whole[5000:]])
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    [said_text] = [event.text for event in events if isinstance(event, TextDelta)]
    assert said_text == "x" * 9000


async def test_a_turn_ended_line_split_across_chunks_ends_the_turn() -> None:
    """`turn.completed` is the one line that must never be lost: split, it would end the turn as
    an error whose text is a fragment of JSON."""
    whole = b'{"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 4}}\n'
    shell = FakeShell(output=[whole[:20], whole[20:]])
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE


async def test_a_character_split_across_two_chunks_is_not_corrupted() -> None:
    """The chunks are bytes, and a multi-byte character may be cut in half by a chunk boundary:
    the line is only whole once its newline comes, so the character is decoded once, whole."""
    whole = (
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "i1", "type": "agent_message", "text": "a — b"},
            },
            ensure_ascii=False,
        )
        + "\n"
    ).encode()
    cut = whole.index("—".encode()) + 1  # (inside the three-byte dash)
    shell = FakeShell(output=[whole[:cut], whole[cut:]])
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    [said_text] = [event.text for event in events if isinstance(event, TextDelta)]
    assert said_text == "a — b"


async def test_a_plain_line_does_not_hide_a_failed_turns_own_message() -> None:
    """When the turn says how it failed (a `turn.failed`), that is the reason, not the last
    line printed."""
    shell = FakeShell(
        output=[
            *said(thread(), {"type": "turn.failed", "error": {"message": "the model is gone"}}),
            b"some trailing noise\n",
        ]
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert "the model is gone" in (events[-1].text or "")


async def test_a_turn_that_ends_without_saying_how_is_an_error() -> None:
    shell = FakeShell(output=said(thread()))
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR


async def test_a_stopped_session_says_so() -> None:
    shell = FakeShell(output=said(thread(), turn_done()))
    given, _ = runner(shell)
    started = await given.start(session())
    await given.stop(started)

    events = await turn_of(given, started)

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.STOPPED


async def test_a_resume_with_no_thread_is_an_error_rather_than_a_guess() -> None:
    """A session that has had a turn but whose thread udl does not know (a store made elsewhere)
    is an error: resuming a thread called by udl's own id would only fail on the host."""
    shell = FakeShell(output=said(thread(), turn_done()))
    # (A store that knows the spec and the turn, but not the thread: what a runner made with a
    # bare mapping of specs would have.)
    store = CodexSessions()
    given = Codex(
        CodexOptions(cli=[sys.executable, str(CODEX)]),
        shell=shell,
        clock=FakeClock(),
        sessions=store,
    )
    started = await given.start(session())
    store.sent.add(started.id)

    events = await turn_of(given, started, "again")

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR
    assert "thread" in events[-1].text


async def test_a_session_udl_does_not_know_is_an_error() -> None:
    shell = FakeShell()
    given, _ = runner(shell)

    events = await turn_of(given, SessionRef(runner="codex", id="nobody", cwd="/w", host="local"))

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.ERROR


# ---------------------------------------------------------------------------------------------
# The shapes the CLI really sends (recorded, not guessed)
# ---------------------------------------------------------------------------------------------


async def test_a_recorded_turn_with_tools_becomes_events() -> None:
    """A turn with a command and an MCP call, in the CLI's field shapes (the file is written from
    them, not recorded — see the fixtures' README): a string command, `server`/`tool`, and the
    reasoning item skipped."""
    written = FIXTURES / "turn-with-tools.jsonl"
    shell = FakeShell(output=[written.read_bytes()])
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    begun = [event.name for event in events if isinstance(event, ToolStarted)]
    ended = [event.name for event in events if isinstance(event, ToolFinished)]
    assert begun == ["shell", "udl/get_status"]
    assert ended == ["shell", "udl/get_status"]
    said_text = [event.text for event in events if isinstance(event, TextDelta)]
    assert said_text == ["udl is up, and the folder is empty."]
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE


async def test_a_command_item_is_a_string_and_a_shell_tool() -> None:
    """`command_execution.command` is the command line as a string; the tool is a shell."""
    shell = FakeShell(
        output=said(
            thread(),
            {
                "type": "item.started",
                "item": {"id": "i1", "type": "command_execution", "command": "ls -la"},
            },
            item("command_execution", status="completed", command="ls -la"),
            turn_done(),
        )
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    [begun] = [event for event in events if isinstance(event, ToolStarted)]
    [ended] = [event for event in events if isinstance(event, ToolFinished)]
    assert (begun.name, begun.summary) == ("shell", "ls -la")
    assert (ended.name, ended.ok) == ("shell", True)


async def test_an_mcp_tool_call_names_its_server_and_tool() -> None:
    shell = FakeShell(
        output=said(
            thread(),
            {
                "type": "item.started",
                "item": {
                    "id": "i1",
                    "type": "mcp_tool_call",
                    "server": "udl",
                    "tool": "count_sites",
                },
            },
            item("mcp_tool_call", server="udl", tool="count_sites", status="failed"),
            turn_done(),
        )
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    [ended] = [event for event in events if isinstance(event, ToolFinished)]
    assert ended.name == "udl/count_sites"
    assert not ended.ok


async def test_a_failed_turn_carries_its_message_and_may_be_a_limit() -> None:
    """`turn.failed` puts the message in `error.message`, and a limit said there is one."""
    shell = FakeShell(
        output=said(
            thread(),
            {
                "type": "turn.failed",
                "error": {"message": f"You've hit your usage limit. Try again after {RESET}."},
            },
        )
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    [limited] = [event for event in events if isinstance(event, RunnerLimited)]
    assert limited.until == datetime(2027, 1, 15, 8, 0, tzinfo=UTC)
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.LIMITED


async def test_a_limit_without_a_time_is_still_a_limit() -> None:
    shell = FakeShell(
        output=said(thread(), {"type": "error", "message": "You've hit your usage limit."})
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    [limited] = [event for event in events if isinstance(event, RunnerLimited)]
    assert limited.until is None
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.LIMITED


async def test_a_turn_that_is_not_a_limit_does_not_take_a_number_for_a_time() -> None:
    """A plain failure that happens to hold a ten-digit number is not a limit."""
    shell = FakeShell(
        output=said(
            thread(),
            {"type": "error", "message": "the build failed on node 1234567890"},
        )
    )
    given, _ = runner(shell)
    started = await given.start(session())

    events = await turn_of(given, started)

    assert [event for event in events if isinstance(event, RunnerLimited)] == []


async def test_a_resumed_turn_keeps_the_sandbox_and_the_policy() -> None:
    """`exec resume` has no `--sandbox`, so every turn carries the same policy as `-c` keys."""
    shell = FakeShell(output=said(thread("thread-9"), turn_done()))
    given, _ = runner(shell, sandbox="read-only", network=False, approval_policy="never")
    started = await given.start(session())
    await turn_of(given, started)

    await turn_of(given, started, "again")

    argv = shell.streams[-1][0]
    assert 'sandbox_mode="read-only"' in argv
    assert 'approval_policy="never"' in argv
    assert "sandbox_workspace_write.network_access=false" in argv


# ---------------------------------------------------------------------------------------------
# The flags it passes are the installed CLI's
# ---------------------------------------------------------------------------------------------


def _has_flag(said: str, flag: str) -> bool:
    """Whether a help text has `flag` as an option of its own (not a prefix of a longer one)."""
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", said) is not None


def _flags(argv: Sequence[str]) -> list[str]:
    """The options a turn's argv passes (its words that begin with `-`; `-` is the prompt)."""
    return [word for word in argv if word.startswith("-") and word != "-"]


def help_text(name: str) -> str:
    """The help text the fixtures hold for `name`."""
    return (FIXTURES / f"{name}-help.txt").read_text()


async def test_the_flags_the_runner_passes_are_in_the_installed_help() -> None:
    """A flag the runner passes must be one this CLI has (the card's own test): the argv is the
    runner's own, so a new or renamed flag fails here."""
    shell = FakeShell(output=said(thread("thread-9"), turn_done()))
    given, _ = runner(shell)
    started = await given.start(session())
    await turn_of(given, started)
    await turn_of(given, started, "again")

    for argv, text in (
        (shell.streams[0][0], help_text("exec")),
        (shell.streams[1][0], help_text("exec-resume")),
    ):
        for flag in _flags(argv):
            assert _has_flag(text, flag), f"{flag} is not a flag this command has"
    assert _has_flag(help_text("login"), "--device-auth")


# ---------------------------------------------------------------------------------------------
# The rules, and never its credentials
# ---------------------------------------------------------------------------------------------


async def test_the_guards_are_reported_from_the_rules_file(tmp_path: Path) -> None:
    """Through a real shell with a real home: the rules file is found where setup wrote it, and
    not found when it is not there (`~` is expanded by the shell, never by udl)."""
    shell = HomeShell(tmp_path / "home")
    given = Codex(CodexOptions(cli=[sys.executable, str(CODEX)]), shell=shell, clock=FakeClock())

    assert not (await given.health()).guards

    await given.setup()

    assert (shell.home / ".codex" / "rules" / "udl.rules").is_file()
    assert (await given.health()).guards
    said_rules = (shell.home / ".codex" / "rules" / "udl.rules").read_text()
    assert 'pattern=["git", "push"]' in said_rules
    assert 'pattern=["gh", "pr", "merge"]' in said_rules
    assert 'decision="forbidden"' in said_rules


def test_the_rules_file_is_asked_for_where_setup_writes_it(tmp_path: Path) -> None:
    """`setup()` writes it through the shell (relative to the home) and says where it is."""
    shell = HomeShell(tmp_path / "home")
    given = Codex(CodexOptions(cli=[sys.executable, str(CODEX)]), shell=shell, clock=FakeClock())

    written = asyncio.run(given.setup())

    assert written == ["~/.codex/rules/udl.rules"]
    [[argv, stdin]] = shell.calls
    assert argv[0] == "sh"
    assert argv[-1] == ".codex/rules/udl.rules"  # (the shell expands $HOME around it)
    assert (stdin or b"").decode().startswith("# udl's rules:")


async def test_udl_never_opens_codexs_own_credentials(scripted: FakeShell) -> None:
    """O-C3's rule: the account is what the CLI says, never a file of its own."""
    scripted.answers = {
        "--version": ProcessResult(returncode=0, stdout="codex-cli 0.171.0\n", stderr=""),
        "login status": ProcessResult(returncode=0, stdout="Not logged in\n", stderr=""),
    }
    given, _ = runner(scripted)

    await given.health()

    for argv, _ in scripted.runs:
        assert not any("auth.json" in part for part in argv)


@pytest.fixture
def scripted() -> FakeShell:
    """A shell for a test that is not async."""
    return FakeShell()
