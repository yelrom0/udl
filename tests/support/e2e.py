"""
----------
- e2e.py -
----------

The server Playwright's tests run against (`web/playwright.config.ts`): the fixture site, a
real `udl serve`, and a small control API a test can post to.

The fakes the card asks for (Google, Slack, an OpenAI-compatible AI) are **not** here yet: they
arrive with the tasks that need them (14's and 15's integration tasks, 18.5's sign-in), as local
plugins in `tests/support/e2e_plugins/`. The control API is ready for them — a mail, a Slack
message or the AI's answers can be queued now, and the fakes will read the queues.

The server is isolated from the developer's own udl: a config in a temp dir, a SQLite database
and downloads under it, the memory secret store, and no `UDL_*` variables inherited.

It prints the URL it serves on, and stops everything on SIGTERM (and Ctrl-C); it also stops
itself after `SELF_STOP` seconds, so a test run that leaves it behind does not leave it forever.
It serves on the port `--port` names, else on a free one the OS picks: never a port of udl's
own (8765, `udl serve`'s default), where the developer's udl may be answering.
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
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Final

# Package Imports
from pydantic import TypeAdapter, ValidationError

# Local Imports
from support.fixture_site import FixtureSite

# The repo root (web/e2e runs from it, with PYTHONPATH=tests).
ROOT: Final[Path] = Path(__file__).resolve().parents[2]
# Local plugins loaded by the e2e config (the fakes, as plugins).
E2E_PLUGINS: Final[Path] = Path(__file__).resolve().parent / "e2e_plugins"
# The UI plugins the shell loads in the e2e run: a working one and one that throws (18.9).
E2E_UI_PLUGINS: Final[Path] = Path(__file__).resolve().parent / "e2e_ui_plugins"
# 18.10's proof: the shadcn/ui example plugin, as `make web` builds it (a folder of bundles).
SHADCN_EXAMPLE: Final[Path] = ROOT / "web" / "examples" / "shadcn" / "dist"
# How long a test server gets to answer before it is called broken (a loaded machine is slow).
READY_TIMEOUT: Final[float] = 30
# How long the server runs on its own before it stops itself, so a test that leaves it behind
# does not leave it forever (Playwright's webServer stops it with SIGTERM, which it also takes).
SELF_STOP: Final[float] = 600
# The API key the e2e server asks for, and the browser tests sign in with.
API_KEY: Final = "e2e-key"
# A control call's answer.
JSON = "application/json"

# A control call's body: an object (a mail, a Slack message) or a list of strings (the AI's).
_MAIL: Final = TypeAdapter(dict[str, object])
_AI: Final = TypeAdapter(list[str])

CONFIG: Final[str] = """\
download_dir = "{downloads}"
plugin_dirs = ["{plugins}"]
probe_order = ["http"]

[[db.connections]]
name = "local"
plugin = "sqlite"
url = "sqlite+aiosqlite:///{database}"
primary = true

[secrets]
store = "memory"

[api]
host = "127.0.0.1"
port = {port}
api_key = "{key}"

[web]
enabled = true
ui_plugin_dirs = {ui}

[automations]
enabled = false
"""


def write_config(root: Path, port: int) -> Path:
    """The config `udl serve` reads: a SQLite database and downloads under `root`, the e2e
    plugins as local ones, the memory secret store, and udl on `port`. The web UI is served (a
    browser is what most of the e2e tests are), from the build `make e2e` makes first. The API
    has `API_KEY`, so the tests sign in as a user does (18.4's session) rather than run against
    an open server."""
    # (18.10's example is built by `make e2e`; a run that has not built it serves the other two.)
    ui_dirs = [E2E_UI_PLUGINS] + ([SHADCN_EXAMPLE] if SHADCN_EXAMPLE.is_dir() else [])
    path = root / "config.toml"
    path.write_text(
        CONFIG.format(
            downloads=root / "downloads",
            database=root / "udl.db",
            plugins=E2E_PLUGINS,
            ui=json.dumps([str(one) for one in ui_dirs]),
            port=port,
            key=API_KEY,
        )
    )
    return path


# ---------------------------------------------------------------------------------------------
# The control API: a test tells it what a fake should answer with next
# ---------------------------------------------------------------------------------------------


class Control:
    """What e2e tests can ask the fakes to do while udl runs."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mails: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []
        self.ai: list[str] = []

    def deliver(self, mail: dict[str, object]) -> None:
        with self.lock:
            self.mails.append(json.loads(json.dumps(mail)))

    def post(self, message: dict[str, object]) -> None:
        with self.lock:
            self.messages.append(json.loads(json.dumps(message)))

    def script(self, *answers: str) -> None:
        with self.lock:
            self.ai.extend(answers)

    def next_ai(self) -> str:
        """The next answer the fake AI gives (a default one when nothing is scripted)."""
        with self.lock:
            return self.ai.pop(0) if self.ai else "I am a fake AI."


def handler_for(control: Control) -> type[BaseHTTPRequestHandler]:
    """A handler class that answers to `control`: a class of its own, so no instance state is
    kept on the class the server was built with."""

    class Handler(BaseHTTPRequestHandler):
        """POST /mail, /slack and /ai: each adds what a fake should answer with next."""

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body: object = json.loads(self.rfile.read(length) or b"null")
            except json.JSONDecodeError:
                self.fail(400, "the body is not JSON")
                return
            path = self.path.rstrip("/")
            try:
                if path == "/mail":
                    control.deliver(dict(_MAIL.validate_python(body)))
                elif path == "/slack":
                    control.post(dict(_MAIL.validate_python(body)))
                elif path == "/ai":
                    control.script(*_AI.validate_python(body))
                else:
                    self.fail(404, "no such control")
                    return
            except ValidationError as e:
                self.fail(400, "; ".join(error["msg"] for error in e.errors()))
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

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base's name
            pass  # (the server is quiet)

    return Handler


@contextmanager
def serving(control: Control) -> Generator[int]:
    """The control API, answering on a free port while the block runs."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(control))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


# ---------------------------------------------------------------------------------------------
# udl serve
# ---------------------------------------------------------------------------------------------


def stop(process: subprocess.Popen[bytes]) -> None:
    """Ask a server to stop (SIGTERM, as Playwright's webServer does), and wait for it."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - a server that ignores SIGTERM
        process.kill()
        process.wait(timeout=5)


def answering(url: str, process: subprocess.Popen[bytes]) -> bool:
    """Whether udl answers `url` yet (and has not died trying)."""
    if process.poll() is not None:
        raise RuntimeError(f"udl serve stopped with {process.returncode}")
    try:
        with urllib.request.urlopen(url, timeout=1):  # noqa: S310 - a URL we just made
            return True
    except urllib.error.URLError, OSError:
        return False


def wait_until(
    url: str, process: subprocess.Popen[bytes], *, timeout: float = READY_TIMEOUT
) -> None:
    """Wait until udl answers `url`, or fail saying it did not in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if answering(url, process):
            return
        time.sleep(0.2)
    raise TimeoutError(f"udl serve did not answer {url} in {timeout:g}s")


def environment(config: Path, home: Path) -> dict[str, str]:
    """The environment `udl serve` runs in: the config, the XDG dirs under `home` (so every
    other default of udl's — the browser profile, fetched pages, scripts — is the temp dir's
    too), its own checkout on the path, and nothing of the caller's `UDL_*` (which would
    override the config)."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("UDL_")}
    env.update(
        {
            "UDL_CONFIG": str(config),
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local/share"),
            "XDG_STATE_HOME": str(home / ".local/state"),
            "XDG_CACHE_HOME": str(home / ".cache"),
        }
    )
    return env


def port_from(argv: list[str] | None, default: int | None) -> int | None:
    """The port the command line asks for (`--port N`), else `default` (`None` when the caller
    has none, and `main()` then picks a free one)."""
    given = list(argv or ())
    for index, arg in enumerate(given):
        if arg == "--port" and index + 1 < len(given):
            return int(given[index + 1])
        if arg.startswith("--port="):
            return int(arg.split("=", 1)[1])
    return default


def free_port() -> int:
    """A port nothing is listening on: the OS picks it, and it is free again by the time this
    returns, so the caller binds that one and no other."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main(
    *,
    port: int | None = None,
    self_stop: float = SELF_STOP,
    make_site: Callable[[], AbstractContextManager[object]] | None = None,
    argv: list[str] | None = None,
) -> int:
    port = port_from(argv, port)
    if port is None:
        port = free_port()
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
        (make_site or FixtureSite.serve)(),
    ):
        # (A port somebody else holds is said, not fought over: Playwright passes the free one
        # it picked in web/playwright.config.ts, so a taken one was taken since.)
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                print(f"e2e: {port} is taken", file=sys.stderr, flush=True)  # noqa: T201
                return 1
        home = Path(work)
        config = write_config(home, port)
        udl = subprocess.Popen(
            [sys.executable, "-m", "udl", "serve"],
            cwd=ROOT,
            env=environment(config, home),
            stdout=subprocess.DEVNULL,
        )
        # (udl answers /docs whether or not the UI is built, so it is what says it is up; the
        # shell's own /ui/ comes with 18.3, and until then a test asks for it and gets a 404.)
        url = f"http://127.0.0.1:{port}/docs"
        # (Both lines are printed before waiting: a server that is up must say so even if its
        # /docs never answers, or the test would only time out.)
        print(f"e2e: udl serve on {url}", file=sys.stdout, flush=True)  # noqa: T201
        print(f"e2e: controls on :{controls}", flush=True)  # noqa: T201
        try:
            wait_until(url, udl)
            stopping.wait(self_stop)  # (Playwright's webServer kills after a few minutes)
        except TimeoutError as e:
            print(f"e2e: {e}", file=sys.stderr, flush=True)  # noqa: T201
            return 1
        finally:
            stop(udl)
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
