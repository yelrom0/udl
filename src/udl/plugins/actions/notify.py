"""
-------------
- notify.py -
-------------

The notify action: a desktop notification, through notify-send (libnotify). Its title and body
come after `--`, so whatever an event's text holds they are text to notify-send, never its
options.
"""

# System Imports
from enum import StrEnum
from typing import Final

# Package Imports
from pydantic import Field

# Local Imports
from udl.errors import ActionFailed, ToolUnavailable
from udl.plugins.actions.base import ActionContext, ActionOptions, ActionOutput, ActionPlugin
from udl.plugins.requirements import Requirement
from udl.process import run_process

_PROGRAM: Final = "notify-send"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    CRITICAL = "critical"


class NotifyOptions(ActionOptions):
    title: str = Field(min_length=1)
    """The notification's title (a template)."""
    body: str = ""
    """Its text (a template)."""
    urgency: Urgency = Urgency.NORMAL
    """low, normal or critical."""


class NotifyAction(ActionPlugin[NotifyOptions]):
    """Shows a desktop notification."""

    name = "notify"
    options_model = NotifyOptions
    requires = (
        Requirement.binary(
            _PROGRAM,
            hint="Install libnotify (e.g. `pacman -S libnotify`, `apt install libnotify-bin`).",
        ),
    )
    summary = "Shows a desktop notification (notify-send)."

    async def run(self, ctx: ActionContext[NotifyOptions]) -> ActionOutput:
        options = ctx.options
        args = [
            _PROGRAM,
            "--app-name=udl",
            f"--urgency={options.urgency}",
            "--",
            options.title,
            options.body,
        ]
        try:
            result = await run_process(args, cancel=ctx.cancel, cancelled="The run was cancelled")
        except ToolUnavailable as e:
            raise ActionFailed(e.message, hint=self.requires[0].hint) from e
        if result.returncode != 0:
            reason = result.stderr.strip() or f"it exited with {result.returncode}"
            raise ActionFailed(f"{_PROGRAM} failed: {reason}")
        return ActionOutput(summary=options.title)
