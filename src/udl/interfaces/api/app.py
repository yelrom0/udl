"""
----------
- app.py -
----------

The HTTP API (FastAPI): a route for every operation (planning/01-core.md's Feature Matrix, and
one made for each operation a plugin adds: udl.interfaces.adapters), named after it (its
operationId), and a download's events as server-sent events. The MCP server is at
/mcp (streamable HTTP), on the same service. `udl serve` runs it.

udl's errors are answered as ErrorInfo, with a status that fits (404 for what does not exist,
400 for a tool or options that cannot work...). When api.api_key is set, every route but the
docs (/docs, /openapi.json) and OAuth's /accounts/callback (checked by its one-time state) needs
it, in the X-API-Key header, /mcp too. Served on localhost (the
default), it answers only requests for localhost: a web page that points its own name at
127.0.0.1 (DNS rebinding) sends that name as the Host, and is refused.
"""

# System Imports
import importlib.metadata
import ipaddress
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated, Final
from urllib.parse import urlsplit

# Package Imports
from fastapi import FastAPI, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.datastructures import Headers
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

# Local Imports
from udl.automations.runtime import AutomationRuntime
from udl.config import Settings
from udl.core.operations import Caller, current_caller
from udl.core.service import ServiceFactory, UDLService
from udl.errors import ErrorCode, ErrorInfo, UDLError, Unauthorized
from udl.integrations.accounts import CALLBACK_PATH
from udl.interfaces.adapters import add_routes
from udl.interfaces.api.routes import accounts, chains, downloads, events, sites, system
from udl.interfaces.mcp.server import create_server
from udl.interfaces.security import API_KEY_HEADER, check_api_key

log = logging.getLogger(__name__)

_STATUS: Final[dict[ErrorCode, int]] = {
    ErrorCode.DOES_NOT_EXIST: 404,
    ErrorCode.NOT_ALLOWED: 403,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.PLUGIN_ERROR: 400,  # (the request names a tool udl does not have)
    ErrorCode.CONFIG_ERROR: 400,  # (or options the tool does not take)
    ErrorCode.DATABASE_ERROR: 503,
    ErrorCode.DUPLICATE_KEY: 409,  # (a name another record has)
    ErrorCode.SECRET_STORE_ERROR: 503,
    ErrorCode.TEMPLATE_FAILED: 400,
    ErrorCode.REAUTH_REQUIRED: 409,  # (the account needs connecting again)
    ErrorCode.INVALID_FILTER: 400,
    ErrorCode.ALREADY_EXISTS: 409,
    ErrorCode.IN_USE: 409,  # (an action a subscription runs)
}

_LOCAL_HOSTS: Final = frozenset({"localhost"})

MCP_PATH: Final = "/mcp"

_DESCRIPTION: Final = """\
udl works out how to download a URL (yt-dlp, gallery-dl, a plain HTTP download, the page's
HTML, or a browser) and remembers what worked for the site.

Every operation is also a command (`udl --help`) and an MCP tool (at /mcp, and `udl mcp`).
"""

_api_key = APIKeyHeader(
    name=API_KEY_HEADER,
    auto_error=False,
    description="The server's API key (api.api_key in its config), when it has one.",
)


async def require_api_key(request: Request, key: Annotated[str | None, Security(_api_key)]) -> None:
    check_api_key(request.app.state.settings, key)


def create_app(settings: Settings, *, make_service: ServiceFactory = UDLService) -> FastAPI:
    """The API, on a service made from `settings` (started and closed by the app's lifespan).
    The service is made first: the operations its plugins add are routes too."""

    service = make_service(settings)
    base = settings.web.public_url or f"http://127.0.0.1:{settings.api.port}"
    service.accounts.callback_url = f"{base.rstrip('/')}{CALLBACK_PATH}"  # (OAuth's answers)
    mcp = create_server(lambda: service, service.operations)
    mcp_app = mcp.streamable_http_app(streamable_http_path=MCP_PATH, host=settings.api.host)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with service, mcp.session_manager.run():
            if not settings.automations.enabled:
                yield
                return
            async with AutomationRuntime(service):  # (unless [automations] enabled = false)
                yield

    app = FastAPI(
        title="udl",
        version=importlib.metadata.version("udl"),
        description=_DESCRIPTION,
        lifespan=lifespan,
        dependencies=[Security(require_api_key)],
    )
    app.state.settings = settings
    app.state.service = service
    # (The event stream before the generated routes: /events/stream is not /events/{id}.)
    for router in (downloads.router, sites.router, chains.router, system.router, events.router):
        app.include_router(router)
    add_routes(app, service.operations)  # (the operations plugins add)
    # (A route rather than a mount, which would answer only /mcp/...)
    app.router.routes.append(Route(MCP_PATH, KeyRequired(mcp_app, settings)))
    # (Before /accounts/{account}; and a route of Starlette's, so no API key is asked for: the
    # authorization's one-time state is what it is checked by.)
    app.router.routes.insert(0, Route(CALLBACK_PATH, accounts.callback, methods=["GET"]))

    @app.exception_handler(UDLError)
    async def udl_error(request: Request, error: UDLError) -> JSONResponse:
        status = _STATUS.get(error.code, 500)
        headers = {"WWW-Authenticate": API_KEY_HEADER} if status == 401 else None
        return JSONResponse(error.to_info().model_dump(mode="json"), status, headers)

    @app.exception_handler(Exception)
    async def bug(request: Request, error: Exception) -> JSONResponse:
        # (The details go to the log, not in the answer.)
        log.error("%s %s failed", request.method, request.url.path, exc_info=error)
        failed = ErrorInfo(code=ErrorCode.UDL_ERROR, message="udl failed; its log says why")
        return JSONResponse(failed.model_dump(mode="json"), 500)

    app.add_middleware(CalledThroughTheAPI)
    if is_local(settings.api.host):
        app.add_middleware(LocalHostsOnly)
    return app


# ---------------------------------------------------------------------------------------------
# Who is answered
# ---------------------------------------------------------------------------------------------


class KeyRequired:
    """An ASGI app (the MCP server) behind the API key, when the server has one."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            check_api_key(self.settings, Headers(scope=scope).get(API_KEY_HEADER))
        except Unauthorized as e:
            refused = JSONResponse(
                e.to_info().model_dump(mode="json"), 401, {"WWW-Authenticate": API_KEY_HEADER}
            )
            await refused(scope, receive, send)
            return
        await self.app(scope, receive, send)


def is_local(host: str) -> bool:
    """Whether `host` (a name or an address, without a port) is this machine's loopback."""
    if host in _LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class CalledThroughTheAPI:
    """Marks the operations a request runs as the API's (current_caller), in the request's own
    task (a plain ASGI middleware, unlike Starlette's BaseHTTPMiddleware)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        called = current_caller.set(Caller.API)
        try:
            await self.app(scope, receive, send)
        finally:
            current_caller.reset(called)


class LocalHostsOnly:
    """Answers only requests whose Host header is a loopback name or address (421 otherwise)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            host = Headers(scope=scope).get("host", "")
            name = urlsplit(f"//{host}").hostname or ""
            if not is_local(name):
                refused = ErrorInfo(
                    code=ErrorCode.NOT_ALLOWED,
                    message=f"This udl answers only requests for localhost, not {host!r}",
                    hint="To serve other machines, set api.host (or `udl serve --host`) to "
                    "their network's address, and an api.api_key.",
                )
                response = JSONResponse(refused.model_dump(mode="json"), 421)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
