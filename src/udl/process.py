"""
--------------
- process.py -
--------------

Running a program (a downloader's tool, curl, a script action, notify-send): its exit code and
output, a tick while it runs, cancellation, and what it is given (its input, environment and
folder). Also how work is asked to stop: a CancelToken, which cancellable() stops anything that
awaits on.
"""

# System Imports
import asyncio
import contextlib
import logging
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Final

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.errors import Cancelled, ToolUnavailable

log = logging.getLogger(__name__)

# Seconds a program gets to exit after being asked to, before it is killed.
GRACE: Final[float] = 2.0
_DOWNLOAD_CANCELLED: Final = "The download was cancelled"
_CHUNK: Final = 64 * 1024


class CancelToken:
    """Asks a download (a probe, a run) to stop. Safe to use from any thread: threaded tools
    check it in their progress hooks."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self, message: str = _DOWNLOAD_CANCELLED) -> None:
        """Raise Cancelled (saying `message`) if the token has been cancelled."""
        if self.cancelled:
            raise Cancelled(message)


async def cancellable[T](
    awaitable: Awaitable[T],
    cancel: CancelToken,
    *,
    interval: float = 0.1,
    message: str = _DOWNLOAD_CANCELLED,
) -> T:
    """Await `awaitable`, stopping it (and raising Cancelled, saying `message`) within `interval`
    seconds of `cancel` being cancelled, even if it is stuck waiting (e.g. on a stalled server).
    If the awaiting task is cancelled, `cancel` is cancelled too."""
    task = asyncio.ensure_future(awaitable)
    try:
        while not cancel.cancelled:
            if (await asyncio.wait([task], timeout=interval))[0]:
                return task.result()
        task.cancel()  # (before its first step, if it never started)
        await asyncio.wait([task])
    except asyncio.CancelledError:
        cancel.cancel()
        task.cancel()
        await asyncio.wait([task])
        raise
    raise Cancelled(message)


class ProcessResult(BaseModel):
    """How a program ended, and what it printed."""

    model_config = ConfigDict(frozen=True)

    returncode: int
    stdout: str
    stderr: str
    truncated: bool = False  # (its output was more than it was let keep: see run_process())


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


async def _first(stream: asyncio.StreamReader | None, limit: int | None) -> tuple[bytes, bool]:
    """What `stream` gives (its first `limit` bytes), and whether it gave more."""
    kept = bytearray()
    more = False
    while stream is not None and (chunk := await stream.read(_CHUNK)):
        room = len(chunk) if limit is None else max(limit - len(kept), 0)
        kept += chunk[:room]
        more = more or room < len(chunk)
    return bytes(kept), more


async def _last(stream: asyncio.StreamReader | None, limit: int | None) -> tuple[bytes, bool]:
    """What `stream` gives (its last `limit` bytes), and whether it gave more."""
    kept = bytearray()
    more = False
    while stream is not None and (chunk := await stream.read(_CHUNK)):
        kept += chunk
        if limit is not None and len(kept) > limit:
            del kept[:-limit]
            more = True
    return bytes(kept), more


async def _feed(process: asyncio.subprocess.Process, data: bytes | None) -> None:
    """Give the program `data` on its stdin, then close it."""
    if process.stdin is None:
        return
    try:
        if data:
            process.stdin.write(data)
            await process.stdin.drain()
    except BrokenPipeError, ConnectionResetError:  # (it stopped reading)
        pass
    finally:
        process.stdin.close()


async def run_process(
    args: Sequence[str],
    *,
    cancel: CancelToken,
    on_tick: Callable[[], None] | None = None,
    interval: float = 0.25,
    grace: float = GRACE,
    stdin: bytes | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    max_output: int | None = None,
    cancelled: str = _DOWNLOAD_CANCELLED,
) -> ProcessResult:
    """Run a program to completion, calling `on_tick` every `interval` seconds while it runs.

    It is given `stdin` (or no input), `env` as its whole environment (else udl's) and `cwd` as
    its folder. With `max_output`, the first `max_output` bytes of its stdout and the last of its
    stderr are kept (the rest is read and dropped). Raises Cancelled (saying `cancelled`, once
    it has stopped) when `cancel` is cancelled; if the awaiting task is cancelled, the program is
    stopped too. A program that is not installed is ToolUnavailable.
    """
    cancel.check(cancelled)
    log.debug("Running %s", " ".join(args))
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=None if env is None else dict(env),
            cwd=cwd,
        )
    except FileNotFoundError as e:
        raise ToolUnavailable(f"{args[0]} is not installed") from e
    output = asyncio.ensure_future(
        asyncio.gather(
            _feed(process, stdin),
            _first(process.stdout, max_output),
            _last(process.stderr, max_output),
        )
    )

    async def stop() -> None:
        await _stop(process, grace)
        await output  # its pipes close as it exits

    try:
        while not (await asyncio.wait([output], timeout=interval))[0]:
            if cancel.cancelled:
                await stop()
                raise Cancelled(cancelled)
            if on_tick is not None:
                on_tick()
    except asyncio.CancelledError:
        cancel.cancel()
        await asyncio.shield(stop())
        raise
    _, (stdout, cut_out), (stderr, cut_err) = output.result()
    return ProcessResult(
        returncode=await process.wait(),
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
        truncated=cut_out or cut_err,
    )
