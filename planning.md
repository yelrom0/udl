# UDL: Automations, AI & Web UI Plan

> udl grows from a downloader into a local automation hub. It connects **several Google and Slack
> accounts**, turns what happens in them (and on a **schedule**) into **events**, and runs the **actions**
> that **subscribe** to those events: scripts, udl operations, webhooks, or a **local AI** with its own prompts
> and guidance. Everything stays available through the **CLI**, the **HTTP API** and the **MCP server**, and a
> **web UI** (React, served by FastAPI) joins them. The UI is built from **UI plugins**, so any plugin can
> bring its own screens, built with whatever component library it likes.
>
> Phases 0–7 (the downloader) are done: their plan and the decisions made along the way are in
> [planning/01-core.md](planning/01-core.md). Numbering continues from there. The work is still **TDD-style**
> and fully **typed**, now in TypeScript too. See [Working agreement](#working-agreement-tdd--typing).

---

## Progress tracker

Task IDs (e.g. `9.4`) are referenced in the detailed sections below. Tick a task only when it meets the
[Definition of Done](#definition-of-done-applies-to-every-task).

- [ ] **Phase 8: Groundwork (plugin operations, records, secrets)**
  - [ ] 8.1 `Operation` carries its tool hints (read-only, destructive, idempotent, open-world, moving `_HINTS` out of the MCP server) and whether it is `admin` (changes automations, accounts, AI setup or settings); an `OperationRegistry` per service holds udl's operations and its plugins' (`operations()` stays the core set)
  - [ ] 8.2 🔴 **Parity test first** for generated adapters: an operation a plugin adds must get a CLI command, an API route and an MCP tool, made from its request model
  - [ ] 8.3 Generated adapters: Typer commands (an option per field, `--input FILE` for the whole request), FastAPI routes (typed in the OpenAPI schema), MCP tools (as today)
  - [ ] 8.4 `Record` base model + `RecordQuery`; `DatabasePlugin` gains the record methods and `claim_lease`; their contract tests first
  - [ ] 8.5 Records in every DB plugin: `memory`, the SQLAlchemy base (schema version 3: a table per record kind), `mongodb` (a collection per kind); `DBManager` reads and writes them with its policies; `db sync` copies them
  - [ ] 8.6 `udl.secrets` plugin kind: `SecretStore` ABC + contract suite; `keyring` (Secret Service / KWallet), `file` (`0600`) and `memory` built-ins; `SecretRef`; secrets never in a database, the config or a log
  - [ ] 8.7 `Clock` (`SystemClock`; `FakeClock` in `tests/support`) for everything time-driven
  - [ ] 8.8 Templates: a sandboxed Jinja2 environment for action arguments and prompts (`StrictUndefined`, size limits, a lone `{{ expression }}` keeps its type), with property tests
  - [ ] 8.9 OAuth 2.0 helper on httpx: authorization code + PKCE, a loopback redirect listener, refresh, revoke; tested against a fake authorization server
  - [ ] 8.10 Settings: the `[automations]`, `[secrets]`, `[ai]`, `[integrations.<name>]` and `[web]` tables; `PluginSettings` records layered over the file (`get_plugin_settings` / `set_plugin_settings`); `get_config` hides the new secrets

- [ ] **Phase 9: Events, subscriptions & actions**
  - [ ] 9.1 `Event` / `EventType` models and the event type registry; udl's own types (`udl.download.*`, `udl.action.*`, `udl.account.*`, `custom.*`); `list_event_types` gives each type's data schema
  - [ ] 9.2 `Filter`: conditions on an event's data, checked against its type's schema when saved; a one-line shorthand with round-trip property tests; the evaluator
  - [ ] 9.3 `Subscription`: event types (globs), accounts, filter, source filter (11.6), actions, throttle; matching
  - [ ] 9.4 `udl.actions` plugin kind: `ActionPlugin` base + contract suite + `FakeAction`; the `Action` record
  - [ ] 9.5 Built-in actions: `script`, `operation`, `webhook`, `emit`, `notify`
  - [ ] 9.6 `EventBus`: publish (persist, dedupe, causation depth), match subscriptions, queue runs; events other udl processes publish are picked up
  - [ ] 9.7 `ActionRunner`: persisted runs, concurrency limits, timeouts, retries, cancelling, crash recovery; a run's end is an event
  - [ ] 9.8 Loop protection: `max_event_depth`, per-subscription throttles, udl's own messages marked (used by 12 and 13)
  - [ ] 9.9 `AutomationRuntime` in `udl serve`: dispatcher, runner, retention, a lease so one runtime runs per database; finished downloads publish `udl.download.*`
  - [ ] 9.10 Operations (events, subscriptions, actions, runs, status) via CLI, API and MCP; `GET /events/stream` (SSE)

- [ ] **Phase 10: Scheduler**
  - [ ] 10.1 `ScheduleSpec`: `cron` | `every` | `at`, in a timezone; cron parsing behind `CronExpression`; next times tested across DST changes (hypothesis)
  - [ ] 10.2 `Schedule` record: misfire policy (`skip` | `once`), jitter, `next_run_at`
  - [ ] 10.3 `Scheduler` on the `Clock`: sleeps until the next due schedule, wakes when schedules change, fires `schedule.fired` once per due time
  - [ ] 10.4 A schedule's `actions` keep a linked subscription, so a cron job is one command
  - [ ] 10.5 Operations (`list/get/set/delete/run/preview_schedule`) via CLI, API and MCP
  - [ ] 10.6 End to end: a schedule runs a script through `udl serve`'s runtime (on a `FakeClock`)

- [ ] **Phase 11: Integrations & accounts**
  - [ ] 11.1 `udl.integrations` plugin kind: `Integration` base class + contract suite + `FakeIntegration`
  - [ ] 11.2 `Account` record (any number per integration, each named), `AccountHandle` (credentials from the secret store, refreshed when they expire; `needs_reauth`)
  - [ ] 11.3 Connecting: `oauth` (a loopback listener for the CLI and stdio MCP; the server's own callback for the API and UI), `tokens`, and `none` (a source that needs no login)
  - [ ] 11.4 Integration operations join the registry (8.1) with an `account` argument (optional while one account is connected), through the generated adapters (8.3); read-only ones that list things fan out over several accounts (`account: "*"` or a list), results tagged by account
  - [ ] 11.5 Watchers: one per connected account that a subscription could match, in the runtime, restarted with backoff; cursors in `PluginState`; `udl.account.*` events
  - [ ] 11.6 Source filters: criteria only the integration can check (e.g. a Gmail search), checked after the generic filter
  - [ ] 11.7 Account operations via CLI, API and MCP

- [ ] **Phase 12: Gmail plugin (`gmail`)**
  - [ ] 12.1 Fake Gmail + fake Google OAuth servers in `tests/support/` (the REST calls udl makes)
  - [ ] 12.2 Google OAuth with the user's own Desktop OAuth client; scopes by access (`read`, `send`, `drafts`, `organise`); revoke on disconnect
  - [ ] 12.3 Typed Gmail client (httpx): messages, threads, history, labels, attachments, send; error mapping
  - [ ] 12.4 `gmail.message.received` and its watcher: History API polling, watched labels, recovery from an expired history id
  - [ ] 12.5 Source filter: a Gmail search query (`from:bank.com has:attachment`)
  - [ ] 12.6 Operations: list, get, thread, send, reply, draft, modify (labels, read, archive), labels, save attachments
  - [ ] 12.7 Opt-in smoke tests against a real account

- [ ] **Phase 13: Slack plugin (`slack`)**
  - [ ] 13.1 Fake Slack (Web API + a Socket Mode WebSocket) in `tests/support/`
  - [ ] 13.2 Connect with tokens from the user's own Slack app (`auth.test`); `slack_app_manifest` gives the app's manifest
  - [ ] 13.3 Typed Web API client (httpx): pagination, rate-limit tiers, `Retry-After`
  - [ ] 13.4 Socket Mode client (`websockets`): acknowledge envelopes, reconnect when Slack asks or the connection drops
  - [ ] 13.5 `slack.message.posted` / `slack.reaction.added`; user and channel names; messages udl sent are recognised
  - [ ] 13.6 Operations: channels, read a channel or thread, search, send, react, users, save files, manifest
  - [ ] 13.7 Opt-in smoke tests against a real workspace

- [ ] **Phase 14: Local AI**
  - [ ] 14.1 `udl.ai` plugin kind: `AIProvider` base + contract suite + `FakeAI` (scripted); `AIProviderConnection` records
  - [ ] 14.2 `ollama` provider: chat with tool calls, models, `keep_alive`, pulling a model
  - [ ] 14.3 `openai-compatible` provider (llama.cpp's `llama-server`, LM Studio, vLLM…); any provider's optional `command` is started on demand and stopped when idle
  - [ ] 14.4 Tools from operations: schemas from request models; `ToolPolicy` (allowlist; destructive / open-world tools wait for approval in `ask` mode and run unattended in `auto` mode; `admin` operations never, for now)
  - [ ] 14.5 The agent loop: tool calls, step / time / size limits, the transcript kept on the run
  - [ ] 14.6 Approvals (`ask` mode): a run waits (`awaiting_approval`), `decide_run`, a `udl.action.awaiting_approval` event, denied after a timeout
  - [ ] 14.7 `Prompt` and `Guidance` records; how a prompt is put together (guidance by scope, the prompt, then the event as untrusted data)
  - [ ] 14.8 The `ai` action
  - [ ] 14.9 Operations (providers, models, prompts, guidance, `try_prompt`) via CLI, API and MCP

- [ ] **Phase 15: Web UI foundations & UI plugins**
  - [ ] 15.1 `web/` pnpm workspace and tooling: Vite + SWC, TypeScript strict, Biome, Vitest (+ coverage gate), Playwright, Tailwind CSS + daisyUI; `make web`, `make e2e`, and `make check` runs the web checks
  - [ ] 15.2 Typed API client generated from udl's OpenAPI schema (`openapi-typescript` + `openapi-fetch`); `make check` fails when it is stale
  - [ ] 15.3 FastAPI serves the UI at `/ui` (from the package, SPA fallback, CSP, cache headers), and a page saying how to build it when it isn't built
  - [ ] 15.4 Web sessions: sign in with the API key → a session cookie; a CSRF header and an Origin check on writes
  - [ ] 15.5 The shell: layout, TanStack Router (file routes, auth guard, a splat route for plugin pages), Zustand stores, light / dark / system theme
  - [ ] 15.6 Live data: shared SSE connections feeding Zustand stores, reconnecting with backoff
  - [ ] 15.7 `UIManifest` (Pydantic → JSON Schema → TypeScript), `ui` on every plugin kind, the `udl.ui` kind, local UI plugin dirs, `GET /ui/plugins.json`, UI availability in `list_plugins`
  - [ ] 15.8 `@udl/ui-sdk`: `defineUIPlugin`, the mount contract, a React adapter, the plugin context, SDK version checks
  - [ ] 15.9 The plugin host: `<PluginSlot>` (lazy import, Shadow DOM isolation, error boundary) and the contribution points
  - [ ] 15.10 Proof: an example UI plugin built with shadcn/ui runs isolated inside the daisyUI shell (e2e); a template for new UI plugins

- [ ] **Phase 16: Web UI for the downloader**
  - [ ] 16.1 Dashboard (recent downloads, runs and events, next schedules, accounts needing attention, approvals; a widget slot)
  - [ ] 16.2 Downloads: start downloads, live progress, history, cancel
  - [ ] 16.3 Sites and strategies
  - [ ] 16.4 Tag chains: the wizard on the web (suggest → pick → test → save), import / export
  - [ ] 16.5 Databases (health, sync with a dry run), plugins (status, problems, UIs, settings), config (read-only)
  - [ ] 16.6 End to end against the fixture site

- [ ] **Phase 17: Web UI for automations + the Gmail and Slack UI plugins**
  - [ ] 17.1 Events: live, history, detail (the plugin's event view, else JSON), emit, replay
  - [ ] 17.2 Subscriptions: event type picker, a filter builder from the type's schema (↔ the shorthand), "would have matched" over recent events
  - [ ] 17.3 Actions and runs: editors for the built-in actions (`auto` AI actions clearly marked), run detail (output, AI transcript), the approvals inbox
  - [ ] 17.4 Schedules: a cron editor showing the next times, timezone, run now
  - [ ] 17.5 AI: providers and models, prompts and guidance, a playground that tries a prompt on an event
  - [ ] 17.6 Accounts: connect (through the integration's UI plugin), status, reconnect, disconnect
  - [ ] 17.7 `gmail` UI plugin: OAuth client and account setup, mailbox, compose / reply, event view, Gmail-search filter editor
  - [ ] 17.8 `slack` UI plugin: account setup (manifest + tokens), channels and threads, send, event view
  - [ ] 17.9 End to end: a subscription made in the UI fires on a fake email, and its run shows up live

- [ ] **Phase 18: Hardening & docs**
  - [ ] 18.1 Security review against the [security model](#security-model): secrets at rest, OAuth, untrusted content (templates, scripts, prompt injection), approvals, the UI (CSP, CSRF, sessions, plugin trust), webhooks, logs
  - [ ] 18.2 Resilience: watchers reconnecting, a provider or database going away, a crash mid-run (recovery tests); an accessibility pass (axe in the e2e tests)
  - [ ] 18.3 `udl service install`: a systemd user unit, so the runtime is always on
  - [ ] 18.4 Docs: README, `docs/automations.md`, `docs/plugins.md` (integrations, actions, AI providers, secret stores), `docs/ui-plugins.md`
  - [ ] 18.5 Opt-in smoke tests: Gmail, Slack and Ollama end to end

---

## Working agreement (TDD + typing)

Unchanged from [Phases 0–7](planning/01-core.md#working-agreement-tdd--typing): **red → green → refactor**,
tests describe behaviour through public interfaces, every plugin kind gets a **contract suite first**, bugs
get a failing regression test before the fix, and **fakes over mocks**. What's new:

- **New fakes:** `FakeIntegration`, `FakeAction`, `FakeAI` (scripted replies and tool calls), `FakeClock`,
  the `memory` secret store, and **fake servers** for Google (OAuth + Gmail), Slack (Web API + Socket Mode)
  and an OpenAI-compatible endpoint. The fake servers are ASGI apps: unit tests reach them through httpx's
  `ASGITransport`, integration and e2e tests serve them on a port, so udl's real clients run against them.
  `respx` stays for stubbing httpx at the edge.
- **Time is injected.** Nothing in the scheduler, watchers, leases, throttles or retries calls
  `datetime.now()` or `asyncio.sleep()` directly; it goes through a `Clock`, so tests move time on instead
  of waiting.
- **The frontend is tested first too:** a component, store or SDK function starts as a failing Vitest test;
  a user-visible flow starts as a failing Playwright test.
- When a phase is done, it gets a "Decisions made while building Phase N" section here, as Phases 0–7 did.

### Test layers

| Layer | Scope | Tools | Default run? |
|---|---|---|---|
| Unit | Pure logic, fakes, `tmp_path` only, no network | pytest, hypothesis, respx | ✅ |
| Contract | Every plugin of a kind (databases, downloaders, sites, integrations, actions, AI providers, secret stores) against one shared suite | parametrised pytest | ✅ |
| Integration | Real plugins against the fake servers; the runtime end to end on a `FakeClock`; the fixture site (Phases 0–7) | pytest, `@pytest.mark.integration` | ✅ |
| Interface | CLI, API and MCP, generated adapters included; the parity test | pytest | ✅ |
| Web unit | Components, stores, the SDK, the plugin host, against `FakeUdl` (a typed in-memory client) | Vitest, Testing Library, jsdom | ✅ (`make check`) |
| Web e2e | Playwright against a real `udl serve` with fake integrations, a fake AI and the fixture site, in both themes | `@playwright/test` | ❌ `make e2e`, required for UI tasks |
| Smoke | Real sites, a real Gmail account, a real Slack workspace, Ollama | `@pytest.mark.network` | ❌ opt-in |

Property-based tests (hypothesis): the filter shorthand round-trip (9.2), schedule times across DST
changes (10.1), templates never escaping their sandbox (8.8), and generated adapters round-tripping any
request model (8.3).

### Typing rules

- **Python:** as before (`pyright --strict`, Pydantic at every boundary, SQLModel for anything persisted,
  no `dict[str, Any]` across modules). New JSON blobs (an event's data, an action's or account's options,
  plugin state) are stored as `JsonObject` and only handed out validated by their owning model: the event
  type's, the action plugin's, the integration's.
- **TypeScript:** `strict`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`,
  `verbatimModuleSyntax`; no `any` (Biome); API types come only from the generated client, never by hand;
  the UI manifest's type is generated from its Pydantic model; untrusted JSON (an event's data in a view)
  is `unknown` until narrowed.

### Definition of Done (applies to every task)

- [ ] Tests were written first and observed failing
- [ ] Implementation passes; refactor done with the suite green
- [ ] `make check` passes: as before (ruff, pyright strict, pytest with **≥ 90 % branch coverage**), plus the
  web checks (Biome, `tsc --noEmit`, Vitest with **≥ 90 % coverage**, the generated API client up to date)
- [ ] Boundary data is a Pydantic/SQLModel model (Python) or a generated type (TypeScript)
- [ ] If an operation was added or changed: the CLI, API and MCP adapters and their interface tests are
  updated, the parity test passes, and the TypeScript client is regenerated
- [ ] If new data is persisted: it is a record kind, and the record contract suite covers it in every DB plugin
- [ ] If the UI changed: a Playwright test covers the flow, in light and dark themes, and `make e2e` passes
- [ ] Secrets go only through the secret store; untrusted content never reaches a shell, a template's code
  or a tool call the policy hasn't allowed

---

## Requirements → where they're delivered

| # | Requirement | Tasks |
|---|---|---|
| 1 | Several Google accounts, reading and writing email | 11.2–11.3, 12.2–12.6 |
| 1a | Subscribe to some emails (or all of them); a matching email fires an event that reaches every subscribed action | Phase 9, 11.5–11.6, 12.4–12.5 |
| 1b | Gmail as a plugin | 11.1, Phase 12 |
| 2 | Several Slack accounts, reading and sending messages | 11.2–11.3, 13.2–13.6 |
| 2a | Pub/sub for Slack | Phase 9, 13.4–13.5 |
| 2b | Slack as a plugin | 11.1, Phase 13 |
| 3 | Set up local AI; scripts that run when events fire; prompts and guidance for the AI per event | 9.5 (scripts), Phase 14 |
| 3a | AI actions that can run unattended (`auto` mode); changing automations and accounts is left to a later AI chat | 8.1 (`admin`), 14.4, 14.8 |
| 4 | Scheduled events, like cron | Phase 10 |
| 5 | A web UI for all of it, and for Phases 0–7's features | Phases 15–17 |
| 5a | React, SWC, Vite, TypeScript, pnpm, Vitest, Playwright, daisyUI (light and dark), Tailwind, Zustand, TanStack Router | 15.1, 15.5 |
| 5b | Served by FastAPI | 15.3–15.4 |
| 5c | Each plugin can have a UI plugin for its behaviour and configuration | 8.10, 15.7–15.9, 17.7–17.8 |
| 5d | A plugin without a UI plugin is used through the CLI, API and MCP only | 8.1–8.3 (generated adapters), 15.7 |
| 5e | Other component libraries and themes in UI plugins (e.g. shadcn/ui instead of daisyUI) | 15.9 (isolation, themes), 15.10 |
| + | CLI / API / MCP parity for every new operation | 8.2, each phase's operations task |
| + | TDD, fully typed (Python and TypeScript) | [Working agreement](#working-agreement-tdd--typing) |

---

## Where udl is heading

The longer-term aim, which this plan builds towards rather than finishes:

1. **Pub/sub for apps and programs that don't offer it:** udl notices what changes in them, turns it into
   events anyone can subscribe to, and eventually builds those flows itself.
2. **Multi-account access for AIs that only offer one account**, such as an assistant whose mail connector
   sees a single inbox.
3. **AI for apps that have none (or none that's good)**, through their integrations' operations.

| Aim | What this plan lays down | What comes later |
|---|---|---|
| 1 | Watchers with cursors and dedupe (11.5) that may poll, diff or scrape; sources that need no login (`none`, 11.3); `custom.*` events from scripts and `POST /events`; MCP clients (agents you run) can already create subscriptions, actions and schedules | Generic watchers (a page through tag chains, a command, a file, a feed); an AI chat that builds flows from a description |
| 2 | Any number of accounts per integration (11.2); every integration operation is an MCP tool with an `account` argument; read-only ones fan out over several accounts in one call (11.4) | More integrations |
| 3 | Integration operations as tools for udl's local AI (14.4) and for any MCP client; AI actions on events, unattended in `auto` mode | Browser-driven integrations for apps without an API; the AI chat |

What that means for the design now:

- **An integration is as useful as its operations are to an agent:** every field described, docstrings
  written for agents (as the MCP tools' already are), and fan-out wherever an operation only reads.
- **Nothing assumes an event source has an API or a login:** a watcher may poll a page, run a program or
  diff a file, and its "account" is then just a configured source.
- **`admin` operations are marked from Phase 8,** so unattended AI actions can't reach them, and a later AI
  chat can be given them with the user there to confirm each one.

---

## Concepts

| Term | Meaning |
|---|---|
| **Integration** | A plugin (`udl.integrations`) that connects udl to a service: `gmail`, `slack`. It brings how to connect an account, operations, event types and a watcher |
| **Account** | One connected login of an integration, named by the user: `gmail:work`, `gmail:personal`, `slack:acme`. Any number per integration; credentials live in the secret store |
| **Event** | Something that happened: an email arrived, a Slack message was posted, a schedule came due, a download finished, an action ran. Typed by its **event type** (`gmail.message.received`), stored, deduplicated. (A download's progress events from Phase 3, `JobEvent`, are something else; a job's end is published as a `udl.download.*` event) |
| **Subscription** | "When an event like *this* happens, run *these* actions": event types, accounts, a **filter** on the event's data, and the actions |
| **Action** | A named, reusable thing to run: a script, a udl operation, a webhook, an AI agent… Action kinds are plugins (`udl.actions`) |
| **Run** | One action running for one event: queued, running, maybe awaiting approval, then succeeded, failed or cancelled |
| **Schedule** | A cron-like timer that fires `schedule.fired` events |
| **Runtime** | What runs inside `udl serve`: the watchers, the scheduler, the event bus and the action runner |
| **AI provider** | A plugin (`udl.ai`) that runs a model: `ollama`, `openai-compatible` |
| **Prompt / Guidance** | A prompt is a reusable AI task. Guidance is standing instructions added for everything, an event type, a subscription or an action |
| **UI plugin** | A bundle the web UI loads at runtime that adds pages, panels and editors (**contributions**) for a plugin |

---

## Architecture

```
 Web UI (/ui)               CLI (Typer)        API (FastAPI on Granian)       MCP (MCPServer)
 shell + UI plugins              \                      |                         /
        \________________________ └── UDLService: the operations registry ──────┘
                                      (udl's own + every integration's)
                                                   |
   ┌────────────┬────────────┬─────────────────────┼─────────────────────┬────────────────┐
 Resolver    JobManager   DBManager          AutomationRuntime       PluginRegistry     SecretStore
 (downloads: Phases 0–7)  (+ records)        (in `udl serve`)        databases · downloaders · sites ·
                                                   |                 integrations · actions · ai ·
                                                   |                 secrets · ui
           ┌──────────────────┬────────────────────┼────────────────────┐
       Watchers           Scheduler            EventBus             ActionRunner
   (one per account)   (cron · every · at)  (store, dedupe,      (script · operation · webhook ·
    gmail · slack                            match)                emit · notify · ai ─► AIProvider)
```

How an event travels:

```
 gmail watcher ──┐                        ┌─ "bank mail": gmail.message.received where from.address endswith "@bank.com"
 slack socket ───┤                        │        └─► summarise (ai)    └─► file-it (script)
 scheduler ──────┼─► EventBus.publish ────┼─ "morning": schedule.fired where schedule == "morning"
 downloads ──────┤   store · dedupe       │        └─► digest (ai)
 POST /events ───┘   depth ≤ 8            └─ "done → slack": udl.download.finished
                          ▲                        └─► post-it (operation: slack_send_message)
                          │
                          └──── each run's end: udl.action.succeeded / failed / awaiting_approval
```

Principles:

- **Interfaces still hold no logic.** New operations follow Phases 0–7's pattern. Operations that plugins
  add get **generated** adapters, so a plugin without a UI is still fully usable from the CLI, API and MCP.
- **Everything is an event, every reaction is a run.** Email, Slack, schedules, downloads, the API and
  actions themselves all go through one bus, so limits, retries, history, loop protection and the UI's live
  view live in one place.
- **At least once, never twice.** An event is stored before it is dispatched and a run before it starts;
  dedupe keys stop the same email or Slack message from firing twice, and a crash resumes where it stopped.
- **Untrusted by default.** Email bodies and Slack messages are written by anyone. They are data: they never
  become shell, template code or a tool call unless the action's policy allows it (see
  [Security model](#security-model)).
- **One runtime per database** (a lease), so two `udl serve`s sharing PostgreSQL never run an action twice.
- **Secrets live in the secret store**; records hold only a `SecretRef`.
- **The web UI is a client of the API like any other.** UI plugins are isolated islands that share only the
  SDK's context.

## Where things live: config file vs records

| Kept in | What | Edited with |
|---|---|---|
| Config file (`config.toml`, `UDL_*`) | Machine and security settings: `[automations]` (`scripts_dir`, limits), `[secrets]`, `[web]`, `[ai]` (defaults), `[api]`, `[db]`; the base layer of `[integrations.<name>]` and the fields that widen access (`attachment_roots`) | The file (`get_config` shows it) |
| Records (the databases) | Accounts, subscriptions, actions, runs, events, schedules, prompts, guidance, AI provider connections, plugin settings (over the file's table), plugin state, leases | Operations: CLI, API, MCP, UI |
| Secret store | OAuth tokens, Slack tokens, API keys, secret plugin settings | Only through udl (connecting an account, a secret setting) |

A plugin's settings are its model's defaults, then its `[integrations.<name>]` table, then its stored
`PluginSettings` (what its UI plugin's settings panel writes). Fields marked file-only in the plugin's model
(anything that widens what an API client could reach, such as a path root) are refused by
`set_plugin_settings`.

## Feature Matrix (new operations)

Source of truth for parity, added to `tests/interface/test_parity.py`'s `MATRIX` as each phase lands (the
Phases 0–7 rows are in [planning/01-core.md](planning/01-core.md#feature-matrix-source-of-truth-for-parity)).

| Operation | CLI | API | MCP tool |
|---|---|---|---|
| plugin settings | `udl plugins settings <name>` | `GET /plugins/{name}/settings` | `get_plugin_settings` |
| set plugin settings | `udl plugins configure <name> -O KEY=VALUE` | `PUT /plugins/{name}/settings` | `set_plugin_settings` |
| event types | `udl events types` | `GET /event-types` | `list_event_types` |
| event history | `udl events list [--type T] [--since …]` | `GET /events` | `list_events` |
| show event | `udl events show <id>` | `GET /events/{id}` | `get_event` |
| emit event | `udl events emit <type> [--data JSON]` | `POST /events` | `emit_event` |
| replay event | `udl events replay <id> [--subscription S]` | `POST /events/{id}/replay` | `replay_event` |
| list subscriptions | `udl subs list` | `GET /subscriptions` | `list_subscriptions` |
| show subscription | `udl subs show <name>` | `GET /subscriptions/{name}` | `get_subscription` |
| set subscription | `udl subs set <name> --on TYPE… [--where FILTER] [--account A…] --action A…` | `PUT /subscriptions/{name}` | `set_subscription` |
| test subscription | `udl subs test <name> [--event ID]` | `POST /subscriptions/test` | `test_subscription` |
| delete subscription | `udl subs rm <name>` | `DELETE /subscriptions/{name}` | `delete_subscription` |
| list actions | `udl actions list` | `GET /actions` | `list_actions` |
| show action | `udl actions show <name>` | `GET /actions/{name}` | `get_action` |
| set action | `udl actions set <name> --kind K -O KEY=VALUE` (or `-f action.yaml`) | `PUT /actions/{name}` | `set_action` |
| run action | `udl actions run <name> [--event ID] [--dry-run]` | `POST /actions/{name}/run` | `run_action` |
| delete action | `udl actions rm <name>` | `DELETE /actions/{name}` | `delete_action` |
| list runs | `udl runs list [--status S]` | `GET /runs` | `list_runs` |
| show run | `udl runs show <id>` | `GET /runs/{id}` | `get_run` |
| cancel run | `udl runs cancel <id>` | `DELETE /runs/{id}` | `cancel_run` |
| approve / deny | `udl runs approve <id>` / `udl runs deny <id>` | `POST /runs/{id}/decision` | `decide_run` |
| runtime status | `udl status` | `GET /status` | `get_status` |
| list schedules | `udl schedules list` | `GET /schedules` | `list_schedules` |
| show schedule | `udl schedules show <name>` | `GET /schedules/{name}` | `get_schedule` |
| set schedule | `udl schedules set <name> (--cron C \| --every D \| --at T) [--tz Z] [--action A…]` | `PUT /schedules/{name}` | `set_schedule` |
| run schedule now | `udl schedules run <name>` | `POST /schedules/{name}/run` | `run_schedule` |
| preview schedule | `udl schedules preview '<spec>' [-n 5] [--tz Z]` | `POST /schedules/preview` | `preview_schedule` |
| delete schedule | `udl schedules rm <name>` | `DELETE /schedules/{name}` | `delete_schedule` |
| list accounts | `udl accounts list [--integration I]` | `GET /accounts` | `list_accounts` |
| show account | `udl accounts show <integration:name>` | `GET /accounts/{account}` | `get_account` |
| connect account | `udl accounts add <integration> <name> [--access …]` | `POST /accounts` | `connect_account` |
| set account options | `udl accounts set <account> -O KEY=VALUE` | `PATCH /accounts/{account}` | `set_account` |
| check account | `udl accounts check <account>` | `POST /accounts/{account}/check` | `check_account` |
| disconnect account | `udl accounts rm <account>` | `DELETE /accounts/{account}` | `disconnect_account` |
| AI providers | `udl ai providers` | `GET /ai/providers` | `list_ai_providers` |
| set / remove provider | `udl ai providers set <name> --plugin P …` / `rm` | `PUT` / `DELETE /ai/providers/{name}` | `set_ai_provider`, `delete_ai_provider` |
| models | `udl ai models [--provider P]` | `GET /ai/models` | `list_ai_models` |
| pull a model | `udl ai pull <model> [--provider P]` | `POST /ai/models` | `pull_ai_model` |
| prompts | `udl ai prompts list \| show \| set \| rm` | `/ai/prompts`, `/ai/prompts/{name}` | `list_prompts`, `get_prompt`, `set_prompt`, `delete_prompt` |
| guidance | `udl ai guidance list \| set \| rm` | `/ai/guidance`, `/ai/guidance/{name}` | `list_guidance`, `set_guidance`, `delete_guidance` |
| try a prompt | `udl ai try <prompt> [--event ID] [--model M]` | `POST /ai/try` | `try_prompt` |
| an integration's operation | `udl <integration> <op> …` (generated) | `POST /integrations/{integration}/{op}` (generated) | `<integration>_<op>` |

**Interface-only (exempt in the parity test):** `GET /events/stream` (SSE of events and run changes),
`POST /ai/try/stream` (the playground, streamed), `GET /accounts/callback` (OAuth's redirect),
`POST /session` / `DELETE /session` (web sign-in), everything under `/ui`, and `udl service install`. As in
Phase 5, methods behind `test_*` operations are named `try_*`, and their request models `…TestRequest`.

---

## Project layout (additions)

Phases 0–7's layout is in [planning/01-core.md](planning/01-core.md#project-layout).

```
planning.md                     # this plan; planning/ keeps the finished ones
src/udl/
  clock.py                      # Clock, SystemClock
  automations/
    events.py                   # Event, EventType, the event type registry, udl's own event types
    filters.py                  # Filter, its shorthand, the evaluator
    subscriptions.py            # Subscription, matching, throttles
    bus.py                      # EventBus
    runner.py                   # ActionRunner, ActionRun
    schedules.py                # ScheduleSpec, CronExpression, Scheduler
    runtime.py                  # AutomationRuntime: watchers, scheduler, bus, runner, lease, retention
    templates.py                # the sandboxed template environment
  ai/
    agent.py                    # the agent loop, ToolPolicy, approvals
    prompts.py                  # Prompt, Guidance, putting a prompt together
  db/records.py                 # Record, RecordQuery, Lease, the record kinds
  integrations/
    oauth.py                    # OAuth 2.0: PKCE, the loopback listener, refresh, revoke
    accounts.py                 # Account, AccountHandle, connecting
    watchers.py                 # WatcherSupervisor
  plugins/
    integrations/ base.py  gmail/ (plugin.py client.py models.py events.py ui/)  slack/ (… socket.py ui/)
    actions/      base.py  script.py  operation.py  webhook.py  emit.py  notify.py  ai.py
    ai/           base.py  ollama.py  openai.py
    secrets/      base.py  keyring.py  file.py  memory.py
    ui.py                       # UIManifest, UIBundle, UIPlugin (the udl.ui kind)
  interfaces/
    adapters.py                 # generated CLI commands, API routes and MCP tools for any Operation
    api/openapi.py              # writes (or checks) web/packages/api/openapi.json
    web/                        # /ui: static files, sessions, CSP; dist/ is built, not committed
web/                            # the frontend: a pnpm workspace
  package.json  pnpm-workspace.yaml  pnpm-lock.yaml  .node-version  tsconfig.base.json  biome.json
  packages/
    api/                        # @udl/api: openapi.json → generated types + an openapi-fetch client
    sdk/                        # @udl/ui-sdk: defineUIPlugin, the mount contract, react(), context types
    shell/                      # the app (React, TanStack Router, Zustand, daisyUI) → src/udl/interfaces/web/dist
    template/                   # a starting point for a UI plugin
  plugins/
    gmail/  slack/              # UI plugins → src/udl/plugins/integrations/<name>/ui/
  examples/shadcn/              # 15.10's proof
  e2e/                          # Playwright tests against a real `udl serve`
docs/  automations.md  ui-plugins.md  (+ plugins.md)
tests/support/  clock.py  secrets.py  integrations.py  actions.py  ai.py  google.py  slack.py  e2e.py
```

## Package management (uv + pnpm)

Python stays on uv with the shared reflinked cache
([planning/01-core.md](planning/01-core.md#package-management-uv)). The frontend uses **pnpm** (12.x and
Node 26 are installed here), which keeps one content-addressable store for every project, as uv does.

- **Reflinks, not hardlinks:** `packageImportMethod: clone-or-copy` in `web/pnpm-workspace.yaml`, for the same
  reason as uv's `link-mode = "clone"` (a hardlinked file edited in one project changes the store and every
  other project). Check with `btrfs filesystem du -s "$(pnpm store path)" web/node_modules`.
- `packageManager` in `web/package.json` pins pnpm; `web/.node-version` pins Node's major.
- **One Chromium:** `@playwright/test` is pinned to the Python Playwright's version (1.63 in `uv.lock`), so
  both use the same browser in `~/.cache/ms-playwright` instead of downloading another (~650 MB). Bump them
  together.

| Task | Command |
|---|---|
| Install the frontend's dependencies | `pnpm -C web install` |
| Add one | `pnpm -C web --filter @udl/shell add zustand` (`-D` for dev) |
| Develop the UI | `udl serve` in one terminal, `pnpm -C web dev` in another (Vite on :5173 serves `/ui/` and proxies everything else to udl) |
| Build the UI into the package | `make web` |
| Regenerate the API client | `make openapi` |
| Web checks | `pnpm -C web check` (part of `make check`) |
| End-to-end tests | `make e2e` (starts `udl serve` on fakes via `tests/support/e2e.py`) |

```make
check: check-py check-web

check-web:
	uv run python -m udl.interfaces.api.openapi --check web/packages/api/openapi.json
	pnpm -C web install --frozen-lockfile
	pnpm -C web check                # biome ci, tsc --noEmit, vitest run --coverage

web:
	pnpm -C web build                # the shell and the built-in UI plugins, into src/udl/…

e2e: web
	pnpm -C web e2e
```

---

## Phase 8: Groundwork

Everything later phases share: plugins that add operations, a place to persist the new data, somewhere to
keep secrets, a clock, templates and OAuth.

### Tests first
- Registry: every existing MCP tool keeps exactly its annotations once hints move onto `Operation` (a
  snapshot of today's `_HINTS`); every operation says whether it is `admin` (a test lists them, so a new
  one has to choose); a plugin operation named like an existing one is refused; operations list by
  namespace.
- **Generated adapters (parity, 8.2):** a fake plugin whose operations use every field kind (str, int,
  bool, `StrEnum`, `Path`, lists, a nested model, `SecretStr`, `JsonValue`) gets a working CLI command, an
  API route (in the OpenAPI schema with its models; bad input is a 422) and an MCP tool, and the same
  request (hypothesis) gives the same response through all three.
- **Record contract suite** (every DB plugin): upsert / get / delete per kind; `list_records` by indexed
  fields, newest first, paged, `since`; unique keys enforced (`DuplicateKey`); JSON data comes back as its
  model; `prune_records(kind, before)`; `claim_lease` is exclusive across two connections to one database,
  renewed by its holder and taken over once it expires.
- **Secret stores contract:** set / get / delete; a value never shows in a `repr` or a log line; `file`
  makes its file `0600` in a `0700` folder; `keyring` skips when there is no backend.
- **Templates (hypothesis):** rendering never reads files, imports, or reaches `_` attributes; an undefined
  name fails with the template's line; output is capped; `{{ x }}` alone keeps `x`'s type (a list stays a
  list).
- **OAuth** against a fake authorization server: PKCE challenge, `state` checked, code exchanged, tokens
  refreshed before they expire, a refused refresh becomes `ReauthRequired`; the loopback listener binds to
  127.0.0.1 only and closes after one callback or its timeout.

### Operations from plugins (8.1–8.3)

```python
@dataclass(frozen=True)
class Operation:
    name: str                      # "download", "gmail_send_message"
    request: type[BaseModel]
    response: type[BaseModel]
    summary: str
    description: str               # written for people and AI agents (MCP, and AI tools in 14.4)
    method: Callable[..., Awaitable[BaseModel]]
    hints: OperationHints          # read_only, destructive, idempotent, open_world (were the MCP server's _HINTS),
                                   # and admin: it changes automations, accounts, AI setup or settings
    namespace: str | None = None   # the plugin that adds it ("gmail"); None for udl's own
```

`UDLService.operations` is an `OperationRegistry`: udl's own operations (the module-level `operations()`, as
now) plus those of every loaded plugin, known as soon as the service is made (plugins are discovered then),
so `create_app()` can add their routes before the service starts. `service.call(name, …)` works for both.

Generated adapters (`udl.interfaces.adapters`), used for plugin operations, and for udl's own wherever a
hand-written adapter would add nothing:

| Interface | From the request model | Output |
|---|---|---|
| CLI | An option per field (`--to`, `--flag/--no-flag`, repeatable for lists, choices for enums, JSON for nested models), hidden prompts for `SecretStr`, `--input FILE` (`-` = stdin) for the whole request | `render.py`'s generic renderer, or `--json` |
| API | `app.add_api_route()` with the request as the body, the response model, the operation's name as its operationId | JSON, errors as `ErrorInfo` |
| MCP | As today: the tool's arguments are the request's fields | Structured content |

### Records (8.4–8.5)

```python
class Record(SQLModel):                          # plain Pydantic; table variants live with the SQL plugin
    kind: ClassVar[str]                          # "account", "event", "subscription", …
    indexed: ClassVar[tuple[str, ...]]           # fields RecordQuery can filter on
    unique: ClassVar[tuple[tuple[str, ...], ...]] = ()   # e.g. (("type", "dedupe_key"),)
    id: UUID
    created_at: datetime
    updated_at: datetime

class DatabasePlugin(ABC):
    ...                                          # (Phases 1–7's methods)
    async def get_record[R: Record](self, kind: type[R], id: UUID) -> R | None: ...
    async def list_records[R: Record](self, kind: type[R], query: RecordQuery) -> list[R]: ...
    async def upsert_record[R: Record](self, record: R) -> R: ...          # DuplicateKey on a unique clash
    async def delete_record(self, kind: type[Record], id: UUID) -> bool: ...
    async def prune_records(self, kind: type[Record], before: datetime) -> int: ...
    async def claim_lease(self, name: str, holder: str, ttl: timedelta) -> Lease | None: ...
```

`RecordQuery`: `where` (equality on indexed fields), `since` / `until` (on `created_at`), `limit`,
`offset`, newest first.

| Record | Phase | Indexed (unique) | Notes |
|---|---|---|---|
| `PluginSettings` | 8 | `plugin` (unique) | Secret fields go to the secret store |
| `Lease` | 9 | `name` (unique) | Through `claim_lease` only |
| `EventRecord` | 9 | `type`, `source`, `account_id`, `status`; (`type`, `dedupe_key`) unique | An `Event` and its dispatch status; `data` validated by its type's model; pruned after `event_retention_days` |
| `Subscription` | 9 | `name` (unique), `enabled` | |
| `Action` | 9 | `name` (unique), `kind` | `options` validated by its action plugin |
| `ActionRun` | 9 | `action_id`, `event_id`, `subscription_id`, `status` | Output and AI transcript; pruned with events |
| `Schedule` | 10 | `name` (unique), `enabled`, `next_run_at` | |
| `Account` | 11 | (`integration`, `name`) unique, `status` | Holds a `SecretRef`, never a secret |
| `PluginState` | 11 | (`plugin`, `account_id`) unique | A watcher's cursor, validated by the plugin's `state_model` |
| `AIProviderConnection` | 14 | `name` (unique) | |
| `Prompt`, `Guidance` | 14 | `name` (unique) | |

- SQL (sqlite, postgres): a table per record kind with Pydantic-backed JSON columns, as Phase 1 did; schema
  version 3. MongoDB: a collection per kind with its unique indexes. Memory: dicts.
- `DBManager` reads and writes records with the read and write policies, like sites. `db sync` copies the
  configuration records (subscriptions, actions, schedules, prompts, guidance, AI providers, plugin
  settings, accounts) and the history that has ended (dispatched events, ended runs), never `PluginState`
  or leases, which belong to one runtime. An account synced to another machine has no credentials there, so
  it shows as `needs_reauth` until it is connected again.

### Secrets (8.6)

```python
class SecretStore(ABC):
    name: ClassVar[str]                          # "keyring", "file", "memory"
    requires: ClassVar[tuple[Requirement, ...]] = ()
    async def get(self, ref: SecretRef) -> SecretStr | None: ...
    async def set(self, ref: SecretRef, value: SecretStr) -> None: ...
    async def delete(self, ref: SecretRef) -> bool: ...
```

- `SecretRef` is a path such as `accounts/<account id>/refresh_token`, under the keyring service `udl`.
- `[secrets] store = "auto"`: `keyring` when it has a backend (Secret Service, KWallet), else `file`
  (`<data dir>/secrets.json`, `0600`, plain text protected by its mode, like the browser profile's cookies).
  `keyring` is synchronous, so it runs in `asyncio.to_thread`.

### Clock, templates, OAuth (8.7–8.9)

```python
class Clock(Protocol):
    def now(self) -> datetime: ...                        # aware, UTC
    async def sleep_until(self, when: datetime) -> None: ...
# tests/support/clock.py: FakeClock.advance(delta) wakes sleepers in order
```

- **Templates** are Jinja2's `SandboxedEnvironment` with `StrictUndefined`, no autoescaping (the output is
  not HTML), and a cap on output size. Their context is the event (`event.type`, `event.subject`,
  `event.data…`, `event.account`), `run` and `now`. Filters: `urls` (the URLs in a text), `tojson`,
  `truncate`. A value that is a single `{{ expression }}` keeps its type, so
  `{"urls": "{{ event.data.body_text | urls }}"}` gives a list.
- **OAuth** (`udl.integrations.oauth`): authorization code with PKCE (S256) and `state`;
  `loopback_redirect()` listens on `127.0.0.1` on a free port for one callback; tokens are refreshed a minute
  before they expire; `revoke()` on disconnect. Google and Slack are plain OAuth 2.0 over HTTP, so one
  small typed module on httpx replaces two vendor SDKs.

---

## Phase 9: Events, subscriptions & actions

### Tests first
- Event types: data is validated by the type's model; an unknown type is refused, except `custom.*` (any
  JSON object); `list_event_types` gives each type's JSON Schema and an example.
- Filters (hypothesis): `parse(format(f)) == f`; a filter naming a field its event type doesn't have is
  refused when saved, listing the type's fields; an operator on the wrong kind of value is refused; a
  missing optional field never matches (except `not exists`); `~` (regex) reads at most 64 KiB of a field.
- Subscriptions: type globs (`gmail.*`); account restrictions; a disabled subscription never matches; a
  throttle (`10 per 1h`) skips the rest, recording why.
- Bus: an event whose dedupe key was seen is not dispatched again, even after a restart; events are stored
  before dispatch; each matched action gets its own run; an event deeper than `max_event_depth` is stored
  as `dropped` and logged, not dispatched.
- Runner: never more than `max_concurrent_runs`, or an action's `max_concurrent`; retries only retryable
  failures, with backoff; a timeout fails the run; cancelling stops it; after a crash, queued runs resume
  and running ones become `failed: interrupted`; every run's end publishes `udl.action.succeeded` or
  `udl.action.failed` with its output or error.
- Built-in actions pass the action contract suite. `script` refuses a program outside `scripts_dir` (after
  `..` is resolved), never uses a shell, passes the event on stdin, keeps 64 KiB of output, fails with the
  end of stderr on a non-zero exit. `operation`'s arguments are checked against the operation's request
  model when saved (rendered with the event type's example) and again when run.
- Runtime: a second runtime on the same database waits for the lease, and takes over when the first stops
  renewing it; `GET /events/stream` sends events and run changes in order, filtered by type.

### Events (9.1)

```python
class EventType[D: BaseModel]:
    name: str                          # "gmail.message.received"
    data: type[D]
    summary: str                       # for list_event_types, the UI and AI prompts
    example: D                         # checks filters and templates when they are saved
    subject: Callable[[D], str]        # one line: "Mail from bank@example.com: Your statement"

class Event(BaseModel):
    id: UUID
    type: str
    source: str                        # "gmail", "slack", "scheduler", "udl", "api"
    account_id: UUID | None
    occurred_at: datetime
    subject: str
    data: JsonObject                   # validated by the type's model
    dedupe_key: str | None             # e.g. "<account id>:<gmail message id>"
    caused_by: UUID | None             # the run that led to it
    depth: int                         # 0 for events from outside; a run's events are its event's + 1
```

udl's own event types:

| Event type | Published by | Data |
|---|---|---|
| `udl.download.finished` / `udl.download.failed` | `JobManager`, when a job ends | url, site, tool, files, size, error |
| `udl.action.succeeded` / `failed` / `awaiting_approval` | `ActionRunner` | run, action, subscription, event, output or error, the pending tool call |
| `udl.account.connected` / `needs_reauth` / `error` | accounts, watchers | account, integration, error |
| `schedule.fired` | `Scheduler` (Phase 10) | schedule, scheduled_for, fired_at, late, data |
| `custom.<name>` | `emit_event`, `POST /events`, the `emit` action | any JSON object |

### Filters (9.2)

A filter is a tree of `all` / `any` / `not` over conditions on dotted paths into the event's data, checked
against the type's schema when a subscription is saved:

```python
type Filter = AllOf | AnyOf | Not | Condition
class Condition(BaseModel):
    field: str                        # "from.address", "labels", "channel.name"
    op: Op                            # eq, ne, contains, startswith, endswith, matches, in, exists, lt, le, gt, ge
    value: JsonValue = None
```

The shorthand (accepted wherever a filter is, like tag chains' shorthand in Phase 5):

```
from.address endswith "@bank.com" and (subject ~ "(?i)statement|invoice" or has_attachments)
channel.kind == "dm" and not sent_by_udl
labels contains "IMPORTANT"
```

A bare field means "is truthy"; strings compare case-sensitively unless written `i"…"`; `~` is a regex
search. Regexes are matched against at most 64 KiB of a field (someone else's email is the input).
`google-re2` (linear time) is used if it has wheels for udl's Python, else `re`.

### Subscriptions (9.3)

```python
class Subscription(Record):
    name: str
    description: str = ""
    events: list[str]                  # types or globs: "gmail.message.received", "slack.*"
    accounts: list[str] | None = None  # "gmail:work"…; None = every account
    filter: Filter | None = None       # None = every event of those types ("emails in general")
    source_filter: JsonObject | None = None   # checked by the integration (11.6), e.g. {"query": "has:attachment"}
    actions: list[str]                 # action names; each matched action is a run of its own
    throttle: Throttle | None = None   # e.g. at most 10 runs an hour
    enabled: bool = True
```

Actions are separate records so several subscriptions can share one ("notify me on Slack"), and a
subscription "alerts" every action it lists: each gets its own run, in parallel. Chaining is through events:
a run's end is `udl.action.succeeded`, which other subscriptions can match (`action == "summarise"`).

### Actions (9.4–9.5)

```python
class ActionPlugin[O: ActionOptions](ABC):
    name: ClassVar[str]                                   # "script", "operation", "ai", …
    options_model: ClassVar[type[ActionOptions]]
    requires: ClassVar[tuple[Requirement, ...]] = ()
    ui: ClassVar[UIBundle | None] = None                  # an editor for its options (15.7)
    async def check(self, options: JsonObject, event_type: EventType[Any] | None) -> None: ...  # when saved
    async def run(self, ctx: ActionContext[O]) -> ActionOutput: ...   # raises ActionFailed or Cancelled

class Action(Record):
    name: str
    kind: str                          # the action kind: an action plugin's name
    options: JsonObject                # may hold templates; rendered for each event, then validated
    timeout: timedelta = timedelta(minutes=5)
    retries: int = 0                   # more tries after a retryable failure, with backoff
    max_concurrent: int = 1
    enabled: bool = True
```

`ActionContext` gives the run's id, the event, the rendered options, the service (to call operations), the
secret store, the clock, a `CancelToken` and a log sink.

| Action | Does | Main options | Notes |
|---|---|---|---|
| `script` | Runs a program from `scripts_dir` | `command` (argv), `cwd`, `env`, `stdin` (`event` \| `none` \| a template) | Never through a shell, each argument rendered on its own. The event as JSON on stdin and in `UDL_EVENT_FILE`; `UDL_EVENT_ID`, `UDL_EVENT_TYPE` and `UDL_CONFIG` set. stdout that is JSON becomes the output. Runs through Phase 2's `run_process()` |
| `operation` | Calls any udl operation | `operation`, `arguments` (templated JSON) | e.g. download the links in an email: `{"operation": "download", "arguments": {"urls": "{{ event.data.body_text \| urls }}"}}` |
| `webhook` | Sends an HTTP request | `url`, `method`, `headers` (values may be `SecretRef`s), `body` | `http_client()`; keeps the status and the start of the body |
| `emit` | Fires another event | `type` (`custom.*`), `data` | Depth + 1 |
| `notify` | A desktop notification | `title`, `body`, `urgency` | `notify-send` (a requirement) |
| `ai` | Runs an AI agent | see [Phase 14](#phase-14-local-ai) | |

An external AI agent (any CLI agent run headless, with udl's MCP server configured) is a `script` action;
`docs/automations.md` gets an example.

### The bus, the runner and the runtime (9.6–9.9)

- **Publishing:** an event is stored (its `(type, dedupe_key)` unique, so a duplicate is dropped there),
  then matched against the enabled subscriptions, and a run is queued for each matched action.
- **Runs:** `queued → running → succeeded | failed | cancelled`, with `awaiting_approval` from Phase 14.
  At most `max_concurrent_runs` run at once and at most `max_concurrent` of one action. Retryable failures
  (`NetworkError`, `RateLimited`, a timeout when the action says so) are tried again `retries` times.
- **Loops:** an action's own events (an email it sends, a Slack message it posts, an `emit`) carry
  `caused_by` and `depth + 1`; past `max_event_depth` they are stored but not dispatched. Integrations
  recognise what udl sent (12.6, 13.5), so subscriptions can say `not sent_by_udl`.
- **The runtime** (`AutomationRuntime`) runs in `udl serve`, in the API's lifespan beside the service,
  unless `[automations] enabled = false`. It takes the `automations` lease (renewed every 10 s, 30 s TTL)
  before running anything; a second `udl serve` on the same database serves the API and UI and says (in
  `get_status`) which udl runs the automations.
- **Processes without the runtime** (a CLI run, `udl mcp` on stdio) publish events as `pending`, and the
  runtime dispatches pending events on start and every 2 s. A run asked for directly (`run_action`,
  `try_prompt`) runs in the process that asked, as downloads do; runs caused by events run in the runtime.
- **Recovery:** runs left `running` by a runtime that lost its lease, or by a udl that stopped (Phase 3's
  `InstanceLock`, as for downloads), become `failed: interrupted`; pending events are dispatched again
  (runs are keyed by event, action and dispatch, so this queues nothing twice; a replay is a new dispatch).
- **Retention:** events and ended runs older than `event_retention_days` are pruned hourly.

---

## Phase 10: Scheduler

### Tests first
- `CronExpression` (hypothesis, across DST changes in several timezones): next times are strictly after
  `now`, in order, and match the expression in the schedule's wall-clock time; a time skipped by a
  spring-forward runs once just after the gap; a time repeated by a fall-back runs once.
- `every` intervals are anchored at their `start` (no drift); `at` fires once, then the schedule disables.
- Misfires: with udl stopped over a due time, `skip` fires next at the next due time; `once` fires one late
  run (`late: true`) if within `misfire_grace`.
- Scheduler on a `FakeClock`: fires each due time exactly once (a crash between firing and saving
  `next_run_at` is deduplicated); a schedule saved with an earlier time wakes the loop; jitter stays within
  its bound.

### Specs

Told apart by their key, like tag chain steps:

```yaml
cron: "0 8 * * 1-5"          # minute hour day month weekday, in the schedule's timezone
every: 15m                   # from `start` (default: when it was saved)
at: 2026-11-01T09:00         # once
```

```python
class Schedule(Record):
    name: str
    spec: CronSpec | EverySpec | AtSpec
    timezone: str | None = None        # IANA name; None = [automations] timezone, else the system's
    data: JsonObject = {}              # passed on in the event
    misfire: Misfire = Misfire.SKIP    # skip | once
    misfire_grace: timedelta = timedelta(hours=1)
    jitter: timedelta = timedelta(0)
    enabled: bool = True
    next_run_at: datetime | None
    last_run_at: datetime | None
```

- The cron parser is a library (`cronsim` or `croniter`, whichever is maintained when 10.1 starts) behind
  `CronExpression`, so it can be swapped. Timezones are `zoneinfo`.
- **One loop**, not a job per schedule: it sleeps until the earliest `next_run_at` (or a change), fires
  `schedule.fired` with the dedupe key `schedule:<id>:<scheduled_for>`, then saves the next time. Not
  APScheduler: schedules are records like everything else, and the loop is a few lines on the `Clock`.
- **One command for a cron job:** `set_schedule(…, actions=[…])` keeps a linked subscription
  (`schedule:<name>`, on `schedule.fired` where `schedule == "<name>"`), so
  `udl schedules set digest --cron "0 8 * * 1-5" --action morning-digest` is all it takes. The subscription
  is shown, and deleted with the schedule.
- `preview_schedule` gives the next N times of a spec without saving it (the UI's cron editor calls it as
  you type).
- This also covers the old plan's "scheduled downloads": a schedule with an `operation` action calling
  `download`.

---

## Phase 11: Integrations & accounts

### Tests first
- Integration contract suite, parametrised over every integration (`fake`, `gmail`, `slack`, each against
  its fake server): its event types' examples validate; every operation has hints and documented fields;
  connecting gives an identity; `watch()` emits only its own event types, stops promptly when cancelled,
  and resumes from its saved state without repeating events.
- Accounts: names are unique per integration; `gmail:work` and `gmail:personal` hold separate credentials;
  an expired token is refreshed once, a refused refresh marks the account `needs_reauth` and publishes
  `udl.account.needs_reauth`; disconnecting revokes, deletes the secret and stops the watcher.
- Integration operations: `account` is required while two accounts are connected, optional with one, and an
  unknown account is a 404 naming the connected ones. A fan-out operation with `account: "*"` runs on
  every connected account at once and merges the results, each tagged with its account; an account that
  fails (e.g. `needs_reauth`) is listed under `failures` while the others' results still come back; an
  operation that writes refuses more than one account.
- Watchers: one per account that an enabled subscription could match (none for an account nothing
  subscribes to); a failing watcher restarts with backoff (1 s, doubling, at most 5 min); its state is saved
  after each batch it emits.

### Interface

```python
class Integration[S: IntegrationSettings, A: AccountOptions]:
    name: ClassVar[str]                                    # "gmail"
    title: ClassVar[str]                                   # "Gmail"
    requires: ClassVar[tuple[Requirement, ...]] = ()
    settings_model: ClassVar[type[IntegrationSettings]]    # [integrations.<name>] + its PluginSettings
    account_model: ClassVar[type[AccountOptions]]          # per account, e.g. the labels to watch
    connect: ClassVar[tuple[ConnectMethod, ...]]           # OAUTH, TOKENS, or NONE (a source that needs no login)
    event_types: ClassVar[tuple[EventType[Any], ...]]
    source_filter_model: ClassVar[type[BaseModel] | None] = None
    state_model: ClassVar[type[BaseModel] | None] = None   # its watcher's cursor
    ui: ClassVar[UIBundle | None] = None                   # its UI plugin (15.7)

    def __init__(self, settings: S, *, http: HttpClientFactory, clock: Clock) -> None: ...
    def oauth_request(self, access: set[str]) -> OAuthRequest: ...            # provider, scopes, extra params
    async def identify(self, credentials: Credentials) -> AccountIdentity: ...  # who this login is
    async def check(self, account: AccountHandle[A]) -> AccountHealth: ...
    async def watch(self, account: AccountHandle[A], events: EventSink, state: StateStore) -> None: ...
    async def source_filter_matches(self, account: AccountHandle[A], event: Event, source_filter: BaseModel) -> bool: ...

    @integration_operation("send_message", hints=OperationHints(open_world=True))
    async def send_message(self, account: AccountHandle[A], request: SendMessage) -> SentMessage: ...
```

```python
class Account(Record):
    integration: str                   # "gmail"
    name: str                          # the user's name for it ("work"); unique per integration
    identity: AccountIdentity          # e.g. the address, or the Slack team and user
    access: list[str]                  # what it was connected for: ["read", "send"]
    options: JsonObject                # validated by the integration's account_model
    status: AccountStatus              # connected | needs_reauth | disabled | error
    secret_ref: SecretRef
    last_error: ErrorInfo | None
```

An integration operation's request model gets an `account` field put in front (`GmailSendMessageRequest`),
and its name the integration's prefix (`gmail_send_message`).

**Several accounts at once:** a read-only operation that lists things (`gmail_list_messages`,
`slack_search_messages`, …) is marked `fan_out`, and its response is `items` (each with its `account`) plus
`failures`. Its `account` then also takes a list or `"*"`: udl runs it on each account concurrently and
merges the items, so an agent using udl's MCP server searches every inbox in one call. The response has
the same type for one account or many.

### Connecting (11.3)

| From | OAuth (Gmail) | Tokens (Slack) |
|---|---|---|
| CLI | `udl accounts add gmail work --access read,send` opens the browser at Google; a loopback listener catches the redirect; prints the account | `udl accounts add slack acme` asks for the tokens (hidden), or reads them from stdin |
| API / UI | `POST /accounts` gives `{connection, authorization_url}`; Google redirects to `/accounts/callback` on the udl server; `GET /accounts/{account}` shows it connected | `POST /accounts` with the tokens (secret fields) |
| MCP | `connect_account` gives the URL for the user to open (over stdio, a loopback listener in the `udl mcp` process); the agent follows up with `get_account` | Tokens as secret arguments (allowed; the CLI is the safer place) |

An integration that connects with `none` (a source that needs no login, such as a watched web page later
on) is added with its options only: `udl accounts add <integration> <name> -O KEY=VALUE`.

Google allows loopback redirects for Desktop OAuth clients, so the UI's flow works when the browser runs on
udl's machine. Serving the UI to other machines needs a Web OAuth client with the server's HTTPS callback
registered (documented in 18.4).

---

## Phase 12: Gmail plugin

### Tests first
- Against the fake Google servers: connecting (scopes by access, `login_hint`, PKCE); a new message in a
  watched label fires `gmail.message.received` once, even when two polls see it; an expired history id is
  recovered by listing since the last message seen, firing only what was missed; messages in spam, trash,
  sent or drafts never fire.
- Event data: HTML-only mail gets a text body; attachments are listed, not downloaded; a message udl sent
  (its `X-UDL-Run` header) has `sent_by_udl` and its run's causation.
- Operations: send / reply build correct MIME (threading headers, attachments); an attachment outside
  `attachment_roots` is refused for the API and MCP; `401` refreshes once, then `needs_reauth`; `429` and
  `rateLimitExceeded` are `RateLimited` with the wait Google asks for.

### Setup (documented in 18.4)

1. A Google Cloud project with the Gmail API enabled and an OAuth consent screen (External).
2. An OAuth client of type **Desktop app**; its id and secret go in `[integrations.gmail]`, or are pasted
   into the Gmail UI plugin's setup (stored as `PluginSettings`, the secret in the secret store). A Desktop
   client's secret is not confidential (Google says so), but udl keeps it with the other secrets.
3. **Publish the consent screen** ("In production"; unverified is fine for personal use, with Google's
   warning screen). While it is in "Testing", refresh tokens expire after 7 days and every account has to
   be connected again each week. Check this against Google's docs when 12.2 starts.

| Access | Scope | Allows |
|---|---|---|
| `read` | `gmail.readonly` | reading mail, watching for new mail |
| `send` | `gmail.send` | sending and replying |
| `drafts` | `gmail.compose` | drafts |
| `organise` | `gmail.modify` | labels, read / unread, archive |

### Watching (12.4)

- On connecting, the cursor is the mailbox's current `historyId` (`users.getProfile`), so old mail never
  fires. Every `poll_interval` (default 60 s, at least 15 s), `users.history.list` with
  `historyTypes=messageAdded` from the cursor; each added message in `watch_labels` (default `["INBOX"]`) is
  fetched (`messages.get`) and becomes an event, deduplicated by `<account id>:<message id>`.
- A history id Google no longer knows (a 404, after about a week offline) falls back to `messages.list`
  with `after:<last message seen>`, firing what was missed.
- Polling is a default, not a limit: Gmail's push notifications need a Google Cloud Pub/Sub topic (out of
  scope; see the end), while polling works on any machine behind NAT, and history calls are cheap in quota.

`gmail.message.received` data (`GmailMessage`): account (name, address), id, thread_id, message_id (the
RFC 822 one), from / to / cc / reply_to (name, address), subject, date, snippet, labels, body_text (the
text part, else the HTML as text via `selectolax`, at most 256 KiB), has_attachments, attachments (name,
type, size, id), list_id (mailing lists and newsletters), in_reply_to, sent_by_udl.

### Source filter (12.5)

`{"query": "from:bank.com has:attachment"}`: Gmail's own search syntax, checked by asking Gmail
(`messages.list` with `q="(<query>) rfc822msgid:<id>"`, one result at most), after the generic filter
passed, cached per message and query.

### Operations (12.6)

| MCP tool | CLI | Access | Hints |
|---|---|---|---|
| `gmail_list_messages` | `udl gmail list [--query Q] [--max N]` | read | read-only |
| `gmail_get_message` | `udl gmail show <id>` | read | read-only |
| `gmail_get_thread` | `udl gmail thread <id>` | read | read-only |
| `gmail_send_message` | `udl gmail send --to … --subject … [--body-file F] [--html] [--attach F…]` | send | open-world |
| `gmail_reply` | `udl gmail reply <id> [--all] --body …` | send | open-world |
| `gmail_create_draft` | `udl gmail draft --to … --subject …` | drafts | |
| `gmail_modify_message` | `udl gmail modify <id> [--add-label L] [--remove-label L] [--read \| --unread] [--archive]` | organise | idempotent |
| `gmail_list_labels` | `udl gmail labels` | read | read-only |
| `gmail_save_attachments` | `udl gmail attachments <id> [-o DIR]` | read | (writes into udl's download dir) |

- `gmail_list_messages` fans out: `udl gmail list --account '*' --query 'from:bank.com'` searches every
  connected inbox.
- MIME is built with the standard library's `email` package; replies set `In-Reply-To`, `References` and the
  thread. Everything udl sends from a run carries an `X-UDL-Run: <run id>` header.
- Attachments to send are files on udl's machine: from the API and MCP they must be under
  `[integrations.gmail] attachment_roots` (a file-only setting, default the download dir), so an API
  client or an AI cannot mail out `~/.ssh`.
- Saved attachments go to `download_dir/gmail/<account>/` (or `-o`), named safely, as downloads are.

---

## Phase 13: Slack plugin

### Tests first
- Against the fake Slack: tokens are checked with `auth.test` and give the team and user; a missing scope
  is named in the error.
- Socket Mode: every envelope is acknowledged within Slack's 3 s; a `disconnect` (Slack refreshing the
  connection) reconnects without losing events; a dropped socket reconnects with backoff; an envelope Slack
  sends again (same `event_id`) fires once.
- Events: a message in a channel, a private channel, a DM and a group DM each fire `slack.message.posted`
  with the right `channel.kind`; edits, joins and bot echoes don't; a message udl posted (its channel and
  `ts` recorded when sent) has `sent_by_udl`; `mentions_me` is set when the account's user is mentioned.
- Web API: cursor pagination; `ratelimited` waits for `Retry-After`; `invalid_auth` / `token_revoked` mark
  the account `needs_reauth`.

### Setup

1. `udl slack manifest` (`slack_app_manifest`) prints an app manifest: Socket Mode on, the user scopes
   below, and user event subscriptions (`message.channels`, `message.groups`, `message.im`,
   `message.mpim`, `reaction_added`).
2. Create the app from that manifest at api.slack.com, install it to the workspace, and create an
   app-level token with `connections:write`.
3. `udl accounts add slack acme` with the user token (`xoxp-`, to read and send as yourself), the app-level
   token (`xapp-`, for events) and, optionally, the bot token (`xoxb-`, to send as the app). Each workspace
   is an account; several are fine.

User scopes: `channels:history`, `groups:history`, `im:history`, `mpim:history`, `channels:read`,
`groups:read`, `im:read`, `mpim:read`, `users:read`, `chat:write`, `reactions:read`, `reactions:write`,
`files:read`, `search:read`.

Slack accounts connect with tokens rather than OAuth: Slack's OAuth needs an HTTPS redirect URL, which a
local udl doesn't have, and the user's own app is what a personal tool wants anyway.

### Events

| Event type | When | Data |
|---|---|---|
| `slack.message.posted` | A new message (not an edit, a join or a bot echo) | account, team, channel (id, name, kind: `channel` \| `private` \| `dm` \| `group_dm`), user (id, name), text, ts, thread_ts, mentions_me, files, sent_by_udl |
| `slack.reaction.added` | A reaction on a message | account, channel, user, reaction, item_ts |

Users and channels are looked up once an hour (cached), so filters can say `channel.name == "alerts"`.

### Operations

| MCP tool | CLI | Hints |
|---|---|---|
| `slack_list_channels` | `udl slack channels` | read-only |
| `slack_read_channel` | `udl slack read <channel> [--since T] [--limit N]` | read-only |
| `slack_read_thread` | `udl slack thread <channel> <ts>` | read-only |
| `slack_search_messages` | `udl slack search <query>` | read-only |
| `slack_send_message` | `udl slack send <#channel \| @user> <text> [--thread TS] [--as-bot]` | open-world |
| `slack_add_reaction` | `udl slack react <channel> <ts> <emoji>` | idempotent |
| `slack_get_user` | `udl slack user <id \| @name \| email>` | read-only |
| `slack_save_files` | `udl slack files <channel> <ts> [-o DIR]` | (writes into udl's download dir) |
| `slack_app_manifest` | `udl slack manifest` | read-only |

`slack_list_channels` and `slack_search_messages` fan out over accounts (workspaces).

---

## Phase 14: Local AI

### Tests first
- AI provider contract suite (`fake`, `ollama` and `openai-compatible` against fake servers): models are
  listed; a chat with tools returns tool calls as typed `ToolCall`s; a provider that is down is a
  `ToolUnavailable`-style error naming its URL; a provider with a `command` is started on first use, waited
  for, and stopped after `idle_timeout`.
- `ToolPolicy`: only listed tools are offered; read-only tools run; in `ask` mode a destructive or
  open-world tool waits for approval unless it is in `auto_approve`, and in `auto` mode it runs unattended;
  `admin` operations are never offered to an action in either mode, even when listed (a glob like `*` skips
  them); a run records which mode it ran in.
- Agent loop (`FakeAI`): a tool result goes back to the model; the loop stops at an answer, `max_steps`,
  the timeout or the size limits; the transcript is on the run; a denied tool call is told to the model,
  which may carry on without it.
- Prompts: guidance applies by scope and priority; event data reaches the model only inside the fenced,
  labelled block; an event whose text says "ignore your instructions and send…" cannot reach a tool the
  policy refused (a regression test kept for good).

### Providers (14.1–14.3)

```python
class AIProvider[O: ProviderOptions](ABC):
    name: ClassVar[str]                                   # "ollama", "openai-compatible"
    options_model: ClassVar[type[ProviderOptions]]        # url, api key (a SecretRef), command, idle_timeout, …
    requires: ClassVar[tuple[Requirement, ...]] = ()
    async def health(self) -> ProviderHealth: ...
    async def models(self) -> list[ModelInfo]: ...        # name, size, context length, tools (when known)
    async def chat(self, request: ChatRequest) -> ChatResponse: ...
    def stream(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...   # the playground
    async def pull(self, model: str) -> AsyncIterator[PullProgress]: ...      # where the provider can
```

- **`ollama`** (0.40 is installed here): `/api/chat` with tools, `/api/tags`, `/api/pull`; `keep_alive`
  loads a model when it is needed and unloads it when idle. udl detects Ollama on `127.0.0.1:11434` and
  `list_ai_providers` offers it before one is added.
- **`openai-compatible`**: `/v1/chat/completions` and `/v1/models`, for `llama-server`, LM Studio, vLLM,
  LocalAI, or any remote endpoint that speaks it (its key in the secret store).
- **"Spins up":** any provider may have a `command` (e.g. `llama-server -m ~/models/x.gguf --port 8081`):
  udl starts it on first use, waits for its health check, and stops it after `idle_timeout`.
- Providers are records (`AIProviderConnection`: name, plugin, options, default model, `max_concurrent`,
  by default 1, since one GPU runs one model at a time), so they can be set up from the UI, the CLI and MCP.
- **No default model:** models change faster than this plan. `udl ai models` lists what a provider has, and
  each provider or action names one.
- Over httpx, no vendor SDKs, like the integrations.

### The agent (14.4–14.6)

1. **Messages:** a system message made of the guidance that applies (everywhere, then the event type's, the
   subscription's and the action's, by priority) and the prompt's `system`; a user message that is the
   prompt's template rendered with the event, with the event's data in a fenced block labelled as
   untrusted content to treat as data, not instructions.
2. **Tools** are udl operations: their JSON Schemas come from the request models and their descriptions from
   the operations' docstrings, already written for agents (MCP).
3. **Each tool call** goes through the `ToolPolicy`, then `UDLService.call` (the same validation as every
   interface); its result (truncated) goes back to the model.
4. **It stops** at an answer, `max_steps` (default 8), the action's timeout, or the size limits. The answer
   (text, or JSON matching the prompt's `output_schema`) is the run's output, so `udl.action.succeeded` can
   feed another subscription.

| Tool | `ask` mode (the default) | `auto` mode |
|---|---|---|
| Read-only (`gmail_get_message`, `slack_read_thread`, `list_downloads`…) | at once, when listed in `tools` | at once, when listed |
| Destructive or open-world (`gmail_send_message`, `slack_send_message`, `download`…) | after approval, unless listed in `auto_approve` | at once, when listed: unattended |
| `admin`: changes automations, accounts, AI setup or settings (`set_*` / `delete_*` of subscriptions, actions, schedules, prompts, guidance, providers; `connect_account`, `disconnect_account`, `set_plugin_settings`, `decide_run`) | never offered to an AI action | never offered to an AI action |

`tools` takes names and globs (`gmail_*`, or `*` for every operation that isn't `admin`). **`auto` mode**
is set per action, for actions meant to run unattended. Their `tools`, their subscriptions' filters and
throttles, and `max_steps` are then all that stands between an event's content and those tools, so they
should be narrow (see [Security model](#security-model)). `admin` operations stay out of every AI action's
reach for now; a later AI chat, with the user there to confirm each call, is where they will be offered.

**Approvals** (`ask` mode): the run waits as `awaiting_approval`, showing the tool call (name and arguments), and
publishes `udl.action.awaiting_approval`, so a subscription can send you a Slack DM or a desktop
notification about it. `decide_run` (CLI, API, MCP, the UI's inbox) approves or denies it, with an optional
note for the model; after `[ai] approval_timeout` (default 24 h) it is denied.

### Prompts and guidance (14.7)

```python
class Prompt(Record):
    name: str                          # "summarise-email"
    description: str = ""
    system: str = ""                   # instructions for the model
    template: str                      # the user message, a template over the event
    output_schema: JsonObject | None = None   # ask for JSON of this shape

class Guidance(Record):
    name: str
    applies_to: GuidanceScope          # everywhere | event types (globs) | subscriptions | actions
    text: str                          # "Never reply to newsletters." "Our on-call channel is #ops."
    priority: int = 0
    enabled: bool = True
```

The `ai` action (14.8):

```yaml
name: summarise-bank-mail
kind: ai
options:
  provider: local                      # an AI provider's name; default [ai] default_provider
  model: "<one `udl ai models` lists>"
  prompt: summarise-email              # a Prompt's name, or inline {system: …, template: …}
  guidance: [money-rules]              # extra, besides the guidance that applies by scope
  tools: [gmail_get_message, slack_send_message]
  approval: ask                        # ask: tools with effects wait for approval | auto: unattended
  auto_approve: []                     # (ask mode) tools that never wait
  max_steps: 8
timeout: 5m
```

---

## Phase 15: Web UI foundations & UI plugins

### Tests first
- Serving (pytest): `/ui/` and any client route under it answer `index.html`; hashed assets are cached as
  immutable and `index.html` is not; responses carry the CSP; a missing build answers the "build it" page;
  API routes are unchanged; a UI plugin's files are served from its bundle dir and nothing outside it.
- Sessions (pytest): with an `api_key`, the UI's API calls work with the session cookie; a write without the
  CSRF header, or from another Origin, is refused; signing out ends the session; without an `api_key` on
  localhost, no sign-in is needed (as the API today).
- Manifests (pytest): invalid manifests, an unknown contribution point, an entry outside the bundle, or an
  incompatible SDK range are listed as problems, not served.
- Shell (Vitest): the theme store follows the system until a choice is made and persists it; the router
  redirects to sign-in when the server asks; the live store reconnects with backoff and keeps a bounded
  buffer.
- Plugin host (Vitest + Playwright): a contribution mounts into its slot, gets prop updates, unmounts on
  navigation; one that throws shows an error card and the shell carries on; its styles don't leak out of
  its shadow root, and the shell's don't leak in.

### The stack (15.1)

| Concern | Choice |
|---|---|
| Build | Vite with `@vitejs/plugin-react-swc` |
| Language | TypeScript, strict |
| UI | React (19 when this was written) |
| Styling | Tailwind CSS 4 (`@tailwindcss/vite`) + daisyUI 5: `@plugin "daisyui" { themes: light --default, dark --prefersdark; }` |
| Icons | `lucide-react` (manifests name icons; the shell maps the names) |
| Routing | TanStack Router, file-based routes (`@tanstack/router-plugin`) |
| State | Zustand (vanilla stores, so UI plugins can share them) |
| API | `openapi-typescript` + `openapi-fetch`, generated from udl's OpenAPI schema |
| Unit tests | Vitest + Testing Library (jsdom), `@vitest/coverage-v8` |
| E2E | `@playwright/test`, pinned to the Python Playwright's version |
| Lint and format | Biome |
| Packages | a pnpm workspace |

Versions are whatever `pnpm add` gives when 15.1 starts (pinned by `pnpm-lock.yaml`), as `uv add` did.

### Serving and signing in (15.3–15.4)

- `udl serve` serves the built shell at **`/ui/`** (static files, falling back to `index.html` for client
  routes). `/` redirects to `/ui/` when a browser asks for HTML. The API keeps its paths: they are
  documented and scripts and MCP clients use them, while the UI needs only one prefix. UI plugins are at
  `/ui/plugins/<name>/` from each plugin's bundle dir.
- Headers on `/ui`: `Content-Security-Policy: default-src 'self'; connect-src 'self'; frame-ancestors
  'none'` (styles are adopted stylesheets, so `style-src 'self'` should hold; 15.9 settles it),
  `Referrer-Policy: same-origin`, `X-Content-Type-Options: nosniff`.
- The build (`src/udl/interfaces/web/dist/`, and each built-in UI plugin's `ui/`) is package data, built by
  `make web` and not committed. Without it, `/ui/` says "run `make web`".
- **Without an `api_key`** (localhost, the default) the UI just works, as the API does. **With one**,
  `/ui/login` asks for it once; `POST /session` sets `udl_session` (a random 256-bit token kept server-side,
  `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS, `[web] session_hours`). The API accepts the cookie in
  place of `X-API-Key`; writes also need the `X-UDL-CSRF: 1` header and a same-origin `Origin`. So the
  browser's `EventSource` works without headers, and other sites still can't drive udl. Sessions live in
  the server process: restarting it means signing in again.

### The shell (15.5–15.6)

- **Layout:** a daisyUI drawer (Dashboard, Downloads, Sites, Chains, Events, Subscriptions, Actions, Runs,
  Schedules, AI, Accounts, Plugins, Databases, Settings, and the plugins' nav items) and a navbar (theme
  switcher, runtime status, approvals waiting, sign out). It works down to phone width.
- **Routing:** file routes under `web/packages/shell/src/routes/`, `basepath: '/ui'`; `beforeLoad` checks the
  session; `validateSearch` keeps list filters in the URL; loaders fetch through the typed client, with
  daisyUI loading and error states. `/p/$plugin/$` renders a plugin's route, handing it the rest of the path,
  so plugin pages need no compile-time route tree, and udl's own routes stay fully typed.
- **State (Zustand):** `session`, `theme` (`system` | `light` | `dark`, persisted; sets `data-theme` on
  `<html>`, or leaves it off to follow the system), `live` (one SSE connection per stream: recent events in a
  bounded buffer, runs and download jobs by id, connection state), `plugins` (manifests, load errors),
  `toasts`. Server data stays in route loaders (`router.invalidate()` after a change, or when a live event
  says the data changed), not copied into stores.
- **Dev:** Vite on :5173 serves `/ui/` with HMR and proxies every other path to `udl serve` on :8765. The
  proxied Host stays `localhost`, so `LocalHostsOnly` lets it through.

### UI plugins (15.7–15.10)

Any plugin (an integration, an action kind, a site or downloader plugin) can bring a UI plugin through its
`ui` class attribute. A UI-only plugin (a dashboard, a theme) is a `udl.ui` entry point, or a folder with a
`manifest.json` in `[web] ui_plugin_dirs`. `GET /ui/plugins.json` lists the valid ones; `list_plugins` says
which plugins have a UI.

```jsonc
// manifest.json, next to the bundle (validated by udl's UIManifest model)
{
  "name": "gmail",                       // the plugin it belongs to, or a UI-only plugin's own name
  "version": "0.1.0",
  "sdk": "^1.0",                         // the @udl/ui-sdk versions it was built for
  "entry": "index.js",                   // an ES module
  "styles": ["index.css"],               // adopted into its shadow root
  "isolation": "shadow",                 // "shadow" (default) or "none" (light DOM, the shell's CSS)
  "contributes": {
    "nav": [{ "title": "Gmail", "icon": "mail", "route": "inbox" }],
    "routes": ["inbox", "compose", "message/$id"],
    "settings": ["gmail"],               // a settings panel for the plugin (its PluginSettings)
    "accountSetup": ["gmail"],           // how an integration's accounts are connected
    "eventViews": ["gmail.*"],           // how its events look in the timeline and in detail
    "filterEditors": ["gmail"],          // an editor for its source filter
    "actionEditors": [],                 // editors for action kinds it adds
    "widgets": [{ "slot": "dashboard", "id": "unread" }],
    "themes": []                         // daisyUI themes it adds
  }
}
```

```ts
// A UI plugin's entry, built with React and daisyUI; any framework works through the mount contract.
import { defineUIPlugin, react } from "@udl/ui-sdk";

export default defineUIPlugin({
  routes: { inbox: react(Inbox), compose: react(Compose), "message/$id": react(Message) },
  settings: { gmail: react(Settings) },
  accountSetup: { gmail: react(Connect) },
  eventViews: { "gmail.*": react(MessageEvent) },
});

// What every contribution is, whatever it is built with:
type Mount<P> = (target: HTMLElement, props: P, ctx: UIContext) => { update(props: P): void; unmount(): void };
```

`UIContext` is all a plugin gets from the shell: `api` (the typed client on the shell's session, plugin
operations included) and `call(name, request)` for any operation; `events.subscribe(filter, callback)` on the
shared live connection; `theme` (mode, name, tokens, `onChange`); `stores` (read-only vanilla Zustand stores:
`getState`, `subscribe`); `navigate`, `toast`, `confirm`; and `plugin` (its name, version and base path).

- **Isolation over sharing:** each plugin bundles its own framework and styles. A contribution mounts into a
  shadow root with its stylesheets adopted there, so a plugin built with shadcn/ui (or Vue, or plain DOM)
  doesn't touch the daisyUI shell, and the shell doesn't touch it. The cost is a copy of React per plugin
  that uses React (tens of kB); sharing one through an import map can come later if that matters.
- **Theming across the boundary:** CSS custom properties inherit into shadow roots, so daisyUI's theme
  tokens (`--color-primary`, …) reach every plugin, and the host sets `data-theme` inside each root. A
  shadcn/ui plugin maps its variables onto them (`--primary: var(--color-primary)`) and follows the theme
  switch. A plugin's `themes` add daisyUI themes to the switcher. Known catch: Tailwind 4's `@property` rules
  are ignored inside a shadow root, so the host hoists a plugin's `@property` rules to the document.
- **Failures stay contained:** entries load lazily (`import()`) the first time one of their contributions is
  needed; a load error or a throw shows an error card in that slot ("The gmail UI failed: …"). An SDK major
  the shell doesn't support is listed as a problem, like a Python plugin that failed to load.
- **udl's own pages** are part of the shell (they share its React and CSS), but are registered in the same
  contribution registry, so nav, slots and widgets treat them like plugins'.
- **Without a UI plugin**, a plugin is used through the CLI, API and MCP: it gets no pages, settings or
  account setup in the web UI until someone writes a UI plugin for it (the Plugins page only notes "no web
  UI"). Its events still show in the event log (as JSON), and can be subscribed to, because events and
  subscriptions are udl's own pages.
- **15.10's proof:** `web/examples/shadcn` is a UI plugin built with shadcn/ui and its own Tailwind setup. It
  adds a route and a dashboard widget, and an e2e test checks it renders with its own styles, follows the
  theme switch, and leaves the shell's styles alone, as Phase 7's MongoDB plugin proved the database
  abstraction. `web/packages/template` is the starting point that `docs/ui-plugins.md` walks through.

### The API client (15.2)

`python -m udl.interfaces.api.openapi` writes `create_app()`'s schema (default settings, the built-in
plugins) to `web/packages/api/openapi.json` (committed); `openapi-typescript` turns it into types and
`openapi-fetch` into a typed client. `make check` regenerates it and fails on a difference, as
`uv lock --check` does for the lockfile. A third-party UI plugin generates its types from its own udl's
`/openapi.json`.

---

## Phase 16: Web UI for the downloader

### Tests first
- Vitest per page against `FakeUdl`; Playwright per flow against `udl serve` on the fixture site:
  download a page's video and watch its progress to the end; cancel a download; save a strategy and see it
  used; suggest, test and save a chain for `json.html`; sync two databases with a dry run first.

### Pages
- **Dashboard (16.1):** recent downloads, runs and events; the next schedules; accounts that need
  attention; approvals waiting; a `dashboard` widget slot for plugins.
- **Downloads (16.2):** a form (URLs, output dir, tool, template, re-probe); live jobs with progress bars
  from `/downloads/{id}/events`; cancel; history with filters in the URL (status, site, since) and paging.
- **Sites (16.3):** search; a site's strategies (successes, failures, last error, enabled); set a strategy
  (tool and its options, validated by the server); delete.
- **Chains (16.4):** the wizard on the web: a URL → suggestions with previews → pick → test (what each step
  found) → save; import a YAML file, export a site's.
- **Databases, Plugins, Config (16.5):** connections and their health, sync with a dry run; every plugin's
  kind, availability, what's missing, problems, whether it has a UI and its settings panel; the config,
  read-only, secrets hidden.

---

## Phase 17: Web UI for automations

### Tests first
- 17.9's e2e, written first: in the UI, connect a Gmail account (udl's `gmail` plugin, pointed at the fake
  Google servers), add a `script` action, subscribe it to mail from one sender with the filter builder, have
  the fake server deliver a matching and a non-matching message, then see exactly one run appear live, open
  it, and read its output.
- Approvals e2e: an `ai` action (on `FakeAI`) asks to send a Slack message; the approvals badge counts it;
  approving it sends the message to the fake Slack.

### Pages
- **Events (17.1):** live (from the `live` store) and history (filters in the URL); detail through the
  plugin's `eventViews`, else a JSON tree; emit a `custom.*` event; replay one to all or one subscription.
- **Subscriptions (17.2):** pick event types from `list_event_types`; build the filter from the type's
  schema (field paths with their types, operators by type, nested all / any / not), or edit it as the
  shorthand (the two stay in step through `test_subscription`); pick accounts and actions; the plugin's
  `filterEditors` for its source filter; "would have matched" over recent events as you edit.
- **Actions and runs (17.3):** editors for the built-in kinds (`script` picks from `scripts_dir`,
  `operation` builds arguments from the operation's schema, `ai` from providers, models, prompts and tools),
  plugins' `actionEditors` for theirs, a dry run that renders the templates against a chosen event; runs
  with live status, output, errors and the AI transcript (each tool call and its result); the approvals
  inbox.
- **Schedules (17.4):** cron / every / at with the next times from `preview_schedule` as you type, the
  timezone, the actions to run (the linked subscription), run now.
- **AI (17.5):** providers (Ollama offered when detected), their health and models, pulling a model; prompts
  and guidance; a playground that renders a prompt against an event and streams the model's answer
  (`POST /ai/try/stream`), with tool calls shown and not run.
- **Accounts (17.6):** every integration's accounts and their status; connect, reconnect and settings
  through the integration's UI plugin; disconnect.

### The Gmail and Slack UI plugins (17.7–17.8)

Built with React and daisyUI on the SDK, isolated like any other plugin, so they use only what a third
party could:

- **gmail:** setup (the OAuth client, with the consent-screen steps), connecting accounts (the access
  levels, then Google's page), a mailbox per account (search, read, threads), compose and reply, how a
  `gmail.message.received` event looks, and a Gmail-search editor for the source filter.
- **slack:** setup (copy the manifest, paste the tokens, check them), channels and threads per account,
  sending, and how `slack.*` events look.

---

## Phase 18: Hardening & docs

- **Security review (18.1)** against the [security model](#security-model), as Phase 7's was: what it finds
  gets a regression test and a fix, or is documented in the README's Security section.
- **Resilience (18.2):** kill the runtime mid-run, take the database or a provider away, drop the Socket
  Mode connection, expire tokens; each recovers as designed (tests on the fakes). An accessibility pass
  with axe (`@axe-core/playwright`) over every page, in both themes.
- **Always on (18.3):** `udl service install` writes a systemd user unit for `udl serve` (and
  `udl service uninstall` removes it), since the runtime only runs while the server does.
- **Docs (18.4):** the README (accounts, automations, schedules, AI, the web UI, the new security notes);
  `docs/automations.md` (events, filters, subscriptions, actions, schedules, AI, with worked examples: bank
  mail summarised to Slack, a morning digest, downloading links mailed to yourself); `docs/plugins.md` gains
  integrations, action kinds, AI providers and secret stores; `docs/ui-plugins.md` (the manifest, the SDK,
  isolation and theming, using another component library, testing).
- **Smoke tests (18.5),** opt-in like Phase 7's: a real Gmail account receives a mail udl sends to itself and
  fires once; a real Slack workspace does the same with a DM; Ollama answers a prompt with a tool call.

---

## Security model

What changes: udl now reads content written by strangers (email, Slack), holds long-lived credentials, can
run programs, and talks to a browser. Each risk has its answer, and 18.1 checks them.

| Risk | Where | Answer |
|---|---|---|
| Someone else's email or message steers udl (prompt or template injection) | Every templated action; AI actions | Event data is only ever a value: templates are sandboxed, scripts get it on stdin and in env vars (never a shell), the AI sees it fenced as untrusted, and its tools are an allowlist with approval for anything with effects (unless the action is `auto`); `admin` operations are never AI action tools |
| An unattended AI does what a stranger's message asks | `auto` AI actions | Opt-in per action and marked in the UI; its `tools`, its subscriptions' filters and throttles, and `max_steps` are the whole boundary, so they should be narrow; every tool call is in the run's transcript; `admin` operations stay out of reach |
| An API or MCP client runs code | `script` actions | Only programs inside `scripts_dir`, which no operation can write to |
| An API or MCP client reads files | Mail attachments | `attachment_roots`, a file-only setting |
| Loops | Actions causing events | `max_event_depth`, throttles, udl's own messages recognised |
| Credentials leak | Tokens, keys | The secret store only (never a database, the config or a log); `0600` files; redacted in `get_config` and errors; revoked on disconnect |
| A web page drives udl | The UI | Same origin only; `SameSite=Strict`, `HttpOnly` session; CSRF header and Origin check on writes; `LocalHostsOnly` (Phase 4); CSP; `frame-ancestors 'none'` |
| A UI plugin misbehaves | The UI | UI plugins are code you install, like Python plugins (documented). Shadow DOM isolates styles, not trust |
| Requests to internal services | `webhook` actions | As for downloads, anyone who can use the API can make udl fetch a URL (accepted in Phase 7); documented |
| Slow regexes on attacker text | Filters | 64 KiB per field; `google-re2` where available |
| Two runtimes run the same action | A shared database | The `automations` lease |
| Quotas and bans | Gmail, Slack | Watchers only for accounts a subscription needs; `Retry-After` honoured; backoff |

---

## Configuration example

Phases 0–7's settings are in [planning/01-core.md](planning/01-core.md#configuration-example). Durations
are written `30s`, `5m`, `24h`.

```toml
[automations]
enabled = true                            # run the runtime in `udl serve`
max_concurrent_runs = 4
max_event_depth = 8                       # events caused by runs, caused by events, …
event_retention_days = 30
scripts_dir = "~/.config/udl/scripts"     # script actions run programs from here only
timezone = ""                             # schedules' default; empty = the system's

[secrets]
store = "auto"                            # auto (keyring, else file) | keyring | file | memory | a plugin

[integrations.gmail]
client_id = "1234-abc.apps.googleusercontent.com"   # your Desktop-app OAuth client (or set it in the UI)
client_secret = "GOCSPX-…"                # moved to the secret store when set through udl
poll_interval = "60s"
attachment_roots = ["~/Downloads"]        # file-only: what the API and MCP may attach

[integrations.slack]                      # nothing needed: each account brings its app's tokens

[ai]
default_provider = "local"
approval_timeout = "24h"

[web]
enabled = true
session_hours = 12
ui_plugin_dirs = ["~/.config/udl/ui-plugins"]
```

Accounts, subscriptions, actions, schedules, prompts, guidance and AI providers are records, made through the
CLI, API, MCP or UI. For example:

```console
$ udl ai providers set local --plugin ollama --default-model <model>
$ udl accounts add gmail personal --access read,send
$ udl actions set file-statement --kind script -O 'command=["file-statement.py"]'
$ udl subs set bank-mail --on gmail.message.received --account gmail:personal \
    --where 'from.address endswith "@bank.com" and has_attachments' --action file-statement
$ udl schedules set digest --cron "0 8 * * 1-5" --action morning-digest
```

## Dependencies

| Purpose | Packages |
|---|---|
| Templates | `jinja2` (its sandbox) |
| Secrets | `keyring` |
| Schedules | a cron parser (`cronsim` or `croniter`, behind `CronExpression`); `zoneinfo` (standard library) |
| Slack's Socket Mode | `websockets` |
| Gmail, Slack's Web API, OAuth, AI providers | `httpx` (already a dependency) |
| HTML mail to text | `selectolax` (already) |
| Linear-time regexes (if it has wheels) | `google-re2` |
| Web: app | `react`, `react-dom`, `@tanstack/react-router`, `zustand`, `tailwindcss`, `@tailwindcss/vite`, `daisyui`, `openapi-fetch`, `lucide-react` |
| Web: dev | `vite`, `@vitejs/plugin-react-swc`, `@tanstack/router-plugin`, `typescript`, `openapi-typescript`, `vitest`, `@vitest/coverage-v8`, `@testing-library/react`, `@testing-library/user-event`, `jsdom`, `@playwright/test`, `@axe-core/playwright`, `@biomejs/biome` |
| Web: the shadcn/ui example | shadcn/ui's generated components and their dependencies, in `web/examples/shadcn` only |

## Decisions (defaults that can be revisited)

Settled on 2026-10-24: the bus and the scheduler before Gmail and Slack; isolated UI plugins; the UI at
`/ui`; a plugin appears in the web UI only through a UI plugin; AI actions may run unattended (`auto`), but
none may change automations or accounts yet.

- **Numbering continues at Phase 8**, so branches (`phase-8`…) and commit history don't collide with
  Phases 0–7.
- **The bus and the scheduler come before Gmail and Slack:** the integrations' pub/sub is the bus, and the
  scheduler is the simplest event source to prove it end to end.
- **One model for every source:** events, subscriptions and actions, whatever fired the event.
- **Subscriptions alert actions; actions don't form pipelines.** Each matched action is its own run, in
  parallel; chaining goes through `udl.action.*` events.
- **Records in the existing `DatabasePlugin` contract**, not a second storage system: one place to configure,
  sync and back up, and every database plugin keeps working.
- **Secrets never in a database:** a pluggable secret store, the system keyring by default.
- **Gmail by polling the History API; Slack by Socket Mode:** neither needs a public URL or a cloud project
  beyond the OAuth client, so both work on a laptop behind NAT.
- **Own thin clients over httpx** for Google, Slack, Ollama and OpenAI-compatible APIs, typed with Pydantic,
  rather than `google-api-python-client`, `slack_sdk` or vendor AI SDKs: async, typed, one HTTP stack, and
  the fake servers test the real clients. Revisit if what udl uses of an API grows a lot.
- **Slack accounts connect with tokens** from the user's own app.
- **Gmail and Slack are built in** (entry points, like `youtube`) but use only the public plugin API (a test
  checks their imports), so either could move to a package of its own.
- **One runtime per database**, in `udl serve`, held by a lease.
- **Scripts only from `scripts_dir`, never through a shell.**
- **Local AI providers built in; no default model.** AI tools are udl's operations, governed by the hints
  they already carry: anything with effects waits for approval unless the action runs in `auto` mode.
  `admin` operations are never AI action tools; a later AI chat is where they will be offered.
- **The UI at `/ui`; the API's paths unchanged.**
- **UI plugins are isolated (a mount contract and Shadow DOM)** rather than sharing React through Module
  Federation or an import map: any library, framework or version per plugin, at the price of some
  duplicated bytes.
- **Server data in TanStack Router loaders, client and live state in Zustand.** TanStack Query was
  considered (it pairs well with the router); revisit if caching and invalidation get complicated.
- **Biome** rather than ESLint + Prettier: one fast tool, as ruff is for Python.
- **The built UI isn't committed;** `make web` builds it into the package.

## Carried over from Phases 0–7

Things that bit before and will bite again:

- A new operation needs a `MATRIX` row in `tests/interface/test_parity.py`, a CLI command marked
  `@runs("<op>")`, an API route with `**documented("<op>")`, its tool hints (in `_HINTS` until 8.1 moves them
  onto `Operation`), and an attribute docstring on every request field (a test checks).
- Typer 0.26+ vendors Click: never import `click` in the CLI; use `typer.Context` / `typer.core`.
- FastAPI includes routers lazily: read routes from `app.openapi()`, not `app.routes`.
- Async fixtures that hold an anyio task group (the API's lifespan, MCP clients, and now the runtime and the
  fake Socket Mode server) go through `support.tasks.held()`.
- Don't name functions in `src` `test_*` (pytest collects them): `test_subscription`'s method is
  `try_subscription`. Pytest collects `Test*` classes, so request models are `SubscriptionTestRequest`.
- SQLModel can't read PEP 695 `type X = Annotated[...]` aliases on table fields; record tables use plain
  aliases.
- Ruff (py314 target) rewrites `except (A, B):` to `except A, B:`.
- A new entry point or dependency needs `uv tool install --editable '.[browser]' --reinstall` for the global
  `udl`. This plan adds several plugin kinds.
- The postgres and mongodb tests start Docker containers (or use `UDL_TEST_POSTGRES_URL` /
  `UDL_TEST_MONGODB_URL`); the record contract suite runs there too.

## Out of scope (later)

- Automations as files (YAML import/export of subscriptions, actions, schedules, prompts), like chain files.
- Gmail push notifications through Google Cloud Pub/Sub (mail without polling delay).
- Slack's OAuth install flow, Enterprise Grid, edited and deleted messages as events.
- **Generic watchers**, the first step towards pub/sub for apps that have none: a web page through tag
  chains or the browser, a command's output, a file, a feed (sources that connect with `none`). **Watched
  downloads** (playlists, channels) are one of these, and also need the download archive below.
- **An AI chat in udl** (the user present) that may also use `admin` operations, each one confirmed, so it
  can build subscriptions, actions and schedules from a description.
- More integrations (Google Calendar and Drive, sharing one Google account between them; IMAP for other
  mail; Discord; Matrix), and browser-driven integrations for apps without an API.
- Remote AI providers (e.g. Anthropic's API) as plugins; letting AI actions use other MCP servers' tools.
- A 1Password (`op`) secret store.
- Several runtimes sharing the work (an external queue); PostgreSQL `LISTEN/NOTIFY` instead of polling for
  pending events.
- Skipping already-downloaded URLs (a download archive), still out from Phases 0–7.
- Sharing React between UI plugins (an import map), if bundle size ever matters.
- Translations.
