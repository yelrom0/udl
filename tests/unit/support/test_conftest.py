"""
--------------------
- test_conftest.py -
--------------------

What tests/conftest.py does before any test runs: no git hook variable reaches a test. A hook
exports GIT_DIR, GIT_INDEX_FILE and the rest for the repo it runs in, and `make check` runs under
the pre-commit hook; on 23 Nov 28.6's end-to-end test ran `git init --bare` and `git add` with the
plain `subprocess`, and turned the repo bare and wiped a worktree's index.
"""

# System Imports
import os
import subprocess
import sys
from pathlib import Path

# Local Imports
from udl.hosts.shell import REPO_VARIABLES

ROOT = Path(__file__).resolve().parents[3]
"""The repo, where a child pytest finds tests/conftest.py."""
PROBE = "tests/unit/support/test_conftest.py::test_no_hook_variable_reaches_a_test"
"""The test that says no hook variable is in its environment."""


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
    done = subprocess.run(  # noqa: S603 - (this Python, running one of the repo's own tests)
        [sys.executable, "-m", "pytest", PROBE, "-q", "-p", "no:cacheprovider", "--no-cov"],
        cwd=ROOT,
        env={**os.environ, **hook},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert done.returncode == 0, done.stdout + done.stderr
