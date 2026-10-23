"""
--------------
- service.py -
--------------

UDLService: everything udl does, as operations. An operation is an async method that takes one
request model and returns one response model, registered with @operation. The CLI, the API and
the MCP server (Phase 4) only parse a request, call an operation and render its response, so
they offer the same operations with the same validation (the parity test compares the operations
each one offers with operations()).
"""

# System Imports
import asyncio
import contextlib
import functools
import inspect
import logging
import re
import typing
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Self
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

# Package Imports
from pydantic import BaseModel, JsonValue

# Local Imports
from udl.config import DBConnectionConfig, Settings, data_dir
from udl.core.executor import CookieSource, Executor, JobSpec, RetryPolicy, site_cookies
from udl.core.jobs import InstanceLock, JobManager, recover_interrupted
from udl.core.models import (
    ConfigInfo,
    DatabaseInfo,
    DatabaseList,
    Deleted,
    DownloadBatch,
    DownloadList,
    DownloadRequest,
    GetConfigRequest,
    JobEvent,
    JobInfo,
    JobRequest,
    ListDatabasesRequest,
    ListPluginsRequest,
    PluginList,
    PluginStatus,
    ProbeReport,
    ProbeRequest,
    SetStrategyRequest,
    SiteInfo,
    SiteList,
    SiteRequest,
)
from udl.core.resolver import Resolver, SiteBehaviour
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import DownloadQuery, Site, SiteQuery, Strategy, StrategySource
from udl.errors import AuthRequired, DoesNotExist, UDLError
from udl.plugins.downloaders.auth import AuthCookies
from udl.plugins.downloaders.base import Cookie, ProbeContext, ProbeResult
from udl.plugins.registry import DOWNLOADERS, PluginInfo, PluginRegistry

log = logging.getLogger(__name__)

# Settings keys whose values are secrets, in the [tools.<name>] tables.
_SECRET_KEY: Final = re.compile(
    r"pass(word|wd)?|secret|token|cookie|credential|authori[sz]ation|api_?key", re.IGNORECASE
)
_HIDDEN: Final[str] = "***"


# ---------------------------------------------------------------------------------------------
# The operations registry
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Operation:
    """One of udl's operations."""

    name: str  # e.g. "download": the MCP tool's name; the CLI and API name theirs after it
    request: type[BaseModel]
    response: type[BaseModel]
    summary: str  # the first line of its docstring
    description: str  # its docstring, written for people and for AI agents (MCP)
    method: Callable[..., Awaitable[BaseModel]]  # (unbound) UDLService's method


_registered: list[tuple[str, Callable[..., Awaitable[BaseModel]]]] = []


def operation[S, Q: BaseModel, R: BaseModel](
    name: str,
) -> Callable[[Callable[[S, Q], Awaitable[R]]], Callable[[S, Q], Awaitable[R]]]:
    """Register a UDLService method as the operation called `name`."""

    def register(method: Callable[[S, Q], Awaitable[R]]) -> Callable[[S, Q], Awaitable[R]]:
        if any(registered == name for registered, _ in _registered):
            raise ValueError(f"There already is an operation called {name!r}")
        _registered.append((name, method))
        return method

    return register


@functools.cache
def operations() -> dict[str, Operation]:
    """Every operation, by name."""
    found: dict[str, Operation] = {}
    for name, method in _registered:
        hints = typing.get_type_hints(method)
        _, request, *_ = inspect.signature(method).parameters
        doc = inspect.getdoc(method) or ""
        found[name] = Operation(
            name=name,
            request=hints[request],
            response=hints["return"],
            summary=doc.partition("\n")[0],
            description=doc,
            method=method,
        )
    return found


# ---------------------------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------------------------


class UDLService:
    """udl's operations, on the configured databases and plugins.

    Start it (start(), or `async with`) before calling an operation, and close it afterwards:
    closing cancels the downloads still running. Everything it is made of can be passed in
    (tests do); by default it comes from the settings.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        registry: PluginRegistry | None = None,
        databases: DBManager | None = None,
        cookies: CookieSource | None = None,
        site_plugins: Sequence[SiteBehaviour] = (),
        retry: RetryPolicy | None = None,
        lock: InstanceLock | None = None,
        progress_interval: float = 0.25,
    ) -> None:
        self.settings = settings
        if registry is None:
            registry = PluginRegistry.discover(settings.plugin_dirs)
        self.registry = registry
        self.db = databases if databases is not None else DBManager(settings.db, registry)
        self.toolbox = Toolbox(settings, registry)
        self.resolver = Resolver(settings, self.toolbox, site_plugins)
        self.cookies = cookies if cookies is not None else AuthCookies.from_settings(settings)
        self.executor = Executor(
            settings, self.resolver, self.toolbox, cookies=self.cookies, retry=retry
        )
        self.jobs = JobManager(
            self.executor,
            max_concurrent=settings.max_concurrent,
            max_per_site=settings.max_per_site,
            progress_interval=progress_interval,
        )
        self._lock = lock if lock is not None else InstanceLock(data_dir() / "udl.lock")

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def start(self) -> None:
        """Connect to the databases. When no other udl is running, the downloads that a udl
        which stopped left unfinished are marked as interrupted."""
        await self.db.connect()
        try:
            alone = await asyncio.to_thread(self._lock.acquire)
        except OSError as e:
            log.warning(
                "Could not tell whether another udl is running, so the download history is "
                "left as it is: %s",
                e,
            )
            return
        if alone:
            try:
                await recover_interrupted(self.db)
            finally:
                self._lock.share()

    async def close(self) -> None:
        """Cancel the downloads still running, then close the databases."""
        await self.jobs.shutdown()
        await self.db.close()
        self._lock.release()

    async def call(self, name: str, request: BaseModel | Mapping[str, Any]) -> BaseModel:
        """Run the operation called `name` with `request`: its request model, or plain data that
        is validated into one (raising ValidationError)."""
        op = operations().get(name)
        if op is None:
            raise DoesNotExist(
                f"No operation called {name!r}", hint=f"The operations: {', '.join(operations())}."
            )
        if isinstance(request, Mapping):
            request = op.request.model_validate(request)
        elif not isinstance(request, op.request):
            raise TypeError(
                f"The {name} operation takes a {op.request.__name__}, "
                f"not a {type(request).__name__}"
            )
        return await op.method(self, request)

    def events(self, id_: UUID) -> AsyncGenerator[JobEvent]:
        """What happens to a download job, or to every job of a batch, until they have ended
        (see JobManager.events). Raises DoesNotExist."""
        return self.jobs.events(id_)

    # -- downloads -----------------------------------------------------------------------------

    @operation("download")
    async def download(self, request: DownloadRequest) -> DownloadBatch:
        """Download one or more URLs.

        For each URL, udl works out how to download it: the tool the request names, else the
        site plugin's choice, else what worked on the site before, else it tries its downloaders
        in turn (those that recognise the URL first) until one actually downloads it, and
        remembers what worked for next time. A site that wants a login is retried with the
        user's cookies, then in a browser. Each URL is a job; this returns them once they have
        all ended, or at once (still queued) when `wait` is false, or after `timeout` seconds.
        """
        if request.tool is not None:
            self.registry.get(DOWNLOADERS, request.tool)  # (raises PluginError)
        db = self.db.only(request.databases) if request.databases else self.db
        specs = [
            JobSpec(
                url=URL.parse(url),
                tool=request.tool,
                reprobe=request.reprobe,
                output_dir=request.output_dir,
                filename_template=request.filename_template,
                record=request.record,
            )
            for url in dict.fromkeys(request.urls)
        ]
        batch = await self.jobs.submit(specs, db=db)
        if request.wait:
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(request.timeout):
                    await self.jobs.wait(batch.id)
        return self.jobs.batch(batch.id) or batch

    @operation("get_download")
    async def get_download(self, request: JobRequest) -> JobInfo:
        """Show a download: whether it is queued, running (with its progress), finished (with
        the files it wrote) or failed (with why, and every tool it tried)."""
        if (job := self.jobs.get(request.id)) is not None:
            return job
        record = await self.db.get_download(request.id)
        if record is None:
            raise DoesNotExist(
                f"No download {request.id}", hint="`udl history` lists the downloads."
            )
        return JobInfo.from_record(record)

    @operation("list_downloads")
    async def list_downloads(self, request: DownloadQuery) -> DownloadList:
        """List the download history, newest first (optionally for one site, or with one
        status), showing the downloads this udl is running as they are now."""
        records = await self.db.list_downloads(request)
        return DownloadList(
            downloads=[
                self.jobs.get(record.id) or JobInfo.from_record(record) for record in records
            ]
        )

    @operation("cancel_download")
    async def cancel_download(self, request: JobRequest) -> DownloadList:
        """Cancel a download that this udl is running, or every download of a batch, and wait
        for them to stop. Downloads that have ended are left as they are."""
        return DownloadList(downloads=await self.jobs.cancel(request.id))

    # -- probing -------------------------------------------------------------------------------

    @operation("probe_url")
    async def probe(self, request: ProbeRequest) -> ProbeReport:
        """Find out which downloaders can download a URL, and what media they see, without
        downloading anything.

        The downloaders are probed in the order a download would try them (those that recognise
        the URL first) until one can download it, or all of them with `all_tools`, or just
        `tool`. When the site wants a login, the downloader is probed again with the user's
        cookies.
        """
        url = URL.parse(request.url)
        if request.tool is not None:
            self.registry.get(DOWNLOADERS, request.tool)  # (raises PluginError)
            tools = [request.tool]
        else:
            tools = [candidate.tool for candidate in self.resolver.probe_chain(url)]
        cookies: list[Cookie] | None = None  # read once the site wants a login
        results: list[ProbeResult] = []
        for tool in tools:
            result = await self._probe(tool, url, cookies or [])
            if cookies is None and _wants_login(result):
                cookies = await site_cookies(self.cookies, url.value)
                if cookies:
                    result = await self._probe(tool, url, cookies)
            results.append(result)
            if result.ok and not request.all_tools:
                break
        return ProbeReport(
            url=url.value, site_key=url.site_key, results=results, signed_in=bool(cookies)
        )

    async def _probe(self, tool: str, url: URL, cookies: list[Cookie]) -> ProbeResult:
        try:
            downloader = self.toolbox.get(tool)
        except UDLError as e:  # its options are invalid
            return ProbeResult.failed(tool, e)
        return await downloader.probe(ProbeContext(url=url.value, cookies=cookies))

    # -- sites ---------------------------------------------------------------------------------

    @operation("list_sites")
    async def list_sites(self, request: SiteQuery) -> SiteList:
        """List the sites udl knows (it has downloaded from them, or has a strategy for them),
        by key, optionally only those whose key or name contains `search`."""
        return SiteList(sites=await self.db.list_sites(request))

    @operation("get_site")
    async def get_site(self, request: SiteRequest) -> SiteInfo:
        """Show what udl knows about a site: its settings, and its strategies (the downloaders
        that work on it, with how often they have worked and failed)."""
        site = await self.db.get_site(request.site)
        if site is None:
            raise DoesNotExist(
                f"udl knows nothing about {request.site}",
                hint="Download something from it, or set a strategy for it (`udl sites set`).",
            )
        return SiteInfo(site=site, strategies=await self.db.strategies_for(site.key))

    @operation("set_site_strategy")
    async def set_site_strategy(self, request: SetStrategyRequest) -> Strategy:
        """Tell udl which downloader (and options) to use for a site, or only for its URLs
        whose path matches `pattern`.

        The strategy is manual: udl tries it before what it has learned, and never replaces it
        when it fails. Setting the same tool and pattern again changes that strategy.
        """
        self.toolbox.get(request.tool, request.options)  # (raises PluginError or ConfigError)
        key = request.site
        await self.db.upsert_site(await self.db.get_site(key) or Site(key=key))
        same = next(
            (
                strategy
                for strategy in await self.db.strategies_for(key)
                if strategy.url_pattern == request.pattern and strategy.tool == request.tool
            ),
            None,
        )
        strategy = same or Strategy(site_key=key, tool=request.tool, source=StrategySource.MANUAL)
        return await self.db.upsert_strategy(
            strategy.model_copy(
                update={
                    "url_pattern": request.pattern,
                    "options": request.options,
                    "priority": request.priority,
                    "requires_auth": request.requires_auth,
                    "source": StrategySource.MANUAL,
                    "enabled": True,
                }
            )
        )

    @operation("delete_site")
    async def delete_site(self, request: SiteRequest) -> Deleted:
        """Forget a site and its strategies (its download history is kept)."""
        return Deleted(deleted=await self.db.delete_site(request.site))

    # -- udl itself ----------------------------------------------------------------------------

    @operation("list_databases")
    async def list_databases(self, request: ListDatabasesRequest) -> DatabaseList:
        """List the database connections udl uses (with any password hidden), in the order it
        consults them, and whether each is connected and answering."""
        return DatabaseList(
            databases=[await self._database(connection) for connection in self.db.connections]
        )

    async def _database(self, connection: DBConnectionConfig) -> DatabaseInfo:
        connected = self.db.is_connected(connection.name)
        healthy = False
        if connected:
            try:
                healthy = await self.db.database(connection.name).health()
            except Exception:  # (logged with its traceback)
                log.warning(
                    "The %r database failed its health check", connection.name, exc_info=True
                )
        return DatabaseInfo(
            name=connection.name,
            plugin=connection.plugin,
            url=hide_password(connection.url),
            primary=connection.name == self.db.primary,
            priority=connection.priority,
            read_only=connection.read_only,
            connected=connected,
            healthy=healthy,
        )

    @operation("list_plugins")
    async def list_plugins(self, request: ListPluginsRequest) -> PluginList:
        """List udl's plugins (database engines and downloaders: built in, installed or local
        files) and whether each downloader can run, with what to install for those that cannot.
        Plugins that failed to load are listed as problems."""
        return PluginList(
            plugins=[self._plugin(info) for info in self.registry.plugins()],
            problems=self.registry.problems,
        )

    def _plugin(self, info: PluginInfo) -> PluginStatus:
        status = PluginStatus.model_validate(info.model_dump())
        if info.kind != DOWNLOADERS.group:
            return status
        try:
            downloader = self.toolbox.get(info.name)
        except UDLError as e:  # its options are invalid
            return status.model_copy(update={"available": False, "problem": e.message})
        missing = downloader.missing()
        return status.model_copy(update={"available": not missing, "missing": missing})

    @operation("get_config")
    async def get_config(self, request: GetConfigRequest) -> ConfigInfo:
        """Show the settings udl runs with (from its config file, UDL_* environment variables
        and the defaults), with secrets hidden."""
        return ConfigInfo(settings=_without_secrets(self.settings))


def _wants_login(result: ProbeResult) -> bool:
    return result.error is not None and result.error.code is AuthRequired.code


# ---------------------------------------------------------------------------------------------
# Hiding secrets
# ---------------------------------------------------------------------------------------------


def hide_password(url: str) -> str:
    """`url` with the password in it (if any) hidden."""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    userinfo, _, host = parts.netloc.rpartition("@")
    user = userinfo.partition(":")[0]
    return urlunsplit(parts._replace(netloc=f"{user}:{_HIDDEN}@{host}"))


def _hide_in(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return _hidden(value)
    if isinstance(value, list):
        return [_hide_in(item) for item in value]
    return value


def _hidden(table: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """A settings table with the values of keys that name secrets (passwords, tokens, cookies,
    keys...) hidden, at any depth."""
    return {
        key: _HIDDEN if _SECRET_KEY.search(key) else _hide_in(value) for key, value in table.items()
    }


def _without_secrets(settings: Settings) -> Settings:
    """The settings to show: the API key is a SecretStr (hidden when shown already), database
    URLs lose their passwords and the [tools.<name>] tables their secrets."""
    connections = [
        connection.model_copy(update={"url": hide_password(connection.url)})
        for connection in settings.db.connections
    ]
    return settings.model_copy(
        update={
            "db": settings.db.model_copy(update={"connections": connections}),
            "tools": {name: _hidden(table) for name, table in settings.tools.items()},
        }
    )
