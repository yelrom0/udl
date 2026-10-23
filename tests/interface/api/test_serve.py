"""
-----------------
- test_serve.py -
-----------------

udl serve: the API on Granian, with one worker, whose process makes the app from udl serve's
settings (Granian is stood in for here; tests/integration/test_servers.py runs the real one)
"""

# System Imports
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

# Package Imports
import pytest
from fastapi import FastAPI
from granian.constants import Interfaces
from pydantic import SecretStr

# Local Imports
from support.cli import CLI
from support.interfaces import Harness
from udl.config import APISettings, Settings
from udl.interfaces.api import serve


class FakeGranian:
    """Stands in for Granian: remembers how it was made and what it was told to serve."""

    made: ClassVar[list[FakeGranian]] = []

    def __init__(self, target: str, **options: Any) -> None:
        self.target = target
        self.options = options
        self.loader: Callable[[], Any] | None = None
        self.wrap_loader: bool | None = None
        FakeGranian.made.append(self)

    def serve(self, *, target_loader: Callable[[], Any], wrap_loader: bool = True) -> None:
        self.loader = target_loader
        self.wrap_loader = wrap_loader


@pytest.fixture(autouse=True)
def granian(monkeypatch: pytest.MonkeyPatch) -> type[FakeGranian]:
    FakeGranian.made = []
    monkeypatch.setattr(serve, "Granian", FakeGranian)
    return FakeGranian


def test_serving_runs_one_granian_worker_on_api_host_and_port(granian: type[FakeGranian]) -> None:
    serve.serve(Settings(api=APISettings(host="127.0.0.2", port=9000)))

    [server] = granian.made
    assert server.options["address"] == "127.0.0.2"
    assert server.options["port"] == 9000
    assert server.options["interface"] is Interfaces.ASGI
    assert server.options["workers"] == 1
    assert server.wrap_loader is False


def test_the_worker_makes_the_app_from_the_settings(granian: type[FakeGranian]) -> None:
    settings = Settings(api=APISettings(api_key=SecretStr("s3cret")))
    serve.serve(settings, verbosity=1)

    [server] = granian.made
    assert server.loader is not None
    app = server.loader()
    assert isinstance(app, FastAPI)
    assert app.state.settings == settings
    assert logging.getLogger("udl").level == logging.INFO  # (-v)
    assert server.options["log_access"] is True


def test_serving_beyond_localhost_without_an_api_key_warns(
    granian: type[FakeGranian], caplog: pytest.LogCaptureFixture
) -> None:
    serve.serve(Settings(api=APISettings(host="0.0.0.0")))  # noqa: S104
    serve.serve(Settings(api=APISettings(host="0.0.0.0", api_key=SecretStr("k"))))  # noqa: S104
    serve.serve(Settings())

    assert [r.message for r in caplog.records if r.levelno == logging.WARNING] == [
        "udl serve answers other machines on 0.0.0.0, and api.api_key is not set: anyone who "
        "can reach it can download with it"
    ]


def test_udl_serve_serves_with_the_settings_it_was_given(
    granian: type[FakeGranian], tmp_path: Path
) -> None:
    config = tmp_path / "config.toml"
    config.write_text("max_per_site = 7\n[api]\nport = 9001\n")
    cli = CLI(Harness(tmp_path))

    default = cli("--config", config, "serve")
    chosen = cli("--config", config, "serve", "--host", "::1", "--port", "9002")

    assert (default.exit_code, chosen.exit_code) == (0, 0)
    first, second = granian.made
    assert (first.options["address"], first.options["port"]) == ("127.0.0.1", 9001)
    assert (second.options["address"], second.options["port"]) == ("::1", 9002)
    assert second.loader is not None
    assert second.loader().state.settings.max_per_site == 7


def test_udl_serve_with_a_bad_port_is_a_usage_error(tmp_path: Path) -> None:
    assert CLI(Harness(tmp_path))("serve", "--port", "0").exit_code == 2
