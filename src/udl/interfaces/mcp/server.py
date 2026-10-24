"""
-------------
- server.py -
-------------

The MCP server (the mcp SDK's MCPServer): a tool for every operation (udl's own and its plugins'),
named after it, whose arguments are the operation's request fields and whose result is its
response (as structured content, and the same as JSON text). Its annotations are the operation's
hints. `udl mcp` serves it on stdin/stdout, and `udl serve` at /mcp (streamable HTTP).

`download` waits for its downloads (at most DEFAULT_WAIT seconds unless the agent says
otherwise), reporting their progress as MCP progress notifications. As with the API, an output
dir the agent names must be in api.allowed_output_roots (the tools are called as MCP's:
current_caller). udl's errors are tool errors that say what to do next.
"""

# System Imports
import functools
import importlib.metadata
import inspect
import types
import typing
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Final, Union
from uuid import UUID

# Package Imports
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field, PlainValidator, SecretStr, TypeAdapter, WithJsonSchema
from pydantic.fields import FieldInfo

# Local Imports
from udl.core.models import DownloadRequest, JobEvent
from udl.core.operations import Caller, Operation, OperationRegistry, current_caller
from udl.core.service import UDLService
from udl.errors import UDLError

DEFAULT_WAIT: Final = 120.0
"""Seconds the download tool waits for its downloads, unless the agent says otherwise."""

INSTRUCTIONS: Final = """\
udl downloads media (videos, audio, images, galleries and plain files) from web pages and URLs.

- `download` takes URLs and works out, for each, which downloader can download it (yt-dlp, \
gallery-dl, a plain HTTP download, the page's HTML, or a browser), and remembers what worked \
for the site. It waits for the downloads (at most `timeout` seconds); a download still running \
when it returns can be followed with `get_download`, or stopped with `cancel_download`.
- `probe_url` tells which downloaders can download a URL, without downloading it.
- `get_site` shows what udl knows about a site; `set_site_strategy` tells it which downloader \
(and options) to use there, when it keeps failing or picks the wrong one.
- When no downloader finds the media on a site, teach udl the site with a tag chain: the steps \
from a page to its media, e.g. `iframe#player@src >> video source@src`. `suggest_chain` scans a \
page and suggests chains (best first), `test_chain` tries one (showing what each step found), \
and `set_site_chain` saves it, so `download` follows it from then on. `export_chain` and \
`import_chains` move chains as YAML files.
- A site that needs a login: the user signs in once on their machine, with \
`udl browser login <url>`; udl then sends the site's cookies.
- Automations: events (a download ended, a run ended, `custom.<name>` events of your own: \
`emit_event`) run actions (a script, an operation, a webhook, an event, a notification: \
`set_action`) through subscriptions (`set_subscription`: event types, and a filter on their \
data such as `name == "x" and not quiet`; `list_event_types` gives each type's fields). \
`test_subscription` tries one on recent events; `list_runs` and `get_run` follow what ran; \
`get_status` says which udl runs them (`udl serve`).
- Accounts: integrations (e.g. Gmail) connect any number of named accounts (`gmail:work`, \
`gmail:personal`: `list_accounts`). Each integration's tools take an `account`, which may be \
left out while it has one; tools that list things also take a list of accounts, or "*" for \
every one, and tag each item with its account. `connect_account` gives a page for the user to \
open; `get_account` then shows the account connected.
"""


# The tools' own defaults, where an agent should not get the operation's.
_DEFAULTS: Final[dict[str, dict[str, object]]] = {"download": {"timeout": DEFAULT_WAIT}}


def create_server(
    service: Callable[[], UDLService], operations: OperationRegistry | None = None
) -> MCPServer:
    """The MCP server, whose tools are `operations` (by default, udl's own) and run on
    `service()` (a started service)."""
    server = MCPServer(
        name="udl",
        title="udl",
        version=importlib.metadata.version("udl"),
        instructions=INSTRUCTIONS,
    )
    for op in operations if operations is not None else OperationRegistry.core():
        if op.name == "download" and op.namespace is None:
            tool = _tool(op, DownloadRequest, _download, service)
        else:
            tool = _tool(op, op.request, functools.partial(_call, op), service)
        server.add_tool(
            tool,
            name=op.name,
            title=op.title,
            description=op.description,
            annotations=_annotations(op),
            structured_output=True,
        )
    return server


def _annotations(op: Operation) -> ToolAnnotations:
    """What the tool does to the world, for the agent (and its user) to decide when to call it."""
    hints = op.hints
    return ToolAnnotations(
        title=op.title,
        read_only_hint=hints.read_only,
        destructive_hint=hints.destructive,
        idempotent_hint=hints.idempotent,
        open_world_hint=hints.open_world,
    )


def _tool[Q: BaseModel](
    op: Operation,
    request: type[Q],
    run: Callable[[UDLService, Q, Context], Awaitable[BaseModel]],
    service: Callable[[], UDLService],
) -> Callable[..., Awaitable[BaseModel]]:
    """The tool function for `op`: its parameters are the request's fields (described), so they
    are the tool's arguments, and it returns the response."""

    async def tool(ctx: Context, **arguments: Any) -> BaseModel:
        called = current_caller.set(Caller.MCP)
        try:
            return await run(service(), request.model_validate(arguments), ctx)
        except UDLError as e:
            raise ToolError(_described(e)) from e
        finally:
            current_caller.reset(called)

    defaults = _DEFAULTS.get(op.name, {})
    fields = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[_parameter(field), Field(description=field.description)],
            default=defaults.get(name, _default(field)),
        )
        for name, field in request.model_fields.items()
    ]
    context = inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context)
    # (The SDK reads a tool's arguments from its signature.)
    signature = inspect.Signature([*fields, context], return_annotation=op.response)
    setattr(tool, "__signature__", signature)  # noqa: B010 - (functions do not declare it)
    return tool


def _parameter(field: FieldInfo) -> Any:
    """A request field's type as a tool's parameter.

    The MCP SDK reads a string argument that is JSON (null, true, a list...) as JSON unless its
    parameter is exactly str, so a text field (text, a secret, a path) is given as str, with a
    validator that also takes null and the field's own JSON Schema: "null" stays the text
    "null", and the request model then validates the field as it is.
    """
    annotation = field.rebuild_annotation()  # (its type, with its constraints)
    if not _is_text(field.annotation):
        return annotation
    schema = TypeAdapter[Any](annotation).json_schema()
    return Annotated[str, PlainValidator(_text_or_null), WithJsonSchema(schema)]


def _is_text(annotation: Any) -> bool:
    """Whether a field's type is text in JSON: a str, a secret or a path (or None)."""
    args = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
    if not (isinstance(annotation, types.UnionType) or typing.get_origin(annotation) is Union):
        args = [annotation]
    return len(args) == 1 and args[0] in (str, SecretStr, Path)


def _text_or_null(value: object) -> object:
    if value is None or isinstance(value, str):
        return value
    raise ValueError("Input should be text, or null")


def _default(field: FieldInfo) -> object:
    if field.default_factory is not None:
        return Field(default_factory=field.default_factory)
    return inspect.Parameter.empty if field.is_required() else field.default


def _described(error: UDLError) -> str:
    """A udl error, for an agent: what went wrong (and its code), and what to do about it."""
    described = f"{error.code}: {error.message}"
    return f"{described}\n{error.hint}" if error.hint else described


async def _call(op: Operation, service: UDLService, request: BaseModel, ctx: Context) -> BaseModel:
    return await op.method(service, request)


async def _download(service: UDLService, request: DownloadRequest, ctx: Context) -> BaseModel:
    return await service.download(request, on_event=_Progress(ctx, len(request.urls)))


class _Progress:
    """Reports a download's progress to the agent: each URL is one unit, done in the share its
    download has got through (progress only ever goes up, as MCP asks)."""

    def __init__(self, ctx: Context, urls: int) -> None:
        self._ctx = ctx
        self._total = urls
        self._shares: dict[UUID, float] = {}
        self._sent = 0.0

    async def __call__(self, event: JobEvent) -> None:
        job = event.job
        share = 1.0 if event.type.ends else (job.progress.fraction if job.progress else None)
        self._shares[job.id] = max(self._shares.get(job.id, 0.0), min(share or 0.0, 1.0))
        done = sum(self._shares.values())
        if done > self._sent:
            self._sent = done
            await self._ctx.report_progress(done, self._total, f"{job.url}: {event.type}")
