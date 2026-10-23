"""
--------------------
- test_executor.py -
--------------------

Executor: tries a URL's candidates until a download actually succeeds. A failed download falls
through to the next candidate, a site that wants a login is retried with its cookies (then in
the browser), network errors and rate limits are retried after a wait, and NotFound stops.
"""

# System Imports
import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

# Package Imports
import pytest

# Local Imports
from support.downloaders import FakeDownloader, FakeMedia
from support.engine import (
    StaticCookies,
    cookie,
    databases,
    fake_tool,
    registry,
    script,
    settings,
)
from support.sites import fake_site
from udl.config import Settings
from udl.core.executor import Execution, Executor, JobSpec, RetryPolicy
from udl.core.models import Attempt, CandidateSource
from udl.core.resolver import Resolver
from udl.core.tools import Toolbox
from udl.core.urls import URL
from udl.db.manager import DBManager
from udl.db.models import Site, Strategy, StrategySource
from udl.errors import (
    AuthRequired,
    Cancelled,
    DatabaseError,
    DownloadError,
    ErrorCode,
    NetworkError,
    NoStrategyFound,
    NotFound,
    PluginError,
    RateLimited,
    UDLError,
)
from udl.plugins.downloaders.base import (
    CancelToken,
    DownloadContext,
    DownloadProgress,
    DownloadResult,
    Handoff,
    render_template,
)
from udl.plugins.registry import DOWNLOADERS, PluginRegistry
from udl.plugins.sites.base import SitePlugin

PAGE = "https://example.com/watch/1"
SIGNED_IN = FakeMedia(content=b"members only", cookie="session=signed-in")
NOW = RetryPolicy(backoff=0)


def tried(attempts: list[Attempt]) -> list[tuple[str, ErrorCode | None]]:
    return [(a.tool, a.error.code if a.error else None) for a in attempts]


def flaky(name: str, failures: list[DownloadError]) -> type[FakeDownloader]:
    """A fake tool that fails with `failures`, one per try, then works as scripted."""

    class Flaky(fake_tool(name)):
        async def _download(self, ctx: DownloadContext) -> DownloadResult:
            if failures:
                raise failures.pop(0)
            return await super()._download(ctx)

    return Flaky


def with_tools(*tools: type[FakeDownloader]) -> PluginRegistry:
    plugins = registry()
    for tool in tools:
        plugins.register(DOWNLOADERS, tool)
    return plugins


class Run:
    """An executor for one test, and what it reported."""

    def __init__(
        self,
        config: Settings,
        plugins: PluginRegistry | None = None,
        *,
        cookies: StaticCookies | None = None,
        retry: RetryPolicy = NOW,
        site_plugins: tuple[SitePlugin, ...] = (),
    ) -> None:
        toolbox = Toolbox(config, plugins or registry())
        self.cookies = cookies or StaticCookies()
        self.executor = Executor(
            config,
            Resolver(config, toolbox, site_plugins),
            toolbox,
            cookies=self.cookies,
            retry=retry,
        )
        self.attempts: list[Attempt] = []
        self.progress: list[tuple[str, DownloadProgress]] = []

    async def __call__(
        self, db: DBManager, url: str = PAGE, *, cancel: CancelToken | None = None, **spec: Any
    ) -> Execution:
        return await self.executor.run(
            JobSpec(url=URL.parse(url), **spec),
            db=db,
            cancel=cancel or CancelToken(),
            progress=lambda tool, progress: self.progress.append((tool, progress)),
            on_attempt=self.attempts.append,
        )

    async def failure(self, db: DBManager, url: str = PAGE, **spec: Any) -> UDLError:
        with pytest.raises(UDLError) as caught:
            await self(db, url, **spec)
        return caught.value


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[DBManager]:
    async with databases(settings(tmp_path), registry()) as manager:
        yield manager


# ---------------------------------------------------------------------------------------------
# Falling through the candidates
# ---------------------------------------------------------------------------------------------


async def test_the_first_candidate_that_downloads_wins(tmp_path: Path, db: DBManager) -> None:
    run = Run(settings(tmp_path, tools={"yt-dlp": script({PAGE: "video"})}))

    execution = await run(db)

    assert execution.candidate.tool == "yt-dlp"
    assert execution.candidate.source is CandidateSource.PROBE
    assert [path.read_bytes() for path in execution.result.files] == [b"video"]
    assert execution.attempts == run.attempts
    assert tried(run.attempts) == [("http", ErrorCode.UNSUPPORTED_URL), ("yt-dlp", None)]


@pytest.mark.parametrize(
    "failure", [ErrorCode.EXTRACTION_FAILED, ErrorCode.DOWNLOAD_ERROR, ErrorCode.UNSUPPORTED_URL]
)
async def test_a_failed_download_falls_through_to_the_next_candidate(
    failure: ErrorCode, tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        tools={"http": script(errors={PAGE: failure}), "yt-dlp": script({PAGE: "video"})},
    )
    run = Run(config)

    await run(db)

    assert tried(run.attempts) == [("http", failure), ("yt-dlp", None)]


async def test_a_download_that_writes_no_media_is_not_a_success(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        tools={
            "http": script({PAGE: FakeMedia(content=b"")}),  # an empty file
            "yt-dlp": script({PAGE: "video"}),
        },
    )
    run = Run(config)

    execution = await run(db)

    assert execution.candidate.tool == "yt-dlp"
    assert tried(run.attempts) == [("http", ErrorCode.EXTRACTION_FAILED), ("yt-dlp", None)]


async def test_not_found_stops_at_once(tmp_path: Path, db: DBManager) -> None:
    config = settings(
        tmp_path,
        tools={"http": script(errors={PAGE: "not_found"}), "yt-dlp": script({PAGE: "video"})},
    )
    run = Run(config)

    error = await run.failure(db)

    assert isinstance(error, NotFound)
    assert tried(run.attempts) == [("http", ErrorCode.NOT_FOUND)]


async def test_the_requested_tool_s_failure_is_the_error(tmp_path: Path, db: DBManager) -> None:
    config = settings(
        tmp_path,
        tools={
            "http": script(errors={PAGE: "extraction_failed"}),
            "yt-dlp": script({PAGE: "video"}),
        },
    )
    run = Run(config)

    error = await run.failure(db, tool="http")

    assert error.code is ErrorCode.EXTRACTION_FAILED
    assert tried(run.attempts) == [("http", ErrorCode.EXTRACTION_FAILED)]


async def test_an_unknown_requested_tool_is_a_plugin_error(tmp_path: Path, db: DBManager) -> None:
    run = Run(settings(tmp_path))

    error = await run.failure(db, tool="nope")

    assert isinstance(error, PluginError)
    assert tried(run.attempts) == [("nope", ErrorCode.PLUGIN_ERROR)]


async def test_a_strategy_with_invalid_options_is_skipped(tmp_path: Path, db: DBManager) -> None:
    await db.upsert_site(Site(key="example.com"))
    await db.upsert_strategy(
        Strategy(
            site_key="example.com",
            tool="scraper",
            options={"no_such_option": 1},
            source=StrategySource.MANUAL,
        )
    )
    run = Run(settings(tmp_path, tools={"http": script({PAGE: "file"})}))

    execution = await run(db)

    assert execution.candidate.tool == "http"
    assert tried(run.attempts) == [("scraper", ErrorCode.CONFIG_ERROR), ("http", None)]


async def test_when_every_candidate_fails_no_strategy_is_found(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(tmp_path, tools={"yt-dlp": script(errors={PAGE: "extraction_failed"})})
    run = Run(config)

    error = await run.failure(db)

    assert isinstance(error, NoStrategyFound)
    assert "http, yt-dlp, gallery-dl, scraper, browser" in error.message
    assert error.hint is not None
    assert f"udl probe {PAGE} --all" in error.hint


async def test_when_every_candidate_fails_the_hint_suggests_tag_chains(
    tmp_path: Path, db: DBManager
) -> None:
    suggested = {
        "a.download@href": "https://cdn.example.com/a.mp3",
        "video#clip > source@src": "https://cdn.example.com/v.mp4",
        "regex:https://cdn\\S+?\\.mp4": "https://cdn.example.com/w.mp4",
        "div p@data-src": "https://cdn.example.com/x.mp4",
    }
    config = settings(tmp_path, tools={"scraper": script(suggested={PAGE: suggested})})

    error = await Run(config).failure(db)

    assert isinstance(error, NoStrategyFound)
    assert error.hint is not None
    assert f"udl probe {PAGE} --all" in error.hint
    lines = error.hint.splitlines()
    assert [line.strip() for line in lines if line.startswith("  ")] == [
        "udl chain set example.com 'video#clip > source@src'",
        "udl chain set example.com 'regex:https://cdn\\S+?\\.mp4'",
        "udl chain set example.com 'div p@data-src'",
    ]
    assert f"udl chain wizard {PAGE}" in error.hint
    assert "set_site_chain" in error.hint


async def test_without_suggestions_the_hint_points_at_the_chain_wizard(
    tmp_path: Path, db: DBManager
) -> None:
    error = await Run(settings(tmp_path)).failure(db)

    assert error.hint is not None
    assert f"udl chain wizard {PAGE}" in error.hint
    assert "suggest_chain" in error.hint
    assert "udl chain set" not in error.hint


@pytest.mark.parametrize(
    "plugins",
    [
        registry("http", "yt-dlp"),  # (no scraper to suggest chains)
        with_tools(fake_tool("scraper")),  # (one whose page cannot be read: see the config)
    ],
)
async def test_when_chains_cannot_be_suggested_the_hint_points_at_the_wizard(
    tmp_path: Path, db: DBManager, plugins: PluginRegistry
) -> None:
    config = settings(tmp_path, tools={"scraper": script(errors={PAGE: "unsupported_url"})})

    error = await Run(config, plugins).failure(db)

    assert isinstance(error, NoStrategyFound)
    assert error.hint is not None
    assert f"udl chain wizard {PAGE}" in error.hint


async def test_a_site_that_wants_a_login_gets_no_chain_hint(tmp_path: Path, db: DBManager) -> None:
    config = settings(
        tmp_path,
        tools={
            "http": script(errors={PAGE: "auth_required"}),
            "scraper": script(suggested={PAGE: {"video@src": "https://cdn.example.com/v.mp4"}}),
        },
    )

    error = await Run(config).failure(db)

    assert error.code is ErrorCode.AUTH_REQUIRED
    assert "udl chain" not in (error.hint or "")


async def test_with_no_downloader_to_try_no_strategy_is_found(
    tmp_path: Path, db: DBManager
) -> None:
    error = await Run(settings(tmp_path, probe_order=[])).failure(db)

    assert isinstance(error, NoStrategyFound)
    assert "No downloader" in error.message


@pytest.mark.parametrize(
    ("errors", "expected"),
    [
        # A site that wants a login says the most about what to do.
        ({"http": "auth_required", "yt-dlp": "network_error"}, ErrorCode.AUTH_REQUIRED),
        ({"http": "network_error", "yt-dlp": "rate_limited"}, ErrorCode.RATE_LIMITED),
        ({"http": "unsupported_url", "yt-dlp": "network_error"}, ErrorCode.NETWORK_ERROR),
        ({"http": "unsupported_url", "yt-dlp": "extraction_failed"}, ErrorCode.NO_STRATEGY_FOUND),
    ],
)
async def test_the_error_when_every_candidate_fails_says_the_most_useful_thing(
    errors: dict[str, str], expected: ErrorCode, tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        probe_order=list(errors),
        tools={tool: script(errors={PAGE: code}) for tool, code in errors.items()},
    )

    error = await Run(config, retry=RetryPolicy(retries=0)).failure(db)

    assert error.code is expected


# ---------------------------------------------------------------------------------------------
# Signing in
# ---------------------------------------------------------------------------------------------


async def test_a_site_that_wants_a_login_is_retried_with_its_cookies(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(tmp_path, probe_order=["http"], tools={"http": script({PAGE: SIGNED_IN})})
    run = Run(config, cookies=StaticCookies([cookie()]))

    execution = await run(db)

    assert execution.signed_in is True
    assert [(a.tool, a.signed_in, a.error and a.error.code) for a in run.attempts] == [
        ("http", False, ErrorCode.AUTH_REQUIRED),
        ("http", True, None),
    ]


async def test_cookies_that_do_not_help_bring_the_browser_in_next(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        tools={"http": script({PAGE: SIGNED_IN}), "browser": script({PAGE: "from the browser"})},
    )
    run = Run(config, cookies=StaticCookies([cookie(value="expired")]))

    execution = await run(db)

    assert execution.candidate.tool == "browser"
    assert [(a.tool, a.signed_in) for a in run.attempts] == [
        ("http", False),
        ("http", True),
        ("browser", True),
    ]


async def test_without_cookies_the_browser_is_tried_straight_after_the_login_failure(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        tools={"http": script({PAGE: SIGNED_IN}), "browser": script({PAGE: "from the browser"})},
    )
    run = Run(config)

    await run(db)

    assert tried(run.attempts) == [("http", ErrorCode.AUTH_REQUIRED), ("browser", None)]


async def test_after_a_login_failure_every_later_candidate_is_signed_in(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        tools={
            "http": script(errors={PAGE: "auth_required"}),
            "gallery-dl": script({PAGE: SIGNED_IN}),
        },
    )
    run = Run(config, cookies=StaticCookies([cookie()]))

    execution = await run(db)

    assert execution.candidate.tool == "gallery-dl"
    assert [(a.tool, a.signed_in) for a in run.attempts] == [
        ("http", False),
        ("http", True),
        ("browser", True),  # (the browser came in early, and failed)
        ("yt-dlp", True),
        ("gallery-dl", True),
    ]
    assert run.cookies.asked == 1  # the cookies are read once per URL


async def test_the_browser_is_not_tried_twice(tmp_path: Path, db: DBManager) -> None:
    config = settings(tmp_path, tools={"http": script(errors={PAGE: "auth_required"})})
    run = Run(config)

    await run.failure(db)

    assert [a.tool for a in run.attempts] == ["http", "browser", "yt-dlp", "gallery-dl", "scraper"]


async def test_a_later_login_failure_does_not_bring_the_browser_in_again(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        tools={tool: script(errors={PAGE: "auth_required"}) for tool in ("http", "yt-dlp")},
    )
    run = Run(config)

    await run.failure(db)

    assert [a.tool for a in run.attempts] == ["http", "browser", "yt-dlp", "gallery-dl", "scraper"]


async def test_the_browser_wanting_a_login_is_followed_by_the_next_candidate(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        probe_order=["browser", "http"],
        tools={"browser": script(errors={PAGE: "auth_required"}), "http": script({PAGE: "x"})},
    )
    run = Run(config)

    await run(db)

    assert tried(run.attempts) == [("browser", ErrorCode.AUTH_REQUIRED), ("http", None)]


async def test_without_a_browser_downloader_the_next_candidate_follows_a_login_failure(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path,
        probe_order=["http", "yt-dlp"],
        tools={"http": script(errors={PAGE: "auth_required"}), "yt-dlp": script({PAGE: "v"})},
    )
    run = Run(config, registry("http", "yt-dlp"))

    await run(db)

    assert tried(run.attempts) == [("http", ErrorCode.AUTH_REQUIRED), ("yt-dlp", None)]


async def test_the_browser_finding_the_content_gone_stops(tmp_path: Path, db: DBManager) -> None:
    config = settings(
        tmp_path,
        tools={
            "http": script(errors={PAGE: "auth_required"}),
            "browser": script(errors={PAGE: "not_found"}),
            "yt-dlp": script({PAGE: "v"}),
        },
    )
    run = Run(config)

    error = await run.failure(db)

    assert isinstance(error, NotFound)
    assert [a.tool for a in run.attempts] == ["http", "browser"]


async def test_a_requested_tool_gets_its_cookies_but_not_the_browser(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(tmp_path, tools={"http": script({PAGE: SIGNED_IN})})
    run = Run(config, cookies=StaticCookies([cookie(value="expired")]))

    error = await run.failure(db, tool="http")

    assert isinstance(error, AuthRequired)
    assert [(a.tool, a.signed_in) for a in run.attempts] == [("http", False), ("http", True)]


async def test_a_strategy_that_requires_auth_is_signed_in_from_the_start(
    tmp_path: Path, db: DBManager
) -> None:
    await db.upsert_site(Site(key="example.com"))
    await db.upsert_strategy(
        Strategy(
            site_key="example.com",
            tool="scraper",
            requires_auth=True,
            source=StrategySource.PROBE,
        )
    )
    run = Run(
        settings(tmp_path, tools={"scraper": script({PAGE: SIGNED_IN})}),
        cookies=StaticCookies([cookie()]),
    )

    execution = await run(db)

    assert execution.signed_in is True
    assert [(a.tool, a.signed_in, a.error) for a in run.attempts] == [("scraper", True, None)]


async def test_no_cookies_are_read_when_no_login_is_needed(tmp_path: Path, db: DBManager) -> None:
    run = Run(settings(tmp_path, tools={"http": script({PAGE: "file"})}))

    execution = await run(db)

    assert execution.signed_in is False
    assert run.cookies.asked == 0


async def test_a_cookie_source_that_fails_gives_no_cookies(
    tmp_path: Path, db: DBManager, caplog: pytest.LogCaptureFixture
) -> None:
    class Failing(StaticCookies):
        async def for_url(self, url: str) -> list[Any]:
            raise RuntimeError("the keyring is locked")

    config = settings(tmp_path, tools={"http": script({PAGE: SIGNED_IN})}, probe_order=["http"])
    run = Run(config, cookies=Failing())

    error = await run.failure(db)

    assert isinstance(error, AuthRequired)
    assert "the keyring is locked" in caplog.text


# ---------------------------------------------------------------------------------------------
# Retrying after network errors and rate limits
# ---------------------------------------------------------------------------------------------


async def test_a_network_error_is_retried_then_the_next_candidate_is_tried(
    tmp_path: Path, db: DBManager
) -> None:
    failures: list[DownloadError] = [NetworkError("reset") for _ in range(3)]
    config = settings(tmp_path, tools={"http": script({PAGE: "x"}), "yt-dlp": script({PAGE: "v"})})
    run = Run(config, with_tools(flaky("http", failures)), retry=RetryPolicy(retries=2, backoff=0))

    execution = await run(db)

    assert execution.candidate.tool == "yt-dlp"
    assert tried(run.attempts) == [
        ("http", ErrorCode.NETWORK_ERROR),
        ("http", ErrorCode.NETWORK_ERROR),
        ("http", ErrorCode.NETWORK_ERROR),
        ("yt-dlp", None),
    ]


async def test_a_retry_that_works_is_a_success(tmp_path: Path, db: DBManager) -> None:
    failures: list[DownloadError] = [NetworkError("reset")]
    config = settings(tmp_path, tools={"http": script({PAGE: "x"})})
    run = Run(config, with_tools(flaky("http", failures)))

    execution = await run(db)

    assert execution.candidate.tool == "http"
    assert tried(run.attempts) == [("http", ErrorCode.NETWORK_ERROR), ("http", None)]


async def test_retries_back_off(tmp_path: Path, db: DBManager) -> None:
    failures: list[DownloadError] = [NetworkError("reset"), NetworkError("reset")]
    config = settings(tmp_path, tools={"http": script({PAGE: "x"})})
    run = Run(config, with_tools(flaky("http", failures)), retry=RetryPolicy(backoff=0.05))

    started = time.monotonic()
    await run(db)

    assert time.monotonic() - started >= 0.05 + 0.1  # 0.05 s, then twice that


async def test_a_rate_limit_waits_as_long_as_the_site_asks(tmp_path: Path, db: DBManager) -> None:
    failures: list[DownloadError] = [RateLimited("slow down", retry_after=0.1)]
    config = settings(tmp_path, tools={"http": script({PAGE: "x"})})
    run = Run(config, with_tools(flaky("http", failures)), retry=RetryPolicy(backoff=0))

    started = time.monotonic()
    await run(db)

    assert time.monotonic() - started >= 0.1
    assert tried(run.attempts) == [("http", ErrorCode.RATE_LIMITED), ("http", None)]


async def test_a_rate_limit_longer_than_the_longest_wait_moves_on(
    tmp_path: Path, db: DBManager
) -> None:
    failures: list[DownloadError] = [RateLimited("come back tomorrow", retry_after=86400)]
    config = settings(tmp_path, tools={"http": script({PAGE: "x"}), "yt-dlp": script({PAGE: "v"})})
    run = Run(config, with_tools(flaky("http", failures)), retry=RetryPolicy(max_wait=60))

    execution = await run(db)

    assert execution.candidate.tool == "yt-dlp"
    assert tried(run.attempts) == [("http", ErrorCode.RATE_LIMITED), ("yt-dlp", None)]


async def test_a_long_backoff_is_cut_to_the_longest_wait(tmp_path: Path, db: DBManager) -> None:
    failures: list[DownloadError] = [NetworkError("reset")]
    config = settings(tmp_path, tools={"http": script({PAGE: "x"})})
    run = Run(
        config, with_tools(flaky("http", failures)), retry=RetryPolicy(backoff=60, max_wait=0.01)
    )

    started = time.monotonic()
    await run(db)

    assert time.monotonic() - started < 5


# ---------------------------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------------------------


async def test_cancelling_stops_the_download(tmp_path: Path, db: DBManager) -> None:
    slow = FakeMedia(content=b"x" * 1000, delay=0.05)
    config = settings(
        tmp_path,
        tools={"http": {**script({PAGE: slow}), "chunk_size": 10}, "yt-dlp": script({PAGE: "v"})},
    )
    run = Run(config)
    cancel = CancelToken()

    task = asyncio.create_task(run(db, cancel=cancel))
    await asyncio.sleep(0.1)
    cancel.cancel()

    with pytest.raises(Cancelled):
        await asyncio.wait_for(task, timeout=2)
    assert tried(run.attempts) == [("http", ErrorCode.CANCELLED)]


async def test_cancelling_during_a_wait_stops_at_once(tmp_path: Path, db: DBManager) -> None:
    failures: list[DownloadError] = [NetworkError("reset")]
    config = settings(tmp_path, tools={"http": script({PAGE: "x"})})
    run = Run(config, with_tools(flaky("http", failures)), retry=RetryPolicy(backoff=30))
    cancel = CancelToken()

    task = asyncio.create_task(run(db, cancel=cancel))
    await asyncio.sleep(0.1)
    cancel.cancel()

    with pytest.raises(Cancelled):
        await asyncio.wait_for(task, timeout=2)


async def test_a_cancelled_token_stops_before_anything_is_tried(
    tmp_path: Path, db: DBManager
) -> None:
    run = Run(settings(tmp_path, tools={"http": script({PAGE: "x"})}))
    cancel = CancelToken()
    cancel.cancel()

    with pytest.raises(Cancelled):
        await run(db, cancel=cancel)
    assert run.attempts == []


# ---------------------------------------------------------------------------------------------
# Where the files go, and progress
# ---------------------------------------------------------------------------------------------


async def test_the_files_go_to_the_config_s_download_dir(tmp_path: Path, db: DBManager) -> None:
    execution = await Run(settings(tmp_path, tools={"http": script({PAGE: "x"})}))(db)

    assert execution.output_dir == tmp_path / "downloads"
    assert execution.result.files == [tmp_path / "downloads" / "media.mp4"]


async def test_the_request_s_output_dir_is_created(tmp_path: Path, db: DBManager) -> None:
    run = Run(settings(tmp_path, tools={"http": script({PAGE: "x"})}))

    execution = await run(db, output_dir=tmp_path / "new" / "dir")

    assert execution.output_dir == tmp_path / "new" / "dir"
    assert execution.result.files == [tmp_path / "new" / "dir" / "media.mp4"]


async def test_the_site_s_output_dir(tmp_path: Path, db: DBManager) -> None:
    await db.upsert_site(Site(key="example.com", default_output_dir=tmp_path / "site"))
    run = Run(settings(tmp_path, tools={"http": script({PAGE: "x"})}))

    assert (await run(db)).output_dir == tmp_path / "site"


async def test_the_site_plugin_s_output_dir_beats_the_site_s(tmp_path: Path, db: DBManager) -> None:
    await db.upsert_site(Site(key="example.com", default_output_dir=tmp_path / "site"))
    plugin = fake_site("example.com", output_dir=tmp_path / "plugin")
    run = Run(settings(tmp_path, tools={"http": script({PAGE: "x"})}), site_plugins=(plugin,))

    assert (await run(db)).output_dir == tmp_path / "plugin"


async def test_when_the_site_cannot_be_read_the_config_s_download_dir_is_used(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    config = settings(tmp_path, tools={"http": script({PAGE: "x"})})

    class Unreadable(DBManager):
        async def get_site(self, key: str) -> Site | None:
            raise DatabaseError("the database is gone")

        async def strategies_for(self, site_key: str) -> list[Strategy]:
            return []

    execution = await Run(config)(Unreadable(config.db, registry()))

    assert execution.output_dir == tmp_path / "downloads"
    assert "the database is gone" in caplog.text


class Named(FakeDownloader):
    """Names its file from the filename template it is given."""

    name = "http"

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        assert ctx.filename_template is not None
        path = ctx.output_dir / render_template(ctx.filename_template, {"title": "Clip"})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"clip")
        return DownloadResult(tool=self.name, url=ctx.url, files=[path])


async def test_the_downloader_gets_the_filename_template_with_the_site_filled_in(
    tmp_path: Path, db: DBManager
) -> None:
    run = Run(settings(tmp_path), with_tools(Named))

    execution = await run(db)

    assert execution.result.files == [tmp_path / "downloads" / "example.com" / "Clip.NA"]


async def test_the_request_s_filename_template_beats_the_config_s(
    tmp_path: Path, db: DBManager
) -> None:
    run = Run(settings(tmp_path), with_tools(Named))

    execution = await run(db, filename_template="{site} - {title}")

    assert execution.result.files == [tmp_path / "downloads" / "example.com - Clip"]


async def test_progress_is_reported_with_the_tool_reporting_it(
    tmp_path: Path, db: DBManager
) -> None:
    config = settings(
        tmp_path, tools={"http": {**script({PAGE: FakeMedia(content=b"x" * 30)}), "chunk_size": 10}}
    )
    run = Run(config)

    await run(db)

    assert [(tool, p.downloaded_bytes) for tool, p in run.progress] == [
        ("http", 10),
        ("http", 20),
        ("http", 30),
    ]


async def test_progress_and_attempts_need_no_listener(tmp_path: Path, db: DBManager) -> None:
    config = settings(tmp_path, tools={"http": script({PAGE: "x"})})
    toolbox = Toolbox(config, registry())
    executor = Executor(config, Resolver(config, toolbox), toolbox, cookies=StaticCookies())

    execution = await executor.run(JobSpec(url=URL.parse(PAGE)), db=db, cancel=CancelToken())

    assert execution.candidate.tool == "http"


class Handing(FakeDownloader):
    """Finds the media on the page and hands it off to http."""

    name = "scraper"

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        return await self.hand_off(Handoff(tool="http", url=f"{ctx.url}/media.mp4"), ctx)


async def test_a_handoff_goes_to_the_configured_downloader(tmp_path: Path, db: DBManager) -> None:
    config = settings(
        tmp_path, probe_order=["scraper"], tools={"http": script({f"{PAGE}/media.mp4": "media"})}
    )

    execution = await Run(config, with_tools(Handing))(db)

    assert execution.result.handed_off_to == "http"
    assert [path.read_bytes() for path in execution.result.files] == [b"media"]
