# ruff: noqa: S603  (a test's own git, and the interpreter running the tests)

"""
---------------------
- test_end_to_end.py -
---------------------

28.6: one orchestration, end to end, through a real `udl serve` — a person's commands on the
CLI and the agent's own calls over udl's MCP server, on a `scripted` runner (a local plugin whose
turns are a script in a file), a `FakeGitHub` served over HTTP and a temp git repository.

Nothing is faked inside udl: the server is the one a person runs, the agent reaches it the way a
real harness does (MCP, with the token its session was given), the pushes are real `git` and the
pulls are real HTTP to the fake's API.
"""

# System Imports
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeGuard

# Package Imports
import httpx
import pytest
import uvicorn

# Local Imports
from support.git import git
from support.github import TOKEN, FakeGitHub, comment_body, repo_body
from udl.plugins.integrations.github.models import GhRepo

ROOT: Final[Path] = Path(__file__).resolve().parents[5]
"""The repo root (the tests' own, for the plugin folder they load)."""

PLUGINS: Final[Path] = ROOT / "tests" / "support" / "orch_e2e_plugins"
"""The local plugins the server loads: the scripted runner."""

API_KEY: Final = "e2e-orch-key"
"""The key the server asks for, and what the CLI sends."""

READY_TIMEOUT: Final[float] = 180.0
"""How long the server, or a turn, gets to get somewhere.

(A loaded machine is slow: the dispatcher wakes on its own 30-second tick, and in the full suite —
`make check` runs 7,500 tests — one tick plus the turn it starts can take minutes. A hang still
fails, in three minutes rather than not at all.)"""

CONFIG: Final[str] = """\
download_dir = "{root}/downloads"
plugin_dirs = ["{plugins}"]

[[db.connections]]
name = "local"
plugin = "sqlite"
url = "sqlite+aiosqlite:///{root}/udl.db"
primary = true

[secrets]
store = "file"
file = "{root}/secrets.json"

[api]
host = "127.0.0.1"
port = {port}
api_key = "{key}"

[automations]
enabled = true

[integrations.github]
poll_every = "1s"

[integrations.scripted]
dir = "{root}"
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass
class Served:
    """The udl a test drives: its config and folder, and the server once it is up."""

    root: Path
    config: Path
    process: subprocess.Popen[bytes] | None = None
    url: str = ""

    def cli(self, *args: str, stdin: str | None = None) -> str:
        """One CLI command, as a person runs it: its stdout (it fails the test if it fails).

        (The CLI is udl itself, in-process, so it needs no server: a person's setup is done
        before the server starts, as it is in a real one.)"""
        finished = subprocess.run(
            [sys.executable, "-m", "udl", "--config", str(self.config), *args],
            capture_output=True,
            text=True,
            input=stdin,
            timeout=120,
            check=False,
        )
        assert finished.returncode == 0, f"udl {' '.join(args)} failed:\n{finished.stderr}"
        return finished.stdout

    def json(self, *args: str) -> object:
        """One CLI command that answers with JSON, as what it printed."""
        return json.loads(self.cli(*args))

    def script(self, turns: Sequence[dict[str, object]]) -> None:
        """What the agent's next turns do, oldest first."""
        (self.root / "turns.json").write_text(json.dumps(list(turns)))

    def start(self) -> None:
        """`udl serve`, in its own process, until the test ends."""
        log = self.root / "serve.log"
        with log.open("wb") as output:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "udl", "--config", str(self.config), "serve"],
                stdout=output,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + READY_TIMEOUT
        while True:
            try:
                httpx.get(
                    f"{self.url}/openapi.json", timeout=1, headers={"X-API-Key": API_KEY}
                ).raise_for_status()
                return
            except httpx.HTTPError:
                if self.process.poll() is not None or time.monotonic() > deadline:
                    self.process.kill()
                    pytest.fail(
                        f"udl serve did not start (it exited {self.process.returncode}):\n"
                        f"{log.read_text()}"
                    )
                time.sleep(0.1)

    def stop(self) -> None:
        """Stop the server, and say what it printed if it would not stop."""
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()
            pytest.fail(f"udl serve did not stop:\n{(self.root / 'serve.log').read_text()}")
        self.process = None

    def wait_for[T](self, what: str, check: Callable[[], T]) -> T:
        """`check()`, until it answers something true (or the wait runs out)."""
        deadline = time.monotonic() + READY_TIMEOUT
        while True:
            found = check()
            if found:
                return found
            if time.monotonic() > deadline:
                raise AssertionError(f"never {what}")
            time.sleep(0.2)


@pytest.fixture
def github() -> Iterator[tuple[FakeGitHub, str]]:
    """`FakeGitHub`, served over HTTP (the server is another process: it needs a URL)."""
    fake = FakeGitHub()
    fake.repos["yelrom0/udl"] = GhRepo.model_validate(repo_body("yelrom0/udl"))
    port = free_port()
    serving = uvicorn.Server(
        uvicorn.Config(fake.app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=serving.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + READY_TIMEOUT
    while not serving.started:
        if time.monotonic() > deadline:
            raise AssertionError("the fake GitHub did not start")
        time.sleep(0.05)
    yield fake, url
    serving.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def served(tmp_path: Path) -> Iterator[Served]:
    """udl's config, for a person's setup and then the server itself (`start()`)."""
    port = free_port()
    config = tmp_path / "config.toml"
    config.write_text(CONFIG.format(root=tmp_path, plugins=PLUGINS, port=port, key=API_KEY))
    served = Served(root=tmp_path, config=config, url=f"http://127.0.0.1:{port}")
    yield served
    served.stop()


PLAN: Final[str] = """\
# A plan

## Progress tracker

- [ ] **Phase 1: A small feature**
  - [ ] 1.1 Do the thing
"""

BOT: Final = "claude"
"""The review bot's login: what the project waits on, and who the fake comments as."""

FIRST_REVIEW: Final[str] = """\
## Review: 1.1 Do the thing (#1)

**Overall: request changes.** One thing is wrong.

### Issues
1. **The file says the wrong thing** (`README.md:1`). It should say the right thing.

**Verdict: changes needed**
"""
"""The fake bot's first comment: one item, and a verdict that needs changes."""

CLEAN_REVIEW: Final[str] = """\
## Follow-up review: 1.1 Do the thing (#1)

### Earlier items
1. The file says the wrong thing: **FIXED**.

### New
Nothing new.

**Verdict: all clear**
"""
"""The fake bot's verification, after the agent fixed it."""


def a_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with an `origin` beside it, and a gate that passes in no time (a test's own
    suite would take half an hour, and this is about the flow, not the gates).

    (Every git here goes through the tests' own `git()`, which runs it without the repo variables
    a pre-commit hook exports: with them, `git init --bare` would re-initialise the repo the
    hook was run from.)"""
    bare = tmp_path / "origin.git"
    bare.mkdir()
    git("init", "--bare", "-b", "main", cwd=bare)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    git("init", "-b", "main", cwd=checkout)
    # (The agent's own turns run plain `git`, so the repo says who commits and signs nothing: the
    # machine's identity may be missing under a hook, and 1Password refuses to sign under load.)
    git("config", "user.name", "udl", cwd=checkout)
    git("config", "user.email", "udl@example.com", cwd=checkout)
    git("config", "commit.gpgsign", "false", cwd=checkout)
    (checkout / "Makefile").write_text("check:\n\t@true\n")
    (checkout / "README.md").write_text("a small repo\n")
    git("add", "-A", cwd=checkout)
    git("commit", "-m", "the first commit", cwd=checkout)
    git("remote", "add", "origin", str(bare), cwd=checkout)
    git("push", "-u", "origin", "main", cwd=checkout)
    return checkout, bare


def the_setup(served: Served, checkout: Path, plan: Path, github_url: str) -> None:
    """What a person does once, before any work: the account, the project, its repo, the agent,
    the plan, and the task on the agent's queue."""
    served.cli(
        "accounts",
        "add",
        "github",
        "yelrom0",
        "--method",
        "tokens",
        "-O",
        f"api_url={github_url}",
        "-O",
        'repos=["yelrom0/udl"]',
        "--tokens-stdin",
        stdin=json.dumps({"token": TOKEN}),
    )
    served.cli(
        "hosts",
        "set",
        "--host",
        json.dumps({"name": "local", "transport": "local", "work_dir": str(served.root / "work")}),
        "--update",
        json.dumps({"work_dir": str(served.root / "work")}),
    )
    served.cli("orch", "projects", "set", "--name", "udl", "--title", "udl", "--review-bot", BOT)
    served.cli(
        "orch",
        "repos",
        "set",
        "--project",
        "udl",
        "--name",
        "udl",
        "--github",
        "yelrom0/udl",
        "--account",
        "github:yelrom0",
        "--checkouts",
        json.dumps({"local": str(checkout)}),
        "--gate",
        json.dumps(["make", "check"]),
    )
    served.cli(
        "orch",
        "agents",
        "set",
        "--name",
        "worker",
        "--title",
        "worker",
        "--runner",
        "scripted",
        "--host",
        "local",
        "--max-nudges",
        "50",
    )
    # (A subscription on the pulls' events: it is what has udl's github watcher look at the
    # repository at all, and what keeps each pull link's head current.)
    served.cli(
        "actions",
        "set",
        "watch-pulls",
        "--kind",
        "notify",
        "-O",
        "title=A pull changed",
    )
    served.cli(
        "subs",
        "set",
        "watch-pulls",
        "--on",
        "github.pr.*",
        "--action",
        "watch-pulls",
    )
    served.cli("orch", "import", str(plan))
    served.cli("orch", "queue", "add", "1.1", "--agent", "worker")


def _is_dict(value: object) -> TypeGuard[dict[str, object]]:
    """Whether `value` is a mapping of JSON's own (a test's check, typed for the rest)."""
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    """Whether `value` is a list of JSON's own (a test's check, typed for the rest)."""
    return isinstance(value, list)


def _dict(value: object) -> dict[str, object]:
    """`value` as a mapping (a test's JSON, checked)."""
    assert _is_dict(value), value
    return value


def _list(value: object) -> list[object]:
    """`value` as a list (a test's JSON, checked)."""
    assert _is_list(value), value
    return value


def _the_questions(served: Served) -> list[dict[str, object]]:
    """The open questions, once there is one (an empty list is not a question yet)."""
    found = _list(_dict(served.json("orch", "list-questions", "--json"))["questions"])
    return [_dict(one) for one in found if _is_dict(one)]


def _the_pull(fake: FakeGitHub) -> dict[str, object] | None:
    """The pull the agent opened, once it is there (None: not yet)."""
    pulls = fake.pulls.get("yelrom0/udl") or []
    if not pulls:
        return None
    return {"head_sha": pulls[-1].head.sha, "number": pulls[-1].number}


def _the_head(served: Served, origin: Path, key: str) -> str:
    """The commit at the head of the task's branch, as the origin has it ("" until it is there)."""
    shown = _dict(_dict(served.json("orch", "tasks", "show", key, "--json"))["task"])
    branch = str(shown.get("branch") or "")
    if not branch:
        return ""
    try:
        return git("rev-parse", f"refs/heads/{branch}", cwd=origin)
    except AssertionError:
        return ""


def _pushed(served: Served, fake: FakeGitHub, origin: Path, key: str) -> bool:
    """Whether the agent has pushed the task's branch (its pull is open, on that commit)."""
    pull = _the_pull(fake)
    head = _the_head(served, origin, key)
    if pull is None or not head:
        return False
    if pull["head_sha"] != head:
        number = pull["number"]
        assert isinstance(number, int)
        fake.push(head, repo="yelrom0/udl", number=number)  # (as GitHub sees a branch push)
    return True


def _a_new_head(served: Served, fake: FakeGitHub, origin: Path, key: str, was: str) -> bool:
    """Whether the agent has pushed a head that is not `was`, with the pull moved to it."""
    head = _the_head(served, origin, key)
    if not head or head == was:
        return False
    pull = _the_pull(fake)
    if pull is not None and pull["head_sha"] != head:
        number = pull["number"]
        assert isinstance(number, int)
        fake.push(head, repo="yelrom0/udl", number=number)  # (as GitHub sees a branch push)
    return pull is not None and pull["head_sha"] == head


def _the_listed(served: Served, key: str) -> dict[str, object]:
    """The row ready to merge lists for the task, once it names the commit it is ready at (an
    empty one until it does: a row with no sha is not ready to merge)."""
    listed = _dict(served.json("orch", "merge-list", "--json"))
    rows = [row for row in (_dict(one) for one in _list(listed["ready"])) if row.get("task") == key]
    if not rows or not rows[0]["sha"]:
        return {}
    return rows[0]


def _answered_the_wait(served: Served) -> bool:
    """Answer the dispatcher's "how long to wait?" question, if it has asked one.

    (It asks when only time stands in the way of the pull — the checks running, the bot not done
    with this head — and here that is a race of the test's own making: the head moves with the
    fix, and the bot's answer to it is written a moment later. A person says to carry on, as they
    would, and the agent hears both that and the pull's news.)"""
    for question in _the_questions(served):
        if "how long to wait" in str(question["title"]):
            served.cli("orch", "answer", "--key", str(question["key"]), "--answer", "Carry on now.")
            return True
    return False


def _the_state(served: Served, key: str) -> str:
    """Where the task is now."""
    task = _dict(_dict(served.json("orch", "tasks", "show", key, "--json"))["task"])
    state = task["state"]
    assert isinstance(state, str)
    return state


def a_turn(*steps: dict[str, object], when: str) -> dict[str, object]:
    """One turn of the agent's: what the prompt must say for it to be this turn, and the steps it
    takes, in order."""
    return {"when": when, "steps": list(steps)}


def test_the_whole_run(served: Served, github: tuple[FakeGitHub, str], tmp_path: Path) -> None:
    """One task, from the plan to merged, by the person's commands and the agent's own calls."""
    fake, github_url = github
    checkout, origin = a_repo(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text(PLAN)
    the_setup(served, checkout, plan, github_url)
    served.script(
        [
            # The first: it asks the one thing it cannot decide.
            a_turn(
                {
                    "mcp": {
                        "tool": "orch_ask",
                        "arguments": {
                            "task": "1.1",
                            "title": "Which way?",
                            "text": "Which way should the file say it?",
                            "blocking": True,
                        },
                    }
                },
                when="Your task is 1.1",
            ),
            # Once the answer is in: the work, committed, and pushed.
            a_turn(
                {"shell": ["sh", "-c", "echo 'the right thing' > README.md"]},
                {"shell": ["git", "add", "-A"]},
                {"shell": ["git", "commit", "-m", "the thing, the right way"]},
                {"mcp": {"tool": "orch_push", "arguments": {"key": "1.1"}}},
                when="The right way.",
            ),
            # Once the bot has found one item: fix it, and push the head that fixes it.
            a_turn(
                {"shell": ["sh", "-c", "echo 'the right thing, said plainly' > README.md"]},
                {"shell": ["git", "add", "-A"]},
                {"shell": ["git", "commit", "-m", "fix: the file says it plainly"]},
                {"mcp": {"tool": "orch_push", "arguments": {"key": "1.1"}}},
                when="The file says the wrong thing",
            ),
            # Once the bot's verification is clean: say the pull is ready.
            a_turn(
                {"mcp": {"tool": "orch_ready", "arguments": {"key": "1.1", "note": "done"}}},
                when="its pull is ready to merge",
            ),
        ]
    )
    served.start()

    # The agent's first turn: it asks the one thing it cannot decide.

    asked = served.wait_for("asked", lambda: _the_questions(served))
    [question] = asked
    assert question["blocking"] is True
    assert question["task"] == "1.1"
    blocked = _dict(_dict(served.json("orch", "tasks", "show", "1.1", "--json"))["task"])
    assert blocked["state"] == "blocked"

    # The person answers it.
    served.cli("orch", "answer", "--key", str(question["key"]), "--answer", "The right way.")

    # The agent pushes; the fake GitHub's pull is on the commit it pushed (as GitHub's is), and
    # the fake bot finds one item.
    served.wait_for("pushed", lambda: _pushed(served, fake, origin, "1.1"))
    # (The head the first push made, read before the bot's item is there: the agent's turn for
    # the fix is prompted by that item, and it must not have pushed by the time this is read.)
    first = _the_head(served, origin, "1.1")
    fake.comments["yelrom0/udl#1"] = [comment_body(1, FIRST_REVIEW, login=BOT)]

    # The agent fixes it and pushes again: a new head, which the pull is moved to.
    served.wait_for("fixed", lambda: _a_new_head(served, fake, origin, "1.1", first))
    fake.comments["yelrom0/udl#1"] = [
        comment_body(1, FIRST_REVIEW, login=BOT),
        comment_body(2, CLEAN_REVIEW, login=BOT),
    ]

    # The agent says the pull is ready (answering, as a person would, if the dispatcher looked at
    # the pull in the moment between the head moving and the bot's answer being written).
    def _ready() -> bool:
        if _the_state(served, "1.1") == "ready":
            return True
        _answered_the_wait(served)
        return False

    served.wait_for("ready", _ready)

    before = _dict(_dict(served.json("orch", "tasks", "show", "1.1", "--json"))["task"])
    assert before["worktree"]
    assert before["branch"]

    # Ready to merge lists it at that head, and a person merges it.
    row = served.wait_for("listed for merging", lambda: _the_listed(served, "1.1"))
    assert row["sha"] == _the_head(served, origin, "1.1")
    served.cli(
        "orch",
        "merge",
        "--key",
        "1.1",
        "--confirm",
        f"{row['repo']}#{row['number']}",
        "--sha",
        str(row["sha"]),
    )

    # The task is done (a person merged, and nothing follows its base), its worktree and its
    # branch are gone, and the answer it waited on is on its notes.
    shown = _dict(served.json("orch", "tasks", "show", "1.1", "--json"))
    task = _dict(shown["task"])
    assert task["state"] == "done"
    assert task["worktree"] in (None, "")
    assert task["branch"] in (None, "")
    assert not Path(str(before["worktree"])).exists()
    assert git("branch", "--list", str(before["branch"]), cwd=checkout) == ""
    assert any("The right way." in str(_dict(note)["text"]) for note in _list(shown["notes"]))
