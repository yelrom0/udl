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
dir the agent names must be in api.allowed_output_roots. udl's errors are tool errors that say
what to do next.
"""

# System Imports
import functools
import importlib.metadata
import inspect
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Final
from uuid import UUID

# Package Imports
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

# Local Imports
from udl.core.models import DownloadRequest, JobEvent
from udl.core.operations import Operation, OperationRegistry
from udl.core.service import UDLService
from udl.errors import UDLError
from udl.interfaces.security import check_output_dir

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
        try:
            return await run(service(), request.model_validate(arguments), ctx)
        except UDLError as e:
            raise ToolError(_described(e)) from e

    defaults = _DEFAULTS.get(op.name, {})
    fields = [
        inspect.Parameter(
            name,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=Annotated[
                field.annotation, *field.metadata, Field(description=field.description)
            ],
            default=defaults.get(name, _default(field)),
        )
        for name, field in request.model_fields.items()
    ]
    context = inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context)
    # (The SDK reads a tool's arguments from its signature.)
    signature = inspect.Signature([*fields, context], return_annotation=op.response)
    setattr(tool, "__signature__", signature)  # noqa: B010 - (functions do not declare it)
    return tool


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
    check_output_dir(service.settings, request.output_dir)
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
