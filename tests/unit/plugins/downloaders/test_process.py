"""
-------------------
- test_process.py -
-------------------

run_process(): running a tool's command-line program, with its output, ticks while it runs,
and cancellation (from the token or the awaiting task)
"""

# System Imports
import asyncio
import sys
import time
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.errors import Cancelled, ToolUnavailable
from udl.plugins.downloaders.base import CancelToken
from udl.plugins.downloaders.process import ProcessResult, run_process


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def wait_for(path: Path) -> None:
    """Wait (up to 10 s) until a program has created `path`."""
    deadline = time.monotonic() + 10
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path} never appeared")
        time.sleep(0.02)


def running(pid: str) -> bool:
    return Path(f"/proc/{pid}").exists()


async def test_it_returns_the_exit_code_and_output() -> None:
    result = await run_process(
        python("import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"),
        cancel=CancelToken(),
    )

    assert result == ProcessResult(returncode=3, stdout="out\n", stderr="err\n")


async def test_it_reads_no_input() -> None:
    result = await run_process(
        python("import sys; print(repr(sys.stdin.read()))"), cancel=CancelToken()
    )

    assert result.stdout == "''\n"


async def test_it_waits_for_a_slow_program() -> None:
    result = await run_process(
        python("import time; time.sleep(0.3)"), cancel=CancelToken(), interval=0.05
    )

    assert result.returncode == 0


async def test_it_ticks_while_the_program_runs() -> None:
    ticks: list[float] = []

    await run_process(
        python("import time; time.sleep(0.5)"),
        cancel=CancelToken(),
        on_tick=lambda: ticks.append(time.monotonic()),
        interval=0.05,
    )

    assert len(ticks) >= 3


async def test_cancelling_the_token_stops_the_program(tmp_path: Path) -> None:
    token = CancelToken()
    marker = tmp_path / "started"
    task = asyncio.create_task(
        run_process(
            python(f"import pathlib, time; pathlib.Path({str(marker)!r}).touch(); time.sleep(30)"),
            cancel=token,
            interval=0.05,
        )
    )
    await asyncio.to_thread(wait_for, marker)

    token.cancel()
    start = time.monotonic()

    with pytest.raises(Cancelled):
        await task
    assert time.monotonic() - start < 2


async def test_a_program_that_ignores_terminate_is_killed(tmp_path: Path) -> None:
    token = CancelToken()
    marker = tmp_path / "started"
    code = (
        "import pathlib, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(marker)!r}).touch(); time.sleep(30)"
    )
    task = asyncio.create_task(run_process(python(code), cancel=token, interval=0.05, grace=0.2))
    await asyncio.to_thread(wait_for, marker)

    token.cancel()
    start = time.monotonic()

    with pytest.raises(Cancelled):
        await task
    assert time.monotonic() - start < 2


async def test_cancelling_the_task_stops_the_program(tmp_path: Path) -> None:
    token = CancelToken()
    marker = tmp_path / "started"
    pid_file = tmp_path / "pid"
    code = (
        f"import os, pathlib, time; pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
        f"pathlib.Path({str(marker)!r}).touch(); time.sleep(30)"
    )
    task = asyncio.create_task(run_process(python(code), cancel=token, interval=0.05))
    await asyncio.to_thread(wait_for, marker)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert token.cancelled
    assert not await asyncio.to_thread(running, pid_file.read_text())


async def test_a_cancelled_token_never_starts_the_program(tmp_path: Path) -> None:
    token = CancelToken()
    token.cancel()
    marker = tmp_path / "ran"

    with pytest.raises(Cancelled):
        await run_process(python(f"open({str(marker)!r}, 'w')"), cancel=token)

    assert not marker.exists()


async def test_a_missing_program_is_tool_unavailable() -> None:
    with pytest.raises(ToolUnavailable, match="udl-no-such-program"):
        await run_process(["udl-no-such-program", "--version"], cancel=CancelToken())
