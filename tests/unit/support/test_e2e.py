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
    Control,
    environment,
    free_port,
    main,
    port_from,
    serving,
    stop,
    wait_until,
    write_config,
)
from udl.config import APISettings

E2E_PLUGINS = Path(__file__).resolve().parents[2] / "support" / "e2e_plugins"
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
    assert f'plugin_dirs = ["{E2E_PLUGINS}"]' in text
    # (Secrets are the memory store, so no keyring is opened; the web UI is served — a browser is
    # what most e2e tests are — and its plugins are looked for under the temp dir, not the user's
    # ~/.config/udl/ui-plugins.)
    assert '[secrets]\nstore = "memory"' in text
    assert "[web]\nenabled = true" in text
    assert f'ui_plugin_dirs = ["{tmp_path / "ui-plugins"}"]' in text


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


def test_the_control_api_says_no_to_what_it_does_not_take() -> None:
    control = Control()

    with serving(control) as port:
        refuses(port, "/mail", ["not an object"], 400)
        refuses(port, "/slack", "not an object", 400)
        refuses(port, "/ai", {"not": "a list"}, 400)
        refuses(port, "/what", {"anything": 1}, 404)

    assert (control.mails, control.messages, control.ai) == ([], [], [])


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


def test_main_stops_itself_when_nothing_else_stops_it() -> None:
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
    started = subprocess.Popen(  # noqa: S603 - the module under test
        [sys.executable, "-m", "support.e2e", "--port", str(port)],
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
