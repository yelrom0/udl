"""
----------
- log.py -
----------

Logging setup. Library code logs through `logging.getLogger(__name__)`; each interface calls
configure_logging() once at startup (the CLI maps -q / -v / -vv / -vvv onto `verbosity`).
"""

# System Imports
import logging
import sys
from typing import Final

# Package Imports
from rich.console import Console
from rich.logging import RichHandler

_HANDLER_NAME: Final[str] = "udl"
_PLAIN_FORMAT: Final[str] = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_UDL_LEVELS: Final[dict[int, int]] = {-1: logging.ERROR, 0: logging.WARNING, 1: logging.INFO}


def configure_logging(verbosity: int = 0, *, rich: bool | None = None) -> None:
    """Send log records to stderr, so stdout stays clean for --json output.

    `verbosity`: -1 errors only, 0 warnings, 1 udl info, 2 udl debug, 3+ debug from every
    library too. `rich` (coloured output) defaults to whether stderr is a terminal, so logs
    written to a file or by a server stay one record per line. Calling it again replaces the
    previous setup.
    """
    if rich is None:
        rich = sys.stderr.isatty()
    handler: logging.Handler
    if rich:
        handler = RichHandler(console=Console(stderr=True), show_path=False)
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_PLAIN_FORMAT))
    handler.set_name(_HANDLER_NAME)

    root = logging.getLogger()
    for old in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(old)
        old.close()
    root.addHandler(handler)

    udl_level = _UDL_LEVELS.get(max(verbosity, -1), logging.DEBUG)
    logging.getLogger("udl").setLevel(udl_level)
    root.setLevel(logging.DEBUG if verbosity >= 3 else max(udl_level, logging.WARNING))
