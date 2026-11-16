"""
-------------------
- test_ci_canary.py -
-------------------

Scratch test for 23.1: it fails only in GitHub Actions, to prove CI's `check` goes red on a failing
test. It is removed in the next commit of the same PR.
"""

# System Imports
import os


def test_fails_in_ci_only() -> None:
    assert os.environ.get("GITHUB_ACTIONS") != "true"
