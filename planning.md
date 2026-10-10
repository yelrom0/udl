# UDL: Implementation Plan

> One tool (`udl`) that works out **how** to download a URL, remembers that per site, and exposes every
> feature identically through a **CLI** (Typer), an **HTTP API** (FastAPI + Granian) and an **MCP server**.
>
> All work is done **TDD-style** and must be fully **typed** (Pydantic / SQLModel models wherever data
> crosses a boundary). See [Working agreement](#working-agreement-tdd--typing).

---

## Progress tracker

Task IDs (e.g. `3.4`) are referenced in the detailed sections below. Tick a task only when it meets the
[Definition of Done](#definition-of-done-applies-to-every-task).

- [ ] **Phase 0: Foundations, tooling & cleanup**
  - [x] 0.1 `git init` + `.gitignore` (`.venv/`, `__pycache__/`, `*.db`, coverage output)
  - [x] 0.2 Move package management to **uv** (see [Package management](#package-management-uv))
    - [x] 0.2.1 Install uv (`sudo pacman -S uv`) and add a user-level `~/.config/uv/uv.toml` with `link-mode = "clone"` so every project's `.venv` reflinks from one shared cache
    - [x] 0.2.2 Write `pyproject.toml` (`uv_build` backend, src layout, `browser` + `postgres` extras, `dev` dependency group, `py.typed`) + `.python-version` (`uv python pin 3.14`); add deps with `uv add`
    - [x] 0.2.3 Delete `requirements.txt` and the pip-built `.venv`, run `uv sync`, generate `uv.lock` (commit it in 0.1); all deps import on Python 3.14.7
    - [x] 0.2.4 Check shared disk usage with `btrfs filesystem du`: `.venv` is 556M apparent but only 25 MiB exclusive
  - [x] 0.3 Quality tooling: pytest (+ asyncio, cov, respx, pytest-httpserver, hypothesis), pyright **strict**, ruff; a single `make check` command; pre-commit hook (including `uv-lock`); coverage gate
  - [x] 0.4 Restructure into the `src/udl/` package (see [Layout](#project-layout))
  - [x] 0.5 🔴→🟢 Write regression tests for each existing DB bug (replacing `test.py`), then fix them (see [Phase 0 notes](#phase-0-notes-existing-code-issues))
  - [x] 0.6 🔴→🟢 Test that two DB instances with different URLs coexist, then remove the per-class singleton
  - [ ] 0.7 `Settings` model (pydantic-settings): TOML file + `UDL_*` env vars + `platformdirs` defaults
  - [ ] 0.8 `UDLError` hierarchy + logging setup
  - [ ] 0.9 Test fixture site: a local HTTP server with a direct file, a `<video>` page, an iframe embed, a JS-rendered page and a cookie-gated (401) page

- [ ] **Phase 1: Plugin system & database layer**
  - [ ] 1.1 Plugin registry: entry points (`udl.databases`, `udl.downloaders`, `udl.sites`) + local plugin dir
  - [ ] 1.2 Domain models: SQLModel bases (`Site`, `Strategy`, `DownloadRecord`) + table variants, typed JSON fields
  - [ ] 1.3 `DatabasePlugin` ABC + **shared contract test suite** (parametrised over every DB plugin)
  - [ ] 1.4 `memory` DB plugin (test double that passes the contract suite)
  - [ ] 1.5 `SQLAlchemyDatabase` base (async SQLModel), usable by any SQLAlchemy URL
  - [ ] 1.6 `sqlite` plugin: the default DB, created automatically in the user data dir
  - [ ] 1.7 `DBManager`: multiple named connections, read policy (`first` / `merge`), write policy (`primary` / `all` / named)
  - [ ] 1.8 Schema versioning (`schema_version` table + `migrate()` on connect)

- [ ] **Phase 2: Downloader plugins**
  - [ ] 2.1 `Downloader` ABC, per-plugin `Options` model, `ProbeResult` / `DownloadResult` models + **contract test suite**
  - [ ] 2.2 Error taxonomy mapping (table-driven tests per tool: `AuthRequired`, `Unsupported`, `RateLimited`, …)
  - [ ] 2.3 `yt-dlp` (in-process API, offline extractor match, `extract_info(download=False)`, progress hooks)
  - [ ] 2.4 `gallery-dl` (offline `extractor.find()`, in-process job)
  - [ ] 2.5 `http` direct-file downloader: `httpx` by default, `curl` / `wget` backends when installed, resume support
  - [ ] 2.6 `scraper` (static HTML, `selectolax`): heuristic media discovery, then hand off to `http` / `yt-dlp`
  - [ ] 2.7 `browser` (Playwright): network sniffing for media/manifest URLs + cookie capture, then hand off
  - [ ] 2.8 Auth (401/403): `cookies_from_browser`, persistent browser profile, `udl browser login`

- [ ] **Phase 3: Resolution engine & service layer**
  - [ ] 3.1 URL normalisation + site key (registrable domain via `tldextract`), with property-based tests
  - [ ] 3.2 Resolver precedence: request override, then site plugin, then stored strategy (all DBs), then probe
  - [ ] 3.3 Probe chain: offline matches first, then network checks, then scraper, then browser
  - [ ] 3.4 Execute with fallback: try candidates until a download **actually succeeds**
  - [ ] 3.5 Record winning strategy + download history through `DBManager`; update success/failure counters
  - [ ] 3.6 Self-healing: a stored strategy that fails N times in a row gets re-probed and replaced
  - [ ] 3.7 Output dir resolution (request, then site, then config) + filename templates
  - [ ] 3.8 Multi-URL batches: async concurrency with global + per-site limits
  - [ ] 3.9 Job manager: progress events, cancellation, persisted as `DownloadRecord`
  - [ ] 3.10 `UDLService` façade + operations registry (the only thing interfaces call)

- [ ] **Phase 4: Interfaces (CLI, API, MCP)**
  - [ ] 4.1 🔴 **Parity test first**: every registered operation must have a CLI command, an API route and an MCP tool
  - [ ] 4.2 CLI: Typer app + default-command shim so `udl <url>...` == `udl get <url>...`; add `[project.scripts]` and install system-wide with `uv tool install --editable .`
  - [ ] 4.3 CLI: all [Feature Matrix](#feature-matrix-source-of-truth-for-parity) commands + rich progress + `--json` output
  - [ ] 4.4 API: FastAPI routes from the Feature Matrix, job endpoints, SSE progress
  - [ ] 4.5 API: `udl serve` runs Granian; localhost bind by default, optional API key, allowed output roots
  - [ ] 4.6 MCP: tools from the Feature Matrix (official `mcp` 2.x SDK, `MCPServer`)
  - [ ] 4.7 MCP: `udl mcp` (stdio) + streamable HTTP mounted at `/mcp` in the API

- [ ] **Phase 5: Tag chains (user-defined extraction)**
  - [ ] 5.1 `TagChain` model with discriminated-union step types
  - [ ] 5.2 One-line shorthand parser (`iframe#player@src >> video source@src`) with round-trip property tests
  - [ ] 5.3 Static chain executor (`selectolax`)
  - [ ] 5.4 Browser chain executor (Playwright: `wait_for`, `click`, `network` steps)
  - [ ] 5.5 `suggest_chain`: scan a page, rank candidate media elements, generate stable selectors
  - [ ] 5.6 `udl chain wizard <url>`: suggest, pick, test, save
  - [ ] 5.7 Chain import/export (YAML files ↔ DB)
  - [ ] 5.8 Expose suggest / test / set / import / export via CLI, API and MCP
  - [ ] 5.9 "All tools failed" returns an actionable hint pointing at chain definition

- [ ] **Phase 6: Site behaviour plugins**
  - [ ] 6.1 `SitePlugin` ABC (`matches`, `transform_urls`, `strategy`, `prepare`, `output_dir`, `post_process`) + contract tests
  - [ ] 6.2 Hook site plugins into resolver/executor with documented precedence
  - [ ] 6.3 Example built-in site plugin
  - [ ] 6.4 Local single-file plugins in `~/.config/udl/plugins/` (no packaging needed)
  - [ ] 6.5 `plugins list` shows type, source and availability (e.g. a missing binary)

- [ ] **Phase 7: Hardening & docs**
  - [ ] 7.1 `postgres` plugin (SQLAlchemy base) passing the DB contract suite
  - [ ] 7.2 A non-SQL plugin (e.g. MongoDB) passing the same suite, which proves the abstraction
  - [ ] 7.3 `db sync` between connected DBs (UUID keys make this collision-free)
  - [ ] 7.4 Opt-in `network` smoke tests against real sites (never in the default run)
  - [ ] 7.5 Plugin authoring guide (DB / downloader / site) + README usage docs
  - [ ] 7.6 Security review: API exposure, output path restriction, cookie/profile storage permissions

---

## Working agreement (TDD + typing)

### TDD loop

Every task is built as a series of **red → green → refactor** cycles:

1. **Red:** write the smallest test that describes the next behaviour. Run it and **watch it fail** for the
   expected reason.
2. **Green:** write the minimum code that makes it pass.
3. **Refactor:** clean up while the whole suite stays green; `make check` passes before committing.

Rules of thumb:

- Tests describe **behaviour through public interfaces** (ABCs, service operations, CLI/API/MCP surfaces),
  not private helpers, so refactors don't break them.
- **Interfaces get contract suites first.** Every plugin type (DB, downloader, site) has one parametrised test
  suite that each implementation must pass. A new plugin starts by adding itself to the suite's params (red).
- **Bugs get a failing regression test before the fix** (Phase 0 starts this way).
- **Fakes over mocks:** `MemoryDatabase`, `FakeDownloader` and `FakeSitePlugin` are real implementations of
  the ABCs. Use `respx` only to stub `httpx` at the edge.
- Every test commit is small: one behaviour, one test, one implementation step.

### Test layers

| Layer | Scope | Tools | Default run? |
|---|---|---|---|
| Unit | Pure logic, fakes, `tmp_path` only, no network | pytest, hypothesis, respx | ✅ |
| Contract | Every plugin of a type against one shared suite | parametrised pytest | ✅ |
| Integration | Real yt-dlp / gallery-dl / httpx / Playwright against the **local fixture site** (0.9) | pytest-httpserver, `@pytest.mark.integration` | ✅ (browser tests skip if Chromium is missing) |
| Interface | CLI via Typer `CliRunner`, API via `httpx.ASGITransport`, MCP via the SDK's in-memory client session | pytest | ✅ |
| Smoke | Real sites | `@pytest.mark.network` | ❌ opt-in |

Property-based tests (hypothesis) for parsers and normalisers: URL normalisation/site keys (3.1), the tag chain
shorthand round-trip (5.2) and filename templating (3.7).

### Typing rules

- `pyright --strict` passes; `py.typed` ships with the package; no unannotated public functions.
- **SQLModel** for anything persisted; **Pydantic** for everything else that crosses a boundary: config,
  requests/responses, plugin options, probe/download results, progress events, tag chains.
- No `dict[str, Any]` across module boundaries. JSON columns (`Strategy.options`, `Strategy.chain`) are
  (de)serialised through their owning model inside the repository layer, so callers only see typed models.
- `StrEnum` for closed sets (tool status, job status, read/write policies); `Annotated[..., Field(...)]` for constraints.
- Untyped third-party libraries (e.g. gallery-dl) are wrapped inside their plugin module behind a typed façade.
  Any `# pyright: ignore` must stay inside that adapter. The yt-dlp info dict gets a `TypedDict` for the fields we use.

### Definition of Done (applies to every task)

- [ ] Tests were written first and observed failing
- [ ] Implementation passes; refactor done with the suite green
- [ ] `make check` passes: ruff + pyright strict + pytest with **≥ 90 % branch coverage** (ratchet upwards, never down), all run through `uv run` against the locked environment
- [ ] Boundary data is a Pydantic/SQLModel model
- [ ] If an operation was added or changed: the CLI, API and MCP adapters and their interface tests are updated and the parity test passes

---

## Requirements → where they're delivered

| # | Requirement | Tasks |
|---|---|---|
| 1 | CLI / API / MCP feature parity | 3.10, Phase 4 (parity test 4.1) |
| 2 | `udl <url>...` checks connected DB(s) for the right tool | 3.1–3.5, 4.2 |
| 3 | User-specified download folder | 3.7 |
| 4 | Unknown site → try all tools → easy tag chain fallback | 3.3, Phase 5 |
| 5 | Record the working path in the DB | 3.5, 3.6 |
| 6 | DB engines as plugins | 1.1, 1.3–1.6, 7.1–7.2 |
| 7 | Multiple DBs of different types at once (SQLite default) | 1.7, 7.3 |
| 8 | Site behaviour as plugins | Phase 6 |
| + | Everything unit tested, typed, TDD | [Working agreement](#working-agreement-tdd--typing), 0.3 |
| + | uv package management with a shared global cache | 0.2, [Package management](#package-management-uv) |

---

## Architecture

```
   CLI (Typer)          API (FastAPI on Granian)          MCP (MCPServer)
        \                        |                          /
         └─────────── UDLService (operations registry) ────┘
                                 |
      ┌──────────────┬───────────┴───────┬──────────────────┐
   Resolver      JobManager          DBManager         PluginRegistry
      |                                  |           (databases / downloaders / sites)
      |                       sqlite · postgres · mongo …
      ▼
 site plugin ─► stored Strategy ─► probe chain
                                       |
            yt-dlp · gallery-dl · http (httpx/curl/wget) · scraper · browser
```

Principles:

- **Interfaces hold no logic.** They parse input into a Pydantic request, call `UDLService`, and render the
  Pydantic response. That's what makes parity cheap to keep and test.
- **Async core.** FastAPI, MCP, httpx and Playwright are async-native. Sync libraries (yt-dlp, gallery-dl)
  run in `asyncio.to_thread`. The CLI wraps calls in `asyncio.run`.
- **One plugin registry** for all three plugin kinds.

## Project layout

```
pyproject.toml  uv.lock  .python-version  Makefile  .pre-commit-config.yaml
src/udl/
  __init__.py  __main__.py  py.typed
  config.py                 # Settings (pydantic-settings)
  errors.py                 # UDLError hierarchy
  core/
    service.py              # UDLService + @operation registry
    resolver.py             # strategy selection + probe chain
    executor.py             # run candidates with fallback, record outcome
    jobs.py                 # JobManager, progress events
    urls.py                 # normalisation, site keys
    models.py               # request/response DTOs
  db/
    models.py               # Site / Strategy / DownloadRecord (+ table variants)
    manager.py              # DBManager
  plugins/
    registry.py
    databases/   base.py  memory.py  sqlalchemy.py  sqlite.py
    downloaders/ base.py  ytdlp.py  gallerydl.py  http.py  scraper.py  browser.py
    sites/       base.py  <examples>.py
  chains/        models.py  shorthand.py  executor.py  suggest.py
  interfaces/
    cli/         app.py  commands/…
    api/         app.py  routes/…
    mcp/         server.py
tests/
  conftest.py
  fixtures/site/            # HTML pages + small media files served by the fixture server
  unit/  contract/  integration/  interface/  smoke/
```

## Package management (uv)

### How the space saving works
- uv keeps **one global cache** of unpacked packages (`~/.cache/uv`). Each project still gets its own `.venv`,
  so projects stay isolated, but the files inside it are **links into the cache**. A package used by ten
  projects is stored once.
- Don't install into the system Python (`uv pip install --system`). It saves no extra space and brings back
  the version conflicts that venvs avoid.
- uv's Linux default is `hardlink`. On btrfs, use **`clone` (reflinks)** instead. It saves the same space but is
  copy-on-write. With hardlinks, a file edited in one `.venv` silently changes the cache and every other
  project, because they share the inode.
- Links only work within one filesystem. This project and `~/.cache/uv` are both on the `/home` btrfs
  subvolume (verified). Projects on another drive get plain copies, and uv warns about it.
- uv-managed Pythons (`uv python install 3.14`) are shared the same way, from `~/.local/share/uv/python`.

```toml
# ~/.config/uv/uv.toml (user-level, applies to every project on the system)
link-mode = "clone"
```

**Checking it works:** `du` counts reflinked data in full, so it will look unchanged. Use
`btrfs filesystem du -s ~/.cache/uv .venv` and compare the *Exclusive* and *Set shared* columns.

### `pyproject.toml` skeleton

```toml
[project]
name = "udl"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [                       # added via `uv add`, which writes the version bounds
  "typer", "rich", "fastapi", "granian", "mcp",
  "sqlmodel", "aiosqlite", "pydantic-settings", "platformdirs",
  "tldextract", "httpx", "yt-dlp", "gallery-dl", "selectolax",
]

[project.optional-dependencies]
browser = ["playwright"]
postgres = ["psycopg[binary]"]

[project.scripts]                      # added in 4.2, once the CLI exists
udl = "udl.interfaces.cli.app:main"

[dependency-groups]
dev = [
  "pytest", "pytest-asyncio", "pytest-cov", "pytest-httpserver", "respx",
  "hypothesis", "pyright[nodejs]", "ruff", "types-yt-dlp",
]

[build-system]
requires = ["uv_build>=0.12.24,<0.13.0"]
build-backend = "uv_build"
```

### Everyday commands

| Task | Command |
|---|---|
| Create or refresh the env from the lockfile | `uv sync` (`uv sync --all-extras` to include `browser`) |
| Add a dependency | `uv add httpx` · `uv add --dev pytest` · `uv add --optional browser playwright` |
| Run anything in the project env | `uv run udl …` · `uv run pytest` |
| Upgrade | `uv lock --upgrade`, or `uv lock --upgrade-package yt-dlp`. yt-dlp needs this often, because site extractors break |
| Global `udl` command | `uv tool install --editable '.[browser]'`; code changes apply immediately, dependency changes need `--reinstall` |
| Global dev tools | `uv tool install pre-commit` |
| Browser binaries | `uv run playwright install chromium` |
| Clear unused cache entries | `uv cache prune` |

### `make check`

```make
export UV_LOCKED := 1                # every uv command fails if uv.lock is out of date with pyproject.toml

check:
	uv lock --check
	uv run ruff format --check .
	uv run ruff check .
	uv run pyright
	uv run pytest --cov              # branch coverage + the 90 % gate live in [tool.coverage]
```

It deliberately avoids `uv sync --locked`: that sync is exact, so it would uninstall extras installed with
`uv sync --all-extras` (e.g. playwright) on every run. `uv run` syncs inexactly. The coverage threshold lives
in `pyproject.toml` so the ratchet has one home. `make fmt` applies ruff's formatting and fixes.

The pre-commit hooks are `astral-sh/uv-pre-commit` (`uv-lock`, which keeps `uv.lock` in sync) plus a local hook
that runs `make check`.

---

## Phase 0 notes: existing code issues

These are confirmed in the current code. Each one gets a failing regression test first (task 0.5 / 0.6):

| Location | Problem | Fix |
|---|---|---|
| `src/plugins/databases/base.py:38,54,128` | `Engine`, `Any` and `delete` are used but never imported | Import them, and pyright strict will catch future cases |
| `base.py:54` | `transacrion` typo | Rename to `transaction` |
| `base.py:83-95` | `_do_add` runs `create_all` (DDL) and never calls `s.add()`, so `add()` creates **tables** instead of inserting **rows**. DDL is committed through the engine even with `commit_session=False`. This is the root cause of the TODO in `test.py` | Create the schema once on connect (`migrate()`); make `add()` take model **instances** and call `s.add_all()` |
| `base.py:131-133` | `s.execute()` auto-begins a transaction, so `not s.in_transaction()` is always `False` (verified). Deletes are never committed and get rolled back when the session closes | Commit explicitly, or use the `transaction()` context manager |
| `base.py:22-27` | The singleton is keyed by class, so a second `SQLLiteEngine("sqlite:///other.db")` silently returns the first instance with the old URL. This blocks requirement 7 | `DBManager` owns instances keyed by configured connection name |
| `base.py:18` | `sqlite:///database.db` is relative to the CWD, so a new DB appears wherever `udl` runs | Default to `platformdirs.user_data_dir("udl")/udl.db` |
| `base.py:105` | `list()` prints instead of returning data | Return typed models and let the interface render them |
| `src/plugins/downloaders/base.py:10` | Method has a comment-only body (`IndentationError`) and no `self` | Replaced by the `Downloader` ABC in 2.1 |
| `sqllite.py` | Misspelt module/class name | `sqlite.py` / `SQLitePlugin` |

---

## Phase 1: Plugin system & database layer

### Tests first
- Registry: discovers a dummy entry point; loads a `.py` from a temp plugin dir; a local plugin overrides a
  built-in with the same name and logs a warning; a broken plugin is reported, not fatal.
- DB contract suite (`tests/contract/test_database_contract.py`), parametrised over `[memory, sqlite, …]`:
  upsert/get/list/delete sites; strategies ordered correctly; `record_outcome` updates counters; download
  history round-trips; JSON fields come back as typed models; `migrate()` is idempotent.
- DBManager: `first` read policy returns the highest-priority hit; `merge` dedupes by
  `(site, url_pattern, tool)`; writes respect `primary` / `all` / named lists; read-only connections are never
  written; a failing secondary DB logs a warning instead of failing the operation.

### Data model (UUID primary keys, so records can be synced between DBs)

- **Site**: `id`, `key` (eTLD+1, unique), `name`, `default_output_dir: Path | None`, `notes`, `created_at`, `updated_at`
- **Strategy** (many per site): `id`, `site_id`, `url_pattern: str | None` (regex on path; `None` = whole site),
  `tool: str` (downloader plugin name), `options` (validated by that downloader's `Options` model),
  `chain: TagChain | None`, `requires_auth: bool`, `source: StrategySource` (`probe | manual | plugin | import`),
  `priority`, `success_count`, `failure_count`, `consecutive_failures`, `last_success_at`, `last_error`, `enabled`
- **DownloadRecord**: `id`, `url`, `site_id`, `strategy_id`, `tool`, `status: JobStatus`, `output_dir`,
  `files: list[Path]`, `bytes`, `started_at`, `finished_at`, `error`

Pattern: `class StrategyBase(SQLModel)` is plain Pydantic, so non-SQL plugins reuse it.
`class StrategyTable(StrategyBase, table=True)` lives with the SQLAlchemy plugin.

### `DatabasePlugin` (repository-level, not SQL-specific, so MongoDB or similar can be a plugin)

```python
class DatabasePlugin(ABC):
    name: ClassVar[str]                                   # "sqlite", "postgres", "memory", …
    def __init__(self, connection: DBConnectionConfig) -> None: ...
    async def connect(self) -> None: ...                  # runs migrate()
    async def close(self) -> None: ...
    async def health(self) -> bool: ...
    async def get_site(self, key: str) -> Site | None: ...
    async def list_sites(self, query: SiteQuery) -> list[Site]: ...
    async def upsert_site(self, site: Site) -> Site: ...
    async def delete_site(self, key: str) -> bool: ...
    async def strategies_for(self, site_key: str) -> list[Strategy]: ...
    async def upsert_strategy(self, strategy: Strategy) -> Strategy: ...
    async def record_outcome(self, strategy_id: UUID, outcome: Outcome) -> None: ...
    async def add_download(self, record: DownloadRecord) -> None: ...
    async def update_download(self, record: DownloadRecord) -> None: ...
    async def list_downloads(self, query: DownloadQuery) -> list[DownloadRecord]: ...
```

`SQLAlchemyDatabase` implements this once with `create_async_engine` + SQLModel's `AsyncSession`
(`sqlite+aiosqlite`, `postgresql+psycopg`). A new SQL engine plugin is then mostly just a name, a URL scheme
and a driver dependency.

### Plugin registry

```toml
# pyproject.toml: built-ins are registered the same way third-party plugins are
[project.entry-points."udl.databases"]
sqlite = "udl.plugins.databases.sqlite:SQLitePlugin"
[project.entry-points."udl.downloaders"]
yt-dlp = "udl.plugins.downloaders.ytdlp:YtDlpDownloader"
# …
```

Local plugins: every `*.py` in `plugin_dirs` is imported, and any `DatabasePlugin` / `Downloader` /
`SitePlugin` subclass it defines is registered.

---

## Phase 2: Downloader plugins

### Tests first
- Downloader contract suite, parametrised over every downloader. `probe()` never writes files.
  `download()` writes into `output_dir` only. Results validate as models. Cancellation stops the download
  within N seconds. `available()` is false when the binary or package is missing.
- Error mapping: table-driven tests from raw tool errors (yt-dlp messages, gallery-dl exit codes, HTTP status
  codes) to `UDLError` subclasses.
- Integration tests against the fixture site (0.9): each plugin succeeds on the page it's meant for and
  returns `Unsupported` on the others.

### Interface

```python
class Downloader(ABC):
    name: ClassVar[str]
    options_model: ClassVar[type[BaseModel]]              # e.g. YtDlpOptions(format="bv*+ba/b", …)
    requires: ClassVar[tuple[str, ...]] = ()              # binaries / importable modules
    def available(self) -> bool: ...
    def match(self, url: URL) -> MatchResult | None: ...  # offline, no network (optional)
    async def probe(self, ctx: ProbeContext) -> ProbeResult: ...
    async def download(self, ctx: DownloadContext) -> DownloadResult: ...
```

- `ProbeResult`: `ok`, `confidence: float`, `title`, `media: list[MediaCandidate]`, `handoff: Handoff | None`
  (scraper/browser return a concrete media URL + headers/cookies for another downloader), `error: UDLError | None`
- `DownloadContext`: `url`, `output_dir`, `filename_template`, `options` (typed), `cookies`, `headers`,
  `progress: ProgressSink`, `cancel: CancelToken`

### Per-tool notes

| Plugin | Offline match | Probe | Download | Notes |
|---|---|---|---|---|
| `yt-dlp` | Any extractor except `Generic` whose `suitable(url)` is true | `extract_info(download=False)` | `YoutubeDL` + progress hooks | A Generic-extractor hit gets low confidence. Use a new `YoutubeDL` per job. Cancel by raising from the progress hook |
| `gallery-dl` | `gallery_dl.extractor.find(url)` | First item's metadata | In-process `DownloadJob` (subprocess fallback) | Untyped and less stable as a library, so keep it behind the plugin's typed façade |
| `http` | URL has a media extension | `HEAD` (fallback: ranged `GET`), then media / `octet-stream` type or `Content-Disposition: attachment` | Streamed `httpx` with `Range` resume | `backend: httpx \| curl \| wget` option; the external binaries are optional |
| `scraper` | n/a | Fetch HTML and look for `<video>/<audio>/<source>`, `og:video`, JSON-LD `contentUrl`, media `<a href>`, `.m3u8/.mpd` in scripts | Hand off to `http` (files) or `yt-dlp` (HLS/DASH) | `selectolax` (Lexbor) is typically an order of magnitude faster than BeautifulSoup. Add `lxml` only if XPath steps are needed |
| `browser` | n/a | Headless Chromium: record responses with media content types or manifests; collect cookies | Hand off with cookies + `Referer`/UA | `udl[browser]` extra + `uv run playwright install chromium`; persistent profile |

### Error taxonomy and how the resolver reacts

| Error | Resolver action | Counts against strategy? |
|---|---|---|
| `UnsupportedURL`, `ExtractionFailed` | Try the next candidate | yes |
| `AuthRequired` (401/403, "sign in" messages) | Retry with cookies, then fall back to `browser` | yes |
| `RateLimited` | Back off (honour `Retry-After`) and retry the same tool | no |
| `NetworkError` | Retry with backoff | no |
| `NotFound` | Stop: the content is gone, not the strategy | no |
| `ToolUnavailable` | Skip the tool and note it in `plugins list` | no |

### Auth-gated sites (2.8)
1. Retry the same tool with cookies, from `cookies_from_browser` (yt-dlp `cookiesfrombrowser`, gallery-dl
   `cookies-from-browser`) or from the persistent udl browser profile.
2. Fall back to the `browser` plugin, which loads the page with that profile, captures media URLs and cookies,
   and hands off.
3. `udl browser login <url>` opens a **headed** browser on the persistent profile so the user logs in once.
4. The winning strategy is saved with `requires_auth=True`, so cookies are attached upfront next time.

---

## Phase 3: Resolution engine & service layer

### Tests first
- URL normalisation (hypothesis): idempotent; `www.`/`m.` and tracking params handled; the site key equals the
  registrable domain (`a.b.example.co.uk` → `example.co.uk`).
- Resolver precedence using `FakeSitePlugin` / `MemoryDatabase` / `FakeDownloader`: request `tool` beats the
  site plugin, which beats the stored strategy, which beats the probe. `url_pattern` specificity orders
  strategies.
- Executor: falls through to the next candidate when a download fails; records only verified successes;
  `NotFound` stops without penalising; `heal_threshold` failures trigger a re-probe; manual strategies are
  never auto-disabled.
- Output dirs: precedence, `~`/env expansion, directory creation, template rendering (hypothesis: never escapes
  `output_dir`).
- Concurrency: never more than `max_concurrent` running, or `max_per_site` for one site.
- Jobs: event order `queued → started → progress* → finished|failed|cancelled`; cancellation; on restart,
  in-flight records become `failed: interrupted`.

### Resolution flow

```
udl URL...
 └─ for each URL (concurrently, ≤ max_concurrent, ≤ max_per_site per site):
     1. normalise URL, site_key = registrable domain (tldextract, offline suffix-list snapshot)
     2. site plugin matches?  → may rewrite URL(s) and supply strategy / options / output dir
     3. request.tool set?     → use it, skip lookup
     4. DBManager.strategies_for(site_key) → filter by url_pattern → order by
        (pattern specificity, priority, success rate)
     5. none, or all disabled → probe chain → ordered candidates
     6. try candidates until a download succeeds (file exists, size > 0)
     7. success → upsert Strategy (source=probe) + DownloadRecord; failure → record_outcome
     8. all failed → NoStrategyFound with a hint to suggest/define a tag chain (Phase 5)
```

### Probe chain (order configurable via `probe_order`)
1. **Offline** (no network): specific yt-dlp extractors, gallery-dl extractors.
2. **`http` HEAD**: is the URL itself a file?
3. **yt-dlp Generic extractor.**
4. **`scraper` heuristics.**
5. **`browser`**: the most expensive step, so it runs last.

Stop at the first **successful download**, not the first successful probe. `udl probe <url> --all` reports
every tool's result for debugging.

### Recording rules
- Strategies are recorded **site-wide** by default. Use `sites set --pattern '^/shorts/'` when one site needs
  different tools for different sections.
- Self-healing: when `consecutive_failures >= heal_threshold` (default 3), the strategy is disabled, the URL is
  re-probed and the new winner is recorded. `manual` strategies only warn.

### Output paths
Precedence: request `output_dir` (`-o/--output`) → site plugin → `Site.default_output_dir` → config
`download_dir` (default: `platformdirs.user_downloads_dir()/udl`). `filename_template` (e.g.
`{site}/{title}.{ext}`) is translated per tool: yt-dlp `outtmpl`, gallery-dl `directory`/`filename`, or
derived for `http`.

### Jobs & progress
- `JobManager` runs one asyncio task per URL; a batch shares a group id.
- `ProgressEvent` models go onto an asyncio queue. The CLI renders rich progress bars, the API streams SSE and
  MCP sends progress notifications.
- Threaded tools report via `loop.call_soon_threadsafe`; cancellation uses a `CancelToken` checked in
  yt-dlp/gallery-dl hooks.

### Service & operations registry

```python
class UDLService:
    @operation("download")
    async def download(self, req: DownloadRequest) -> DownloadBatch: ...
    @operation("probe")
    async def probe(self, req: ProbeRequest) -> ProbeReport: ...
```

Every operation takes and returns Pydantic models, so the CLI, API and MCP share validation and serialisation.
Each adapter registers which operation it implements, and the parity test compares those sets against the
registry.

---

## Phase 4: Interfaces

### Feature Matrix (source of truth for parity)

| Operation | CLI | API | MCP tool |
|---|---|---|---|
| download | `udl <url>...` / `udl get` | `POST /downloads` | `download` |
| job status | (CLI waits) `udl jobs show <id>` | `GET /downloads/{id}` | `get_download` |
| history | `udl history` | `GET /downloads` | `list_downloads` |
| cancel | Ctrl-C / `udl jobs cancel <id>` | `DELETE /downloads/{id}` | `cancel_download` |
| probe | `udl probe <url>` | `POST /probe` | `probe_url` |
| list sites | `udl sites list` | `GET /sites` | `list_sites` |
| show site | `udl sites show <site>` | `GET /sites/{key}` | `get_site` |
| set strategy | `udl sites set <site> --tool … [--pattern …]` | `PUT /sites/{key}/strategies` | `set_site_strategy` |
| delete site/strategy | `udl sites rm <site>` | `DELETE /sites/{key}` | `delete_site` |
| suggest chain | `udl chain suggest <url>` | `POST /chains/suggest` | `suggest_chain` |
| test chain | `udl chain test <url> <chain>` | `POST /chains/test` | `test_chain` |
| save chain | `udl chain set <site> <chain>` | `PUT /sites/{key}/chain` | `set_site_chain` |
| import / export chains | `udl chain import/export` | `POST /chains/import`, `GET /sites/{key}/chain` | `import_chains`, `export_chain` |
| list DBs | `udl db list` | `GET /databases` | `list_databases` |
| sync DBs | `udl db sync --from a --to b` | `POST /databases/sync` | `sync_databases` |
| list plugins | `udl plugins list` | `GET /plugins` | `list_plugins` |
| show config | `udl config show` | `GET /config` (secrets redacted) | `get_config` |

**Interface-only (explicitly exempt in the parity test):** `udl serve`, `udl mcp`, `udl chain wizard` (an
interactive wrapper over suggest/test/set, which are available everywhere) and `udl browser login` (needs a
headed browser on the user's machine).

### Tests first
- Parity test (4.1) goes in first and stays red until all three adapters exist.
- One interface test per matrix row per adapter, all running against a `UDLService` built on fakes.
- One end-to-end `probe` through all three adapters against the fixture site, asserting identical results.

### CLI
- **Default-command shim:** prepend `get` unless a registered command name appears among the args.

  ```python
  class DefaultGetGroup(TyperGroup):
      def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
          if not any(a in self.commands for a in args):
              args = ["get", *args]
          return super().parse_args(ctx, args)

  app = typer.Typer(cls=DefaultGetGroup)
  ```
- `get` options: `-o/--output DIR`, `--tool NAME`, `--db NAME` (restrict lookup/writes), `--reprobe`,
  `--no-record`, `-i/--input FILE` (`-` = stdin), `-j/--jobs N`, `--json`.
- Global options: `--config PATH`, `-v/--verbose`, `-q/--quiet`. Exit codes: `0` all OK, `1` some failed,
  `2` usage error.

### API
- `POST /downloads` returns `202` with job ids; `?wait=true` blocks until done (handy for scripts).
  `GET /downloads/{id}/events` streams SSE.
- The service is created in FastAPI's `lifespan` and injected with `Depends`. OpenAPI at `/docs` is the base
  for the future UI.
- `udl serve [--host 127.0.0.1] [--port 8765]` runs Granian (ASGI) with **one worker**, because job state is
  in-process. Multiple workers would need an external queue (out of scope).
- Security: bind to localhost by default. When `api.api_key` is set, an `X-API-Key` header is required.
  `output_dir` must be under `api.allowed_output_roots`, otherwise the request gets a 403.

### MCP
- Built on the official `mcp` 2.x SDK: `from mcp.server.mcpserver import MCPServer`. mcp 2.x renamed
  `FastMCP` to `MCPServer`, so ignore v1 examples that import `mcp.server.fastmcp`. Tool docstrings are written for LLMs.
- `download` takes `wait: bool` (default true, with a timeout). When not waiting it returns job ids, and it
  reports progress via MCP progress notifications.
- `suggest_chain`, `test_chain` and `set_site_chain` let an AI agent teach udl an unsupported site itself.
- Transports: `udl mcp` (stdio) plus streamable HTTP mounted at `/mcp` in the API, with the MCP session
  manager wired into FastAPI's lifespan. It uses the same output-root restrictions as the API.
- Client config: `{"mcpServers": {"udl": {"command": "udl", "args": ["mcp"]}}}`. This works anywhere once
  `udl` is installed as a uv tool (4.2). Without that, use
  `"command": "uv", "args": ["run", "--project", "/path/to/udl", "udl", "mcp"]`.

---

## Phase 5: Tag chains

The "easy system" for sites no tool handles. A chain is stored on a `Strategy` (`tool = "scraper"` or
`"browser"`), so it shares the recording, syncing and import/export machinery.

### Tests first
- Model validation: unknown step types are rejected; browser-only steps in `fetch: static` are rejected.
- Shorthand parser (hypothesis): `parse(format(chain)) == chain`; invalid syntax gives a helpful error
  location.
- Executor against fixture pages: direct `<video>`, iframe → follow → `<source>`, JSON-in-`<script>`, a
  JS-rendered page (browser), a `network` capture. Relative URLs resolve against the current document.
- `suggest_chain` ranks the real media element first on each fixture page, and generated selectors are stable
  (no `nth-child` when an id/class/data-attr exists).

### Formats

One-liner shorthand (CLI-friendly): steps separated by `>>`, each a CSS selector plus `@attr` or `::text`.
When a step yields a URL and another step follows, that URL is fetched next.

```
iframe#player@src >> video > source[type*=mp4]@src
```

Full YAML (files in `~/.config/udl/sites/*.yaml`, or stored in the DB):

```yaml
site: example.com
pattern: "^/watch/"          # optional, defaults to the whole site
fetch: static                # static | browser
headers: { Referer: "{url}" }
steps:
  - select: "iframe#player"
    attr: src
    follow: true
  - select: "video > source[type='video/mp4']"
    attr: src
    pick: best               # first | all | best
handoff: http                # http | yt-dlp (for HLS/DASH)
```

| Step | Does | Mode |
|---|---|---|
| `select` + `attr` / `text` | CSS select, extract attribute or text | both |
| `follow` | Fetch the extracted URL and continue on that document | both |
| `regex` | Apply a regex (group 1) to the current values | both |
| `json` | Parse JSON (e.g. `<script type="application/json">`) and take a dotted path | both |
| `wait_for` | Wait for a selector | browser |
| `click` | Click an element (e.g. a play button) | browser |
| `network` | Capture request URLs matching a regex instead of reading the DOM | browser |

Steps are a Pydantic **discriminated union** (`Annotated[SelectStep | FollowStep | …, Field(discriminator="type")]`).

### Suggest & wizard
- `suggest_chain(url)` renders the page (static first, browser if nothing is found) and collects candidates:
  media tags, iframes, media links, `og:video`, JSON-LD, manifest URLs and network-captured media. For each it
  generates a stable selector (id → `data-*` → class chain) and a preview (URL, content type, size via
  `HEAD`), ranked by type and size.
- `udl chain wizard <url>`: numbered table → pick → test → save to DB (optionally export YAML).
- When every tool fails, the error includes the top suggestions and the exact `udl chain set …` command to run.

---

## Phase 6: Site behaviour plugins

### Tests first
- Site plugin contract suite: all hooks are optional (the base class is a valid no-op plugin), `matches` is
  consistent with `domains`, and `transform_urls` never returns an empty list.
- Resolver integration: a plugin's `strategy()` beats a stored DB strategy; request args beat the plugin;
  `post_process` sees the final file paths.

### Interface

```python
class SitePlugin(ABC):
    name: ClassVar[str]
    domains: ClassVar[tuple[str, ...]] = ()
    priority: ClassVar[int] = 0
    def matches(self, url: URL) -> bool: ...                          # default: site key in domains
    def transform_urls(self, url: URL) -> list[URL]: ...              # expand lists, normalise short/mobile links
    def strategy(self, url: URL) -> Strategy | None: ...              # force tool / options / chain
    def prepare(self, ctx: DownloadContext) -> DownloadContext: ...   # headers, cookies, tool options
    def output_dir(self, url: URL, meta: MediaMeta) -> Path | None: ...
    async def post_process(self, result: DownloadResult) -> DownloadResult: ...  # rename / move / tag
```

**Precedence (highest first):** explicit request args → site plugin → stored strategy (DB) → probe. Plugins
encode deliberate behaviour; the DB holds learned behaviour.

**When to use which:** use a strategy or tag chain (data) for *how to find the file*. Use a site plugin (code)
for *behaviour*: auth flows, URL rewriting, per-site folders, post-processing, API-based extraction.

---

## Configuration example

Without a config file, udl uses a single SQLite connection named `local` in the user data dir.

```toml
download_dir = "~/Downloads/udl"
filename_template = "{site}/{title}.{ext}"
max_concurrent = 4
max_per_site = 2
probe_order = ["yt-dlp", "gallery-dl", "http", "scraper", "browser"]
heal_threshold = 3
cookies_from_browser = "firefox"          # optional
plugin_dirs = ["~/.config/udl/plugins"]

[db]
read_policy = "first"                     # first | merge
write_policy = "primary"                  # primary | all | ["local", "shared"]

[[db.connections]]
name = "local"
plugin = "sqlite"
url = "sqlite+aiosqlite:///~/.local/share/udl/udl.db"
primary = true
priority = 0                              # lower = consulted first

[[db.connections]]
name = "shared"
plugin = "postgres"
url = "postgresql+psycopg://udl@nas/udl"
priority = 10
read_only = true

[api]
host = "127.0.0.1"
port = 8765
api_key = ""                              # set to require X-API-Key
allowed_output_roots = ["~/Downloads"]

[browser]
headless = true
profile_dir = "~/.local/share/udl/browser-profile"

[tools.yt-dlp]                            # validated by YtDlpOptions
format = "bv*+ba/b"
```

## Dependencies

| Purpose | Packages |
|---|---|
| CLI | `typer`, `rich` |
| API | `fastapi`, `granian` |
| MCP | `mcp` 2.x (`MCPServer`; `FastMCP` was the v1 name) |
| DB | `sqlmodel`, `aiosqlite` (`psycopg` for the postgres plugin) |
| Config | `pydantic-settings`, `platformdirs` |
| URLs | `tldextract` (use the bundled suffix list so it doesn't fetch on first run) |
| HTTP | `httpx` |
| Tools | `yt-dlp`, `gallery-dl` |
| Scraping | `selectolax` |
| Browser (extra `udl[browser]`) | `playwright` (then `uv run playwright install chromium`) |
| Dev (`dev` dependency group) | `pytest`, `pytest-asyncio`, `pytest-cov`, `pytest-httpserver`, `respx`, `hypothesis`, `pyright[nodejs]` (ships its own Node), `ruff`, `types-yt-dlp` |
| Global tools (`uv tool install`) | `pre-commit`, and `udl` itself |

## Decisions (defaults that can be revisited)

- **uv** for packaging: one committed lockfile, and one shared reflinked cache for every project on the system.
- **Async core**, so the API and MCP never block on DB or network I/O.
- **Repository-level DB interface** rather than an engine-level one, so non-SQL backends can be plugins.
- **Hand-written thin adapters + parity test** rather than auto-generating MCP tools from FastAPI. MCP tools
  need LLM-oriented descriptions and different long-running semantics.
- **Record only verified successes**, site-wide by default.
- **`httpx` native** for direct files; `curl`/`wget` are optional backends.
- **`selectolax`** rather than BeautifulSoup, for speed.
- **One Granian worker**, since job state is in-process.

## Out of scope (later)

- Web UI (mentioned in the README); the API + SSE are designed to support it.
- Skipping already-downloaded URLs (download archive); `DownloadRecord` makes this straightforward.
- External job queue / multiple workers.
- Scheduled or watched downloads (playlists, channels).
