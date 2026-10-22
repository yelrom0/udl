"""
--------------
- plugins.py -
--------------

Plugin kinds that exist only in the tests, so the registry can be tested on its own
"""

# System Imports
from abc import ABC, abstractmethod
from typing import ClassVar, Final

# Local Imports
from udl.plugins.registry import PluginKind


class Widget(ABC):
    """A test plugin type."""

    name: ClassVar[str]

    @abstractmethod
    def spin(self) -> str: ...


class Gadget(ABC):
    """A second test plugin type."""

    name: ClassVar[str]


WIDGETS: Final = PluginKind("udl_test.widgets", Widget)
GADGETS: Final = PluginKind("udl_test.gadgets", Gadget)
