"""
----------------------------
- test_optional_drivers.py -
----------------------------

The postgres and mongodb plugins' drivers are udl extras, so their modules load without them:
udl starts, and `udl plugins list` says what is missing, instead of failing to load the plugin
"""

# System Imports
import importlib
import sys

# Package Imports
import pytest

# Local Imports
from udl.plugins.databases.base import DatabasePlugin
from udl.plugins.requirements import unmet


@pytest.mark.parametrize(
    ("module", "driver"),
    [
        ("udl.plugins.databases.postgres", "psycopg"),
        ("udl.plugins.databases.mongodb", "pymongo"),
    ],
)
def test_a_database_plugin_loads_without_its_driver(
    module: str, driver: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    importlib.import_module(module)  # (so it is put back as it was afterwards)
    monkeypatch.delitem(sys.modules, module)
    for name in [n for n in sys.modules if n == driver or n.startswith(f"{driver}.")]:
        monkeypatch.setitem(sys.modules, name, None)  # importing it now fails
    monkeypatch.setitem(sys.modules, driver, None)

    loaded = importlib.import_module(module)

    [plugin] = [
        cls
        for cls in vars(loaded).values()
        if isinstance(cls, type) and issubclass(cls, DatabasePlugin) and cls.__module__ == module
    ]
    assert [requirement.name for requirement in unmet(plugin.requires)] == [driver]
