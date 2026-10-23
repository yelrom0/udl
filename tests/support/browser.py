"""
--------------
- browser.py -
--------------

Skipping browser tests when Playwright's Chromium cannot run (it is installed separately:
`uv run playwright install chromium`)
"""

# System Imports
import functools
import subprocess
import sys

# Package Imports
import pytest

_LAUNCH = """
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    playwright.chromium.launch().close()
"""


@functools.cache
def chromium_problem() -> str | None:
    """Why Chromium cannot run, or None when it can. Checked once, in a separate process (the
    tests' event loop rules out Playwright's sync API)."""
    run = subprocess.run(  # noqa: S603 - this Python, running fixed code
        [sys.executable, "-c", _LAUNCH], capture_output=True, text=True, timeout=120, check=False
    )
    if run.returncode == 0:
        return None
    return (run.stderr.strip().splitlines() or ["it failed to start"])[-1]


def needs_chromium() -> None:
    """Skip the test when Chromium cannot run."""
    if problem := chromium_problem():
        pytest.skip(f"Chromium cannot run ({problem}): uv run playwright install chromium")
