"""
-------------------
- settings.py -
-------------------

Orch's settings: its `[apps.orch]` table, as every app has one (udl.plugins.apps.base).

A session's transcript is kept for `session_days` (30 unless a config says otherwise); the
gate's timeout and the dispatcher's other knobs come with 27.7.
"""

# Package Imports
from pydantic import PositiveInt

# Local Imports
from udl.plugins.apps.base import AppSettings

__all__ = ["OrchSettings"]


class OrchSettings(AppSettings):
    """Orch's settings: how long a session's transcript is kept (the dispatcher's other knobs
    come with 27.7)."""

    session_days: PositiveInt = 30
    """How many days a session's transcript (its SessionEvents) is kept: what the dispatcher
    (27.7) prunes with SessionRecords.prune()."""
