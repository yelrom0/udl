"""
-------------
- config.py -
-------------

Where udl keeps its files (platformdirs user directories)
"""

# System Imports
from pathlib import Path
from typing import Final

# Package Imports
import platformdirs

APP_NAME: Final[str] = "udl"


def data_dir() -> Path:
    """The user data dir, e.g. ~/.local/share/udl."""
    return platformdirs.user_data_path(APP_NAME, appauthor=False)


def default_db_path() -> Path:
    """The default SQLite database file."""
    return data_dir() / "udl.db"
