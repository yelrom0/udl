"""
----------------------
- test_opencode.py -
----------------------

OpenCode, through the runner, on this machine: one real turn, so a change to the service's API is
caught. It is a smoke test (it costs a turn, and it needs `opencode` installed, its service running
and a model signed in), so it never runs by default: `make smoke` (or `uv run pytest -m network`)
runs it.

OpenCode's service is per directory (`opencode service status` answers only where it was started),
so the turn runs in the directory the suite was started from, with every shell command denied.
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


async def test_opencode_answers_one_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    # (The suite points the XDG dirs at a temporary home, so nothing a test does touches the real
    # user's; this one talks to the service the real user started, so it reads the real config.)
    for var, sub in {
        "XDG_CONFIG_HOME": ".config",
        "XDG_DATA_HOME": ".local/share",
        "XDG_STATE_HOME": ".local/state",
        "XDG_CACHE_HOME": ".cache",
    }.items():
        monkeypatch.setenv(var, str(Path.home() / sub))
    where = Path.cwd()
    runner = OpenCode(
        OpenCodeOptions(),
        shell=LocalShell(Host.local(str(where))),
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
            cwd=str(where),
            model=models[0].id,
            instructions="Answer in one short word. Use no tools.",
            deny=["*"],
        )
    )
    events: list[AgentEvent] = [event async for event in runner.send(session, "Say hello.")]
    await runner.stop(session)

    assert any(isinstance(event, TextDelta) and event.text for event in events), events
    assert isinstance(events[-1], TurnEnded)
    assert events[-1].reason is TurnReason.DONE, events[-1]
