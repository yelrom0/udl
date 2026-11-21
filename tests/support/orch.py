"""
----------
- orch.py -
----------

The orch app's tests' own registry: the memory database and the secret store, with orch
registered (what a test that runs its operations needs).
"""

# Local Imports
from support.engine import registry
from udl.plugins.apps.orch.plugin import Orch
from udl.plugins.registry import APPS, PluginRegistry


def orch_registry() -> PluginRegistry:
    """The memory database and the secret store, and the orch app (which the tests are of)."""
    plugins = registry()
    plugins.register(APPS, Orch)
    return plugins
