"""
---------------
- checkout.py -
---------------

28.8: after a merge, each host's checkout of the repo follows `main`.

People read the plan in a repo's own checkout (`Repo.checkouts[host]`), while merges land on
GitHub. This is 28.3's after-merge step: for each checkout, `git fetch origin <base>`, then a
fast-forward when the checkout is on `<base>`, clean, and can take it. Anything else is left
alone and named — a `system` note on the merged task and an `orch.checkout.behind` event say
which checkout and why. A host that cannot be reached is the same: noted, never fatal.
"""

# System Imports
from datetime import timedelta
from typing import Final

from pydantic import BaseModel, ConfigDict

from udl.automations.events import EventType
from udl.clock import Clock
from udl.errors import DoesNotExist, PluginFailed, ToolUnavailable

# Local Imports
from udl.hosts.manager import Hosts
from udl.hosts.shell import HostShell
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import HostName, Key, NoteKind, OwnerKind
from udl.plugins.apps.orch.records import (
    Note,
    NoteRecords,
    RepoRecords,
    TaskRecords,
)

__all__ = ["CHECKOUT_BEHIND", "GIT", "CheckoutBehind", "follow"]

GIT: timedelta = timedelta(minutes=5)
"""How long a checkout's git commands may take (as workspace's)."""


class CheckoutBehind(BaseModel):
    """What orch publishes when a host's checkout did not follow the base (28.8)."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)

    project: str
    """The project the repo is of."""
    host: str
    """The host whose checkout did not move."""
    checkout: str
    """The checkout's path on that host."""
    why: str
    """Why it did not move: on another branch, uncommitted changes, diverged, or unreachable."""


CHECKOUT_BEHIND: Final = EventType(
    name="orch.checkout.behind",
    data=CheckoutBehind,
    summary="A host's checkout did not follow the base",
    example=CheckoutBehind(
        project="udl",
        host="local",
        checkout="/home/user/Documents/code/udl",
        why="it is on another branch (feat/x)",
    ),
    subject=lambda event: f"{event.host}'s checkout did not follow: {event.why}",
    source="orch",
)


async def follow(
    records: AppRecords,
    *,
    project: str,
    key: Key,
    base: str,
    shells: Hosts,
    clock: Clock | None = None,
) -> list[CheckoutBehind]:
    """After `key` merged: bring every checkout of the project's repo to `origin/<base>`, and
    answer the ones that did not move (each with why). Never raises: a checkout that cannot be
    read is a note, not a failed merge."""
    repos = await RepoRecords(records).repos(project)
    tasks = TaskRecords(records, clock=clock)
    found = await tasks.task(project, key)
    notes = NoteRecords(records)
    behind: list[CheckoutBehind] = []
    for repo in repos:
        for host, checkout in repo.checkouts.items():
            why = await _bring_up_to_date(shells, host, checkout, base)
            if why is None:
                continue
            said = CheckoutBehind(project=project, host=host, checkout=checkout, why=why)
            behind.append(said)
            if found is not None:
                await notes.add(
                    Note(
                        owner_kind=OwnerKind.TASK,
                        owner=found.id,
                        note_kind=NoteKind.SYSTEM,
                        text=(
                            f"{host}'s checkout ({checkout}) did not follow `{base}` after this "
                            f"merge: {why}. Bring it up with `git -C {checkout} merge --ff-only "
                            f"origin/{base}` when you can."
                        ),
                    )
                )
    return behind


async def _bring_up_to_date(shells: Hosts, host: HostName, checkout: str, base: str) -> str | None:
    """Bring one checkout to `origin/<base>`, or say why it was left. None: it followed."""
    try:
        shell = await shells.shell(host)
        await shell.run(["git", "-C", checkout, "fetch", "origin", base], within=GIT)
    except (DoesNotExist, PluginFailed, ToolUnavailable) as error:
        return f"its host could not be reached ({error})"
    on = await _says(shell, ["git", "-C", checkout, "rev-parse", "--abbrev-ref", "HEAD"])
    if on is None:
        return "its branch could not be read"
    if on != base:
        return f"it is on another branch ({on})"
    state = await _says(shell, ["git", "-C", checkout, "status", "--porcelain"])
    if state is None:
        return "its changes could not be read"
    if state:
        return "it has uncommitted changes"
    if not await _succeeds(shell, ["git", "-C", checkout, "merge", "--ff-only", f"origin/{base}"]):
        return f"it diverged from origin/{base}"
    return None


async def _says(shell: HostShell, args: list[str]) -> str | None:
    """What `git <args>` printed, or None when it failed."""
    try:
        done = await shell.run(args, within=GIT)
    except PluginFailed:
        return None
    return done.stdout.strip()


async def _succeeds(shell: HostShell, args: list[str]) -> bool:
    """Whether `git <args>` succeeded."""
    try:
        await shell.run(args, within=GIT)
    except PluginFailed:
        return False
    return True
