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

- [x] **Phase 0: Foundations, tooling & cleanup**
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
  - [x] 0.7 `Settings` model (pydantic-settings): TOML file + `UDL_*` env vars + `platformdirs` defaults
  - [x] 0.8 `UDLError` hierarchy + logging setup
  - [x] 0.9 Test fixture site: a local HTTP server with a direct file, a `<video>` page, an iframe embed, a JS-rendered page and a cookie-gated (401) page

- [x] **Phase 1: Plugin system & database layer**
  - [x] 1.1 Plugin registry: entry points (`udl.databases`, `udl.downloaders`, `udl.sites`) + local plugin dir
  - [x] 1.2 Domain models: SQLModel bases (`Site`, `Strategy`, `DownloadRecord`) + table variants, typed JSON fields
  - [x] 1.3 `DatabasePlugin` ABC + **shared contract test suite** (parametrised over every DB plugin)
  - [x] 1.4 `memory` DB plugin (test double that passes the contract suite)
  - [x] 1.5 `SQLAlchemyDatabase` base (async SQLModel), usable by any SQLAlchemy URL
  - [x] 1.6 `sqlite` plugin: the default DB, created automatically in the user data dir
  - [x] 1.7 `DBManager`: multiple named connections, read policy (`first` / `merge`), write policy (`primary` / `all` / named)
  - [x] 1.8 Schema versioning (`schema_version` table + `migrate()` on connect)

- [x] **Phase 2: Downloader plugins**
  - [x] 2.1 `Downloader` ABC, per-plugin `Options` model, `ProbeResult` / `DownloadResult` models + **contract test suite**; add the `udl.downloaders` `PluginKind` to `PLUGIN_KINDS`
  - [x] 2.2 Error taxonomy mapping (table-driven tests per tool: `AuthRequired`, `Unsupported`, `RateLimited`, …)
  - [x] 2.3 `yt-dlp` (in-process API, offline extractor match, `extract_info(download=False)`, progress hooks)
  - [x] 2.4 `gallery-dl` (offline `extractor.find()`, in-process job)
  - [x] 2.5 `http` direct-file downloader: `httpx` by default, `curl` / `wget` backends when installed, resume support
  - [x] 2.6 `scraper` (static HTML, `selectolax`): heuristic media discovery, then hand off to `http` / `yt-dlp`
  - [x] 2.7 `browser` (Playwright): network sniffing for media/manifest URLs + cookie capture, then hand off
  - [x] 2.8 Auth (401/403): `cookies_from_browser`, persistent browser profile, `udl browser login`

- [x] **Phase 3: Resolution engine & service layer**
  - [x] 3.1 URL normalisation + site key (registrable domain via `tldextract`), with property-based tests
  - [x] 3.2 Resolver precedence: request override, then site plugin, then stored strategy (all DBs), then probe
  - [x] 3.3 Probe chain: offline matches first, then network checks, then scraper, then browser
  - [x] 3.4 Execute with fallback: try candidates until a download **actually succeeds** (`AuthRequired`: retry with `AuthCookies`, then the `browser` downloader)
  - [x] 3.5 Record winning strategy + download history through `DBManager`; update success/failure counters; `requires_auth` when it needed cookies
  - [x] 3.6 Self-healing: a stored strategy that fails N times in a row gets re-probed and replaced
  - [x] 3.7 Output dir resolution (request, then site, then config) + filename templates
  - [x] 3.8 Multi-URL batches: async concurrency with global + per-site limits
  - [x] 3.9 Job manager: progress events, cancellation, persisted as `DownloadRecord`
  - [x] 3.10 `UDLService` façade + operations registry (the only thing interfaces call)

- [x] **Phase 4: Interfaces (CLI, API, MCP)**
  - [x] 4.1 🔴 **Parity test first**: every registered operation must have a CLI command, an API route and an MCP tool
  - [x] 4.2 CLI: Typer app + default-command shim so `udl <url>...` == `udl get <url>...`; add `[project.scripts]` and install system-wide with `uv tool install --editable .`
  - [x] 4.3 CLI: all [Feature Matrix](#feature-matrix-source-of-truth-for-parity) commands + rich progress + `--json` output; `udl browser login` (`auth.browser_login()`, ending on Enter)
  - [x] 4.4 API: FastAPI routes from the Feature Matrix, job endpoints, SSE progress
  - [x] 4.5 API: `udl serve` runs Granian; localhost bind by default, optional API key, allowed output roots
  - [x] 4.6 MCP: tools from the Feature Matrix (official `mcp` 2.x SDK, `MCPServer`)
  - [x] 4.7 MCP: `udl mcp` (stdio) + streamable HTTP mounted at `/mcp` in the API

- [ ] **Phase 5: Tag chains (user-defined extraction)**
  - [ ] 5.1 `TagChain` model with discriminated-union step types; add `Strategy.chain` (a schema migration, see [Phase 1 decisions](#decisions-made-while-building-phase-1))
  - [ ] 5.2 One-line shorthand parser (`iframe#player@src >> video source@src`) with round-trip property tests
  - [ ] 5.3 Static chain executor (`selectolax`)
  - [ ] 5.4 Browser chain executor (Playwright: `wait_for`, `click`, `network` steps)
  - [ ] 5.5 `suggest_chain`: scan a page, rank candidate media elements, generate stable selectors
  - [ ] 5.6 `udl chain wizard <url>`: suggest, pick, test, save
  - [ ] 5.7 Chain import/export (YAML files ↔ DB)
  - [ ] 5.8 Expose suggest / test / set / import / export via CLI, API and MCP
  - [ ] 5.9 "All tools failed" returns an actionable hint pointing at chain definition

- [ ] **Phase 6: Site behaviour plugins**
  - [ ] 6.1 `SitePlugin` ABC (`matches`, `transform_urls`, `strategy`, `prepare`, `output_dir`, `post_process`) + contract tests; add the `udl.sites` `PluginKind`
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
  log.py                    # configure_logging() (stderr; Rich on a terminal)
  core/
    service.py              # UDLService + @operation registry
    resolver.py             # candidates: request, site plugin, stored strategies, probe chain
    executor.py             # try candidates with fallback, sign in, retry, record and heal
    jobs.py                 # JobManager, job events, concurrency limits, crash recovery
    tools.py                # Toolbox: configured downloaders
    output.py               # output dir precedence, {site} in filename templates
    urls.py                 # normalisation, site keys
    models.py               # jobs, request/response DTOs
  db/
    models.py               # Site / Strategy / DownloadRecord, Outcome, SiteQuery / DownloadQuery
    manager.py              # DBManager
  plugins/
    registry.py             # PluginRegistry, PluginKind (DATABASES, …)
    databases/   base.py  memory.py  sqlalchemy.py (+ table variants)  sqlite.py
    downloaders/ base.py  errors.py  process.py  media.py  ytdlp.py  gallerydl.py  http.py  scraper.py  browser.py  auth.py
    sites/       base.py  <examples>.py
  chains/        models.py  shorthand.py  executor.py  suggest.py
  interfaces/
    security.py             # allowed output roots, the API key (the API and MCP)
    cli/         app.py  common.py  render.py  commands/  (downloads probe sites system servers browser)
    api/         app.py  deps.py  serve.py  routes/  (downloads sites system)
    mcp/         server.py
tests/
  conftest.py
  fixtures/site/            # HTML pages + small media files served by the fixture server
  support/                  # shared helpers imported as `support.*` (fixture_site.py: FixtureSite, Page;
                            #   interfaces.py: Harness; cli.py, api.py, mcp.py: run each interface on it)
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
  "udl[browser]",                      # so pyright and the browser tests always have playwright
]

[build-system]
requires = ["uv_build>=0.13.5,<0.14.0"]
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
- **Strategy** (many per site): `id`, `site_key`, `url_pattern: str | None` (regex on path; `None` = whole site),
  `tool: str` (downloader plugin name), `options: JsonObject` (validated by that downloader's `Options` model),
  `requires_auth: bool`, `source: StrategySource` (`probe | manual | plugin | import`), `priority` (lower first),
  `success_count`, `failure_count`, `consecutive_failures`, `last_success_at`, `last_error: ErrorInfo | None`,
  `enabled`. `chain: TagChain | None` arrives in 5.1.
- **DownloadRecord**: `id`, `url`, `site_key`, `strategy_id`, `tool`, `status: JobStatus`, `output_dir`,
  `files: list[Path]`, `size_bytes`, `started_at`, `finished_at`, `error: ErrorInfo | None`

Pattern: `class Strategy(SQLModel)` is plain Pydantic, so non-SQL plugins reuse it.
`class StrategyTable(Strategy, table=True)` lives with the SQLAlchemy plugin.

### `DatabasePlugin` (repository-level, not SQL-specific, so MongoDB or similar can be a plugin)

```python
class DatabasePlugin(ABC):
    name: ClassVar[str]                                   # "sqlite", "postgres", "memory", …
    def __init__(self, connection: DBConnectionConfig) -> None: ...
    async def connect(self) -> None: ...                  # runs migrate()
    async def close(self) -> None: ...
    async def migrate(self) -> None: ...                  # idempotent
    async def health(self) -> bool: ...
    async def get_site(self, key: str) -> Site | None: ...
    async def list_sites(self, query: SiteQuery) -> list[Site]: ...
    async def upsert_site(self, site: Site) -> Site: ...
    async def delete_site(self, key: str) -> bool: ...
    async def strategies_for(self, site_key: str) -> list[Strategy]: ...
    async def upsert_strategy(self, strategy: Strategy) -> Strategy: ...
    async def record_outcome(self, strategy_id: UUID, outcome: Outcome) -> Strategy | None: ...
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

The registry is generic: each plugin type is a `PluginKind` (entry point group + base class), and
`PLUGIN_KINDS` lists the ones udl uses. Load order is udl's own entry points, then other installed
packages', then each plugin dir; a later plugin with the same name overrides an earlier one (with a
warning), and a plugin that fails to load lands in `registry.problems` (for `plugins list`) instead of
stopping udl.

### Decisions made while building Phase 1

- **Strategies and downloads refer to their site by `site_key`, not `site_id`.** The key is the same in
  every connected DB, while two DBs that each learned a site on their own give it different ids. With
  `site_key`, `DBManager` can write a strategy to several DBs and `merge` reads need no id mapping. A
  download also knows its site key before the site row exists (first download from a new site).
- **`upsert_site` matches on `key`** and keeps the stored `id` / `created_at` (it sets `updated_at`).
  `upsert_strategy` matches on `id` and needs the site stored first. `record_outcome` is one atomic update
  (concurrent outcomes are all counted) and returns the updated strategy for self-healing (3.6), or `None`.
- **`Strategy.chain` is left to 5.1.** Nothing in Phases 1–4 reads it, and adding it then (a JSON column)
  is the first real schema migration.
- **Errors are typed JSON** (`ErrorInfo`), as are `options` (`JsonObject`) and `files` (`list[Path]`).
  The SQL plugin stores them through Pydantic-backed column types, so they load as the same types.
- **`DBManager` reads:** `get_site` takes the highest-priority hit; `strategies_for` follows the read policy;
  `list_sites` / `list_downloads` always combine every connection (highest-priority copy wins). A failing
  connection is skipped with a warning, except the primary (the `primary = true` one, else the
  highest-priority writable one) or a write's leading connection, whose failure raises `DatabaseError`.
- **Migrations:** a new DB is created at the current version; an older one runs the steps it lacks
  (`SQLAlchemyDatabase.migrations`) in one transaction; a newer one is refused. The `sqlite` plugin lets
  SQLAlchemy begin transactions (the sqlite3 module would commit DDL as it runs) and uses one pooled
  connection, so concurrent writers never hit "database is locked".

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
class Downloader[O: DownloaderOptions](ABC):
    name: ClassVar[str]
    options_model: ClassVar[type[DownloaderOptions]]      # e.g. YtDlpOptions(format="bv*+ba/b", …)
    requires: ClassVar[tuple[Requirement, ...]] = ()      # Requirement.binary("curl"), .module("yt_dlp")
    def __init__(self, options: O | None = None) -> None: ...
    @classmethod
    def configure(cls, *layers: Mapping[str, JsonValue]) -> Self: ...  # [tools.<name>], then a strategy's
    def requirements(self) -> tuple[Requirement, ...]: ...  # may depend on the options (http's backend)
    def available(self) -> bool: ...
    def match(self, url: str) -> MatchResult | None: ...  # offline, no network (optional)
    async def probe(self, ctx: ProbeContext) -> ProbeResult: ...  # raises only Cancelled
    async def download(self, ctx: DownloadContext) -> DownloadResult: ...  # a DownloadError, or Cancelled
```

Plugins implement `_probe` / `_download`; the public methods add the checks every downloader must pass.
`hand_off()` / `hand_off_best()` download media a plugin found with another downloader.

- `ProbeResult`: `tool`, `ok`, `confidence`, `title`, `media: list[MediaCandidate]`, `handoff: Handoff | None`
  (scraper/browser give a concrete media URL + headers/cookies for another downloader), `error: ErrorInfo | None`
- `ProbeContext`: `url`, `headers`, `cookies: list[Cookie]`, `cancel: CancelToken`. `DownloadContext` adds
  `output_dir`, `filename_template`, `progress: ProgressSink` and `downloader_for` (finds the handoff's tool)
- `DownloadResult`: `tool`, `url`, `files` (absolute, real, not empty and inside `output_dir`: checked),
  `size_bytes`, `title`, `handed_off_to`

### Per-tool notes

| Plugin | Offline match | Probe | Download | Notes |
|---|---|---|---|---|
| `yt-dlp` | Any extractor except `Generic` whose `suitable(url)` is true | `extract_info(download=False)` | `YoutubeDL` + progress hooks | A Generic-extractor hit gets low confidence. Use a new `YoutubeDL` per job. Cancel by raising from the progress hook |
| `gallery-dl` | `gallery_dl.extractor.find(url)` | First item's metadata | In-process `DownloadJob` (subprocess fallback) | Untyped and less stable as a library, so keep it behind the plugin's typed façade |
| `http` | URL path has a video/audio/image extension | `HEAD` (on 403/405/501: a `GET` of the first byte); anything but a web page or a stream playlist is a file | Into `<name>.part`, renamed once complete; a part left by an earlier try is resumed with `Range` | `backend: httpx \| curl \| wget` makes every request (curl/wget optional). Named from `Content-Disposition`, else the URL |
| `scraper` | n/a | Fetch the HTML: `<video>/<audio>/<source>`, `og:video`/`og:audio`, JSON-LD `contentUrl`, media `<a href>`, media and playlist URLs in scripts; its iframes' pages when the page has none | Hand off the best find to `http` (files) or `yt-dlp` (HLS/DASH), the next one on a failure of its own | `selectolax` (Lexbor) is typically an order of magnitude faster than BeautifulSoup. Add `lxml` only if XPath steps are needed |
| `browser` | n/a | Chromium: the media and playlists the page requests (any frame, not stream segments), then its media elements' sources | Hand off like the scraper, with the `Referer` the browser sent, its UA and its cookies | `udl[browser]` extra + `uv run playwright install chromium`; udl's persistent profile by default |

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
1. Retry the same tool with the site's cookies from `AuthCookies.for_url()`: the user's own browser
   (`cookies_from_browser`, read by udl with yt-dlp's cookie readers) and udl's persistent browser profile
   (whose cookies win). Every downloader takes them as `ctx.cookies`.
2. Fall back to the `browser` plugin, which loads the page on that profile, captures media URLs and cookies,
   and hands off.
3. `udl browser login <url>` (CLI in 4.3) calls `browser_login()`: a **headed** Chromium on the persistent
   profile, until the user closes it (or presses Enter), so they log in once.
4. The winning strategy is saved with `requires_auth=True`, so cookies are attached upfront next time (3.5).

### Decisions made while building Phase 2

- **Options live on the downloader**, not the context: `Downloader[O]` holds a validated `O`, and
  `configure(*layers)` merges `[tools.<name>]` and then a strategy's `options` (a ConfigError names the tool).
  One instance is one configured tool, so `requirements()` can depend on its options (http's backend).
- **Probe results carry `ErrorInfo`**, not a `UDLError`, so they serialise for the API, MCP and `--json`;
  `error_from_info()` turns one back into its exception. `probe()` reports failures and raises only
  `Cancelled`; `download()` raises a `DownloadError` or `Cancelled`, and succeeds only with real, non-empty
  files inside `output_dir`.
- **`Cancelled` is not a `DownloadError`**, so code that tries the next tool after a failure stops instead.
  Threaded tools run through `run_blocking()`, async work through `cancellable()` and programs through
  `run_process()`; each stops soon after its `CancelToken` is cancelled and never outlives its task.
- **The resolver fills in `{site}`** before a downloader sees the filename template (3.7). Each downloader
  translates the rest: yt-dlp's `outtmpl`, gallery-dl's `directory`/`filename`, http's `{title}`/`{ext}` from
  the file's name. `DownloadContext` checks templates (relative, plain `{field}`s), and a field's value can
  never add a folder or leave one.
- **Cookies are udl's own `Cookie` model** (the value a `SecretStr`), translated for each tool: standard
  library cookie jars (yt-dlp, gallery-dl, httpx), Netscape cookie files in a private temp dir (curl, wget,
  gallery-dl's CLI) and Playwright's. udl reads `cookies_from_browser` itself (`AuthCookies`) instead of
  passing yt-dlp's `cookiesfrombrowser` / gallery-dl's `cookies-from-browser`, so every downloader gets the
  same cookies.
- **`http`'s backend makes every request**, HEAD too, so a site that blocks one client is not probed with
  another. It asks for files uncompressed (and decodes them if a server compresses anyway), so sizes compare;
  a size that disagrees with the server's is a `NetworkError` that keeps the `.part`. A file downloaded
  before (same size) is kept. curl and wget ignore `~/.curlrc` / `~/.wgetrc` and write no `~/.wget-hsts`.
- **Finds are handed off best first** (`hand_off_best()`): a failure of the find's own (unsupported, not
  found, not media, its tool missing) tries the next, five at most; any other (auth, rate limit, network) is
  raised at once, so a trailer never stands in for a film that needs a login. The scraper searches iframes'
  pages only when the page itself has no media.
- **The browser runs on udl's persistent profile by default** (`BrowserOptions.profile_dir` defaults to
  `[browser] profile_dir`'s default); the resolver should pass `[browser]`'s `headless` and `profile_dir` as
  the first layer of its options. One Chromium at a time uses a profile (others wait). Headless, it runs
  without the GPU, whose drivers would write shader caches to the user's cache dir. Playwright is imported
  only when the browser or the profile is used, so udl works without the `browser` extra.
- **gallery-dl's in-process jobs take turns** (its config is global), and its command line takes over if its
  Python API breaks (e.g. after an upgrade).

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
 └─ for each URL (a job; ≤ max_concurrent at once, ≤ max_per_site per site, across batches):
     1. normalise the URL; site_key = registrable domain (tldextract's bundled suffix list)
     2. output dir: request → site plugin → Site.default_output_dir → download_dir; {site} filled in
     3. candidates, best first (Resolver.candidates):
          request.tool        → the only candidate
          site plugin         → its strategy
          stored strategies   → every DB (read policy); enabled, url_pattern found in the path; ranked
          probe chain         → probe_order: offline matches first, then the rest
     4. try each until a download actually succeeds (real, non-empty files in the output dir):
          unsupported / extraction failed → next · AuthRequired → same tool with cookies, then browser
          RateLimited / NetworkError → wait and retry the same tool · NotFound / Cancelled → stop
     5. remember (unless --no-record): the winner, the stored strategies' successes and failures,
        requires_auth; heal_threshold failures in a row disable a learned strategy
     6. all failed → the most useful error (login, rate limit, network), else NoStrategyFound
```

### Probe chain (order configurable via `probe_order`)
1. **Offline** (no network): the tools whose `match()` recognises the URL (a site's yt-dlp or gallery-dl
   extractor, a media file's extension), in `probe_order`.
2. The other tools, in `probe_order`. By default: **`http`** (is the URL itself a file?), **yt-dlp**'s generic
   extractor, **gallery-dl**, the **`scraper`**, and the **`browser`**, the most expensive, last.

A candidate is tried by downloading with it: each downloader's `download()` makes the checks its `probe()`
would and fails as fast, so the chain stops at the first **successful download**. `udl probe <url> --all`
(`probe_url`) runs `probe()` on every tool for debugging.

### Recording rules
- Strategies are recorded **site-wide** by default. Use `sites set --pattern '^/shorts/'` when one site needs
  different tools for different sections.
- Only failures that are the strategy's fault (`counts_against_strategy`) are counted against it.
- Self-healing: when `consecutive_failures >= heal_threshold` (default 3), the strategy is disabled, the rest
  of the chain re-probes the URL and the new winner is recorded. `manual` strategies only warn.

### Output paths
Precedence: request `output_dir` (`-o/--output`) → site plugin → `Site.default_output_dir` → config
`download_dir` (default: `platformdirs.user_downloads_dir()/udl`). `filename_template` (e.g.
`{site}/{title}.{ext}`, the request's or the config's) gets `{site}` filled in, then is translated per tool:
yt-dlp `outtmpl`, gallery-dl `directory`/`filename`, or derived for `http`.

### Jobs & progress
- `JobManager` runs one asyncio task per URL; a batch (one request) shares a batch id.
- `JobEvent`s (`queued → started → progress* → finished|failed|cancelled`, each carrying the job's `JobInfo`)
  go to every subscriber of the job or batch (`UDLService.events()`): the CLI renders progress bars, the API
  streams SSE and MCP sends progress notifications. Progress is sent at most every 0.25 s per job.
- Threaded tools report via `loop.call_soon_threadsafe`; cancellation uses a `CancelToken` checked in
  yt-dlp/gallery-dl hooks.

### Service & operations registry

```python
class UDLService:
    @operation("download")
    async def download(self, request: DownloadRequest) -> DownloadBatch: ...
    @operation("probe_url")
    async def probe(self, request: ProbeRequest) -> ProbeReport: ...
    # get_download, list_downloads, cancel_download, list_sites, get_site, set_site_strategy,
    # delete_site, list_databases, list_plugins, get_config

operations()                  # name -> Operation(name, request, response, summary, description, method)
await service.call(name, request_model_or_plain_data)
service.events(job_or_batch_id)   # a stream, not an operation
```

Every operation takes and returns Pydantic models, so the CLI, API and MCP share validation and serialisation.
Each adapter registers which operation it implements, and the parity test compares those sets against the
registry. Operation names are the Feature Matrix's MCP tool names.

### Decisions made while building Phase 3

- **Candidates are tried by downloading, not probed first.** A downloader's `download()` makes the same
  checks its `probe()` would (a HEAD request, fetching the page, extraction) and fails as fast with
  `UnsupportedURL`, so probing first would only double the work for the tool that wins.
- **`probe_order` now defaults to `http, yt-dlp, gallery-dl, scraper, browser`**, as the chain above needs:
  http's HEAD request is cheap and certain, so it goes before yt-dlp's generic extractor, and a direct media
  link goes to http before gallery-dl's direct-link extractor. Offline matches go first in `probe_order` (not
  by confidence), so `probe_order` is the one setting that orders the chain.
- **The probe chain also follows failed stored strategies**, not only a site with none: a download works on
  the first run even when the stored strategy has just broken. A tool is never offered twice with the same
  options.
- **Ranking stored strategies:** a matching `url_pattern` before a site-wide strategy, then `priority`, a
  manual strategy before a learned one, fewer consecutive failures, the better success rate (smoothed: an
  untried strategy counts as half), then the id. Among patterns, `priority` decides.
- **URL normalisation** lowercases the scheme and host, drops default ports, tracking parameters (`utm_*`,
  `fbclid`, …) and text fragments, and adds `https://` to a URL written without a scheme. Everything else stays
  as written: `www.`/`m.` (a site may not answer without them), the query's encoding and order (a signed URL's
  signature covers it) and fragments (pages that route in JavaScript). **Site keys** include the suffix
  list's private domains (`someone.github.io` is its own site); a host with no registrable domain (localhost,
  an IP address, an intranet name) is its own key, without its port.
- **Signing in:** a URL's cookies are read once, at its first `AuthRequired` (or up front for a
  `requires_auth` strategy); after a login failure every later candidate is signed in. The `browser` comes in
  straight after the failure, once, but never for a tool the request named, whose own failure is the error.
- **Retries** (`RetryPolicy`, not in the config): 2 more tries after a network error or rate limit, waiting
  the site's `Retry-After` or a backoff from 1 s that doubles, at most 60 s; a longer `Retry-After` moves on.
- **What is remembered:** the probe's winner (site-wide, `source=probe`), or its success on the site's
  strategy for that tool and options (enabling it again). A requested tool or a site plugin's strategy is
  chosen, not learned, so neither is stored. A strategy stored only in a database udl does not write to (a
  read-only shared one) is copied into one it does when counted. A database that cannot be written is
  logged and never fails a download.
- **The executor learns strategies; the job manager writes the history.** A `DownloadRecord` is saved when
  its job is queued, starts and ends (`started_at` is the queue time until it starts); `--no-record` saves
  neither strategies nor history.
- **Concurrency:** a job waits for its site's turn before a global one, so jobs queued behind a busy site
  leave room for other sites. The limits hold across batches. `-j/--jobs` (4.2) sets the CLI's
  `max_concurrent` (each CLI run makes its own service) rather than being a request field; `--db` is
  `DownloadRequest.databases`, a `DBManager.only()` view that reads from and writes to the named connections.
- **Cancelling** a running job cancels its `CancelToken`, so the downloader stops as designed and the try ends
  as cancelled; its task is cancelled only if it has not stopped 5 s later. A job waiting for its turn has its
  task cancelled at once.
- **Recovering after a crash:** a starting udl marks records left `queued`/`running` as `failed:
  interrupted` only when no other udl is running: every udl holds a shared `flock` on `udl.lock` in the data
  dir, and one that can take it exclusively is alone (it keeps it exclusively while it cleans up). This is
  POSIX-only. A udl on another machine that shares a database can still have a record marked interrupted;
  its final update puts it right.
- **Site plugins (Phase 6)** are asked through the `SiteBehaviour` protocol (`matches`, `strategy`,
  `output_dir`); `transform_urls`, `prepare` and `post_process` arrive with 6.2.
- **Smaller pieces:** `Toolbox` gives configured downloaders (the `[browser]` table first for the browser);
  a downloader's offline `match()` that raises counts as no match. `DatabasePlugin.get_download(id)` joined
  the contract, and errors gained `Interrupted` and `DoesNotExist`. The config's `filename_template` is
  validated when the config loads. A request's URLs are deduplicated after normalising.

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
- **Default-command shim:** when the first argument after udl's own options (`-v`, `--config FILE`…)
  is not a command's name, `get` goes in before it (`DefaultGetGroup.parse_args`), so
  `udl --config x.toml <url>` and `udl -o history <url>` both work.

  ```python
  class DefaultGetGroup(TyperGroup):
      def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
          at = self._own_options_end(ctx, args)
          if at < len(args) and args[at] not in self.commands:
              args = [*args[:at], "get", *args[at:]]
          return super().parse_args(ctx, args)
  ```
- `get` options: `-o/--output DIR`, `--tool NAME`, `--db NAME` (restrict lookup/writes:
  `DownloadRequest.databases`), `--reprobe`, `--no-record`, `-i/--input FILE` (`-` = stdin), `-j/--jobs N`
  (the CLI's `max_concurrent`, see [Phase 3 decisions](#decisions-made-while-building-phase-3)), `--json`.
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

### Decisions made while building Phase 4

- **The parity test holds the Feature Matrix** (operation → CLI command → API route; the MCP tool is
  the operation's name). CLI commands are marked with `@runs("<operation>")`, API routes are read from
  the OpenAPI schema's operationIds (FastAPI 0.143 includes routers lazily, so `app.routes` no longer
  lists them) and MCP tools from `list_tools()`. Interface-only: `serve`, `mcp`, `browser login` and
  `GET /downloads/{id}/events`. A new operation fails it until each interface has it, and the MCP
  server refuses to start without the operation's tool annotations (`_HINTS`).
- **The service grew what the interfaces needed, so they hold no logic:** `download(on_event=…)`
  hears the jobs' events while it waits (the CLI's progress bars, MCP progress), `DownloadSpec` /
  `StrategySpec` are the requests without `wait`/`timeout` and `site` (the API takes those from the
  query and the path), and every request field is described with an attribute docstring
  (`use_attribute_docstrings`), which becomes the API's docs and the MCP tools' argument descriptions.
- **A udl cancels only the downloads it runs**, and each CLI run makes its own service, so
  `udl jobs cancel` of a download in the history leaves one that has ended as it is, and says when
  another udl runs it (Ctrl-C cancels `udl get`'s). `DBManager.only()` raises `DoesNotExist` for an
  unknown connection: the request's fault, not a database's.
- **CLI:** Typer 0.26+ vendors Click, so the CLI uses only Typer's API (`typer.Context`,
  `typer.core.TyperGroup`). Exit codes `0` / `1` / `2` (a usage error, an invalid config or invalid
  tool options) and `130` for Ctrl-C, which closes the service (cancelling and saving its downloads).
  `get` also takes `-t/--template`, `sites set` takes `-O KEY=VALUE` (JSON values), and `history`
  shows 20 by default. Progress bars go to stderr, only on a terminal; results to stdout. `udl browser
  login` waits for Enter only on a terminal, through the event loop (a thread blocked on `input()`
  would keep udl from exiting). `serve` and `mcp` import their servers only when run.
- **API:** errors are `ErrorInfo` with a status by code: 404 does not exist, 400 a plugin or config
  error (a tool or options that cannot work), 403 not allowed, 401 unauthorized, 503 a database,
  500 a bug (whose details are only logged). `POST /downloads` answers 202, or 200 once every job has
  ended. Events use FastAPI's own SSE (`EventSourceResponse`, with keep-alives); an unknown id is a
  404 before the stream starts. The API key covers every route but the docs, and `/mcp`.
- **Served on localhost, udl answers only requests for localhost** (`LocalHostsOnly`, 421), so a web
  page that points its name at 127.0.0.1 (DNS rebinding) cannot drive it; the MCP SDK checks `/mcp`'s
  Host and Origin too. Serving other machines without an `api_key` logs a warning.
- **`udl serve`** gives Granian a pickled loader, `worker_app(settings, verbosity)`, so its worker
  process makes the app from exactly the settings `udl serve` had (`--config`, `--host`, `--port`, `-v`),
  with no environment variables to pass on. `-v` adds Granian's access log.
- **MCP:** each tool's signature is built from its request model, so the arguments are the request's
  fields, flat. `download` waits at most `DEFAULT_WAIT` (120 s) by default, its progress counting one
  unit per URL; an output dir it is given must be in `api.allowed_output_roots` on both transports (an
  agent picks paths, whoever runs it). Errors are tool errors reading `<code>: <message>` and the hint.
  `/mcp` is a route (a mount would answer only `/mcp/…`), and the SDK's session manager runs in the
  API's lifespan.
- **Fixed on the way:** `JobManager.shutdown()` now waits for jobs that have just ended to be saved
  (their final save could run after the databases closed).
- **Tests:** a `Harness` (tests/support/interfaces.py) makes services on fakes whose in-memory
  databases outlive each one (each CLI run makes its own). CLI tests are not async (the CLI runs
  `asyncio.run`); API tests run the app's lifespan around httpx's `ASGITransport`; MCP tests use the
  SDK's in-process `Client`. anyio task groups must be left in the task they were entered in, which
  async fixtures do not do, so `support.tasks.held()` holds them in a task of their own. The MCP
  client uses httpx2, so `/mcp` is tested against a real `udl serve`; a stdio client gets only a
  default environment, so the tests pass theirs. Coverage follows the processes tests start
  (`patch = ["subprocess"]`).

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
probe_order = ["http", "yt-dlp", "gallery-dl", "scraper", "browser"]
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
