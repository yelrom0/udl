"""
---------------
- test_e2e.py -
---------------

tests/support/e2e.py: the config it writes for `udl serve`, the control API beside it (a test
delivers a mail, posts a Slack message or scripts the AI through it), and the process helpers.
"""

# System Imports
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.e2e import (
    E2E_UI_PLUGINS,
    Control,
    environment,
    free_port,
    github,
    half_built,
    head_of,
    main,
    port_from,
    repo,
    serving,
    stop,
    wait_until,
    write_config,
)
from support.git import git
from udl.config import APISettings

E2E_PLUGINS = Path(__file__).resolve().parents[2] / "support" / "e2e_plugins"
# (28.6's scripted runner, which 29.12's chat runs its turns on.)
ORCH_E2E_PLUGINS = Path(__file__).resolve().parents[2] / "support" / "orch_e2e_plugins"
# (The module under test runs `udl` from the checkout; tests start it as a module.)
ROOT = Path(__file__).resolve().parents[3]


def post(port: int, path: str, body: object) -> None:
    """A control call that must be taken (204)."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310 - our own server
        assert answer.status == 204


def refuses(port: int, path: str, body: object, status: int) -> None:
    """A control call that must be refused with `status`."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)  # noqa: S310 - our own server
    refused = caught.value
    refused.close()  # (else it is left to the garbage collector, which warns)
    assert refused.code == status, f"{path} answered {refused.code}, not {status}"


# ---------------------------------------------------------------------------------------------
# The config, and the environment the server runs in
# ---------------------------------------------------------------------------------------------


def test_the_config_is_what_the_server_reads(tmp_path: Path) -> None:
    path = write_config(tmp_path, port=9123)

    text = path.read_text()
    assert "port = 9123" in text
    # (Its own SQLite database and downloads, under the temp dir: never the user's.)
    assert f'url = "sqlite+aiosqlite:///{tmp_path / "udl.db"}"' in text
    assert f'download_dir = "{tmp_path / "downloads"}"' in text
    assert f'plugin_dirs = ["{E2E_PLUGINS}", "{ORCH_E2E_PLUGINS}"]' in text
    # (29.12's chat: the scripted runner, whose turns are a file in this test's folder.)
    assert f'[integrations.scripted]\ndir = "{tmp_path}"' in text
    # (Secrets are a file in the harness's temp home — no keyring is opened, and 29.10's story,
    # which starts udl again, keeps its account connected; the web UI is served — a browser is
    # what most e2e tests are — and its plugins are the fixtures the repo has, not the user's
    # ~/.config/udl/ui-plugins: a working one, and one that throws, for 18.9's plugin host.)
    assert "[secrets]" in text
    assert 'store = "file"' in text
    assert f'file = "{tmp_path / "secrets.json"}"' in text
    assert "[web]\nenabled = true" in text
    # (A key, so a browser test signs in as a user does; 18.4's session is what the UI uses.)
    assert 'api_key = "e2e-key"' in text
    assert f'ui_plugin_dirs = ["{E2E_UI_PLUGINS}"' in text
    assert (E2E_UI_PLUGINS / "hello" / "manifest.json").is_file()
    assert (E2E_UI_PLUGINS / "boom" / "manifest.json").is_file()


def test_the_example_and_the_template_are_served_when_they_are_built(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (18.10: `make web` builds both, so `make e2e` serves both; a run that has not built them
    # serves the hand-written bundles alone rather than failing.)
    example, template = tmp_path / "example", tmp_path / "template"
    (example / "shadcn").mkdir(parents=True)
    (template / "template").mkdir(parents=True)
    monkeypatch.setattr("support.e2e.SHADCN_EXAMPLE", example)
    monkeypatch.setattr("support.e2e.TEMPLATE", template)

    text = write_config(tmp_path, port=9124).read_text()
    assert f'"{example}"' in text
    assert f'"{template}"' in text

    monkeypatch.setattr("support.e2e.SHADCN_EXAMPLE", tmp_path / "not-built")
    assert f'"{example}"' not in write_config(tmp_path, port=9124).read_text()


def test_a_half_built_web_tree_is_said_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (The shell is there and a plugin the specs expect is not: a page missing a plugin would
    # fail somewhere confusing, so the harness says what to run instead.)
    shell = tmp_path / "shell"
    shell.mkdir()
    monkeypatch.setattr("support.e2e.SHELL_DIST", shell)
    monkeypatch.setattr("support.e2e.SHADCN_EXAMPLE", tmp_path / "example")
    monkeypatch.setattr("support.e2e.TEMPLATE", tmp_path / "template")
    monkeypatch.setattr("support.e2e.ORCH_UI", tmp_path / "orch")

    assert half_built() == (
        "the shell is built and the shadcn example and the template and the orchestrator's UI are"
        " not built: run `make web`"
    )
    with taken_port() as port:
        assert main(port=port, make_site=nothing) == 1

    # (One missing is said alone, with its name, not the folder's — both are called `dist`.)
    (tmp_path / "example" / "shadcn").mkdir(parents=True)
    assert half_built() == (
        "the shell is built and the template and the orchestrator's UI are not built:"
        " run `make web`"
    )

    # (All there: nothing wrong.)
    (tmp_path / "template" / "template").mkdir(parents=True)
    (tmp_path / "orch").mkdir(parents=True)
    assert half_built() is None

    # (Nothing built is a fresh checkout, which the harness serves: the hand-written bundles.)
    monkeypatch.setattr("support.e2e.SHELL_DIST", tmp_path / "no-shell")
    assert half_built() is None


def test_the_server_runs_without_the_callers_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UDL_API__PORT", "9999")
    monkeypatch.setenv("UDL_DOWNLOAD_DIR", "/nowhere")

    env = environment(tmp_path / "config.toml", tmp_path)

    assert not [name for name in env if name.startswith("UDL_") and name != "UDL_CONFIG"]
    assert env["UDL_CONFIG"] == str(tmp_path / "config.toml")
    # (Every XDG dir under the temp dir: the browser profile, fetched pages and scripts too.)
    assert env["XDG_CONFIG_HOME"] == str(tmp_path / ".config")
    assert env["XDG_DATA_HOME"] == str(tmp_path / ".local/share")
    assert env["XDG_STATE_HOME"] == str(tmp_path / ".local/state")
    assert env["XDG_CACHE_HOME"] == str(tmp_path / ".cache")


# ---------------------------------------------------------------------------------------------
# The control API
# ---------------------------------------------------------------------------------------------


def test_the_control_api_takes_what_a_test_delivers() -> None:
    control = Control()

    with serving(control) as port:
        post(port, "/mail", {"from": "bank@example.com"})
        post(port, "/slack", {"channel": "#ops", "text": "hi"})
        post(port, "/ai", ["one", "two"])

    assert control.mails == [{"from": "bank@example.com"}]
    assert control.messages == [{"channel": "#ops", "text": "hi"}]
    assert control.next_ai() == "one"
    assert control.next_ai() == "two"
    assert control.next_ai() == "I am a fake AI."


def test_the_control_api_holds_the_fake_github_and_says_what_it_was_asked() -> None:
    # (29.8: a test pushes a head and reads what the watcher looked at, so the harness serves a
    # fake GitHub of its own beside the site.)
    control = Control()

    with serving(control) as port, github() as (fake, gh_port):
        control.fake_github = fake
        post(port, "/github", {"sha": "abc1235"})
        assert fake.pull_of("acme/widgets", 7).head.sha == "abc1235"

        asked = urllib.request.urlopen(f"http://127.0.0.1:{port}/github-calls", timeout=5)
        with asked:
            assert asked.status == 200
            assert json.loads(asked.read())["calls"] == []
        assert gh_port > 0


def test_the_control_api_puts_a_comment_on_a_pull_as_the_bot(tmp_path: Path) -> None:
    """(29.10: the review bot's words are a comment on the pull, as GitHub has them.)"""
    control = Control()

    with serving(control) as port, github() as (fake, _):
        control.fake_github = fake
        fake.open_pull(repo="acme/widgets", number=9, head="feat/story", sha="c0ffee")
        post(
            port,
            "/github/comment",
            {
                "repo": "acme/widgets",
                "number": 9,
                "text": "**Verdict: changes needed**",
                "login": "claude",
            },
        )

    said = fake.comments["acme/widgets#9"]
    assert [(one.user.login, one.body) for one in said] == [
        ("claude", "**Verdict: changes needed**")
    ]


def test_the_control_api_says_a_repository_s_pulls_and_their_heads() -> None:
    """(29.10: the pull a story's agent opened, and the head it has now.)"""
    control = Control()

    with serving(control) as port, github() as (fake, _):
        control.fake_github = fake
        fake.open_pull(repo="acme/widgets", number=9, head="feat/story", sha="c0ffee")
        fake.push("d00d1e", repo="acme/widgets", number=9)

        answer = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/github-pulls?repo=acme/widgets", timeout=5
        )
        with answer:
            found = json.loads(answer.read())["pulls"]

    assert {"number": 9, "head": "d00d1e", "branch": "feat/story", "state": "open"} in found


def test_a_push_can_name_its_own_repository_and_pull() -> None:
    """(29.8's push is a person's: 29.10's story pushes its own pull, not the default one.)"""
    control = Control()

    with serving(control) as port, github() as (fake, _):
        control.fake_github = fake
        fake.open_pull(repo="acme/widgets", number=9, head="feat/story", sha="c0ffee")
        post(port, "/github", {"sha": "d00d1e", "repo": "acme/widgets", "number": 9})

    assert fake.pull_of("acme/widgets", 9).head.sha == "d00d1e"
    assert fake.pull_of("acme/widgets", 7).head.sha != "d00d1e"


def test_a_comment_with_no_pull_is_refused_not_a_key_error() -> None:
    """(A control body is a model: what is missing is a 400, as the other controls say.)"""
    control = Control()

    with serving(control) as port, github() as (fake, _):
        control.fake_github = fake
        refuses(port, "/github/comment", {"text": "no pull said"}, 400)
        refuses(port, "/github/comment", {"repo": "acme/widgets", "text": "no number"}, 400)


def test_a_body_that_does_not_fit_is_one_answer_and_not_two() -> None:
    """(A control's body is a model: a body that fails validation gets its 400 and nothing else —
    a second status line queued behind it would be read as the answer to the next call.)"""
    control = Control()

    with serving(control) as port, socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        body = json.dumps({"repo": "acme/widgets"}).encode()
        sock.sendall(
            b"POST /github HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        said = b""
        while chunk := sock.recv(4096):
            said += chunk

    # (One answer, and its status is the refusal: the harness says 400 and ends the connection.)
    assert said.count(b"HTTP/1.") == 1, said
    assert b" 400 " in said.split(b"\r\n", 1)[0], said


def test_the_control_api_says_a_branch_s_head_in_the_story_s_origin(tmp_path: Path) -> None:
    """(29.10: the commit a story's branch has in its origin, and "" for one it does not have.)"""
    control = Control()

    with serving(control) as port, repo(tmp_path) as (checkout, origin):
        control.repo = (checkout, origin)
        there = git("rev-parse", "HEAD", cwd=checkout).strip()
        said = urllib.request.urlopen(f"http://127.0.0.1:{port}/repo/head?branch=main", timeout=5)
        with said:
            assert json.loads(said.read())["sha"] == there
        missing = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/repo/head?branch=feat/never", timeout=5
        )
        with missing:
            assert json.loads(missing.read())["sha"] == ""


def test_a_branch_is_read_from_the_origin_not_the_checkout(tmp_path: Path) -> None:
    """(The story reads the origin's branches: a branch made in the checkout is not there yet.)"""
    control = Control()

    with repo(tmp_path) as (checkout, origin):
        control.repo = (checkout, origin)
        assert head_of(control, "main") != ""
        assert head_of(control, "feat/never") == ""
        assert head_of(Control(), "main") == ""


def test_the_story_s_repository_is_a_checkout_of_its_own_origin(tmp_path: Path) -> None:
    """(29.10: an agent's worktree, gate and push are real git in a temp folder.)"""
    with repo(tmp_path) as (checkout, origin):
        assert (checkout / ".git").is_dir()
        assert (origin / "HEAD").is_file()
        assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=checkout).strip() == "main"
        assert (checkout / "Makefile").read_text() == "check:\n\t@true\n"

        answer = None
        with serving(control := Control()) as port:
            control.repo = (checkout, origin)
            asked = urllib.request.urlopen(f"http://127.0.0.1:{port}/repo", timeout=5)
            with asked:
                answer = json.loads(asked.read())
        assert answer == {"checkout": str(checkout), "origin": str(origin)}


def test_the_control_api_starts_udl_again() -> None:
    """(29.10: the dispatcher reads its agents when udl starts, so a story starts it again.)"""
    control = Control()
    started: list[str] = []
    control.restart = lambda: started.append("again")

    with serving(control) as port:
        post(port, "/restart", {})

    assert started == ["again"]


def test_a_control_with_no_udl_refuses_a_restart() -> None:
    control = Control()

    with serving(control) as port:
        refuses(port, "/restart", {}, 400)

    assert control.restart is None


def test_the_control_api_says_no_to_what_it_does_not_take() -> None:
    control = Control()

    with serving(control) as port:
        refuses(port, "/mail", ["not an object"], 400)
        refuses(port, "/slack", "not an object", 400)
        refuses(port, "/ai", {"not": "a list"}, 400)
        refuses(port, "/what", {"anything": 1}, 404)

    assert (control.mails, control.messages, control.ai) == ([], [], [])


def test_the_control_api_scripts_a_turn_in_its_own_folder(tmp_path: Path) -> None:
    """(29.12: a chat's turn is what a test wrote, in the folder the scripted runner reads.)"""
    control = Control(tmp_path)

    with serving(control) as port:
        post(port, "/turns", [{"when": "what is stuck?", "steps": [{"wait": 0}]}])

    assert json.loads((tmp_path / "turns.json").read_text()) == [
        {"when": "what is stuck?", "steps": [{"wait": 0}]}
    ]


def test_a_control_with_no_folder_refuses_a_turn() -> None:
    control = Control()

    with serving(control) as port:
        refuses(port, "/turns", [{"steps": [{"wait": 0}]}], 400)

    assert control.root is None


def test_the_queues_of_one_control_are_not_another_s() -> None:
    one, two = Control(), Control()

    one.script("first")
    one.deliver({"a": 1})

    assert two.next_ai() == "I am a fake AI."
    assert two.mails == []


def test_what_a_call_gave_is_kept_as_it_was_given() -> None:
    control = Control()
    body: dict[str, object] = {"nested": {"list": [1, 2]}}
    control.deliver(body)
    body["nested"] = "changed"

    assert control.mails == [{"nested": {"list": [1, 2]}}]


# ---------------------------------------------------------------------------------------------
# The process helpers
# ---------------------------------------------------------------------------------------------


def test_a_server_that_died_is_a_failure() -> None:
    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])

    try:
        # (It is dead by now: the helper must say so, not report it as "not answering yet".)
        with pytest.raises(RuntimeError, match="stopped with 3"):
            wait_until("http://127.0.0.1:1/", dead, timeout=10)
    finally:
        stop(dead)


def test_a_server_that_never_answers_is_a_timeout() -> None:
    asleep = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="did not answer"):
            wait_until("http://127.0.0.1:1/", asleep, timeout=0.5)
        assert time.monotonic() - started < 10
    finally:
        stop(asleep)


def test_a_server_is_asked_to_stop_and_waited_for() -> None:
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])

    stop(sleeper)

    assert sleeper.poll() is not None


def test_stopping_what_already_stopped_is_quiet() -> None:
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()

    stop(done)

    assert done.returncode == 0


# ---------------------------------------------------------------------------------------------
# main(): the port, the self-stop and SIGTERM
# ---------------------------------------------------------------------------------------------


@contextmanager
def nothing() -> Generator[None]:
    """A stand-in for the fixture site: the harness's other servers, not this test's subject."""
    yield None


def test_main_stops_itself_when_nothing_else_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # (Whether this checkout's web tree happens to be built is not this test's subject: a
    # half-built one is refused, so the harness is told nothing is built.)
    monkeypatch.setattr("support.e2e.SHELL_DIST", tmp_path / "no-shell")

    assert main(port=free_port(), self_stop=0.3, make_site=nothing) == 0


def test_a_request_that_is_not_json_is_refused() -> None:
    control = Control()

    with serving(control) as port:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/mail", data=b"{oops", method="POST"
        )
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)  # noqa: S310 - our own server
        refused = caught.value
        refused.close()
        assert refused.code == 400


def test_main_says_when_its_port_is_taken() -> None:
    with taken_port() as port:
        assert main(port=port, make_site=nothing) == 1


def test_the_port_can_come_from_the_command_line() -> None:
    assert port_from(["--port", "9123"], None) == 9123
    assert port_from(["--port=9123"], None) == 9123
    # (Nothing given: the caller's default is kept — `None` for `main()`, which picks a free one.)
    assert port_from([], None) is None
    assert port_from([], 9124) == 9124


def test_a_free_port_is_free_and_is_not_udls_own() -> None:
    port = free_port()

    # (8765 is `udl serve`'s default, where the developer's own udl answers: a harness that
    # took it would test that udl, with the user's own data.)
    assert port != APISettings().port
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", port))


def test_the_default_port_is_a_free_one(monkeypatch: pytest.MonkeyPatch) -> None:
    # (Told no `--port`, the harness asks `free_port()`: a port it fixed itself could be one
    # somebody else holds. A taken port is how this test sees the one it used.)
    with taken_port() as port:
        monkeypatch.setattr("support.e2e.free_port", lambda: port)
        assert main(make_site=nothing) == 1


def test_main_stops_on_sigterm() -> None:
    port = free_port()
    # (Run as a script rather than `-m`, so the harness is told this checkout's web tree is not
    # built: a half-built one is refused, which is another test's subject.)
    script = (
        "import pathlib\n"
        "import support.e2e as e2e\n"
        "e2e.SHELL_DIST = pathlib.Path('/nonexistent')\n"
        f"raise SystemExit(e2e.main(port={port}))\n"
    )
    started = subprocess.Popen(  # noqa: S603 - the module under test
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment_for_the_test(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert started.stdout is not None
        # (The "udl serve on" line is printed before it waits, so it is what says it is up —
        # and on the port it was given.)
        line = started.stdout.readline()
        assert line.startswith(f"e2e: udl serve on http://127.0.0.1:{port}/docs"), line
        started.send_signal(signal.SIGTERM)
        assert started.wait(timeout=30) == 0
        started.stdout.close()
    finally:
        if started.poll() is None:  # pragma: no cover - the server must have stopped
            started.kill()
            started.wait(timeout=10)


def test_a_first_start_that_never_answers_says_so_and_ends_with_1() -> None:
    """(A udl that never answers on the first start is the harness's own failure: it says so and
    ends with 1 — not a traceback out of `main` — and the process it started is stopped.)"""
    port = free_port()
    script = (
        "import pathlib\n"
        "import support.e2e as e2e\n"
        "e2e.SHELL_DIST = pathlib.Path('/nonexistent')\n"
        "def never(*args: object, **kwargs: object) -> None:\n"
        "    raise TimeoutError('udl serve did not answer')\n"
        "e2e.wait_until = never\n"
        f"raise SystemExit(e2e.main(port={port}))\n"
    )
    started = subprocess.Popen(  # noqa: S603 - the module under test
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment_for_the_test(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert started.stdout is not None
        said = started.stdout.read()
        assert started.wait(timeout=60) == 1, said
        # (Said, not raised: the harness's own line, and no traceback.)
        assert "e2e: udl serve did not answer" in said, said
        assert "Traceback" not in said, said
    finally:
        if started.stdout is not None:
            started.stdout.close()
        if started.poll() is None:  # pragma: no cover - the harness must have ended
            started.kill()
            started.wait(timeout=10)


def test_the_log_a_test_asks_for_has_udl_s_own_lines(tmp_path: Path) -> None:
    """(29.10: a test that has to see why udl did what it did names a file, and udl's own lines
    — `--verbose` — are written to it, not to the harness's own output.)"""
    port = free_port()
    logged = tmp_path / "udl.log"
    script = (
        "import pathlib\n"
        "import support.e2e as e2e\n"
        "e2e.SHELL_DIST = pathlib.Path('/nonexistent')\n"
        f"raise SystemExit(e2e.main(port={port}))\n"
    )
    env = environment_for_the_test()
    env["UDL_E2E_LOG"] = str(logged)
    started = subprocess.Popen(  # noqa: S603 - the module under test
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert started.stdout is not None
        assert started.stdout.readline().startswith("e2e: udl serve on"), "the server came up"
        started.send_signal(signal.SIGTERM)
        assert started.wait(timeout=30) == 0
        started.stdout.close()
    finally:
        if started.poll() is None:  # pragma: no cover - the server must have stopped
            started.kill()
            started.wait(timeout=10)

    assert "udl" in logged.read_text()


@contextmanager
def taken_port() -> Generator[int]:
    """A port that is bound and listening, so a server told to use it finds it taken."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield int(sock.getsockname()[1])


def environment_for_the_test() -> dict[str, str]:
    """The environment the module runs in: `support.*` is importable, as pytest makes it."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("UDL_")}
    tests = str(ROOT / "tests")
    env["PYTHONPATH"] = f"{tests}:{env.get('PYTHONPATH', '')}".rstrip(":")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env
