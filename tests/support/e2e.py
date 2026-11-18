"""
-----------
- e2e.py -
-----------

The server Playwright's tests run against (`web/playwright.config.ts`): the fixture site, the
fake Google and Slack servers, a fake OpenAI-compatible AI and a real `udl serve`, started from a
config in a temp dir.

It prints the URL it serves on, so Playwright (which reads the server's output) can wait for it,
and stops everything on SIGTERM (and Ctrl-C). A small control API on a free port beside it lets a
test deliver a mail, post a Slack message or script the AI's answer.
"""

# System Imports
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Any, Final, cast

# Package Imports
from support.fixture_site import FixtureSite

# The repo root (web/e2e runs from it, with PYTHONPATH=tests).
ROOT: Final[Path] = Path(__file__).resolve().parents[2]
# The UI plugins' bundles, as `make web` leaves them (`src/udl/plugins/<plugin>/ui/`).
PLUGINS: Final[Path] = ROOT / "src" / "udl" / "plugins"
# Local plugins loaded by the e2e config (the fakes, as plugins).
E2E_PLUGINS: Final[Path] = Path(__file__).resolve().parent / "e2e_plugins"
# How long a test server gets to answer before it is called broken (a loaded machine is slow).
READY_TIMEOUT: Final[float] = 30
# How long the server runs on its own before it stops itself, so a test that leaves it behind
# does not leave it forever (Playwright's webServer stops it with SIGTERM, which it also takes).
SELF_STOP: Final[float] = 600
# How a control call's answer is sent back.
JSON = "application/json"


CONFIG: Final[str] = """\
download_dir = "{downloads}"
plugin_dirs = ["{plugins}"]
probe_order = ["http"]

[api]
host = "127.0.0.1"
port = {port}

[web]
enabled = {web}
ui_plugin_dirs = {ui}

[automations]
enabled = false
"""


def write_config(root: Path, port: int) -> Path:
    """The config `udl serve` reads: a SQLite database and downloads under `root`, the e2e
    plugins as local ones, and the UI plugins' dirs (which may not be built yet)."""
    built = sorted(str(path) for path in PLUGINS.glob("*/ui") if (path / "manifest.json").is_file())
    path = root / "config.toml"
    path.write_text(
        CONFIG.format(
            downloads=root / "downloads",
            plugins=E2E_PLUGINS,
            port=port,
            web="true" if built else "false",
            ui=json.dumps([str(PLUGINS), *built]),
        )
    )
    return path


# ---------------------------------------------------------------------------------------------
# The control API: a test tells it what to deliver, or scripts the AI
# ---------------------------------------------------------------------------------------------


class Control:
    """What e2e tests can ask the fakes to do while udl runs."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mails: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []
        self.ai: list[str] = []

    def add(self, path: str, body: object) -> None:
        """Queue what a control call gave: a mail or a Slack message (an object)."""
        item = mapping(body)
        if item is None:
            raise ValueError(f"{path} needs an object")
        with self.lock:
            if path == "/mail":
                self.mails.append(item)
            else:
                self.messages.append(item)

    def script(self, *answers: str) -> None:
        with self.lock:
            self.ai.extend(answers)

    def next_ai(self) -> str:
        """The next answer the fake AI gives (a default one when nothing is scripted)."""
        with self.lock:
            return self.ai.pop(0) if self.ai else "I am a fake AI."


def mapping(body: object) -> dict[str, object] | None:
    """The body of a control call, as a JSON object (None when it is not one)."""
    if not isinstance(body, dict):
        return None
    pairs = cast("Iterable[tuple[object, object]]", body.items())
    return {str(key): value for key, value in pairs}


class ControlHandler(BaseHTTPRequestHandler):
    """POST /mail, /slack and /ai: each adds what a fake should answer with next."""

    control: Control

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body: object = json.loads(self.rfile.read(length) or b"null")
        except json.JSONDecodeError:
            self.fail(400, "the body is not JSON")
            return
        path = self.path.rstrip("/")
        if path in ("/mail", "/slack"):
            item = mapping(body)
            if item is None:
                self.fail(400, "a mail or message is an object")
                return
            self.control.add(path, item)
        elif path == "/ai":
            if not isinstance(body, list):
                self.fail(400, "the AI's answers are a list of strings")
                return
            items = cast("Iterable[object]", body)
            self.control.script(*(item for item in items if isinstance(item, str)))
        else:
            self.fail(404, "no such control")
            return
        self.send_response(204)
        self.end_headers()

    def fail(self, status: int, message: str) -> None:
        """Answer a call that was not understood with `status` and `message` as JSON."""
        body = json.dumps({"error": message}).encode()
        self.send_response(status)
        self.send_header("Content-Type", JSON)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's name
        pass  # (the server is quiet)


@contextmanager
def serving(control: Control) -> Generator[int]:
    """The control API, answering on a free port while the block runs."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), ControlHandler)
    server.RequestHandlerClass.control = control  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------------------------
# udl serve, and the servers it talks to
# ---------------------------------------------------------------------------------------------


def stop(process: subprocess.Popen[Any]) -> None:
    """Ask a server to stop (SIGTERM, as Playwright's webServer does), and wait for it."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - a server that ignores SIGTERM
        process.kill()
        process.wait(timeout=5)


def answering(url: str, process: subprocess.Popen[Any]) -> bool:
    """Whether udl answers `url` yet (and has not died trying)."""
    if process.poll() is not None:
        raise RuntimeError(f"udl serve stopped with {process.returncode}")
    try:
        with urllib.request.urlopen(url, timeout=1):  # noqa: S310 - a URL we just made
            return True
    except urllib.error.URLError, OSError:
        return False


def wait_until(url: str, process: subprocess.Popen[Any]) -> None:
    """Wait until udl answers `url`, or fail saying it did not in time."""
    deadline = time.monotonic() + READY_TIMEOUT
    while time.monotonic() < deadline:
        if answering(url, process):
            return
        time.sleep(0.2)
    raise TimeoutError(f"udl serve did not answer {url} in {READY_TIMEOUT:g}s")


def main() -> int:
    control = Control()
    stopping = threading.Event()

    def by_signal(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    # (Installed before `serving()` starts its thread, so a SIGTERM that arrives while the
    # servers are still being started is not lost.)
    signal.signal(signal.SIGTERM, by_signal)
    signal.signal(signal.SIGINT, by_signal)

    with (
        tempfile.TemporaryDirectory(prefix="udl-e2e-") as work,
        serving(control) as controls,
        FixtureSite.serve() as site,
    ):
        # (Playwright's webServer waits for a URL, so the server listens on the port the config
        # gives, 8765 by default. It is checked to be free first: a test that starts one of its
        # own would rather be told than fail oddly.)
        port = 8765
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                print(f"e2e: {port} is taken", file=sys.stderr, flush=True)  # noqa: T201
                return 1
        config = write_config(Path(work), port)
        env = {
            **os.environ,
            "UDL_CONFIG": str(config),
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        udl = subprocess.Popen(
            [sys.executable, "-m", "udl", "serve"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        # (udl answers /docs whether or not the UI is built, so it is what says it is up; the
        # shell's own /ui/ comes with 18.3, and until then a test asks for it and gets a 404.)
        url = f"http://127.0.0.1:{port}/docs"
        # (Playwright waits for this line: it is the server's way of saying it is up. Both
        # lines are printed before waiting: a server that is up must say so even if its /docs
        # never answers, or the test would only time out.)
        print(f"e2e: udl serve on {url}", file=sys.stdout, flush=True)  # noqa: T201
        print(  # noqa: T201 - where the fakes are, for a person reading the log
            f"e2e: fixture site on {site.url('/')}, controls on :{controls}", flush=True
        )
        try:
            wait_until(url, udl)
            stopping.wait(SELF_STOP)  # (Playwright's webServer kills after a few minutes)
        except TimeoutError as e:
            print(f"e2e: {e}", file=sys.stderr, flush=True)  # noqa: T201
            return 1
        finally:
            stop(udl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
