"""
-----------------
- operations.py -
-----------------

Operations: everything udl does, each an async function of one request model that returns one
response model. The CLI, the API and the MCP server only parse a request, call an operation and
show its response, so they offer the same operations with the same validation.

udl's own operations are UDLService's methods marked with @operation (operations()). A plugin
(of any kind) adds its own through a classmethod, `operations()`, that gives them: each is named
after the plugin (the gizmo plugin's `echo` is `gizmo_echo`), and its function takes the service
and the request. A service's OperationRegistry holds udl's and its plugins', and the interfaces
offer every operation in it: the plugins' through adapters made from their request models
(udl.interfaces.adapters), as their CLISpec and APISpec say.

The interface an operation is called through sets current_caller (cli, api, mcp, and the
automations' runtime), so an operation can tell what to allow: one that takes a path checks it
against its roots unless it came from the CLI (or from udl's own Python).

An operation may also say how the generated UI (udl.interfaces.web) shows it: UISpec on the
operation, and ui_field() on the fields of its request, both read by the generated page and
GET /ui/generated.json (24.3), which nothing else here acts on.
"""

# System Imports
import annotationlib
import functools
import importlib
import inspect
import logging
import re
import types
import typing
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final, Literal, Self, cast

# Package Imports
from pydantic import BaseModel, ConfigDict, JsonValue, SecretStr
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, JsonSchemaValue
from pydantic_core import CoreSchema

# Local Imports
from udl.errors import DoesNotExist, PluginError

if TYPE_CHECKING:
    from udl.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

# An operation's name, and a plugin's when it names operations: lower case, digits, _ and -.
_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_-]*")
_PATH_FIELD: Final = re.compile(r"{(\w+)}")
_METHODS: Final = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
INTEGRATIONS: Final = "udl.integrations"  # (the integrations' kind: their routes are their own)
# What a plugin's operation is never called on the API, besides an integration's: its routes
# are /plugins/<plugin>/<op>, and /plugins/<plugin>/settings is the plugin's settings.
_RESERVED: Final = frozenset({"settings"})


type OperationMethod = Callable[..., Awaitable[BaseModel]]
"""An operation's function: (service, request) -> response (UDLService's methods are too)."""


Widget = Literal[
    "text",
    "textarea",
    "markdown",
    "password",
    "number",
    "switch",
    "select",
    "multiselect",
    "path",
    "url",
    "datetime",
    "duration",
    "json",
    "account",
    "code",
]
"""How a form shows a field (what ui_field's widget= may be)."""

WIDGETS: Final = frozenset[str](typing.get_args(Widget))
"""The same list, at run time: ui_field() refuses an unknown one, and so does a schema drawn by
UISchemaGenerator (a hint written by hand is refused then)."""


class UISpec(BaseModel):
    """How the generated UI shows an operation: where it sits (group, order), which view to open
    it in (an operation's own view, where auto's answer is not what it should be), and what it
    must be asked before it runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group: str | None = None
    """The heading it sits under (None: the plugin's operations, ungrouped)."""
    order: int = 0
    """Where it sits in its group: the lowest first."""
    view: Literal["auto", "list", "detail", "form", "action"] = "auto"
    """Which view to open it in (auto: resolved_view)."""
    icon: str | None = None
    """The icon beside it (a name the shell knows)."""
    hidden: bool = False
    """Whether the page shows it at all (it is still a command, a route and a tool)."""
    confirm_field: str | None = None
    """A field of its request that must equal a named thing before the button enables (as
    Proxmox's `confirm` in 22.5): the name of the thing it destroys or overwrites."""


def ui_field(
    *,
    widget: Widget | None = None,
    label: str | None = None,
    help: str | None = None,  # noqa: A002 - (as the field's key names it)
    placeholder: str | None = None,
    order: int | None = None,
    advanced: bool = False,
) -> dict[str, JsonValue]:
    """What a request's field says about how the generated UI shows it, for
    `Field(json_schema_extra=ui_field(…))`: one `x-udl-ui` key in its JSON Schema, holding only
    what was given. The page (and <SchemaForm>) reads it; nothing in udl acts on it."""
    hints: dict[str, JsonValue] = {}
    if widget is not None and widget not in WIDGETS:
        raise PluginError(
            f"{widget!r} is not a widget: " + ", ".join(sorted(WIDGETS)),
            hint="Use one of those, or leave the widget to the field's type.",
        )
    if widget is not None:
        hints["widget"] = widget
    if label is not None:
        hints["label"] = label
    if help is not None:
        hints["help"] = help
    if placeholder is not None:
        hints["placeholder"] = placeholder
    if order is not None:
        hints["order"] = order
    if advanced:
        hints["advanced"] = True
    return {"x-udl-ui": hints}


def _check_ui(
    what: str, request: type[BaseModel], ui: UISpec | None, hints: OperationHints
) -> None:
    """Raise PluginError when `ui` asks for something the operation does not have, or means
    nothing for it."""
    if ui is None:
        return
    if ui.confirm_field is not None:
        if ui.confirm_field not in request.model_fields:
            raise PluginError(
                f"{what}: its confirm field {ui.confirm_field!r} is not a field of its request"
            )
        if hints.read_only or not hints.destructive:
            raise PluginError(
                f"{what}: its confirm field {ui.confirm_field!r} only makes sense for an "
                "operation that changes something (and is destructive)"
            )


_UI_KEY: Final = "x-udl-ui"


class UISchemaGenerator(GenerateJsonSchema):
    """The schema generator that checks the field hints (ui_field's) as it draws a model's
    schema: a secret is never shown as a box that shows its value, and every widget named is one
    the UI has. So a model that names a wrong one is refused when its schema is asked for, with
    the model's own model_json_schema()."""

    def generate(self, schema: CoreSchema, mode: JsonSchemaMode = "validation") -> JsonSchemaValue:
        for model in _models(schema):
            check_ui_hints(model)
        return super().generate(schema, mode)


def _models(schema: CoreSchema) -> Iterator[type[BaseModel]]:
    """Every model a core schema holds, each once: the outer one and every one nested in it,
    wherever pydantic put them (its own schema, or a definition of it)."""
    seen: set[int] = set()

    def walk(part: object) -> Iterator[type[BaseModel]]:
        if isinstance(part, dict):
            fields = cast(dict[str, object], part)
            model = fields.get("cls")
            if (
                fields.get("type") == "model"
                and isinstance(model, type)
                and issubclass(model, BaseModel)
                and id(model) not in seen
            ):
                seen.add(id(model))
                yield model
            for value in fields.values():
                yield from walk(value)
        elif isinstance(part, (list, tuple)):
            for value in cast(Sequence[object], part):
                yield from walk(value)

    yield from walk(schema)


def _hints(extra: object) -> dict[str, JsonValue] | None:
    """The `x-udl-ui` value in a field's json_schema_extra, as a dict (None: there is none)."""
    if not isinstance(extra, dict):
        return None
    found = cast(dict[str, JsonValue], extra)
    hints = found.get(_UI_KEY)
    if not isinstance(hints, dict):
        return None
    return cast(dict[str, JsonValue], hints)


def check_ui_hints(model: type[BaseModel]) -> None:
    """Make sure the field hints a model's fields carry (ui_field's) are ones the UI can show:
    a secret is never a box that shows its value (it is always a password), and every widget named
    is one the UI has. Raises PluginError for one that is not."""
    for name, info in model.model_fields.items():
        hints = _hints(info.json_schema_extra)
        if hints is None:
            continue
        widget = hints.get("widget")
        if isinstance(widget, str) and widget not in WIDGETS:
            raise PluginError(
                f"{model.__qualname__}.{name}: {widget!r} is not a widget: "
                + ", ".join(sorted(WIDGETS))
            )
        if _is_secret(info.annotation) and widget is not None and widget != "password":
            raise PluginError(
                f"{model.__qualname__}.{name} is a secret, so it is never shown as a "
                f"{widget!r} box (a password box is used, whatever the field says)"
            )


class UIModel(BaseModel):
    """The base a request or a settings model that carries field hints (ui_field) extends: its
    schema is drawn by UISchemaGenerator, so a wrong hint is a PluginError when the schema is
    asked for rather than a form that draws the wrong box for a secret."""

    @classmethod
    def model_json_schema(
        cls,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: type[GenerateJsonSchema] = UISchemaGenerator,
        mode: JsonSchemaMode = "validation",
        *,
        union_format: Literal["any_of", "primitive_type_array"] = "any_of",
    ) -> dict[str, Any]:
        return super().model_json_schema(
            by_alias=by_alias,
            ref_template=ref_template,
            schema_generator=schema_generator,
            mode=mode,
            union_format=union_format,
        )


def json_schema(model: type[BaseModel]) -> dict[str, JsonValue]:
    """A model's JSON Schema, its field hints (ui_field's) checked as it is drawn (see
    UISchemaGenerator): the schema the generated UI and GET /ui/generated.json are given."""
    # (pydantic types a schema Dict[str, Any]; everything in one is JSON.)
    return cast(dict[str, JsonValue], model.model_json_schema(schema_generator=UISchemaGenerator))


def resolved_view(op: Operation) -> Literal["list", "detail", "form", "action"]:
    """The view an operation is opened in: its own (UISpec.view), or, when it says auto, what the
    card means by auto, in the card's order:
    - a list: it reads and asks nothing it needs (every field it has has a default) — so a
      read-only operation that takes nothing is a list too, which is what 24.6 runs on open;
    - a detail: it reads, and needs something;
    - an action: it changes something and asks nothing (it is run on a button);
    - a form: anything else (it changes something and asks something)."""
    if op.ui is not None and op.ui.view != "auto":
        return op.ui.view
    if op.hints.read_only:
        needed = [info for info in op.request.model_fields.values() if info.is_required()]
        return "detail" if needed else "list"
    if not op.request.model_fields:
        return "action"
    return "form"


class OperationHints(BaseModel):
    """What an operation does to the world, for an agent (its MCP tool's annotations) and its
    user to decide when to call it, and for udl to keep it from where it does not belong."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    read_only: bool = False
    """It changes nothing."""
    destructive: bool = False
    """It may delete or overwrite something (when it changes anything at all)."""
    idempotent: bool = False
    """Calling it again with the same request changes nothing more."""
    open_world: bool = False
    """It reaches outside udl: the web, a service, someone else's inbox."""
    admin: bool = False
    """It changes automations, accounts, AI setup or settings: never a tool for an AI action."""


@dataclass(frozen=True)
class CLISpec:
    """How an operation's generated command looks, where the defaults (`udl <plugin> <op>`, an
    option per field) are not what it should be."""

    # The command's words, e.g. ("gmail", "list"); one word is a command of udl's own (`status`).
    path: tuple[str, ...] | None = None
    arguments: tuple[str, ...] = ()  # the fields given as positional arguments, in order
    # field: its option's name, or names (e.g. ("-O", "--option"))
    options: Mapping[str, str | tuple[str, ...]] = field(
        default_factory=dict[str, str | tuple[str, ...]]
    )
    pairs: tuple[str, ...] = ()  # dict fields given as KEY=VALUE, repeated (VALUE: JSON, else text)
    render: Callable[..., None] | None = None  # (console, response): how it is shown


@dataclass(frozen=True)
class APISpec:
    """How an operation's generated route looks: its method, its path (whose `{field}`s are
    the request's fields; the rest is the body, or the query of a GET or DELETE) and the status
    it answers with."""

    method: str = "POST"
    path: str | None = None  # by default /plugins/<plugin>/<op> (an integration's: /integrations/…)
    status: int = 200


@dataclass(frozen=True)
class Operation:
    """One operation: udl's own, or one a plugin adds."""

    name: str  # e.g. "download", "gizmo_echo": its MCP tool's; the CLI and API name theirs after it
    title: str  # a few words, e.g. "Download URLs"
    request: type[BaseModel]
    response: type[BaseModel]
    summary: str  # the first line of its docstring
    description: str  # its docstring, written for people and for AI agents (MCP)
    method: OperationMethod
    hints: OperationHints = field(default_factory=OperationHints)
    namespace: str | None = None  # the plugin that adds it (e.g. "gizmo"); None for udl's own
    kind: str | None = None  # that plugin's kind (its entry point group, e.g. "udl.sites")
    cli: CLISpec | None = (
        None  # its generated command, where not the default (udl.interfaces.adapters)
    )
    api: APISpec | None = None  # its generated route, where not the default
    ui: UISpec | None = None  # how the generated UI shows it (None: the defaults)

    @classmethod
    def of(
        cls,
        method: OperationMethod,
        *,
        name: str,
        title: str,
        hints: OperationHints | None = None,
        cli: CLISpec | None = None,
        api: APISpec | None = None,
        ui: UISpec | None = None,
    ) -> Self:
        """The operation `method` is: an async function (or UDLService's method) whose second
        parameter is its request model, which returns its response model and whose docstring
        describes it, with its command, route and UI hints as `cli`, `api` and `ui` say. Raises
        PluginError when it is not one."""
        what = getattr(method, "__qualname__", repr(method))
        if not valid_name(name):
            raise PluginError(
                f"{what}: an operation's name is lower case letters, digits, _ and -, not {name!r}"
            )
        doc = inspect.getdoc(method)
        if not doc:
            raise PluginError(
                f"{what} has no docstring: it describes the operation to people and to AI agents"
            )
        # (Annotations that name what is only imported for type checkers, such as the service,
        # stay unevaluated.)
        forward = annotationlib.Format.FORWARDREF
        annotations = annotationlib.get_annotations(method, format=forward)
        parameters = list(inspect.signature(method, annotation_format=forward).parameters)
        request = _model(annotations.get(parameters[1]) if len(parameters) > 1 else None)
        response = _model(annotations.get("return"))
        if request is None or response is None:
            raise PluginError(
                f"{what} is not an operation: it takes (service, request) and returns a "
                "response, both annotated as Pydantic models"
            )
        given = hints or OperationHints()
        _check_specs(what, request, cli, api)
        _check_ui(what, request, ui, given)
        return cls(
            name=name,
            title=title,
            request=request,
            response=response,
            summary=doc.partition("\n")[0],
            description=doc,
            method=method,
            hints=given,
            cli=cli,
            api=api,
            ui=ui,
        )

    @property
    def short_name(self) -> str:
        """Its name without its namespace (e.g. "echo" for "gizmo_echo")."""
        if self.namespace is None:
            return self.name
        return self.name.removeprefix(f"{self.namespace}_")

    def in_namespace(self, namespace: str, *, kind: str) -> Self:
        """This operation as the plugin called `namespace` (of `kind`) adds it."""
        if not valid_name(namespace):
            raise PluginError(
                f"A plugin called {namespace!r} cannot add operations: its name must be lower "
                "case letters, digits, _ and -"
            )
        if self.cli is not None and self.cli.path is not None and len(self.cli.path) < 2:
            raise PluginError(
                f"The {namespace} plugin's {self.name} command is {self.cli.path!r}: a plugin's "
                "command is two words or more (udl <plugin> <op>; one word is udl's own)"
            )
        if kind != INTEGRATIONS and self.name in _RESERVED:
            raise PluginError(
                f"A plugin's operation is not called {self.name!r}: "
                f"/plugins/{namespace}/{self.name} is the plugin's settings",
                hint="Rename the operation.",
            )
        return replace(self, name=f"{namespace}_{self.name}", namespace=namespace, kind=kind)


def _check_specs(
    what: str, request: type[BaseModel], cli: CLISpec | None, api: APISpec | None
) -> None:
    """Raise PluginError when a spec names what the request does not have."""
    fields = request.model_fields
    named: list[str] = []
    if cli is not None:
        if cli.path is not None and not cli.path:
            raise PluginError(f"{what}: a command's path is a word or more, not {cli.path!r}")
        named += [*cli.arguments, *cli.options, *cli.pairs]
        for paired in cli.pairs:
            if paired in fields and not _is_dict(fields[paired].annotation):
                raise PluginError(f"{what}: {paired} is given as KEY=VALUE pairs, so it is a dict")
        for argument in cli.arguments:
            if argument in fields and _is_secret(fields[argument].annotation):
                raise PluginError(
                    f"{what}: {argument} is a secret, never a command's argument (it is asked for)"
                )
    if api is not None:
        if api.method not in _METHODS:
            raise PluginError(f"{what}: {api.method} is not a method udl routes")
        named += _PATH_FIELD.findall(api.path or "")
    unknown = [name for name in named if name not in fields]
    if unknown:
        raise PluginError(f"{what}: its request has no field {', '.join(unknown)}")


def _is_secret(annotation: Any) -> bool:
    return annotation is SecretStr or SecretStr in getattr(annotation, "__args__", ())


def _is_dict(annotation: Any) -> bool:
    """Whether `annotation` (or None) is a dict (an alias of one too)."""
    while isinstance(annotation, typing.TypeAliasType):
        annotation = annotation.__value__
    args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
    if typing.get_origin(annotation) in (types.UnionType, typing.Union) and len(args) == 1:
        return _is_dict(args[0])
    return typing.get_origin(annotation) is dict or annotation is dict


def route(op: Operation) -> APISpec:
    """The route an operation's generated adapter has: its APISpec, with its path by default
    /plugins/<plugin>/<op> (or, for an integration's, /integrations/<integration>/<op>)."""
    spec = op.api or APISpec()
    if spec.path is not None:
        return spec
    if op.namespace is None:
        raise ValueError(f"{op.name} is udl's own: its route is its APISpec's, or written by hand")
    section = "integrations" if op.kind == INTEGRATIONS else "plugins"
    return replace(spec, path=f"/{section}/{op.namespace}/{op.short_name}")


def valid_name(name: str) -> bool:
    """Whether `name` can name an operation, or a plugin that adds them."""
    return _NAME.fullmatch(name) is not None


def _model(annotation: object) -> type[BaseModel] | None:
    """`annotation`, when it is a model."""
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return annotation
    return None


# ---------------------------------------------------------------------------------------------
# udl's own operations
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Registered:
    name: str
    title: str
    hints: OperationHints | None
    cli: CLISpec | None
    api: APISpec | None
    ui: UISpec | None
    method: OperationMethod


_registered: list[_Registered] = []


def operation[**P, R: BaseModel](
    name: str,
    *,
    title: str,
    hints: OperationHints | None = None,
    cli: CLISpec | None = None,
    api: APISpec | None = None,
    ui: UISpec | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Register a UDLService method as the operation called `name`: it takes its request as its
    only positional argument (others are keyword-only, for callers in Python)."""

    def register(method: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        if any(registered.name == name for registered in _registered):
            raise ValueError(f"There already is an operation called {name!r}")
        _registered.append(_Registered(name, title, hints, cli, api, ui, method))
        return method

    return register


@functools.cache
def operations() -> dict[str, Operation]:
    """udl's own operations, by name (each plugin's are in a service's OperationRegistry)."""
    importlib.import_module("udl.core.service")  # (its methods register the operations)

    return {
        found.name: Operation.of(
            found.method,
            name=found.name,
            title=found.title,
            hints=found.hints,
            cli=found.cli,
            api=found.api,
            ui=found.ui,
        )
        for found in _registered
    }


# ---------------------------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------------------------


class OperationRegistry:
    """The operations a service offers, by name: udl's own, then its plugins'."""

    def __init__(self, ops: Iterable[Operation] = ()) -> None:
        self._ops: dict[str, Operation] = {}
        for op in ops:
            self.add(op)

    @classmethod
    def core(cls) -> Self:
        """udl's own operations."""
        return cls(operations().values())

    @classmethod
    def discover(cls, plugins: PluginRegistry) -> Self:
        """udl's own operations, then those each plugin in `plugins` adds (by kind and name). A
        plugin's operation that cannot be added (named like another, or not an operation) is
        left out and reported as one of the plugins' problems."""
        found = cls.core()
        for info, plugin in plugins.entries():
            provide = getattr(plugin, "operations", None)
            if not callable(provide):
                continue
            try:
                added = list(cast(Iterable[object], provide()))
            except Exception as e:  # noqa: BLE001 - a broken plugin must never stop udl
                plugins.report(info.kind, info.origin, e)
                continue
            for op in added:
                try:
                    if not isinstance(op, Operation):
                        raise PluginError(
                            f"{info.name}'s operations() gave {op!r}, not an Operation"
                        )
                    found.add(op.in_namespace(info.name, kind=info.kind))
                except PluginError as e:
                    plugins.report(info.kind, info.origin, e)
        return found

    def add(self, op: Operation) -> None:
        """Add `op`. Raises PluginError when an operation of its name is already here."""
        if op.name in self._ops:
            raise PluginError(
                f"There already is an operation called {op.name!r}",
                hint="Rename the plugin's operation (or the plugin).",
            )
        self._ops[op.name] = op

    def get(self, name: str) -> Operation:
        """The operation called `name`. Raises DoesNotExist when there is none."""
        op = self._ops.get(name)
        if op is None:
            raise DoesNotExist(
                f"No operation called {name!r}", hint=f"The operations: {', '.join(self._ops)}."
            )
        return op

    def names(self) -> list[str]:
        """Every operation's name: udl's own first, in the order they were added."""
        return list(self._ops)

    def namespaces(self) -> list[str]:
        """The plugins that add operations, by name."""
        return sorted({op.namespace for op in self if op.namespace is not None})

    def in_namespace(self, namespace: str | None) -> list[Operation]:
        """The operations the plugin called `namespace` adds (None: udl's own)."""
        return [op for op in self if op.namespace == namespace]

    def __contains__(self, name: object) -> bool:
        return name in self._ops

    def __iter__(self) -> Iterator[Operation]:
        return iter(self._ops.values())

    def __len__(self) -> int:
        return len(self._ops)


# ---------------------------------------------------------------------------------------------
# Who calls
# ---------------------------------------------------------------------------------------------


class Caller(StrEnum):
    """The interface an operation is called through."""

    CLI = "cli"
    API = "api"
    MCP = "mcp"
    AUTOMATION = "automation"  # (an action's run, in the runtime)


current_caller: ContextVar[Caller | None] = ContextVar("udl_current_caller", default=None)
"""Who is calling the operation that runs: set by each interface; None for udl's own Python."""


def checks_paths() -> bool:
    """Whether the operation that runs checks the paths it is given against their roots (such
    as api.allowed_output_roots): unless the CLI (the user, on their machine) or udl's own Python
    called it."""
    return current_caller.get() not in (None, Caller.CLI)
