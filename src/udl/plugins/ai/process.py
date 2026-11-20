"""
--------------
- process.py -
--------------

ProviderProcess: a provider udl starts on demand (a `llama-server`, LM Studio, vLLM), and
ProcessPool, the one process per provider a udl has.

A provider with a `command` is started on its first use and waited for (its health is asked every
POLL seconds, for at most `start_timeout`), and stopped after `idle_timeout` without a request:
SIGTERM, then kill. Nothing runs through a shell — the program is argv, each argument one
argument — and `command[0]` must be inside [automations] scripts_dir or listed in [ai]
allowed_commands (the config file only), so no API client can make udl run a program of its
choosing.

tests/unit/plugins/ai/test_process.py drives one with a tiny Python server as its `command`.
"""

# System Imports
import asyncio
import contextlib
import logging
import math
import os
import shutil
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

# Package Imports
from pydantic import Field

# Local Imports
from udl.clock import Clock, pause
from udl.config import Duration, Settings
from udl.errors import ConfigError, NotAllowed, ProviderUnavailable
from udl.plugins.ai.base import AnAI, ProviderOptions

log = logging.getLogger(__name__)

POLL: Final = 0.5
"""Seconds between health checks while a provider is starting."""
GRACE: Final = 5.0
"""Seconds a provider's program gets to exit after SIGTERM, before it is killed."""
LOG_TAIL: Final = 2000
"""Characters of a program's output kept to say why it did not start."""

_REAPING: set[asyncio.Future[None]] = set()
"""The waits still ending a program: a stop that was cancelled mid-wait leaves its wait running,
and this holds it until it is done (a collected task would never reap the program)."""

type Key = tuple[str, tuple[str, ...]]
"""What makes two connections the same provider: where it is, and what starts it."""

_SECRET_FLAGS: Final = frozenset(
    {
        "--api-key",
        "--apikey",
        "--key",
        "--token",
        "--access-token",
        "--auth",
        "--password",
        "--secret",
    }
)
"""Options whose value is a secret (so it is never logged or shown)."""


class ProcessOptions(ProviderOptions):
    """A provider that udl may start on demand: its program, how long it may take to start, and
    how long it may stay up without a request. A provider that is always running (a system
    service) leaves `command` empty and is not started by udl."""

    command: list[str] = Field(default_factory=list)
    """The program and its arguments, as argv (never through a shell). Empty: udl does not start
    it."""
    start_timeout: Duration = timedelta(seconds=120)
    """How long it may take to answer after udl starts it."""
    idle_timeout: Duration = timedelta(minutes=10)
    """How long it may be unused before udl stops it."""


def allowed_command(command: Sequence[str], *, scripts_dir: Path, allowed: Sequence[str]) -> Path:
    """The program `command` starts: one in `scripts_dir`, or one `allowed` names (then it is
    found on PATH).

    Raises NotAllowed for anything else — a program outside the allowed places, which no API
    client may name — and ConfigError for one that is not there or cannot run."""
    named = command[0]
    scripts = scripts_dir.expanduser().resolve()
    inside = (scripts / Path(named).expanduser()).resolve()
    bare = Path(named).name == named  # (a name, not a path: scripts_dir's, else PATH's)
    if inside.is_relative_to(scripts) and (inside.is_file() or not bare):
        program = inside
    elif named in allowed:
        # (Named in [ai] allowed_commands: a program on PATH, e.g. a llama-server.)
        found = shutil.which(named)
        if found is None:
            raise ConfigError(
                f"There is no program {named} on the path",
                hint="Install it, or correct the provider's command (udl ai providers show).",
            )
        program = Path(found)
    else:
        raise NotAllowed(
            f"{named} is not in scripts_dir ({scripts}) or in [ai] allowed_commands: an AI "
            "provider starts programs from there only",
            hint="Put the program in scripts_dir ([automations] scripts_dir), or list it in "
            "[ai] allowed_commands (which the config file only sets).",
        )
    if not program.is_file():
        raise ConfigError(
            f"There is no program {named} at {program}",
            hint="`udl ai providers show` says where a provider's command must be.",
        )
    if not os.access(program, os.X_OK):
        raise ConfigError(f"{program} is not executable", hint=f"Make it so: chmod +x {program}")
    return program


class ProviderProcess:
    """A provider's program: started on first use, waited for, and stopped once it has been idle
    for its `idle_timeout`.

    One per provider (ProcessPool keeps them): a provider made again from the same connection —
    another chat, another udl operation — gets the same process, so it does not start a second
    server. Its output's last LOG_TAIL characters are kept, to say why it did not start."""

    def __init__(
        self, options: ProcessOptions, *, provider: AnAI, clock: Clock, pool: ProcessPool
    ) -> None:
        self.options = options
        self.provider = provider
        self.clock = clock
        """The clock its idle timeout is measured on (a program starts in real time, so its
        start is not)."""
        self.pool = pool
        self.started_at: datetime | None = None
        """When the program was started (None: it is not running)."""
        self.used_at: datetime | None = None
        """When it was last asked for something."""
        self._process: asyncio.subprocess.Process | None = None
        self._keeper: asyncio.Task[None] | None = None
        self._reader: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._changed = asyncio.Event()  # (a request started or ended)
        self._starting = asyncio.Lock()  # (one start at a time: two callers, one program)
        self._in_flight = 0  # (the requests running now: the idle keeper waits for them)
        self._output = bytearray()

    @property
    def running(self) -> bool:
        """Whether its program is running."""
        return self._process is not None and self._process.returncode is None

    @property
    def pid(self) -> int | None:
        """The program's process id (None: it is not running)."""
        return self._process.pid if self._process is not None else None

    def output(self) -> str:
        """The last of what the program printed (to say why it did not start)."""
        return self._output.decode(errors="replace")

    def used(self) -> None:
        """Note that the provider was just asked for something (so it is not stopped yet)."""
        self.used_at = self.clock.now()

    async def ensure(self) -> None:
        """Start the program if it is not running, and wait until it answers.

        Raises NotAllowed or ConfigError for a program udl may not start, and
        ProviderUnavailable when it does not answer within `start_timeout` (naming its program,
        its URL and what it printed). Two callers that arrive together start one program."""
        async with self._starting:
            if self.running:
                self.used()
                return
            if not self.options.command:
                return  # (udl does not start this provider)
            await self._start()

    async def _start(self) -> None:
        """Start the program and wait for it to answer. (Called under the start lock.)"""
        program = self.pool.program(self.options)
        argv = [str(program), *self.options.command[1:]]
        log.info("Starting the %s provider: %s", self.provider.name, _shown(argv))
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as e:
            raise ProviderUnavailable(
                f"{program} is not installed",
                hint="Install it, or correct the provider's command (udl ai providers show).",
            ) from e
        except OSError as e:
            raise ProviderUnavailable(
                f"{program} could not be started: {e.strerror or e}",
                hint="Check the program: it must be executable and runnable on this machine.",
            ) from e
        self._process = process
        self._output.clear()
        self.started_at = self.used_at = self.clock.now()
        self._stopped.clear()
        self._drain(process)
        try:
            await self._wait_until_ready()
        except BaseException:
            await self.stop()
            raise
        self._keep()

    @asynccontextmanager
    async def hold(self) -> AsyncGenerator[None]:
        """Hold the program up while a request runs: the idle keeper does not stop a program
        that is answering, however long the answer takes."""
        self._in_flight += 1
        self._changed.set()
        self.used()
        try:
            yield
        finally:
            self._in_flight -= 1
            self._changed.set()
            self.used()

    async def stop(self) -> None:
        """Stop the program (SIGTERM, then kill), and forget it: the next use starts it again."""
        keeper, self._keeper = self._keeper, None
        if keeper is not None:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
        await self._shutdown()

    async def _ended(self, process: asyncio.subprocess.Process) -> None:
        """Wait for the program to end, and kill it if it will not (the grace is its own)."""
        try:
            await asyncio.wait_for(process.wait(), timeout=GRACE)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    async def _shutdown(self) -> None:
        """End the program and forget it. (What the idle keeper calls too: it must not call
        stop(), which would cancel the very task that is stopping it.)"""
        self._stopped.set()
        self.started_at = self.used_at = None
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            log.info("Stopping the %s provider (pid %d)", self.provider.name, process.pid)
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            # (The wait is shielded: a cancel — a pool's close() cancelling the idle keeper
            # mid-stop — must not leave the program running or unreaped. The wait carries on
            # without the caller, and is held until it is done so it cannot be collected.)
            waiting = asyncio.ensure_future(self._ended(process))
            _REAPING.add(waiting)
            waiting.add_done_callback(_REAPING.discard)
            await asyncio.shield(waiting)
        reader, self._reader = self._reader, None
        if reader is not None:
            # (The program has exited, so its output is at an end: let the reader finish, which
            # closes the pipe, rather than leaving it to the garbage collector.)
            await asyncio.wait({reader}, timeout=POLL)
            if not reader.done():
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader

    async def _wait_until_ready(self) -> None:
        """Ask the provider whether it is well, every POLL seconds, for at most `start_timeout`
        (a real program starts in real time, so this is counted, not read off the clock).
        Raises ProviderUnavailable when it does not answer (with what it printed)."""
        for _ in range(max(1, math.ceil(self.options.start_timeout.total_seconds() / POLL))):
            if await self._healthy():
                process = self._process
                if process is None:
                    return
                # (It has to be the program udl started that answers: one that exited while
                # something else answers on its port has not started at all. So give it POLL
                # seconds to die, which costs a start half a second and finds the dead ones.)
                exiting = asyncio.ensure_future(process.wait())
                await asyncio.wait({exiting}, timeout=POLL)
                if not exiting.done():
                    exiting.cancel()
                    await asyncio.gather(exiting, return_exceptions=True)
                    return
                await asyncio.gather(exiting, return_exceptions=True)
                await self._drained()
                raise self._exited(process)
            await self._poll()
        await self._drained()
        raise ProviderUnavailable(
            f"The {self.provider.name} provider at {self.options.url} did not answer within "
            f"{self.options.start_timeout.total_seconds():g}s of being started",
            hint=_hint(self),
        )

    async def _drained(self) -> None:
        """Let the reader finish, so what the program printed before it exited is kept."""
        reader = self._reader
        if reader is not None and not reader.done():
            await asyncio.wait({reader}, timeout=POLL)

    async def _healthy(self) -> bool:
        """Whether the provider says it is well (health() never raises)."""
        return (await self.provider.health()).ok

    async def _poll(self) -> None:
        """Wait POLL seconds, or until the program exits: one that has exited will never answer,
        so say so now (with what it printed) rather than waiting out `start_timeout`."""
        process = self._process
        if process is None:
            await asyncio.sleep(0)
            return
        exiting = asyncio.ensure_future(process.wait())
        await asyncio.wait({exiting}, timeout=POLL)
        if not exiting.done():
            exiting.cancel()
        await asyncio.gather(exiting, return_exceptions=True)
        if exiting.done() and not exiting.cancelled():
            await self._drained()
            raise self._exited(process)

    def _exited(self, process: asyncio.subprocess.Process) -> ProviderUnavailable:
        """What a program that exited before it answered is."""
        return ProviderUnavailable(
            f"The {self.provider.name} provider's program exited (code {process.returncode}) "
            "before it answered",
            hint=_hint(self),
        )

    def _drain(self, process: asyncio.subprocess.Process) -> None:
        """Keep the last LOG_TAIL characters of the program's output, in the background."""
        self._reader = asyncio.ensure_future(self._read(process))

    async def _read(self, process: asyncio.subprocess.Process) -> None:
        """Read the program's output, keeping its last LOG_TAIL characters."""
        if process.stdout is None:
            return
        while chunk := await process.stdout.read(1024):
            self._output += chunk
            if len(self._output) > LOG_TAIL:
                del self._output[:-LOG_TAIL]

    def _keep(self) -> None:
        """Keep watching the clock, so the program is stopped once it has been idle long
        enough."""
        if self._keeper is None:
            self._keeper = asyncio.ensure_future(self._watch())

    async def _watch(self) -> None:
        """Stop the program when nothing has asked for it within `idle_timeout` — never while a
        request is running, and never more than `idle_timeout` after the last one."""
        while self.running:
            self._changed.clear()
            if self._in_flight:
                # (A request is running: look again when it ends, or in POLL seconds.)
                await pause(self.clock, timedelta(seconds=POLL), self._stopped, self._changed)
                continue
            left = self.options.idle_timeout - self._idle_for()
            if left <= timedelta(0):
                log.info(
                    "The %s provider has been idle for %s: stopping it",
                    self.provider.name,
                    self.options.idle_timeout,
                )
                self._keeper = None  # (this is the keeper)
                await self._shutdown()
                return
            await pause(self.clock, left, self._stopped, self._changed)

    def _idle_for(self) -> timedelta:
        """How long it has been since the provider was last asked for something."""
        since = self.used_at or self.started_at
        return self.clock.now() - since if since is not None else timedelta.max


def _hint(process: ProviderProcess) -> str:
    """What to do about a provider that did not start: what it printed, else its program (with
    anything that looks like a key left out)."""
    printed = process.output().strip()
    if printed:
        return f"It printed: {_redacted(printed)[-500:]}"
    return (
        f"Run its command by hand to see what is wrong: {' '.join(_shown(process.options.command))}"
    )


def _shown(argv: Sequence[str]) -> list[str]:
    """`argv` with the values of its secret-looking options left out (a command is logged and put
    in an error's hint, so `--api-key sk-…` must not be)."""
    shown: list[str] = []
    skip = False
    for argument in argv:
        if skip:
            shown.append("***")
            skip = False
            continue
        name, sep, _ = argument.partition("=")
        if name.lower() in _SECRET_FLAGS:
            shown.append(f"{name}=***" if sep else argument)
            skip = not sep
            continue
        shown.append(argument)
    return shown


def _redacted(text: str) -> str:
    """`text` with the value of anything that looks like `name=value` or `--name value` hidden."""
    return " ".join(_shown(text.split()))


class ProcessPool:
    """The provider processes a udl has started, one per provider: where each provider is and
    what starts it (its `command`) is what makes two connections the same one, so a chat does not
    start a second server for a provider that is already running."""

    def __init__(self, *, scripts_dir: Path, allowed: Sequence[str] = ()) -> None:
        self.scripts_dir = scripts_dir
        """Where a provider's program may be (from [automations] scripts_dir)."""
        self.allowed = list(allowed)
        """The programs [ai] allowed_commands names (the config file only)."""
        self._running: dict[Key, ProviderProcess] = {}

    def process(self, provider: AnAI) -> ProviderProcess | None:
        """The process of `provider` (started on its first use), or None when udl does not start
        it (no `command`, or options that are not a ProcessOptions')."""
        options = provider.options
        if not isinstance(options, ProcessOptions) or not options.command:
            return None
        key = (options.url, tuple(options.command))
        found = self._running.get(key)
        if found is None:
            found = ProviderProcess(options, provider=provider, clock=provider.clock, pool=self)
            self._running[key] = found
        return found

    def program(self, options: ProcessOptions) -> Path:
        """The program `options.command` starts, if udl may start it. Raises NotAllowed for one
        outside scripts_dir and [ai] allowed_commands, and ConfigError for one that is not
        there."""
        return allowed_command(options.command, scripts_dir=self.scripts_dir, allowed=self.allowed)

    @property
    def running(self) -> list[ProviderProcess]:
        """The processes that are running."""
        return [process for process in self._running.values() if process.running]

    async def close(self) -> None:
        """Stop every provider's program (at udl's shutdown)."""
        for process in list(self._running.values()):
            await process.stop()


_shared: ProcessPool | None = None
"""The pool a provider gets when it is not given one (one per udl process)."""


def default_pool() -> ProcessPool:
    """The pool providers share when nothing else is given: one per udl process, with the
    settings udl runs with — `[automations] scripts_dir` and `[ai] allowed_commands`."""
    global _shared
    if _shared is None:
        settings = Settings.load()
        _shared = ProcessPool(
            scripts_dir=settings.automations.scripts_dir.expanduser(),
            allowed=settings.ai.allowed_commands,
        )
    return _shared


async def close_default_pool() -> None:
    """Stop every provider the default pool started (at udl's shutdown)."""
    global _shared
    pool, _shared = _shared, None
    if pool is not None:
        await pool.close()


def reset_default_pool() -> None:
    """Forget the default pool (a test that has started programs in it)."""
    global _shared
    _shared = None
