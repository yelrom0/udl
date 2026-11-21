"""
--------------
- codex.py -
--------------

The `codex` runner against the real Codex CLI on the machine udl runs on: the one thing the
fake cannot tell us — that its `--json` stream is the shape the runner parses, that a turn really
ends, and that `login status` says what the runner reads. It runs on the local llama.cpp router
through a local profile in `~/.codex/config.toml` (a router's own provider), not on OpenAI:
the user's ChatGPT plan is not udl's to spend.

It needs the CLI (0.171.0), a signed-in or configured provider and the router's key in
`LLAMA_API_KEY`, so it never runs by default: `uv run pytest -m network`. Claude runs it at
review, as the card says.

What it does not do: the card's own done-when — `get_status` over udl's MCP from inside the turn.
That needs a hub this process can reach over HTTP (Codex runs as a program, not in-process), so it
belongs to 27.7's e2e run, where a hub and a dispatcher are started anyway. What is tested for
here is the turn, the resume and the login status; the MCP wiring itself is the argv, which the
unit tests read.
"""

# System Imports
import os
from pathlib import Path
from typing import Final

# Package Imports
import pytest

# Local Imports
from support.clock import FakeClock
from support.runners import Sessions
from udl.hosts.shell import Host, LocalShell
from udl.plugins.runners.base import SessionSpec, TurnEnded, TurnReason
from udl.plugins.runners.codex import Codex, CodexOptions, CodexSessions

KEY: Final = os.environ.get("LLAMA_API_KEY", "")
"""The router's key (none: this is skipped)."""
CLI: Final = Path("~/.local/bin/codex").expanduser()
"""The installed Codex CLI (none: this is skipped)."""

pytestmark = [
    pytest.mark.skipif(not KEY, reason="LLAMA_API_KEY is not set"),
    pytest.mark.skipif(not CLI.is_file(), reason="codex is not installed"),
]


def codex() -> Codex:
    """The real runner, on a local profile (a router on udl's own machine), with no sign-in."""
    return Codex(
        CodexOptions(cli=[str(CLI)], models=["qwen3.6-35b-a3b"], provider="local", network=True),
        shell=LocalShell(Host.local("/tmp")),  # noqa: S108 - (a turn is read, not written)
        clock=FakeClock(),
        sessions=CodexSessions(specs=Sessions().specs),
    )


async def test_a_real_turn_still_ends_and_says_what_it_used() -> None:
    """A real turn's stream is the shape the runner parses: a thread, a message, a usage, an end."""
    runner = codex()
    session = await runner.start(SessionSpec(cwd="/tmp"))  # noqa: S108 - (read-only: the sandbox)

    events = [event async for event in runner.send(session, "Say hi in one word.")]

    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE
    assert any(event.__class__.__name__ == "TextDelta" for event in events)
    assert any(event.__class__.__name__ == "Usage" for event in events)


async def test_a_real_login_status_is_read() -> None:
    """`login status` is the runner's own read of the account: it runs, and says either way."""
    runner = codex()

    health = await runner.health()

    assert health.installed
    assert health.version


async def test_a_real_resumed_turn_ends_too() -> None:
    """The second turn is `exec resume <thread> --json -`, and it ends like the first."""
    runner = codex()
    session = await runner.start(SessionSpec(cwd="/tmp"))  # noqa: S108 - (read-only: the sandbox)
    first = [event async for event in runner.send(session, "Remember the number 7.")]
    assert isinstance(first[-1], TurnEnded)
    assert first[-1].reason is TurnReason.DONE

    second = [
        event async for event in runner.send(session, "What number did I ask you to remember?")
    ]

    assert isinstance(second[-1], TurnEnded)
    assert second[-1].reason is TurnReason.DONE
