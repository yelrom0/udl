"""
-----------------
- shared_run.py -
-----------------

A probe for test_conftest.py, never collected on its own: run by pytest-xdist on every worker
(`--dist each`), it says which process it is and which run it is in, and makes that run's directory
as a server would, for the test to read once the run has ended.
"""

# System Imports
import os
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support import servers


def test_say_which_run_this_is(pytestconfig: pytest.Config) -> None:
    """(A line each worker writes: its pid, the run's owner and the run's directory.)"""
    run = pytestconfig.stash[servers.RUN]
    run.directory.mkdir(exist_ok=True)
    with Path(os.environ["PROBE_SAID"]).open("a") as said:
        said.write(f"{os.getpid()} {run.owner} {run.directory}\n")
