"""
---------------
- test_app.py -
---------------

The udl command: `udl <url>...` is `udl get <url>...`, its own options, its exit codes and how
it is installed
"""

# System Imports
import importlib.metadata
import subprocess
import sys
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.interfaces import A, B, Harness
from udl.core.models import DownloadBatch
from udl.db.models import JobStatus
from udl.interfaces.cli.app import main


def batch(stdout: str) -> DownloadBatch:
    return DownloadBatch.model_validate_json(stdout)


def test_udl_with_urls_downloads_them_as_udl_get_does(cli: CLI) -> None:
    for args in ([A, B], ["get", A, B]):
        result = cli(*args, "--json")

        assert result.exit_code == 0, result.output
        downloaded = batch(result.stdout)
        assert [job.status for job in downloaded.jobs] == [JobStatus.FINISHED] * 2
        assert [[p.read_bytes() for p in job.files] for job in downloaded.jobs] == [[b"a"], [b"bb"]]


def test_udl_s_own_options_can_come_before_the_urls(cli: CLI, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("max_per_site = 7\n")

    for args in (["-v", "--config", config], ["-vv", f"--config={config}"], ["-q"]):
        result = cli(*args, A, "--json")

        assert result.exit_code == 0, result.output
        assert batch(result.stdout).jobs[0].status is JobStatus.FINISHED
    assert [loaded.max_per_site for loaded in cli.harness.loaded] == [7, 7, 2]


def test_the_get_options_can_come_before_the_urls(cli: CLI, tmp_path: Path) -> None:
    result = cli("-o", tmp_path / "out", A, "--json")

    assert result.exit_code == 0, result.output
    assert batch(result.stdout).jobs[0].output_dir == tmp_path / "out"


def test_a_command_is_not_taken_for_a_url(cli: CLI) -> None:
    result = cli("sites", "list", "--json")

    assert result.exit_code == 0, result.output
    assert '"sites"' in result.stdout


def test_udl_alone_shows_its_help(cli: CLI) -> None:
    result = cli()

    assert "Usage" in result.output
    assert "get" in result.output


def test_udl_shows_its_version(cli: CLI) -> None:
    result = cli("--version")

    assert result.exit_code == 0
    assert result.stdout.strip() == f"udl {importlib.metadata.version('udl')}"


def test_jobs_sets_how_many_downloads_run_at_once(cli: CLI, harness: Harness) -> None:
    result = cli("-j", "1", A, B)

    assert result.exit_code == 0, result.output
    assert harness.loaded[-1].max_concurrent == 1


def test_a_config_file_that_is_not_there_is_a_usage_error(cli: CLI, tmp_path: Path) -> None:
    result = cli("--config", tmp_path / "nope.toml", "sites", "list")

    assert result.exit_code == 2
    assert "not found" in result.stderr


def test_an_invalid_config_is_a_usage_error(cli: CLI, tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text("max_concurrent = 0\n")

    result = cli("--config", config, "sites", "list")

    assert result.exit_code == 2
    assert "max_concurrent" in result.stderr


def test_a_url_that_is_not_one_is_a_usage_error(cli: CLI) -> None:
    result = cli("ftp://example.com/a")

    assert result.exit_code == 2
    assert "ftp://example.com/a" in result.stderr
    assert cli.harness.loaded == []  # (nothing ran)


def test_some_downloads_failing_exits_with_1(cli: CLI) -> None:
    result = cli(A, "https://example.com/nothing")

    assert result.exit_code == 1


def test_the_udl_script_runs_the_cli() -> None:
    [script] = importlib.metadata.entry_points(group="console_scripts", name="udl")

    assert script.value == "udl.interfaces.cli.app:main"


def test_python_m_udl_runs_the_cli() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "udl", "--version"], capture_output=True, text=True, check=True
    )

    assert done.stdout.startswith("udl ")


def test_main_runs_the_udl_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["udl", "--version"])

    with pytest.raises(SystemExit) as exited:
        main()

    assert exited.value.code == 0
    assert capsys.readouterr().out.startswith("udl ")
