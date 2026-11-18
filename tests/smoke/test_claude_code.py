"""
------------------------
- test_claude_code.py -
------------------------

Claude Code, through the runner, on this machine: one real turn, so a change to the CLI's flags or
its stream-json is caught. It is a smoke test (it costs a turn, and it needs `claude` installed and
signed in), so it never runs by default: `make smoke` (or `uv run pytest -m network`) runs it.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.clock import SystemClock
from udl.hosts.shell import Host, LocalShell
from udl.plugins.runners.base import AgentEvent, SessionSpec, TextDelta, TurnEnded, TurnReason
from udl.plugins.runners.claude_code import ClaudeCode, ClaudeCodeOptions


async def test_claude_answers_one_prompt(tmp_path: Path) -> None:
    runner = ClaudeCode(
        ClaudeCodeOptions(models=["haiku"]),
        shell=LocalShell(Host.local(str(tmp_path))),
        clock=SystemClock(),
    )
    health = await runner.health()
    if not health.installed:
        pytest.skip("claude is not installed here")
    if not health.signed_in:
        pytest.skip("claude is not signed in here")

    session = await runner.start(
        SessionSpec(cwd=str(tmp_path), model="haiku", instructions="Answer in one short word.")
    )
    events: list[AgentEvent] = [event async for event in runner.send(session, "Say hello.")]
    await runner.stop(session)

    assert any(isinstance(event, TextDelta) and event.text for event in events), events
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE, events[-1]
