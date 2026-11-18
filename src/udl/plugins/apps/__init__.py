"""
-----------------
- apps (kind) -
-----------------

udl.apps: a plugin that brings operations, record kinds, event types, a runtime service and a
UI, without accounts (udl's orchestrator, orch, is one). The kind's interface is
udl.plugins.apps.base.
"""

# Local Imports
from udl.plugins.apps.base import (
    AppContext,
    AppEvent,
    AppEvents,
    AppPlugin,
    AppRecords,
    AppSettings,
)

__all__ = [
    "AppContext",
    "AppEvent",
    "AppEvents",
    "AppPlugin",
    "AppRecords",
    "AppSettings",
]
