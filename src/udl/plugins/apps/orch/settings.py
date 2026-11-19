"""
-------------------
- settings.py -
-------------------

Orch's settings: its `[apps.orch]` table, as every app has one (udl.plugins.apps.base).

There is nothing to set yet — the gate's timeout and the dispatcher's knobs are Phase 27's —
so this is the settings an app gets when it has none of its own.
"""

# Local Imports
from udl.plugins.apps.base import AppSettings

__all__ = ["OrchSettings"]


class OrchSettings(AppSettings):
    """Orch's settings: nothing yet (Phase 27 adds the dispatcher's)."""
