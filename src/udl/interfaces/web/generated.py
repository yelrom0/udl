"""
----------------
- generated.py -
----------------

What the shell generates its pages from: `GET /ui/generated.json`, everything a page needs —
each plugin's operations (with the view it opens in, what it does to the world, its route and its
request's and response's schemas), its settings form, how its accounts connect and the event
types it publishes — and udl's own operations, under the pseudo-plugin `udl`, so a page can be
generated for anything Phase 19 or 20 has not hand-built yet.

Interface-only: it is not an operation, so it is on neither the CLI nor MCP, and the parity
test's API routes (the OpenAPI schema's) do not list it.
"""

# System Imports
import logging
from collections.abc import Iterable
from typing import Final, Protocol, cast

# Package Imports
from pydantic import BaseModel, ConfigDict, Field, JsonValue

# Local Imports
from udl.core.operations import (
    Operation,
    OperationHints,
    OperationRegistry,
    UISpec,
    resolved_view,
    route,
)
from udl.core.schemas import model_schema
from udl.plugins.registry import INTEGRATIONS, PluginInfo, PluginRegistry
from udl.plugins.settings import is_file_only, is_secret, settings_model_of

log = logging.getLogger(__name__)

UDL: Final = "udl"
"""The pseudo-plugin udl's own operations are listed under."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)


class GeneratedOperation(_Frozen):
    """One operation, as the shell draws it: where it opens, what it does, and its forms."""

    name: str
    """Its name (the API's operationId, the MCP tool's)."""
    title: str
    """A few words."""
    summary: str
    """What it does, in a line."""
    description: str
    """Its docstring (for people, and for AI agents)."""
    hints: OperationHints
    """Whether it reads, destroys, is idempotent, reaches out or is an admin's."""
    ui: UISpec
    """How it is shown, with `view` resolved (auto's answer, not `auto`)."""
    method: str | None = None
    """Its route's method (None for udl's own: their routes are hand-written)."""
    path: str | None = None
    """Its route's path (None for udl's own)."""
    request_schema: JsonValue
    """Its request's JSON Schema, `$defs` inlined: the form is drawn from it."""
    response_schema: JsonValue
    """Its response's JSON Schema, `$defs` inlined: the table is drawn from it."""

    @classmethod
    def of(cls, op: Operation) -> GeneratedOperation:
        """`op`, with its route (where it has one of its own) and both schemas."""
        method, path = _route(op)
        return cls(
            name=op.name,
            title=op.title,
            summary=op.summary,
            description=op.description,
            hints=op.hints,
            ui=(op.ui or UISpec()).model_copy(update={"view": resolved_view(op)}),
            method=method,
            path=path,
            request_schema=model_schema(op.request),
            response_schema=model_schema(op.response),
        )


class GeneratedForm(_Frozen):
    """A plugin's settings, as a form: the schema, and which fields are not the user's to set."""

    json_schema: JsonValue
    """Its settings model's JSON Schema, `$defs` inlined."""
    read_only: list[str] = Field(default_factory=list[str])
    """The fields only the config file sets (they widen what udl reaches)."""
    secret: list[str] = Field(default_factory=list[str])
    """The fields that are secrets (the secret store keeps them)."""

    @classmethod
    def of(cls, model: type[BaseModel]) -> GeneratedForm:
        """The form for the settings `model`."""
        fields = model.model_fields
        return cls(
            json_schema=model_schema(model),
            read_only=[name for name, info in fields.items() if is_file_only(info)],
            secret=[name for name, info in fields.items() if is_secret(info)],
        )


class GeneratedAccounts(_Frozen):
    """How an integration's accounts connect, and what they may be connected for."""

    connect: list[str] = Field(default_factory=list[str])
    """How they connect, the usual first (oauth, tokens, none)."""
    access_levels: list[str] = Field(default_factory=list[str])
    """What an account may be connected for (read, send...), the default first."""
    options_schema: JsonValue
    """The account options' JSON Schema, `$defs` inlined."""


class GeneratedPlugin(_Frozen):
    """One plugin's page: its operations, and what else it has to show."""

    name: str
    """Its name (or `udl` for udl's own operations)."""
    kind: str | None = None
    """Its kind (its entry point group), None for `udl`."""
    title: str
    """What it is called."""
    summary: str = ""
    """What it is, in a line."""
    has_ui_plugin: bool = False
    """Whether it brings a UI plugin of its own (which the shell loads instead)."""
    operations: list[GeneratedOperation] = Field(default_factory=list[GeneratedOperation])
    """Its operations, in the order they are offered."""
    settings: GeneratedForm | None = None
    """Its settings form, when it has settings."""
    accounts: GeneratedAccounts | None = None
    """How its accounts connect (integrations only)."""
    event_types: list[str] = Field(default_factory=list[str])
    """The event types it publishes."""


class GeneratedUI(_Frozen):
    """Everything the shell generates pages from: udl's own operations, then each plugin's."""

    plugins: list[GeneratedPlugin] = Field(default_factory=list[GeneratedPlugin])
    """`udl` first, then by name."""


def generated(ops: OperationRegistry, plugins: PluginRegistry) -> GeneratedUI:
    """The generated UI's JSON: udl's own operations under `udl`, then each plugin's, with its
    settings form, its accounts and its event types."""
    found = [_of_udl(ops)]
    for info, cls in plugins.entries():
        found.append(_of_plugin(info, cls, ops))
    return GeneratedUI(plugins=found)


def _of_udl(ops: OperationRegistry) -> GeneratedPlugin:
    """udl's own operations, under the pseudo-plugin `udl`."""
    return GeneratedPlugin(
        name=UDL,
        title="udl",
        summary="udl's own operations: the ones no plugin adds.",
        operations=[GeneratedOperation.of(op) for op in ops.in_namespace(None)],
    )


def _of_plugin(info: PluginInfo, cls: type[object], ops: OperationRegistry) -> GeneratedPlugin:
    """`info`'s plugin, with what it adds and what it has to show."""
    model = settings_model_of(cls)
    integration = info.kind == INTEGRATIONS.group
    return GeneratedPlugin(
        name=info.name,
        kind=info.kind,
        title=str(getattr(cls, "title", info.name)),
        summary=str(getattr(cls, "summary", "")),
        has_ui_plugin=getattr(cls, "ui", None) is not None,
        operations=[GeneratedOperation.of(op) for op in ops.in_namespace(info.name)],
        settings=None if model is None else GeneratedForm.of(model),
        accounts=_accounts_of(cls) if integration else None,
        event_types=_event_types(cls),
    )


def _accounts_of(cls: type[object]) -> GeneratedAccounts:
    """How an integration's accounts connect."""
    account_model = getattr(cls, "account_model", None)
    options = (
        model_schema(account_model)
        if isinstance(account_model, type) and issubclass(account_model, BaseModel)
        else {}
    )
    return GeneratedAccounts(
        connect=[str(method.value) for method in getattr(cls, "connect", ())],
        access_levels=[str(level) for level in getattr(cls, "access_levels", ())],
        options_schema=options,
    )


class _Named(Protocol):
    """Something with a name (an event type's)."""

    @property
    def name(self) -> str: ...


def _event_types(cls: type[object]) -> list[str]:
    """The names of the event types a plugin publishes (none for most)."""
    publish = getattr(cls, "event_types", None)
    if not callable(publish):
        return []
    return [kind.name for kind in cast(Iterable[_Named], publish())]


def _route(op: Operation) -> tuple[str | None, str | None]:
    """An operation's route: its method and path. A plugin's is `route(op)`'s; udl's own is
    hand-written in udl.interfaces.api.routes, so it has none here (the shell calls udl's own
    operations by name, through the typed client)."""
    if op.namespace is None:
        return None, None
    spec = route(op)
    return spec.method, spec.path
