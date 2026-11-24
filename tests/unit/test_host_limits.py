"""
--------------------------
- test_host_limits.py -
--------------------------

A run under a memory cap (27.12): the name systemd knows it by, and the argv that starts it.

The scope is transient, so its name is escaped the way `systemd-escape` escapes one (a task key
such as `27.3a` and a project with a slash in it both make a valid name), and it is always named
with its `.scope` suffix: a bare name means `.service` to `systemctl`.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.harnesses import ScriptedShell
from udl.hosts.shell import (
    Host,
    LocalShell,
    Probe,
    ResourceLimits,
    capped,
    enforces,
    escaped,
    scope_of,
    size_words,
    unit_name,
)


def test_a_plain_name_keeps_its_dots_and_its_inner_dashes() -> None:
    """(A part is escaped by `systemd-escape`'s rules, except that a dash inside it is kept: it is
    how the parts are joined, and `deepseek-b` reads better than `deepseek\\x2db`.)"""
    assert unit_name("udl-gate", "udl", "27.3a", "0a1b2c3d") == "udl-gate-udl-27.3a-0a1b2c3d"
    assert unit_name("udl-gate", "udl", "F-1.1", "0a1b2c3d") == "udl-gate-udl-F-1.1-0a1b2c3d"


def test_a_slash_becomes_a_dash_so_the_name_is_valid() -> None:
    """(A project named `yelrom0/udl` cannot name a unit as it is, and systemd-escape answers
    `yelrom0-udl` for it.)"""
    assert unit_name("udl-gate", "yelrom0/udl", "F-1.1", "0a1b2c3d") == (
        "udl-gate-yelrom0-udl-F-1.1-0a1b2c3d"
    )


def test_a_space_is_escaped() -> None:
    assert unit_name("udl-session", "deep seek") == "udl-session-deep\\x20seek"


def test_a_dash_is_escaped_only_at_the_start() -> None:
    """(A name never starts with a dash — it would read as an option — but a dash inside one is
    how udl joins its parts, so `deepseek-b` and `F-1.1` stay as they are.)"""
    assert escaped("-a-b") == "\\x2da-b"


def test_a_name_with_an_at_sign_has_it_escaped() -> None:
    """(`@` is how systemd marks a template unit: a name with one in it would mean something
    else.)"""
    assert escaped("a@b") == "a\\x40b"


def test_a_non_ascii_name_is_escaped_byte_by_byte() -> None:
    """(A letter is not enough: `isalnum()` is true for `é` and `٣`, and a unit name allows only
    ASCII letters and digits, so `systemd-escape 'café'` writes `caf\\xc3\\xa9` — a name with `é`
    left in it is one systemd refuses.)"""
    assert escaped("café") == "caf\\xc3\\xa9"
    assert escaped("٣") == "\\xd9\\xa3"


def test_a_scope_is_named_with_its_suffix() -> None:
    """(A bare name means `.service` to `systemctl`, so the stop has to say `.scope`.)"""
    assert scope_of("udl-gate-udl-27.3a-0a1b2c3d") == "udl-gate-udl-27.3a-0a1b2c3d.scope"


def test_a_capped_run_is_a_transient_user_scope_with_no_swap() -> None:
    assert capped(
        ["make", "check"], ResourceLimits(memory=4 * 1024**3), unit="udl-gate-udl-27.3a-0a1b2c3d"
    ) == (
        "systemd-run",
        "--user",
        "--scope",
        "-q",
        "--unit=udl-gate-udl-27.3a-0a1b2c3d",
        "-p",
        "MemoryMax=4294967296",
        "-p",
        "MemorySwapMax=0",
        "--",
        "make",
        "check",
    )


def test_a_capped_run_with_no_unit_still_has_one() -> None:
    """(systemd needs a name to stop it by, and udl's own is what a person finds in
    `systemctl --user list-units`.)"""
    argv = capped(["true"], ResourceLimits(memory=1024**2), unit=None)

    assert argv[4].startswith("--unit=udl-")
    assert len(argv[4].removeprefix("--unit=")) == len("udl-") + 8


def test_a_cap_is_said_in_words() -> None:
    assert size_words(4 * 1024**3) == "4 GiB"
    assert size_words(1536 * 1024**2) == "1536 MiB"
    assert size_words(500) == "500 B"


def test_a_cap_must_be_more_than_nothing() -> None:
    with pytest.raises(ValueError, match="not more than nothing"):
        ResourceLimits(memory=0)


async def test_the_caps_probe_is_asked_once_a_host() -> None:
    """(A shell is made on every call — `Hosts.shell()` — so the answer is kept by host name, in
    the map a service's hosts share: a host is probed once, not before every gate and turn.)"""
    shell = ScriptedShell()
    shell.answer(["systemd-run", "--user", "--scope", "-q", "true"], code=0)
    seen: dict[str, bool] = {}

    assert await Probe(shell, seen).there() is True
    assert await Probe(shell, seen).there() is True

    assert len([argv for argv in shell.runs if argv[:2] == ["systemd-run", "--user"]]) == 1


class _Said(Probe):
    """A probe that says one thing, and counts how often it is asked."""

    def __init__(self, shell: LocalShell, said: bool) -> None:
        super().__init__(shell)
        self.said = said
        self.times = 0

    async def there(self) -> bool:
        self.times += 1
        return self.said


async def test_a_gate_that_ran_past_its_time_asks_the_probe_the_shell_already_made(
    tmp_path: Path,
) -> None:
    """(The stop needs to know whether the run was a unit at all, and the shell has already
    asked: a second `systemd-run` before every timed-out gate would be a probe per timeout.)"""
    shell = LocalShell(Host.local(str(tmp_path)))
    said = _Said(shell, True)
    shell.probe = said

    assert await enforces(shell) is True

    assert said.times == 1
