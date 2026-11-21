"""
----------
- git.py -
----------

The tests' own git: a repo a test makes is its own. It has its own identity, no signing and no
hooks, and git is run without the repo variables a hook exports (udl.hosts.shell.inherited()),
so a `make check` under the pre-commit hook never works on the repo it was run from.
"""

# System Imports
import subprocess
from pathlib import Path
from typing import Final

# Local Imports
from udl.hosts.shell import inherited

# (No signing: the machine's git signs with 1Password, which refuses under load and would fail a
# test for something udl did not do. No hooks: a machine's `core.hooksPath` or `init.templateDir`
# must not run in a test's repo.)
GIT: Final[list[str]] = [
    "git",
    "-c",
    "user.name=udl",
    "-c",
    "user.email=udl@example.com",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.hooksPath=/dev/null",
]


def git(*args: str, cwd: Path) -> str:
    """Run git in `cwd` and answer what it printed (its own error when it fails)."""
    done = subprocess.run(  # noqa: S603 - (a test's own argv, never a user's)
        [*GIT, *args], cwd=cwd, env=inherited(), capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()
