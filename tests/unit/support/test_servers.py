"""
--------------------
- test_servers.py -
--------------------

The database servers the tests start in Docker: each container is labelled with the run that
started it, and a run that is killed (a `timeout` around pytest, a cancelled agent-push) never
reaches the end that stops its container. So before a container is started, the ones whose run is
gone are stopped: one `docker ps`, not one call per container.

And a run has one container of each server, however many pytest-xdist workers it has (Q-25: a pair
per worker was 15 containers and 2.2 GiB with three suites at once): the first worker to ask starts
it, the others read its URL from the run's directory, and the run's owner stops them as it ends.

Docker itself is never called here: `_docker` is a fake, so the test says what `docker ps`
answers and reads what the code asked.
"""

# System Imports
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support import servers


class FakeDocker:
    """A stand-in for `_docker`: it answers `ps` with what the test scripted, `run` with a new
    container (after `starting` seconds, as a real start takes a while) and `port` with where that
    container is published, and records every call."""

    def __init__(self, ps: str = "", starting: float = 0) -> None:
        self.ps = ps
        self.starting = starting
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        del timeout
        self.calls.append(args)
        answer = ""
        if args[0] == "ps":
            answer = self.ps
        elif args[0] == "run":
            time.sleep(self.starting)
            answer = f"container{len(self.started()) - 1}\n"  # (this call is one of them)
        elif args[0] == "port":
            answer = f"127.0.0.1:{5000 + int(args[1].removeprefix('container'))}\n"
        return subprocess.CompletedProcess(["docker", *args], 0, answer, "")

    def started(self) -> list[tuple[str, ...]]:
        """The `docker run` calls the code made."""
        return [call for call in self.calls if call[0] == "run"]

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


def _no_docker(_name: str) -> str | None:
    """`shutil.which` with no Docker on the path."""
    return None


def _answers(_url: str) -> bool:
    """A server that answers as soon as it is asked."""
    return True


def _never_answers(_url: str) -> bool:
    return False


@pytest.fixture
def docker(monkeypatch: pytest.MonkeyPatch) -> FakeDocker:
    """Docker, faked, on the path."""
    fake = FakeDocker()
    monkeypatch.setattr(servers, "_docker", fake)
    monkeypatch.setattr(servers.shutil, "which", _which_docker)
    monkeypatch.delenv("UDL_TEST_POSTGRES_URL", raising=False)
    monkeypatch.delenv("UDL_TEST_MONGODB_URL", raising=False)
    return fake


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> servers.Run:
    """A run of the tests of its own, owned by a pid that is not this one (as a worker's run is
    its controller's), with its directory under the test's own."""
    monkeypatch.setattr(servers.tempfile, "gettempdir", lambda: str(tmp_path))
    return servers.Run(owner=os.getppid())


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


def test_a_started_container_says_which_run_it_belongs_to(docker: FakeDocker) -> None:
    """The label is what makes a killed run's container findable afterwards: the pid of the run's
    owner, which a worker is not."""
    owner = os.getppid()

    # (The helper itself, tested here: the label is what a killed run leaves behind.)
    servers._start(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", owner)  # pyright: ignore[reportPrivateUsage]

    [started] = docker.started()
    assert f"udl-tests.pid={owner}" in started
    assert f"udl-tests.pid={os.getpid()}" not in started
    assert f"udl-tests.host={socket.gethostname()}" in started
    assert servers._LABEL in started  # pyright: ignore[reportPrivateUsage]  (the label itself)
    assert "postgres:18-alpine" in started


def test_the_workers_of_a_run_share_one_container(docker: FakeDocker, run: servers.Run) -> None:
    """A second worker of the same run gets the first one's server, not one of its own."""
    first = servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)
    second = servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)

    assert len(docker.started()) == 1
    assert first == second == "postgresql://udl:udl@127.0.0.1:5000/postgres"


def test_two_workers_asking_at_once_start_one_container(
    docker: FakeDocker, run: servers.Run
) -> None:
    """The first to ask starts it while the other waits on the run's lock: a start takes seconds,
    and every worker asks at its first database test, often together."""
    docker.starting = 0.2
    urls: list[str] = []

    def worker() -> None:
        urls.append(servers.serve(servers.MONGODB, "UDL_TEST_MONGODB_URL", _answers, run))

    workers = [threading.Thread(target=worker) for _ in range(4)]
    for each in workers:
        each.start()
    for each in workers:
        each.join()

    assert len(docker.started()) == 1
    assert urls == ["mongodb://127.0.0.1:5000"] * 4


def test_each_server_is_a_container_of_its_own(docker: FakeDocker, run: servers.Run) -> None:
    postgres = servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)
    mongodb = servers.serve(servers.MONGODB, "UDL_TEST_MONGODB_URL", _answers, run)

    images = [servers.POSTGRES.image, servers.MONGODB.image]
    assert [next(arg for arg in call if arg in images) for call in docker.started()] == images
    assert postgres.endswith("127.0.0.1:5000/postgres")
    assert mongodb == "mongodb://127.0.0.1:5001"


def test_a_server_the_environment_names_starts_nothing(
    docker: FakeDocker, run: servers.Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UDL_TEST_POSTGRES_URL", "postgresql://someone@elsewhere/postgres")

    url = servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)

    assert url == "postgresql://someone@elsewhere/postgres"
    assert docker.calls == []


def test_the_end_of_a_run_stops_every_container_it_started_in_one_call(
    docker: FakeDocker, run: servers.Run
) -> None:
    servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)
    servers.serve(servers.MONGODB, "UDL_TEST_MONGODB_URL", _answers, run)
    docker.calls.clear()

    run.end()

    assert [call[0] for call in docker.calls] == ["stop"]
    assert sorted(docker.stopped()) == ["container0", "container1"]
    assert not run.directory.exists()


def test_a_server_that_never_answers_fails_and_is_still_stopped_at_the_end(
    docker: FakeDocker, run: servers.Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(servers, "_READY_TIMEOUT", 0)

    with pytest.raises(pytest.fail.Exception, match="did not start answering"):
        servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _never_answers, run)
    run.end()

    assert docker.stopped() == ["container0"]


def test_after_a_server_that_never_answered_the_next_worker_tries_again(
    docker: FakeDocker, run: servers.Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A URL is kept for the others only once the server answers, so a failed start is not handed
    on as a server."""
    monkeypatch.setattr(servers, "_READY_TIMEOUT", 0)
    with pytest.raises(pytest.fail.Exception):
        servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _never_answers, run)

    url = servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)

    assert len(docker.started()) == 2
    assert url.endswith("127.0.0.1:5001/postgres")


def test_a_run_that_started_nothing_ends_without_docker(
    docker: FakeDocker, run: servers.Run
) -> None:
    run.end()

    assert docker.calls == []


def test_a_daemon_that_does_not_answer_the_stop_leaves_the_end_alone(
    docker: FakeDocker, run: servers.Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run is ending: a hanging daemon is not a reason to fail it (the next run's reap stops
    what is left)."""
    servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)

    def hanging(*args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(["docker", *args], timeout)

    monkeypatch.setattr(servers, "_docker", hanging)

    run.end()

    assert not run.directory.exists()


def test_without_docker_a_test_that_needs_a_server_is_skipped(
    docker: FakeDocker, run: servers.Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(servers.shutil, "which", _no_docker)

    with pytest.raises(pytest.skip.Exception, match="needs Docker"):
        servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)


def test_a_run_begun_here_is_passed_down_to_the_workers_it_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The controller begins the run before xdist starts its workers, which inherit its
    environment: a worker joins the controller's run, not one of its own."""
    monkeypatch.delenv(servers.RUN_VAR, raising=False)

    begun = servers.Run.begin()

    assert begun.owner == os.getpid()
    assert os.environ[servers.RUN_VAR] == str(os.getpid())
    assert servers.Run.joined() == begun


def test_a_worker_with_no_run_to_join_has_one_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(It never happens under xdist, but a worker that found nothing would otherwise have no one
    to stop its containers.)"""
    monkeypatch.delenv(servers.RUN_VAR, raising=False)

    assert servers.Run.joined().owner == os.getpid()


def test_a_run_whose_variable_is_not_a_pid_is_one_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(servers.RUN_VAR, "not-a-pid")

    assert servers.Run.joined().owner == os.getpid()


def test_a_run_begun_on_a_reused_pid_starts_its_own_servers(
    docker: FakeDocker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run that was killed leaves its directory, and its URLs name containers the reap has since
    stopped: a new run on the same pid starts afresh."""
    monkeypatch.setattr(servers.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.delenv(servers.RUN_VAR, raising=False)
    left = tmp_path / f"udl-tests-{os.getpid()}"
    left.mkdir()
    (left / "postgres.url").write_text("postgresql://udl:udl@127.0.0.1:1/postgres")

    run = servers.Run.begin()
    url = servers.serve(servers.POSTGRES, "UDL_TEST_POSTGRES_URL", _answers, run)

    assert len(docker.started()) == 1
    assert url.endswith("127.0.0.1:5000/postgres")


def test_the_shared_mongodb_keeps_a_small_cache(docker: FakeDocker, run: servers.Run) -> None:
    """MongoDB's cache is half the machine's memory by default, and one server holding every
    worker's databases grew to 1.6 GiB in a suite (28 Nov): a test's data needs a fraction of it.
    The size is for `mongod`, so it comes after the image."""
    servers.serve(servers.MONGODB, "UDL_TEST_MONGODB_URL", _answers, run)

    [started] = docker.started()
    assert started[-3:] == ("mongo:8", "--wiredTigerCacheSizeGB", "0.25")
