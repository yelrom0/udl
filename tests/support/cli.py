"""
----------
- cli.py -
----------

Runs `udl` in the tests, through Typer's CliRunner, on a Harness's fakes
"""

# System Imports
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

# Package Imports
from typer import rich_utils
from typer.testing import CliRunner, Result

# Local Imports
from support.interfaces import Harness
from udl.interfaces.cli.app import app


@dataclass
class CLI:
    """Runs `udl` with the harness's services."""

    harness: Harness
    runner: CliRunner = field(default_factory=lambda: CliRunner(env={"COLUMNS": "200"}))

    def __call__(self, *args: str | Path, stdin: str | None = None) -> Result:
        # Typer forces a terminal (styled, ANSI-coded output) whenever GITHUB_ACTIONS is set: the
        # tests read the plain text a person's pipe gets, wherever they run.
        with patch.object(rich_utils, "FORCE_TERMINAL", False):
            return self.runner.invoke(
                app, [str(arg) for arg in args], input=stdin, obj=self.harness.service
            )
