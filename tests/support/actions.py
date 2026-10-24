"""
--------------
- actions.py -
--------------

FakeAction: an action plugin that does what its options say (succeed with an output, fail,
fail retryably, wait until it is cancelled, hang, or fail as a bug would) and records each run
it is given, for the runner's and the bus's tests
"""

# System Imports
import asyncio
from enum import StrEnum
from typing import ClassVar

# Package Imports
from pydantic import Field, JsonValue

# Local Imports
from udl.errors import Cancelled
from udl.plugins.actions.base import (
    ActionContext,
    ActionFailed,
    ActionOptions,
    ActionOutput,
    ActionPlugin,
)


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
