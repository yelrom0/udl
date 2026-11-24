"""
----------------
- test_health.py -
----------------

A host's health (27.13): what one `sh -c` script says about it, read into numbers, and the
thresholds that make an issue of one.

The measures are tab-separated lines, so a path with a space in it is still one field, and a
measure a host cannot give (no PSI, not Linux, not btrfs) is left out — which is None, and shown
as "not measured", never as zero.
"""

# System Imports
from datetime import UTC, datetime

# Local Imports
from udl.plugins.apps.orch.health import Measures, issues_of, parse
from udl.plugins.apps.orch.models import HealthKind, HostHealth
from udl.plugins.apps.orch.settings import HealthSettings

NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)

LINUX = """\
memory\t8589934592
swap\t1073741824\t2147483648
io\t12.5
disk\t/home/yelrom0/Documents/code/udl\t536870912000\t1000204886016\text4
harness\tudl-opencode-deepseek-b.service\t1073741824\t4294967296
"""

BTRFS = """\
memory\t8589934592
disk\t/home/yelrom0/Documents/code/udl\t536870912000\t1000204886016\tbtrfs
btrfs\t/home/yelrom0/Documents/code/udl\t1073741824\t4294967296\t7516192768
"""

MACOS = """\
disk\t/Users/someone/code/udl\t536870912000\t1000204886016\tapfs
"""


def _health(**overrides: object) -> HostHealth:
    """A host whose every measure is healthy, with `overrides` on top."""
    healthy: dict[str, object] = {
        "host": "jeff-wiggle",
        "memory_available": 8 * 1024**3,
        "swap_used": 0,
        "swap_total": 2 * 1024**3,
        "io_pressure": 1.5,
        "disks": [
            {
                "path": "/home/yelrom0/Documents/code/udl",
                "filesystem": "ext4",
                "free": 500 * 1024**3,
                "size": 1000 * 1024**3,
            }
        ],
        "harnesses": [],
        "gates_running": 0,
        "gates_allowed": 1,
        "at": NOW,
    }
    return HostHealth.model_validate({**healthy, **overrides})


def test_a_linux_host_s_measures_are_read_off_its_lines() -> None:
    found = parse(LINUX)

    assert found.memory_available == 8589934592
    assert (found.swap_used, found.swap_total) == (1073741824, 2147483648)
    assert found.io_pressure == 12.5
    assert [disk.path for disk in found.disks] == ["/home/yelrom0/Documents/code/udl"]
    assert found.disks[0].filesystem == "ext4"
    assert (found.disks[0].free, found.disks[0].size) == (536870912000, 1000204886016)
    assert found.harnesses[0].unit == "udl-opencode-deepseek-b.service"
    assert found.harnesses[0].memory_current == 1073741824
    assert found.harnesses[0].memory_max == 4294967296


def test_a_btrfs_disk_keeps_what_its_filesystem_says_it_has_left() -> None:
    found = parse(BTRFS)

    disk = found.disks[0]
    assert disk.filesystem == "btrfs"
    assert disk.unallocated == 1073741824
    assert (disk.metadata_used, disk.metadata_total) == (4294967296, 7516192768)


def test_a_host_without_psi_or_proc_says_not_measured_rather_than_zero() -> None:
    found = parse(MACOS)

    assert found.memory_available is None
    assert found.swap_used is None
    assert found.swap_total is None
    assert found.io_pressure is None
    assert found.disks[0].filesystem == "apfs"
    assert found.disks[0].unallocated is None


def test_a_measure_that_is_not_a_number_is_not_measured() -> None:
    # (systemd says `[not set]` for a unit that has no current reading, and `infinity` for one
    # with no cap.)
    found = parse("harness\tudl-opencode-a.service\t[not set]\tinfinity\nio\t[not set]\nmemory\t\n")

    assert found.harnesses[0].memory_current is None
    assert found.harnesses[0].memory_max is None
    assert found.io_pressure is None
    assert found.memory_available is None


def test_a_healthy_host_has_no_issues() -> None:
    assert issues_of(_health(), HealthSettings()) == []


def test_little_memory_is_an_issue_that_says_what_it_has() -> None:
    found = issues_of(_health(memory_available=1024**3), HealthSettings())

    assert [issue.kind for issue in found] == [HealthKind.MEMORY]
    assert "1 GiB" in found[0].detail
    assert found[0].hint


def test_swap_mostly_used_is_an_issue() -> None:
    found = issues_of(
        _health(swap_used=int(1.9 * 1024**3), swap_total=2 * 1024**3), HealthSettings()
    )

    assert [issue.kind for issue in found] == [HealthKind.SWAP]


def test_no_swap_at_all_is_not_an_issue() -> None:
    # (A host with swap turned off is a host that chose to: there is no ratio to be over.)
    assert issues_of(_health(swap_used=0, swap_total=0), HealthSettings()) == []


def test_high_io_pressure_is_an_issue() -> None:
    found = issues_of(_health(io_pressure=45.0), HealthSettings())

    assert [issue.kind for issue in found] == [HealthKind.IO]
    assert "45.0%" in found[0].detail


def test_a_disk_nearly_full_is_an_issue() -> None:
    found = issues_of(
        _health(
            disks=[
                {
                    "path": "/w",
                    "filesystem": "ext4",
                    "free": 1024**3,
                    "size": 1000 * 1024**3,
                }
            ]
        ),
        HealthSettings(),
    )

    assert [issue.kind for issue in found] == [HealthKind.DISK]
    assert "/w" in found[0].detail
    assert "worktree" in found[0].hint


def test_btrfs_with_little_unallocated_space_is_an_issue_that_names_the_balance() -> None:
    found = issues_of(
        _health(
            disks=[
                {
                    "path": "/w",
                    "filesystem": "btrfs",
                    "free": 500 * 1024**3,
                    "size": 1000 * 1024**3,
                    "unallocated": 1024**2,
                    "metadata_used": 1024**3,
                    "metadata_total": 4 * 1024**3,
                }
            ]
        ),
        HealthSettings(),
    )

    assert [issue.kind for issue in found] == [HealthKind.BTRFS_UNALLOCATED]
    assert "btrfs balance start -dusage=10 /w" in found[0].hint


def test_btrfs_metadata_nearly_full_is_an_issue() -> None:
    found = issues_of(
        _health(
            disks=[
                {
                    "path": "/w",
                    "filesystem": "btrfs",
                    "free": 500 * 1024**3,
                    "size": 1000 * 1024**3,
                    "unallocated": 100 * 1024**3,
                    "metadata_used": int(3.9 * 1024**3),
                    "metadata_total": 4 * 1024**3,
                }
            ]
        ),
        HealthSettings(),
    )

    assert [issue.kind for issue in found] == [HealthKind.BTRFS_METADATA]


def test_a_btrfs_disk_that_is_full_is_both_a_disk_and_an_unallocated_issue() -> None:
    found = issues_of(
        _health(
            disks=[
                {
                    "path": "/w",
                    "filesystem": "btrfs",
                    "free": 1024**3,
                    "size": 1000 * 1024**3,
                    "unallocated": 0,
                    "metadata_used": 0,
                    "metadata_total": 0,
                }
            ]
        ),
        HealthSettings(),
    )

    assert {issue.kind for issue in found} == {HealthKind.DISK, HealthKind.BTRFS_UNALLOCATED}


def test_a_threshold_is_the_setting_it_is_named_for() -> None:
    found = issues_of(
        _health(memory_available=3 * 1024**3, io_pressure=35.0),
        HealthSettings(memory_min=4 * 1024**3, io_pressure_max=30.0),
    )

    assert {issue.kind for issue in found} == {HealthKind.MEMORY, HealthKind.IO}


def test_every_measure_that_is_missing_is_never_an_issue() -> None:
    # (A host udl cannot measure is not a host udl calls unhealthy: it says nothing.)
    found = issues_of(
        _health(memory_available=None, swap_used=None, swap_total=None, io_pressure=None),
        HealthSettings(),
    )

    assert found == []


def test_a_reading_with_nothing_in_it_is_all_not_measured() -> None:
    found = parse("")

    assert found == Measures()
