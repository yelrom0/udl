"""
----------------------------
- test_codex_app_server.py -
----------------------------

The fake `codex app-server` speaks the protocol the committed schemas describe
(`tests/fixtures/codex-app-server/`, from `codex app-server generate-json-schema`): every message
it sends is validated against them.

This is what ties the fake to the fixtures: without it the two drift, and the driver's tests pass
on shapes the real Codex never sends (the first review's own finding).
"""

# System Imports
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

# Package Imports
import jsonschema
from pydantic import TypeAdapter

# Local Imports
from udl.db.models import JsonObject

# (The repo root: this file is tests/unit/support/test_codex_app_server.py.)
ROOT = Path(__file__).resolve().parents[3]
FAKE = ROOT / "tests" / "support" / "codex_app_server.py"
SCHEMAS = ROOT / "tests" / "fixtures" / "codex-app-server"

V2 = json.loads((SCHEMAS / "codex_app_server_protocol.v2.schemas.json").read_text())
V1 = json.loads((SCHEMAS / "codex_app_server_protocol.schemas.json").read_text())

LEGACY: frozenset[str] = frozenset(
    entry["properties"]["method"]["enum"][0]
    for entry in V1["definitions"]["ServerRequest"]["oneOf"]
)
"""The older requests (the v1 vocabulary)."""

APPROVALS: frozenset[str] = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "execCommandApproval",
        "applyPatchApproval",
    }
)
"""The four approval requests, whichever protocol they belong to."""

START: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": "1",
    "method": "initialize",
    "params": {"clientInfo": {"name": "udl", "version": "1"}},
}
THREAD: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": "2",
    "method": "thread/start",
    "params": {"cwd": str(ROOT)},
}
TURN: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": "3",
    "method": "turn/start",
    "params": {"threadId": "thread-1", "input": [{"type": "text", "text": "go"}]},
}
ANSWER: dict[str, Any] = {"jsonrpc": "2.0", "id": "approval-1", "result": {"decision": "accept"}}

MODES = ("turn", "command", "file", "legacy-exec", "legacy-patch")


_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
"""One answer's `result`, as the JSON object it is."""


def schema(document: dict[str, Any], name: str) -> dict[str, Any]:
    """One definition of a document, with its own `$ref`s resolved inside it."""
    return {**document["definitions"][name], "definitions": document["definitions"]}


def check(document: dict[str, Any], name: str, message: dict[str, Any]) -> None:
    """`message` as the schema has it (a failure says which field)."""
    jsonschema.validate(message, schema(document, name))


def said(mode: str, *requests: object) -> list[dict[str, Any]]:
    """What the fake says for `mode`, given the requests udl would send it (a trailing string is
    where it keeps the answers udl wrote)."""
    argv = [sys.executable, str(FAKE), mode]
    rest: list[object] = list(requests)
    if rest and isinstance(rest[-1], str):
        argv.append(rest[-1])
        rest = rest[:-1]
    process = subprocess.Popen(  # noqa: S603 - the fake is the program under test
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    for request in rest:
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
    process.stdin.close()
    out, _ = process.communicate(timeout=30)
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def approvals(mode: str) -> list[dict[str, Any]]:
    """The approval the fake asks in `mode` (there is one)."""
    return [
        one for one in said(mode, START, THREAD, TURN, ANSWER) if one.get("method") in APPROVALS
    ]


def test_every_message_the_fake_says_is_the_protocol() -> None:
    for mode in MODES:
        for message in said(mode, START, THREAD, TURN, ANSWER):
            method = message.get("method")
            if method is None:
                continue
            params = message.get("params")
            if method == "item/agentMessage/delta":
                check(V2, "AgentMessageDeltaNotification", params or {})
            elif method == "thread/tokenUsage/updated":
                check(V2, "ThreadTokenUsageUpdatedNotification", params or {})
            elif method == "turn/completed":
                check(V2, "TurnCompletedNotification", params or {})
            elif method == "item/started":
                check(V2, "ItemStartedNotification", params or {})
            elif method in LEGACY:
                check(V1, "ServerRequest", message)
            else:
                check(V2, "ServerRequest", message)


def test_the_fake_asks_each_approval_in_its_own_shape() -> None:
    """(v2's command is a string; the older exec's is an array; a patch carries `fileChanges`.)"""
    asked = {
        mode: approvals(mode)[0] for mode in ("command", "file", "legacy-exec", "legacy-patch")
    }

    assert isinstance(asked["command"]["params"]["command"], str)
    assert isinstance(asked["legacy-exec"]["params"]["command"], list)
    assert asked["legacy-patch"]["params"]["fileChanges"]


OUT: list[dict[str, Any]] = [
    {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "initialize",
        "params": {"clientInfo": {"name": "udl", "version": "1"}},
    },
    {
        "jsonrpc": "2.0",
        "id": "2",
        "method": "thread/start",
        "params": {"cwd": str(ROOT), "model": None, "approvalPolicy": "on-request", "config": {}},
    },
    {
        "jsonrpc": "2.0",
        "id": "3",
        "method": "turn/start",
        "params": {"threadId": "thread-1", "input": [{"type": "text", "text": "go"}]},
    },
    {"jsonrpc": "2.0", "id": 0, "result": {"decision": "accept"}},
    {"jsonrpc": "2.0", "id": 1, "result": {"decision": "acceptForSession"}},
    {"jsonrpc": "2.0", "id": 2, "result": {"decision": "decline"}},
]
"""What udl writes: its three requests, and its three answers to an approval (v2's words)."""

OLD_OUT: list[dict[str, Any]] = [
    {"jsonrpc": "2.0", "id": 0, "result": {"decision": "approved"}},
    {"jsonrpc": "2.0", "id": 1, "result": {"decision": "approved_for_session"}},
    {"jsonrpc": "2.0", "id": 2, "result": {"decision": {"denied": {"rejection": "no"}}}},
]
"""The same, in the older vocabulary (`ReviewDecision`)."""


def test_udls_own_messages_are_the_protocol(tmp_path: Path) -> None:
    """(What the driver *actually writes*: the fake keeps every answer udl sent, and each is
    validated against the response its own request names — v2's and the older vocabulary.)"""
    client = V2["definitions"]["ClientRequest"]["oneOf"]
    for message in OUT:
        method = message.get("method")
        if method is None:
            continue
        check(V2, "ClientRequest", message)
        assert method in [one["properties"]["method"]["enum"][0] for one in client]
    for mode, name in (
        ("command", "CommandExecutionRequestApprovalResponse"),
        ("file", "FileChangeRequestApprovalResponse"),
        ("legacy-exec", "ExecCommandApprovalResponse"),
        ("legacy-patch", "ApplyPatchApprovalResponse"),
    ):
        kept = tmp_path / f"{mode}.jsonl"
        process = subprocess.Popen(  # noqa: S603 - the fake is the program under test
            [sys.executable, str(FAKE), mode, str(kept)],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert process.stdin is not None
        for request in (START, THREAD, TURN, ANSWER):
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
        process.stdin.close()
        process.communicate(timeout=30)
        assert process.returncode == 0, f"{mode} exited {process.returncode}"

        for line in kept.read_text().splitlines():
            check(V1, name, _OBJECT.validate_python(json.loads(line).get("result")))


def test_the_schema_has_the_requests_the_driver_writes() -> None:
    """(What udl sends: `initialize`, `thread/start` and `turn/start`, as the client's schema
    has them — the other half of the same check.)"""
    client = V2["definitions"]["ClientRequest"]["oneOf"]

    for method in ("initialize", "thread/start", "turn/start"):
        found = [entry for entry in client if entry["properties"]["method"]["enum"] == [method]]
        assert found, f"the schema has no {method}"
        assert found[0]["required"] == ["id", "method", "params"]
