"""
------------------
- test_openapi.py -
------------------

udl/interfaces/api/openapi.py: the API's schema as a file — the same on every machine — and
`--check`, which `make check` runs so the committed one, and the web client's types, stay current.
"""

# System Imports
import subprocess
import sys
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.config import Settings
from udl.interfaces.api.app import create_app
from udl.interfaces.api.openapi import main, render, schema

# A local plugin that adds operations (Gizmo's), with routes of their own: what a machine's own
# plugins would add to the schema if it were made from anything but udl's built-ins.
PLUGIN = '''\
from support.operations import Gizmo


class Extra(Gizmo):
    """Gizmo's operations, from a plugin file of this machine's."""

    name = "extra"
'''

# One of Gizmo's routes (its `find` operation's APISpec).
GIZMO_PATH = "/gizmo/things/{key}"


def test_the_schema_is_the_same_every_time() -> None:
    assert render(schema()) == render(schema())


def test_keys_are_sorted() -> None:
    assert render({"b": 1, "a": {"d": 2, "c": 3}}) == (
        '{\n  "a": {\n    "c": 3,\n    "d": 2\n  },\n  "b": 1\n}\n'
    )


def test_the_schema_is_udls_own_whatever_the_machine_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "extra.py").write_text(PLUGIN)
    monkeypatch.setenv("UDL_PLUGIN_DIRS", f'["{plugins}"]')
    monkeypatch.setenv("UDL_CONFIG", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text(f'plugin_dirs = ["{plugins}"]\n')

    paths = schema()["paths"]

    # (The plugin dir is not ignored by accident: an app made from settings that name it has
    # Gizmo's routes.)
    assert isinstance(paths, dict)
    assert GIZMO_PATH not in paths
    assert GIZMO_PATH in create_app(Settings(plugin_dirs=[plugins])).openapi()["paths"]


def test_check_passes_when_the_file_is_up_to_date(tmp_path: Path) -> None:
    path = tmp_path / "openapi.json"

    assert main([str(path)]) == 0
    assert path.read_text() == render(schema())
    assert main(["--check", str(path)]) == 0


def test_check_fails_when_the_file_is_out_of_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "openapi.json"
    path.write_text("{}\n")

    assert main(["--check", str(path)]) == 1

    error = capsys.readouterr().err
    assert str(path) in error
    # (It says how to fix it.)
    assert f"python -m udl.interfaces.api.openapi {path}" in error


def test_check_fails_when_the_file_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "missing.json"

    assert main(["--check", str(path)]) == 1

    assert str(path) in capsys.readouterr().err


def test_the_written_file_makes_its_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "openapi.json"

    assert main([str(path)]) == 0

    assert path.is_file()


def test_a_path_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage" in capsys.readouterr().err


def test_help_says_how_it_is_used(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == 0

    out = capsys.readouterr().out
    assert "usage" in out
    assert "--check" in out


def test_python_m_writes_the_file(tmp_path: Path) -> None:
    # (The command the card names, run as it is run: a process of its own.)
    path = tmp_path / "nested" / "openapi.json"

    result = subprocess.run(  # noqa: S603 - the interpreter running the tests
        [sys.executable, "-m", "udl.interfaces.api.openapi", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == render(schema())


def test_the_committed_schema_is_up_to_date() -> None:
    # (The web client's types are generated from this file, so it is in the repo, and stale
    # after any route changes until it is regenerated.)
    path = Path(__file__).resolve().parents[4] / "web" / "packages" / "api" / "openapi.json"

    assert main(["--check", str(path)]) == 0
