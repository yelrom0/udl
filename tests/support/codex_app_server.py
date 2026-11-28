"""
-------------------------
- codex_app_server.py -
-------------------------

A fake `codex app-server` (27.11's Codex half): the JSON-RPC protocol over stdio, as the installed
Codex 0.171 speaks it, for udl's tests to drive a chat without OpenAI's CLI.

It is a program of its own (a test points the codex runner's `cli` at it), so what is tested is
the real thing: a process, lines in, lines out. What it does is what `argv[1]` says:

- `turn` — a turn that says one line and ends;
- `command` — a turn that asks to run a command (v2's `item/commandExecution/requestApproval`,
  whose `command` is a string), and says what the answer was;
- `file` — a turn that asks to change a file (v2's `item/fileChange/requestApproval`);
- `legacy-exec` and `legacy-patch` — the older `execCommandApproval` and `applyPatchApproval`,
  answered in `ReviewDecision`'s words;
- `retry` — an error Codex will retry, then a clean turn;
- `failed` and `interrupted` — a turn that failed (with its `TurnError`) and one interrupted;
- `no-server` — `app-server --help` fails, as a Codex without it does.

Every message it sends is validated against `tests/fixtures/codex-app-server/` by a test, so the
fake cannot drift from the schemas the driver reads.
"""

# System Imports
import json
import sys
from pathlib import Path
from typing import Any

# (The JSON shapes, without udl's own imports: this is run as a program by a test.)
type Json = dict[str, Any]

THREAD = "thread-1"
"""The thread the fake has open."""

TURN = "turn-1"
"""The turn it is in."""

APPROVALS: dict[str, str] = {
    "command": "item/commandExecution/requestApproval",
    "file": "item/fileChange/requestApproval",
    "legacy-exec": "execCommandApproval",
    "legacy-patch": "applyPatchApproval",
}
"""Each mode's approval request, and the method it is sent as."""


def send(message: Json) -> None:
    """One line to the client, as the protocol says (one JSON object, one line)."""
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def answer(asked: Json, result: Json) -> None:
    """Answer a request, keeping its id."""
    send({"jsonrpc": "2.0", "id": asked.get("id"), "result": result})


def notification(method: str, params: Json) -> None:
    """Tell the client something happened."""
    send({"jsonrpc": "2.0", "method": method, "params": params})


def say(text: str) -> None:
    """The harness's answer, as the deltas it comes in, the usage, and the turn's end."""
    notification(
        "item/agentMessage/delta",
        {"delta": text, "itemId": "item-agent", "threadId": THREAD, "turnId": TURN},
    )
    notification(
        "thread/tokenUsage/updated",
        {
            "threadId": THREAD,
            "turnId": TURN,
            "tokenUsage": {
                "total": _counts(10, 5),
                "last": _counts(4, 2),
            },
        },
    )
    notification(
        "turn/completed",
        {"threadId": THREAD, "turn": {"id": TURN, "status": "completed", "items": []}},
    )


def _counts(asked: int, said: int) -> Json:
    """One `TokenUsageBreakdown` (the schema's own fields)."""
    return {
        "cachedInputTokens": 0,
        "inputTokens": asked,
        "outputTokens": said,
        "reasoningOutputTokens": 0,
        "totalTokens": asked + said,
    }


def _approval(mode: str) -> Json:
    """What the mode's approval asks, in the shape its own schema has."""
    if mode == "command":  # (v2: the command is a string, and the item names it)
        return {
            "itemId": "item-command",
            "threadId": THREAD,
            "turnId": TURN,
            "startedAtMs": 1,
            "command": "git status",
        }
    if mode == "file":
        return {
            "itemId": "item-file",
            "threadId": THREAD,
            "turnId": TURN,
            "startedAtMs": 1,
            "grantRoot": None,
            "reason": "it needs to write a file",
        }
    if mode == "legacy-exec":
        return {
            "callId": "call-1",
            "conversationId": THREAD,
            "cwd": str(Path(__file__).parent),
            "command": ["git", "status"],
            "parsedCmd": [],
            "approvalId": None,
            "reason": None,
        }
    return {
        "callId": "call-1",
        "conversationId": THREAD,
        "fileChanges": {"a.txt": {"type": "update", "unified_diff": "@@ one line @@"}},
        "grantRoot": None,
        "reason": None,
    }


def main(argv: list[str]) -> int:
    """Read requests until the client closes its end, answering each as the mode says."""
    mode = argv[1] if len(argv) > 1 else "turn"
    if mode == "no-server":
        sys.stderr.write("this codex has no app-server\n")
        return 1
    for line in sys.stdin:
        if not line.strip():
            continue
        message: Json = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            answer(message, {"userAgent": "codex-cli 0.171.0"})
        elif method == "thread/start":
            answer(message, {"thread": {"id": THREAD, "cliVersion": "0.171.0"}})
        elif method == "turn/start":
            answer(message, {"turn": {"id": TURN, "status": "inProgress"}})
            if mode == "file":
                notification(
                    "item/started",
                    {
                        "threadId": THREAD,
                        "turnId": TURN,
                        "startedAtMs": 1,
                        "item": {
                            "id": "item-file",
                            "type": "fileChange",
                            "changes": [
                                {"path": "a.txt", "kind": {"type": "update"}, "diff": "one line"}
                            ],
                            "status": "inProgress",
                        },
                    },
                )
            if mode in APPROVALS:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": 0,
                        "method": APPROVALS[mode],
                        "params": _approval(mode),
                    }
                )
            elif mode == "retry":
                notification(
                    "error",
                    {
                        "threadId": THREAD,
                        "turnId": TURN,
                        "willRetry": True,
                        "error": {"message": "the model was busy"},
                    },
                )
                say("hello from codex")
            elif mode == "failed":
                notification(
                    "turn/completed",
                    {
                        "threadId": THREAD,
                        "turn": {
                            "id": TURN,
                            "status": "failed",
                            "error": {"message": "it broke"},
                            "items": [],
                        },
                    },
                )
            elif mode == "interrupted":
                notification(
                    "turn/completed",
                    {
                        "threadId": THREAD,
                        "turn": {"id": TURN, "status": "interrupted", "items": []},
                    },
                )
            else:
                say("hello from codex")
        elif message.get("id") == 0:
            answered: Json | None = message.get("result")
            said = answered or {}
            say(f"it was {said.get('decision')}")
        else:
            answer(message, {})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
