"""
--------------
- servers.py -
--------------

Database servers for the database plugins' tests (PostgreSQL, MongoDB): the server an environment
variable names, else a throwaway Docker container that lasts the test run. With neither, the tests
that need the server are skipped.
"""

# System Imports
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

# Package Imports
import pytest

# How long a container may take to start (the first run pulls its image) and to answer.
_START_TIMEOUT: Final[float] = 600
_READY_TIMEOUT: Final[float] = 60
# Containers the tests start carry this label (`docker ps --filter label=udl-tests`).
_LABEL: Final[str] = "udl-tests"
# Which run started a container: a run that is killed never reaches `serving`'s finally, so the
# container outlives it and has to be found afterwards (`docker ps` reports the label).
_PID_LABEL: Final[str] = "udl-tests.pid"
# Which machine started it: a pid only means anything on the machine that wrote it (a daemon may
# be shared), so a container from another machine is left to the age rule.
_HOST_LABEL: Final[str] = "udl-tests.host"
# A container this old is a leftover, whatever its pid says (a pid may be reused, and a container
# of a run that is still going is never this old).
_OLD: Final[timedelta] = timedelta(days=1)
# What `docker ps` prints a line of: the container, when it started, and the two labels.
_PS_FORMAT: Final[str] = (
    '{{.ID}}\t{{.CreatedAt}}\t{{.Label "udl-tests.pid"}}\t{{.Label "udl-tests.host"}}'
)
# How `docker ps` prints the time a container started: the date, the time and the offset, then a
# zone's name (which is not read: "AEDT" is not a %Z anything).
_CREATED: Final[str] = "%Y-%m-%d %H:%M:%S %z"


@dataclass(frozen=True)
class Server:
    """A database server the tests can start in Docker."""

    image: str
    port: int  # the port the server listens on in the container
    url: Callable[[str], str]  # the server's URL, given the "host:port" it is published on
    env: dict[str, str] = field(default_factory=dict[str, str])
    options: tuple[str, ...] = ()  # more for `docker run`


POSTGRES: Final = Server(
    image="postgres:18-alpine",
    port=5432,
    url=lambda address: f"postgresql://udl:udl@{address}/postgres",
    env={"POSTGRES_USER": "udl", "POSTGRES_PASSWORD": "udl"},
)
MONGODB: Final = Server(
    image="mongo:8",
    port=27017,
    url=lambda address: f"mongodb://{address}",
    # Each collection and index is a file MongoDB keeps open, and every test makes a database of
    # them: Docker's usual limit (1024) runs out partway (MongoDB asks for 64000).
    options=("--ulimit", "nofile=64000:64000"),
)


@contextmanager
def serving(server: Server, env_var: str, ready: Callable[[str], bool]) -> Generator[str]:
    """The URL of a `server` to test against while the block runs: the one `env_var` names,
    else a container's (stopped afterwards) once `ready(url)` says it answers."""
    if url := os.environ.get(env_var):
        yield url
        return
    container = _start(server, env_var)
    try:
        url = server.url(_published(container, server.port))
        deadline = time.monotonic() + _READY_TIMEOUT
        while not ready(url):
            if time.monotonic() > deadline:
                pytest.fail(f"{server.image} did not start answering at {url}")
            time.sleep(0.2)
        yield url
    finally:
        _docker("stop", "--time", "1", container)


def _start(server: Server, env_var: str) -> str:
    """Start a container of `server` (removed when it stops); skip the test without Docker."""
    if shutil.which("docker") is None:
        pytest.skip(f"needs Docker, or a server named by {env_var}")
    reap_containers()
    env = [arg for name, value in server.env.items() for arg in ("--env", f"{name}={value}")]
    started = _docker(
        "run",
        "--detach",
        "--rm",
        "--label",
        _LABEL,
        "--label",
        f"{_PID_LABEL}={os.getpid()}",
        "--label",
        f"{_HOST_LABEL}={socket.gethostname()}",
        "--publish",
        f"127.0.0.1::{server.port}",
        *env,
        *server.options,
        server.image,
        timeout=_START_TIMEOUT,
    )
    if started.returncode != 0:
        pytest.skip(f"needs Docker, or a server named by {env_var}: {started.stderr.strip()}")
    return started.stdout.strip()


def reap_containers() -> None:
    """Stop the containers of runs that are gone (called before each container is started).

    A run that is killed — a `timeout` around pytest, a cancelled agent-push — never reaches the
    `finally` that stops its container, so the container stays (and each one holds memory). Which
    run started a container is its `udl-tests.pid` label, read on the machine its `udl-tests.host`
    label names: a pid that is not running there is a container that outlived its run. Anything
    else — no pid label (a container from before the labels), another machine's, a pid that is
    not a number — is left to its age, and so is anything older than `_OLD`: a pid may be reused,
    and a container of a run still going is never that old.

    One `docker ps`, and one `docker stop` for them all: a machine with a backlog is not made to
    wait on a call per container. A daemon that does not answer is left alone — the run is not
    the place to fight Docker."""
    try:
        listed = _docker("ps", "--filter", f"label={_LABEL}", "--format", _PS_FORMAT)
    except OSError, subprocess.SubprocessError:
        return  # (a daemon that does not answer: the run is not the place to fight Docker)
    if listed.returncode != 0:
        return
    gone: list[str] = []
    for line in listed.stdout.splitlines():
        container, _, rest = line.partition("\t")
        created, _, rest = rest.partition("\t")
        pid, _, host = rest.partition("\t")
        if not container.strip():
            continue  # (a blank line: docker printed the table's own newline)
        old = (began := started_at(created)) is not None and began < datetime.now(UTC) - _OLD
        if old or not _live(host, pid):
            gone.append(container)
    if gone:
        # (A container that will not stop: not this run's to fight over.)
        with suppress(OSError, subprocess.SubprocessError):
            _docker("stop", "--time", "1", *gone)


def _live(host: str, pid: str) -> bool:
    """Whether to leave alone a container that is not old: its pid, on this machine, running.

    A container with no pid label, or one from another machine (a pid only means something on the
    machine that wrote it), or one whose pid label is not a number, is not this code's to judge:
    the age rule above decides for those."""
    if not pid.strip() or (host.strip() and host.strip() != socket.gethostname()):
        return True
    try:
        pid_of = int(pid)
    except ValueError:
        return True
    return _running(pid_of)


def _running(pid: int) -> bool:
    """Whether a process of that pid is running (a pid that is not is a run that is gone)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError, OverflowError:
        return True  # (it is there; it is just not ours to signal, or not a pid at all)
    return True


def started_at(said: str) -> datetime | None:
    """When `docker ps` said a container started, or None when it said nothing readable.

    The zone's name ("UTC", "AEDT") is dropped: only the offset says when it was, and a name
    Python does not know would make an old container look young."""
    fields = said.split()
    if len(fields) < 3:
        return None
    try:
        return datetime.strptime(" ".join(fields[:3]), _CREATED)
    except ValueError:
        return None


def _published(container: str, port: int) -> str:
    """The "host:port" the container's `port` is published on."""
    found = _docker("port", container, f"{port}/tcp")
    return found.stdout.splitlines()[0].strip()


def _docker(*args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - docker, with arguments the tests choose
        ["docker", *args],  # noqa: S607 - whichever docker is on PATH
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
