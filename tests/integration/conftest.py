"""
---------------
- conftest.py -
---------------

Fixtures for the integration tests
"""

# System Imports
import logging

# Package Imports
import pytest

# The loggers whose debug lines a failing integration test reports: the fetcher's (its windows,
# its queue, its checks) and the automations' runtime. Their tests are the ones that have
# failed only on a loaded CI runner, and their logs say which wait ran out. (A logger's own level
# is what filters here: pytest's report handler, which writes "Captured log call", takes every
# level because [tool.pytest] sets no `log_level`, the same as the caplog handler the tests in
# test_captured_logs.py read. Setting a `log_level` there would cut the report back to it.)
_REPORTED = ("udl.fetch", "udl.automations")


@pytest.fixture(autouse=True)
def reported_logs(caplog: pytest.LogCaptureFixture) -> None:
    """Keep the fetcher's and the automations' debug lines for a failing test's report."""
    for name in _REPORTED:
        caplog.set_level(logging.DEBUG, logger=name)
