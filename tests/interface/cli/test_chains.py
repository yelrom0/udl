"""
------------------
- test_chains.py -
------------------

udl chain suggest / test / set / import / export / wizard
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.downloaders import FakeMedia
from support.engine import script
from support.interfaces import Harness
from udl.chains.files import read_chains
from udl.chains.models import FetchMode, TagChain
from udl.config import config_dir
from udl.core.models import ChainFile, ChainReport, ChainSuggestions, ImportedChains, SiteInfo
from udl.db.models import Strategy, StrategySource

PAGE = "https://example.com/watch/1"
MEDIA = "https://cdn.example.com/v.mp4"
OTHER = "https://cdn.example.com/other.mp4"

FILE = """\
site: example.com
pattern: ^/watch/
steps: iframe#player@src >> video source@src
---
site: other.org
steps: click:#play >> video@src
"""


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    return Harness(
        tmp_path,
        tools={
            "scraper": script(
                suggested={PAGE: {"video#clip@src": MEDIA, "a.download@href": OTHER}},
                chained={PAGE: FakeMedia(title="A film")},
            ),
            "http": script({MEDIA: FakeMedia(content=b"x" * 2000), OTHER: "x", PAGE: "page"}),
        },
    )


def site_info(cli: CLI, site: str) -> SiteInfo:
    result = cli("sites", "show", site, "--json")
    assert result.exit_code == 0, result.output
    return SiteInfo.model_validate_json(result.stdout)


# ---------------------------------------------------------------------------------------------
# suggest
# ---------------------------------------------------------------------------------------------


def test_chain_suggest_lists_chains_best_first(cli: CLI) -> None:
    shown = cli("chain", "suggest", PAGE)
    found = ChainSuggestions.model_validate_json(cli("chain", "suggest", PAGE, "--json").stdout)

    assert shown.exit_code == 0, shown.output
    assert shown.stdout.index("video#clip@src") < shown.stdout.index("a.download@href")
    assert "2.0 kB" in shown.stdout
    assert [s.shorthand for s in found.suggestions] == ["video#clip@src", "a.download@href"]


def test_chain_suggest_with_none_exits_with_1(cli: CLI) -> None:
    result = cli("chain", "suggest", "https://example.com/nothing", "--fetch", "static")

    assert result.exit_code == 1
    assert "No media found" in result.stdout


def test_chain_suggest_says_why_a_page_cannot_be_read(tmp_path: Path) -> None:
    cli = CLI(Harness(tmp_path, tools={"scraper": script(errors={PAGE: "not_found"})}))

    result = cli("chain", "suggest", PAGE)

    assert result.exit_code == 1
    assert "not_found" in result.stdout


# ---------------------------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------------------------


def test_chain_test_shows_each_step_and_the_media(cli: CLI) -> None:
    shown = cli("chain", "test", PAGE, "iframe@src >> video@src")
    result = cli("chain", "test", PAGE, "iframe@src >> video@src", "--json")

    assert shown.exit_code == 0, shown.output
    assert "follow" in shown.stdout
    assert "It leads to 1 media" in shown.stdout
    report = ChainReport.model_validate_json(result.stdout)
    assert [step.step for step in report.steps] == ["iframe@src", "follow", "video@src"]
    assert report.media[0].size_bytes == len(b"page")


def test_chain_test_takes_the_chain_s_options(cli: CLI) -> None:
    result = cli(
        "chain", "test", PAGE, "video@src", "--browser", "--handoff", "yt-dlp",
        "-H", "Referer: {url}", "-H", "X-Site:yes", "--json",
    )  # fmt: skip

    assert result.exit_code == 1  # (the fake browser has no chained media)
    report = ChainReport.model_validate_json(result.stdout)
    assert report.chain == TagChain.model_validate(
        {
            "steps": "video@src",
            "fetch": "browser",
            "handoff": "yt-dlp",
            "headers": {"Referer": "{url}", "X-Site": "yes"},
        }
    )


def test_chain_test_of_a_chain_that_finds_nothing_exits_with_1(cli: CLI) -> None:
    result = cli("chain", "test", "https://example.com/nothing", "video@src")

    assert result.exit_code == 1
    assert "unsupported_url" in result.stdout


@pytest.mark.parametrize(
    ("args", "problem"),
    [
        (["video source"], "end the select step with @attr"),
        (["video@src", "-H", "no colon"], "is not 'NAME: VALUE'"),
        (["video@src", "-H", ": value"], "is not 'NAME: VALUE'"),
    ],
)
def test_chain_test_of_a_chain_that_is_not_one_is_a_usage_error(
    cli: CLI, args: list[str], problem: str
) -> None:
    result = cli("chain", "test", PAGE, *args)

    assert result.exit_code == 2
    assert problem in result.output


# ---------------------------------------------------------------------------------------------
# set
# ---------------------------------------------------------------------------------------------


def test_chain_set_saves_a_chain_for_the_site(cli: CLI) -> None:
    result = cli(
        "chain", "set", "https://www.example.com/x", "click:#play >> video@src",
        "--pattern", "^/watch/", "--priority", "-1", "--requires-auth", "--json",
    )  # fmt: skip
    shown = cli("chain", "set", "example.com", "video source@src")

    assert result.exit_code == 0, result.output
    strategy = Strategy.model_validate_json(result.stdout)
    assert (strategy.site_key, strategy.tool, strategy.url_pattern, strategy.priority) == (
        "example.com",
        "browser",
        "^/watch/",
        -1,
    )
    assert (strategy.requires_auth, strategy.source) == (True, StrategySource.MANUAL)
    assert shown.exit_code == 0, shown.output
    assert "by following video source@src" in shown.stdout
    assert len(site_info(cli, "example.com").strategies) == 2
    assert "video source@src" in cli("sites", "show", "example.com").stdout


# ---------------------------------------------------------------------------------------------
# import and export
# ---------------------------------------------------------------------------------------------


def test_chain_import_imports_chain_files(cli: CLI, tmp_path: Path) -> None:
    file = tmp_path / "chains.yaml"
    file.write_text(FILE)

    shown = cli("chain", "import", file)
    result = cli("chain", "import", "-", "--json", stdin=FILE)

    assert shown.exit_code == 0, shown.output
    assert "Imported example.com" in shown.stdout
    imported = ImportedChains.model_validate_json(result.stdout)
    assert [(s.site_key, s.source) for s in imported.strategies] == [
        ("example.com", StrategySource.IMPORT),
        ("other.org", StrategySource.IMPORT),
    ]


def test_chain_import_reads_the_chain_files_in_the_config_dir_by_default(cli: CLI) -> None:
    sites = config_dir() / "sites"
    sites.mkdir(parents=True)
    (sites / "a.yaml").write_text("site: a.com\nsteps: video@src\n")
    (sites / "b.yml").write_text("site: b.com\nsteps: video@src\n")
    (sites / "notes.txt").write_text("not a chain file")

    result = cli("chain", "import", "--json")

    assert result.exit_code == 0, result.output
    imported = ImportedChains.model_validate_json(result.stdout)
    assert [s.site_key for s in imported.strategies] == ["a.com", "b.com"]


def test_chain_import_without_chain_files_is_a_usage_error(cli: CLI, tmp_path: Path) -> None:
    nothing = cli("chain", "import")
    missing = cli("chain", "import", tmp_path / "missing.yaml")

    assert (nothing.exit_code, missing.exit_code) == (2, 2)
    assert "no chain files" in nothing.output
    assert "missing.yaml" in missing.output


def test_chain_import_of_a_file_with_a_problem_names_it(cli: CLI, tmp_path: Path) -> None:
    file = tmp_path / "broken.yaml"
    file.write_text("site: example.com\nsteps: video source\n")
    empty = tmp_path / "empty.yaml"
    empty.write_text("")

    result = cli("chain", "import", empty, file)

    assert result.exit_code == 2
    assert "broken.yaml" in result.output
    assert "chain 1" in result.output


def test_chain_export_prints_or_writes_a_chain_file(cli: CLI, tmp_path: Path) -> None:
    cli("chain", "import", "-", stdin=FILE)
    output = tmp_path / "out" / "example.yaml"

    printed = cli("chain", "export", "www.example.com")
    written = cli("chain", "export", "example.com", "-o", output)
    exported = ChainFile.model_validate_json(cli("chain", "export", "example.com", "--json").stdout)

    assert printed.exit_code == 0, printed.output
    assert [chain.site for chain in read_chains(printed.stdout)] == ["example.com"]
    assert written.exit_code == 0, written.output
    assert f"Wrote 1 chain for example.com to {output}" in written.stdout
    assert read_chains(output.read_text()) == exported.chains


def test_chain_export_of_a_site_without_chains_exits_with_1(cli: CLI) -> None:
    result = cli("chain", "export", "example.com")

    assert result.exit_code == 1
    assert "no tag chains" in result.output


def test_chain_export_to_a_file_it_cannot_write_exits_with_1(cli: CLI, tmp_path: Path) -> None:
    cli("chain", "import", "-", stdin=FILE)
    blocker = tmp_path / "file"
    blocker.write_text("")

    result = cli("chain", "export", "example.com", "-o", blocker / "chains.yaml")

    assert result.exit_code == 1
    assert "Could not write" in result.output


# ---------------------------------------------------------------------------------------------
# The wizard
# ---------------------------------------------------------------------------------------------


def test_the_wizard_suggests_tests_and_saves_a_chain(cli: CLI) -> None:
    result = cli("chain", "wizard", PAGE, "--pattern", "^/watch/", stdin="1\n\nn\n")

    assert result.exit_code == 0, result.output
    assert "video#clip@src" in result.stdout
    assert "It leads to 1 media" in result.stdout
    [strategy] = site_info(cli, "example.com").strategies
    assert (strategy.chain, strategy.url_pattern) == (
        TagChain.model_validate("video#clip@src"),
        "^/watch/",
    )
    assert not (config_dir() / "sites").exists()


def test_the_wizard_can_write_a_chain_file_for_the_site(cli: CLI) -> None:
    result = cli("chain", "wizard", PAGE, stdin="2\ny\ny\n")

    assert result.exit_code == 0, result.output
    path = config_dir() / "sites" / "example.com.yaml"
    [chain] = read_chains(path.read_text())
    assert chain.chain() == TagChain.model_validate("a.download@href")
    assert f"Wrote {path}" in result.stdout


def test_the_wizard_takes_a_chain_written_out_and_asks_again_until_one_works(
    tmp_path: Path,
) -> None:
    other = "https://example.com/other"
    cli = CLI(Harness(tmp_path, tools={"browser": script(chained={other: "x"})}))

    # (Nothing is suggested; a chain that is not one; one that finds nothing (the fake scraper
    # has no media for it); then one that does (in the browser), but not saved; then saved.)
    result = cli(
        "chain", "wizard", other, "--fetch", "static",
        stdin="video source\nvideo@src\nclick:a >> video@src\nn\nclick:a >> video@src\n\n\n",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert "end the select step with @attr" in result.output
    assert "Pick another chain" in result.stdout
    [strategy] = site_info(cli, "example.com").strategies
    assert strategy.chain is not None
    assert strategy.chain.fetch is FetchMode.BROWSER


def test_the_wizard_of_a_page_that_needed_the_browser_writes_browser_chains(
    tmp_path: Path,
) -> None:
    cli = CLI(
        Harness(
            tmp_path,
            tools={
                "browser": script(suggested={PAGE: {"div#p video@src": MEDIA}}, chained={PAGE: "x"})
            },
        )
    )

    result = cli("chain", "wizard", PAGE, stdin="video@src\n\n\n")

    assert result.exit_code == 0, result.output
    [strategy] = site_info(cli, "example.com").strategies
    assert strategy.chain is not None
    assert strategy.chain.fetch is FetchMode.BROWSER


def test_the_wizard_can_be_left(cli: CLI) -> None:
    result = cli("chain", "wizard", PAGE, stdin="")

    assert result.exit_code == 1
    assert cli("sites", "show", "example.com").exit_code == 1  # (nothing was saved)
