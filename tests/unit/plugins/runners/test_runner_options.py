"""
--------------------------------
- test_runner_options.py -
--------------------------------

A runner's options (25.8): how long one turn may run. `turn_within` is written as a `Duration`
(`30m`, `1h`, or a number of seconds), as `[actions] timeout` is, and it is more than none: a zero
is falsy, so the shell's own 30 s default would come back, and less than none would time every
turn out at once.
"""

# System Imports
from datetime import timedelta

# Package Imports
import pytest
from pydantic import ValidationError

# Local Imports
from udl.plugins.runners.base import RunnerOptions


def test_a_turn_is_an_hour_long_by_default() -> None:
    assert RunnerOptions().turn_within == timedelta(hours=1)


def test_a_turn_within_is_written_as_a_duration() -> None:
    # (What a config file writes: `turn_within = "30m"`, read as `Settings.load` reads it.)
    assert RunnerOptions.model_validate({"turn_within": "30m"}).turn_within == timedelta(minutes=30)
    assert RunnerOptions.model_validate({"turn_within": "1h30m"}).turn_within == timedelta(
        hours=1, minutes=30
    )


def test_a_turn_within_may_still_be_a_timedelta() -> None:
    # (What code sets: `ClaudeCodeOptions(turn_within=timedelta(seconds=0.3))` in a test.)
    assert RunnerOptions(turn_within=timedelta(seconds=0.3)).turn_within == timedelta(seconds=0.3)


@pytest.mark.parametrize("within", [0, -5, "0s", "-1m", timedelta(0)])
def test_a_turn_with_no_time_is_refused(within: object) -> None:
    with pytest.raises(ValidationError):
        RunnerOptions.model_validate({"turn_within": within})
