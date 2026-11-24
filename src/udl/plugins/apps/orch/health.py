"""
---------------
- health.py -
---------------

A host's health, and what udl does about it (27.13): on 23 Nov btrfs ran out of unallocated
space — `df` still said 116 GiB free, and every commit failed — and later five full suites at
once took disk I/O pressure to 50%. Claude caught both by hand every half hour; this makes it
udl's.

One `sh -c` script reads a host through its `HostShell` — memory, swap, I/O pressure, every disk
udl uses there, and each harness service's memory — and prints one tab-separated line per
measure. A measure a host cannot give (no PSI, not Linux, not btrfs) is left out, which is None
("not measured"), never zero.

`issues_of()` turns those numbers into the issues the thresholds make of them, and `Health`
keeps the open ones as records: what the dispatcher holds turns and gates for, and what raises
the one human action per issue.
"""

# System Imports
import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta
from typing import Final

# Package Imports
from pydantic import BaseModel, ConfigDict, Field

# Local Imports
from udl.clock import Clock
from udl.db.manager import DBManager
from udl.db.records import Lease, RecordQuery
from udl.hosts.shell import HostShell, size_words
from udl.plugins.apps.base import AppRecords
from udl.plugins.apps.orch.actions import ActionRecords, finish, set_action
from udl.plugins.apps.orch.events import (
    HOST_HEALTHY,
    HOST_UNHEALTHY,
    HostHealthy,
    HostIssueSaid,
    HostUnhealthy,
)
from udl.plugins.apps.orch.models import (
    ActionState,
    ActionWhen,
    DiskHealth,
    HarnessHealth,
    HealthIssue,
    HealthKind,
    HostHealth,
)
from udl.plugins.apps.orch.records import (
    HostIssue,
    HostIssueRecords,
    ProjectRecords,
    RepoRecords,
)
from udl.plugins.apps.orch.settings import HealthSettings

__all__ = ["MEASURES", "Health", "Measures", "Reading", "issues_of", "parse", "read"]

log = logging.getLogger(__name__)

MEASURES: Final = r"""\
# udl host health (27.13): one tab-separated line per measure, and nothing for one this host
# cannot give. $1 is how many paths follow, and each path is a disk udl uses there; the harness
# services are the `udl-*.service` units systemd knows, by udl's own naming.
count=$1; shift
if [ -r /proc/meminfo ]; then
  awk '/^MemAvailable:/ {printf "memory\t%d\n", $2 * 1024}' /proc/meminfo
  awk '/^SwapTotal:/ {total = $2} /^SwapFree:/ {free = $2}
       END {if (total) printf "swap\t%d\t%d\n", (total - free) * 1024, total * 1024}' \
    /proc/meminfo
fi
if [ -r /proc/pressure/io ]; then
  awk '/^some/ {for (i = 2; i <= NF; i++) {
         split($i, pair, "=")
         if (pair[1] == "avg60") printf "io\t%s\n", pair[2]}}' /proc/pressure/io
fi
seen=0
while [ "$seen" -lt "$count" ]; do
  path=$1; shift
  seen=$((seen + 1))
  said=$(df -Pk -- "$path" 2>/dev/null | tail -n 1)
  [ -z "$said" ] && continue  # (a path df cannot read is no disk of this host's)
  sizes=$(printf '%s\n' "$said" | awk '{printf "%d\t%d", $4 * 1024, $2 * 1024}')
  mount=$(printf '%s\n' "$said" |
          awk '{mount = $6; for (i = 7; i <= NF; i++) mount = mount " " $i; print mount}')
  filesystem=$(stat -f -c %T -- "$path" 2>/dev/null)
  printf 'disk\t%s\t%s\t%s\t%s\n' "$path" "$sizes" "$filesystem" "$mount"
  if [ "$filesystem" = btrfs ] && command -v btrfs >/dev/null 2>&1; then
    btrfs filesystem usage -b -- "$path" 2>/dev/null | awk -v path="$path" '
      {line = $0; sub(/^[ \t]+/, "", line)
       if (line ~ /^Device unallocated:/) {
         split(line, part, ":"); held = part[2]; gsub(/[ \t]/, "", held)}
       else if (line ~ /^Metadata,/) {
         size = line; sub(/.*Size:/, "", size); sub(/,.*/, "", size); gsub(/[ \t]/, "", size)
         total += size
         use = line; sub(/.*Used:/, "", use); sub(/[ \t].*/, "", use); gsub(/[ \t]/, "", use)
         used += use}}
      END {if (held == "") exit
           if (total) printf "btrfs\t%s\t%s\t%d\t%d\n", path, held, used, total
           else printf "btrfs\t%s\t%s\t\t\n", path, held}'
  fi
done
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user list-units --all --type=service --no-legend 'udl-*.service' 2>/dev/null |
  while read -r unit rest; do
    case "$unit" in *.service) ;; *) continue ;; esac
    said=$(systemctl --user show -p MemoryCurrent -p MemoryMax -- "$unit" 2>/dev/null)
    [ -z "$said" ] && continue
    current=$(printf '%s\n' "$said" | awk -F= '/^MemoryCurrent=/ {print $2}')
    most=$(printf '%s\n' "$said" | awk -F= '/^MemoryMax=/ {print $2}')
    printf 'harness\t%s\t%s\t%s\n' "$unit" "$current" "$most"
  done
fi
exit 0
"""
"""The script one check runs on a host (27.13). It exits 0 whatever it could not measure: a
measure it could not take is one line it did not print, which is how "not measured" travels."""

MEASURE_TIMEOUT: Final = timedelta(seconds=30)
"""How long a health check's script gets: it reads files and runs two commands, and a host that
cannot answer in this is a host that is in trouble anyway."""


class Measures(BaseModel):
    """What one host's script said, read into numbers (27.13). Every measure a host could not
    give is None."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_available: int | None = None
    """Bytes available for a program."""
    swap_used: int | None = None
    """Bytes of swap in use."""
    swap_total: int | None = None
    """Bytes of swap there are."""
    io_pressure: float | None = None
    """`some avg60`, in percent."""
    disks: list[DiskHealth] = Field(default_factory=list[DiskHealth])
    """Every path the script was given, in its order."""
    harnesses: list[HarnessHealth] = Field(default_factory=list[HarnessHealth])
    """Every unit the script was given, in its order."""


def _number(said: str) -> int | None:
    """`said` as a whole number of bytes, or None when it is not one (systemd's `[not set]`, an
    empty field, `infinity`)."""
    try:
        return int(said)
    except ValueError:
        return None


def _share(said: str) -> float | None:
    """`said` as a number (PSI's percent has a decimal point), or None when it is not one."""
    try:
        return float(said)
    except ValueError:
        return None


def parse(said: str) -> Measures:
    """What a host's script printed, as measures: one line per measure, its fields separated by
    tabs (so a path with a space in it stays one field)."""
    memory: int | None = None
    swap_used: int | None = None
    swap_total: int | None = None
    pressure: float | None = None
    disks: list[DiskHealth] = []
    harnesses: list[HarnessHealth] = []
    for line in said.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = line.split("\t")
        measure = fields[0]
        if measure == "memory" and len(fields) == 2:
            memory = _number(fields[1])
        elif measure == "swap" and len(fields) == 3:
            swap_used, swap_total = _number(fields[1]), _number(fields[2])
        elif measure == "io" and len(fields) == 2:
            pressure = _share(fields[1])
        elif measure == "disk" and len(fields) >= 5:
            free, size = _number(fields[2]), _number(fields[3])
            disks.append(
                DiskHealth(
                    path=fields[1],
                    filesystem=_filesystem(fields),
                    free=free,
                    size=size,
                    mount=_mount(fields),
                )
            )
        elif measure == "btrfs" and len(fields) == 5:
            disks = [
                _with_btrfs(disk, fields) if disk.path == fields[1] else disk for disk in disks
            ]
        elif measure == "harness" and len(fields) == 4:
            harnesses.append(
                HarnessHealth(
                    unit=fields[1],
                    memory_current=_number(fields[2]),
                    memory_max=_number(fields[3]),
                )
            )
    return Measures(
        memory_available=memory,
        swap_used=swap_used,
        swap_total=swap_total,
        io_pressure=pressure,
        disks=disks,
        harnesses=harnesses,
    )


def _filesystem(fields: Sequence[str]) -> str | None:
    """The filesystem a disk line names (None when the host could not say)."""
    said = fields[4].strip() if len(fields) > 4 else ""
    return said or None


def _mount(fields: Sequence[str]) -> str | None:
    """The mount point a disk line names (None when the host could not say)."""
    said = fields[5].strip() if len(fields) > 5 else ""
    return said or None


def _with_btrfs(disk: DiskHealth, fields: Sequence[str]) -> DiskHealth:
    """`disk` with what btrfs said about it (its unallocated and metadata numbers)."""
    return disk.model_copy(
        update={
            "unallocated": _number(fields[2]),
            "metadata_used": _number(fields[3]),
            "metadata_total": _number(fields[4]),
        }
    )


def _issue(host: str, kind: HealthKind, detail: str, hint: str) -> HealthIssue:
    """One issue, as its kind's line and hint make it."""
    return HealthIssue(host=host, kind=kind, detail=detail, hint=hint)


def issues_of(health: HostHealth, settings: HealthSettings) -> list[HealthIssue]:
    """What the thresholds make of `health`'s numbers (27.13): nothing when the host is healthy,
    and never an issue for a measure it could not give."""
    found: list[HealthIssue] = []
    host = health.host
    if health.memory_available is not None and health.memory_available < settings.memory_min:
        found.append(
            _issue(
                host,
                HealthKind.MEMORY,
                f"{size_words(health.memory_available)} available, under the "
                f"{size_words(settings.memory_min)} it needs",
                "Finish or stop what is running on it, or raise [apps.orch.health] memory_min.",
            )
        )
    if (
        health.swap_used is not None
        and health.swap_total
        and health.swap_used > health.swap_total * settings.swap_max_ratio
    ):
        found.append(
            _issue(
                host,
                HealthKind.SWAP,
                f"{size_words(health.swap_used)} of {size_words(health.swap_total)} swap in use",
                "A program is holding memory it does not use: find it with `ps` sorted by RSS, "
                "and restart it.",
            )
        )
    breached: dict[HealthKind, list[tuple[DiskHealth, str]]] = {}
    """The disks that break a threshold, by kind: a `HostIssue` is one per host and kind, so the
    disks of one kind are said in one issue."""
    order: list[HealthKind] = []
    """The kinds in the order they were first found, so the list reads as it is built."""
    seen: dict[HealthKind, set[str]] = {}
    """The disks already said for a kind, by mount (or path, when a host cannot say its mount):
    two paths on one mount are one disk, and a balance on it runs once."""
    if health.io_pressure is not None and health.io_pressure > settings.io_pressure_max:
        found.append(
            _issue(
                host,
                HealthKind.IO,
                f"{health.io_pressure:.1f}% I/O pressure, over the {settings.io_pressure_max:.0f}%"
                " it may reach",
                "Fewer suites at once: the full one takes half an hour on a quiet machine, and "
                "three at once take hours.",
            )
        )
    for disk in health.disks:
        for kind, said in _breaches_of(disk, settings):
            if (disk.mount or disk.path) in seen.setdefault(kind, set()):
                continue
            seen[kind].add(disk.mount or disk.path)
            breached.setdefault(kind, []).append((disk, said))
            if kind not in order:
                order.append(kind)
    found.extend(_one_of(host, kind, breached[kind]) for kind in order if kind in breached)
    return found


def _breaches_of(disk: DiskHealth, settings: HealthSettings) -> list[tuple[HealthKind, str]]:
    """The thresholds `disk`'s numbers break (27.13), each with what it says about them."""
    broke: list[tuple[HealthKind, str]] = []
    if disk.free is not None and disk.free < settings.disk_min:
        broke.append(
            (
                HealthKind.DISK,
                f"{disk.path} has {size_words(disk.free)} free, under the "
                f"{size_words(settings.disk_min)} it needs",
            )
        )
    if (
        disk.filesystem == "btrfs"
        and disk.unallocated is not None
        and disk.unallocated < settings.btrfs_unallocated_min
    ):
        broke.append(
            (
                HealthKind.BTRFS_UNALLOCATED,
                f"{disk.path} can allocate {size_words(disk.unallocated)}, under the "
                f"{size_words(settings.btrfs_unallocated_min)} it needs: writes fail with No "
                "space left on device, whatever df says",
            )
        )
    if (
        disk.filesystem == "btrfs"
        and disk.metadata_used is not None
        and disk.metadata_total
        and disk.metadata_used > disk.metadata_total * settings.btrfs_metadata_max_ratio
    ):
        broke.append(
            (
                HealthKind.BTRFS_METADATA,
                f"{disk.path} has {size_words(disk.metadata_used)} of "
                f"{size_words(disk.metadata_total)} metadata used",
            )
        )
    return broke


def _one_of(host: str, kind: HealthKind, disks: Sequence[tuple[DiskHealth, str]]) -> HealthIssue:
    """The one issue `disks` make of `kind` (27.13): what each said, and a hint that names every
    mount a person would run it on."""
    said = "; ".join(one for _, one in disks)
    if kind is HealthKind.DISK:
        return _issue(
            host,
            kind,
            said,
            "udl removes a task's worktree after its merge: look for the ones of tasks that are "
            "done, and for anything else that grew.",
        )
    what = "-dusage=10" if kind is HealthKind.BTRFS_UNALLOCATED else "-musage=10"
    return _issue(host, kind, said, _balance([disk for disk, _ in disks], what))


def _balance(disks: Sequence[DiskHealth], what: str) -> str:
    """What a person runs to free btrfs space on `disks`: one balance per mount, since a mount
    is what it is run on."""
    mounts = ", then ".join(
        f"`sudo btrfs balance start {what} {disk.mount or disk.path}`" for disk in disks
    )
    return f"A balance frees it: {mounts}, which needs root, so a person runs it."


class Health:
    """The hosts' health, and what udl does about it (27.13).

    `check()` reads the hosts it is given, turns their numbers into issues, and keeps the open
    ones as records: a new one is said (`orch.host.unhealthy`) and, when `on_breach` says to
    tell, raises the one action a person reads; one that has cleared is closed
    (`orch.host.healthy`) and its action finished with what cleared it.

    `held()` is what a turn and a gate ask before they start: whether the host has an open issue
    while `on_breach` says to act on it.
    """

    def __init__(
        self,
        *,
        records: AppRecords,
        db: DBManager,
        clock: Clock,
        settings: HealthSettings,
        reading: Reading,
        gates_allowed: int = 1,
    ) -> None:
        self._records = records
        self._db = db
        self._clock = clock
        self._settings = settings
        self._reading = reading
        self._gates_allowed = gates_allowed
        self._one_at_a_time = asyncio.Lock()
        """One check at a time (see `check`)."""
        self._seen: dict[str, datetime] = {}
        """When each host was last checked, in this process: what `stale()` reads (an issue
        already open is a record, so a fresh process still holds the host)."""

    @property
    def settings(self) -> HealthSettings:
        """What counts as a breach, and what udl does about it."""
        return self._settings

    async def see(self, names: Sequence[str]) -> list[HostHealth]:
        """Read `names` and answer what each is, with the issues its numbers make: what the
        operation shows, recording nothing."""
        found: list[HostHealth] = []
        for name in names:
            health = await self._reading(name)
            running = await self._gates_running(name)
            found.append(
                health.model_copy(
                    update={
                        "issues": issues_of(health, self._settings),
                        "gates_running": running,
                        "gates_allowed": self._gates_allowed,
                    }
                )
            )
        return found

    async def check(self, names: Sequence[str]) -> list[HostHealth]:
        """Read `names`, open and close the issues the numbers make, and answer what each host
        is now — with its issues, which is what the operation shows.

        One check at a time: the dispatcher's loop and a turn's freshness both ask, and two
        checks of one host at once would each find its issue new and raise two actions."""
        async with self._one_at_a_time:
            found = await self.see(names)
            for health in found:
                await self._reconciled(health)
                self._seen[health.host] = health.at
            return found

    def stale(self, host: str) -> bool:
        """Whether `host` has not been checked within `check_minutes`: what a gate asks before
        it starts, so a push is never held on a check from an hour ago."""
        seen = self._seen.get(host)
        if seen is None:
            return True
        return self._clock.now() - seen >= timedelta(minutes=self._settings.check_minutes)

    async def freshly(self, host: str) -> None:
        """Check `host` when its last check is older than `check_minutes`: what a gate does
        before it starts."""
        if self.stale(host):
            await self.check([host])

    async def held(self, host: str) -> bool:
        """Whether `host` has an open issue while `on_breach` says to act on it: a turn and a
        gate ask this before they start."""
        if self._settings.on_breach not in ("act", "act_and_tell"):
            return False
        return bool(await HostIssueRecords(self._records).for_host(host))

    async def _reconciled(self, health: HostHealth) -> None:
        """Keep `health`'s host's issues as records: open what is new, close what has cleared,
        say both, and raise or finish the action a person reads."""
        issues = HostIssueRecords(self._records)
        now = self._clock.now()
        known = {one.issue: one for one in await issues.for_host(health.host)}
        present = {issue.kind.value: issue for issue in health.issues}
        opened: list[HostIssue] = []
        for kind, issue in present.items():
            if kind in known:
                if (known[kind].detail, known[kind].hint) != (issue.detail, issue.hint):
                    await issues.save(
                        known[kind].model_copy(update={"detail": issue.detail, "hint": issue.hint})
                    )
                continue
            record = HostIssue(host=health.host, issue=kind, detail=issue.detail, hint=issue.hint)
            if self._settings.on_breach in ("tell", "act_and_tell"):
                record = await self._told(record)
            opened.append(await issues.save(record))
        cleared = [known[kind] for kind in known if kind not in present]
        if opened:
            await self._said(
                HOST_UNHEALTHY.name,
                HostUnhealthy(
                    host=health.host,
                    issues=[
                        HostIssueSaid(host=one.host, kind=one.issue, detail=one.detail)
                        for one in opened
                    ],
                ),
                dedupe=(
                    # (each opening is its own news: event dedupe is unique on type and key, so a
                    # breach that clears and comes back would otherwise say nothing the second
                    # time)
                    f"host.unhealthy:{health.host}:{now.isoformat()}:"
                    + ",".join(sorted(one.issue for one in opened))
                ),
            )
        for record in cleared:
            await issues.close(record, at=now)
            await self._finished(record, at=now)
        if cleared:
            await self._said(
                HOST_HEALTHY.name,
                HostHealthy(
                    host=health.host,
                    cleared=[
                        HostIssueSaid(host=one.host, kind=one.issue, detail=one.detail)
                        for one in cleared
                    ],
                ),
                dedupe=f"host.healthy:{health.host}:{now.isoformat()}",
            )

    async def _told(self, issue: HostIssue) -> HostIssue:
        """`issue` with the action a person reads about it: one per issue while it is open, in
        the project whose checkout is on the host (or the only project udl has). An issue with
        no project to put one in is still an issue, said in the log."""
        project = await self._project_for(issue.host)
        if project is None:
            log.warning("No project to raise an action for %s on %s", issue.issue, issue.host)
            return issue
        action = await set_action(
            ActionRecords(self._records),
            project=project,
            title=f"{issue.host}: {issue.issue.replace('_', ' ')}",
            text=f"{issue.detail}\n\n{issue.hint}",
            when=ActionWhen.NOW,
        )
        return issue.model_copy(update={"project": project, "action": action.key})

    async def _finished(self, issue: HostIssue, *, at: datetime) -> None:
        """Finish the action `issue` raised, with what cleared it: what a person reads when the
        trouble is over. An action a person already finished is left as it is."""
        if issue.action is None or issue.project is None:
            return
        actions = ActionRecords(self._records)
        found = await actions.action(issue.project, issue.action)
        if found is None or found.state is not ActionState.OPEN:
            return
        await finish(
            actions,
            project=issue.project,
            key=issue.action,
            result=f"cleared at {at.isoformat()}: {issue.issue.replace('_', ' ')} on {issue.host}",
        )

    async def _project_for(self, host: str) -> str | None:
        """The project whose repo has a checkout on `host`, or the only project udl has."""
        projects = await ProjectRecords(self._records).projects()
        for project in projects:
            for repo in await RepoRecords(self._records).repos(project.name):
                if host in repo.checkouts:
                    return project.name
        return projects[0].name if len(projects) == 1 else None

    async def _gates_running(self, host: str) -> int:
        """How many of `host`'s gate slots are held now: its lane's leases that have not
        expired (a lease whose run died has, and is not a running gate)."""
        now = self._clock.now()
        prefix = f"orch-gate:{host}:"
        held = await self._db.list_records(Lease, RecordQuery())
        return len(
            {one.name for one in held if one.name.startswith(prefix) and one.expires_at > now}
        )

    async def _said(self, name: str, data: BaseModel, *, dedupe: str) -> None:
        """Publish `name` with `data`, or say in the log that it could not be (an event is never
        worth losing a check over)."""
        events = self._records.events
        if events is None:
            return
        try:
            await events.emit(name, data, dedupe_key=dedupe)
        except Exception:
            log.warning("The %s event could not be published", name, exc_info=True)


type Reading = Callable[[str], Awaitable[HostHealth]]
"""How a host's health is read (27.13): the name of a host, and what it says about itself. It is
a callable so a test's reader is the one the dispatcher's checks run through."""


async def read(
    name: str,
    *,
    shell: HostShell,
    paths: Sequence[str],
    clock: Clock,
    gates_running: int = 0,
    gates_allowed: int = 1,
) -> HostHealth:
    """Read `name`'s health through `shell` (27.13): the script's lines, parsed into measures,
    with the gates it has running and allows, and the time it was read."""
    said = await shell.run(
        ["sh", "-c", MEASURES, "udl-health", str(len(paths)), *paths],
        within=MEASURE_TIMEOUT,
        check=False,
        tail=False,
    )
    measures = parse(said.stdout)
    return HostHealth(
        host=name,
        memory_available=measures.memory_available,
        swap_used=measures.swap_used,
        swap_total=measures.swap_total,
        io_pressure=measures.io_pressure,
        disks=measures.disks,
        harnesses=measures.harnesses,
        gates_running=gates_running,
        gates_allowed=gates_allowed,
        at=clock.now(),
    )
