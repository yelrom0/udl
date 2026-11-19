"""
---------------------
- test_opencode.py -
---------------------

The `opencode` runner: the service's URL and password read through the host's shell, every call
through that shell's `forward()`, a turn as one prompt and the session's durable log resumed from
the last sequence udl stored, the ruleset built from a deny list, a `permission.asked` refused, the
MCP token in the session's environment and in no config file, and no password in a log line.
"""

# System Imports
import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

# Package Imports
import anyio
import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr, TypeAdapter

# Local Imports
from support.clock import T0, FakeClock
from support.opencode import PASSWORD, FakeOpenCode, FakeShell, event
from udl.errors import NotAllowed, PluginFailed, ToolUnavailable
from udl.plugins.runners import opencode
from udl.plugins.runners.base import (
    AgentEvent,
    McpEndpoint,
    RunnerLimited,
    SessionRef,
    SessionSpec,
    TextDelta,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnReason,
    Usage,
)
from udl.plugins.runners.opencode import (
    OpenCode,
    OpenCodeOptions,
    OpenCodeSessions,
    _Event,  # pyright: ignore[reportPrivateUsage]
)
from udl.process import ProcessResult


@pytest.fixture
def service() -> FakeOpenCode:
    """The fake service (this module's, shadowing the conftest's own `service`)."""
    return FakeOpenCode()


@pytest.fixture
def shell() -> FakeShell:
    return FakeShell()


class _Sent(BaseModel):
    """A body the runner sent, as the fake recorded it."""

    model_config = ConfigDict(extra="allow")


class _Document(BaseModel):
    """The OpenAPI document OpenCode serves, as far as its paths go."""

    paths: dict[str, object]


class _Rule(BaseModel):
    """One rule of the session's ruleset, as OpenCode reads it."""

    model_config = ConfigDict(extra="ignore")

    action: str = "*"
    resource: str = "*"
    effect: str = "ask"


def _decide(rules: object, action: str, resource: str) -> str:
    """What OpenCode decides for a request: the last rule whose action and resource match, with
    `*` matching anything (and everything, if no rule matches, is asked)."""
    effect = "ask"
    for rule in TypeAdapter(list[_Rule]).validate_python(rules or []):
        if rule.action not in (action, "*"):
            continue
        pattern = re.escape(rule.resource).replace(r"\*", ".*")
        if re.fullmatch(pattern, resource):
            effect = rule.effect
    return effect


def _sent(service: FakeOpenCode, path: str) -> dict[str, object]:
    """What the runner sent to `path` (its body, as the fake recorded it)."""
    said = next(call for call in service.calls if call[1] == path)[2]
    return _Sent.model_validate(said).model_dump()


def _runner(
    service: FakeOpenCode,
    shell: FakeShell,
    *,
    sessions: OpenCodeSessions | None = None,
    options: OpenCodeOptions | None = None,
) -> OpenCode:
    return OpenCode(
        options if options is not None else OpenCodeOptions(),
        shell=shell,
        clock=FakeClock(),
        transport=httpx.ASGITransport(app=service.app),
        sessions=sessions,
    )


SPEC = SessionSpec(cwd="/w", model="openrouter/deepseek/deepseek-v4.1-flash", deny=["git push *"])


async def _turn(runner: OpenCode, session: SessionRef, prompt: str = "go") -> list[AgentEvent]:
    return [event async for event in runner.send(session, prompt)]


# -- health ------------------------------------------------------------------------------------


async def test_its_health_is_the_service_s_version(service: FakeOpenCode, shell: FakeShell) -> None:
    health = await _runner(service, shell).health()

    assert health.installed
    assert health.signed_in
    assert health.version == "2.1.2"
    assert health.detail == "pid 1234"


async def test_health_says_a_service_that_is_not_running(service: FakeOpenCode) -> None:
    health = await _runner(service, FakeShell(status="stopped")).health()

    assert health.installed
    assert "not running" in (health.detail or "")
    assert "service start" in (health.detail or "")


async def test_health_says_a_service_json_it_cannot_read(service: FakeOpenCode) -> None:
    health = await _runner(service, FakeShell(service="{}")).health()

    assert health.installed
    assert "service.json" in (health.detail or "")


async def test_health_says_a_program_that_is_not_there() -> None:
    class Missing(FakeShell):
        async def run(self, argv: Sequence[str], **fields: object) -> ProcessResult:
            raise ToolUnavailable("opencode: command not found")

    health = await OpenCode(OpenCodeOptions(), shell=Missing(), clock=FakeClock()).health()

    assert not health.installed
    assert "command not found" in (health.detail or "")


async def test_a_bearer_header_is_refused(service: FakeOpenCode, shell: FakeShell) -> None:
    # (The fake answers 401 unless the password is the service.json one: a wrong password is a
    # failure udl says, never a silent empty answer.)
    health = await _runner(service, FakeShell(service=json.dumps({"password": "wrong"}))).health()

    assert health.installed
    assert "401" in (health.detail or "")


# -- models ------------------------------------------------------------------------------------


async def test_its_models_are_the_server_s(service: FakeOpenCode, shell: FakeShell) -> None:
    models = await _runner(service, shell).models()

    assert [model.id for model in models] == ["openrouter/deepseek/deepseek-v4.1-flash"]
    assert models[0].name == "DeepSeek V4.1 Flash"
    assert service.calls[-1] == ("GET", "/api/model", {})


async def test_the_models_it_may_run_are_the_ones_it_lists(service: FakeOpenCode) -> None:
    runner = _runner(
        service, FakeShell(), options=OpenCodeOptions(models=["openrouter/other/model"])
    )

    assert await runner.models() == []


async def test_a_location_the_options_name_is_the_one_models_are_listed_for(
    service: FakeOpenCode,
) -> None:
    await _runner(service, FakeShell(), options=OpenCodeOptions(directory="/w/task")).models()

    assert service.calls[-1] == ("GET", "/api/model", {"location[directory]": "/w/task"})


async def test_models_that_cannot_be_listed_say_so(service: FakeOpenCode) -> None:
    with pytest.raises(NotAllowed, match="not running"):
        await _runner(service, FakeShell(status="stopped")).models()


# -- a session ---------------------------------------------------------------------------------


async def test_a_session_is_created_with_the_ruleset_the_location_and_the_model(
    service: FakeOpenCode, shell: FakeShell
) -> None:
    session = await _runner(
        service, shell, options=OpenCodeOptions(agent="udl-worker", title="25.9")
    ).start(SPEC)

    assert session.id == "ses_1"
    assert session.runner == "opencode"
    assert session.cwd == "/w"
    assert _sent(service, "/api/session") == {
        "title": "25.9",
        "model": {"providerID": "openrouter", "id": "deepseek/deepseek-v4.1-flash"},
        "agent": "udl-worker",
        "location": {"directory": "/w"},
        "permissions": [
            {"action": "*", "resource": "*", "effect": "allow"},
            {"action": "shell", "resource": "git push *", "effect": "deny"},
            {"action": "external_directory", "resource": "*", "effect": "deny"},
            {"action": "edit", "resource": "*", "effect": "deny"},
            {"action": "edit", "resource": "/w/*", "effect": "allow"},
            {"action": "question", "resource": "*", "effect": "deny"},
        ],
        "metadata": {"udl": {"agent": "udl-worker"}},
    }


async def test_the_ruleset_allows_the_worktree_and_forbids_what_udl_does(
    service: FakeOpenCode,
) -> None:
    # (V2's session rules are `permissions`, and their actions are `shell`, `edit`,
    # `external_directory` and `question`; the last match wins. `tool.use` is a policy's action,
    # not a session rule's.)
    await _runner(service, FakeShell()).start(
        SessionSpec(cwd="/w/one", deny=["git push *", "rm *"])
    )

    rules = _sent(service, "/api/session")["permissions"]
    assert rules == [
        {"action": "*", "resource": "*", "effect": "allow"},
        {"action": "shell", "resource": "git push *", "effect": "deny"},
        {"action": "shell", "resource": "rm *", "effect": "deny"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": "/w/one/*", "effect": "allow"},
        {"action": "question", "resource": "*", "effect": "deny"},
    ]
    # (What the service makes of them: last match wins, and `*` crosses `/`.)
    assert _decide(rules, "edit", "/w/one/src/x.py") == "allow"
    assert _decide(rules, "edit", "/w/other/x.py") == "deny"
    assert _decide(rules, "edit", "/w/one") == "deny"  # (a path, not a folder's contents)
    assert _decide(rules, "shell", "git push origin main") == "deny"
    assert _decide(rules, "shell", "git status") == "allow"
    assert _decide(rules, "external_directory", "/etc") == "deny"
    assert _decide(rules, "question", "*") == "deny"
    assert _decide(rules, "read", "x.py") == "allow"


async def test_a_model_variant_is_named_as_the_api_names_it(service: FakeOpenCode) -> None:
    await _runner(service, FakeShell()).start(
        SessionSpec(cwd="/w", model="openrouter/deepseek/deepseek-v4.1-flash#fast")
    )

    assert _sent(service, "/api/session")["model"] == {
        "providerID": "openrouter",
        "id": "deepseek/deepseek-v4.1-flash",
        "variant": "fast",
    }


async def test_a_model_with_no_provider_keeps_its_variant(service: FakeOpenCode) -> None:
    # (A bare name is sent as both provider and id — the service says whether it has it — and the
    # variant after the `#` is kept with it, or the model would be asked for in the wrong shape.)
    await _runner(service, FakeShell()).start(SessionSpec(cwd="/w", model="gpt-5#high"))

    assert _sent(service, "/api/session")["model"] == {
        "providerID": "gpt-5",
        "id": "gpt-5",
        "variant": "high",
    }


async def test_a_spec_with_no_model_leaves_the_session_s_own(service: FakeOpenCode) -> None:
    await _runner(service, FakeShell()).start(SessionSpec(cwd="/w"))

    sent = _sent(service, "/api/session")
    assert sent["model"] is None
    assert sent["agent"] is None


async def test_the_mcp_server_and_the_token_are_added_where_the_api_takes_them(
    service: FakeOpenCode,
) -> None:
    spec = SessionSpec(
        cwd="/w",
        mcp=McpEndpoint(url="https://hub.example/mcp", token=SecretStr("s3cret")),
    )

    await _runner(service, FakeShell()).start(spec)

    mcp = next(call for call in service.calls if call[1].startswith("/api/experimental/mcp"))
    assert mcp[2] == {
        "config": {
            "type": "remote",
            "url": "https://hub.example/mcp",
            "headers": {"X-API-Key": "{env:UDL_AGENT_TOKEN}"},
            "oauth": False,
        }
    }
    environment = next(call for call in service.calls if call[1].endswith("/environment"))
    assert environment[2] == {"variables": {"UDL_AGENT_TOKEN": "s3cret"}}
    assert "s3cret" not in json.dumps(mcp[2])


# -- a turn ------------------------------------------------------------------------------------


async def test_the_spec_s_environment_and_secrets_go_to_the_session_s_own(
    service: FakeOpenCode,
) -> None:
    spec = SessionSpec(
        cwd="/w",
        env={"UDL_TASK": "26.3"},
        secret_env={"UDL_AGENT_TOKEN": SecretStr("s3cret")},
    )

    await _runner(service, FakeShell()).start(spec)

    environment = next(call for call in service.calls if call[1].endswith("/environment"))
    assert environment[2] == {"variables": {"UDL_TASK": "26.3", "UDL_AGENT_TOKEN": "s3cret"}}
    assert "s3cret" not in json.dumps(
        [call for call in service.calls if call[1].endswith("/session")]
    )


async def test_a_spec_with_no_environment_sets_none(service: FakeOpenCode) -> None:
    await _runner(service, FakeShell()).start(SessionSpec(cwd="/w"))

    assert not [call for call in service.calls if call[1].endswith("/environment")]


async def test_another_runner_resumes_a_session_by_id(service: FakeOpenCode) -> None:
    sessions = OpenCodeSessions()
    first = _runner(service, FakeShell(), sessions=sessions)
    session = await first.start(SPEC)
    await _turn(first, session)

    events = await _turn(_runner(service, FakeShell(), sessions=sessions), session, "again")

    assert events[-1] == TurnEnded(reason=TurnReason.DONE)
    # (The session store carries what the first runner knew: its spec, and the last sequence.)
    assert sessions.specs[session.id] == SPEC


def _real_turn() -> list[dict[str, object]]:
    """One real turn's events, as OpenCode 2.1.2 sent them (a run on 2026-11-20, committed)."""
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "opencode" / "events-2.1.2.json"
    said: list[dict[str, object]] = json.loads(fixture.read_text(encoding="utf-8"))
    turn = [item for item in said if item["type"] != "server.connected"]
    for item in turn:  # (the fixture is one real session's; the fake's is `ses_1`)
        data = item.get("data")
        if isinstance(data, dict) and "sessionID" in data:
            data["sessionID"] = "ses_1"
    return turn


def _document() -> dict[str, object]:
    """The OpenAPI document OpenCode 2.1.2 serves, as committed (the calls are written to it)."""
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "opencode" / "openapi-2.1.2.json"
    said: dict[str, object] = json.loads(fixture.read_text(encoding="utf-8"))
    return said


async def test_every_call_it_makes_is_in_the_openapi_document_it_was_written_against(
    service: FakeOpenCode,
) -> None:
    document = _document()
    known = set(_Document.model_validate(document).paths)
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.ask("per_1")
    await _turn(runner, session)
    await runner.models()
    await runner.health()
    await runner.stop(session)

    made = {
        "/api/session/{sessionID}/permission/{requestID}/reply"
        if "/permission/" in path
        else path.replace(session.id, "{sessionID}")
        for _, path, _ in service.calls
    }
    assert made
    assert made <= known, made - known


async def test_a_turn_is_a_prompt_and_the_log_from_the_sequence_udl_stored(
    service: FakeOpenCode, shell: FakeShell
) -> None:
    runner = _runner(service, shell)
    session = await runner.start(SPEC)

    events = await _turn(runner, session, "Say hello.")

    assert next(call for call in service.calls if call[1].endswith("/prompt"))[2] == {
        "text": "Say hello.",
        "delivery": "queue",
        "metadata": {"udl": True},
    }
    assert events == [
        TextDelta(text="Hello!"),
        TurnEnded(reason=TurnReason.DONE),
    ]


async def test_each_turn_is_followed_on_the_service_s_own_event_stream(
    service: FakeOpenCode, shell: FakeShell
) -> None:
    runner = _runner(service, shell)
    session = await runner.start(SPEC)
    await _turn(runner, session)
    await _turn(runner, session, "again")

    # (The session log replays nothing in 2.1.2, so a turn is followed on `/api/event`, subscribed
    # before its prompt — one subscription per turn, and one more when a read ends empty.)
    reads = [call for call in service.calls if call[1] == "/api/event"]
    assert len(reads) == 2 * 2


async def test_a_dropped_stream_is_opened_again_and_the_turn_still_ends(
    service: FakeOpenCode, shell: FakeShell
) -> None:
    runner = _runner(service, shell)
    session = await runner.start(SPEC)
    service.cut_after = 1  # (the first subscription drops after one event)
    service.keep_log = True  # (so the second one can bring what the first did not)

    events = await _turn(runner, session)

    # (The stream is volatile, so a drop can lose what it had not said; the turn's end comes from
    # the session itself, so it never hangs, and the reopened stream brings the rest.)
    assert [event for event in events if isinstance(event, TextDelta)] == [TextDelta(text="Hello!")]
    assert events[-1] == TurnEnded(reason=TurnReason.DONE)
    reads = [call for call in service.calls if call[1] == "/api/event"]
    assert len(reads) >= 3  # (the empty first read, the dropped one, and the one that brought it)


async def test_a_stream_that_keeps_dropping_with_no_outcome_fails_the_turn(
    service: FakeOpenCode,
) -> None:
    service.cut_after = 0
    service.cut_always = True
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.report_outcome = False  # (it never says how a turn ended)

    events = await _turn(runner, session)

    assert len(events) == 1
    assert isinstance(events[0], TurnEnded)
    assert events[0].reason is TurnReason.ERROR
    assert "keep dropping" in (events[0].text or "")


async def test_the_stream_is_open_before_the_prompt_is_sent(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    await _turn(runner, session)

    # (The stream has no replay: a fast turn's own events would be gone before udl listened.)
    order = [call[1] for call in service.calls]
    assert order.index("/api/event") < order.index(f"/api/session/{session.id}/prompt")


async def test_a_turn_that_is_silent_for_a_while_is_not_given_up_on(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.silent_reads = 5  # (five subscriptions that say nothing, then the turn)

    events = await _turn(runner, session)

    assert events[-1] == TurnEnded(reason=TurnReason.DONE)
    assert [event for event in events if isinstance(event, TextDelta)] == [TextDelta(text="Hello!")]


async def test_a_stream_that_drops_before_the_turn_says_it_started_still_ends_it(
    service: FakeOpenCode,
) -> None:
    # (The stream is volatile: the turn can start and end inside a gap, so its `started` event
    # never arrives. The session's own time says it went idle after this turn's prompt, so the
    # turn still ends with its outcome instead of running until it is given up on.)
    service.omit_started = True
    service.ends_before_served = True  # (it ended on the service, while the read was away)
    service.cut_after = 1
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events[-1] == TurnEnded(reason=TurnReason.DONE)


async def test_silence_between_a_turn_s_events_does_not_count_against_it(
    service: FakeOpenCode,
) -> None:
    # (Only a read that says nothing counts against the turn, and a turn that says something
    # starts the count again: two 28-read silences either side of a part are still one turn.)
    service.parts = 3
    service.gap = 28  # (just under `_DROPS`, twice: without the reset the turn is given up on)
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events[-1] == TurnEnded(reason=TurnReason.DONE)
    assert [event for event in events if isinstance(event, TextDelta)] == [TextDelta(text="Hello!")]


async def test_a_second_turn_never_takes_the_first_turn_s_outcome(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    await _turn(runner, session)
    service.report_outcome = False  # (the second turn's own outcome is never said)
    service.cut_after = 0  # (its stream drops before a single event)
    service.cut_always = True

    events = await _turn(runner, session, "again")

    # (The first turn ended `succeeded`; without this turn's own `execution.started` that must not
    # be read as this turn's end.)
    assert len(events) == 1
    assert isinstance(events[0], TurnEnded)
    assert events[0].reason is TurnReason.ERROR


async def test_a_permission_asked_is_refused(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.ask("per_1")

    events = await _turn(runner, session)

    assert service.replies == [("ses_1", "per_1", "reject", "Not allowed here: ask with orch_ask.")]
    assert service.pending == []
    assert events[-1] == TurnEnded(reason=TurnReason.DONE)


async def test_a_real_turn_s_events_are_read_as_the_card_says(service: FakeOpenCode) -> None:
    """The events OpenCode 2.1.2 really sends for one turn (committed as a fixture, captured on a
    real run): its text streams as deltas, its usage is read, and the turn ends once."""
    service.log = _real_turn()
    service.turn()  # (the fixture is the whole turn)
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events == [
        TextDelta(text="Hello there, friend."),
        Usage(
            input_tokens=118,
            output_tokens=9,
            cost=Decimal("0.00007404488"),
        ),
        TurnEnded(reason=TurnReason.DONE),
    ]


async def test_the_spec_s_instructions_become_the_agent_it_runs_as(service: FakeOpenCode) -> None:
    shell = FakeShell()
    await _runner(service, shell).start(
        SessionSpec(cwd="/w", instructions="You are udl's worker. Do the task.")
    )

    [name] = list(shell.files)
    assert name.startswith("udl-")
    assert len(name) == len("udl-") + 12
    assert shell.files[name] == "---\nmode: primary\n---\n\nYou are udl's worker. Do the task.\n"
    assert _sent(service, "/api/session")["agent"] == name


async def test_two_briefs_never_share_an_agent_file(service: FakeOpenCode) -> None:
    shell = FakeShell()
    runner = _runner(service, shell)

    await runner.start(SessionSpec(cwd="/w", instructions="one"))
    await runner.start(SessionSpec(cwd="/w", instructions="two"))

    assert len(shell.files) == 2


async def test_a_brief_that_cannot_be_written_is_not_run(service: FakeOpenCode) -> None:
    shell = FakeShell()
    shell.writes_fail = True

    with pytest.raises(PluginFailed, match="agent file"):
        await _runner(service, shell).start(SessionSpec(cwd="/w", instructions="a brief"))

    assert service.calls == []  # (no session was created)


async def test_a_spec_with_no_brief_uses_the_options_agent(service: FakeOpenCode) -> None:
    shell = FakeShell()

    await _runner(service, shell).start(SessionSpec(cwd="/w"))

    assert shell.files == {}
    assert _sent(service, "/api/session")["agent"] is None


async def test_a_prompt_the_service_refuses_ends_the_turn(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.prompt_status = 500

    events = await _turn(runner, session)

    assert len(events) == 1
    assert isinstance(events[0], TurnEnded)
    assert events[0].reason is TurnReason.ERROR
    assert "500" in events[0].text


async def test_a_service_that_is_not_running_ends_the_turn(service: FakeOpenCode) -> None:
    sessions = OpenCodeSessions(specs={"ses_1": SPEC})
    runner = _runner(service, FakeShell(status="stopped"), sessions=sessions)
    session = SessionRef(runner="opencode", id="ses_1", cwd="/w", host="local")

    events = await _turn(runner, session)

    assert events == [
        TurnEnded(
            reason=TurnReason.ERROR,
            text="opencode's service is not running (stopped): `opencode service start` starts it.",
        )
    ]


async def test_a_stream_read_that_errors_does_not_fail_the_turn(
    service: FakeOpenCode, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.turn(
        event("session.text.ended", 0, {"text": "one"}),
        event("session.execution.succeeded", 0, {}),
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    real = opencode._sse  # pyright: ignore[reportPrivateUsage]
    reads = {"n": 0}

    async def flaky(answer: httpx.Response) -> AsyncIterator[_Event]:
        reads["n"] += 1
        if reads["n"] == 1:  # (as `stop` closing the stream under a read, or a timeout, does)
            raise httpx.ReadError("the stream closed")
        async for said in real(answer):
            yield said

    monkeypatch.setattr(opencode, "_sse", flaky)

    events = await _turn(runner, session)

    # (The read that errored had taken nothing; the next one brought the turn, which still ended.)
    assert events == [TextDelta(text="one"), TurnEnded(reason=TurnReason.DONE)]
    assert reads["n"] == 2


async def test_a_permission_of_another_session_is_left_alone(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.ask("per_2", sessionID="ses_other")

    await _turn(runner, session)

    assert service.replies == []
    assert [said["id"] for said in service.pending] == ["per_2"]


async def test_a_permission_list_that_cannot_be_read_does_not_end_the_turn(
    service: FakeOpenCode,
) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.permission_list_fails = True

    events = await _turn(runner, session)

    assert events[-1] == TurnEnded(reason=TurnReason.DONE)


async def test_a_turn_that_failed_says_why(service: FakeOpenCode) -> None:
    service.turn(
        event(
            "session.execution.failed",
            0,
            {"sessionID": "ses", "error": {"type": "ProviderError", "message": "no credit"}},
        )
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events == [TurnEnded(reason=TurnReason.ERROR, text="no credit")]


async def test_a_turn_out_of_quota_is_a_limit_that_says_when_it_lifts(
    service: FakeOpenCode,
) -> None:
    service.turn(
        event(
            "session.execution.failed",
            0,
            {
                "sessionID": "ses",
                "error": {
                    "type": "ProviderError",
                    "message": "The account is out of credit.",
                    "status": 429,
                    "retryAfterMs": 60_000,
                },
            },
        )
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events[0] == RunnerLimited(
        until=T0 + timedelta(seconds=60), message="The account is out of credit."
    )
    assert events[-1] == TurnEnded(reason=TurnReason.LIMITED, text="The account is out of credit.")


async def test_a_failure_that_is_not_a_limit_is_an_error(service: FakeOpenCode) -> None:
    service.turn(
        event(
            "session.execution.failed",
            0,
            {"sessionID": "ses", "error": {"type": "ProviderError", "status": 500}},
        )
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events == [TurnEnded(reason=TurnReason.ERROR, text="ProviderError")]


async def test_what_a_turn_cost_is_a_usageevent(service: FakeOpenCode) -> None:
    service.turn(
        event(
            "session.usage.recorded",
            0,
            {
                "sessionID": "ses",
                "source": "generate",
                "cost": 0.0125,
                "tokens": {"input": 12, "output": 34},
            },
        ),
        *service.script[-1:],
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    [usage] = [event for event in await _turn(runner, session) if isinstance(event, Usage)]

    assert (usage.input_tokens, usage.output_tokens) == (12, 34)
    assert usage.cost == Decimal("0.0125")


async def test_the_tools_a_turn_ran_are_events(service: FakeOpenCode) -> None:
    service.turn(
        event(
            "session.tool.called",
            0,
            {
                "sessionID": "ses",
                "assistantMessageID": "msg",
                "id": "call_1",
                "input": {"command": "git status"},
                "executed": True,
                "native": {"name": "shell"},
            },
        ),
        event(
            "session.tool.failed",
            0,
            {
                "sessionID": "ses",
                "assistantMessageID": "msg",
                "id": "call_1",
                "error": {"name": "Boom", "message": "no"},
                "executed": True,
                "native": {"name": "shell"},
            },
        ),
        *service.script[-1:],
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events[:2] == [
        ToolStarted(name="shell", summary="git status"),
        ToolFinished(name="shell", ok=False),
    ]


async def test_a_tool_with_no_provider_blob_is_just_a_tool(service: FakeOpenCode) -> None:
    service.turn(
        event(
            "session.tool.called",
            0,
            {
                "sessionID": "ses",
                "assistantMessageID": "msg",
                "id": "c",
                "input": {},
                "executed": True,
            },
        ),
        event(
            "session.tool.success",
            0,
            {
                "sessionID": "ses",
                "assistantMessageID": "msg",
                "id": "c",
                "content": [],
                "executed": True,
            },
        ),
        *service.script[-1:],
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events[:2] == [ToolStarted(name="tool", summary=""), ToolFinished(name="tool", ok=True)]


async def test_an_unknown_event_is_skipped(service: FakeOpenCode) -> None:
    service.turn(event("session.whatever", 0, {"sessionID": "ses"}), *service.script)
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events == [TextDelta(text="Hello!"), TurnEnded(reason=TurnReason.DONE)]


async def test_a_stopped_turn_is_interrupted(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    await runner.stop(session)

    assert service.interrupted
    assert await _turn(runner, session) == [
        TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")
    ]


async def test_a_log_that_will_not_be_read_fails_the_turn(service: FakeOpenCode) -> None:
    service.log_status = 500
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    ended = events[0]
    assert isinstance(ended, TurnEnded)
    assert ended.reason is TurnReason.ERROR
    assert "500" in (ended.text or "")


async def test_a_turn_stopped_while_it_runs_ends_as_stopped(service: FakeOpenCode) -> None:
    # (The service's log has no end in sight, and `stop` interrupts it: the turn says stopped.)
    runner = _runner(service, FakeShell())
    session = await runner.start(SessionSpec(cwd="/w", env={"FAKE_HARNESS_SLOW": "1"}))

    async def stop_soon() -> None:
        await anyio.sleep(0.02)
        await runner.stop(session)

    stopping = asyncio.create_task(stop_soon())
    events = await _turn(runner, session)
    await stopping

    assert events[-1] == TurnEnded(reason=TurnReason.STOPPED, text="The session was stopped.")


async def test_an_interrupted_execution_ends_the_turn_as_stopped(service: FakeOpenCode) -> None:
    service.turn(
        event("session.execution.interrupted", 0, {"sessionID": "ses"}),
    )
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert events == [TurnEnded(reason=TurnReason.STOPPED, text="The turn was interrupted.")]


async def test_a_service_that_says_no_port_is_said_as_a_problem() -> None:
    runner = _runner(FakeOpenCode(), FakeShell(status="http://127.0.0.1"))

    health = await runner.health()

    assert health.installed
    assert "no port" in (health.detail or "")


async def test_a_permission_with_no_id_is_left_alone(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.pending.append({"sessionID": "ses", "message": "what?"})

    await _turn(runner, session)

    assert service.replies == []
    assert len(service.pending) == 1


async def test_a_session_it_never_started_says_so(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    stranger = SessionRef(runner="opencode", id="ses_9", cwd="/w", host="localhost")

    assert await _turn(runner, stranger) == [
        TurnEnded(reason=TurnReason.ERROR, text="No session 'ses_9'.")
    ]


async def test_the_password_is_in_no_log_line(
    service: FakeOpenCode, shell: FakeShell, caplog: pytest.LogCaptureFixture
) -> None:
    runner = _runner(service, shell)
    session = await runner.start(SPEC)
    await _turn(runner, session)

    assert PASSWORD not in caplog.text
    assert PASSWORD not in json.dumps(service.calls)
