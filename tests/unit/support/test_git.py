"""
---------------
- test_git.py -
---------------

The tests' own git (tests/support/git.py): a repo a test makes is its own, even when the suite
runs under a git hook. A hook exports GIT_DIR and GIT_INDEX_FILE for the repo it was run in; on
21 Nov a `make check` under the pre-commit hook passed them on, and the tests' `git init --bare`
turned that repo bare.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.git import git


def test_a_repo_the_test_makes_is_its_own_under_a_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    git("init", "-b", "main", cwd=outer)
    config = (outer / ".git" / "config").read_text()
    # (What git exports to a hook that runs in `outer`.)
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(outer / ".git" / "index"))
    inner = tmp_path / "inner"
    inner.mkdir()

    git("init", "--bare", "-b", "main", str(tmp_path / "origin.git"), cwd=tmp_path)
    git("init", "-b", "main", cwd=inner)
    (inner / "README.md").write_text("udl\n")
    git("add", "README.md", cwd=inner)
    git("commit", "-m", "the first commit", cwd=inner)

    assert (outer / ".git" / "config").read_text() == config
    assert not (outer / ".git" / "index").exists()
    assert git("log", "--format=%s", cwd=inner) == "the first commit"


def test_a_repo_the_test_makes_runs_no_hook(tmp_path: Path) -> None:
    # (A machine with `init.templateDir` or `core.hooksPath` set must not run its hooks in a
    # test's repo either.)
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-b", "main", cwd=repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    (repo / "a.txt").write_text("a\n")
    git("add", "a.txt", cwd=repo)

    git("commit", "-m", "it commits", cwd=repo)

    assert git("log", "--format=%s", cwd=repo) == "it commits"
