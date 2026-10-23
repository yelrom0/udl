"""
---------------
- test_cli.py -
---------------

The udl command for real, in its own process: Ctrl-C cancels its downloads, which the history
keeps as cancelled
"""

# System Imports
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# Local Imports
from support.fixture_site import FixtureSite, Page


def udl(config: Path, *args: str) -> list[str]:
    return [sys.executable, "-m", "udl", "--config", str(config), *args]


def test_ctrl_c_cancels_the_downloads_and_the_history_keeps_them(
    tmp_path: Path, fixture_site: FixtureSite
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        f'download_dir = "{tmp_path / "downloads"}"\n'
        '[[db.connections]]\nname = "local"\nplugin = "sqlite"\n'
        f'url = "sqlite+aiosqlite:///{tmp_path / "udl.db"}"\n'
    )
    getting = subprocess.Popen(  # noqa: S603 - the interpreter running the tests
        udl(config, "get", fixture_site.url(Page.SLOW_FILE)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ | {"PYTHONUNBUFFERED": "1"},
    )
    part = tmp_path / "downloads" / "localhost" / "slow.mp4.part"
    deadline = time.monotonic() + 20
    while not part.exists():  # (until it is downloading)
        assert getting.poll() is None, getting.communicate()
        assert time.monotonic() < deadline, "the download did not start"
        time.sleep(0.05)

    getting.send_signal(signal.SIGINT)
    _, stderr = getting.communicate(timeout=20)

    assert getting.returncode == 130, stderr
    assert "Cancelled" in stderr
    history = subprocess.run(  # noqa: S603
        udl(config, "history", "--json"), capture_output=True, text=True, check=True
    )
    [download] = json.loads(history.stdout)["downloads"]
    assert download["status"] == "cancelled"
