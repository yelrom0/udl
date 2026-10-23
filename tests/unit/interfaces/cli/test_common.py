"""
------------------
- test_common.py -
------------------

What the CLI's commands share, for what the commands' tests do not reach
"""

# Package Imports
import pytest
import typer
import typer.main

# Local Imports
from udl.interfaces.cli.app import app
from udl.interfaces.cli.common import EXIT_INTERRUPTED, Runtime, run_async, runs


def test_a_command_runs_in_the_udl_group() -> None:
    with pytest.raises(RuntimeError, match="udl command group"):
        Runtime.of(typer.Context(typer.main.get_command(app)))


def test_only_an_operation_can_be_run() -> None:
    with pytest.raises(ValueError, match="nope"):
        runs("nope")


def test_ctrl_c_exits_with_130(capsys: pytest.CaptureFixture[str]) -> None:
    async def interrupted() -> None:
        raise KeyboardInterrupt

    with pytest.raises(typer.Exit) as exited:
        run_async(interrupted())

    assert exited.value.exit_code == EXIT_INTERRUPTED
    assert "Cancelled" in capsys.readouterr().err
