"""
-------------------------
- test_captured_logs.py -
-------------------------

What a failing integration test reports: the fetcher's and the automations' own logs, and the
fake site's and person's, down to debug, so a test that fails only on a loaded CI runner says why
(which window closed, which check timed out, whether the person pressed it) the first time it
fails
"""

# System Imports
import logging
import re

# Package Imports
import pytest


@pytest.mark.parametrize(
    "name", ["udl.fetch.window", "udl.fetch.queue", "udl.automations.runtime", "support.antibot"]
)
def test_the_reported_loggers_debug_lines_are_kept(
    caplog: pytest.LogCaptureFixture, name: str
) -> None:
    logging.getLogger(name).debug("a line for the report")
    assert "a line for the report" in caplog.text


def test_other_debug_lines_are_left_out(caplog: pytest.LogCaptureFixture) -> None:
    logging.getLogger("udl.plugins.downloaders.http").debug("a line nobody asked for")
    assert "a line nobody asked for" not in caplog.text


def test_a_reported_line_says_when_it_was_logged(caplog: pytest.LogCaptureFixture) -> None:
    # (A flaky run's report then shows how long each wait took: the window's 120 s, or a
    # callback that held it.)
    logging.getLogger("udl.fetch.window").debug("a timed line")
    assert re.search(
        r"\d\d:\d\d:\d\d\.\d{3} DEBUG +udl\.fetch\.window:.* a timed line", caplog.text
    )
