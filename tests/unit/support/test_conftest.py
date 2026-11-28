"""
--------------------
- test_conftest.py -
--------------------

What tests/conftest.py does for every test. No git hook variable reaches a test: a hook exports
GIT_DIR, GIT_INDEX_FILE and the rest for the repo it runs in, and `make check` runs under the
pre-commit hook; on 23 Nov 28.6's end-to-end test ran `git init --bare` and `git add` with the plain
`subprocess`, and turned the repo bare and wiped a worktree's index. And a leak fails in the module
that made it: an unclosed socket or pipe left in a reference cycle is found when garbage is
collected, which on a busy worker was often inside a later, unrelated test (Q-22). And where
UDL_REQUIRE_SLOT is set (the build agents' service, on jeff-wiggle), a whole suite runs only in one
of rule 9's full-suite slots: one run by hand beside the others' made a fourth on 28 Nov. And the
pytest-xdist workers of a run share its database servers (Q-25): the run is the controller's, and
the controller clears it up as it ends.
"""

# System Imports
import os
import subprocess
import sys
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.hosts.shell import REPO_VARIABLES

ROOT = Path(__file__).resolve().parents[3]
"""The repo, where a child pytest finds tests/conftest.py."""
PROBE = "tests/unit/support/test_conftest.py::test_no_hook_variable_reaches_a_test"
"""The test that says no hook variable is in its environment."""
LEAKS = "tests/unit/support/probes/leaks.py"
"""A module whose test leaves an open socket for a collection to find."""
CLEAN = "tests/unit/support/probes/clean.py"
"""A module that leaks nothing, run after it."""
SHARED_RUN = "tests/unit/support/probes/shared_run.py"
"""A test each worker runs, saying which run it is in."""


def child_pytest(
    *args: str, env: dict[str, str] | None = None, coverage_in: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the repo's pytest on `args` in a child process, as a run of the suite has it.

    With `coverage_in`, it runs with coverage, as `make check` does (its report and its floor left
    out), and keeps its data there: in the repo's own `.coverage` it would erase and combine the
    data of the run it is part of."""
    flags = ["--cov", "--cov-report=", "--cov-fail-under=0"] if coverage_in else ["--no-cov"]
    if coverage_in is not None:
        env = {
            **(os.environ if env is None else env),
            "COVERAGE_FILE": str(coverage_in / ".coverage"),
        }
    return subprocess.run(  # noqa: S603 - (this Python, running the repo's own tests)
        [sys.executable, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider", *flags],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_no_hook_variable_reaches_a_test() -> None:
    """(On its own this passes wherever pytest runs without a hook variable; the test below runs it
    with them set, so it says something everywhere.)"""
    assert not REPO_VARIABLES & os.environ.keys()


def test_a_suite_run_under_a_git_hook_gives_no_test_its_variables(tmp_path: Path) -> None:
    """(A child pytest started as the pre-commit hook would start it, with GIT_DIR and
    GIT_INDEX_FILE set, runs the probe above: it passes only because tests/conftest.py dropped
    them, so this fails on any machine if that goes.)"""
    hook = {
        "GIT_DIR": str(tmp_path / ".git"),
        "GIT_INDEX_FILE": str(tmp_path / ".git" / "index"),
    }
    done = child_pytest(PROBE, env={**os.environ, **hook})

    assert done.returncode == 0, done.stdout + done.stderr


def test_a_leak_fails_in_the_module_that_made_it() -> None:
    """(The probe's socket is freed only by a collection: the one tests/conftest.py makes as the
    module ends fails the leaking module's test, and the clean module after it passes.)"""
    done = child_pytest(LEAKS, CLEAN, "-rA")

    out = done.stdout + done.stderr
    assert "ERROR at teardown of test_a_socket_is_left_open" in out, out
    assert "ResourceWarning: unclosed <socket.socket" in out, out
    assert f"PASSED {CLEAN}::test_nothing_is_left_open" in out, out
    # (The leaking test's own call passed: it is its teardown, as its module ends, that errors.)
    assert "2 passed, 1 error" in out, out


def without_slots(**names: str) -> dict[str, str]:
    """This run's environment without the slot's two variables (an agent's run has both), and
    `names`."""
    kept = {k: v for k, v in os.environ.items() if k not in {"UDL_REQUIRE_SLOT", "WAIT_SLOT_HELD"}}
    return {**kept, **names}


def test_a_whole_suite_run_outside_a_slot_is_refused_where_one_is_required(tmp_path: Path) -> None:
    """(An agent's `make check` by hand, or a bare `agent-push`, runs pytest with coverage and no
    slot: it stops before any test, saying how to run it.)"""
    done = child_pytest(CLEAN, env=without_slots(UDL_REQUIRE_SLOT="1"), coverage_in=tmp_path)

    out = done.stdout + done.stderr
    assert done.returncode == pytest.ExitCode.USAGE_ERROR, out
    assert "wait-slot" in out, out
    assert "passed" not in out, out


@pytest.mark.parametrize(
    ("names", "cov"),
    [
        ({"UDL_REQUIRE_SLOT": "1", "WAIT_SLOT_HELD": "slot0"}, True),
        ({"UDL_REQUIRE_SLOT": "1"}, False),
        ({}, True),
        ({"UDL_REQUIRE_SLOT": "0"}, True),
    ],
    ids=[
        "in-a-slot",
        "a-few-files-without-coverage",
        "where-no-slot-is-required",
        "where-it-is-turned-off",
    ],
)
def test_a_run_in_a_slot_a_targeted_run_and_a_run_elsewhere_go_ahead(
    names: dict[str, str], cov: bool, tmp_path: Path
) -> None:
    """(Through `wait-slot`, a quick run of a few files, and CI or a person's own run all go on; so
    does a machine whose UDL_REQUIRE_SLOT is anything but `1`.)"""
    done = child_pytest(CLEAN, env=without_slots(**names), coverage_in=tmp_path if cov else None)

    assert done.returncode == 0, done.stdout + done.stderr
    # (A run with coverage keeps its data where it was told, never in the repo's own `.coverage`.)
    assert (tmp_path / ".coverage").exists() == cov


def test_the_workers_of_a_run_are_in_their_controller_s_run(tmp_path: Path) -> None:
    """(Two workers each run the probe: both say the same run, owned by neither of them, so a
    server one of them starts is labelled with the controller's pid and found by the other; and
    the run's directory is gone once the controller has ended.)"""
    said = tmp_path / "said"
    done = child_pytest(
        SHARED_RUN, "-n", "2", "--dist", "each", env={**os.environ, "PROBE_SAID": str(said)}
    )

    assert done.returncode == 0, done.stdout + done.stderr
    lines = [line.split() for line in said.read_text().splitlines()]
    workers = {worker for worker, _, _ in lines}
    [owner] = {owner for _, owner, _ in lines}
    [directory] = {directory for _, _, directory in lines}
    assert len(workers) == 2
    assert owner not in workers
    assert not Path(directory).exists()
