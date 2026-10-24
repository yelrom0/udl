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
(udl.interfaces.adapters).
"""

# System Imports
import annotationlib
import functools
import importlib
import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Self, cast

# Package Imports
from pydantic import BaseModel, ConfigDict

# Local Imports
from udl.errors import DoesNotExist, PluginError

if TYPE_CHECKING:
    from udl.plugins.registry import PluginRegistry

log = logging.getLogger(__name__)

# An operation's name, and a plugin's when it names operations: lower case, digits, _ and -.
_NAME: Final = re.compile(r"[a-z0-9][a-z0-9_-]*")

type OperationMethod = Callable[..., Awaitable[BaseModel]]
"""An operation's function: (service, request) -> response (UDLService's methods are too)."""


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

    @classmethod
    def of(
        cls,
        method: OperationMethod,
        *,
        name: str,
        title: str,
        hints: OperationHints | None = None,
    ) -> Self:
        """The operation `method` is: an async function (or UDLService's method) whose second
        parameter is its request model, which returns its response model and whose docstring
        describes it. Raises PluginError when it is not one."""
        what = getattr(method, "__qualname__", repr(method))
        if not _NAME.fullmatch(name):
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
        return cls(
            name=name,
            title=title,
            request=request,
            response=response,
            summary=doc.partition("\n")[0],
            description=doc,
            method=method,
            hints=hints or OperationHints(),
        )

    @property
    def short_name(self) -> str:
        """Its name without its namespace (e.g. "echo" for "gizmo_echo")."""
        if self.namespace is None:
            return self.name
        return self.name.removeprefix(f"{self.namespace}_")

    def in_namespace(self, namespace: str, *, kind: str) -> Self:
        """This operation as the plugin called `namespace` (of `kind`) adds it."""
        if not _NAME.fullmatch(namespace):
            raise PluginError(
                f"A plugin called {namespace!r} cannot add operations: its name must be lower "
                "case letters, digits, _ and -"
            )
        return replace(self, name=f"{namespace}_{self.name}", namespace=namespace, kind=kind)


def _model(annotation: object) -> type[BaseModel] | None:
    """`annotation`, when it is a model."""
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return annotation
    return None


# ---------------------------------------------------------------------------------------------
# udl's own operations
# ---------------------------------------------------------------------------------------------

_registered: list[tuple[str, str, OperationHints, OperationMethod]] = []


def operation[**P, R: BaseModel](
    name: str, *, title: str, hints: OperationHints | None = None
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Register a UDLService method as the operation called `name`: it takes its request as its
    only positional argument (others are keyword-only, for callers in Python)."""

    def register(method: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        if any(registered == name for registered, *_ in _registered):
            raise ValueError(f"There already is an operation called {name!r}")
        _registered.append((name, title, hints or OperationHints(), method))
        return method

    return register


@functools.cache
def operations() -> dict[str, Operation]:
    """udl's own operations, by name (each plugin's are in a service's OperationRegistry)."""
    importlib.import_module("udl.core.service")  # (its methods register the operations)

    return {
        name: Operation.of(method, name=name, title=title, hints=hints)
        for name, title, hints, method in _registered
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
