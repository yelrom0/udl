"""
-------------------------
- codex_app_server.py -
-------------------------

A fake `codex app-server` (27.11's Codex half): the JSON-RPC protocol over stdio, as the installed
Codex 0.171 speaks it, for udl's tests to drive a chat without OpenAI's CLI.

It is a program of its own (a test points the codex runner's `cli` at it), so what is tested is
the real thing: a process, lines in, lines out. What it does is what `argv[1]` says:

- `turn` — a turn that says one line and ends;
- `approval` — a turn that asks to run a command first, and says what the answer was;
- `resume` — a thread that is already open, so a second turn resumes it;
- `no-server` — `app-server --help` fails, as a Codex without it does.

The scenario is on stdin's side: udl writes a request, the fake answers, and the notifications
come back as lines.
"""

# System Imports
import json
import sys
from pathlib import Path
from typing import Any

THREAD = "thread-1"
"""The thread the fake has open."""

APPROVAL = "item/commandExecution/requestApproval"
"""What it asks when a turn wants to run something."""


def send(message: dict[str, Any]) -> None:
    """One line to the client, as the protocol says (one JSON object, one line)."""
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def answer(asked: dict[str, Any], result: dict[str, Any]) -> None:
    """Answer a request, keeping its id."""
    send({"jsonrpc": "2.0", "id": asked.get("id"), "result": result})


def notification(method: str, params: dict[str, Any]) -> None:
    """Tell the client something happened."""
    send({"jsonrpc": "2.0", "method": method, "params": params})


def item(kind: str, **fields: Any) -> dict[str, Any]:
    """One thread item, as the notifications carry it."""
    return {"id": f"item-{kind}", "type": kind, **fields}


def say(text: str) -> None:
    """The harness's answer, as the deltas it comes in, and the turn's end."""
    notification(
        "item/agentMessage/delta",
        {"delta": text, "itemId": "item-agent", "threadId": THREAD, "turnId": "turn-1"},
    )
    notification(
        "turn/completed",
        {"threadId": THREAD, "turn": {"id": "turn-1", "items": [], "completedAt": 1}},
    )


def main(argv: list[str]) -> int:
    """Read requests until the client closes its end, answering each as the scenario says."""
    if len(argv) > 1 and argv[1] == "no-server":
        sys.stderr.write("this codex has no app-server\n")
        return 1
    asked: dict[str, Any] | None = None
    for line in sys.stdin:
        if not line.strip():
            continue
        message: dict[str, Any] = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            answer(message, {"userAgent": "codex-cli 0.171.0"})
        elif method == "thread/start":
            answer(message, {"thread": {"id": THREAD, "cliVersion": "0.171.0"}})
        elif method == "turn/start":
            asked = message
            if len(argv) > 1 and argv[1] == "approval":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": "approval-1",
                        "method": APPROVAL,
                        "params": {
                            "callId": "call-1",
                            "command": ["git", "status"],
                            "cwd": str(Path(__file__).parent),
                            "parsedCmd": [],
                            "conversationId": THREAD,
                        },
                    }
                )
            else:
                answer(message, {"turn": {"id": "turn-1"}})
                say("hello from codex")
        elif message.get("id") == "approval-1":
            answered: dict[str, Any] = message.get("result") or {}
            decision = answered.get("decision")
            answer(asked or {"id": "turn-1"}, {"turn": {"id": "turn-1"}})
            say(f"it was {decision}")
        else:
            answer(message, {})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
