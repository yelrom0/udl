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
from collections.abc import Awaitable
from datetime import timedelta
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict

from udl.automations.events import EventType
from udl.clock import Clock
from udl.errors import DoesNotExist, PluginFailed, ToolUnavailable

# Local Imports
from udl.hosts.shell import HostShell
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.models import HostName, Key, NoteKind, OwnerKind
from udl.plugins.apps.orch.records import (
    Note,
    NoteRecords,
    RepoRecords,
    TaskRecords,
)

__all__ = ["CHECKOUT_BEHIND", "GIT", "CheckoutBehind", "HostShells", "follow"]

GIT: timedelta = timedelta(minutes=5)
"""How long a checkout's git commands may take (as workspace's)."""


class HostShells(Protocol):
    """What `follow` needs of the hosts: the shell of the host called `name` (udl's
    `Hosts.shell`, and a test's fake of one)."""

    def shell(self, name: str) -> Awaitable[HostShell]:
        """The shell that acts on the host called `name`."""
        ...


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
    repo: str,
    shells: HostShells,
    clock: Clock | None = None,
) -> list[CheckoutBehind]:
    """After `key` merged: bring every checkout of the repo `repo` (`owner/name`) to its own
    base, and answer the ones that did not move (each with why). Never raises: a checkout that
    cannot be read is a note, not a failed merge."""
    found = await RepoRecords(records).repos_for(repo)
    repo_record = next((one for one in found if one.project == project), None)
    if repo_record is None:
        return []
    base = repo_record.base_branch
    tasks = TaskRecords(records, clock=clock)
    task = await tasks.task(project, key)
    notes = NoteRecords(records)
    behind: list[CheckoutBehind] = []
    for host, checkout in repo_record.checkouts.items():
        why = await _bring_up_to_date(shells, host, checkout, base)
        if why is None:
            continue
        said = CheckoutBehind(project=project, host=host, checkout=checkout, why=why)
        behind.append(said)
        if task is not None:
            await notes.add(
                Note(
                    owner_kind=OwnerKind.TASK,
                    owner=task.id,
                    note_kind=NoteKind.SYSTEM,
                    text=(
                        f"{host}'s checkout ({checkout}) did not follow `{base}` after this "
                        f"merge: {why}. {advice(why, checkout, base)}"
                    ),
                )
            )
    return behind


def advice(why: str, checkout: str, base: str) -> str:
    """What to do about a checkout that did not follow: only the fix that can help."""
    if why == "it has no branch checked out (a detached HEAD)":
        return f"Bring it onto the branch yourself: `git -C {checkout} checkout {base}`."
    if why.startswith("it is on another branch") or why == "it has uncommitted changes":
        return (
            f"Bring it over yourself (`git -C {checkout} checkout {base}` and commit or stash "
            f"its work), then `git -C {checkout} merge --ff-only origin/{base}`."
        )
    if why.startswith("it diverged"):
        return f"Bring it over yourself: `git -C {checkout} pull --rebase origin {base}`."
    return f"It is at {checkout}; check the host and the path."


async def _bring_up_to_date(
    shells: HostShells, host: HostName, checkout: str, base: str
) -> str | None:
    """Bring one checkout to `origin/<base>`, or say why it was left. None: it followed."""
    try:
        shell = await shells.shell(host)
        await shell.run(["git", "-C", checkout, "fetch", "origin", base], within=GIT)
    except DoesNotExist as error:
        return f"its host is not known ({error})"
    except (PluginFailed, ToolUnavailable) as error:
        return f"its fetch failed: {error}"
    except TimeoutError:
        return f"its fetch timed out after {int(GIT.total_seconds() // 60)} minutes"
    on = await _says(shell, ["git", "-C", checkout, "rev-parse", "--abbrev-ref", "HEAD"])
    if on is None:
        return "its branch could not be read"
    if on == "HEAD":
        return "it has no branch checked out (a detached HEAD)"
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
    """What `git <args>` printed, or None when it failed (or the host did not answer in time)."""
    try:
        done = await shell.run(args, within=GIT)
    except PluginFailed, ToolUnavailable, TimeoutError:
        return None
    return done.stdout.strip()


async def _succeeds(shell: HostShell, args: list[str]) -> bool:
    """Whether `git <args>` succeeded (or the host did not answer in time)."""
    try:
        await shell.run(args, within=GIT)
    except PluginFailed, ToolUnavailable, TimeoutError:
        return False
    return True
