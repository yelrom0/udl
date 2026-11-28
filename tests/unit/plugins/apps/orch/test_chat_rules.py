"""
----------------------
- test_chat_rules.py -
----------------------

What may be remembered for the rest of a chat (27.11): the same tool and the same exact input,
and never a shell command that chains, nests or runs another shell.
"""

# Package Imports
import pytest

# Local Imports
from udl.db.models import JsonObject
from udl.plugins.apps.orch.chat import rememberable


@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "git status",
        "ls -la /tmp",
        "python -m pytest tests/unit",
    ],
)
def test_a_plain_command_may_be_remembered(command: str) -> None:
    assert rememberable("Bash", {"command": command})


@pytest.mark.parametrize(
    "command",
    [
        "ls && rm -rf build",
        "ls || rm -rf build",
        "ls; rm -rf build",
        "ls | rm -rf build",
        "ls & rm -rf build",
        "ls\nrm -rf build",
        "ls\r\nrm -rf build",
        "echo $(rm -rf build)",
        "echo `rm -rf build`",
        "cat <(rm -rf build)",
        "tee >(rm -rf build)",
        "ls 2>&1",
        "ls > out.txt",
        "bash -c 'rm -rf build'",
        "sh -c 'rm -rf build'",
        "/bin/bash -c 'rm -rf build'",
        "env rm -rf build",
        "sudo rm -rf build",
        "xargs rm -rf",
        "eval rm -rf build",
        "exec rm -rf build",
        "nohup rm -rf build &",
        "timeout 5 rm -rf build",
        "ssh host rm -rf build",
        "nice rm -rf build",
        "setsid rm -rf build",
        "stdbuf -o0 rm -rf build",
        "script -c 'rm -rf build'",
        "VAR=x bash -c 'rm -rf build'",
        "A=1 B=2 sh -c 'rm -rf build'",
    ],
)
def test_a_command_that_chains_nests_or_runs_a_shell_is_never_remembered(command: str) -> None:
    """(The card: Allow for this chat is Allow once for any of these.)"""
    assert not rememberable("Bash", {"command": command})


def test_a_command_that_is_not_there_is_not_remembered() -> None:
    assert not rememberable("Bash", {})
    assert not rememberable("Bash", {"command": "   "})
    assert not rememberable("Bash", {"command": 3})


def test_a_tool_that_is_not_a_shell_is_remembered() -> None:
    assert rememberable("Edit", {"file_path": "a.py"})


def test_an_edit_with_no_file_is_not_remembered() -> None:
    """(30.1: allowing a pathless change for the chat would allow every later one.)"""
    assert not rememberable("Edit", {})
    assert not rememberable("Edit", {"file_path": ""})
    assert not rememberable("Edit", {"reason": "it needs to write a file"})


def test_a_shell_command_is_read_from_its_command() -> None:
    """(Its other fields say nothing about what it runs.)"""
    given: JsonObject = {"description": "list the folder", "timeout": 1000}
    assert not rememberable("Bash", given)
