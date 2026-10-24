"""
-------------------
- test_service.py -
-------------------

UDLService: everything udl does, as operations that take a request model and return a response
model. The CLI, the API and the MCP server only call these (Phase 4).
"""

# System Imports
import asyncio
import json
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

# Package Imports
import pytest
from pydantic import BaseModel, SecretStr, ValidationError

# Local Imports
from support.downloaders import FakeDownloader, FakeMedia
from support.engine import (
    StaticCookies,
    connection,
    cookie,
    databases,
    registry,
    script,
    settings,
)
from support.integrations import FakeIntegration
from support.sites import FakeSitePlugin, fake_site
from udl.chains.models import TagChain
from udl.config import APISettings, SecretsSettings, Settings
from udl.core.executor import RetryPolicy
from udl.core.jobs import InstanceLock
from udl.core.models import (
    ConfigInfo,
    DownloadBatch,
    DownloadRequest,
    GetConfigRequest,
    JobEvent,
    JobEventType,
    JobRequest,
    ListDatabasesRequest,
    ListPluginsRequest,
    ProbeRequest,
    SetStrategyRequest,
    SiteRequest,
    SyncDatabasesRequest,
)
from udl.core.operations import operation, operations
from udl.core.service import UDLService
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import (
    DownloadQuery,
    DownloadRecord,
    JobStatus,
    Site,
    SiteQuery,
    Strategy,
    StrategySource,
)
from udl.errors import (
    ConfigError,
    DatabaseError,
    DoesNotExist,
    ErrorCode,
    NotAllowed,
    PluginError,
)
from udl.plugins.databases.memory import MemoryDatabase
from udl.plugins.registry import (
    DATABASES,
    DOWNLOADERS,
    INTEGRATIONS,
    SECRETS,
    SITES,
    PluginRegistry,
)
from udl.plugins.requirements import Requirement
from udl.plugins.secrets.keyring import KeyringSecretStore
from udl.plugins.sites.base import SitePlugin
from udl.plugins.sites.youtube import YouTubePlugin

A = "https://example.com/a"
B = "https://example.com/b"
LIST = "https://example.com/list"
SLOW = FakeMedia(content=b"x" * 2000, delay=0.02)

type Make = Callable[..., Awaitable[UDLService]]


@pytest.fixture
async def make(tmp_path: Path) -> AsyncIterator[Make]:
    """Makes started services (closed at the end of the test), on fakes unless told otherwise."""
    services: list[UDLService] = []

    async def make(
        config: Settings | None = None,
        *,
        plugins: PluginRegistry | None = None,
        cookies: StaticCookies | None = None,
        db: DBManager | None = None,
        lock: InstanceLock | None = None,
        site_plugins: Sequence[SitePlugin] | None = None,
    ) -> UDLService:
        config = config or settings(tmp_path)
        service = UDLService(
            config,
            registry=plugins or registry(),
            databases=db,
            cookies=cookies or StaticCookies(),
            site_plugins=site_plugins,
            retry=RetryPolicy(retries=0),
            lock=lock or InstanceLock(tmp_path / "udl.lock"),
            progress_interval=0,
        )
        await service.start()
        services.append(service)
        return service

    yield make
    for service in services:
        await service.close()


def media_settings(tmp_path: Path, **fields: Any) -> Settings:
    files = {A: FakeMedia(content=b"a", filename="a.mp4"), B: FakeMedia(content=b"b", filename="b")}
    return settings(tmp_path, tools={"http": script(files)}, **fields)


def slow_settings(tmp_path: Path) -> Settings:
    return settings(
        tmp_path,
        probe_order=["http"],
        tools={"http": {**script({A: SLOW, B: SLOW}), "chunk_size": 20}},
    )


# ---------------------------------------------------------------------------------------------
# The operations
# ---------------------------------------------------------------------------------------------


def test_the_operations_are_the_feature_matrix_s() -> None:
    # planning/01-core.md's Feature Matrix (syncing databases comes in Phase 7), then
    # planning.md's (plugin settings in Phase 8, the automations in Phases 9 and 10, accounts
    # in Phase 11).
    assert set(operations()) == {
        "download",
        "get_download",
        "list_downloads",
        "cancel_download",
        "probe_url",
        "list_sites",
        "get_site",
        "set_site_strategy",
        "delete_site",
        "suggest_chain",
        "test_chain",
        "set_site_chain",
        "import_chains",
        "export_chain",
        "list_databases",
        "sync_databases",
        "list_plugins",
        "get_plugin_settings",
        "set_plugin_settings",
        "get_config",
        "list_event_types",
        "list_events",
        "get_event",
        "emit_event",
        "replay_event",
        "list_subscriptions",
        "get_subscription",
        "set_subscription",
        "test_subscription",
        "delete_subscription",
        "list_actions",
        "list_scripts",
        "get_action",
        "set_action",
        "run_action",
        "delete_action",
        "list_runs",
        "get_run",
        "cancel_run",
        "get_status",
        "list_schedules",
        "get_schedule",
        "set_schedule",
        "run_schedule",
        "preview_schedule",
        "delete_schedule",
        "connect_account",
    }


def test_every_operation_takes_a_request_model_and_returns_a_response_model() -> None:
    for name, op in operations().items():
        assert op.name == name
        assert issubclass(op.request, BaseModel)
        assert issubclass(op.response, BaseModel)
        assert op.summary
        assert op.description.startswith(op.summary)


def test_an_operation_name_can_be_registered_once() -> None:
    async def another(service: UDLService, request: DownloadRequest) -> DownloadBatch:
        raise AssertionError

    with pytest.raises(ValueError, match="download"):
        operation("download", title="Another")(another)
    assert operations()["download"].method is UDLService.download


def test_every_request_field_is_described() -> None:
    # The descriptions are the API's docs and what an AI agent reads about each MCP tool argument.
    for op in operations().values():
        for name, field in op.request.model_fields.items():
            assert field.description, f"{op.request.__name__}.{name} has no description"


def test_help_keeps_its_placeholders() -> None:
    # The CLI shows help as Markdown, where a bare <name> is an HTML tag, and vanishes: in help
    # (an operation's description, its fields'), placeholders go in `code`.
    bare = re.compile(r"<[A-Za-z]")
    for op in operations().values():
        texts = [op.description] + [
            field.description or "" for field in op.request.model_fields.values()
        ]
        for text in texts:
            outside_code = re.sub(r"`[^`]*`", "", text)
            assert not bare.search(outside_code), f"{op.name}: {text!r}"


def test_the_download_operation_s_models() -> None:
    op = operations()["download"]

    assert (op.request, op.response) == (DownloadRequest, DownloadBatch)


async def test_an_operation_can_be_called_by_name(tmp_path: Path, make: Make) -> None:
    service = await make(media_settings(tmp_path))

    batch = await service.call("download", DownloadRequest(urls=[A]))

    assert isinstance(batch, DownloadBatch)
    assert batch.jobs[0].status is JobStatus.FINISHED


async def test_a_call_by_name_validates_plain_data_into_the_request(
    tmp_path: Path, make: Make
) -> None:
    service = await make(media_settings(tmp_path))

    batch = await service.call("download", {"urls": ["example.com/a?utm_source=x"]})

    assert isinstance(batch, DownloadBatch)
    assert batch.jobs[0].url == A


async def test_a_call_by_name_checks_the_request(make: Make) -> None:
    service = await make()

    with pytest.raises(ValidationError):
        await service.call("download", {"urls": []})
    with pytest.raises(TypeError, match="DownloadRequest"):
        await service.call("download", SiteRequest(site="example.com"))
    with pytest.raises(DoesNotExist, match="nope"):
        await service.call("nope", GetConfigRequest())


# ---------------------------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"urls": []},
        {"urls": ["ftp://example.com/a"]},
        {"urls": [A], "filename_template": "../{title}"},
        {"urls": [A], "databases": []},
        {"urls": [A], "timeout": 0},
        {"urls": [A], "no_such_field": True},
    ],
)
def test_a_bad_download_request_is_rejected(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        DownloadRequest.model_validate(fields)


async def test_download_waits_and_returns_what_came_of_each_url(tmp_path: Path, make: Make) -> None:
    service = await make(media_settings(tmp_path))

    batch = await service.download(DownloadRequest(urls=[A, B]))

    assert [job.status for job in batch.jobs] == [JobStatus.FINISHED, JobStatus.FINISHED]
    assert [[p.read_bytes() for p in job.files] for job in batch.jobs] == [[b"a"], [b"b"]]
    assert {job.batch_id for job in batch.jobs} == {batch.id}


async def test_a_url_given_twice_is_downloaded_once(tmp_path: Path, make: Make) -> None:
    service = await make(media_settings(tmp_path))

    batch = await service.download(DownloadRequest(urls=[A, f"{A}?utm_source=x", "example.com/a"]))

    assert [job.url for job in batch.jobs] == [A]


async def test_a_site_plugin_turns_a_url_into_the_urls_to_download(
    tmp_path: Path, make: Make
) -> None:
    plugin = fake_site(urls={LIST: [A, B]})
    service = await make(media_settings(tmp_path), site_plugins=[plugin])

    batch = await service.download(DownloadRequest(urls=[LIST]))

    assert [(job.url, job.status) for job in batch.jobs] == [
        (A, JobStatus.FINISHED),
        (B, JobStatus.FINISHED),
    ]


async def test_urls_a_site_plugin_gives_twice_are_downloaded_once(
    tmp_path: Path, make: Make
) -> None:
    plugin = fake_site(urls={LIST: [A, B], B: [A]})
    service = await make(media_settings(tmp_path), site_plugins=[plugin])

    batch = await service.download(DownloadRequest(urls=[B, LIST, A]))

    assert [job.url for job in batch.jobs] == [A, B]


async def test_the_registry_s_site_plugins_are_used(tmp_path: Path, make: Make) -> None:
    class Lists(FakeSitePlugin):
        name = "lists"

        async def transform_urls(self, url: URL) -> list[URL]:
            return [URL.parse(A), URL.parse(B)] if url.value == LIST else [url]

    plugins = registry()
    plugins.register(SITES, Lists)
    service = await make(media_settings(tmp_path), plugins=plugins)

    batch = await service.download(DownloadRequest(urls=[LIST]))

    assert [job.url for job in batch.jobs] == [A, B]


async def test_download_without_waiting_returns_the_queued_jobs(tmp_path: Path, make: Make) -> None:
    service = await make(media_settings(tmp_path))

    batch = await service.download(DownloadRequest(urls=[A], wait=False))

    assert [job.status for job in batch.jobs] == [JobStatus.QUEUED]
    events = [event async for event in service.events(batch.id)]
    assert events[-1].type is JobEventType.FINISHED


async def test_download_waits_no_longer_than_its_timeout(tmp_path: Path, make: Make) -> None:
    service = await make(slow_settings(tmp_path))

    batch = await service.download(DownloadRequest(urls=[A], timeout=0.05))

    assert [job.status for job in batch.jobs] == [JobStatus.RUNNING]


class Heard:
    """An on_event that keeps the events it hears."""

    def __init__(self) -> None:
        self.events: list[JobEvent] = []

    async def __call__(self, event: JobEvent) -> None:
        self.events.append(event)


async def test_download_tells_on_event_what_happens_while_it_waits(
    tmp_path: Path, make: Make
) -> None:
    service = await make(media_settings(tmp_path))
    heard = Heard()

    batch = await service.download(DownloadRequest(urls=[A, B]), on_event=heard)

    assert [job.status for job in batch.jobs] == [JobStatus.FINISHED] * 2
    for url in (A, B):
        assert [e.type for e in heard.events if e.job.url == url and e.type.ends] == [
            JobEventType.FINISHED
        ]
    assert heard.events[0].type is JobEventType.QUEUED


async def test_download_stops_telling_on_event_once_it_stops_waiting(
    tmp_path: Path, make: Make
) -> None:
    service = await make(slow_settings(tmp_path))
    heard = Heard()

    batch = await service.download(DownloadRequest(urls=[A], timeout=0.05), on_event=heard)
    told = len(heard.events)
    await asyncio.sleep(0.1)

    assert batch.jobs[0].status is JobStatus.RUNNING
    assert told > 0
    assert len(heard.events) == told


async def test_download_tells_on_event_nothing_when_it_does_not_wait(
    tmp_path: Path, make: Make
) -> None:
    service = await make(media_settings(tmp_path))
    heard = Heard()

    batch = await service.download(DownloadRequest(urls=[A], wait=False), on_event=heard)
    await service.jobs.wait(batch.id)

    assert heard.events == []


async def test_download_passes_the_request_on(tmp_path: Path, make: Make) -> None:
    config = settings(tmp_path, tools={"scraper": script({A: "a"})})
    service = await make(config)

    batch = await service.download(
        DownloadRequest(urls=[A], tool="scraper", output_dir=tmp_path / "out", record=False)
    )

    [job] = batch.jobs
    assert (job.tool, job.output_dir, job.strategy_id) == ("scraper", tmp_path / "out", None)
    assert (await service.list_downloads(DownloadQuery())).downloads == []


async def test_download_with_an_unknown_tool_is_a_plugin_error_before_anything_runs(
    make: Make,
) -> None:
    service = await make()

    with pytest.raises(PluginError, match="nope"):
        await service.download(DownloadRequest(urls=[A], tool="nope"))

    assert (await service.list_downloads(DownloadQuery())).downloads == []


async def test_download_can_be_kept_to_some_databases(tmp_path: Path, make: Make) -> None:
    config = media_settings(
        tmp_path, connections=[connection("local", primary=True), connection("other", priority=1)]
    )
    service = await make(config)

    await service.download(DownloadRequest(urls=[A], databases=["other"]))

    other, local = service.db.database("other"), service.db.database("local")
    assert len(await other.list_downloads(DownloadQuery())) == 1
    assert len(await other.strategies_for("example.com")) == 1
    assert await local.list_downloads(DownloadQuery()) == []
    assert await local.strategies_for("example.com") == []


async def test_download_with_an_unknown_database_does_not_exist(make: Make) -> None:
    service = await make()

    with pytest.raises(DoesNotExist, match="nope"):
        await service.download(DownloadRequest(urls=[A], databases=["nope"]))


async def test_get_download_shows_a_job_as_it_runs(tmp_path: Path, make: Make) -> None:
    service = await make(slow_settings(tmp_path))
    batch = await service.download(DownloadRequest(urls=[A], wait=False))

    job = await service.get_download(JobRequest(id=batch.jobs[0].id))

    assert job.batch_id == batch.id
    assert job.status in (JobStatus.QUEUED, JobStatus.RUNNING)


async def test_get_download_finds_a_download_from_the_history(tmp_path: Path, make: Make) -> None:
    service = await make()
    record = DownloadRecord(url=A, site_key="example.com", status=JobStatus.FINISHED, tool="http")
    await service.db.add_download(record)

    job = await service.get_download(JobRequest(id=record.id))

    assert (job.id, job.batch_id, job.tool) == (record.id, None, "http")


async def test_get_download_of_an_unknown_id_does_not_exist(make: Make) -> None:
    service = await make()

    with pytest.raises(DoesNotExist):
        await service.get_download(JobRequest(id=uuid.uuid4()))


async def test_list_downloads_lists_the_history_newest_first(tmp_path: Path, make: Make) -> None:
    service = await make(media_settings(tmp_path))
    await service.download(DownloadRequest(urls=[A]))
    await service.download(DownloadRequest(urls=[B]))
    old = DownloadRecord(url=A, site_key="other.com", status=JobStatus.FAILED)
    await service.db.add_download(
        old.model_copy(update={"started_at": old.started_at.replace(year=2000)})
    )

    everything = await service.list_downloads(DownloadQuery())
    failed = await service.list_downloads(DownloadQuery(status=JobStatus.FAILED))

    assert [job.url for job in everything.downloads] == [B, A, A]
    assert everything.downloads[0].batch_id is not None  # run by this udl: shown live
    assert [job.site_key for job in failed.downloads] == ["other.com"]


async def test_cancel_download_cancels_a_running_download(tmp_path: Path, make: Make) -> None:
    service = await make(slow_settings(tmp_path))
    batch = await service.download(DownloadRequest(urls=[A, B], wait=False))

    cancelled = await service.cancel_download(JobRequest(id=batch.id))

    assert [job.status for job in cancelled.downloads] == [JobStatus.CANCELLED] * 2


async def test_cancelling_a_download_that_has_ended_leaves_it_as_it_is(make: Make) -> None:
    service = await make()
    record = DownloadRecord(url=A, site_key="example.com", status=JobStatus.FINISHED)
    await service.db.add_download(record)

    cancelled = await service.cancel_download(JobRequest(id=record.id))

    assert [(job.id, job.status) for job in cancelled.downloads] == [
        (record.id, JobStatus.FINISHED)
    ]


async def test_cancelling_a_download_another_udl_runs_says_where_to_cancel_it(
    make: Make,
) -> None:
    service = await make()
    record = DownloadRecord(url=A, site_key="example.com", status=JobStatus.RUNNING)
    await service.db.add_download(record)

    with pytest.raises(DoesNotExist, match="another udl") as raised:
        await service.cancel_download(JobRequest(id=record.id))

    assert raised.value.hint is not None


async def test_cancelling_an_unknown_download_does_not_exist(make: Make) -> None:
    service = await make()

    with pytest.raises(DoesNotExist):
        await service.cancel_download(JobRequest(id=uuid.uuid4()))


async def test_events_of_an_unknown_id_do_not_exist(make: Make) -> None:
    service = await make()

    with pytest.raises(DoesNotExist):
        service.events(uuid.uuid4())


# ---------------------------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------------------------


async def test_probing_stops_at_the_first_tool_that_can_download_the_url(
    tmp_path: Path, make: Make
) -> None:
    service = await make(settings(tmp_path, tools={"yt-dlp": script({A: "video"})}))

    report = await service.probe(ProbeRequest(url="example.com/a?fbclid=1"))

    assert (report.url, report.site_key, report.signed_in) == (A, "example.com", False)
    assert [(r.tool, r.ok) for r in report.results] == [("http", False), ("yt-dlp", True)]


async def test_probing_every_tool(tmp_path: Path, make: Make) -> None:
    service = await make(settings(tmp_path, tools={"yt-dlp": script({A: "video"})}))

    report = await service.probe(ProbeRequest(url=A, all_tools=True))

    assert [r.tool for r in report.results] == [
        "http",
        "yt-dlp",
        "gallery-dl",
        "scraper",
        "browser",
    ]


async def test_probing_one_tool(tmp_path: Path, make: Make) -> None:
    service = await make(settings(tmp_path, tools={"scraper": script({A: "page"})}))

    report = await service.probe(ProbeRequest(url=A, tool="scraper"))

    assert [(r.tool, r.ok) for r in report.results] == [("scraper", True)]


async def test_probing_with_an_unknown_tool_is_a_plugin_error(make: Make) -> None:
    service = await make()

    with pytest.raises(PluginError):
        await service.probe(ProbeRequest(url=A, tool="nope"))


async def test_a_tool_with_invalid_options_probes_as_a_failure(tmp_path: Path, make: Make) -> None:
    service = await make(settings(tmp_path, tools={"http": {"nope": 1}}))

    report = await service.probe(ProbeRequest(url=A, tool="http"))

    [result] = report.results
    assert result.error is not None
    assert result.error.code is ErrorCode.CONFIG_ERROR


async def test_a_site_that_wants_a_login_is_probed_again_with_its_cookies(
    tmp_path: Path, make: Make
) -> None:
    members = FakeMedia(content=b"m", cookie="session=signed-in")
    config = settings(tmp_path, tools={"http": script({A: members})})
    service = await make(config, cookies=StaticCookies([cookie()]))

    report = await service.probe(ProbeRequest(url=A))

    assert report.signed_in is True
    assert [(r.tool, r.ok) for r in report.results] == [("http", True)]


async def test_a_site_that_wants_a_login_without_cookies_reports_it(
    tmp_path: Path, make: Make
) -> None:
    members = FakeMedia(content=b"m", cookie="session=signed-in")
    service = await make(
        settings(tmp_path, probe_order=["http"], tools={"http": script({A: members})})
    )

    report = await service.probe(ProbeRequest(url=A))

    [result] = report.results
    assert result.error is not None
    assert result.error.code is ErrorCode.AUTH_REQUIRED
    assert report.signed_in is False


# ---------------------------------------------------------------------------------------------
# Sites
# ---------------------------------------------------------------------------------------------


async def test_setting_a_strategy_stores_the_site_and_a_manual_strategy(make: Make) -> None:
    service = await make()

    strategy = await service.set_site_strategy(
        SetStrategyRequest(
            site="https://www.example.com/watch", tool="yt-dlp", options={"confidence": 0.5}
        )
    )

    info = await service.get_site(SiteRequest(site="example.com"))
    assert info.site.key == "example.com"
    assert info.strategies == [strategy]
    assert (strategy.tool, strategy.source, strategy.options) == (
        "yt-dlp",
        StrategySource.MANUAL,
        {"confidence": 0.5},
    )


async def test_setting_the_same_tool_and_pattern_again_changes_that_strategy(make: Make) -> None:
    service = await make()
    first = await service.set_site_strategy(
        SetStrategyRequest(site="example.com", tool="yt-dlp", pattern="^/shorts/")
    )

    second = await service.set_site_strategy(
        SetStrategyRequest(site="example.com", tool="yt-dlp", pattern="^/shorts/", priority=-1)
    )
    other = await service.set_site_strategy(SetStrategyRequest(site="example.com", tool="yt-dlp"))

    info = await service.get_site(SiteRequest(site="example.com"))
    assert second.id == first.id
    assert second.priority == -1
    assert other.id != first.id
    assert len(info.strategies) == 2


async def test_setting_a_strategy_takes_over_a_learned_one(tmp_path: Path, make: Make) -> None:
    service = await make(media_settings(tmp_path))
    await service.download(DownloadRequest(urls=[A]))
    [learned] = (await service.get_site(SiteRequest(site="example.com"))).strategies

    strategy = await service.set_site_strategy(
        SetStrategyRequest(site="example.com", tool="http", requires_auth=True)
    )

    assert strategy.id == learned.id
    assert (strategy.source, strategy.requires_auth, strategy.success_count) == (
        StrategySource.MANUAL,
        True,
        1,
    )


async def test_setting_a_strategy_leaves_the_site_s_chain_for_that_tool(make: Make) -> None:
    service = await make()
    await service.db.upsert_site(Site(key="example.com"))
    chained = await service.db.upsert_strategy(
        Strategy(
            site_key="example.com",
            tool="scraper",
            chain=TagChain.model_validate("video@src"),
            source=StrategySource.MANUAL,
        )
    )

    strategy = await service.set_site_strategy(
        SetStrategyRequest(site="example.com", tool="scraper")
    )

    assert strategy.id != chained.id
    assert strategy.chain is None
    assert chained in (await service.get_site(SiteRequest(site="example.com"))).strategies


async def test_setting_a_strategy_keeps_what_is_known_about_the_site(make: Make) -> None:
    service = await make()
    await service.db.upsert_site(Site(key="example.com", name="Example"))

    await service.set_site_strategy(SetStrategyRequest(site="example.com", tool="http"))

    assert (await service.get_site(SiteRequest(site="example.com"))).site.name == "Example"


async def test_setting_a_strategy_checks_the_tool_and_its_options(make: Make) -> None:
    service = await make()

    with pytest.raises(PluginError):
        await service.set_site_strategy(SetStrategyRequest(site="example.com", tool="nope"))
    with pytest.raises(ConfigError):
        await service.set_site_strategy(
            SetStrategyRequest(site="example.com", tool="http", options={"nope": 1})
        )
    with pytest.raises(ValidationError):
        SetStrategyRequest(site="example.com", tool="http", pattern="(unclosed")


async def test_an_unknown_site_does_not_exist(make: Make) -> None:
    service = await make()

    with pytest.raises(DoesNotExist, match=r"example\.com"):
        await service.get_site(SiteRequest(site="example.com"))


async def test_listing_sites(make: Make) -> None:
    service = await make()
    for key in ("b.com", "a.com", "example.com"):
        await service.db.upsert_site(Site(key=key))

    everything = await service.list_sites(SiteQuery())
    found = await service.list_sites(SiteQuery(search="exam"))

    assert [site.key for site in everything.sites] == ["a.com", "b.com", "example.com"]
    assert [site.key for site in found.sites] == ["example.com"]


async def test_deleting_a_site(make: Make) -> None:
    service = await make()
    await service.set_site_strategy(SetStrategyRequest(site="example.com", tool="http"))

    deleted = await service.delete_site(SiteRequest(site="www.example.com"))
    again = await service.delete_site(SiteRequest(site="example.com"))

    assert (deleted.deleted, again.deleted) == (True, False)


# ---------------------------------------------------------------------------------------------
# Databases, plugins and the config
# ---------------------------------------------------------------------------------------------


async def test_listing_the_databases(tmp_path: Path, make: Make) -> None:
    config = settings(
        tmp_path,
        connections=[
            connection("local", primary=True),
            connection("shared", priority=10, read_only=True),
        ],
    )
    service = await make(config)
    await service.db.database("shared").close()

    found = await service.list_databases(ListDatabasesRequest())

    assert [
        (d.name, d.plugin, d.primary, d.priority, d.read_only, d.connected, d.healthy)
        for d in found.databases
    ] == [
        ("local", "memory", True, 0, False, True, True),
        ("shared", "memory", False, 10, True, True, False),
    ]


async def test_listing_the_databases_hides_passwords(tmp_path: Path, make: Make) -> None:
    config = settings(
        tmp_path,
        connections=[
            connection(primary=True).model_copy(update={"url": "memory://udl:s3cret@nas/udl"})
        ],
    )
    service = await make(config)

    [found] = (await service.list_databases(ListDatabasesRequest())).databases

    assert found.url == "memory://udl:***@nas/udl"


def two_databases(tmp_path: Path, **shared: Any) -> Settings:
    return settings(
        tmp_path,
        connections=[
            connection("local", primary=True),
            connection("shared", priority=10, **shared),
        ],
    )


async def test_syncing_copies_what_one_database_knows_into_another(
    tmp_path: Path, make: Make
) -> None:
    service = await make(two_databases(tmp_path))
    local = service.db.database("local")
    await local.upsert_site(Site(key="example.com"))
    await local.add_download(
        DownloadRecord(url=A, site_key="example.com", status=JobStatus.FINISHED)
    )

    report = await service.sync_databases(SyncDatabasesRequest(source="local", targets=["shared"]))

    assert (report.source, report.dry_run) == ("local", False)
    [synced] = report.targets
    assert (synced.target, synced.sites.added, synced.downloads.added) == ("shared", 1, 1)
    shared = service.db.database("shared")
    assert await shared.get_site("example.com") == await local.get_site("example.com")


async def test_a_dry_run_sync_changes_nothing(tmp_path: Path, make: Make) -> None:
    service = await make(two_databases(tmp_path))
    await service.db.database("local").upsert_site(Site(key="example.com"))

    report = await service.sync_databases(
        SyncDatabasesRequest(source="local", targets=["shared"], dry_run=True)
    )

    assert report.dry_run
    assert report.targets[0].sites.added == 1
    assert await service.db.database("shared").list_sites(SiteQuery()) == []


async def test_syncing_with_a_connection_that_does_not_exist_is_refused(
    tmp_path: Path, make: Make
) -> None:
    service = await make(two_databases(tmp_path))

    with pytest.raises(DoesNotExist, match="nas") as caught:
        await service.sync_databases(SyncDatabasesRequest(source="local", targets=["nas"]))

    assert caught.value.hint is not None
    assert "local, shared" in caught.value.hint


async def test_syncing_into_a_read_only_connection_is_not_allowed(
    tmp_path: Path, make: Make
) -> None:
    service = await make(two_databases(tmp_path, read_only=True))
    await service.db.database("local").upsert_site(Site(key="example.com"))

    with pytest.raises(NotAllowed, match="'shared' database is read-only"):
        await service.sync_databases(SyncDatabasesRequest(source="local", targets=["shared"]))

    assert await service.db.database("shared").list_sites(SiteQuery()) == []
    # Reading from it is fine.
    await service.sync_databases(SyncDatabasesRequest(source="shared", targets=["local"]))


async def test_syncing_with_a_connection_that_is_not_connected_fails(
    tmp_path: Path, make: Make
) -> None:
    plugins = registry()
    plugins.register(DATABASES, Unreachable)
    far = connection("far", priority=5).model_copy(update={"plugin": "unreachable"})
    service = await make(
        settings(tmp_path, connections=[connection(primary=True), far]), plugins=plugins
    )

    with pytest.raises(DatabaseError, match="'far' database is not connected"):
        await service.sync_databases(SyncDatabasesRequest(source="far", targets=["local"]))


@pytest.mark.parametrize(
    "fields",
    [
        {"source": "local", "targets": []},
        {"source": "local", "targets": ["local"]},
        {"source": "local", "targets": ["shared", "shared"]},
    ],
)
def test_a_sync_needs_other_connections_to_sync_into(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SyncDatabasesRequest.model_validate(fields)


class Unreachable(MemoryDatabase):
    """A memory database that cannot be connected to."""

    name = "unreachable"

    async def connect(self) -> None:
        raise ConnectionError("no route to host")


async def test_a_database_that_could_not_be_connected_to_is_listed_as_such(
    tmp_path: Path, make: Make
) -> None:
    plugins = registry()
    plugins.register(DATABASES, Unreachable)
    far = connection("far", priority=5).model_copy(update={"plugin": "unreachable"})
    service = await make(
        settings(tmp_path, connections=[connection(primary=True), far]), plugins=plugins
    )

    _, found = (await service.list_databases(ListDatabasesRequest())).databases

    assert (found.name, found.connected, found.healthy) == ("far", False, False)


class Sick(MemoryDatabase):
    """A memory database whose health check fails."""

    name = "sick"

    async def health(self) -> bool:
        raise ConnectionError("gone")


async def test_a_database_whose_health_check_fails_is_unhealthy(tmp_path: Path, make: Make) -> None:
    plugins = registry()
    plugins.register(DATABASES, Sick)
    sick = connection(primary=True).model_copy(update={"plugin": "sick"})
    service = await make(settings(tmp_path, connections=[sick]), plugins=plugins)

    [found] = (await service.list_databases(ListDatabasesRequest())).databases

    assert (found.connected, found.healthy) == (True, False)


class NeedsABinary(FakeDownloader):
    name = "needs-a-binary"
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.binary("udl-no-such-binary", hint="Install it"),
    )


async def test_listing_the_plugins_says_which_downloaders_can_run(
    tmp_path: Path, make: Make
) -> None:
    plugins = registry("http")
    plugins.register(DOWNLOADERS, NeedsABinary)
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "broken.py").write_text("raise RuntimeError('broken plugin')\n")
    plugins.load_dir(plugin_dir)
    service = await make(settings(tmp_path, tools={"http": script()}), plugins=plugins)

    found = await service.list_plugins(ListPluginsRequest())

    by_name = {p.name: p for p in found.plugins}
    assert (by_name["http"].available, by_name["http"].missing) == (True, [])
    assert by_name["needs-a-binary"].available is False
    assert by_name["needs-a-binary"].missing == [
        Requirement.binary("udl-no-such-binary", hint="Install it")
    ]
    assert {(p.kind, p.available) for p in found.plugins if p.name == "memory"} == {
        ("udl.databases", True),
        ("udl.secrets", True),
    }
    assert [problem.origin for problem in found.problems] == [str(plugin_dir / "broken.py")]


async def test_listing_the_plugins_says_which_secret_stores_can_run(make: Make) -> None:
    plugins = registry()
    plugins.register(SECRETS, KeyringSecretStore)  # (and no keyring, in the tests)
    service = await make(plugins=plugins)

    found = {
        p.name: p
        for p in (await service.list_plugins(ListPluginsRequest())).plugins
        if p.kind == SECRETS.group
    }

    assert found["memory"].available is True
    assert found["keyring"].available is False
    assert found["keyring"].problem is not None
    assert "no keyring" in found["keyring"].problem


async def test_listing_the_plugins_says_which_databases_can_run(tmp_path: Path, make: Make) -> None:
    class Remote(MemoryDatabase):
        name = "remote"
        requires: ClassVar[tuple[Requirement, ...]] = (
            Requirement.module("udl_no_such_driver", hint="Install the driver"),
        )

    plugins = registry()
    plugins.register(DATABASES, Remote)
    service = await make(plugins=plugins)

    found = {p.name: p for p in (await service.list_plugins(ListPluginsRequest())).plugins}

    assert (found["remote"].available, found["remote"].missing) == (False, list(Remote.requires))
    assert found["memory"].available is True


class NeedsAnSDK(FakeIntegration):
    name = "needs-an-sdk"
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.module("udl_no_such_sdk", hint="Install the SDK"),
    )


async def test_listing_the_plugins_says_which_integrations_can_run(make: Make) -> None:
    plugins = registry()
    plugins.register(INTEGRATIONS, FakeIntegration)
    plugins.register(INTEGRATIONS, NeedsAnSDK)
    service = await make(plugins=plugins)

    found = {p.name: p for p in (await service.list_plugins(ListPluginsRequest())).plugins}

    assert (found["fake"].kind, found["fake"].available) == ("udl.integrations", True)
    assert (found["needs-an-sdk"].available, found["needs-an-sdk"].missing) == (
        False,
        list(NeedsAnSDK.requires),
    )


class NeedsFfmpeg(FakeSitePlugin):
    name = "needs-ffmpeg"
    domains = ("example.org",)
    requires: ClassVar[tuple[Requirement, ...]] = (
        Requirement.binary("udl-no-such-ffmpeg", hint="Install ffmpeg"),
    )


class CannotStart(FakeSitePlugin):
    name = "cannot-start"

    def __init__(self) -> None:
        raise RuntimeError("no API key")


async def test_listing_the_plugins_shows_the_site_plugins_their_sites_and_whether_they_run(
    tmp_path: Path, make: Make
) -> None:
    plugins = registry()
    for plugin in (YouTubePlugin, NeedsFfmpeg, CannotStart):
        plugins.register(SITES, plugin)
    service = await make(plugins=plugins)

    found = {p.name: p for p in (await service.list_plugins(ListPluginsRequest())).plugins}

    youtube = found["youtube"]
    assert (youtube.kind, youtube.available) == ("udl.sites", True)
    assert youtube.domains == ["youtube.com", "youtu.be", "youtube-nocookie.com"]
    assert (found["needs-ffmpeg"].available, found["needs-ffmpeg"].missing) == (
        False,
        list(NeedsFfmpeg.requires),
    )
    assert found["cannot-start"].available is False
    assert found["cannot-start"].problem == "RuntimeError: no API key"
    assert found["memory"].domains == []


async def test_a_site_plugin_that_cannot_run_is_not_used(
    tmp_path: Path, make: Make, caplog: pytest.LogCaptureFixture
) -> None:
    plugins = registry()
    plugins.register(SITES, NeedsFfmpeg)
    service = await make(plugins=plugins)

    assert service.resolver.site_plugin(URL.parse("https://example.org/")) is None
    assert "needs-ffmpeg site plugin needs udl-no-such-ffmpeg" in caplog.text


async def test_listing_the_plugins_says_where_local_plugins_are_read_from(
    tmp_path: Path, make: Make
) -> None:
    dirs = [tmp_path / "plugins", tmp_path / "more"]
    service = await make(settings(tmp_path, plugin_dirs=dirs))

    assert (await service.list_plugins(ListPluginsRequest())).plugin_dirs == dirs


async def test_a_downloader_with_invalid_options_is_listed_as_unavailable(
    tmp_path: Path, make: Make
) -> None:
    service = await make(settings(tmp_path, tools={"http": {"nope": 1}}), plugins=registry("http"))

    [http] = [
        p for p in (await service.list_plugins(ListPluginsRequest())).plugins if p.name == "http"
    ]

    assert http.available is False
    assert http.problem is not None
    assert "nope" in http.problem


async def test_the_config_is_shown_with_its_secrets_hidden(tmp_path: Path, make: Make) -> None:
    config = settings(
        tmp_path,
        api=APISettings(api_key=SecretStr("api-secret")),
        connections=[
            connection(primary=True).model_copy(update={"url": "memory://udl:db-secret@nas/udl"})
        ],
        tools={
            "yt-dlp": {"format": "best", "params": {"password": "pw-secret", "username": "me"}},
            "http": {"headers": [{"Authorization": "Bearer token-secret"}]},
        },
        integrations={"gmail": {"client_id": "abc", "client_secret": "gmail-secret"}},
    )
    service = await make(config)

    shown = await service.get_config(GetConfigRequest())

    text = shown.model_dump_json()
    assert isinstance(shown, ConfigInfo)
    for secret in ("api-secret", "db-secret", "pw-secret", "token-secret", "gmail-secret"):
        assert secret not in text
    data = json.loads(text)["settings"]
    assert data["integrations"]["gmail"]["client_id"] == "abc"
    assert shown.secret_store == "memory"  # noqa: S105 - (the store [secrets] store chose)
    assert data["tools"]["yt-dlp"]["format"] == "best"
    assert data["tools"]["yt-dlp"]["params"]["username"] == "me"
    assert data["db"]["connections"][0]["url"] == "memory://udl:***@nas/udl"
    assert shown.settings.download_dir == config.download_dir


# ---------------------------------------------------------------------------------------------
# Starting and stopping
# ---------------------------------------------------------------------------------------------


async def running_download(config: Settings) -> tuple[DBManager, DownloadRecord]:
    """A database with a download that a udl which stopped left running."""
    db = databases(config, registry())
    await db.connect()
    record = DownloadRecord(url=A, site_key="example.com", status=JobStatus.RUNNING)
    await db.add_download(record)
    return db, record


async def test_starting_alone_marks_downloads_a_stopped_udl_left_as_interrupted(
    tmp_path: Path, make: Make
) -> None:
    config = settings(tmp_path)
    db, record = await running_download(config)

    service = await make(config, db=db)

    job = await service.get_download(JobRequest(id=record.id))
    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert job.error.code is ErrorCode.INTERRUPTED


async def test_starting_while_another_udl_runs_leaves_the_history_alone(
    tmp_path: Path, make: Make
) -> None:
    config = settings(tmp_path)
    db, record = await running_download(config)
    other = InstanceLock(tmp_path / "udl.lock")
    other.acquire()
    other.share()

    service = await make(config, db=db)

    assert (await service.get_download(JobRequest(id=record.id))).status is JobStatus.RUNNING
    other.release()


async def test_a_lock_that_cannot_be_taken_leaves_the_history_alone(
    tmp_path: Path, make: Make, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(tmp_path)
    db, record = await running_download(config)
    (tmp_path / "file").write_text("not a dir")

    service = await make(config, db=db, lock=InstanceLock(tmp_path / "file" / "udl.lock"))

    assert (await service.get_download(JobRequest(id=record.id))).status is JobStatus.RUNNING
    assert "another udl" in caplog.text


async def test_closing_cancels_the_running_downloads(tmp_path: Path) -> None:
    config = slow_settings(tmp_path)
    lock = InstanceLock(tmp_path / "udl.lock")
    async with UDLService(
        config, registry=registry(), cookies=StaticCookies(), lock=lock
    ) as service:
        batch = await service.download(DownloadRequest(urls=[A], wait=False))
        await asyncio.sleep(0.1)

    job = service.jobs.get(batch.jobs[0].id)
    assert job is not None
    assert job.status is JobStatus.CANCELLED
    # The lock went with it: the next udl is alone.
    assert lock.acquire() is True
    lock.release()


async def test_a_service_made_from_the_settings_alone_uses_udl_s_own_plugins() -> None:
    # The default settings: the user's SQLite database (in the isolated data dir).
    async with UDLService(Settings()) as service:
        plugins = await service.list_plugins(ListPluginsRequest())
        databases_ = await service.list_databases(ListDatabasesRequest())

    assert {"http", "yt-dlp", "gallery-dl", "scraper", "browser", "sqlite", "youtube"} <= {
        p.name for p in plugins.plugins
    }
    # (All but the keyring store: the tests have no keyring.)
    assert {p.name for p in plugins.plugins if not p.available} == {"keyring"}
    assert [(d.name, d.plugin, d.healthy) for d in databases_.databases] == [
        ("local", "sqlite", True)
    ]


def test_a_cookies_from_browser_setting_udl_cannot_read_is_a_config_error(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path, cookies_from_browser="netscape-navigator")

    with pytest.raises(ConfigError, match="cookies_from_browser"):
        UDLService(config, registry=registry())


async def test_the_config_says_when_its_secret_store_cannot_be_used(
    tmp_path: Path, make: Make
) -> None:
    plugins = registry()
    plugins.register(SECRETS, KeyringSecretStore)  # (and no keyring, in the tests)
    service = await make(
        settings(tmp_path, secrets=SecretsSettings(store="keyring")), plugins=plugins
    )

    shown = await service.get_config(GetConfigRequest())

    assert shown.secret_store is None
