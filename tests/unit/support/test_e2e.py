"""
-------------
- test_e2e.py -
-------------

tests/support/e2e.py: the config it writes for `udl serve`, the control API beside it (a test
delivers a mail, posts a Slack message or scripts the AI through it), and the process helpers.
"""

# System Imports
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.e2e import (
    PORT,
    SELF_STOP,
    Control,
    environment,
    handler_for,
    serving,
    stop,
    wait_until,
    write_config,
)

E2E_PLUGINS = Path(__file__).resolve().parents[2] / "support" / "e2e_plugins"


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


def test_the_config_is_what_the_server_reads(tmp_path: Path) -> None:
    path = write_config(tmp_path)

    text = path.read_text()
    assert f"port = {PORT}" in text
    # (Its own SQLite database and downloads, under the temp dir: never the user's.)
    assert f'url = "sqlite+aiosqlite:///{tmp_path / "udl.db"}"' in text
    assert f'download_dir = "{tmp_path / "downloads"}"' in text
    assert f'plugin_dirs = ["{E2E_PLUGINS}"]' in text
    # (Secrets are the memory store, so no keyring is opened; the web UI is 18.3's.)
    assert '[secrets]\nstore = "memory"' in text
    assert "[web]\nenabled = false" in text


def test_the_server_runs_without_the_callers_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UDL_API__PORT", "9999")
    monkeypatch.setenv("UDL_DOWNLOAD_DIR", "/nowhere")

    env = environment(tmp_path / "config.toml")

    assert not [name for name in env if name.startswith("UDL_") and name != "UDL_CONFIG"]
    assert env["UDL_CONFIG"] == str(tmp_path / "config.toml")


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


def test_a_handler_of_its_own_is_made_for_each_control() -> None:
    one, two = Control(), Control()

    assert handler_for(one) is not handler_for(two)


def test_a_server_that_died_is_a_failure(tmp_path: Path) -> None:
    dead = subprocess.Popen([sys.executable, "-c", "raise SystemExit(3)"])

    with pytest.raises(RuntimeError, match="stopped with 3"):
        wait_until("http://127.0.0.1:1/", dead, timeout=5)


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


def test_the_self_stop_is_long_enough_for_a_test_and_short_enough_to_not_linger() -> None:
    assert 60 <= SELF_STOP <= 900


def test_main_refuses_a_port_that_is_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    port = taken.getsockname()[1]
    monkeypatch.setattr("support.e2e.FixtureSite.serve", lambda: _nothing())
    try:
        assert main_on(port) == 1
    finally:
        taken.close()


def main_on(port: int) -> int:
    from support.e2e import main

    return main(port=port)


class _Nothing:
    def __enter__(self) -> _Nothing:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _nothing() -> _Nothing:
    return _Nothing()


def test_the_module_is_importable_without_running() -> None:
    import support.e2e as module

    assert module.PORT == 8765


def test_the_queues_are_not_shared_between_controls() -> None:
    one, two = Control(), Control()

    one.script("first")
    one.deliver({"a": 1})

    assert two.next_ai() == "I am a fake AI."
    assert two.mails == []


def test_what_a_call_gave_is_kept_as_it_was_given() -> None:
    control = Control()
    body: dict[str, Any] = {"nested": {"list": [1, 2]}}
    control.deliver(body)
    body["nested"] = "changed"
    assert control.mails[0] == {"nested": {"list": [1, 2]}}
