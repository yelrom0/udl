"""
------------
- serve.py -
------------

udl serve: the API (with the MCP server at /mcp) on Granian. It runs one worker, since the
downloads' state lives in the process; the worker process makes the app from the settings udl
serve started with.
"""

# System Imports
import functools
import logging
from typing import Final

# Package Imports
from fastapi import FastAPI
from granian import Granian
from granian.constants import Interfaces
from granian.log import LogLevels

# Local Imports
from udl.config import Settings
from udl.interfaces.api.app import create_app, is_local
from udl.log import configure_logging

log = logging.getLogger(__name__)

_GRANIAN_LEVELS: Final[dict[int, LogLevels]] = {-1: LogLevels.warning, 0: LogLevels.info}


def serve(settings: Settings, *, verbosity: int = 0) -> None:
    """Serve the API on api.host and api.port until stopped (Ctrl-C, or SIGTERM)."""
    api = settings.api
    if not is_local(api.host) and api.api_key is None:
        log.warning(
            "udl serve answers other machines on %s, and api.api_key is not set: anyone who "
            "can reach it can download with it",
            api.host,
        )
    server = Granian(
        "udl.interfaces.api.app:create_app",  # (its process's name: the loader makes the app)
        address=api.host,
        port=api.port,
        interface=Interfaces.ASGI,
        workers=1,
        log_level=_GRANIAN_LEVELS.get(max(verbosity, -1), LogLevels.debug),
        log_access=verbosity >= 1,
    )
    server.serve(
        target_loader=functools.partial(worker_app, settings, verbosity), wrap_loader=False
    )


def worker_app(settings: Settings, verbosity: int) -> FastAPI:
    """The app, made in Granian's worker process."""
    configure_logging(verbosity)
    return create_app(settings)
