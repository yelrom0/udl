#!/usr/bin/env python3
"""
-------------------
- codex_harness.py -
-------------------

A `codex` in a file: what the codex runner's tests point their binary at (the twin of
fake_harness.py for claude-code), so a turn runs a program and its JSON lines are parsed without
a model, an install or a sign-in.

`CODEX_HARNESS_SIGNED_OUT=1` makes `login status` say `Not logged in`; `CODEX_HARNESS_KEY_REFUSED=1`
makes `login --with-api-key` fail as a refused key does.

It answers the way Codex 0.171 does:
- `--version` prints `codex-cli 0.171.0`.
- `login status` prints what it prints (`Logged in using ChatGPT`, or `Not logged in`).
- `exec --json … -` reads the prompt on stdin and prints the recorded events
  (`thread.started`, an `item.completed` of type `error` — the "Model metadata … not found"
  warning, `turn.started`, an `agent_message`, `turn.completed` with its `usage`), and
- `exec resume <id> --json -` prints the resumed session's events.

`CODEX_HARNESS_LIMITED=1` (or the contract's own `FAKE_HARNESS_LIMITED=1`) makes the turn fail
with a usage limit that says when it lifts; `CODEX_HARNESS_FAILS=1` makes it fail;
`CODEX_HARNESS_SLOW=1` (or `FAKE_HARNESS_SLOW=1`) makes it wait before it answers, so a runner's
stop can end it. Its argv and environment are appended to `$CODEX_HARNESS_LOG` when it
is set, so a test can read what the runner passed (a secret must be in the environment, never in
an argv).
"""

# ruff: noqa: T201  (a program's output is the point: the runner's tests read it)

# System Imports
import json
import os
import sys
import time
import uuid
from pathlib import Path

VERSION = "codex-cli 0.171.0"
"""What `--version` prints."""
RESETS_AT = 1_800_000_000
"""When the limit lifts (2027-01-15T08:00:00Z), as a limit's message says it."""
SLOW = 30.0
"""How long `CODEX_HARNESS_SLOW=1` waits (long enough that a test's stop comes first)."""
WARNING = (
    "Model metadata for `fake-model` not found. Defaulting to fallback metadata; "
    "this can degrade performance and cause issues."
)
"""The item a real turn's stream opens with (an `error` item that is only a warning)."""


def main(argv: list[str]) -> int:
    """Answer the way codex does; 0 when it worked, 1 when it was told to fail."""
    _logged(argv)
    if "--version" in argv or "-V" in argv:
        print(VERSION)
        return 0
    if argv[1:3] == ["login", "--with-api-key"]:
        if _flag("KEY_REFUSED"):
            print("Incorrect API key provided", file=sys.stderr)
            return 1
        sys.stdin.read()
        return 0
    if argv[1:2] == ["login"]:
        if "status" in argv:
            print("Not logged in" if _flag("SIGNED_OUT") else "Logged in using ChatGPT")
        return 0
    return turn(resume=_resumed(argv))


def _resumed(argv: list[str]) -> str | None:
    """The session id a resumed turn names (`exec resume <id>`), if it is one."""
    if "resume" not in argv:
        return None
    after = argv[argv.index("resume") + 1 :]
    return next((part for part in after if not part.startswith("-")), None)


def turn(*, resume: str | None = None) -> int:
    """One turn: the prompt on stdin, the events on stdout, one JSON line each."""
    prompt = sys.stdin.read().strip()
    if _flag("SLOW"):
        time.sleep(SLOW)
    thread = resume or str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": thread}), flush=True)
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "error", "message": WARNING},
            }
        ),
        flush=True,
    )
    print(json.dumps({"type": "turn.started"}), flush=True)
    if _flag("LIMITED"):
        print(
            json.dumps(
                {
                    "type": "error",
                    "message": f"You've hit your usage limit. Try again after {RESETS_AT}.",
                }
            ),
            flush=True,
        )
        return 1
    if _flag("FAILS"):
        print(json.dumps({"type": "error", "message": "the fake failed"}), flush=True)
        return 1
    said = f"resumed: {prompt}" if resume else prompt
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_1", "type": "agent_message", "text": said},
            }
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 6437,
                    "cached_input_tokens": 0,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 22,
                    "reasoning_output_tokens": 0,
                },
            }
        ),
        flush=True,
    )
    return 0


def _flag(what: str) -> bool:
    """Whether a test asked for `what` (the harness's own name, or the contract's)."""
    return (
        os.environ.get(f"CODEX_HARNESS_{what}") == "1"
        or os.environ.get(f"FAKE_HARNESS_{what}") == "1"
    )


def _logged(argv: list[str]) -> None:
    """Append this call's argv and environment to `$CODEX_HARNESS_LOG`, when it is set."""
    logged = os.environ.get("CODEX_HARNESS_LOG")
    if not logged:
        return
    with Path(logged).open("a", encoding="utf-8") as out:
        out.write(json.dumps({"argv": argv, "env": dict(os.environ)}) + "\n")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
