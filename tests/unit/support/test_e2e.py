"""
-----------
- test_e2e.py -
--------------

tests/support/e2e.py: the config it writes for `udl serve`, and the control API beside it (a
test delivers a mail, posts a Slack message or scripts the AI through it).
"""

# System Imports
import json
import urllib.error
import urllib.request
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.e2e import CONFIG, SELF_STOP, Control, serving, write_config

E2E_PLUGINS = Path(__file__).resolve().parents[2] / "support" / "e2e_plugins"


def test_the_config_points_udl_at_the_fakes(tmp_path: Path) -> None:
    path = write_config(tmp_path, 9123)

    text = path.read_text()
    assert "port = 9123" in text
    assert f'plugin_dirs = ["{E2E_PLUGINS}"]' in text
    assert "probe_order" in text
    assert "[automations]" in text
    # (The template has no placeholders left: a config udl can read.)
    assert "{" not in text


def test_the_server_stops_itself_when_nothing_else_stops_it(tmp_path: Path) -> None:
    # (A server left behind after a test is a hung process: it stops on its own, as well as on
    # SIGTERM.)
    assert 0 < SELF_STOP <= 900


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


def test_the_control_api_says_no_to_what_it_does_not_take() -> None:
    control = Control()

    with serving(control) as port:
        refuses(port, "/mail", ["not an object"], 400)
        refuses(port, "/slack", "not an object", 400)
        refuses(port, "/ai", {"not": "a list"}, 400)
        refuses(port, "/what", {"anything": 1}, 404)

    assert (control.mails, control.messages, control.ai) == ([], [], [])
    assert CONFIG.startswith("download_dir")
