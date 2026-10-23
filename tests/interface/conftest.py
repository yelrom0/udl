"""
---------------
- conftest.py -
---------------

Fixtures for every interface's tests
"""

# System Imports
import logging
from collections.abc import Iterator

# Package Imports
import pytest


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Put the logging setup back: `udl` (and the servers) configure logging as they start."""
    root, udl = logging.getLogger(), logging.getLogger("udl")
    handlers, levels = list(root.handlers), (root.level, udl.level)
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(levels[0])
    udl.setLevel(levels[1])
