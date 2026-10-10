"""
---------------
- conftest.py -
---------------

Fixtures shared by every test
"""

# System Imports
import os
from pathlib import Path

# Package Imports
import pytest

# Playwright looks for its browsers under XDG_CACHE_HOME, which the tests point elsewhere,
# so pin the real location before isolating.
_PLAYWRIGHT_BROWSERS_PATH = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or str(
    Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"
)


@pytest.fixture(autouse=True)
def isolated_user_dirs(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Point the XDG user dirs (and so platformdirs) at a temporary home, and clear UDL_* env vars.

    Tests can never read or write the real user's config, data or downloads.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", _PLAYWRIGHT_BROWSERS_PATH)
    for var, sub in {
        "XDG_CONFIG_HOME": ".config",
        "XDG_DATA_HOME": ".local/share",
        "XDG_STATE_HOME": ".local/state",
        "XDG_CACHE_HOME": ".cache",
    }.items():
        monkeypatch.setenv(var, str(home / sub))
    config_home = home / ".config"
    config_home.mkdir(parents=True)
    (config_home / "user-dirs.dirs").write_text(f'XDG_DOWNLOAD_DIR="{home / "Downloads"}"\n')
    for name in os.environ:
        if name.startswith("UDL_"):
            monkeypatch.delenv(name)
    return home
