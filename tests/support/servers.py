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
import subprocess
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Final

# Package Imports
import pytest

# How long a container may take to start (the first run pulls its image) and to answer.
_START_TIMEOUT: Final[float] = 600
_READY_TIMEOUT: Final[float] = 60
# Containers the tests start carry this label (`docker ps --filter label=udl-tests`).
_LABEL: Final[str] = "udl-tests"


@dataclass(frozen=True)
class Server:
    """A database server the tests can start in Docker."""

    image: str
    port: int  # the port the server listens on in the container
    url: Callable[[str], str]  # the server's URL, given the "host:port" it is published on
    env: dict[str, str] = field(default_factory=dict[str, str])


POSTGRES: Final = Server(
    image="postgres:18-alpine",
    port=5432,
    url=lambda address: f"postgresql://udl:udl@{address}/postgres",
    env={"POSTGRES_USER": "udl", "POSTGRES_PASSWORD": "udl"},
)
MONGODB: Final = Server(image="mongo:8", port=27017, url=lambda address: f"mongodb://{address}")


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
    env = [arg for name, value in server.env.items() for arg in ("--env", f"{name}={value}")]
    started = _docker(
        "run",
        "--detach",
        "--rm",
        "--label",
        _LABEL,
        "--publish",
        f"127.0.0.1::{server.port}",
        *env,
        server.image,
        timeout=_START_TIMEOUT,
    )
    if started.returncode != 0:
        pytest.skip(f"needs Docker, or a server named by {env_var}: {started.stderr.strip()}")
    return started.stdout.strip()


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
