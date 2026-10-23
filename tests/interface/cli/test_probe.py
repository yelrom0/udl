"""
-----------------
- test_probe.py -
-----------------

udl probe: which downloaders can download a URL, without downloading it
"""

# System Imports
from pathlib import Path

# Local Imports
from support.cli import CLI
from support.engine import script
from support.interfaces import A, Harness
from udl.core.models import ProbeReport


def report(stdout: str) -> ProbeReport:
    return ProbeReport.model_validate_json(stdout)


def test_probe_stops_at_the_first_tool_that_can(tmp_path: Path) -> None:
    cli = CLI(Harness(tmp_path, tools={"yt-dlp": script({A: "video"})}))

    result = cli("probe", f"{A}?utm_source=x")

    assert result.exit_code == 0, result.output
    assert A in result.stdout
    assert "unsupported_url" in result.stdout  # (http's)
    assert "yt-dlp" in result.stdout
    probed = report(cli("probe", A, "--json").stdout)
    assert [(r.tool, r.ok) for r in probed.results] == [("http", False), ("yt-dlp", True)]


def test_probe_all_probes_every_tool(cli: CLI) -> None:
    probed = report(cli("probe", A, "--all", "--json").stdout)

    assert [r.tool for r in probed.results] == [
        "http",
        "yt-dlp",
        "gallery-dl",
        "scraper",
        "browser",
    ]


def test_probe_one_tool(cli: CLI) -> None:
    probed = report(cli("probe", A, "--tool", "scraper", "--json").stdout)

    assert [r.tool for r in probed.results] == ["scraper"]


def test_probe_with_an_unknown_tool_fails(cli: CLI) -> None:
    result = cli("probe", A, "--tool", "nope")

    assert result.exit_code == 1
    assert "nope" in result.stderr


def test_probe_of_something_that_is_not_a_url_is_a_usage_error(cli: CLI) -> None:
    result = cli("probe", "ftp://example.com/a")

    assert result.exit_code == 2
    assert "url" in result.stderr
