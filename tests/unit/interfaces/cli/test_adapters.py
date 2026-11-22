"""
--------------------
- test_adapters.py -
--------------------

The CLI the adapters build for operations udl's own code hands them (`add_commands`), for what
the real app's tests do not reach: an operation whose CLISpec path is a command group's own, and a
group the caller already made.
"""

# System Imports
import inspect
from pathlib import Path

# Package Imports
import typer
import typer.core
import typer.main
from rich.console import Console
from typer.testing import CliRunner

# Local Imports
from support.engine import settings
from support.interfaces import Harness
from support.operations import Things, every_thing, find
from udl.core.operations import CLISpec, Operation
from udl.interfaces.adapters import add_commands
from udl.interfaces.cli.common import Runtime, operation_of


def every() -> Operation:
    """The operation whose path is the `things` group's own: `udl gizmo things`."""
    return Operation.of(
        every_thing,
        name="every_thing",
        title="Every thing",
        cli=CLISpec(path=("gizmo", "things"), options={"count": "--count"}),
    )


def finding() -> Operation:
    """The operation under that group: `udl gizmo things find`."""
    return Operation.of(
        find,
        name="find",
        title="Find a thing",
        cli=CLISpec(path=("gizmo", "things", "find"), arguments=("key", "words")),
    )


def group_of(app: typer.Typer, *path: str) -> typer.core.TyperGroup:
    """The group at `path` in `app`, as a person's `udl` sees it."""
    found: object = typer.main.get_group(app)
    for word in path:
        assert isinstance(found, typer.core.TyperGroup)
        found = found.commands[word]
    assert isinstance(found, typer.core.TyperGroup)
    return found


def callback_name(group: typer.core.TyperGroup) -> str | None:
    """The operation a group's own command runs, if it has one."""
    return operation_of(inspect.unwrap(group.callback or print))


def test_an_operation_at_a_group_s_own_path_is_the_group_s_command() -> None:
    app = typer.Typer()

    add_commands(app, [every(), finding()], renderers={}, helps={})

    things = group_of(app, "gizmo", "things")
    assert things.invoke_without_command is True
    assert things.no_args_is_help is False
    assert callback_name(things) == "every_thing"
    assert operation_of(inspect.unwrap(things.commands["find"].callback or print)) == "find"


def test_a_group_the_caller_made_gets_its_commands_and_no_callback() -> None:
    app = typer.Typer()
    made = typer.Typer(help="Things.")
    app.add_typer(made, name="gizmo")

    add_commands(app, [finding()], renderers={}, helps={}, groups={("gizmo",): made})

    things = group_of(app, "gizmo", "things")
    assert things.invoke_without_command is False
    assert things.no_args_is_help is True
    assert callback_name(things) is None
    assert operation_of(inspect.unwrap(things.commands["find"].callback or print)) == "find"


def watching() -> Operation:
    """The operation whose command follows it: `udl gizmo follow --watch`."""
    return Operation.of(
        every_thing,
        name="every_thing",
        title="Every thing",
        cli=CLISpec(path=("gizmo", "follow"), watch=watched),
    )


_watched: list[tuple[str, str]] = []


async def watched(console: Console, service: object, request: Things) -> None:
    """(What a command's watch does: the inbox redraws on events, so a test records the call.)"""
    _watched.append(("watched", str(request.count)))


def test_a_command_that_follows_takes_watch_and_runs_its_watcher(tmp_path: Path) -> None:
    # (The flag is the CLI's own: it is not a field of the request, and the API and MCP never see
    # it — a command that follows is what a person reads, not what an agent calls.)
    app = typer.Typer()
    add_commands(app, [watching()], renderers={}, helps={})
    # (The watcher is given a real service — the harness's — and records the request it was
    # given; a watch is the CLI's own, so it never reaches the operation.)
    harness = Harness(tmp_path)
    runtime = Runtime(settings=settings(tmp_path), make_service=harness.service, verbosity=0)

    result = CliRunner().invoke(app, ["gizmo", "follow", "--watch"], obj=runtime)

    assert result.exit_code == 0, result.output
    assert _watched == [("watched", "2")]  # (the request's own count)


def test_a_command_that_does_not_follow_has_no_watch() -> None:
    app = typer.Typer()
    add_commands(app, [finding()], renderers={}, helps={})

    result = CliRunner().invoke(app, ["gizmo", "things", "find", "--watch"])

    assert result.exit_code == 2  # (a usage error: there is no such option)
    assert "No such option" in result.output
