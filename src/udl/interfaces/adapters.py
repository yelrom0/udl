"""
---------------
- adapters.py -
---------------

The adapters udl makes for an operation from its request model, so the operations plugins add
are offered by every interface without any interface code of their own:

- the CLI: `udl <plugin> <op>` (or the path its CLISpec gives), with an option per field (a flag
  for a yes or no, choices for one of a few, repeated for a list, KEY=VALUE pairs for a dict its
  CLISpec says, JSON for anything else; a secret is asked for, hidden, never given on the command
  line), or a positional argument where its CLISpec says, `--input FILE` (`-f`; `-` for stdin)
  for the whole request as JSON or YAML, and `--json`. udl's own operations that have a CLISpec
  are commands made the same way (add_commands());
- the API: `POST /plugins/<plugin>/<op>` (an integration's: /integrations/…), or as its APISpec
  says: its path's `{field}`s are path parameters, and the rest of the request is the body (or,
  for a GET or DELETE, the query), typed in the OpenAPI schema;
- the MCP server: the `<plugin>_<op>` tool, as for udl's own (udl.interfaces.mcp.server).
"""

# System Imports
import copy
import enum
import inspect
import json
import re
import types
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Final, Literal

# Package Imports
import typer
import yaml
from pydantic import BaseModel, JsonValue, SecretStr, ValidationError, create_model
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined
from typer.core import TyperGroup

# Local Imports
from udl.core.operations import APISpec, Operation, OperationRegistry, route
from udl.interfaces.cli import render
from udl.interfaces.cli.common import (
    JsonOption,
    key_values,
    mark,
    parse,
    run,
    show,
    usage_error,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

# The command's own options: a field called the same is only given in --input.
_OWN_OPTIONS: Final = frozenset({"input", "json", "help"})
# The types an option can be read as; anything else is read as JSON (or, when it is not JSON,
# as text).
_PLAIN: Final = (str, int, float, Path)

type FieldKind = Literal["flag", "plain", "list", "json", "json_list", "secret", "pairs"]


# ---------------------------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------------------------


def cli_groups(ops: OperationRegistry) -> dict[str, TyperGroup]:
    """The command groups of the operations plugins add (`udl <plugin> <op>`, or the path their
    CLISpec gives), by their first word."""
    apps: dict[tuple[str, ...], typer.Typer] = {}
    for op in ops:
        if op.namespace is None:  # (udl's own commands are written by hand)
            continue
        path = cli_path(op)
        _group(apps, path[:-1]).command(path[-1], help=op.description)(cli_command(op))
    return {path[0]: typer.main.get_group(app) for path, app in apps.items() if len(path) == 1}


def add_commands(
    app: typer.Typer,
    ops: Iterable[Operation],
    *,
    renderers: Mapping[str, Callable[..., None]],
    helps: Mapping[str, str],
    groups: Mapping[tuple[str, ...], typer.Typer] | None = None,
) -> None:
    """Add to `app` (udl's) a command for each of udl's own operations in `ops` that has a
    CLISpec path: a top-level one for a word (`udl status`), else in the group of its first
    words (`udl events list`, the group's help from `helps`, or one of `groups`, already in
    `app` with commands of its own), shown by its renderer in `renderers` (by operation
    name)."""
    groups = dict(groups or {})
    for op in ops:
        if op.namespace is not None or op.cli is None or not op.cli.path:
            continue
        path = op.cli.path
        command = cli_command(op, shown=renderers.get(op.name))
        if len(path) == 1:
            app.command(path[0], help=op.description)(command)
            continue
        for depth in range(1, len(path)):
            group = path[:depth]
            if group not in groups:
                groups[group] = typer.Typer(
                    help=helps.get(" ".join(group), f"udl {' '.join(group)}"),
                    no_args_is_help=True,
                    rich_markup_mode="markdown",
                )
                parent = app if depth == 1 else groups[group[:-1]]
                parent.add_typer(groups[group], name=group[-1])
        groups[path[:-1]].command(path[-1], help=op.description)(command)


def cli_path(op: Operation) -> tuple[str, ...]:
    """The words of an operation's command: e.g. ("gizmo", "echo")."""
    if op.cli is not None and op.cli.path is not None:
        return op.cli.path
    return (op.namespace or "udl", op.short_name.replace("_", "-"))


def _group(apps: dict[tuple[str, ...], typer.Typer], path: tuple[str, ...]) -> typer.Typer:
    """The Typer app of the command group `path` (made, with the groups it is in, if new)."""
    if path not in apps:
        what = f"The {path[0]} plugin's operations." if len(path) == 1 else f"udl {' '.join(path)}"
        apps[path] = typer.Typer(help=what, no_args_is_help=True, rich_markup_mode="markdown")
        if len(path) > 1:
            _group(apps, path[:-1]).add_typer(apps[path], name=path[-1])
    return apps[path]


@dataclass(frozen=True)
class _Field:
    """A request field, as a command's option or argument."""

    name: str
    kind: FieldKind
    parameter: inspect.Parameter
    option_names: tuple[str, ...] = ()


def cli_command(op: Operation, *, shown: Callable[..., None] | None = None) -> Callable[..., None]:
    """The command that runs `op`: its options (and arguments) are the request's fields. Its
    response is shown by `shown`, else its CLISpec's renderer, else render.model."""
    spec = op.cli
    arguments = spec.arguments if spec is not None else ()
    names: Mapping[str, str | tuple[str, ...]] = spec.options if spec is not None else {}
    pairs = spec.pairs if spec is not None else ()
    fields = [
        _field(name, op.request.model_fields[name], n, argument=True)
        for n, name in enumerate(arguments)
    ] + [
        _field(name, info, n + len(arguments), option=names.get(name), pairs=name in pairs)
        for n, (name, info) in enumerate(op.request.model_fields.items())
        if name not in _OWN_OPTIONS and name not in arguments
    ]
    show_it = shown or (spec.render if spec is not None and spec.render is not None else None)
    show_it = show_it or render.model

    def command(ctx: typer.Context, **options: Any) -> None:
        values = _read_input(options.pop("input_file"))
        as_json: bool = options.pop("as_json")
        for found in fields:
            given = options[found.parameter.name]
            if found.kind == "secret":
                info = op.request.model_fields[found.name]
                if given or (info.is_required() and found.name not in values):
                    values[found.name] = typer.prompt(
                        _label(found.name, info), hide_input=True, err=True
                    )
            elif found.kind == "pairs" and given:
                option = "/".join(found.option_names)
                values[found.name] = {**values.get(found.name, {}), **key_values(given, option)}
            elif given is not None and given != [] and given != ():
                values[found.name] = _value(found.kind, given)
        request = parse(op.request, **values)
        # (As data, which the service reads as its own operation's request: a local plugin's
        # file is imported again by each service, so its models are other classes there.)
        given = request.model_dump(round_trip=True)
        show(run(ctx, lambda service: service.call(op.name, given)), as_json, show_it)

    parameters = [
        inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=typer.Context),
        *(found.parameter for found in fields),
        inspect.Parameter(
            "input_file",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[
                str | None,
                typer.Option(
                    "--input",
                    "-f",
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


def _field(
    name: str,
    info: FieldInfo,
    n: int,
    *,
    argument: bool = False,
    option: str | tuple[str, ...] | None = None,
    pairs: bool = False,
) -> _Field:
    """The option (or the positional argument) for the request field `name`."""
    kind, python_type = ("pairs", list[str] | None) if pairs else _kind(info.annotation)
    names = (option,) if isinstance(option, str) else option or (f"--{name.replace('_', '-')}",)
    option = next((found for found in names if found.startswith("--")), names[0])
    help_text = info.description or ""
    if argument:
        declared: Any = typer.Argument(help=help_text, metavar=name.upper(), show_default=False)
    elif kind == "flag":
        declared = typer.Option(f"{option}/--no-{option[2:]}", help=help_text, show_default=False)
    elif kind == "secret":
        declared = typer.Option(
            *names,
            help=f"{help_text} Asked for, hidden, when given (or give it in --input).".strip(),
            show_default=False,
        )
    elif kind == "pairs":
        declared = typer.Option(
            *names,
            help=f"{help_text} KEY=VALUE (VALUE is JSON, else text); repeat for more.".strip(),
            metavar="KEY=VALUE",
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
        declared = typer.Option(*names, help=help_text, show_default=False)
    parameter = inspect.Parameter(
        f"field_{n}",
        inspect.Parameter.KEYWORD_ONLY,
        annotation=Annotated[python_type, declared],
        default=False if kind == "secret" else None,
    )
    return _Field(name=name, kind=kind, parameter=parameter, option_names=names)


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


def add_routes(app: FastAPI, ops: OperationRegistry) -> None:
    """Add a route to `app` for each operation in `ops` that a plugin adds (or that has an
    APISpec), as route() gives it."""
    # (Imported here, as the API is: the CLI starts faster without it.)
    from udl.interfaces.api.deps import documented_op

    for op in ops:
        if op.namespace is None and op.api is None:  # (udl's own are written by hand)
            continue
        spec = route(op)
        app.add_api_route(
            spec.path or "",
            _endpoint(op, spec),
            methods=[spec.method],
            status_code=spec.status,
            response_model=op.response,
            tags=[op.namespace or "udl"],
            **documented_op(op, 400, 403, 404, 409, 503),
        )


def _endpoint(op: Operation, spec: APISpec) -> Callable[..., Any]:
    """The route's function: its path parameters, then the rest of the request as the body (or
    the query), and it runs the operation."""
    from fastapi import Path as InPath
    from fastapi import Query
    from fastapi.exceptions import RequestValidationError

    from udl.interfaces.api.deps import ServiceDep

    fields = op.request.model_fields
    in_path = re.findall(r"{(\w+)}", spec.path or "")
    rest = [name for name in fields if name not in in_path]
    parameters = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[
                fields[name].annotation, InPath(description=fields[name].description)
            ],
        )
        for name in in_path
    ]
    if rest:
        part = op.request if not in_path else _part(op.request, rest, spec.method)
        if spec.method in ("GET", "DELETE"):
            parameters.append(
                inspect.Parameter(
                    "udl_query", inspect.Parameter.KEYWORD_ONLY, annotation=Annotated[part, Query()]
                )
            )
        else:
            parameters.append(
                inspect.Parameter("udl_body", inspect.Parameter.KEYWORD_ONLY, annotation=part)
            )
    parameters.append(
        inspect.Parameter("udl_service", inspect.Parameter.KEYWORD_ONLY, annotation=ServiceDep)
    )

    async def endpoint(**given: Any) -> BaseModel:
        service = given.pop("udl_service")
        part: BaseModel | None = given.pop("udl_query", None) or given.pop("udl_body", None)
        data = {**(part.model_dump() if part is not None else {}), **given}
        try:
            request = op.request.model_validate(data)
        except ValidationError as e:
            raise RequestValidationError(e.errors()) from e
        return await service.call(op.name, request)

    # (FastAPI reads a route's parameters, body and answer from its signature.)
    signature = inspect.Signature(parameters, return_annotation=op.response)
    setattr(endpoint, "__signature__", signature)  # noqa: B010
    return endpoint


def _part(request: type[BaseModel], names: list[str], method: str) -> type[BaseModel]:
    """A model of some of a request's fields (those not in the route's path)."""
    fields = request.model_fields
    what = "Query" if method in ("GET", "DELETE") else "Body"
    definitions: dict[str, Any] = {
        name: (fields[name].annotation, copy.copy(fields[name])) for name in names
    }
    made: type[BaseModel] = create_model(
        f"{request.__name__}{what}", __config__=request.model_config, **definitions
    )
    return made
