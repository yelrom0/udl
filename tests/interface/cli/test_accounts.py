"""
--------------------
- test_accounts.py -
--------------------

udl accounts list, show, set, check and rm (and add: tests/integration/test_connecting.py), on
two fake accounts
"""

# System Imports
import json

# Package Imports
import pytest

# Local Imports
from support.accounts import Fakes, with_two_accounts
from support.cli import CLI
from support.interfaces import Harness


@pytest.fixture
def fakes(harness: Harness) -> Fakes:
    return with_two_accounts(harness)


def test_accounts_list_shows_how_each_account_is(cli: CLI, fakes: Fakes) -> None:
    shown = cli("accounts", "list")
    listed = json.loads(cli("accounts", "list", "--integration", "fake", "--json").stdout)

    assert shown.exit_code == 0, shown.output
    for text in ("fake:work", "fake:home", "connected", "work@example.com", "read, write"):
        assert text in shown.stdout
    assert [found["account"] for found in listed["accounts"]] == ["fake:home", "fake:work"]


def test_accounts_list_says_when_there_are_none(harness: Harness) -> None:
    result = CLI(harness)("accounts", "list")

    assert result.exit_code == 0
    assert "No accounts are connected" in result.stdout


def test_accounts_show_shows_an_account(cli: CLI, fakes: Fakes) -> None:
    result = cli("accounts", "show", "fake:work")

    assert result.exit_code == 0, result.output
    for text in ("fake:work", "connected", "work@example.com", "oauth, for read, write", "Work"):
        assert text in result.stdout


def test_accounts_show_of_an_unknown_account_fails(cli: CLI, fakes: Fakes) -> None:
    result = cli("accounts", "show", "fake:play")

    assert result.exit_code == 1
    assert "No fake account called 'play'" in result.output


def test_accounts_set_changes_options_and_whether_it_is_used(cli: CLI, fakes: Fakes) -> None:
    result = cli("accounts", "set", "fake:work", "-O", "label=Office", "--no-enabled", "--json")

    assert result.exit_code == 0, result.output
    shown = json.loads(result.stdout)
    assert (shown["options"], shown["enabled"]) == ({"label": "Office"}, False)
    assert "disabled" in cli("accounts", "list").stdout


def test_accounts_check_says_whether_an_account_works(cli: CLI, fakes: Fakes) -> None:
    result = cli("accounts", "check", "fake:work")

    assert result.exit_code == 0, result.output
    assert "fake.read fake.write" in result.stdout


def test_accounts_rm_disconnects_an_account(cli: CLI, fakes: Fakes) -> None:
    first = cli("accounts", "rm", "fake:work")
    again = cli("accounts", "rm", "fake:work")

    assert first.exit_code == 0, first.output
    assert "its credentials were given back" in first.stdout
    assert "These subscriptions name it, and are kept: work-things." in first.stdout
    assert len(fakes.server.revoked) == 1
    assert "There was no such account" in again.stdout
    assert "Disconnected." in cli("accounts", "rm", "fake:home").stdout
