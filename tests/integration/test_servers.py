"""
-------------------
- test_servers.py -
-------------------

udl's servers for real, each in its own process, as a user runs them: `udl serve` (Granian)
downloading from the local fixture site
"""

# System Imports
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Package Imports
import httpx
import pytest

# Local Imports
from support.fixture_site import FixtureSite, Page
from udl.core.models import ConfigInfo, DownloadBatch
from udl.db.models import JobStatus


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def write_config(tmp_path: Path, port: int) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        f'download_dir = "{tmp_path / "downloads"}"\n'
        'probe_order = ["http", "yt-dlp", "gallery-dl", "scraper"]\n'
        f'[api]\nport = {port}\nallowed_output_roots = ["{tmp_path}"]\n'
        '[[db.connections]]\nname = "local"\nplugin = "memory"\nurl = "memory://"\n'
    )
    return config


@dataclass
class Served:
    url: str
    process: subprocess.Popen[bytes]
    log: Path


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    """`udl serve` on a free port, until the test ends (then stopped with Ctrl-C)."""
    port = free_port()
    log = tmp_path / "serve.log"
    with log.open("wb") as output:
        process = subprocess.Popen(  # noqa: S603 - the interpreter running the tests
            [sys.executable, "-m", "udl", "--config", str(write_config(tmp_path, port)), "serve"],
            stdout=output,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            httpx.get(f"{url}/openapi.json", timeout=1).raise_for_status()
            break
        except httpx.HTTPError:
            if process.poll() is not None or time.monotonic() > deadline:
                process.kill()
                pytest.fail(f"udl serve did not start:\n{log.read_text()}")
            time.sleep(0.1)
    yield Served(url, process, log)
    process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        pytest.fail(f"udl serve did not stop on Ctrl-C:\n{log.read_text()}")


def test_udl_serve_serves_the_api(served: Served) -> None:
    response = httpx.get(f"{served.url}/config")

    assert response.status_code == 200
    assert ConfigInfo.model_validate(response.json()).settings.api.port == int(
        served.url.rsplit(":", 1)[1]
    )


def test_udl_serve_downloads(served: Served, fixture_site: FixtureSite, tmp_path: Path) -> None:
    response = httpx.post(
        f"{served.url}/downloads",
        params={"wait": True},
        json={"urls": [fixture_site.url(Page.DIRECT_FILE)], "output_dir": str(tmp_path / "out")},
        timeout=30,
    )

    assert response.status_code == 200, response.text
    [job] = DownloadBatch.model_validate(response.json()).jobs
    assert job.status is JobStatus.FINISHED, job.error
    assert [p.read_bytes() for p in job.files] == [fixture_site.content(Page.DIRECT_FILE)]


def test_udl_serve_stops_on_ctrl_c(served: Served) -> None:
    served.process.send_signal(signal.SIGINT)

    assert served.process.wait(timeout=30) == 0
