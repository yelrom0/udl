"""
--------------
- process.py -
--------------

Running a tool's command-line program (curl, wget, gallery-dl's CLI) from a downloader: its
exit code and output, a tick while it runs (to report progress) and cancellation.
"""

# System Imports
import asyncio
import contextlib
import logging
from collections.abc import Callable, Sequence
from typing import Final

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.errors import Cancelled, ToolUnavailable
from udl.plugins.downloaders.base import CancelToken

log = logging.getLogger(__name__)

# Seconds a program gets to exit after being asked to, before it is killed.
GRACE: Final[float] = 2.0


class ProcessResult(BaseModel):
    """How a program ended, and what it printed."""

    model_config = ConfigDict(frozen=True)

    returncode: int
    stdout: str
    stderr: str


async def _stop(process: asyncio.subprocess.Process, grace: float) -> None:
    """Ask the program to exit (SIGTERM), then kill it if it has not within `grace` seconds."""
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=grace)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        await process.wait()


async def run_process(
    args: Sequence[str],
    *,
    cancel: CancelToken,
    on_tick: Callable[[], None] | None = None,
    interval: float = 0.25,
    grace: float = GRACE,
) -> ProcessResult:
    """Run a program to completion, with no input, calling `on_tick` every `interval` seconds
    while it runs. Raises Cancelled (once it has stopped) when `cancel` is cancelled; if the
    awaiting task is cancelled, the program is stopped too. A program that is not installed is
    ToolUnavailable."""
    cancel.check()
    log.debug("Running %s", " ".join(args))
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        raise ToolUnavailable(f"{args[0]} is not installed") from e
    output = asyncio.ensure_future(process.communicate())

    async def stop() -> None:
        await _stop(process, grace)
        await output  # its pipes close as it exits

    try:
        while not (await asyncio.wait([output], timeout=interval))[0]:
            if cancel.cancelled:
                await stop()
                raise Cancelled("The download was cancelled")
            if on_tick is not None:
                on_tick()
    except asyncio.CancelledError:
        cancel.cancel()
        await asyncio.shield(stop())
        raise
    stdout, stderr = output.result()
    return ProcessResult(
        returncode=await process.wait(),
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
    )
