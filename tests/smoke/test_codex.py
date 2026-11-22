"""
--------------
- codex.py -
--------------

The `codex` runner against the real Codex CLI on the machine udl runs on: the one thing the
fake cannot tell us — that its `--json` stream is the shape the runner parses, that a turn really
ends, and that `login status` says what the runner reads. It runs on the local llama.cpp router
through a local profile in `~/.codex/config.toml` (a router's own provider), not on OpenAI:
the user's ChatGPT plan is not udl's to spend.

It needs the CLI (0.171.0), that profile and the router's key in `LLAMA_API_KEY`, so it never
runs by default: `uv run pytest -m network`. The provider the profile names is read from
`UDL_CODEX_PROVIDER` (the profile's own `model_provider`), since it is the machine's.

What it covers, as 25.12's card asks: a real turn that ends and says what it used, a resumed
turn, a real `login status`, and — with a hub started here — one real turn that calls `get_status`
over udl's MCP, which is the card's done-when.
"""

# System Imports
import os
import subprocess
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

# Package Imports
import pytest
from pydantic import SecretStr

# Local Imports
from support import e2e
from support.clock import FakeClock
from support.git import git
from support.runners import Sessions
from udl.hosts.shell import Host, LocalShell
from udl.plugins.runners.base import (
    McpEndpoint,
    SessionSpec,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnReason,
)
from udl.plugins.runners.codex import Codex, CodexOptions, CodexSessions

KEY: Final = os.environ.get("LLAMA_API_KEY", "")
"""The router's key (none: this is skipped)."""
CLI: Final = Path("~/.local/bin/codex").expanduser()
"""The installed Codex CLI (none: this is skipped)."""
PROVIDER: Final = os.environ.get("UDL_CODEX_PROVIDER", "jeff-wiggle")
"""The provider the local profile names (`model_provider` in `~/.codex/local.config.toml`)."""
MODEL: Final = "qwen3.6-35b-a3b"
"""What the router serves (the profile's own model)."""

pytestmark = [
    pytest.mark.skipif(not KEY, reason="LLAMA_API_KEY is not set"),
    pytest.mark.skipif(not CLI.is_file(), reason="codex is not installed"),
]


def codex() -> Codex:
    """The real runner, on a local profile (a router on udl's own machine), with no sign-in."""
    return Codex(
        CodexOptions(cli=[str(CLI)], models=[MODEL], provider=PROVIDER, network=True),
        shell=LocalShell(Host.local("/tmp")),  # noqa: S108 - (a turn is read, not written)
        clock=FakeClock(),
        sessions=CodexSessions(specs=Sessions().specs),
    )


@contextmanager
def repo(path: Path) -> Generator[Path]:
    """A folder that is a git repository: Codex refuses to run outside one."""
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=path)
    yield path


@contextmanager
def hub(home: Path) -> Generator[tuple[int, str]]:
    """A udl hub on a free port with an API key, started as `udl serve` (the way the e2e support
    starts it), and stopped again: what an agent's MCP server is pointed at. What it yields is
    the port it answers on and the key it asks for."""
    port = e2e.free_port()
    config = e2e.write_config(home, port)
    process = subprocess.Popen(
        [sys.executable, "-m", "udl", "serve"],
        cwd=e2e.ROOT,
        env=e2e.environment(config, home),
        stdout=subprocess.DEVNULL,
    )
    try:
        e2e.wait_until(f"http://127.0.0.1:{port}/docs", process)
        yield port, e2e.API_KEY
    finally:
        e2e.stop(process)


async def test_a_real_turn_still_ends_and_says_what_it_used(tmp_path: Path) -> None:
    """A real turn's stream is the shape the runner parses: a thread, a message, a usage, an end."""
    runner = codex()
    with repo(tmp_path / "work") as work:
        session = await runner.start(SessionSpec(cwd=str(work), model=MODEL))

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


async def test_a_real_resumed_turn_ends_too(tmp_path: Path) -> None:
    """The second turn is `exec resume <thread> --json -`, and it ends like the first."""
    runner = codex()
    with repo(tmp_path / "work") as work:
        session = await runner.start(SessionSpec(cwd=str(work), model=MODEL))
        first = [event async for event in runner.send(session, "Remember the number 7.")]
        assert isinstance(first[-1], TurnEnded)
        assert first[-1].reason is TurnReason.DONE

        second = [
            event async for event in runner.send(session, "What number did I ask you to remember?")
        ]

    assert isinstance(second[-1], TurnEnded)
    assert second[-1].reason is TurnReason.DONE


async def test_a_real_turn_calls_get_status_over_udls_mcp(tmp_path: Path) -> None:
    """The card's done-when: one real turn that calls `get_status` over udl's MCP. The hub is
    started here, with a key, and the session's token is that key (in the environment, never in
    an argv)."""
    home = tmp_path / "hub"
    home.mkdir()
    with hub(home) as (port, key), repo(tmp_path / "work") as work:
        token = SecretStr(key)
        runner = codex()
        session = await runner.start(
            SessionSpec(
                cwd=str(work),
                model=MODEL,
                instructions=(
                    "You can call tools. When you are asked to call one, call exactly the tool "
                    "named, and do not list resources or explain instead."
                ),
                mcp=McpEndpoint(url=f"http://127.0.0.1:{port}/mcp", token=token),
                secret_env={"UDL_MCP_TOKEN": token},
            )
        )

        events = [
            event
            async for event in runner.send(
                session,
                "Call the tool named get_status on the udl MCP server (a tool, not a "
                "resource), then say in one line what it returned.",
            )
        ]

    begun = [event.name for event in events if isinstance(event, ToolStarted)]
    ended = [event.name for event in events if isinstance(event, ToolFinished)]
    assert "udl/get_status" in begun, events
    assert "udl/get_status" in ended, events
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE
