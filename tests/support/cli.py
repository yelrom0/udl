"""
----------
- cli.py -
----------

Runs `udl` in the tests, through Typer's CliRunner, on a Harness's fakes
"""

# System Imports
from dataclasses import dataclass, field
from pathlib import Path

# Package Imports
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
        return self.runner.invoke(
            app, [str(arg) for arg in args], input=stdin, obj=self.harness.service
        )
