"""
---------------
- conftest.py -
---------------

The CLI's tests run `udl` on a Harness's fakes (support.cli)
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.interfaces import Harness


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(tmp_path)


@pytest.fixture
def cli(harness: Harness) -> CLI:
    return CLI(harness)
