"""
-------------------
- test_package.py -
-------------------

Package-wide checks: every module in udl can be imported
"""

# System Imports
import importlib
import pkgutil

# Package Imports
import pytest

# Local Imports
import udl


def _module_names() -> list[str]:
    found = [module.name for module in pkgutil.walk_packages(udl.__path__, prefix="udl.")]
    return ["udl", *sorted(found)]


@pytest.mark.parametrize("name", _module_names())
def test_module_imports(name: str) -> None:
    importlib.import_module(name)
