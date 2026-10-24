"""
-------------
- script.py -
-------------

The script action: runs a program from [automations] scripts_dir, and nothing else. Its command
is resolved (following links and `..`), so it cannot leave scripts_dir, and it never runs
through a shell: each argument is one argument (a template of its own), whatever an event's
text holds. The event comes as JSON on its stdin (or nothing, or a text the action gives) and in
a file only its owner reads (UDL_EVENT_FILE, removed afterwards).

Its environment is the action's `env`, PATH, HOME and LANG, and udl's UDL_* variables for it,
never udl's own environment (which may hold secrets). Its stdout is the run's output (JSON when
it is JSON), of which the first 64 KiB are kept; its stderr is the run's log, and its last lines
say why it failed when it exits with anything but 0.
"""

# System Imports
import contextlib
import json
import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any, Final

# Package Imports
from pydantic import Field, JsonValue

# Local Imports
from udl.automations.events import EventType
from udl.db.models import JsonObject
from udl.errors import ActionFailed, ConfigError, NotAllowed, ToolUnavailable, UDLError
from udl.plugins.actions.base import ActionContext, ActionOptions, ActionOutput, ActionPlugin
from udl.process import run_process

MAX_OUTPUT: Final = 64 * 1024
"""Bytes of a program's stdout (its first) and stderr (its last) that are kept."""
_REASON: Final = 1000  # characters of stderr's end that say why a program failed
_SUMMARY: Final = 200
_FROM_UDL: Final = ("PATH", "HOME", "LANG")  # what a program gets of udl's own environment
_TEMPLATE_MARKS: Final = ("{{", "{%")


class ScriptOptions(ActionOptions):
    command: list[str] = Field(min_length=1)
    """The program (its path in scripts_dir) and its arguments; each is a template of its own,
    and one argument whatever it holds (there is no shell)."""
    cwd: str | None = None
    """The folder it runs in (by default scripts_dir)."""
    env: dict[str, str] = Field(default_factory=dict[str, str])
    """Environment variables to give it (besides PATH, HOME, LANG and udl's UDL_* ones)."""
    stdin: str = "event"
    """What it reads: event (the event, as JSON), none, or any other text (a template)."""


class ScriptAction(ActionPlugin[ScriptOptions]):
    """Runs a program from scripts_dir."""

    name = "script"
    options_model = ScriptOptions
    summary = (
        "Runs a program from scripts_dir (never through a shell), with the event as JSON on its "
        "stdin; what it prints is the run's output."
    )

    async def check(self, options: JsonObject, event_type: EventType[Any] | None) -> None:
        await super().check(options, event_type)
        program = options.get("command", [None])
        named = program[0] if isinstance(program, list) and program else None
        if isinstance(named, str) and not any(mark in named for mark in _TEMPLATE_MARKS):
            self.program(named)

    def program(self, named: str) -> Path:
        """The program `named` is: an executable file in scripts_dir. Raises NotAllowed for
        one outside it, and ConfigError for one that is not there or cannot run."""
        scripts = self.service.settings.automations.scripts_dir.expanduser().resolve()
        found = (scripts / Path(named).expanduser()).resolve()
        if not found.is_relative_to(scripts):
            raise NotAllowed(
                f"{named} is not in scripts_dir ({scripts}): a script action runs programs "
                "from there only",
                hint="Put the program in scripts_dir ([automations] scripts_dir in the config).",
            )
        if not found.is_file():
            raise ConfigError(
                f"There is no program {named} in scripts_dir ({scripts})",
                hint="`udl actions scripts` lists the programs there.",
            )
        if not os.access(found, os.X_OK):
            raise ConfigError(
                f"{named} is not executable",
                hint=f"Make it so: chmod +x {found}",
            )
        return found

    async def run(self, ctx: ActionContext[ScriptOptions]) -> ActionOutput:
        options = ctx.options
        named = options.command[0]
        try:
            program = self.program(named)
        except UDLError as e:
            raise ActionFailed(e.message, hint=e.hint) from e
        event = ctx.event.model_dump_json() if ctx.event is not None else "null"
        match options.stdin:
            case "event":
                stdin: bytes | None = event.encode()
            case "none":
                stdin = None
            case text:
                stdin = text.encode()
        settings = self.service.settings
        cwd = _folder(options.cwd) if options.cwd else settings.automations.scripts_dir
        with _event_file(event) as event_file:
            env = {
                **options.env,
                **{name: os.environ[name] for name in _FROM_UDL if name in os.environ},
                "UDL_EVENT_FILE": str(event_file),
                "UDL_RUN_ID": str(ctx.run_id),
                "UDL_ACTION": ctx.action,
            }
            if ctx.event is not None:
                env |= {"UDL_EVENT_ID": str(ctx.event.id), "UDL_EVENT_TYPE": ctx.event.type}
            if settings.config_file is not None:
                env["UDL_CONFIG"] = str(settings.config_file)
            try:
                result = await run_process(
                    [str(program), *options.command[1:]],
                    cancel=ctx.cancel,
                    stdin=stdin,
                    env=env,
                    cwd=cwd,
                    max_output=MAX_OUTPUT,
                    cancelled="The run was cancelled",
                )
            except ToolUnavailable as e:
                raise ActionFailed(f"{named} cannot be run: {e.message}") from e
            except OSError as e:
                raise ActionFailed(f"{named} cannot be run: {e}") from e
        if result.stderr:
            ctx.log.write(result.stderr)
        if result.truncated:
            ctx.log.write(
                f"[{named}'s output was more than {MAX_OUTPUT} bytes: the rest is left out]"
            )
        if result.returncode != 0:
            reason = result.stderr.strip()[-_REASON:].strip()
            raise ActionFailed(
                f"{named} exited with {result.returncode}" + (f": {reason}" if reason else "")
            )
        return _output(result.stdout)


def _folder(named: str) -> Path:
    return Path(named).expanduser()


@contextlib.contextmanager
def _event_file(event: str) -> Generator[Path]:
    """A file holding `event` that only its owner reads, removed afterwards."""
    handle, name = tempfile.mkstemp(prefix="udl-event-", suffix=".json")
    path = Path(name)
    try:
        with os.fdopen(handle, "w") as file:
            file.write(event)
        yield path
    finally:
        path.unlink(missing_ok=True)


def _output(stdout: str) -> ActionOutput:
    """A program's stdout as a run's output: its JSON, else its text."""
    try:
        found: JsonValue = json.loads(stdout)
    except ValueError:
        first = next((line.strip() for line in stdout.splitlines() if line.strip()), "")
        return ActionOutput(output=stdout, summary=first[:_SUMMARY])
    summary = found.get("summary") if isinstance(found, dict) else None
    return ActionOutput(output=found, summary=summary if isinstance(summary, str) else "")
