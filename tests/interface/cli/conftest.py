"""
---------------
- conftest.py -
---------------

The CLI's tests run `udl` on a Harness's fakes (support.cli). Each run configures logging, as
`udl` does, so the logging setup is put back afterwards.
"""

# System Imports
import logging
from collections.abc import Iterator
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.interfaces import Harness


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    root, udl = logging.getLogger(), logging.getLogger("udl")
    handlers, levels = list(root.handlers), (root.level, udl.level)
    yield
    for handler in root.handlers:
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(levels[0])
    udl.setLevel(levels[1])


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


@pytest.fixture
def cli(harness: Harness) -> CLI:
    return CLI(harness)
