"""
-------------------
- settings.py -
-------------------

Orch's settings: its `[apps.orch]` table, as every app has one (udl.plugins.apps.base).

A session's transcript is kept for `session_days` (30 unless a config says otherwise), one push's
gate gets `gate_minutes` (60), and a readiness wait holds `wait_minutes` (60); the dispatcher's
other knobs come with 27.7.
"""

# System Imports
from typing import Final

# Package Imports
from pydantic import Field, PositiveInt

# Local Imports
from udl.plugins.apps.base import AppSettings

__all__ = ["DENIED", "OrchSettings"]

DENIED: Final = (
    "git push *",
    "git clean *",
    "git reset --hard *",
    "gh pr create *",
    "gh pr merge *",
    "gh pr close *",
)
"""What a session's harness must refuse: pushing and merging are udl's (an agent asks for them
with `orch_push` and `orch_ready`), and what would throw away work it did not make."""


class OrchSettings(AppSettings):
    """Orch's settings: how long a session's transcript is kept, and how long a push's gate
    gets (the dispatcher's other knobs come with 27.7)."""

    gate_minutes: PositiveInt = 60
    """How long one push's gate run gets, in minutes (a test suite on a busy machine is the slow
    one, 27.5)."""

    wait_minutes: PositiveInt = 60
    """How long `orch_pr_status`'s wait holds, in minutes, before answering with what is still
    running (27.5)."""

    poll_seconds: PositiveInt = 20
    """How often the wait looks at the pull again, in seconds (27.5)."""

    hub_url: str | None = None
    """Where agents on other hosts reach udl's MCP server (`/mcp`), when they are not on this
    machine. File-only, never on the API: it is the address a session is pointed at (27.7)."""

    bot_wait: PositiveInt = 60
    """How long the dispatcher waits for the review bot's comment after a pull's checks are
    done, in minutes, before it asks for the checks to run again (27.7)."""

    max_agents_running: PositiveInt = 4
    """How many agents may be mid-turn at once (27.7): the rest wait their turn."""

    deny: list[str] = Field(default_factory=lambda: list(DENIED))
    """Commands a session's harness must refuse, given to every session as its deny list: what
    only udl may do for an agent (push, open a pull, merge one), and what would throw away work.
    File-only, like the rest of a server's own settings (27.7)."""

    session_days: PositiveInt = 30
    """How many days a session's transcript (its SessionEvents) is kept: what the dispatcher
    (27.7) prunes with SessionRecords.prune()."""
