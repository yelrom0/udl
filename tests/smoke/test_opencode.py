"""
----------------------
- test_opencode.py -
----------------------

OpenCode, through the runner, on this machine: one real turn, so a change to the service's API is
caught. It is a smoke test (it costs a turn, and it needs `opencode` installed, its service running
and a model signed in), so it never runs by default: `make smoke` (or `uv run pytest -m network`)
runs it.
"""

# System Imports
from pathlib import Path

# Package Imports
import pytest

# Local Imports
from udl.clock import SystemClock
from udl.hosts.shell import Host, LocalShell
from udl.plugins.runners.base import AgentEvent, SessionSpec, TextDelta, TurnEnded, TurnReason
from udl.plugins.runners.opencode import OpenCode, OpenCodeOptions


async def test_opencode_answers_one_prompt(tmp_path: Path) -> None:
    runner = OpenCode(
        OpenCodeOptions(),
        shell=LocalShell(Host.local(str(tmp_path))),
        clock=SystemClock(),
    )
    health = await runner.health()
    if not health.installed:
        pytest.skip("opencode is not installed here")
    if health.detail is not None and "service" in health.detail:
        pytest.skip(f"opencode's service is not running here: {health.detail}")
    models = await runner.models()
    if not models:
        pytest.skip("opencode has no model signed in here")

    session = await runner.start(
        SessionSpec(
            cwd=str(tmp_path),
            model=models[0].id,
            instructions="Answer in one short word.",
            deny=["git push *"],
        )
    )
    events: list[AgentEvent] = [event async for event in runner.send(session, "Say hello.")]
    await runner.stop(session)

    assert any(isinstance(event, TextDelta) and event.text for event in events), events
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE, events[-1]
