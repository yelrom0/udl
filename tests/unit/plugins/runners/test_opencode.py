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
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

# Package Imports
import anyio
import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr

# Local Imports
from support.clock import T0, FakeClock
from support.opencode import PASSWORD, FakeOpenCode, FakeShell, event
from udl.errors import NotAllowed, ToolUnavailable
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
from udl.plugins.runners.opencode import OpenCode, OpenCodeOptions, OpenCodeSessions
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
            {"action": "edit", "resource": "/w/*", "effect": "allow"},
            {"action": "edit", "resource": "*", "effect": "deny"},
            {"action": "question", "resource": "*", "effect": "deny"},
        ],
        "metadata": {"udl": {"agent": "udl-worker", "task": "25.9"}},
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

    assert _sent(service, "/api/session")["permissions"] == [
        {"action": "*", "resource": "*", "effect": "allow"},
        {"action": "shell", "resource": "git push *", "effect": "deny"},
        {"action": "shell", "resource": "rm *", "effect": "deny"},
        {"action": "external_directory", "resource": "*", "effect": "deny"},
        {"action": "edit", "resource": "/w/one/*", "effect": "allow"},
        {"action": "edit", "resource": "*", "effect": "deny"},
        {"action": "question", "resource": "*", "effect": "deny"},
    ]


async def test_a_model_variant_is_named_as_the_api_names_it(service: FakeOpenCode) -> None:
    await _runner(service, FakeShell()).start(
        SessionSpec(cwd="/w", model="openrouter/deepseek/deepseek-v4.1-flash#fast")
    )

    assert _sent(service, "/api/session")["model"] == {
        "providerID": "openrouter",
        "id": "deepseek/deepseek-v4.1-flash",
        "variant": "fast",
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
    sessions = OpenCodeSessions()

    await _runner(service, FakeShell(), sessions=sessions).start(spec)

    environment = next(call for call in service.calls if call[1].endswith("/environment"))
    assert environment[2] == {"variables": {"UDL_TASK": "26.3", "UDL_AGENT_TOKEN": "s3cret"}}
    assert "s3cret" not in json.dumps(
        [call for call in service.calls if call[1].endswith("/session")]
    )
    assert sessions.secret_envs == [{"UDL_AGENT_TOKEN": "s3cret"}]


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
    reads = [call for call in service.calls if call[1].endswith("/log")]
    assert [read[2] for read in reads] == [{"after": 0}, {"after": 3}]


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


async def test_the_second_turn_resumes_from_the_sequence_the_first_stored(
    service: FakeOpenCode, shell: FakeShell
) -> None:
    runner = _runner(service, shell)
    session = await runner.start(SPEC)
    await _turn(runner, session)

    await _turn(runner, session, "again")

    reads = [call for call in service.calls if call[1].endswith("/log")]
    assert [read[2] for read in reads] == [{"after": 0}, {"after": 3}]


async def test_a_dropped_log_resumes_with_nothing_lost_or_repeated(
    service: FakeOpenCode, shell: FakeShell
) -> None:
    runner = _runner(service, shell)
    session = await runner.start(SPEC)
    service.cut_after = 1  # (the first read drops after one event)

    events = await _turn(runner, session)

    assert [event for event in events if isinstance(event, TextDelta)] == [TextDelta(text="Hello!")]
    assert events[-1] == TurnEnded(reason=TurnReason.DONE)
    reads = [call for call in service.calls if call[1].endswith("/log")]
    assert [read[2] for read in reads] == [{"after": 0}, {"after": 1}]


async def test_a_log_that_keeps_dropping_fails_the_turn(service: FakeOpenCode) -> None:
    service.cut_after = 0  # (every read drops before its first event)
    service.cut_always = True
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)

    events = await _turn(runner, session)

    assert len(events) == 1
    ended = events[0]
    assert isinstance(ended, TurnEnded)
    assert ended.reason is TurnReason.ERROR
    assert "keeps dropping" in (ended.text or "")


async def test_a_permission_asked_is_refused(service: FakeOpenCode) -> None:
    runner = _runner(service, FakeShell())
    session = await runner.start(SPEC)
    service.ask("per_1")

    events = await _turn(runner, session)

    assert service.replies == [("ses_1", "per_1", "reject")]
    assert service.pending == []
    assert events[-1] == TurnEnded(reason=TurnReason.DONE)


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
