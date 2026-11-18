"""
----------------
- test_github.py -
----------------

The github integration end to end, against the fake GitHub: an account connected with
`udl accounts add github main` (its token in the secret store, its repositories and API its
options) is checked, its operations run from the CLI (`udl github list-pulls`, `get-pull`,
`comment`, `open-pull`, `merge-pull`), and a repository it does not name is refused
"""

# System Imports
import json
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from support.cli import CLI
from support.github import BASE, TOKEN, FakeGitHub
from support.interfaces import Harness
from udl.plugins.integrations.github.plugin import GitHubIntegration
from udl.plugins.registry import INTEGRATIONS

REPO = "acme/widgets"


@pytest.fixture
def fake() -> FakeGitHub:
    """A GitHub with a token, a repository and two open pulls."""
    return FakeGitHub()


@pytest.fixture
def harness(tmp_path: Path) -> Harness:
    """A harness with the github integration (on the fake, as its accounts say)."""
    found = Harness(tmp_path)
    found.registry.register(INTEGRATIONS, GitHubIntegration)
    return found


def connect(cli: CLI) -> None:
    """`udl accounts add github main`, with the token on stdin and the fake's API its options."""
    added = cli(
        "accounts",
        "add",
        "github",
        "main",
        "--method",
        "tokens",
        "-O",
        f'repos=["{REPO}"]',
        "-O",
        f"api_url={BASE}",
        "--tokens-stdin",
        "--json",
        stdin=json.dumps({"token": TOKEN}),
    )

    assert added.exit_code == 0, added.output


def test_an_account_is_connected_and_listed(harness: Harness) -> None:
    cli = CLI(harness)

    connect(cli)
    found = cli("accounts", "list", "--json")

    assert found.exit_code == 0, found.output
    [account] = json.loads(found.stdout)
    assert account["account"] == "github:main"
    assert account["status"] == "connected"
    assert account["identity"]["display"] == "udl-bot (acme/widgets)"


def test_the_pulls_are_listed_from_the_cli(harness: Harness) -> None:
    cli = CLI(harness)
    connect(cli)

    found = cli("github", "list-pulls", "--repo", REPO, "--json")

    assert found.exit_code == 0, found.output
    assert [pull["number"] for pull in json.loads(found.stdout)["items"]] == [7, 8]


def test_a_pull_is_read_from_the_cli(harness: Harness, fake: FakeGitHub) -> None:
    cli = CLI(harness)
    connect(cli)
    fake.comment_as("Please fix the test")

    found = cli("github", "get-pull", "--repo", REPO, "--number", "7", "--json")

    assert found.exit_code == 0, found.output
    read = json.loads(found.stdout)
    assert read["title"] == "Add a widget"
    assert [note["body"] for note in read["comments"]] == ["Please fix the test"]


def test_a_comment_is_made_from_the_cli(harness: Harness, fake: FakeGitHub) -> None:
    cli = CLI(harness)
    connect(cli)

    found = cli("github", "comment", "--repo", REPO, "--number", "7", "--body", "On it", "--json")

    assert found.exit_code == 0, found.output
    assert [note.body for note in fake.comments["acme/widgets#7"]] == ["On it"]


def test_a_repository_the_account_does_not_name_is_refused(harness: Harness) -> None:
    cli = CLI(harness)
    connect(cli)

    found = cli("github", "get-pull", "--repo", "acme/elsewhere", "--number", "7", "--json")

    assert found.exit_code != 0
    assert "not one of this github account's" in found.output


def test_a_merge_says_which_pull_it_is(harness: Harness, fake: FakeGitHub) -> None:
    cli = CLI(harness)
    connect(cli)

    found = cli(
        "github",
        "merge-pull",
        "--repo",
        REPO,
        "--number",
        "7",
        "--sha",
        "abc1234",
        "--confirm",
        "acme/widgets#8",
        "--json",
    )

    assert found.exit_code != 0
    assert "confirm=acme/widgets#7" in found.output
    assert fake.merged == []
