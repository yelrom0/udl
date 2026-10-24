"""
--------------
- actions.py -
--------------

FakeAction: an action plugin that does what its options say (succeed with an output, fail,
fail retryably, wait until it is cancelled, hang, or fail as a bug would) and records each run
it is given, for the runner's and the bus's tests; and Chores, a plugin whose operations wait,
fail retryably or say who called, for operation actions to call; and a fake notify-send
"""

# System Imports
import asyncio
from collections.abc import Generator
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

# Package Imports
import pytest
from pydantic import BaseModel, ConfigDict, Field, JsonValue

# Local Imports
from support.interfaces import Harness
from udl.core.operations import Operation, OperationHints, current_caller
from udl.errors import Cancelled, NetworkError
from udl.plugins.actions.base import (
    ActionContext,
    ActionFailed,
    ActionOptions,
    ActionOutput,
    ActionPlugin,
)
from udl.plugins.registry import SITES
from udl.plugins.sites.base import SitePlugin

if TYPE_CHECKING:
    from udl.core.service import UDLService


class Outcome(StrEnum):
    SUCCEED = "succeed"
    FAIL = "fail"
    RETRYABLE = "retryable"  # (fails, and trying again may help)
    WAIT = "wait"  # (until it is cancelled)
    HANG = "hang"  # (ignoring its cancel token, so only its task's cancelling stops it)
    BUG = "bug"  # (raises what an action never should)


class FakeActionOptions(ActionOptions):
    outcome: Outcome = Outcome.SUCCEED
    """What the run does."""
    output: JsonValue = None
    """What it gives when it succeeds."""
    summary: str = ""
    """Its summary."""
    message: str = "It failed"
    """Why it fails."""
    retry_after: float | None = None
    """When it fails retryably: seconds to wait before trying again."""
    log: list[str] = Field(default_factory=list[str])
    """Lines it logs."""


class FakeAction(ActionPlugin[FakeActionOptions]):
    """Does what its options say. `ran` is every run it was given (its subclasses' own)."""

    name = "fake"
    options_model = FakeActionOptions
    summary = "Does what its options say (for tests)."
    ran: ClassVar[list[ActionContext[FakeActionOptions]]] = []

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        cls.ran = []

    async def run(self, ctx: ActionContext[FakeActionOptions]) -> ActionOutput:
        type(self).ran.append(ctx)
        options = ctx.options
        for line in options.log:
            ctx.log.write(line)
        match options.outcome:
            case Outcome.SUCCEED:
                return ActionOutput(output=options.output, summary=options.summary)
            case Outcome.FAIL:
                raise ActionFailed(options.message)
            case Outcome.RETRYABLE:
                raise ActionFailed(options.message, retryable=True, retry_after=options.retry_after)
            case Outcome.WAIT:
                # (A cancel token is a threading.Event, for downloaders' threads: polled.)
                while not ctx.cancel.cancelled:  # noqa: ASYNC110
                    await asyncio.sleep(0.005)
                raise Cancelled("The run was cancelled")
            case Outcome.HANG:
                await asyncio.Event().wait()
                raise AssertionError  # (never: it waits for ever)
            case Outcome.BUG:
                raise RuntimeError("a bug in the action")


def fake_action(called: str = "fake") -> type[FakeAction]:
    """A FakeAction called `called`, with runs of its own."""

    class Fake(FakeAction):
        name = called

    Fake.__qualname__ = Fake.__name__ = f"FakeAction[{called}]"
    return Fake


# ---------------------------------------------------------------------------------------------
# Operations for operation actions to call
# ---------------------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", use_attribute_docstrings=True)


class ChoreRequest(_Model):
    """A chore."""

    what: str = "nothing"
    """What to do."""


class ChoreDone(_Model):
    what: str
    caller: str | None
    """Who called (cli, api, mcp, automation), or None from Python."""


async def wait(service: UDLService, request: ChoreRequest) -> ChoreDone:
    """Wait until cancelled."""
    await asyncio.Event().wait()
    raise AssertionError  # (never: it waits for ever)


async def flaky(service: UDLService, request: ChoreRequest) -> ChoreDone:
    """Fail as a dropped connection does."""
    raise NetworkError("The connection was reset")


async def broken(service: UDLService, request: ChoreRequest) -> ChoreDone:
    """Fail as a bug does."""
    raise RuntimeError("a bug in the operation")


async def do(service: UDLService, request: ChoreRequest) -> ChoreDone:
    """Do a chore, saying who asked."""
    caller = current_caller.get()
    return ChoreDone(what=request.what, caller=None if caller is None else caller.value)


class Chores(SitePlugin):
    """A site plugin (for no sites) whose operations wait, fail or say who called."""

    name = "chores"

    @classmethod
    def operations(cls) -> list[Operation]:
        return [
            Operation.of(wait, name="wait", title="Wait", hints=OperationHints(read_only=True)),
            Operation.of(flaky, name="flaky", title="Fail", hints=OperationHints(read_only=True)),
            Operation.of(do, name="do", title="Do a chore", hints=OperationHints(read_only=True)),
            Operation.of(
                broken, name="broken", title="Break", hints=OperationHints(read_only=True)
            ),
        ]


def with_chores(harness: Harness) -> Harness:
    """`harness`, with the Chores plugin."""
    harness.registry.register(SITES, Chores)
    return harness


# ---------------------------------------------------------------------------------------------
# A notify-send
# ---------------------------------------------------------------------------------------------


# A notify-send that writes its arguments (one a line) to $FAKE_NOTIFY_LOG, fails for the title
# "fail" and waits for the title "wait".
FAKE_NOTIFY_SEND = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE_NOTIFY_LOG"
while [ "$1" != "--" ]; do shift; done
case "$2" in
    fail) echo "Cannot connect to the notification server" >&2; exit 1 ;;
    wait) exec sleep 30 ;;
esac
"""


@contextmanager
def fake_notify_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """A fake notify-send on PATH, which logs its arguments to tmp_path / "notified"."""
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    program = folder / "notify-send"
    program.write_text(FAKE_NOTIFY_SEND)
    program.chmod(0o755)
    monkeypatch.setenv("PATH", f"{folder}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE_NOTIFY_LOG", str(tmp_path / "notified"))
    yield
