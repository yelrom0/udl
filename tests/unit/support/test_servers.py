"""
--------------------
- test_servers.py -
--------------------

The database servers the tests start in Docker: each container is labelled with the run that
started it, and a run that is killed (a `timeout` around pytest, a cancelled agent-push) never
reaches the `finally` that stops its container. So before a container is started, the ones whose
run is gone are stopped: one `docker ps`, not one call per container.

Docker itself is never called here: `_docker` is a fake, so the test says what `docker ps`
answers and reads what the code asked.
"""

# System Imports
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta

# Package Imports
import pytest

# Local Imports
from support import servers


class FakeDocker:
    """A stand-in for `_docker`: it answers `ps` with what the test scripted, and records every
    call."""

    def __init__(self, ps: str = "") -> None:
        self.ps = ps
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append(args)
        return subprocess.CompletedProcess(
            ["docker", *args], 0, self.ps if args[0] == "ps" else "", ""
        )

    def stopped(self) -> list[str]:
        """The containers the code asked Docker to stop."""
        return [
            argument
            for call in self.calls
            if call[0] == "stop"
            for argument in call
            if argument not in ("stop", "--time", "1")
        ]


def _which_docker(_name: str) -> str | None:
    """`shutil.which` with Docker on the path (the test's own stand-in)."""
    return "/usr/bin/docker"


def ps_line(container: str, pid: str, age: timedelta, host: str = "") -> str:
    """A line of `docker ps`, as the code reads it: the container, when it started, its pid and
    the machine that started it."""
    created = datetime.now(UTC) - age
    said = created.strftime("%Y-%m-%d %H:%M:%S %z UTC")
    return f"{container}\t{said}\t{pid}\t{host}\n"


def dead_pid() -> int:
    """A pid that is not running: a child that has been reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_a_container_whose_run_is_gone_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(ps_line("one", str(dead_pid()), timedelta(minutes=5)))
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == ["one"]


def test_a_container_whose_run_is_live_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker(ps_line("mine", str(os.getpid()), timedelta(minutes=5)))
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == []


def test_an_unlabelled_container_older_than_a_day_is_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    docker = FakeDocker(
        ps_line("old", "", timedelta(days=3)) + ps_line("fresh", "", timedelta(hours=2))
    )
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == ["old"]


def test_every_dead_container_is_stopped_in_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """One `docker ps`, not one call per container: a machine with a backlog is not made to wait
    on a call each."""
    docker = FakeDocker(
        ps_line("one", str(dead_pid()), timedelta(minutes=5))
        + ps_line("two", str(dead_pid()), timedelta(hours=1))
        + ps_line("mine", str(os.getpid()), timedelta(minutes=5))
    )
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert [call[0] for call in docker.calls] == ["ps", "stop"]
    assert sorted(docker.stopped()) == ["one", "two"]


def test_a_pid_label_nobody_wrote_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container labelled with something that is not a pid is not for this code to guess at."""
    docker = FakeDocker(ps_line("odd", "not-a-pid", timedelta(minutes=5)))
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == []


def test_a_container_of_another_machine_is_left_to_its_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pid means nothing on another machine (the daemon may be shared), so only its age says:
    a young one is left alone, whatever its pid."""
    docker = FakeDocker(
        ps_line("theirs", "999999", timedelta(minutes=5), host="some-other-host")
        + ps_line("theirs-old", "999999", timedelta(days=3), host="some-other-host")
    )
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == ["theirs-old"]


def test_a_container_older_than_a_day_goes_even_with_a_live_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pid may be reused, so an old container is a leftover even when its pid is running now."""
    docker = FakeDocker(ps_line("stale", str(os.getpid()), timedelta(days=2)))
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == ["stale"]


def test_a_blank_line_in_docker_ps_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    docker = FakeDocker("\n" + ps_line("one", str(os.getpid()), timedelta(minutes=5)) + "\n")
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == []


def test_a_container_whose_time_docker_printed_oddly_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `CreatedAt` this code cannot read says nothing about age: only its age could tell, and
    that is unknown, so a young container is left alone. Its pid is dead but on another machine,
    which is exactly the case the age rule alone decides."""
    docker = FakeDocker(f"odd\tnot a date at all\t{dead_pid()}\tsome-other-host\n")
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == []


def test_the_time_a_container_started_is_read_or_not() -> None:
    """The reader itself: docker's own format, a zone name, and the two things it cannot read."""
    assert servers.started_at("2026-11-21 10:00:00 +0000 UTC") is not None
    assert servers.started_at("2026-11-21 10:00:00 +1100 AEDT") is not None
    assert servers.started_at("not a date at all") is None  # (three words, none of them a date)
    assert servers.started_at("2026-11-21") is None  # (no time, no offset)
    assert servers.started_at("") is None


def test_a_zone_name_in_the_time_is_read_as_its_offset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker prints the zone's name as well as its offset, and Python knows no "AEDT": the
    offset is what says when the container started."""
    docker = FakeDocker("old\t2026-11-19 08:00:00 +1100 AEDT\t\n")
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == ["old"]


def test_a_pid_that_is_not_ours_to_signal_counts_as_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`os.kill(pid, 0)` may be refused although the process is there: the container stays."""

    def refused(_pid: int, _signal: int) -> None:
        raise PermissionError(1, "not permitted")

    monkeypatch.setattr(servers.os, "kill", refused)
    docker = FakeDocker(ps_line("theirs", "4242", timedelta(minutes=5)))
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == []


def test_a_pid_too_big_to_be_one_counts_as_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """`os.kill` refuses an absurd pid with OverflowError: not a reason to stop the container."""

    def absurd(_pid: int, _signal: int) -> None:
        raise OverflowError("pid too large")

    monkeypatch.setattr(servers.os, "kill", absurd)
    docker = FakeDocker(ps_line("odd", "99999999999999999999", timedelta(minutes=5)))
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == []


def test_a_daemon_that_does_not_answer_leaves_the_run_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hanging daemon must not fail the run that is starting its server."""

    def hanging(*args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["docker", *args], timeout)

    monkeypatch.setattr(servers, "_docker", hanging)

    servers.reap_containers()


def test_a_docker_ps_that_fails_leaves_the_run_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon that is not answering is not a reason to stop starting containers."""

    class Broken(FakeDocker):
        def __call__(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
            self.calls.append(args)
            return subprocess.CompletedProcess(
                ["docker", *args], 1, "", "Cannot connect to the Docker daemon"
            )

    docker = Broken()
    monkeypatch.setattr(servers, "_docker", docker)

    servers.reap_containers()

    assert docker.stopped() == []


def test_a_started_container_says_which_run_it_belongs_to(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The label is what makes a killed run's container findable afterwards."""
    docker = FakeDocker()
    monkeypatch.setattr(servers, "_docker", docker)
    monkeypatch.setattr(servers.shutil, "which", _which_docker)

    # (The helper itself, tested here: the label is what a killed run leaves behind.)
    servers._start(servers.POSTGRES, "UDL_TEST_POSTGRES_URL")  # pyright: ignore[reportPrivateUsage]

    [run] = [call for call in docker.calls if call[0] == "run"]
    assert f"udl-tests.pid={os.getpid()}" in run
    assert f"udl-tests.host={socket.gethostname()}" in run
    assert servers._LABEL in run  # pyright: ignore[reportPrivateUsage]  (the label itself)
    assert "postgres:18-alpine" in run
