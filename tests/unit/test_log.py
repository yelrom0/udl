"""
---------------
- test_log.py -
---------------

configure_logging(): levels per verbosity, output to stderr, repeat calls
"""

# System Imports
import logging
import sys
from collections.abc import Iterator

# Package Imports
import pytest

# Local Imports
from udl.log import configure_logging


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """configure_logging() changes global state; put it back after each test."""
    root, udl_logger = logging.getLogger(), logging.getLogger("udl")
    handlers, root_level, udl_level = root.handlers[:], root.level, udl_logger.level
    yield
    root.handlers[:] = handlers
    root.setLevel(root_level)
    udl_logger.setLevel(udl_level)


@pytest.mark.parametrize(
    ("verbosity", "udl_level", "root_level"),
    [
        (-1, logging.ERROR, logging.ERROR),  # -q: errors only
        (0, logging.WARNING, logging.WARNING),
        (1, logging.INFO, logging.WARNING),  # -v: udl's own progress, other libraries stay quiet
        (2, logging.DEBUG, logging.WARNING),  # -vv
        (3, logging.DEBUG, logging.DEBUG),  # -vvv: everything, including httpx, yt-dlp, ...
    ],
)
def test_verbosity_sets_the_udl_and_third_party_levels(
    verbosity: int, udl_level: int, root_level: int
) -> None:
    configure_logging(verbosity)

    assert logging.getLogger("udl").level == udl_level
    assert logging.getLogger().level == root_level


def test_plain_logs_go_to_stderr_so_stdout_stays_clean(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(rich=False)

    logging.getLogger("udl.test").warning("careful")

    captured = capsys.readouterr()
    assert "WARNING  udl.test: careful" in captured.err
    assert captured.out == ""


def test_rich_logs_go_to_stderr_so_stdout_stays_clean(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(rich=True)

    logging.getLogger("udl.test").warning("careful")

    captured = capsys.readouterr()
    assert "careful" in captured.err
    assert "udl.test:" not in captured.err
    assert captured.out == ""


def test_rich_is_the_default_when_stderr_is_a_terminal(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    configure_logging()

    logging.getLogger("udl.test").warning("careful")

    err = capsys.readouterr().err
    assert "careful" in err
    assert "udl.test:" not in err


def test_plain_is_the_default_when_stderr_is_not_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging()

    logging.getLogger("udl.test").warning("careful")

    assert "udl.test: careful" in capsys.readouterr().err


def test_configuring_again_replaces_the_handler(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(rich=False)
    configure_logging(rich=False)

    logging.getLogger("udl.test").warning("once")

    assert capsys.readouterr().err.count("once") == 1
