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

# Package Imports
import typer
import typer.core
import typer.main

# Local Imports
from support.operations import every_thing, find
from udl.core.operations import CLISpec, Operation
from udl.interfaces.adapters import add_commands
from udl.interfaces.cli.common import operation_of


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
