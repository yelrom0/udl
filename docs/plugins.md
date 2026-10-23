# Writing udl plugins

udl has three kinds of plugin:

| Kind | Base class | What it is for | Entry point group |
|---|---|---|---|
| **site plugin** | `udl.plugins.sites.base.SitePlugin` | one site's behaviour: its URLs, its downloader, the headers it needs, its folder, work on its files | `udl.sites` |
| **downloader** | `udl.plugins.downloaders.base.Downloader` | a tool that downloads URLs, like yt-dlp or gallery-dl | `udl.downloaders` |
| **database** | `udl.plugins.databases.base.DatabasePlugin` | where udl keeps sites, strategies and download history | `udl.databases` |

Most needs are met without code. A tool or options for a site is a strategy (`udl sites set`),
and the way from a page to its media is a tag chain (`udl chain wizard`). Write a plugin when a
site needs behaviour (a site plugin), when udl lacks a tool (a downloader), or when you want to
keep udl's data somewhere new (a database).

## Contents

- [Where plugins come from](#where-plugins-come-from)
- [Site plugins](#site-plugins)
- [Downloader plugins](#downloader-plugins)
- [Database plugins](#database-plugins)
- [Testing a plugin](#testing-a-plugin)

## Where plugins come from

udl finds plugins in two places:

- **Entry points**, for plugins in an installed package. udl's own built-in plugins are
  registered this way too. The entry point's name must be the plugin's `name`:

  ```toml
  # the plugin package's pyproject.toml
  [project.entry-points."udl.sites"]
  example-video = "udl_example.site:ExampleVideo"

  [project.entry-points."udl.downloaders"]
  example-video = "udl_example.downloader:ExampleVideoDownloader"
  ```

  Install the package into udl's environment: `uv tool install --editable '.[browser]' --with
  ../udl-example` from udl's checkout.

- **Local plugin files**: every `*.py` file in the plugin dirs (`plugin_dirs`, by default
  `~/.config/udl/plugins`). There is nothing to package or install. udl registers each class in
  the file that subclasses one of the base classes and is not abstract. It ignores classes the
  file only imports.

Plugins load in this order: udl's own, then other packages', then each plugin dir in turn. A
later plugin with the same name replaces an earlier one, with a warning, so a local file can
replace a built-in plugin. A plugin that fails to load is reported, not fatal.

`udl plugins list` shows every plugin: its type, its source (`builtin`, `installed` or `local`),
where it comes from, which plugin it replaced, and whether it can run.

**What a plugin needs installed.** Declare it in `requires`, and `udl plugins list` says what is
missing instead of the plugin failing when it runs:

```python
from udl.plugins.requirements import Requirement

requires = (
    Requirement.binary("ffmpeg", hint="Install ffmpeg, e.g. `sudo pacman -S ffmpeg`."),
    Requirement.module("mutagen", hint="`uv tool install ... --with mutagen`"),
)
```

Import an optional module where you use it, not at the top of the file. Otherwise the plugin
cannot even load without it, and udl cannot say what is missing.

**Errors.** Raise udl's errors (`udl.errors`), each with a message and, when there is one, a
hint that tells the user what to do. They reach the user the same way on the command line, the
API and the MCP server.

Plugins are fully typed in udl's own code (`pyright --strict`), and the examples here pass it.

## Site plugins

A site plugin gives one site's behaviour, as code. Every hook is optional: `SitePlugin` itself
does nothing, and a plugin overrides only the hooks it needs.

| Hook | When udl calls it | It returns |
|---|---|---|
| `matches(url)` | to find a URL's plugin | whether the plugin handles `url`. By default, whether it is on `domains` |
| `async transform_urls(url)` | when a download is asked for | the URLs to download instead (for example a short link's usual form, or a list's items). Never empty |
| `strategy(url)` | when choosing downloaders | the strategy to try first, or `None` to leave it to udl |
| `async prepare(ctx)` | before every try, whichever downloader makes it | the context to download with. udl takes only its **headers and cookies** |
| `output_dir(url)` | when choosing the folder | the folder for the URL's files, or `None` |
| `async post_process(result)` | after a download has worked (and been recorded) | the result, after the plugin has worked on the files. They must exist |

```python
"""
--------------------
- example_video.py -
--------------------

Example Video: short links, a token, a folder of its own, and files named by their id
"""

# System Imports
import os
from pathlib import Path

# Local Imports
from udl.core.urls import URL
from udl.db.models import Strategy, StrategySource
from udl.errors import AuthRequired
from udl.plugins.downloaders.base import DownloadContext, DownloadResult
from udl.plugins.sites.base import SitePlugin


class ExampleVideo(SitePlugin):
    """https://video.example.com/v/<id>, and its short links https://exv.example/<id>."""

    name = "example-video"
    domains = ("video.example.com", "exv.example")

    async def transform_urls(self, url: URL) -> list[URL]:
        # A short link is the video's page, so it is downloaded once however it was linked.
        if url.host == "exv.example":
            return [URL.parse(f"https://video.example.com/v{url.path}")]
        return [url]

    def strategy(self, url: URL) -> Strategy | None:
        return Strategy(site_key=url.site_key, tool="yt-dlp", source=StrategySource.PLUGIN)

    async def prepare(self, ctx: DownloadContext) -> DownloadContext:
        token = os.environ.get("EXAMPLE_VIDEO_TOKEN")
        if token is None:
            raise AuthRequired("Example Video needs an API token", hint="Set EXAMPLE_VIDEO_TOKEN.")
        headers = {**ctx.headers, "Authorization": f"Bearer {token}"}
        return ctx.model_copy(update={"headers": headers})

    def output_dir(self, url: URL) -> Path | None:
        return Path.home() / "Videos" / "Example Video"

    async def post_process(self, result: DownloadResult) -> DownloadResult:
        video = URL.parse(result.url).path.rsplit("/", 1)[-1]
        files = [file.rename(file.with_stem(f"{video} {file.stem}")) for file in result.files]
        return result.model_copy(update={"files": files})
```

Drop the file into `~/.config/udl/plugins/` and `udl plugins list` lists it.

**`domains`** are hosts, in lower case, with no scheme, port or path. A plugin handles them and
their subdomains (`example.com` covers `www.example.com`). A domain that is not written as a
host is reported as a problem with the plugin. When several plugins match a URL, the lowest
`priority` is asked.

**Precedence:** what the request asks for beats the plugin, and the plugin beats what udl has
learned.

- **Folder:** the request's `--output`, then `output_dir`, then the site's folder, then the
  config's.
- **Downloader:** the request's `--tool` is the only candidate. Otherwise the plugin's `strategy`
  is tried first, before the strategies udl has learned and the probe chain. udl never records
  it, because the plugin gives it every time.
- **URLs** from `transform_urls` each become a job of their own, duplicates dropped. They are
  not transformed again.

**When a plugin fails.** A hook that only advises (`matches`, `transform_urls`, `strategy`,
`output_dir`) is skipped when it raises or returns the wrong type. udl logs the bug with its
traceback and carries on as if the plugin had nothing to say, so a broken plugin never stops a
download. `prepare` and `post_process` take part in the download, so:

- a `UDLError` they raise on purpose (for example `AuthRequired` or `NotFound`) is the download's
  error;
- anything else is a `PluginFailed` naming the plugin, and the download stops.

udl makes one instance of each site plugin, with no arguments. A plugin that cannot start, or is
missing something in `requires`, is left out with a warning.

`udl.plugins.sites.youtube` is udl's own example: it turns every form of a YouTube video link
into its watch URL, and has yt-dlp download it.

## Downloader plugins

A downloader is one configured tool. It probes a URL (can I download this, and what media is
there?) and downloads it.

```python
"""
-------------------------
- example_downloader.py -
-------------------------

A downloader for Example Video, whose pages (https://video.example.com/v/<id>) play the file
<cdn>/<id>.mp4: it finds the file, and has http download it
"""

# System Imports
import re
from typing import Final

# Local Imports
from udl.errors import UnsupportedURL
from udl.plugins.downloaders.base import (
    DownloadContext,
    Downloader,
    DownloaderOptions,
    DownloadResult,
    Handoff,
    MatchResult,
    MediaCandidate,
    ProbeContext,
    ProbeResult,
)

_PAGE: Final = re.compile(r"https://video\.example\.com/v/(?P<id>[\w-]+)")


class ExampleVideoOptions(DownloaderOptions):
    """[tools.example-video] in the config, and a strategy's options."""

    cdn: str = "https://cdn.example.com"


class ExampleVideoDownloader(Downloader[ExampleVideoOptions]):
    name = "example-video"
    options_model = ExampleVideoOptions

    def match(self, url: str) -> MatchResult | None:
        # Offline (no network): udl tries downloaders that recognise a URL first.
        return MatchResult(confidence=0.9, detail="a video page") if _PAGE.match(url) else None

    def _file(self, url: str) -> Handoff:
        page = _PAGE.match(url)
        if page is None:
            raise UnsupportedURL(f"{url} is not an Example Video page", tool=self.name)
        file = f"{self.options.cdn}/{page['id']}.mp4"
        return Handoff(tool="http", url=file, headers={"Referer": url})

    async def _probe(self, ctx: ProbeContext) -> ProbeResult:
        handoff = self._file(ctx.url)
        media = MediaCandidate(url=handoff.url, found_by="the page's id")
        return ProbeResult(tool=self.name, ok=True, confidence=0.9, media=[media], handoff=handoff)

    async def _download(self, ctx: DownloadContext) -> DownloadResult:
        return await self.hand_off(self._file(ctx.url), ctx)
```

**What you implement:**

- **`name`:** what strategies, `--tool` and `[tools.<name>]` call the downloader.
- **`options_model`:** its options, a `DownloaderOptions` (Pydantic) model, validated from the
  config's `[tools.<name>]` with a strategy's `options` on top. Unknown keys are errors.
- **`requires`:** what it needs installed. Override `requirements()` when that depends on the
  options, as the http downloader's backend does.
- **`match(url)`** (optional): an offline guess, with no network, that it handles the URL.
- **`_probe(ctx)`:** whether it can download `ctx.url`, and the media it sees, without writing
  files.
- **`_download(ctx)`:** download `ctx.url` into `ctx.output_dir`, and return the files.

**What udl does around it.** udl calls the public `probe()` and `download()`, which check what
every downloader must:

- **Missing requirements:** a downloader that lacks something in `requires` fails with
  `ToolUnavailable`.
- **Probe failures** are returned as a failed `ProbeResult`, never raised.
- **Unexpected exceptions** become `DownloadError`s, with the downloader's name added.
- **Downloaded files** must be real, not empty, and inside the output folder. They are measured,
  and each is listed once.

**The context.** `ProbeContext` has the `url` (normalised http(s)), the `headers` and `cookies` to
send, and a `cancel` token. `DownloadContext` adds:

- `output_dir`: it exists, and is absolute.
- `filename_template`: for example `{title}.{ext}`, or `None` to let the tool name files. Fill it
  in with `render_template()`, which keeps each field to one path segment.
- `progress`: report `DownloadProgress` to it.
- `downloader_for`: gives the downloader to hand media off to.

**Fail with the error that says what happened.** udl's next move depends on it:

| Raise | When | udl then |
|---|---|---|
| `UnsupportedURL` | the URL is not one it handles | tries the next candidate |
| `ExtractionFailed` | it handles the site but found no media | tries the next candidate |
| `AuthRequired` | the site wants a login (401/403) | tries again with the site's cookies, then the browser |
| `RateLimited(retry_after=…)` | the site is throttling | waits, then tries the same tool again |
| `NetworkError` | a connection problem | tries again |
| `NotFound` | the content is gone | stops |
| `ToolUnavailable` | something it needs is missing | skips the tool |

Only failures that are the strategy's fault (`UnsupportedURL`, `ExtractionFailed`,
`AuthRequired`) count against the strategy that chose the downloader.

**Being a good citizen:**

- **Cancelling:** stop soon after `ctx.cancel` is cancelled, and raise `Cancelled`. Use
  `cancellable(awaitable, ctx.cancel)` for async work, and `run_blocking(call,
  cancel=ctx.cancel)` for a blocking library, which must check the token itself.
- **Progress:** from a worker thread, report through `threadsafe(ctx.progress)`.
- **Handing off:** when a downloader finds media rather than downloading it (as the scraper and
  the browser do), give a `Handoff` in its probe and download with `hand_off()`. Use
  `hand_off_best()` to try several finds in turn.
- **HTTP requests:** make them with udl's `http_client()`, or at least send `USER_AGENT`
  (`udl.plugins.downloaders.http`). Many sites refuse a library's User-Agent.
- **Secrets:** never put cookies or headers on a command line, which other users of the machine
  can read. Write them to a file in a `tempfile.TemporaryDirectory()`.

The built-in downloaders are the fuller examples: `http.py` (three backends), `scraper.py` and
`browser.py` (handoffs), `ytdlp.py` and `gallerydl.py` (blocking libraries in worker threads).

## Database plugins

A database plugin stores udl's three kinds of record (`Site`, `Strategy` and `DownloadRecord`,
from `udl.db.models`). It works with records, not SQL, so a database that is not SQL works too.

**What every database must do:**

- **Return copies.** Changing a model the database returned never changes what it stores.
- **Keep ids.** Records have UUIDs, which are the same in every database, so `udl db sync` can
  copy records between databases. Strategies and downloads refer to their site by its `site_key`.
- **`upsert_site`** matches on `key`. On an update it keeps the stored `id` and `created_at`, and
  sets `updated_at` to now. Two writers storing the same new site at once must store it once.
- **`upsert_strategy`** matches on `id`, and raises `DatabaseError` when its site is not stored.
- **`record_outcome`** counts a success or failure **atomically**: concurrent outcomes are all
  counted.
- **Keep times exactly**, to the microsecond, as the same instant whatever their timezone.
- **Order lists** as the contract says: sites by key, strategies in `strategy_order`, downloads
  newest first (then by id).
- **Raise `DatabaseError`** for problems with the database itself. Never put the URL's password,
  or the values being stored, in its message: API clients read it.
- **Version the schema.** `connect()` runs `migrate()`, which creates a new database and brings
  an older one up to date. Refuse a database made by a newer udl, with a hint to upgrade.

The connection's settings arrive as `self.connection` (its `name`, `url` and so on). Every
method's exact behaviour is in `udl/plugins/databases/base.py` and the contract suite.

**A SQL database** is mostly a name, a URL and a driver: subclass `SQLAlchemyDatabase`, which
maps the models to tables, migrates them, and implements every method. udl's PostgreSQL plugin
is, in essence:

```python
class PostgresPlugin(SQLAlchemyDatabase):
    name = "postgres"
    requires = (Requirement.module("psycopg", hint="Install udl's postgres extra."),)

    def __init__(self, connection: DBConnectionConfig) -> None:
        super().__init__(connection)
        if self.url.get_backend_name() not in ("postgresql", "postgres"):
            raise ConfigError(f"{connection.name!r} is not a PostgreSQL URL")
        self.url = self.url.set(drivername="postgresql+psycopg")  # its async driver

    def _lock_schema(self, conn: Connection) -> None:
        # Several udls may connect to a new shared database at once: one migrates it.
        conn.execute(select(func.pg_advisory_xact_lock(_SCHEMA_LOCK)))
```

`_create_engine()` tunes the engine (the SQLite plugin uses one pooled connection), and
`_lock_schema()` serialises migrations on a server many udls share. A change to udl's tables
needs a step appended to `SQLAlchemyDatabase.migrations`.

**A database that is not SQL** implements `DatabasePlugin` itself. `mongodb.py` is the example:
each record is a document of its JSON form. Times are written at a fixed width in UTC, so they
sort as text and keep their microseconds. Its updates do their own arithmetic (`$inc`), so
outcomes are counted atomically.

## Testing a plugin

udl's tests include a **contract suite** for each kind of plugin: one set of tests that every
plugin of that kind must pass. In a checkout of udl, a new plugin starts by adding itself to its
suite and watching it fail:

| Kind | Suite | Add the plugin to |
|---|---|---|
| site | `tests/contract/test_site_contract.py` | `CASES`, with URLs on the sites it handles |
| downloader | `tests/contract/test_downloader_contract.py` | `CASES`, with the fixture site's pages it handles |
| database | `tests/contract/test_database_contract.py` | `CONNECTIONS`, with the URL of a fresh, empty database |

The downloader suite runs against a local fixture site (`tests/support/fixture_site.py`), never
the internet. The PostgreSQL and MongoDB plugins get a server from fixtures in
`tests/conftest.py`, which start a throwaway Docker container (`tests/support/servers.py`). A new
database server can be added the same way.

An end-to-end test of a local plugin file is in `tests/integration/test_site_plugins.py`: it
writes a plugin into the plugin dir, and downloads through it from the fixture site.

`make check` runs everything (ruff, pyright strict, pytest with coverage). `make smoke` also
tries udl against real sites.
