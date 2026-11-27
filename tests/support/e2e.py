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
import urllib.parse
import urllib.request
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import FrameType
from typing import Final

# Package Imports
from pydantic import BaseModel, TypeAdapter, ValidationError

# Local Imports
from support.fixture_site import FixtureSite
from support.git import git
from support.github import FakeGitHub

# The repo root (web/e2e runs from it, with PYTHONPATH=tests).
ROOT: Final[Path] = Path(__file__).resolve().parents[2]
# Local plugins loaded by the e2e config (the fakes, as plugins).
E2E_PLUGINS: Final[Path] = Path(__file__).resolve().parent / "e2e_plugins"
# 28.6's scripted runner, and 29.12's chat: a harness whose turns are a script in a file, so a
# browser test drives a chat end to end without a model or a harness installed.
ORCH_E2E_PLUGINS: Final[Path] = Path(__file__).resolve().parent / "orch_e2e_plugins"
# The UI plugins the shell loads in the e2e run: a working one and one that throws (18.9).
E2E_UI_PLUGINS: Final[Path] = Path(__file__).resolve().parent / "e2e_ui_plugins"
# 18.10's proof: the shadcn/ui example plugin, as `make web` builds it (a folder of bundles).
SHADCN_EXAMPLE: Final[Path] = ROOT / "web" / "examples" / "shadcn" / "dist"
# 18.10's starting point: the template plugin, built the same way (a folder of bundles).
TEMPLATE: Final[Path] = ROOT / "web" / "packages" / "template" / "dist"
# 29.2's: the orchestrator's own UI, which `make web` builds beside its plugin (the folder *is*
# the bundle, so the plugin serves it with itself rather than from `ui_plugin_dirs`).
ORCH_UI: Final[Path] = ROOT / "src" / "udl" / "plugins" / "apps" / "orch" / "ui"
# The shell, as `make web` builds it: the other half of the web tree, and what says a build ran.
SHELL_DIST: Final[Path] = ROOT / "src" / "udl" / "interfaces" / "web" / "dist"
# How long a test server gets to answer before it is called broken (a loaded machine is slow).
READY_TIMEOUT: Final[float] = 30
# How long the server runs on its own before it stops itself, so a test that leaves it behind
# does not leave it forever (Playwright's webServer stops it with SIGTERM, which it also takes).
SELF_STOP: Final[float] = 600
# The API key the e2e server asks for, and the browser tests sign in with.
API_KEY: Final = "e2e-key"
# The GitHub the e2e tests merge against: the fake's own token and repository, so a test that
# merges a pull is merging the fake's, never github.com's.
GITHUB_TOKEN: Final = "ghp_fake_token"  # noqa: S105 - (the fake's own)
GITHUB_REPO: Final = "acme/widgets"
# A control call's answer.
JSON = "application/json"

# A control call's body: an object (a mail, a Slack message) or a list of strings (the AI's).
_MAIL: Final = TypeAdapter(dict[str, object])


class Push(BaseModel):
    """A test's push: the commit that is now at a pull's head (29.10: its own pull, if it says)."""

    sha: str
    """The commit the pull's head moves to."""
    repo: str = GITHUB_REPO
    """The repository its pull is in (the fake's own, unless the test says)."""
    number: int = 7
    """The pull's number (the fake's first, unless the test says)."""


class Said(BaseModel):
    """A review bot's comment on a pull (29.10's story: what the bot says, and where)."""

    repo: str
    """The repository the pull is in."""
    number: int
    """The pull's number."""
    text: str
    """What the bot says."""
    login: str = "claude"
    """Who says it (a bot's login)."""


_AI: Final = TypeAdapter(list[str])
# A scripted runner's turns: a list of objects (29.12's chat, and 28.6's orchestration).
_TURNS: Final = TypeAdapter(list[dict[str, object]])

CONFIG: Final[str] = """\
download_dir = "{downloads}"
plugin_dirs = ["{plugins}", "{orch_plugins}"]
probe_order = ["http"]

[[db.connections]]
name = "local"
plugin = "sqlite"
url = "sqlite+aiosqlite:///{database}"
primary = true

[secrets]
# (A file in the harness's own temp home, not memory: 29.10's story starts udl again once its
# agents exist — the dispatcher reads them at start (27.7) — and an account connected before the
# restart is still connected after it, as a person's is.)
store = "file"
file = "{root}/secrets.json"

[api]
host = "127.0.0.1"
port = {port}
api_key = "{key}"

[web]
enabled = true
ui_plugin_dirs = {ui}

[automations]
# (On: the integrations' watchers run, so a pull's link is kept current as it is in a real one.)
enabled = true

[apps.orch.health]
# (29.10: a turn needs a healthy host, and the harness's disks are its temp folder's — a few
# gigabytes, where a real host's are not. Everything else is the default.)
disk_min = "1GiB"

[integrations.scripted]
dir = "{root}"

[integrations.github]
# (Often enough that a test sees a pull's event in a few seconds, not the 60 s default.)
poll_every = "2s"
"""


def half_built() -> str | None:
    """What is wrong with the web tree, if it is half built: the shell is there and a plugin the
    e2e tests expect is not. (Neither built is a fresh checkout, and the harness serves the
    hand-written bundles alone; `make e2e` builds both, so a half-built one is a mistake worth
    saying out loud rather than a page quietly missing a plugin.)"""
    if not SHELL_DIST.is_dir():
        return None
    # (Named, not by their folders: two of them end in `dist`.)
    plugins = {
        "the shadcn example": SHADCN_EXAMPLE,
        "the template": TEMPLATE,
        "the orchestrator's UI": ORCH_UI,
    }
    missing = [name for name, path in plugins.items() if not path.is_dir()]
    if not missing:
        return None
    said = " and ".join(missing)
    verb = "is" if len(missing) == 1 else "are"
    return f"the shell is built and {said} {verb} not built: run `make web`"


def write_config(root: Path, port: int) -> Path:
    """The config `udl serve` reads: a SQLite database and downloads under `root`, the e2e
    plugins as local ones, the memory secret store, and udl on `port`. The web UI is served (a
    browser is what most of the e2e tests are), from the build `make e2e` makes first. The API
    has `API_KEY`, so the tests sign in as a user does (18.4's session) rather than run against
    an open server."""
    # (18.10's two plugins are built by `make e2e`; a run that has not built them serves the
    # hand-written bundles alone.)
    ui_dirs = [E2E_UI_PLUGINS] + [one for one in (SHADCN_EXAMPLE, TEMPLATE) if one.is_dir()]
    path = root / "config.toml"
    path.write_text(
        CONFIG.format(
            downloads=root / "downloads",
            database=root / "udl.db",
            plugins=E2E_PLUGINS,
            orch_plugins=ORCH_E2E_PLUGINS,
            root=root,
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

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self.lock = threading.Lock()
        self.mails: list[dict[str, object]] = []
        self.messages: list[dict[str, object]] = []
        self.ai: list[str] = []
        self.fake_github: FakeGitHub | None = None
        """The fake GitHub, once it is running (what a test's push is made on)."""
        self.restart: Callable[[], None] | None = None
        """Start udl again, while the block runs (29.10: the dispatcher reads its agents when it
        starts, so a story makes them first and starts udl after)."""
        self.repo: tuple[Path, Path] | None = None
        """The story's own repository: its checkout and its bare origin, once they are made
        (29.10: an agent works in the checkout and pushes to the origin, as it does for real)."""

    def deliver(self, mail: dict[str, object]) -> None:
        with self.lock:
            self.mails.append(json.loads(json.dumps(mail)))

    def post(self, message: dict[str, object]) -> None:
        with self.lock:
            self.messages.append(json.loads(json.dumps(message)))

    def script(self, *answers: str) -> None:
        with self.lock:
            self.ai.extend(answers)

    def script_turns(self, turns: list[dict[str, object]]) -> None:
        """Write the scripted runner's turns: what its next turn does (29.12's chat reads it)."""
        if self.root is None:
            raise ValueError("this control has no folder to script a turn in")
        with self.lock:
            (self.root / "turns.json").write_text(json.dumps(turns))

    def calls(self) -> list[str]:
        """Every call the fake GitHub has had (what the watcher looked at).

        The fake appends from uvicorn's thread, without this lock: `list()` of a list is one
        bytecode under the GIL, which is enough for a test that only looks for a call it made."""
        return list(self.fake_github.calls) if self.fake_github is not None else []

    def push(self, sha: str, *, repo: str = GITHUB_REPO, number: int = 7) -> None:
        """What the fake GitHub is asked to do: a new commit on the pull's head (a test's stand-in
        for a person pushing, which is what makes the watcher look again)."""
        with self.lock:
            if self.fake_github is not None:
                self.fake_github.push(sha, repo=repo, number=number)

    def comment(self, *, repo: str, number: int, text: str, login: str) -> None:
        """Put a comment on a pull as `login` — the review bot's own words, as GitHub has them."""
        with self.lock:
            if self.fake_github is not None:
                self.fake_github.comment_as(text, login=login, repo=repo, number=number)

    def pulls(self, repo: str) -> list[dict[str, object]]:
        """A repository's pulls: its number, its head's branch and commit, and its state."""
        with self.lock:
            found = list(self.fake_github.pulls.get(repo, [])) if self.fake_github else []
        return [
            {"number": one.number, "head": one.head.sha, "branch": one.head.ref, "state": one.state}
            for one in found
        ]

    def restart_udl(self) -> None:
        """Start udl again (a test's stand-in for a person restarting the service)."""
        if self.restart is None:
            raise ValueError("this control has no udl to start")
        self.restart()

    def next_ai(self) -> str:
        """The next answer the fake AI gives (a default one when nothing is scripted)."""
        with self.lock:
            return self.ai.pop(0) if self.ai else "I am a fake AI."


def handler_for(control: Control) -> type[BaseHTTPRequestHandler]:
    """A handler class that answers to `control`: a class of its own, so no instance state is
    kept on the class the server was built with."""

    class Handler(BaseHTTPRequestHandler):
        """POST /mail, /slack, /ai and /turns: each adds what a fake should answer with next."""

        def do_GET(self) -> None:
            asked = urllib.parse.urlsplit(self.path)
            path = asked.path.rstrip("/")
            if path == "/github-pulls":
                # (29.10: the pulls a story's agent opened, and the head each has now.)
                repo = urllib.parse.parse_qs(asked.query).get("repo", [""])[0]
                body = json.dumps({"pulls": control.pulls(repo)}).encode()
            elif path == "/repo/head":
                # (29.10: a branch's commit in the story's origin — "" until it is pushed.)
                branch = urllib.parse.parse_qs(asked.query).get("branch", [""])[0]
                body = json.dumps({"sha": head_of(control, branch)}).encode()
            elif path == "/repo":
                # (29.10: where the story's checkout and its origin are.)
                made = control.repo or (Path(), Path())
                body = json.dumps({"checkout": str(made[0]), "origin": str(made[1])}).encode()
            elif path == "/github-calls":
                # (A test's look at the fake GitHub: what it has been asked, to tell a watcher that
                # never looked from one that did.)
                body = json.dumps({"calls": control.calls()}).encode()
            else:
                self.fail(404, "no such control")
                return
            self.send_response(200)
            self.send_header("Content-Type", JSON)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                elif path == "/turns":
                    control.script_turns(list(_TURNS.validate_python(body)))
                elif path == "/restart":
                    control.restart_udl()
                elif path == "/github":
                    pushed = Push.model_validate(body)
                    control.push(pushed.sha, repo=pushed.repo, number=pushed.number)
                elif path == "/github/comment":
                    said = Said.model_validate(body)
                    control.comment(
                        repo=said.repo, number=said.number, text=said.text, login=said.login
                    )
                else:
                    self.fail(404, "no such control")
                    return
            except ValidationError as e:
                self.fail(400, "; ".join(error["msg"] for error in e.errors()))
            except TimeoutError as e:
                # (udl did not come back: the test hears it, rather than a reset connection.)
                self.fail(503, str(e))
                return
            except ValueError as e:
                self.fail(400, str(e))
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
def github() -> Generator[tuple[FakeGitHub, int]]:
    """The fake GitHub, answering on a free port while the block runs (uvicorn, in a thread).

    It is a real HTTP server, because udl runs as its own process and reaches it as it would
    github.com: the account's `api_url` option points here."""
    import uvicorn

    fake = FakeGitHub()
    # (Port 0: the OS picks, as it does for the harness's own port, and a test that monkeypatches
    # `free_port` cannot make this one collide.)
    server = uvicorn.Server(uvicorn.Config(fake.app(), host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + READY_TIMEOUT
    while not server.started:
        if thread.is_alive() is False or time.monotonic() > deadline:
            raise RuntimeError("the fake GitHub did not start")
        time.sleep(0.05)
    port = int(server.servers[0].sockets[0].getsockname()[1])
    try:
        yield fake, port
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def head_of(control: Control, branch: str) -> str:
    """The commit at `branch` in the story's origin, or "" when it has no such branch."""
    if control.repo is None or branch == "":
        return ""
    origin = control.repo[1]
    try:
        return git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=origin)
    except AssertionError:
        return ""  # (git answers 1 for a branch it does not have)


@contextmanager
def repo(root: Path) -> Generator[tuple[Path, Path]]:
    """The story's repository, while the block runs (29.10): a bare origin and a checkout of it,
    with a `main` that has one commit and a gate that passes, so an agent's worktree, gate and
    push are real git in a temp folder (never the user's).

    Every call goes through `support.git.git`, which strips the variables a git hook exports —
    with them, `git init --bare` re-initialises the repository the hook runs in."""
    bare = root / "origin.git"
    bare.mkdir()
    git("init", "--bare", "-b", "main", cwd=bare)
    checkout = root / "checkout"
    checkout.mkdir()
    git("init", "-b", "main", cwd=checkout)
    git("config", "user.name", "udl", cwd=checkout)
    git("config", "user.email", "udl@example.com", cwd=checkout)
    git("config", "commit.gpgsign", "false", cwd=checkout)
    (checkout / "Makefile").write_text("check:\n\t@true\n")
    (checkout / "README.md").write_text("a small repo\n")
    git("add", "-A", cwd=checkout)
    git("commit", "-m", "the first commit", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-u", "origin", "main", cwd=checkout)
    yield checkout, bare


@contextmanager
def serving(control: Control, port: int = 0) -> Generator[int]:
    """The control API, answering on `port` (a free one when it is 0) while the block runs."""
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(control))
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


def port_for(argv: list[str] | None, flag: str, default: int | None) -> int | None:
    """The port the command line asks for (`--port N`, `--controls N`), else `default` (`None`
    when the caller has none, and `main()` then picks a free one)."""
    given = list(argv or ())
    for index, arg in enumerate(given):
        if arg == flag and index + 1 < len(given):
            return int(given[index + 1])
        if arg.startswith(f"{flag}="):
            return int(arg.split("=", 1)[1])
    return default


def port_from(argv: list[str] | None, default: int | None) -> int | None:
    """The port the command line asks for (`--port N`), else `default`."""
    return port_for(argv, "--port", default)


def free_port() -> int:
    """A port nothing is listening on: the OS picks it, and it is free again by the time this
    returns, so the caller binds that one and no other."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main(
    *,
    port: int | None = None,
    controls: int | None = None,
    self_stop: float = SELF_STOP,
    make_site: Callable[[], AbstractContextManager[object]] | None = None,
    argv: list[str] | None = None,
) -> int:
    port = port_from(argv, port)
    if port is None:
        port = free_port()
    controls = port_for(argv, "--controls", controls)
    if (wrong := half_built()) is not None:
        print(f"e2e: {wrong}", file=sys.stderr, flush=True)  # noqa: T201
        return 1
    stopping = threading.Event()

    def by_signal(signum: int, frame: FrameType | None) -> None:
        stopping.set()

    # (Installed before `serving()` starts its thread, so a SIGTERM that arrives while the
    # servers are still being started is not lost.)
    signal.signal(signal.SIGTERM, by_signal)
    signal.signal(signal.SIGINT, by_signal)

    with (
        tempfile.TemporaryDirectory(prefix="udl-e2e-") as work,
        github() as (gh, gh_port),
        (make_site or FixtureSite.serve)(),
    ):
        home = Path(work)
        control = Control(home)
        with serving(control, controls or 0) as controls_port, repo(home) as made:
            # (A port somebody else holds is said, not fought over: Playwright passes the free one
            # it picked in web/playwright.config.ts, so a taken one was taken since.)
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    print(f"e2e: {port} is taken", file=sys.stderr, flush=True)  # noqa: T201
                    return 1
            config = write_config(home, port)
            gh_url = f"http://127.0.0.1:{gh_port}"
            # (A test's push: what the fake's watcher sees as a new head, so a link is refreshed.)
            control.fake_github = gh
            # (29.10's story: the checkout an agent works in, and the origin it pushes to.)
            control.repo = made
            # (Where the tests read the fake's address and token: the browser's environment is
            # not this process's, and a token never goes in a command line.)
            written = Path(
                os.environ.get("UDL_E2E_GITHUB")
                or str(ROOT / "web" / "playwright" / ".e2e-github.json")
            )
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text(
                json.dumps(
                    {
                        "url": gh_url,
                        "token": GITHUB_TOKEN,
                        "repo": GITHUB_REPO,
                        "controls": f"http://127.0.0.1:{controls_port}",
                    }
                )
            )
            # (udl answers /docs whether or not the UI is built, so it is what says it is up; the
            # shell's own /ui/ comes with 18.3, and until then a test asks for it and gets a 404.)
            url = f"http://127.0.0.1:{port}/docs"
            # (Every process this harness starts, so the one that is running when the harness
            # stops is stopped — including a restart's, which the control server asks for from
            # its own thread.)
            running: list[subprocess.Popen[bytes]] = []
            restarting = threading.Lock()
            # (A test that has to see why udl did what it did asks for its log by naming a file.)
            named = os.environ.get("UDL_E2E_LOG")
            with ExitStack() as opened:
                logged = (
                    opened.enter_context(Path(named).open("ab")) if named else subprocess.DEVNULL
                )

                def launch() -> None:
                    """Start udl, and wait for it to answer."""
                    started = subprocess.Popen(  # noqa: S603 - (a test's own argv)
                        # (--verbose when a test asked for the log: udl's own lines are why it did.)
                        [sys.executable, "-m", "udl", "--verbose", "serve"]
                        if named
                        else [sys.executable, "-m", "udl", "serve"],
                        cwd=ROOT,
                        env=environment(config, home),
                        stdout=logged,
                        stderr=subprocess.STDOUT,
                    )
                    running.append(started)
                    wait_until(url, started)

                def again() -> None:
                    """Stop udl and start it again (29.10: the dispatcher reads its agents at
                    start). The harness's own stop waits for this lock, so a stop mid-restart
                    stops the process the restart started, not the one before it."""
                    with restarting:
                        stop(running[-1])
                        launch()

                control.restart = again
                launch()
                # (Both lines are printed before waiting: a server that is up must say so even
                # if its /docs never answers, or the test would only time out.)
                print(f"e2e: udl serve on {url}", file=sys.stdout, flush=True)  # noqa: T201
                print(f"e2e: github on {gh_url}", flush=True)  # noqa: T201
                print(f"e2e: controls on :{controls_port}", flush=True)  # noqa: T201
                try:
                    stopping.wait(self_stop)  # (Playwright's webServer kills after a few minutes)
                except TimeoutError as e:
                    print(f"e2e: {e}", file=sys.stderr, flush=True)  # noqa: T201
                    return 1
                finally:
                    with restarting:
                        for one in running:
                            stop(one)
    return 0


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
