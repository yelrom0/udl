"""
---------------------
- orch_scripted.py -
---------------------

The 28.6 e2e's agent: a runner whose turns are a script in a file, so a test drives a whole
orchestration end to end through a real `udl serve` — a person's commands on the CLI, and this
agent's own calls over udl's MCP server — without a model or a harness installed.

It is a local plugin (`plugin_dirs` in the test's config): udl imports this file and registers
the class. A turn reads `<dir>/turns.json`, takes the first turn in it, runs its steps, and writes
the file back without that turn, so each turn happens once. A step is one of:

- `{"mcp": {"tool": "orch_ask", "arguments": {...}}}` — a call on udl's MCP server, with the
  token the session was given, as a real agent's tool call is;
- `{"shell": ["git", "add", "-A"]}` — a program, in the session's own folder;
- `{"wait": 0.1}` — a pause, for a turn that has to let the server get somewhere first.

A turn ends with its last step's answer as its text. Nothing here reaches the network but udl's
own MCP server.
"""

# System Imports
import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, TypeGuard

# Package Imports
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

# Local Imports
from udl.plugins.runners.base import (
    AgentEvent,
    AgentRunner,
    RunnerHealth,
    RunnerModel,
    RunnerOptions,
    SessionRef,
    SessionSpec,
    TextDelta,
    ToolFinished,
    TurnEnded,
    TurnReason,
)

STORE: dict[str, SessionSpec] = {}
"""What each session was started with, by id: a session outlives the runner object udl makes for
each call, as a real harness's does."""

TURN_TIMEOUT: timedelta = timedelta(seconds=60)
"""How long one step gets (a tool call back into udl, or a program)."""


def _is_dict(value: object) -> TypeGuard[dict[str, object]]:
    """Whether `value` is a turn, or a step: a mapping of JSON's own."""
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    """Whether `value` is a list of JSON's own (the turns, or a turn's steps)."""
    return isinstance(value, list)


def _dicts(value: object, *, what: str) -> list[dict[str, object]]:
    """`value` as a list of mappings (the turns, or a turn's steps), checked."""
    if not _is_list(value):
        raise ValueError(f"{what} is not a list: {value!r}")
    found: list[dict[str, object]] = []
    for one in value:
        if not _is_dict(one):
            raise ValueError(f"a {what}'s item is not an object: {one!r}")
        found.append(one)
    return found


def _strings(value: object, *, what: str) -> list[str]:
    """`value` as a list of strings (a program and its arguments), checked."""
    if not _is_list(value):
        raise ValueError(f"{what} is not a list: {value!r}")
    return [str(one) for one in value]


def _chosen(turns: Sequence[dict[str, object]], prompt: str) -> int | None:
    """Which turn this prompt is: the first whose `when` it says, else the first with no `when`.

    (The dispatcher may give a turn this script did not expect — it is waiting on the bot, say —
    and that turn must not use up one written for later: a prompt that matches nothing gets a
    turn that does nothing.)"""
    for at, turn in enumerate(turns):
        when = turn.get("when")
        if when is not None and str(when) in prompt:
            return at
    for at, turn in enumerate(turns):
        if turn.get("when") is None:
            return at
    return None


class ScriptedOptions(RunnerOptions):
    """Where the script is."""

    dir: str = ""
    """The folder `turns.json` is in (the test's own temp folder)."""


class ScriptedRunner(AgentRunner[ScriptedOptions]):
    """A runner whose turns are a script in a file (the 28.6 e2e's agent)."""

    name: ClassVar[str] = "scripted"
    options_model: ClassVar[type[RunnerOptions]] = ScriptedOptions

    async def health(self) -> RunnerHealth:
        return RunnerHealth(installed=True, signed_in=True, version="scripted-1.0")

    async def models(self) -> list[RunnerModel]:
        return [RunnerModel(id="scripted-1", name="scripted")]

    async def start(self, spec: SessionSpec) -> SessionRef:
        session = SessionRef(
            runner=self.name,
            id=f"scripted-{len(STORE) + 1}",
            cwd=spec.cwd,
            host=self.shell.host.name,
        )
        STORE[session.id] = spec
        return session

    async def stop(self, session: SessionRef) -> None:
        STORE.pop(session.id, None)

    async def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]:
        spec = STORE.get(session.id)
        if spec is None:
            yield TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
            return
        with (Path(self.options.dir) / "prompts.log").open("a") as log:
            log.write(f"=== {session.id} ===\n{prompt}\n")
        turns = self._turns()
        chosen = _chosen(turns, prompt)
        if chosen is None:
            yield TextDelta(text="Nothing to do yet.")
            yield TurnEnded(reason=TurnReason.DONE, text="Nothing to do yet.")
            return
        steps = _dicts(turns[chosen]["steps"], what="turn")
        self._write([one for at, one in enumerate(turns) if at != chosen])
        said = ""
        for step in steps:
            if "wait" in step:
                await asyncio.sleep(float(str(step["wait"])))
                continue
            if "mcp" in step:
                call = step["mcp"]
                if not _is_dict(call):
                    raise ValueError(f"An mcp step is not an object: {call!r}")
                said = await self._call(spec, call)
                yield ToolFinished(name=str(call.get("tool") or "udl"), ok=True)
                continue
            if "shell" in step:
                said = await self._run(spec, _strings(step["shell"], what="shell"))
                continue
            raise ValueError(f"A step is not one udl knows: {step!r}")
        yield TextDelta(text=str(said))
        yield TurnEnded(reason=TurnReason.DONE, text=str(said))

    # -- what a turn does -----------------------------------------------------------------------

    def _turns(self) -> list[dict[str, object]]:
        """The turns still to run, oldest first."""
        path = Path(self.options.dir) / "turns.json"
        if not path.is_file():
            return []
        return _dicts(json.loads(path.read_text()), what="turns")

    def _write(self, turns: Sequence[dict[str, object]]) -> None:
        """The turns still to run, after the one just taken."""
        path = Path(self.options.dir) / "turns.json"
        path.write_text(json.dumps(list(turns)))

    async def _call(self, spec: SessionSpec, step: Mapping[str, object]) -> str:
        """One MCP call on udl, with the session's own token, as an agent's tool call is."""
        if spec.mcp is None:
            raise ValueError("The session was given no MCP endpoint to call.")
        arguments = step.get("arguments")
        asked: dict[str, object] = {}
        if arguments is not None:
            for one in _dicts([arguments], what="call"):
                asked = one
        token = spec.mcp.token.get_secret_value() if spec.mcp.token is not None else ""
        timeout = TURN_TIMEOUT.total_seconds()
        async with httpx2.AsyncClient(headers={"X-API-Key": token}, timeout=timeout) as http:
            transport = streamable_http_client(spec.mcp.url, http_client=http)
            async with Client(transport) as client:
                answer = await client.call_tool(str(step.get("tool") or ""), asked)
        if answer.is_error:
            raise ValueError(f"The call failed: {answer.content}")
        return json.dumps(answer.structured_content, default=str)

    async def _run(self, spec: SessionSpec, argv: Sequence[str]) -> str:
        """One program, in the session's folder."""
        result = await self.shell.run(
            list(argv), cwd=spec.cwd, check=False, within=TURN_TIMEOUT, tail=True
        )
        return f"{result.returncode}: {result.stdout}"


__all__ = ["ScriptedOptions", "ScriptedRunner"]
