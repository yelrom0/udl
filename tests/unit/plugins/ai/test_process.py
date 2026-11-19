"""
---------------
- test_process.py -
---------------

A provider that udl starts on demand (udl.plugins.ai.process): its program is argv (never a
shell), `command[0]` must be in scripts_dir or in [ai] allowed_commands, it is started on first
use and waited for, and stopped after `idle_timeout` without a request.

The command here is a tiny Python server (written to a temporary folder), so the test starts a
real program: one that answers its health check, and one that never does.
"""

# System Imports
import asyncio
import os
import socket
import stat
import textwrap
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from udl.clock import Clock, SystemClock
from udl.errors import ConfigError, NotAllowed, ProviderUnavailable
from udl.plugins.ai.base import ChatRequest, ProviderHealth
from udl.plugins.ai.openai import OpenAICompatibleProvider, OpenAIOptions
from udl.plugins.ai.process import (
    ProcessPool,
    ProviderProcess,
    _shown,  # pyright: ignore[reportPrivateUsage]  (its own rule, reached only here)
    allowed_command,
    default_pool,
    reset_default_pool,
)

# A server that answers a provider's health check (GET /v1/models) and a chat (POST
# /v1/chat/completions): enough for a provider with a command to be started and used. A second
# script only sleeps, so a provider whose program never answers can be tried.
_SERVER = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import json
    import sys
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    ANSWER = {
        "id": "chat-1",
        "object": "chat.completion",
        "model": "tiny-1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from the tiny server"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    MODELS = {"object": "list", "data": [{"id": "tiny-1"}]}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self._send(MODELS)

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self._send(ANSWER)

        def _send(self, body):
            sent = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(sent)))
            self.end_headers()
            self.wfile.write(sent)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765), Handler
    )
    server.serve_forever()
    """
)

_SLEEPER = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    import time

    time.sleep(60)
    """
)

PORT = 8765
"""The port the tiny server listens on (a test's own)."""
URL = f"http://127.0.0.1:{PORT}/v1"
"""Where the tiny server answers."""


def free_port() -> int:
    """A port nothing is listening on (each test's own, so a test never meets another's)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture
def script(tmp_path: Path) -> Path:
    """The tiny server, as a program in scripts_dir."""
    return _program(tmp_path, "tiny-server.py", _SERVER)


@pytest.fixture
def sleeper(tmp_path: Path) -> Path:
    """A program that starts and never answers, as a program in scripts_dir."""
    return _program(tmp_path, "sleeper.py", _SLEEPER)


def _program(tmp_path: Path, name: str, text: str) -> Path:
    """`text` as an executable program in a scripts_dir of its own."""
    folder = tmp_path / "scripts"
    folder.mkdir(exist_ok=True)
    program = folder / name
    program.write_text(text)
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    return program


_POOLS: list[ProcessPool] = []
"""The pools the tests made (stopped after each one: nothing is left running)."""


@pytest.fixture(autouse=True)
async def stop_everything() -> AsyncIterator[None]:
    """Stop whatever a test started."""
    yield
    while _POOLS:
        await _POOLS.pop().close()
    assert not _children(), f"processes left running: {_children()}"


def _children() -> list[str]:
    """The commands of this process's children (from /proc, so nowhere else)."""
    found: list[str] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode()
        except OSError:
            continue
        if int(fields[1]) == os.getpid():
            found.append(cmd.strip())
    return found


def options(script: Path, *args: str, port: int | None = None, **fields: object) -> OpenAIOptions:
    """A provider's options: the tiny server as its command, on a port of its own."""
    where = port if port is not None else free_port()
    return OpenAIOptions.model_validate(
        {
            "url": f"http://127.0.0.1:{where}/v1",
            "command": [str(script), str(where), *args],
            "start_timeout": timedelta(seconds=20),
            "idle_timeout": timedelta(seconds=30),
            **fields,
        }
    )


class Tiny(OpenAICompatibleProvider):
    """A provider of the tiny server."""

    name = "tiny"
    title = "Tiny"
    summary = "The tiny server (for tests)."

    def __init__(self, options: OpenAIOptions, *, clock: Clock, pool: ProcessPool) -> None:
        super().__init__(options, clock=clock, processes=pool)

    async def health(self) -> ProviderHealth:
        try:
            async with self._client() as client:
                response = await client.get(f"{self.options.url}/models")
        except ProviderUnavailable:
            return ProviderHealth(ok=False, problems=["not answering"])
        return ProviderHealth(ok=response.status_code < 400)


class Answered(Tiny):
    """A provider whose url another server already answers: udl's own program is not the one
    that says it is well."""

    async def health(self) -> ProviderHealth:
        return ProviderHealth(ok=True)


def pool(script: Path, *, allowed: tuple[str, ...] = ()) -> ProcessPool:
    """A pool whose scripts_dir is the folder the tiny server is in."""
    found = ProcessPool(scripts_dir=script.parent, allowed=allowed)
    _POOLS.append(found)
    return found


def started(provider: Tiny) -> ProviderProcess:
    """The provider's process (it has one: its options name a command)."""
    processes = provider.processes
    assert processes is not None
    process = processes.process(provider)
    assert process is not None
    return process


async def wait_until(what: str, done: bool) -> None:
    """Wait (really) until `done` — the program starts in real time, not on a clock."""
    for _ in range(200):
        if done:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(what)


# ---------------------------------------------------------------------------------------------
# What udl may start
# ---------------------------------------------------------------------------------------------


def test_a_program_in_scripts_dir_is_allowed(script: Path) -> None:
    found = allowed_command([str(script)], scripts_dir=script.parent, allowed=())

    assert found == script.resolve()


def test_a_program_named_in_allowed_commands_is_allowed(script: Path) -> None:
    # (By its name, then found on PATH — which is where a `llama-server` is.)
    found = allowed_command(["sh"], scripts_dir=script.parent, allowed=["sh"])

    assert found.is_file()
    assert found.is_relative_to(Path("/"))


def test_a_program_outside_the_allowed_places_is_refused(script: Path) -> None:
    with pytest.raises(NotAllowed, match="scripts_dir"):
        allowed_command(["/bin/sh"], scripts_dir=script.parent, allowed=())


def test_a_program_that_is_not_named_and_not_allowed_is_refused(script: Path) -> None:
    with pytest.raises(NotAllowed, match="allowed_commands"):
        allowed_command(["sh"], scripts_dir=script.parent, allowed=())


def test_a_program_that_is_not_there_is_refused(script: Path) -> None:
    with pytest.raises(ConfigError, match="no program"):
        allowed_command(["no-such-program"], scripts_dir=script.parent, allowed=["no-such-program"])


def test_a_program_that_cannot_run_is_refused(tmp_path: Path) -> None:
    program = tmp_path / "not-executable"
    program.write_text("#!/bin/sh\n")
    program.chmod(0o644)

    with pytest.raises(ConfigError, match="not executable"):
        allowed_command([str(program)], scripts_dir=tmp_path, allowed=())


async def test_a_command_outside_the_allowed_places_is_refused_when_the_provider_is_saved(
    script: Path,
) -> None:
    provider = Tiny(
        OpenAIOptions.model_validate({"url": URL, "command": ["sh"]}),
        clock=FakeClock(),
        pool=pool(script),
    )

    with pytest.raises(NotAllowed, match="allowed_commands"):
        provider.check()


async def test_a_command_inside_scripts_dir_is_accepted_when_the_provider_is_saved(
    script: Path,
) -> None:
    provider = Tiny(options(script), clock=FakeClock(), pool=pool(script))

    provider.check()  # (it does not raise)


# ---------------------------------------------------------------------------------------------
# Starting it, and waiting for it
# ---------------------------------------------------------------------------------------------


async def test_a_command_starts_on_the_first_chat_and_stops_when_idle(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(
        options(script, idle_timeout=timedelta(seconds=30)), clock=clock, pool=pool(script)
    )

    answer = await provider.chat(
        ChatRequest.model_validate(
            {"model": "tiny-1", "messages": [{"role": "user", "content": "Hello?"}]}
        )
    )

    assert answer.message.content == "Hello from the tiny server"
    process = started(provider)
    assert process.running

    await clock.advance(timedelta(seconds=31))

    assert not process.running


async def test_it_is_started_on_first_use_and_waited_for(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(options(script), clock=clock, pool=pool(script))
    process = started(provider)

    await process.ensure()

    assert process.running
    assert process.pid is not None
    assert process.pid != os.getpid()  # (its own program, not udl's)
    assert (await provider.health()).ok
    await process.stop()
    assert not process.running


async def test_a_provider_that_is_already_running_is_not_started_twice(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(options(script), clock=clock, pool=pool(script))
    process = started(provider)

    await process.ensure()
    first = process.pid

    await process.ensure()

    assert process.pid == first
    await process.stop()


async def test_a_program_that_never_answers_is_given_up_on(sleeper: Path) -> None:
    # (A real clock: the program takes real time not to answer.)
    clock: Clock = SystemClock()
    provider = Tiny(
        options(sleeper, start_timeout=timedelta(seconds=0.3)),
        clock=clock,
        pool=pool(sleeper),
    )
    process = started(provider)

    with pytest.raises(ProviderUnavailable, match="did not answer"):
        await process.ensure()

    assert not process.running


async def test_a_program_that_exits_while_another_server_answers_is_not_ready(
    tmp_path: Path,
) -> None:
    # (Something answers on its port, but not the program udl started: it exited, so say so.)
    program = _program(tmp_path, "gone.py", "#!/usr/bin/env python3\nimport sys\nsys.exit(1)\n")
    clock: Clock = SystemClock()
    provider = Answered(
        options(program, start_timeout=timedelta(seconds=5)), clock=clock, pool=pool(program)
    )
    process = started(provider)

    with pytest.raises(ProviderUnavailable, match="exited"):
        await process.ensure()

    assert not process.running


async def test_a_program_that_prints_something_says_why_it_did_not_start(
    tmp_path: Path,
) -> None:
    program = tmp_path / "noisy.py"
    program.write_text(
        "#!/usr/bin/env python3\nimport sys\nprint('no model here', file=sys.stderr)\nsys.exit(1)\n"
    )
    program.chmod(0o755)
    clock: Clock = SystemClock()
    provider = Tiny(
        options(program, start_timeout=timedelta(seconds=0.3)), clock=clock, pool=pool(program)
    )
    process = started(provider)

    with pytest.raises(ProviderUnavailable) as asked:
        await process.ensure()

    assert "no model here" in (asked.value.hint or "")
    assert "exited" in asked.value.message
    assert "no model here" in process.output()


# ---------------------------------------------------------------------------------------------
# Stopping it when it is idle
# ---------------------------------------------------------------------------------------------


async def test_it_is_stopped_once_it_has_been_idle_long_enough(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(
        options(script, idle_timeout=timedelta(seconds=30)), clock=clock, pool=pool(script)
    )
    process = started(provider)
    await process.ensure()

    assert process.running

    await clock.advance(timedelta(seconds=31))

    assert not process.running
    assert process.pid is None


async def test_it_is_stopped_at_the_first_look_past_the_idle_timeout(script: Path) -> None:
    # (The keeper pauses for what is left of idle_timeout, so a use halfway through does not buy
    # it another full one: stopped at 20 + 30, not at 20 + 60.)
    clock = FakeClock()
    provider = Tiny(
        options(script, idle_timeout=timedelta(seconds=30)), clock=clock, pool=pool(script)
    )
    process = started(provider)
    await process.ensure()

    await clock.advance(timedelta(seconds=20))
    process.used()  # (a request, halfway through the idle timeout)
    await clock.advance(timedelta(seconds=31))

    assert not process.running


async def test_a_use_keeps_it_up(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(
        options(script, idle_timeout=timedelta(seconds=30)), clock=clock, pool=pool(script)
    )
    process = started(provider)
    await process.ensure()

    await clock.advance(timedelta(seconds=20))
    process.used()  # (a request)
    await clock.advance(timedelta(seconds=10))

    assert process.running  # (the keeper looked, and it was used 10 s ago)

    await clock.advance(timedelta(seconds=60))

    assert not process.running  # (nothing has asked for it since)
    await process.stop()


async def test_stopping_it_ends_the_program_and_the_next_use_starts_it_again(
    script: Path,
) -> None:
    clock = FakeClock()
    provider = Tiny(options(script), clock=clock, pool=pool(script))
    process = started(provider)
    await process.ensure()
    first = process.pid

    await process.stop()

    assert process.pid is None
    assert not process.running

    await process.ensure()

    assert process.pid is not None
    assert process.pid != first
    await process.stop()


async def test_it_never_runs_through_a_shell(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(options(script), clock=clock, pool=pool(script))
    process = started(provider)

    await process.ensure()

    # (Its command is argv: a `;` in an argument is one argument, and no shell reads it.)
    assert provider.options.command[0] == str(script)
    await process.stop()


# ---------------------------------------------------------------------------------------------
# One process per provider
# ---------------------------------------------------------------------------------------------


def test_two_connections_of_one_provider_share_one_process(script: Path) -> None:
    processes = pool(script)
    settings = options(script)
    one = Tiny(settings, clock=FakeClock(), pool=processes)
    two = Tiny(settings, clock=FakeClock(), pool=processes)

    assert processes.process(one) is processes.process(two)


def test_two_providers_at_another_url_are_two_processes(script: Path) -> None:
    processes = pool(script)
    one = Tiny(options(script), clock=FakeClock(), pool=processes)
    two = Tiny(
        OpenAIOptions.model_validate(
            {
                "url": f"http://127.0.0.1:{free_port()}/v1",
                "command": [str(script), str(PORT)],
            }
        ),
        clock=FakeClock(),
        pool=processes,
    )

    assert processes.process(one) is not processes.process(two)


def test_a_provider_without_a_command_is_not_started(script: Path) -> None:
    provider = Tiny(
        OpenAIOptions.model_validate({"url": URL}), clock=FakeClock(), pool=pool(script)
    )
    processes = provider.processes
    assert processes is not None

    assert processes.process(provider) is None


def test_the_default_pool_is_one_per_udl_and_can_be_reset() -> None:
    reset_default_pool()

    assert default_pool() is default_pool()

    reset_default_pool()


async def test_the_pool_stops_everything_it_started(script: Path) -> None:
    processes = pool(script)
    provider = Tiny(options(script), clock=FakeClock(), pool=processes)

    process = started(provider)
    await process.ensure()

    assert processes.running

    await processes.close()

    assert processes.running == []


async def test_a_process_that_was_never_started_is_not_waited_for(script: Path) -> None:
    processes = pool(script)

    await processes.close()  # (nothing to stop)

    assert processes.running == []


async def test_a_program_whose_interpreter_is_missing_is_not_installed(tmp_path: Path) -> None:
    program = tmp_path / "wrong-interpreter"
    program.write_text("#!/no/such/interpreter\n")
    program.chmod(0o755)
    clock: Clock = SystemClock()
    provider = Tiny(options(program), clock=clock, pool=pool(program))
    process = started(provider)

    with pytest.raises(ProviderUnavailable, match=r"not installed|did not answer"):
        await process.ensure()


async def test_a_program_that_is_not_a_program_cannot_be_started(tmp_path: Path) -> None:
    program = tmp_path / "not-a-program"
    program.write_text("this is not a program\n")
    program.chmod(0o755)
    clock: Clock = SystemClock()
    provider = Tiny(options(program), clock=clock, pool=pool(program))
    process = started(provider)

    with pytest.raises(ProviderUnavailable, match=r"could not be started|did not answer"):
        await process.ensure()


async def test_two_callers_that_arrive_together_start_one_program(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(options(script), clock=clock, pool=pool(script))
    process = started(provider)
    before = len(_children())

    await asyncio.gather(process.ensure(), process.ensure())

    first = process.pid
    assert first is not None
    assert len(_children()) == before + 1  # (one program, not two)
    await process.ensure()
    assert process.pid == first
    await process.stop()


def test_the_default_pool_reads_the_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reset_default_pool()
    monkeypatch.setenv("UDL_AUTOMATIONS__SCRIPTS_DIR", str(tmp_path))
    monkeypatch.setenv("UDL_AI__ALLOWED_COMMANDS", '["llama-server"]')

    shared = default_pool()

    assert shared.scripts_dir == tmp_path.resolve()
    assert shared.allowed == ["llama-server"]
    reset_default_pool()


async def test_a_request_that_runs_long_is_not_stopped_under_it(script: Path) -> None:
    clock = FakeClock()
    provider = Tiny(
        options(script, idle_timeout=timedelta(seconds=1)), clock=clock, pool=pool(script)
    )
    process = started(provider)
    await process.ensure()

    async with process.hold():
        await clock.advance(timedelta(seconds=5))

        assert process.running  # (it is answering)

    await clock.advance(timedelta(seconds=5))

    assert not process.running


async def test_the_hint_for_a_program_that_did_not_start_keeps_its_key_out(
    sleeper: Path,
) -> None:
    clock: Clock = SystemClock()
    provider = Tiny(
        options(sleeper, "--api-key", "sk-1", start_timeout=timedelta(seconds=0.3)),
        clock=clock,
        pool=pool(sleeper),
    )
    process = started(provider)

    with pytest.raises(ProviderUnavailable) as asked:
        await process.ensure()

    hint = asked.value.hint or ""
    assert "sk-1" not in hint
    assert "***" in hint


def test_a_command_that_is_only_a_name_must_be_on_the_path(
    script: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")

    with pytest.raises(ConfigError, match="no program"):
        allowed_command(["llama-server"], scripts_dir=script.parent, allowed=["llama-server"])


def test_a_secret_in_a_command_is_never_shown() -> None:
    assert _shown(["llama-server", "--api-key", "sk-1", "--port", "8080"]) == [
        "llama-server",
        "--api-key",
        "***",
        "--port",
        "8080",
    ]
    assert _shown(["llama-server", "--api-key=sk-1"]) == ["llama-server", "--api-key=***"]
    assert _shown(["llama-server", "--port", "8080"]) == ["llama-server", "--port", "8080"]
