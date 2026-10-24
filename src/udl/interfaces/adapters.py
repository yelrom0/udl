"""
---------------
- adapters.py -
---------------

The adapters udl makes for an operation from its request model, so the operations plugins add
are offered by every interface without any interface code of their own:

- the CLI: `udl <plugin> <op>`, with an option per field (a flag for a yes or no, choices for
  one of a few, repeated for a list, JSON for anything else; a secret is asked for, hidden, never
  given on the command line), `--input FILE` (`-` for stdin) for the whole request as JSON or
  YAML, and `--json`;
- the API: `POST /<kind>/<plugin>/<op>` (e.g. /sites/gizmo/echo), whose body is the request and
  whose answer is the response, typed in the OpenAPI schema;
- the MCP server: the `<plugin>_<op>` tool, as for udl's own (udl.interfaces.mcp.server).
"""

# System Imports
import enum
import inspect
import json
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

# Package Imports
import typer
import yaml
from pydantic import BaseModel, JsonValue, SecretStr
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from typer.core import TyperGroup

# Local Imports
from udl.core.operations import Operation, OperationRegistry
from udl.interfaces.cli import render
from udl.interfaces.cli.common import JsonOption, mark, parse, run, show, usage_error

if TYPE_CHECKING:
    from fastapi import FastAPI

# The command's own options: a field called the same is only given in --input.
_OWN_OPTIONS: Final = frozenset({"input", "json", "help"})
# The types an option can be read as; anything else is read as JSON (or, when it is not JSON,
# as text).
_PLAIN: Final = (str, int, float, Path)

type FieldKind = Literal["flag", "plain", "list", "json", "json_list", "secret"]


# ---------------------------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------------------------


def cli_groups(ops: OperationRegistry) -> dict[str, TyperGroup]:
    """A command group for each plugin that adds operations (`udl <plugin>`), with a command
    for each of its operations, by the plugin's name."""
    groups: dict[str, TyperGroup] = {}
    for namespace in ops.namespaces():
        app = typer.Typer(
            help=f"The {namespace} plugin's operations.",
            no_args_is_help=True,
            rich_markup_mode="markdown",
        )
        for op in ops.in_namespace(namespace):
            app.command(command_name(op), help=op.description)(cli_command(op))
        groups[namespace] = typer.main.get_group(app)
    return groups


def command_name(op: Operation) -> str:
    """The operation's command, in its plugin's group: e.g. `send-message` for send_message."""
    return op.short_name.replace("_", "-")


@dataclass(frozen=True)
class _Field:
    """A request field, as a command's option."""

    name: str
    kind: FieldKind
    parameter: inspect.Parameter


def cli_command(op: Operation) -> Callable[..., None]:
    """The command that runs `op`: its options are the request's fields."""
    fields = [
        _field(name, info, n)
        for n, (name, info) in enumerate(op.request.model_fields.items())
        if name not in _OWN_OPTIONS
    ]

    def command(ctx: typer.Context, **options: Any) -> None:
        values = _read_input(options.pop("input_file"))
        as_json: bool = options.pop("as_json")
        for spec in fields:
            given = options[spec.parameter.name]
            if spec.kind == "secret":
                info = op.request.model_fields[spec.name]
                if given or (info.is_required() and spec.name not in values):
                    values[spec.name] = typer.prompt(
                        _label(spec.name, info), hide_input=True, err=True
                    )
            elif given is not None and given != []:
                values[spec.name] = _value(spec.kind, given)
        request = parse(op.request, **values)
        # (As data, which the service reads as its own operation's request: a local plugin's
        # file is imported again by each service, so its models are other classes there.)
        given = request.model_dump(round_trip=True)
        show(run(ctx, lambda service: service.call(op.name, given)), as_json, render.model)

    parameters = [
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context),
        *(spec.parameter for spec in fields),
        inspect.Parameter(
            "input_file",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[
                str | None,
                typer.Option(
                    "--input",
                    help="The whole request, as JSON or YAML, from FILE (- for stdin); the "
                    "options given beat it.",
                    metavar="FILE",
                    show_default=False,
                ),
            ],
            default=None,
        ),
        inspect.Parameter(
            "as_json", inspect.Parameter.KEYWORD_ONLY, annotation=JsonOption, default=False
        ),
    ]
    # (Typer reads a command's options from its signature.)
    setattr(command, "__signature__", inspect.Signature(parameters))  # noqa: B010
    command.__annotations__ = {p.name: p.annotation for p in parameters}
    command.__doc__ = op.description
    mark(command, op.name)
    return command


def _field(name: str, info: FieldInfo, n: int) -> _Field:
    """The option for the request field `name`."""
    kind, python_type = _kind(info.annotation)
    option = f"--{name.replace('_', '-')}"
    help_text = info.description or ""
    if kind == "flag":
        declared = typer.Option(f"{option}/--no-{option[2:]}", help=help_text, show_default=False)
    elif kind == "secret":
        declared = typer.Option(
            option,
            help=f"{help_text} Asked for, hidden, when given (or give it in --input).".strip(),
            show_default=False,
        )
    else:
        if kind in ("json", "json_list"):
            help_text = f"{help_text} (JSON, else text)".strip()
        if kind in ("list", "json_list"):
            help_text = f"{help_text} Repeat for more.".strip()
        default = _default(info)
        if default is not None:
            help_text = f"{help_text} [default: {default}]".strip()
        declared = typer.Option(option, help=help_text, show_default=False)
    parameter = inspect.Parameter(
        f"field_{n}",
        inspect.Parameter.KEYWORD_ONLY,
        annotation=Annotated[python_type, declared],
        default=False if kind == "secret" else None,
    )
    return _Field(name=name, kind=kind, parameter=parameter)


def _kind(annotation: Any) -> tuple[FieldKind, Any]:
    """How a field of type `annotation` is given as an option, and the type it is read as."""
    found = _optional(annotation)
    if found is bool:
        return "flag", bool | None
    if found is SecretStr:
        return "secret", bool
    if _is_plain(found):
        return "plain", found | None
    if typing.get_origin(found) in (list, set, tuple, frozenset):
        args = [arg for arg in typing.get_args(found) if arg is not Ellipsis]
        item = args[0] if len(args) == 1 else None
        if item is not None and _is_plain(item):
            return "list", types.GenericAlias(list, (item,)) | None
        return "json_list", list[str] | None
    return "json", str | None


def _optional(annotation: Any) -> Any:
    """`annotation` without `| None`."""
    if isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is typing.Union:
        args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _is_plain(annotation: Any) -> bool:
    return inspect.isclass(annotation) and (
        annotation in _PLAIN or issubclass(annotation, enum.Enum)
    )


def _default(info: FieldInfo) -> str | None:
    """A field's default, to show in its help; None when it has none worth showing."""
    if info.default is PydanticUndefined or info.default in (None, "", [], {}):
        return None
    default = info.default
    return str(default.value if isinstance(default, enum.Enum) else default)


def _label(name: str, info: FieldInfo) -> str:
    """What a secret is asked for as."""
    return info.description.rstrip(".") if info.description else name.replace("_", " ")


def _value(kind: FieldKind, given: Any) -> Any:
    """An option's value, as the request takes it."""
    if kind == "json":
        return _json_or_text(given)
    if kind == "json_list":
        return [_json_or_text(item) for item in given]
    if kind == "list":
        return [_plain(item) for item in given]
    return _plain(given)


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, enum.Enum) else value


def _json_or_text(text: str) -> JsonValue:
    """`text` as JSON, or, when it is not JSON, the text itself."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _read_input(name: str | None) -> dict[str, Any]:
    """The request in the file called `name` (`-` for stdin), as JSON or YAML."""
    if name is None:
        return {}
    try:
        text = typer.get_text_stream("stdin").read() if name == "-" else Path(name).read_text()
        found = yaml.safe_load(text)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        usage_error(f"Cannot read the request in {_shown(name)}: {e}")
    if not isinstance(found, dict):
        usage_error(f"{_shown(name)} holds no request: it is one JSON or YAML object")
    return {str(key): value for key, value in typing.cast(dict[Any, Any], found).items()}


def _shown(name: str) -> str:
    return "stdin" if name == "-" else name


# ---------------------------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------------------------


def api_path(op: Operation) -> str:
    """The route of a plugin's operation: /<kind>/<plugin>/<op>, e.g. /sites/gizmo/echo."""
    if op.namespace is None or op.kind is None:
        raise ValueError(f"{op.name} is udl's own: its route is written by hand")
    return f"/{op.kind.rpartition('.')[2]}/{op.namespace}/{op.short_name}"


def add_routes(app: FastAPI, ops: OperationRegistry) -> None:
    """Add a route to `app` for each operation in `ops` that a plugin adds."""
    # (Imported here, as the API is: the CLI starts faster without it.)
    from udl.errors import ErrorInfo

    for op in ops:
        if op.namespace is None:
            continue
        app.add_api_route(
            api_path(op),
            _endpoint(op),
            methods=["POST"],
            response_model=op.response,
            operation_id=op.name,
            summary=op.summary,
            description=op.description,
            responses={status: {"model": ErrorInfo} for status in (400, 403, 404, 503)},
            tags=[op.namespace],
        )


def _endpoint(op: Operation) -> Callable[..., Any]:
    """The route's function: it takes the request (the body) and runs the operation."""
    from udl.interfaces.api.deps import ServiceDep

    async def endpoint(request: BaseModel, service: Any) -> BaseModel:
        return await service.call(op.name, request)

    # (FastAPI reads a route's body and answer from its signature.)
    signature = inspect.Signature(
        [
            inspect.Parameter(
                "request", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=op.request
            ),
            inspect.Parameter(
                "service", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ServiceDep
            ),
        ],
        return_annotation=op.response,
    )
    setattr(endpoint, "__signature__", signature)  # noqa: B010
    return endpoint
