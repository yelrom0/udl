"""
----------------------
- test_host_caps.py -
----------------------

A run under a memory cap, on a real host (27.12): where the host has user systemd the run is a
transient scope of that cap and no swap, and the result says so; where it has not, the run goes
ahead uncapped and the result says `unenforced` rather than nothing. A capped run the kernel
stopped at its cap is OverCap, not a failure of the program.

These are the card's first two done-whens, on this machine: a host without user systemd is what
the test's own shell stands in for.
"""

# System Imports
import sys
from datetime import timedelta
from pathlib import Path
from typing import Final

# Package Imports
import pytest

# Local Imports
from udl.errors import OverCap
from udl.hosts import Host
from udl.hosts.shell import (
    Command,
    LocalShell,
    Probe,
    ResourceLimits,
    capped,
    remote_line,
)


class _Never(Probe):
    """A probe that says the host has no user systemd, without running anything."""

    async def there(self) -> bool:
        return False


class NoSystemd(LocalShell):
    """This machine's shell, as a machine without user systemd is: the probe fails."""

    def __init__(self, host: Host) -> None:
        super().__init__(host)
        self.probe = _Never(self)


@pytest.fixture
def here(tmp_path: Path) -> LocalShell:
    return LocalShell(Host.local(str(tmp_path)))


async def enforced(shell: LocalShell) -> bool:
    """Whether this machine's user systemd runs a capped program (a CI runner may have none)."""
    result = await shell.run(["true"], limits=ResourceLimits(memory=256 * 1024**2), check=False)
    return result.limits == "enforced"


_OWN_LIMIT: Final = (
    "d=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); "
    'for f in "$d/memory.max" "$d/memory/memory.limit_in_bytes"; do '
    'if [ -f "$f" ]; then cat "$f"; break; fi; done'
)
"""The memory cap the run's own cgroup has, read from inside it: cgroup v2's `memory.max`, or
v1's `memory/memory.limit_in_bytes` (this machine is v1)."""


@pytest.mark.host
async def test_a_capped_run_is_a_scope_of_that_cap_and_no_swap(here: LocalShell) -> None:
    result = await here.run(
        ["sh", "-c", _OWN_LIMIT],
        limits=ResourceLimits(memory=256 * 1024**2),
        check=False,
    )

    if result.limits != "enforced":
        pytest.skip("this machine has no user systemd: the uncapped path is the next test")
    assert result.stdout.strip() == str(256 * 1024**2)
    assert result.returncode == 0


async def test_a_host_without_systemd_runs_it_uncapped_and_says_so(tmp_path: Path) -> None:
    shell = NoSystemd(Host.local(str(tmp_path)))

    result = await shell.run(["true"], limits=ResourceLimits(memory=256 * 1024**2), check=False)

    assert result.limits == "unenforced"
    assert result.returncode == 0


async def test_a_run_with_no_cap_says_nothing_about_one(here: LocalShell) -> None:
    result = await here.run(["true"], check=False)

    assert result.limits is None


@pytest.mark.host
async def test_a_capped_run_the_kernel_stopped_is_over_cap(here: LocalShell) -> None:
    """(The card's 22.9 GiB run: the cap is what ends it, and udl says that rather than the
    program's own failure.)"""
    if not await enforced(here):
        pytest.skip("this machine has no user systemd: nothing to enforce a cap")

    with pytest.raises(OverCap, match="passed its memory cap of 256 MiB"):
        await here.run(
            [sys.executable, "-c", "x = bytearray(1024 * 1024 * 1024)"],
            limits=ResourceLimits(memory=256 * 1024**2),
            within=timedelta(minutes=1),
        )


async def test_an_uncapped_run_stopped_the_same_way_is_not_called_over_cap(
    here: LocalShell,
) -> None:
    """(137 without a cap is the program's own business: udl only knows what it asked for.)"""
    result = await here.run(["sh", "-c", "kill -9 $$"], check=False)

    assert result.returncode in (137, -9)
    assert result.limits is None


@pytest.mark.host
async def test_a_capped_stream_the_kernel_stopped_is_over_cap(here: LocalShell) -> None:
    """(It allocates past its cap, so it is one of the card's host tests, not the gate's.)"""
    if not await enforced(here):
        pytest.skip("this machine has no user systemd: nothing to enforce a cap")

    with pytest.raises(OverCap, match="passed its memory cap of 256 MiB"):
        async for _ in here.stream(
            [sys.executable, "-c", "x = bytearray(1024 * 1024 * 1024)"],
            limits=ResourceLimits(memory=256 * 1024**2),
            within=timedelta(minutes=1),
        ):
            pass


@pytest.mark.host
async def test_a_capped_run_over_ssh_arrives_as_137_and_never_255(tmp_path: Path) -> None:
    """(Over SSH, a program the kernel killed at its cap arrives as 137, never as ssh's own 255:
    255 would read as "could not reach the host" instead of "it passed its cap". The remote line
    is what sshd runs — `sh -lc` — and `systemd-run --scope` is what translates the kill into an
    exit code. Run here as sshd runs it, so no host of the user's is needed.)"""
    host = Host.local(str(tmp_path))  # (only for its name: the line is run here, as sshd runs it)
    killer = tmp_path / "killer.sh"
    killer.write_text("kill -9 $$\n")
    limits = ResourceLimits(memory=256 * 1024**2)
    # (The command a capped run becomes: the scope's argv, reporting its own status.)
    capped_run = Command(
        capped(("sh", str(killer)), limits, unit="udl-gate-test"), reports_status=True
    )
    line = remote_line(host, capped_run)

    shell = LocalShell(host)
    if not await enforced(shell):
        pytest.skip("this machine has no user systemd: nothing to enforce a cap")
    assert line.startswith("sh -lc "), "sshd's own shell runs it, so a signal becomes a code"
    said = await shell.run(["sh", "-c", line], check=False)

    assert said.returncode == 137
