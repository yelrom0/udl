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
from typing import Annotated, Final, Literal

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

# Local Imports
from udl.config import ByteSize
from udl.plugins.apps.base import AppSettings
from udl.plugins.settings import FileOnly

__all__ = ["DENIED", "HealthSettings", "OrchSettings"]

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


class HealthSettings(BaseModel):
    """What counts as a host in trouble, and what udl does about it (27.13): its
    `[apps.orch.health]` table. Each threshold is what a healthy host stays above (or below);
    a measure a host cannot give is never a breach."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    memory_min: ByteSize = 2 * 1024**3
    """The memory a host needs available for a program, in bytes."""

    swap_max_ratio: float = 0.9
    """How much of a host's swap may be in use, as a share of it (a host with no swap is never
    over it)."""

    disk_min: ByteSize = 10 * 1024**3
    """The space every disk udl uses on a host needs free, in bytes."""

    btrfs_unallocated_min: ByteSize = 1024**3
    """The space a btrfs disk must be able to allocate, in bytes: below it, `df` says there is
    room and every write still fails (the 23 Nov incident)."""

    btrfs_metadata_max_ratio: float = 0.9
    """How much of a btrfs disk's allocated metadata may be used, as a share of it."""

    io_pressure_max: float = 40.0
    """`/proc/pressure/io`'s `some avg60` a host may reach, in percent (the 23 Nov afternoon ran
    at 45-70%)."""

    check_minutes: PositiveInt = 10
    """How often the dispatcher checks the hosts, in minutes (and before a gate, when the last
    check is older than this)."""

    on_breach: Literal["act_and_tell", "tell", "act"] = "act_and_tell"
    """What a breach does: `act` holds new turns and gates on that host, `tell` raises one action
    per issue, and `act_and_tell` (the default) does both."""


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

    hub_url: Annotated[str | None, FileOnly()] = None
    """Where agents on other hosts reach udl's MCP server (`/mcp`), when they are not on this
    machine. File-only, never on the API: it is the address a session is pointed at (27.7)."""

    max_agents_running: PositiveInt = 4
    """How many agents may be mid-turn at once (27.7): the rest wait their turn."""

    deny: Annotated[list[str], FileOnly()] = Field(default_factory=lambda: list(DENIED))
    """Commands a session's harness must refuse, given to every session as its deny list: what
    only udl may do for an agent (push, open a pull, merge one), and what would throw away work.
    File-only, like the rest of a server's own settings (27.7)."""

    chat_idle: PositiveInt = 24
    """How long a chat's token keeps working with its chat untouched, in hours (27.11): past it,
    the token is refused and taken away, so a chat left open for a week is not a key left open
    for a week."""

    chat_permission_minutes: PositiveInt = 10
    """How long a chat's harness waits for a person to answer its permission prompt, in minutes:
    past it, udl does not allow what it asked (a harness is never left waiting forever)."""

    gates_per_host: PositiveInt = 1
    """How many gate runs a host runs at once (27.12): a lane per slot, `orch-gate:<host>:<n>`.
    One by default, as 27.5 built it: a bigger host raises it."""

    gate_memory: ByteSize | None = 4 * 1024**3
    """The memory a gate run may use, and no swap (27.12): a runaway suite is stopped at the cap
    rather than by the kernel taking the host's memory. None: no cap."""

    session_memory: ByteSize | None = 20 * 1024**3
    """The memory one agent's turn may use (27.12), so a runaway in a turn is that turn's
    failure. None: no cap."""

    health: HealthSettings = Field(default_factory=HealthSettings)
    """What counts as a host in trouble, and what udl does about it (27.13)."""

    session_days: PositiveInt = 30
    """How many days a session's transcript (its SessionEvents) is kept: what the dispatcher
    (27.7) prunes with SessionRecords.prune(), when it starts and on every `PRUNE_EVERY` tick."""
