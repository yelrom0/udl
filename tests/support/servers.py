"""
--------------
- servers.py -
--------------

Database servers for the database plugins' tests (PostgreSQL, MongoDB): the server an environment
variable names, else a throwaway Docker container that lasts the run. A run has one container of
each, however many pytest-xdist workers it has: the first worker that needs a server starts it, and
the others read its URL from the run's directory (Q-25, 28 Nov 2026: a pair per worker was 15
containers and 2.2 GiB with three suites at once). With neither, the tests that need the server are
skipped.
"""

# System Imports
import fcntl
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
# The run a process's tests belong to: the pid of the process that owns it, which pytest-xdist's
# workers inherit from their controller's environment.
RUN_VAR: Final[str] = "UDL_TESTS_RUN"
# In the run's directory: every container the run started, one a line (whether it ever answered).
_CONTAINERS: Final[str] = "containers"
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


@dataclass(frozen=True)
class Run:
    """A run of the tests, whose processes share its servers.

    Its owner is the process the run ends with: pytest-xdist's controller, or pytest itself without
    workers. The owner stops the run's containers as it ends, so each is labelled with the owner's
    pid rather than the worker's that started it: a worker xdist restarts mid-run is not a run that
    is gone. The run's directory is where its workers find what the first of them started."""

    owner: int

    @property
    def directory(self) -> Path:
        return Path(tempfile.gettempdir()) / f"udl-tests-{self.owner}"

    @classmethod
    def begin(cls) -> Run:
        """The run this process owns, passed down (in the environment) to the workers it starts.

        What a killed run of the same pid left in the directory is cleared: its URLs name
        containers that are gone."""
        run = cls(os.getpid())
        shutil.rmtree(run.directory, ignore_errors=True)
        os.environ[RUN_VAR] = str(run.owner)
        return run

    @classmethod
    def joined(cls) -> Run:
        """The run a worker belongs to: its controller's, else (nothing said one) its own."""
        try:
            return cls(int(os.environ[RUN_VAR]))
        except KeyError, ValueError:
            return cls.begin()

    def end(self) -> None:
        """Stop every container the run started (one `docker stop` for them all), and clear its
        directory. A daemon that does not answer is left alone: the next run's reap stops what is
        left."""
        try:
            started = (self.directory / _CONTAINERS).read_text().split()
        except FileNotFoundError:
            started = []
        if started:
            with suppress(OSError, subprocess.SubprocessError):
                _docker("stop", "--time", "1", *started)
        shutil.rmtree(self.directory, ignore_errors=True)


# Where a process keeps the run it is in (`config.stash[RUN]`), from tests/conftest.py's
# `pytest_configure` on.
RUN: Final = pytest.StashKey[Run]()


def serve(server: Server, env_var: str, ready: Callable[[str], bool], run: Run) -> str:
    """The URL of a `server` to test against: the one `env_var` names, else the run's container
    of it, started by the first of its workers to ask once `ready(url)` says it answers.

    The others wait on the run's lock while it starts, then read its URL: one is kept only once
    the server answers, so a start that failed is tried again rather than handed on."""
    if url := os.environ.get(env_var):
        return url
    run.directory.mkdir(exist_ok=True)
    name = server.image.partition(":")[0]
    with _held(run.directory / f"{name}.lock"):
        if (known := run.directory / f"{name}.url").exists():
            return known.read_text()
        container = _start(server, env_var, run.owner)
        with (run.directory / _CONTAINERS).open("a") as started:
            started.write(f"{container}\n")
        url = server.url(_published(container, server.port))
        deadline = time.monotonic() + _READY_TIMEOUT
        while not ready(url):
            if time.monotonic() > deadline:
                pytest.fail(f"{server.image} did not start answering at {url}")
            time.sleep(0.2)
        known.write_text(url)
        return url


@contextmanager
def _held(lock: Path) -> Generator[None]:
    """The block runs holding `lock`, which the run's other processes wait on."""
    with lock.open("w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        yield


def _start(server: Server, env_var: str, owner: int) -> str:
    """Start a container of `server` (removed when it stops), labelled as the run `owner` owns;
    skip the test without Docker."""
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
        f"{_PID_LABEL}={owner}",
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
    end that stops its containers, so the container stays (and each one holds memory). Which
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
