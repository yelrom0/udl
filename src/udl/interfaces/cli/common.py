"""
-------------
- common.py -
-------------

What udl's commands share: the settings udl started with and the services they run operations
on, turning udl's errors into a message and an exit code (0 all OK, 1 something failed, 2 a usage
error), and showing responses (rendered for people, or as JSON with --json)
"""

# System Imports
import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any, NoReturn, Self

# Package Imports
import typer
from pydantic import BaseModel, ValidationError
from rich.console import Console
from rich.markup import escape

# Local Imports
from udl.config import Settings
from udl.core.operations import operations
from udl.core.service import ServiceFactory, UDLService
from udl.core.urls import URL
from udl.errors import ConfigError, UDLError

EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130  # (128 + SIGINT, as shells report a command stopped by Ctrl-C)

JsonOption = Annotated[bool, typer.Option("--json", help="Print the result as JSON.")]


@dataclass(frozen=True)
class Runtime:
    """What the commands run with (the udl group's ctx.obj)."""

    settings: Settings
    make_service: ServiceFactory
    verbosity: int  # -q: -1, -v: 1, -vv: 2...

    @classmethod
    def of(cls, ctx: typer.Context) -> Self:
        runtime = ctx.find_object(cls)
        if runtime is None:
            raise RuntimeError("udl's commands run in the udl command group")
        return runtime


# ---------------------------------------------------------------------------------------------
# Which command runs which operation (the parity test checks every operation has one)
# ---------------------------------------------------------------------------------------------

_operations: dict[Callable[..., Any], str] = {}


def runs[F: Callable[..., Any]](name: str) -> Callable[[F], F]:
    """Mark a command as the one that runs the operation called `name`."""
    if name not in operations():
        raise ValueError(f"There is no operation called {name!r}")

    def marked(command: F) -> F:
        mark(command, name)
        return command

    return marked


def mark(command: Callable[..., Any], name: str) -> None:
    """Mark `command` as the one that runs the operation called `name` (udl's own, through
    runs(), or a plugin's: udl.interfaces.adapters makes their commands)."""
    _operations[command] = name


def operation_of(command: Callable[..., Any]) -> str | None:
    """The operation `command` runs, if it runs one."""
    return _operations.get(command)


# ---------------------------------------------------------------------------------------------
# Running operations
# ---------------------------------------------------------------------------------------------


def parse[M: BaseModel](model: type[M], **fields: object) -> M:
    """The request that `fields` (the command's arguments) make; a usage error says what is
    wrong with them."""
    try:
        return model.model_validate(fields)
    except ValidationError as e:
        usage_error(problems(e))


def parse_url(text: str) -> URL:
    """A URL argument; a usage error when it is not an http(s) URL."""
    try:
        return URL.parse(text)
    except ValueError as e:
        usage_error(str(e))


def usage_error(message: str) -> NoReturn:
    """Say what is wrong with the command's arguments, and exit with 2."""
    show_error(stderr(), message, "See the command's --help.")
    raise typer.Exit(EXIT_USAGE)


def problems(error: ValidationError) -> str:
    """A ValidationError, in a line."""
    return "; ".join(
        f"{'.'.join(map(str, problem['loc'])) or 'input'}: {problem['msg']}"
        for problem in error.errors()
    )


def run[T](
    ctx: typer.Context,
    work: Callable[[UDLService], Awaitable[T]],
    *,
    settings: Settings | None = None,
) -> T:
    """Run `work` on a service (with `settings`, else udl's), started and closed around it.

    Ctrl-C closes the service, which cancels (and saves) the downloads it runs, and exits with
    130. A UDLError is shown and exits with 1, or 2 when it is the config's fault.
    """
    runtime = Runtime.of(ctx)

    async def main() -> T:
        async with runtime.make_service(settings or runtime.settings) as service:
            return await work(service)

    return run_async(main())


def run_async[T](work: Coroutine[Any, Any, T]) -> T:
    """Run `work` (see run(), which runs it on a service)."""
    try:
        return asyncio.run(work)
    except KeyboardInterrupt:
        stderr().print("Cancelled.")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    except ConfigError as e:
        fail(e, EXIT_USAGE)
    except UDLError as e:
        fail(e)


def fail(error: UDLError, code: int = EXIT_FAILED) -> NoReturn:
    """Show `error` (and what to do about it), and exit with `code`."""
    show_error(stderr(), error.message, error.hint)
    raise typer.Exit(code)


def show_error(console: Console, message: str, hint: str | None = None) -> None:
    console.print(f"[bold red]Error:[/] {escape(message)}")
    if hint:
        console.print(escape(hint), style="dim")


# ---------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------


def stdout() -> Console:
    """Where results go (stdout, so they can be piped)."""
    return Console()


def stderr() -> Console:
    """Where progress, errors and logs go."""
    return Console(stderr=True)


def show[R: BaseModel](response: R, as_json: bool, render: Callable[[Console, R], None]) -> None:
    """Print a response: as JSON, or rendered by `render`."""
    if as_json:
        typer.echo(response.model_dump_json(indent=2))
    else:
        render(stdout(), response)
