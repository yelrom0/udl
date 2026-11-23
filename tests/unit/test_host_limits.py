"""
--------------------------
- test_host_limits.py -
--------------------------

A run under a memory cap (27.12): the name systemd knows it by, and the argv that starts it.

The scope is transient, so its name is escaped the way `systemd-escape` escapes one (a task key
such as `27.3a` and a project with a slash in it both make a valid name), and it is always named
with its `.scope` suffix: a bare name means `.service` to `systemctl`.
"""

# Package Imports
import pytest

# Local Imports
from udl.hosts.shell import ResourceLimits, capped, escaped, scope_of, size_words, unit_name


def test_a_plain_name_keeps_its_dots_and_escapes_its_dashes() -> None:
    """(systemd-escape's own answer: `systemd-escape 27.3a` is `27.3a`, and `F-1.1` is
    `F\\x2d1.1`.)"""
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
