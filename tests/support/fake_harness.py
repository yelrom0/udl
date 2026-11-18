#!/usr/bin/env python3
"""
------------------
- fake_harness.py -
------------------

A harness in a file: what the real runners' tests (25.8's `claude`, 25.9's `opencode`, 25.10's
`command`) point their binary at, so they run a program and parse its output without a model, an
install or a sign-in.

It answers the way the harnesses do:
- `--version` prints a version, as `claude --version` does.
- `auth status` prints the JSON `claude auth status` prints.
- a turn reads its prompt on stdin and prints JSON lines on stdout: `system`, `assistant` and
  `result` (the stream-json shape), one per line, with the prompt echoed back.

`FAKE_HARNESS_FAILS=1` makes a turn exit 1 after its first line, and `FAKE_HARNESS_LIMITED=1` makes
it print a usage-limit result, so a runner's error paths have something to parse.
"""

# ruff: noqa: T201  (a program's output is the point: the runners' tests read it)

# System Imports
import json
import os
import sys

VERSION = "fake-harness 1.0.0"
"""What `--version` prints."""


def main(argv: list[str]) -> int:
    """Answer the way a harness does; 0 when it worked, 1 when it was told to fail."""
    if "--version" in argv or "-v" in argv:
        print(VERSION)
        return 0
    if argv[1:2] == ["auth"]:
        print(
            json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "fake",
                    "subscriptionType": "fake",
                    "email": "fake@example.invalid",
                }
            )
        )
        return 0
    return turn()


def turn() -> int:
    """One turn: the prompt on stdin, the events on stdout, one JSON line each."""
    prompt = sys.stdin.read().strip()
    print(json.dumps({"type": "system", "subtype": "init", "model": "fake-model"}))
    if os.environ.get("FAKE_HARNESS_FAILS") == "1":
        print(json.dumps({"type": "result", "subtype": "error", "is_error": True}), flush=True)
        return 1
    if os.environ.get("FAKE_HARNESS_LIMITED") == "1":
        print(json.dumps({"type": "result", "subtype": "error", "is_error": True, "limited": True}))
        return 0
    print(
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": f"You said: {prompt}"}]},
            }
        )
    )
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"You said: {prompt}",
                "usage": {"input_tokens": 3, "output_tokens": 5},
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
