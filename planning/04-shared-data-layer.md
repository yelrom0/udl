# UDL Phases 31–41: The shared data layer: workspaces, people, audit, devices, knowledge and pages

> Part of udl's plan. [planning.md](../planning.md) holds the tracker, the [Working agreement](../planning.md#working-agreement-tdd--typing) (TDD, `pyright --strict`, `make check`), the Definition of Done and the task-card format; they all apply here. Sources: the user's planning conversation of 18 Nov 2026 (a standup where udl's direction was talked through) and the user's answers to Claude's questions the same day. Written by Claude on 18 Nov 2026.

## Progress tracker

**The tracker for these phases is in [planning.md](../planning.md#then-phases-3141-the-shared-data-layer)**, beside Phases 23–30's, so there is one place to mark. The marks and who sets them are explained there. Agents write what they did, the PR and anything they hit under the task's card below, in the same PR as the work.

---

## What it is

udl (Ultra Data Layer) becomes **the shared data layer that people and AI agents work in together**. The user's way of putting it: *udl is the back end and AI is the front end.* AI is good at presenting data quickly (dashboards, artifacts, code), and it is getting good at working as several agents; what it lacks is one back end that many agents and many people share, readable by both. Today that back end is markdown files (the plans, human-comms.md, the agents' queues, Claude's memories); Phases 23–30 move the plans and queues into udl, and these phases make udl the place a whole team uses:

- **A hub** (one server) serves **many workspaces**, each a team's own projects, people, secrets and data. People reach it from the web, the CLI, or their own AI (Claude, ChatGPT, Codex) over MCP, which stays a first-class way in: someone who uses Claude or ChatGPT as their interface can do everything through it.
- **Every machine runs a client:** udl itself, linked to one or more hubs. It is the machine's local, safe way to and from each hub, and its manager. It runs the agents (Claude Code, OpenCode, Codex), holds the secrets of the integrations on it (its Gmail, Slack, a browser session), and pushes their data up. A device belongs to whoever installed it, and its owner decides what each hub and each person may use on it.
- **People:** accounts, sign-in (passkeys, passwords with TOTP), roles per workspace and project, tokens, and OAuth for remote MCP connectors.
- **Audit:** who did what, when, traced to a user or an agent (and on whose behalf), in a tamper-evident log; decisions signed by the people who made them.
- **The data bank:** datasets that plugins, agents and people fill, docs (a wiki), memories shared by every agent and synced both ways with the files agents read, search over all of it, and conversations (meeting transcripts, emails) turned into plan changes that a person accepts, each traceable to where it was said.
- **Pages:** dashboards and public read-only pages over udl's data, written by people or by AI, like an AI's artifacts but on the team's own data and system.
- **Analytics:** how long tasks take, where people and agents are stuck, and how each model does, with budgets.
- And, after Phase 13: **browser-driven integrations** for apps with no API, and later still **sandboxed agent runs**.

udl stays open source and self-hostable; a hub can equally be one person's machine or a shared hosted server for many teams (H-D9).

**Depends on:** Phases 23–30. These phases come **after Phase 30 and before Phases 13–22** (H-D1), except Phase 40, which needs Phase 13 (and 16.2) and is built after them.

## Decisions

**The user's (18 Nov 2026):**

| ID | Decision | Rejected |
|---|---|---|
| **H-D1** | **Phases 31–41 come after Phase 30 and before Phases 13–22.** Multi-user is "one of the next features". Phase 40 is the exception: it needs Phase 13 | A small slice for a teammate's Mac first; after Phases 13–22 |
| **H-D2** | **Multi-tenant: one hub serves many workspaces,** each with its own members, projects, secrets and data | One organisation per hub, a hub instance per customer |
| **H-D3** | **A client can be linked to several hubs, each link in its own way:** its own transport (dialling out over `wss`, reached over SSH, or over a private network), its own identity (user and workspace), what it shares (agents, integrations, folders, operations), and which way data flows (push up, run what the hub hands it, or both) | One hub per client |
| **H-D4** | **The client is the device's local, safe gateway to and from each hub, and its manager. A device is owned by the user who installed it** (managed devices, enrolled for a workspace, excepted), **and what on it is available to whom is up to its owner** | Devices owned by the workspace; the hub decides |
| **H-D5** | **Built-in sign-in first** (passkeys; a password with TOTP; personal tokens for the CLI and MCP), **OIDC later** (39.3) | OIDC only; both at once |
| **H-D6** | **Decisions are kept in a hash-chained log the hub signs; people sign decisions with a passkey, and SSH keys are supported too** | SSH keys only; the hub's signature only |
| **H-D7** | **Memories sync both ways** between udl and the files agents read (Claude's memory folders, a repo's `AGENTS.md` / `CLAUDE.md`) | udl as the source with the files generated; udl as the source with the files hand-written |
| **H-D8** | **A kit for browser-driven integrations now, proven on a fixture app; WhatsApp later.** (WhatsApp's terms forbid unofficial automated clients, and accounts get banned for it) | A read-only WhatsApp Web plugin now; Meta's Cloud API |
| **H-D9** | **Neutral wording in the public repo:** udl can be self-hosted or run as a shared hosted hub for many teams; nothing about pricing or business arrangements | Spelling out a hosted business model; not mentioning shared hubs |
| **H-D10** | **Auditing: "who did what, when",** so a company can trace anything back to a user or an agent: every call is recorded with who made it (a user, an agent, a device, an automation) and on whose behalf, what, when and where (the user, while this plan was being written) | Recording decisions only |

From the conversation itself (the direction these phases follow): udl is the back end and AI the front end; MCP, the web UI and the CLI are equal ways in; agents can be assigned agents (Phase 27's managers) and devices' resources are shared by their owners; pub/sub reaches across plugins (an email about a bug becomes a task); public read-only pages from the team's data; analytics on agents; open source and extensible by plugins written by people or agents; containers for agents "later"; a teammate working with Codex on a Mac.

**Claude's, made while planning (revisit in a phase's "Decisions made while building" if building finds better):**

| ID | Decision | Why |
|---|---|---|
| **H-C1** | **A workspace is its own database** (a SQLite file, a PostgreSQL schema or a MongoDB database), with **its own `UDLService` and runtime** in the hub's process. Hub-level records (users, workspaces, memberships, devices, grants, tokens, sessions) live in a separate hub database | No query can cross workspaces by forgetting a filter; everything built so far works unchanged inside a workspace; exporting, backing up or deleting a workspace is a whole-database operation. A `workspace` column on every record was rejected: one missed filter leaks |
| **H-C2** | **A standalone udl stays exactly as it is** (no hub database, no users, the API key's owner). `udl hub init` turns one into a hub whose existing database becomes workspace `default`, with no data moved | Nobody running udl today has to change anything |
| **H-C3** | **Operations that run programs or touch files on the machine they run on are marked `local`.** On a hub, a workspace may run them only on a device (`on=<device>`), unless the hub's admin allows that workspace to run them on the hub itself (file-only) | On a shared hub, a workspace must not run scripts, browsers or downloads on the hub's machine; that is what devices are for |
| **H-C4** | **One hub ↔ client protocol over every transport:** JSON-RPC 2.0 messages, typed by Pydantic models, over a WebSocket, an SSH channel's stdio, or a direct connection. A device proves itself with its own ed25519 key | One protocol test suite covers all three transports |
| **H-C5** | **Two ceilings.** A device's own config (file-only, on the device) is the most it will ever do for a hub; the hub's grants narrow that. The device checks every call itself, so no hub can widen what a device allows | "Local, safe to/from": a compromised or mistaken hub still can't reach past the owner's limits |
| **H-C6** | **The secrets of a device's integrations never leave the device.** It pushes events and data, never credentials | A hub (possibly shared, possibly hosted) holds no one's Gmail or Slack tokens |
| **H-C7** | **Remote MCP uses the MCP authorization spec** (OAuth 2.1: protected resource metadata, authorization server metadata, dynamic client registration, PKCE, resource indicators), with udl as its own authorization server; the consent page picks the workspace and the scopes | What Claude's and ChatGPT's remote connectors speak |
| **H-C8** | **The audit log is append-only and hash-chained per workspace** (canonical JSON, SHA-256), with checkpoints signed by the hub's ed25519 key. Decisions a project says need signing take a person's passkey assertion or SSH signature over the decision's hash. Agents never sign as people | Tamper-evident, verifiable offline from an export |
| **H-C9** | **Memory files merge three ways** (the last synced version as the base). A conflict is never settled silently: it becomes a question to the memory's owner. A repo's files change only through a PR | `main` is locked (O-C8), and two-way sync must never lose an edit |
| **H-C10** | **Conversations become plans through proposals a person accepts,** each citing the span of the source it came from | Text from outside never changes the plan by itself, and every change traces back to who said it |
| **H-C11** | **Search is full-text in each database plugin** (SQLite FTS5, PostgreSQL `tsvector`, MongoDB text indexes); semantic search is optional, through an AI provider's embeddings, brute-force per workspace | Works on every database udl supports, with no new service to run |
| **H-C12** | **Datasets are two record kinds** (`Dataset`, `DataItem` with JSON data, an optional JSON Schema), not a table per dataset | Records stay "one generic table per kind" (Phase 8) |
| **H-C13** | **Public pages never run anything for the visitor.** A page reads only its views, computed by the hub as the page's publisher and cut down to the fields each view names. AI-written HTML pages run in a sandboxed iframe with no network, fed their data by `postMessage` | An anonymous visitor reaches cached, projected data and nothing else |
| **H-C14** | **The `codex` runner drives `codex exec --json`,** one process a turn, as `claude-code` drives `claude -p`, and never reads Codex's credential file | The same rule as O-C3 |

## Concepts

| Term | Meaning |
|---|---|
| **Node** | A running udl install. Any node can be **standalone** (today's udl) or a **hub**, and any node can be linked to hubs as a **client** |
| **Hub** | A node that serves workspaces to people, agents and devices: the web UI, the API, `/mcp`, the hub database |
| **Workspace** | One team's space on a hub: its own database, secrets, settings, projects, data and audit log. `default` on a hub made from a standalone udl |
| **User** | A person with an account on a hub. A **membership** gives them a role in a workspace: `owner`, `admin`, `member` or `guest` |
| **Principal** | Whoever makes a call: a user, an agent, a device, the standalone owner, the public, or udl itself, with who it acts for (`on_behalf_of`) |
| **Device** | A machine with a client, known to a hub by its key. **Owned** by the user who installed it, or **managed** by a workspace |
| **Link** | A client's connection to one hub: its transport, identity, ceiling and data direction |
| **Ceiling** | What a device allows a hub at most, set in the device's own config file |
| **Grant** | What a device's owner lets a person, a workspace or a role use on it (agents, operations, folders, integrations), within the ceiling |
| **Audit entry** | One recorded call or event: who, on whose behalf, what, when, where, and how it ended, chained to the one before |
| **Decision** | An audit entry of a kind a project can require to be signed: an answer, a merge, a task marked done, a proposal accepted, a grant changed |
| **Dataset / item** | A named collection of JSON items that plugins, agents and people put data in |
| **Doc** | A wiki page, with its revisions |
| **Memory** | A short fact agents should know (as Claude's memory files are), scoped to a workspace, project, user or agent, and synced with files |
| **Source / proposal** | A conversation, email or document brought into udl, and a change to the plan suggested from it, waiting for a person to accept it |
| **View / page** | A saved read-only query, and a page (Markdown or sandboxed HTML) that shows views, privately, to the workspace, or publicly |

## Architecture

```
 People: web UI · CLI · their own AI (Claude, ChatGPT, Codex) over remote MCP (OAuth)
        \___________________________________________________________________/
                                      |
 HUB   authenticate → Principal → workspace → UDLService of that workspace (its own DB, runtime, secrets)
       hub DB: users · memberships · tokens · OAuth clients · devices · grants · workspaces
       per workspace: everything so far (orch, events, automations, integrations…) + audit · kb · pages · analytics
                                      |
              links (JSON-RPC 2.0 over wss · ssh stdio · direct), grants checked on the hub
              ┌────────────────────────┴────────────────────────┐
 CLIENT (jeff-wiggle)                                 CLIENT (a teammate's Mac)
   ceiling from its own config, checked on every call   linked to two hubs, a different identity on each
   ClientShell → runners: claude-code · opencode · codex     its Gmail / Slack / browser apps → push events up
   local ops (downloads, fetches, scripts) with on=<device>  memory files ↔ udl (two-way sync)
   its own audit log of what each hub did on it
```

How a call travels on a hub: the interface authenticates the request into a `Principal` (32.6) and a workspace (31.6); `UDLService.call` checks the principal's permission for the operation (32.7) and, for an agent, its role (27.2); a `local` operation with `on=<device>` goes over that device's link (34.9); every call that isn't read-only, and every sensitive read, is written to the workspace's audit log (33.1), whatever its outcome.

---

## Phase 31: Workspaces and the hub

Today's udl is one person's: one set of databases, one secret store, one owner. A hub holds many workspaces, each with its own database and its own `UDLService`, and routes every call to the right one.

### Tests first
- Isolation, generated over the registry: a record written in workspace `a` is never returned, changed or deleted by any operation called in workspace `b`.
- A standalone udl behaves exactly as before: the whole existing suite passes with no `[hub]` table, unchanged.
- `udl hub init` on an existing database keeps every record, reachable in workspace `default`.
- On a hub, a `local` operation called in a workspace without `local = true` is refused, with a hint naming `on=<device>`.

### Tasks

**31.1 Node roles and the hub database** · needs Phase 30
- Build: a `udl/hub/` package (`__init__.py`, `config.py`, `records.py`, `manager.py`).
  - `[hub]` settings: `enabled = false`; `db`, a list shaped like `[[db.connections]]` for the hub database (default `sqlite:///~/.local/share/udl/hub.db`); `public_url: HttpUrl | None` (what devices, OAuth and passkeys use; **file-only**); `workspace_db`, a URL template with `{workspace}` (default `sqlite:///~/.local/share/udl/workspaces/{workspace}.db`; PostgreSQL `postgresql://udl@db/udl?udl_schema=ws_{workspace}`; MongoDB `mongodb://db/udl_{workspace}`).
  - PostgreSQL: the `postgres` plugin learns the `udl_schema` URL parameter: it creates the schema if missing and sets `search_path` on every connection. A record contract run in two schemas of one database sees no records of the other.
  - `RecordScope.HUB`: hub-level kinds. `DBManager` refuses to store a `HUB` kind in a workspace's databases, and a workspace kind in the hub's.
  - `NodeRole`: `standalone` (no hub) or `hub`. Links to hubs (Phase 34) are separate: any node can have them.
- Tests first: `tests/unit/hub/test_config.py`; the record contract over the `HUB` kinds in every database plugin; `tests/integration/plugins/databases/test_postgres_schema.py`.
- Done when: `udl serve` with `[hub] enabled = false` serves exactly the routes, tools and behaviour it serves today.

**31.2 Workspaces** · needs 31.1
- Build: `udl/hub/records.py`, `udl/hub/services.py`.
  - `Workspace(Record)`, scope `HUB`: `name` (unique; `^[a-z0-9][a-z0-9-]{1,38}$`, since it lands in database names and URLs), `title`, `status: active | suspended | deleting`, `db: list[DBConnectionConfig] | None` (explicit connections, else from the template), `created_by`.
  - `WorkspaceServices`: `get(name) -> UDLService`, built on first use from the hub's `Settings` with `db` replaced by the workspace's connections and the secret store wrapped (31.3), and cached; `close(name)`, `close_all()`.
  - `udl serve` on a hub starts each active workspace's `AutomationRuntime` under that workspace's own lease, and stops a suspended one's. `[hub] max_active_runtimes` (default 50): past it, a workspace with no subscriptions, schedules or apps has its runtime started only when one appears.
- Tests first: `tests/unit/hub/test_services.py`: two workspaces in one process each fire their own schedule on a `FakeClock`; suspending one stops only its runtime; a service is built once.
- Done when: the runtime status (`udl status`) reports each workspace's runtime separately.

**31.3 Secrets per workspace** · needs 31.2
- Build: `udl/hub/secrets.py`: `NamespacedStore(SecretStore)` wraps the hub's store; each `SecretRef` is stored under `ws/<workspace>/…`, and `list()` answers only the workspace's own, with the prefix taken off. A reference that tries to leave its namespace (`..`, an absolute name, another prefix) is refused.
  - The 1Password resolver (25.1) per workspace: `[hub.workspaces.<name>.onepassword] vaults` (**file-only**: the hub's admin decides which vaults a workspace may read), and the service account token is stored in the workspace's namespace.
- Tests first: the secret contract over `NamespacedStore` over `memory`; `tests/unit/hub/test_secrets.py`: `a` can't read `b`'s secret by any reference.
- Done when: `get_config` and `get_secrets_status` in one workspace show nothing of another's.

**31.4 `local` operations** · needs 31.2
- Build:
  - `OperationHints.local: bool = False`: the operation runs programs, opens a browser, or reads or writes files on the machine that runs it. `True` for: `download`, `probe_url`, the chain operations that fetch pages, `fetch_pages` and the fetch operations that read or solve, `import_cookies`, the host and runner operations (25.6, 25.11), and `orch_push`. An action kind can be local too (`ActionPlugin.local`: `script` is; `webhook`, `emit`, `notify` and `operation` aren't), and so is an AI provider's `command`; `run_action` and the action runner check the action's kind, not the operation.
  - `[hub.workspaces.<name>] local = false` (**file-only**). With it false, a `local` operation called in that workspace raises `NotAllowed`: "This runs programs on the hub's machine. Run it on one of your devices: on=<device>". The action runner refuses `script` actions there the same way.
  - The standalone node always allows them; `udl hub init` writes `local = true` for `default` (31.5).
  - `tests/unit/core/test_operations.py` gets a snapshot of every operation's `local` value, so a new operation has to choose.
- Tests first: `tests/unit/hub/test_local.py`: the phase's fourth bullet, over the API and MCP.
- Done when: no `local` operation can run on a hub's machine for a workspace that isn't allowed to.

**31.5 `udl hub init`** · needs 31.2, 31.4
- Build: `udl hub init [--workspace default]`: creates the hub database and the `Workspace` record whose `db` is the node's current `[db]` connections (no data moves), and prints the `[hub]` and `[hub.workspaces.default]` lines to add to the config (udl never writes the config file). `udl serve` with `[hub] enabled = true` and no hub database stops with a hint naming `udl hub init`.
  - Until Phase 32, the configured `api_key` is the hub's admin and picks a workspace with the header of 31.6.
- Tests first: `tests/unit/hub/test_init.py`: the phase's third bullet; running it twice changes nothing.
- Done when: the hand check of the phase passes on a copy of jeff-wiggle's database.

**31.6 Choosing the workspace of a call** · needs 31.2
- Build: `current_workspace: ContextVar[str | None]` beside `current_caller`.
  - API: the `X-UDL-Workspace` header (default: the principal's default workspace; until 32, `default`). The routes are unchanged and the workspace is never a request field, so the generated adapters and the parity test don't change.
  - MCP: `/mcp` serves the default workspace and `/w/{workspace}/mcp` a named one.
  - CLI on the hub's machine: a global `--workspace / -W` option and `$UDL_WORKSPACE`.
  - The API's `service` dependency returns `WorkspaceServices.get(current_workspace)`. An unknown workspace is `DoesNotExist` (404); a suspended one `NotAllowed` (403).
- Tests first: `tests/interface/test_workspace_routing.py`: the API, MCP and CLI each reach the workspace asked for; the error model for an unknown one.
- Done when: the same `list_schedules` call answers differently per workspace through all three interfaces.

**31.7 Workspace operations** · needs 31.6
- Build: a hub-level registry (`udl/hub/operations.py`) beside each workspace's, served under `/hub/…` and in every `/mcp` (admin-gated):

  | Operation | CLI | Hints |
  |---|---|---|
  | `list_workspaces`, `get_workspace` | `udl workspaces [show <name>]` | read-only |
  | `create_workspace` (`name`, `title`) | `udl workspaces create` | admin |
  | `suspend_workspace`, `resume_workspace` | `udl workspaces suspend / resume` | admin |
  | `delete_workspace` (`confirm` = its name) | `udl workspaces rm` | admin, destructive: `status = deleting`, its runtime stopped, its database or schema dropped, its secrets' namespace emptied |
  | `export_workspace` | `udl workspaces export <name> <file>` | admin: every record kind copied (as `db sync` does) into a new SQLite file with a manifest; secrets are listed by reference, never copied |
  | `import_workspace` (`file`, `name`) | `udl workspaces import` | admin: into a new workspace |

- Tests first: an interface test and a `MATRIX` row per operation; export then import round-trips every kind's `example()`.
- Done when: the parity test lists them all.

**31.8 The isolation proof** · needs 31.7
- Build: `tests/interface/test_workspace_isolation.py`, the phase's first bullet, generated: for each record kind with an `example()`, store it in `a`; call every read-only operation in `b` (with each request model's minimal valid request) and assert the record's id and name appear in no response; then every `delete_*`, `set_*` and `get_*` naming it in `b` is `DoesNotExist`.
- Done when: it passes over `sqlite`, `postgres` and `mongodb`.

**Phase 31 is done when:** after `udl hub init` and `udl workspaces create acme`, `udl -W acme schedules set …` makes a schedule that `udl -W default schedules list` doesn't show, and a restarted `udl serve` fires both workspaces' schedules.

---

## Phase 32: People: users, sign-in, roles and tokens

### Tests first
- Passwords are stored only as argon2id; a wrong password and a wrong TOTP code get the same answer; ten failures in fifteen minutes lock the account for fifteen minutes (on the `FakeClock`); each recovery code works once.
- Passkeys: registration and sign-in against a software authenticator (`tests/support/webauthn.py`, built with `cryptography`, ES256 and Ed25519); a wrong origin, a wrong RP id and a signature counter going backwards are each refused.
- Permissions: one table-driven test over every (role, permission) pair and every operation's required permission; a member can't reach an admin operation; a guest reads only the projects it is a member of.
- Tokens are shown once, stored hashed, limited by workspace, scope and expiry, and stop working the moment they are revoked.
- Remote MCP: a client discovers the authorization server from a 401, registers, gets a code with PKCE (S256 only), exchanges it, calls a tool; a token for workspace `a` can't call in `b`; a reused refresh token revokes the grant.

### Tasks

**32.1 Users and memberships** · needs 31.2
- Build: `udl/people/records.py`, all scope `HUB`:
  - `User`: `handle` (unique, lowercase, `^[a-z0-9][a-z0-9_-]{1,38}$`), `display_name`, `email` (optional; unique when set; lowercased), `status: invited | active | locked | disabled`, `hub_admin: bool`, `default_workspace`.
  - `Membership`: `user`, `workspace`, `role: owner | admin | member | guest`; unique on (`user`, `workspace`). A workspace always keeps at least one owner: removing or demoting the last is refused.
  - `Invite`: `workspace`, `role`, `email`, `token_hash`, `expires_at` (7 days), `created_by`, `used_by`.
- Tests first: the record contract over the three kinds; `tests/unit/people/test_memberships.py` (the last owner).
- Done when: `User.example()` and the others round-trip in every database plugin.

**32.2 Passwords and TOTP** · needs 32.1
- Build: `udl/people/auth/passwords.py` (`argon2-cffi`'s `PasswordHasher` with its defaults, rehashing on sign-in when the parameters change), `udl/auth/totp.py` (RFC 6238: SHA-1, 6 digits, 30 s, one step either side, a step already used refused; **13.8 imports it from here** for web accounts' TOTP), recovery codes (ten, hashed, each used once).
  - `PasswordCredential` (`user`, `hash`) and `TotpCredential` (`user`, `secret: SecretRef` in the hub's secret store, `confirmed`). A TOTP secret is confirmed by a first good code.
  - Lockout: `[hub.auth] max_failures = 10`, `lockout = "15m"`, counted per user and per client address.
- Dependency: `argon2-cffi`.
- Tests first: `tests/unit/people/test_passwords.py`, `tests/unit/auth/test_totp.py` (RFC 6238's test vectors): the phase's first bullet.
- Done when: no code path stores or logs a password, a TOTP secret or a recovery code.

**32.3 Passkeys** · needs 32.1
- Build: `udl/people/auth/passkeys.py` on `webauthn` (py_webauthn).
  - `PasskeyCredential` (scope `HUB`): `user`, `credential_id`, `public_key` (COSE, base64), `sign_count`, `transports`, `name`, `last_used_at`.
  - The RP id is `public_url`'s host (`localhost` without one); the origin must be `public_url`'s. `user_verification = "required"`, no attestation.
  - Routes: `POST /auth/passkeys/register/options` and `…/register/verify` (signed in), `POST /auth/passkeys/sign-in/options` and `…/sign-in/verify`.
  - A user may have several passkeys; removing their last way to sign in is refused.
- Dependency: `webauthn`.
- Tests first: `tests/unit/people/test_passkeys.py` against `tests/support/webauthn.py`: the phase's second bullet.
- Done when: a Playwright test signs in with a passkey through Chromium's virtual authenticator (`WebAuthn.addVirtualAuthenticator` over CDP).

**32.4 Web sessions for people** · needs 32.2, 32.3, 24.1 (18.4)
- Build: on a hub, 18.4's in-memory `SessionStore` becomes `WebSession` records (scope `HUB`: `user`, `token_hash`, `expires_at` sliding 12 h and at most 30 days, `ip`, `user_agent`, `last_seen_at`), so sessions survive a restart and can be listed and revoked. A standalone node keeps 18.4's sign-in with the API key. 18.4's CSRF header and Origin check are unchanged.
  - Pages, hand-built in the shell: `/ui/sign-in` (handle or email, password and TOTP code, or a passkey), `/ui/invite/<token>` (accept: choose a handle, then a passkey or a password with TOTP), and an account page (passkeys, TOTP, recovery codes, sessions, tokens).
- Tests first: `tests/interface/web/test_people_sessions.py`; Playwright for sign-in, invite and sign-out, both themes.
- Done when: a revoked session is refused on its next request.

**32.5 Personal tokens and `udl login`** · needs 32.1
- Build: `AccessToken` (scope `HUB`): `user`, `name`, `prefix` (`udl_pat_` and 8 characters, shown in lists), `hash` (SHA-256 of the whole token: tokens carry 256 bits, so a fast hash is right), `workspaces: list[str] | None` (None: all the user's), `scopes: list[Literal["read", "write", "admin"]]`, `expires_at` (default 90 days; at most a year unless a hub admin makes it), `last_used_at`.
  - `udl login <hub-url> [--name N]`: the OAuth device authorization grant (RFC 8628) against the hub's own server (32.9): it prints the code and the link, the user approves in the browser, and the token lands in the local secret store under the hub's name.
- Tests first: `tests/unit/people/test_tokens.py`: the phase's fourth bullet.
- Done when: `udl login` against an in-process hub stores a token that `udl --hub` (32.6) uses.

**32.6 Principals, and the CLI against a hub** · needs 32.4, 32.5, 27.2
- Build: 27.2's `Principal` grows into `udl/people/principals.py`:
  - `kind: owner | user | agent | device | public | system`, `name`, `workspace`, `role` (a workspace role, or an agent's role), `scopes`, `on_behalf_of: Principal | None` (an agent's owner, a device's owner, the user who last saved an automation), `via: Caller`, `credential` (a token's prefix, a session's id, an OAuth client's id or a device's id: what the audit records).
  - `authenticate(request) -> Principal` tries a session cookie, then `Authorization: Bearer` (a personal token, an OAuth access token, an agent token), then `X-API-Key` (the standalone owner, or a hub's bootstrap admin). `current_principal` is set for every interface, the CLI included (on the hub's own machine, the CLI is the `owner`).
  - `RemoteService`: the same `call(operation, request)` as `UDLService`, over the hub's API with the stored token, so **every generated CLI command works against a hub**: `udl --hub <name> …` (and `[cli] hub` in the config). Output is identical.
- Tests first: `tests/interface/test_principals.py` grows to every credential kind; `tests/interface/cli/test_remote.py` runs the CLI interface suite through `RemoteService` against an in-process hub.
- Done when: the CLI's interface tests pass both in process and through `RemoteService`.

**32.7 Roles and permissions** · needs 32.6
- Build: `udl/people/permissions.py`.
  - `Permission`: `read`, `write`, `admin`, `hub_admin`, `audit.read`, `analytics.read`, `pages.publish`, and the decision permissions `decide.answer`, `decide.assign`, `decide.merge`, `decide.done`, `decide.accept_proposal`, `decide.grant`.
  - `ROLE_PERMISSIONS`: `owner` has all of a workspace's; `admin` all but deleting the workspace and changing owners; `member` has `read`, `write` and the decisions its projects give it; `guest` has `read` on the projects it is a member of.
  - `required_permission(op)`: `OperationHints.permission` when the operation sets one, else `admin` for an `admin` operation, `read` for a read-only one, `write` otherwise. `UDLService.call` checks it before validating the request. 27.2's role table stays the only place an **agent's** role is compared; an agent also needs its owner to hold the permission.
  - Projects: `ProjectMember` (workspace record: `project`, `user`, `role: maintainer | contributor | viewer`) and `Project.decisions: dict[DecisionKind, list[str]]` (which project roles may make each decision; default: maintainers, and the workspace's owners and admins), so who may decide what is data. orch's list operations show a guest only its projects.
- Tests first: `tests/unit/people/test_permissions.py`: the phase's third bullet, generated from the tables.
- Done when: `ROLE_PERMISSIONS` and `Project.decisions` are the only places people's roles are compared.

**32.8 The orchestrator learns about people** · needs 32.7, Phase 28
- Build:
  - `human:<name>` becomes `user:<handle>` everywhere orch names a person (assignees, `HumanAction.assignee`, authors). A migration maps `human:<name>` to the user with that handle, and leaves a `system` note on anything it couldn't map.
  - `Agent.owner: str` (a user's handle; required on a hub; existing agents go to the hub's first admin). Only the owner, and the people its owner's grant names (34.7), may assign to it, pause it or send it a message.
  - A question `to: humans` goes to the people who hold `decide.answer` on its project; `orch_inbox(for = "me")` gives each user their own: the questions they may answer, the merges they may make, the actions assigned to them.
- Tests first: `tests/integration/plugins/apps/orch/test_people.py`: assignment refused to someone the owner didn't grant; the inbox per user; the migration.
- Done when: 28.6's end-to-end test passes with two users, each seeing only their own inbox.

**32.9 Remote MCP authorization (OAuth 2.1)** · needs 32.4
- Build: `udl/people/oauth_server.py` (8.9 is udl as an OAuth client; this is udl as the server).
  - `/.well-known/oauth-protected-resource` and a per-path one for each `/w/{workspace}/mcp` (RFC 9728); `/.well-known/oauth-authorization-server` (RFC 8414).
  - `/oauth/register` (RFC 7591): public clients only; redirect URIs must be `https` or loopback. `OAuthClient` (scope `HUB`: `client_id`, `name`, `redirect_uris`, `created_at`). `[hub.oauth] registration = "open" | "admin_approval" | "closed"`.
  - `/oauth/authorize`: needs a signed-in session; the consent page shows the client's name, the redirect's host, a workspace picker (the user's) and the scopes; `code_challenge_method = S256` only; the `resource` parameter (RFC 8707) is required and must be one of udl's MCP URLs.
  - `/oauth/token`: `authorization_code`, `refresh_token` (rotated; a reused one revokes the whole grant), and the device code grant for 32.5. `/oauth/revoke`.
  - Access tokens are opaque (`udl_oat_…`, 1 h), refresh tokens 30 days; both stored hashed in `OAuthGrant` (`user`, `client`, `workspace`, `scopes`, `resource`).
  - `/mcp` without a credential answers 401 with `WWW-Authenticate: Bearer resource_metadata="<url>"`.
  - Check each endpoint against the MCP authorization spec current on the day it is built, and record any difference in the phase's decisions.
- Tests first: `tests/interface/test_oauth_server.py`: the phase's fifth bullet, with the MCP SDK's own client as the connector.
- Done when: Claude Code adds `https://<hub>/w/<ws>/mcp` as a remote MCP server, signs in through the browser, and calls `orch_list_tasks` (the live connectors in 39.9).

**32.10 Operations and pages** · needs 32.1–32.9
- Build: on the hub's registry: `get_me`, `list_users`, `get_user`, `invite_user`, `set_membership`, `remove_member`, `disable_user`, `list_my_sessions`, `revoke_session`, `create_token`, `list_tokens`, `revoke_token`, `list_oauth_grants`, `revoke_oauth_grant`, `list_oauth_clients`, `delete_oauth_client`. Setting a password, TOTP and passkeys are web and CLI flows only (`mcp=False`). CLI: `udl me`, `udl users …`, `udl tokens …`. The members and tokens pages are generated (Phase 24) and guided by `ui_field()`s.
- Tests first: an interface test and a `MATRIX` row per operation.
- Done when: the parity test lists them all.

**Phase 32 is done when:** on a hub, the admin invites a member; the member accepts, signs in with a passkey, makes a token, runs `udl --hub h orch tasks` from another machine, and connects Claude Code to `/w/<ws>/mcp` through OAuth.

---

## Phase 33: Audit: who did what, when

A company has to be able to trace anything back to a user or an agent (H-D10). From this phase on, every call through udl is attributed and recorded, in an append-only, hash-chained log per workspace (and one for the hub itself), and the decisions a project says need it are signed by the people who made them (H-D6).

### Tests first
- Generated over the registry: every call of an operation that isn't read-only writes exactly one audit entry, whether it succeeds, is refused or fails, through every interface (CLI, API, MCP, an automation's run, a device's link). Reads are recorded when `[audit] reads = true`, and always for operations marked `sensitive`.
- Attribution: an agent's call names the agent and its owner; an automation's run names its subscription, action or schedule and the user who last saved it; a device's push names the device and its owner; a call through an OAuth client names the client.
- Redaction (hypothesis over request models): no secret value ever reaches an entry.
- The chain: changing, removing, inserting or reordering any entry is found by `verify`, which names the first bad one; two processes writing at once never fork the chain.
- Decisions: one that needs a signature doesn't take effect without one; passkey and SSH signatures verify; a signature over another statement is refused; an agent can't sign.

### Tasks

**33.1 Audit entries and the writer** · needs 32.6
- Build: a `udl/audit/` package (`models.py`, `log.py`).
  - `AuditEntry(Record)`, kind `audit_entry`, on the primary connection only, never updated: `log` (the workspace, or `hub`), `seq: int` (unique per log), `at`, `actor` (the principal's kind and name), `on_behalf_of: list[str]` (the chain of principals it acted for, outermost last), `via` (`cli`, `api`, `mcp`, `web`, `automation`, `device`, `system`), `credential` (a token's prefix, a session's id, an OAuth client's id, a device's id), `origin` (client address, user agent, device), `action` (an operation's name, or one of udl's own: `auth.signed_in`, `auth.failed`, `auth.token_used`, `secret.resolved`, `device.linked`, `device.unlinked`, `export.made`), `target` (a kind and key, e.g. `task:udl/22.4`, and a URL when there is one), `outcome: ok | refused | failed` and the error's `code`, `request` (redacted, 33.2), `changed: list[str]` (the records it created or changed, as `udl:` URLs), `correlation` (the event, run or agent session that caused it), `prev_hash`, `hash`.
  - `AuditLog.append(entry)`: reads the head, fills `seq = head + 1`, `prev_hash` and `hash`, and inserts; a clash on `seq`'s uniqueness means another writer won, so it rereads and tries again. It works the same in every database plugin.
  - `UDLService.call` wraps every operation: the entry is written after the call, with its outcome. If it can't be written, it is tried again; if it still can't, the call's result stands, but the workspace refuses every further call that isn't read-only until its log can be written again, and says why (an unrecorded change must not be followed by more).
  - `OperationHints.sensitive: bool = False`, for reads that must be recorded: `orch_get_session` (a transcript), `export_*`, `list_audit`, and reading another user's sessions or tokens.
  - `[audit]`: `reads = false`, `max_field = 4096`, `retention_days = 365`.
- Tests first: `tests/unit/audit/test_log.py`; `tests/interface/test_audit_coverage.py`: the phase's first and second bullets.
- Done when: no change to a record happens without its entry, by the generated test.

**33.2 Redaction** · needs 33.1
- Build: `udl/audit/redact.py`: `redact(request: BaseModel) -> JsonObject`. Every `SecretStr`, `SecretRef` and field shown as a `password` widget becomes `"<secret>"`; a string longer than `max_field` becomes `{"sha256", "length", "head"}` (its first 256 characters); a file becomes its path and size; floats become strings (so 33.3's canonical form needs no number rules).
- Tests first: `tests/unit/audit/test_redact.py`: the phase's third bullet, as a hypothesis test over every request model in the registry.
- Done when: a test greps a full test run's audit entries for each secret the tests used and finds none.

**33.3 The chain and its checkpoints** · needs 33.1
- Build: `udl/audit/chain.py`, `udl/audit/jcs.py` (RFC 8785 canonical JSON over strings, integers, booleans, null, lists and objects; anything else is refused).
  - `hash = sha256(jcs(entry without hash))`. The first entry's `prev_hash` is the hash of the log's genesis (`{"log", "created_at", "hub_key"}`).
  - `AuditCheckpoint` (every 1,000 entries and at least hourly): `log`, `seq`, `hash`, `at`, and the hub key's ed25519 signature over `udl-audit-checkpoint\n<log>\n<seq>\n<hash>`.
  - The hub's signing key is made on first start, kept in the hub's secret store (`hub/audit/signing-key`), and published with every key it has had at `/.well-known/udl-audit-keys.json` (a rotated key stays listed, with when it stopped).
- Tests first: `tests/unit/audit/test_chain.py`: the phase's fourth bullet; `jcs.py` against RFC 8785's examples that fit its types.
- Done when: an entry changed by hand in SQLite is found at its `seq`.

**33.4 Signed decisions** · needs 33.3, 32.3, 32.7
- Build: `udl/audit/decisions.py`, `udl/audit/sshsig.py`.
  - `DecisionKind`: `answer`, `merge`, `done`, `accept_proposal`, `grant`, `membership`, `publish`. Each project says which need a signature: `Project.sign: dict[DecisionKind, Literal["none", "optional", "required"]]` (default `none`); a workspace setting does the same for workspace-level kinds.
  - A decision's operations (`orch_answer`, `orch_merge`, `orch_move_task` to `done`, `kb_accept_proposal`, `set_device_grant`, `set_membership`, `pages_publish`) take an optional `signature: DecisionSignature`. Without one where it is `required`, they raise `SignatureRequired` (HTTP 428) carrying a **statement**: `{kind, log, target, choice, request_digest, nonce, expires_at}` and its hash. The caller signs the hash and calls again with the same request and the signature; a nonce works once, for five minutes.
  - **Passkey:** a WebAuthn assertion whose challenge is the statement's hash, verified with the user's passkey; `authenticatorData`, `clientDataJSON`, `signature` and the credential id are kept on the entry.
  - **SSH:** `SshSigningKey` (scope `HUB`: `user`, the OpenSSH public key, `fingerprint`, `title`, `revoked_at`), registered by signing a challenge. A signature is SSHSIG (`ssh-keygen -Y sign -n udl-decision`), verified in Python (`sshsig.py` parses the format and checks the namespace and hash with `cryptography`: `ssh-ed25519`, `ecdsa-sha2-nistp256`, `rsa-sha2-512`; never SHA-1). The CLI, on `SignatureRequired`, runs `ssh-keygen -Y sign -n udl-decision -f <public key>` with the statement on stdin (which uses the SSH agent, the 1Password agent included); `[sign] ssh_key` names the key.
  - **Agents never sign.** A decision that needs a signature can only be made by a person; a manager agent's answer (27.8) to such a question waits as a draft until a person countersigns it.
- Tests first: `tests/unit/audit/test_decisions.py`, `test_sshsig.py` (signatures made by `ssh-keygen` in the test, and ones made by hand with each key type): the phase's fifth bullet.
- Done when: a merge with `merge: required` can't happen without a person's signature, through the API, the CLI or the web.

**33.5 Verifying and exporting** · needs 33.4
- Build: `udl audit verify [--log L] [--file F]`: recomputes every hash and link, checks each checkpoint's signature against the published keys, and each person's signature against their key as it was at the time (keys are never deleted, only revoked from a time on). It reports the first bad entry and why.
  - `export_audit` (`log`, `since`, `until`; `sensitive`, `audit.read`): JSON Lines with the checkpoints and keys needed, which `udl audit verify --file` checks with no hub.
- Tests first: `tests/unit/audit/test_verify.py`: each way of tampering, in a live log and in an export.
- Done when: an export verifies offline on another machine.

**33.6 Querying and tracing** · needs 33.1
- Build: `list_audit` (filters: `actor`, `on_behalf_of`, `action` glob, `target`, `outcome`, `since`, `until`, `correlation`; paged; `sensitive`), `get_audit_entry`, and `audit_trace` (`target` or a URL: a task, a PR, a doc): every entry that touched it, and the chain of causes back through `correlation` to the person who started it, e.g. "merged by daniel (passkey) ← `orch_ready` by ernie-a (owned by daniel) ← assigned by daniel". Permission `audit.read`; members see the entries about themselves and their own agents. Audit entries are never events (no loops).
  - CLI: `udl audit list`, `udl audit show <seq>`, `udl audit trace <target|url>`.
- Tests first: an interface test and a `MATRIX` row per operation; `tests/unit/audit/test_trace.py` on a recorded story.
- Done when: `udl audit trace <a merged PR's URL>` names who assigned its task.

**33.7 Retention** · needs 33.5
- Build: once a day, entries older than `retention_days` (0: kept for ever) are removed **after** a checkpoint at the cut is written and, if `[audit] archive_dir` is set, the removed range is exported there. The chain verifies from that checkpoint on.
- Tests first: `tests/unit/audit/test_retention.py` on a `FakeClock`.
- Done when: a log cut by retention still verifies, and so does the archive.

**33.8 The audit in the UI** · needs 33.6, 24.6
- Build: an Audit page (filters as `list_audit`'s, and a trace view), a History tab on the task and feature pages (who did what, when, with a badge for a signed decision), and the signing dialog (a passkey, or "sign with your SSH key" showing the CLI command).
- Tests first: Vitest against `FakeUdl`; Playwright: a signed merge through the virtual authenticator, both themes.
- Done when: a task's History tab reads as its story, top to bottom.

**Phase 33 is done when:** a PR an agent made and a person merged with a passkey shows, in `udl audit trace <PR URL>`, everything back to who assigned the task; `udl audit verify` passes; and editing one entry in the database makes it fail at that entry.

---

## Phase 34: Devices and the client

O-D11's "udl clients, later" comes forward (H-D3, H-D4): every machine runs udl, linked to one or more hubs. The client is the machine's safe way to and from each hub: it runs what a hub may run there, pushes up what it may push, and keeps its own record of both.

### Tests first
- Enrolment: a device belongs to the user who approved it; an enrolment token makes it the workspace's (managed); a device that can't sign the hub's challenge is refused; a revoked device is cut off within a second.
- Two ceilings (hypothesis over random grants and calls): the device refuses anything outside its own config, whatever a hub grants or asks; the hub refuses anything outside its grants before sending it.
- Transports: one protocol suite passes over `wss`, SSH stdio and a direct connection; a dropped connection reconnects with backoff, and a pushed event is neither lost nor repeated.
- Several hubs: a device linked to two hubs, as two identities, with different shares and directions; each hub sees only what its link shares, and what is pushed to one never reaches the other.
- `ClientShell` passes the `HostShell` contract (25.6), so a runner on a device works as it does on `local`.

### Tasks

**34.1 A device's identity** · needs Phase 30
- Build: `udl/node/identity.py`: on first run, an ed25519 key pair; the private key in the local secret store (`node/identity/key`); the public key and `device_id` (base32 of the first 20 bytes of the public key's SHA-256) in `~/.local/share/udl/node.json`. `udl device show` prints the id, the fingerprint and the name (the hostname, unless set). `udl device rotate-key` makes a new key and has the old one sign it, so every linked hub can check it is the same device.
- Tests first: `tests/unit/node/test_identity.py`.
- Done when: the id is stable across restarts and the private key is in no file but the secret store's.

**34.2 Devices on the hub** · needs 34.1, 32.1
- Build: `Device` (scope `HUB`): `device_id` (unique), `public_key`, `name`, `owner: str | None` (a user's handle) or `managed_by: str | None` (a workspace), exactly one of them; `platform` (OS, architecture, udl version), `status: pending | active | revoked`, `last_seen_at`, `transports` (those it last used), `capacity` (`max_sessions` and the like, set by its owner). Only the owner transfers a device (a workspace admin, for a managed one).
- Tests first: the record contract; `tests/unit/node/test_devices.py` (one of `owner` or `managed_by`).
- Done when: `Device.example()` round-trips everywhere.

**34.3 Enrolling a device** · needs 34.2, 32.4
- Build: `udl hubs add <name> <url> [--token T]` on the device:
  1. It posts its public key and platform to `POST /hub/devices/enrol` and gets a `user_code` and a `verification_uri` (as RFC 8628).
  2. The user opens the link, signs in, sees the device's name, fingerprint and platform, picks the workspaces it serves, and approves. The device is theirs.
  3. The client, polling, gets the hub's id and key. From then on it proves itself by signing a fresh challenge each time it connects; it holds no long-lived bearer secret for the link.
  - `--token`: an enrolment token a workspace admin made (`create_enrolment_token`, admin, one use, 24 h) skips step 2; the device is managed by that workspace.
- Tests first: `tests/integration/node/test_enrolment.py`: the phase's first bullet.
- Done when: a device enrolled to an in-process hub shows as its approver's.

**34.4 Links and the ceiling** · needs 34.3
- Build: `[[hubs]]` in the device's config (**file-only**: this is the ceiling, H-C5):
  - `name`, `url`, `transport: wss | ssh | direct`, `identity` (the user's token, by name in the local store, used for the user's own `udl --hub` calls).
  - `allow`: `accept` (operation globs it runs when the hub asks; default none but `node_*`), `runners` (which harnesses agents may use), `shell` (program names a `ClientShell` may start; default `git`, `gh` and the allowed runners' binaries), `paths` (folders for worktrees, downloads and memory files), `integrations` (accounts on this device whose data may go up, e.g. `gmail:work`), `push` (event-type globs it pushes), `max_sessions`.
  - `udl hubs list | show | add | rm | status` (`status`: connected, since when, what is in flight, the outbox's length).
- Tests first: `tests/unit/node/test_links.py`: a call outside `allow` is refused on the device, naming the setting.
- Done when: a link's ceiling can only be changed by editing the device's own config file.

**34.5 The protocol** · needs 34.1
- Build: `udl/node/protocol.py`: JSON-RPC 2.0 messages as Pydantic models.
  - `Hello {protocol: 1, device_id, nonce, signature, udl_version}` / `Welcome {hub_id, session, grants_digest}` (the hub signs its nonce too, so both ends know who they talk to).
  - `Call {id, op, request, principal, workspace, deadline}` → `Result` or `Error {ErrorInfo}`.
  - `Push {events, cursor}` → `Ack {cursor}`.
  - `StreamOpen / StreamData / StreamClose` (for `HostShell` streams and interactions, stdin frames included), `Ping / Pong`, `Grants {digest, grants}`.
  - Limits: 4 MiB a frame (streams in chunks), 64 calls in flight a link, a deadline on every call. A hub refuses a protocol version it doesn't speak, with a hint to update udl.
- Tests first: `tests/unit/node/test_protocol.py`: every message round-trips; oversize frames and expired deadlines are refused.
- Done when: the models are the only place the wire format is written.

**34.6 Transports** · needs 34.5, 25.6
- Build: `udl/node/transports/`:
  - `WssTransport`: the device dials `wss://<hub>/hub/link`; reconnects with jittered backoff up to 5 minutes; honours `HTTPS_PROXY`. (`websockets`, which 15.4 also uses.)
  - `SshTransport`: the hub runs `ssh <host> -- udl node link --stdio` and speaks over its stdio, reusing `SshShell`'s argv and quoting.
  - `DirectTransport`: the hub dials the device's own `udl serve` at an address on a private network (a Tailscale or WireGuard name); both ends prove themselves in `Hello`.
- Tests first: `tests/contract/test_link_transport_contract.py` over all three: the phase's third bullet.
- Done when: the contract passes over each transport.

**34.7 Grants** · needs 34.4, 32.7
- Build: `DeviceGrant` (scope `HUB`): `device`, `grantee` (`user:<handle>`, `workspace:<name>` or `role:<workspace>/<role>`), `runners`, `operations`, `paths`, `integrations`, `push`, `max_sessions`, `expires_at`.
  - Only the device's owner (for a managed device, its workspace's admins) sets them: `set_device_grant` and `remove_device_grant` (admin; decision kind `grant`, 33.4).
  - The hub checks every call against the grants before sending it; the device checks it against its ceiling when it arrives. **The device's own audit log** (Phase 33, on the device's standalone node) records each call a hub made on it, so an owner can see what every hub did on their machine.
- Tests first: `tests/integration/node/test_grants.py`: the phase's second bullet.
- Done when: neither a grant nor a hub can make a device do what its ceiling doesn't allow.

**34.8 `ClientShell`** · needs 34.6, 34.7
- Build: `udl/hosts/` gains `transport = "client"` and `Host.device`. `ClientShell` implements `run`, `stream`, `interact` and `forward` as calls to the device's `node_shell_*` handlers, which run through its `LocalShell` with `cwd` checked against `paths` and `argv[0]` against `shell`. Runners and orch don't change (25.6's rule that nothing outside `udl/hosts/` knows a host's transport holds). Each active device the caller may use appears in `list_hosts` as `device:<name>`.
- Tests first: the `HostShell` contract over `ClientShell` against an in-process device: the phase's fifth bullet.
- Done when: a `FakeRunner` agent's session runs on a device exactly as on `local`.

**34.9 Running an operation on a device** · needs 34.8, 31.4
- Build: an operation marked `local` takes `on: <device>` from the interface, not the request: `--on` in the CLI, `X-UDL-On` in the API, and an `on` argument the MCP adapter adds to local operations' tools. The hub sends the call over the link; the device runs it in its own service (its own databases and secret store) as the calling principal's grant allows; the result comes back. Files stay on the device; paths in the result are the device's.
- Tests first: `tests/integration/node/test_on_device.py`: a download and a fetch on a device, through each interface.
- Done when: `udl --hub h --on mac get <url>` leaves the file on the Mac and records the job there.

**34.10 Data up** · needs 34.7, 11.5
- Build: a device's watchers (its own accounts' integrations) publish into its own bus as today; events matching a link's `push` (within its grants) are copied into an `Outbox` record kind on the device, sent as `Push`, and removed when acked. The hub publishes them into the workspace's bus with `source = device:<id>` and their original dedupe keys, so a resend never fires twice. Dataset items (36.1) go up the same way. Credentials never do (H-C6).
- Tests first: `tests/integration/node/test_push.py`: the phase's third and fourth bullets (a link dropped mid-push).
- Done when: a device's `FakeIntegration` event fires a subscription on the hub exactly once across a reconnect.

**34.11 Agents through the client** · needs 34.8, 27.7
- Build: an agent session on a device gets its MCP server from the device: `udl mcp --hub <name> --agent <agent>` (stdio), which forwards the agent's calls over the link. The agent's token stays on the hub: the device is given a short-lived session token bound to (agent, task, device), 12 h. The dispatcher (27.7) writes this MCP config for runners on `client` hosts, in place of the hub's URL and the agent's token.
- Tests first: `tests/integration/node/test_agent_mcp.py`: an agent on a device calls `orch_my_task`; its session token fails for another task and after it expires.
- Done when: an agent on a device needs no network path to the hub of its own.

**34.12 Placement** · needs 34.11
- Build: `Agent.host` may be one host, a list, or `any` (any device its owner has granted it). The dispatcher places a session on the first eligible host: active, its runner signed in (`orch_list_runners`), under the device's and the grant's `max_sessions`, not `limited_until`; `orch.agent.placed` names where. A device view (in 29.9's Machines page) shows the sessions on each device and its limits.
- Tests first: `tests/unit/plugins/apps/orch/test_placement.py`: worked examples, including a limited login on one device sending the next session to another.
- Done when: two devices share one agent's work by their limits, on fakes.

**34.13 Managing devices** · needs 34.7
- Build: hub operations `list_devices`, `get_device`, `rename_device`, `transfer_device`, `revoke_device` (destructive; `confirm` = its name; the link closes within a second), `create_enrolment_token`, `set_device_grant`, `remove_device_grant`, `update_device_udl` (admin, the owner only; a fixed argv on the device: `uv tool upgrade udl`, then the service restarts); on the device, `node_status`. CLI: `udl devices …` (hub side), `udl hubs …` (device side). Generated pages, guided by the manifest.
- Tests first: an interface test and a `MATRIX` row per operation.
- Done when: the parity test lists them all.

**34.14 `udl service install`, pulled forward** · needs nothing
- Build: 21.3's card as written (a systemd user unit), extended in 35.1 with a launchd agent for macOS, so a device's links stay up. Tick 21.3 in its own tracker.
- Done when: 21.3's "done when" holds.

**34.15 End to end** · needs 34.1–34.14
- Build: `tests/integration/node/test_hub_and_client.py`: an in-process hub and two devices (separate data dirs) over real localhost WebSockets: one enrolled as a user's, one as managed; grants set; a `FakeRunner` agent on the first takes a task to `ready` through `ClientShell`; the second pushes a `FakeIntegration` event that a hub subscription turns into a task; the first is revoked mid-turn: its session stops and the task is `blocked` saying why.
- Done when: it passes three times running, and with random ordering.

**Phase 34 is done when:** thevault, enrolled over `wss` to a hub on jeff-wiggle, runs an agent task, and jeff-wiggle's own client, linked to two hubs as two identities, shows each only what its link shares.

---

## Phase 35: macOS, the `codex` runner, and trying udl alone

A teammate works on a Mac with Codex; anyone should be able to try udl on their own machine, with no hub, and have their agent write plugins for it.

### Tests first
- `make check` passes on macOS in CI. A test that can't run there skips through its `Requirement`, never through an OS check in the test.
- The `codex` runner passes the runner contract over a fake `codex` that speaks its JSON Lines; the argv for a first and a later turn are exact; the MCP token is never in `argv`.

### Tasks

**35.1 macOS** · needs 34.14
- Build:
  - CI: a `check-macos` job on `macos-latest` running `make check` (it touches `.github/workflows/`: read in full by whoever merges it).
  - The `notify` action on macOS runs `osascript -e 'on run argv' -e 'display notification (item 1 of argv) with title (item 2 of argv)' -e 'end run' <text> <title>`: the text is an argument, never part of the script.
  - The keyring store uses the macOS Keychain (the `keyring` package's backend); udl's paths stay `~/.config/udl` and `~/.local/share/udl` on macOS too (documented).
  - `udl service install` on macOS writes `~/Library/LaunchAgents/dev.udl.serve.plist` and loads it with `launchctl bootstrap gui/<uid>`.
  - `Requirement` hints say `brew install …` on macOS.
- Tests first: `tests/unit/plugins/actions/test_notify_macos.py` (the argv), `tests/unit/test_service_install.py` (the plist).
- Done when: the macOS job is green and required in `main`'s ruleset.

**35.2 The `codex` runner** · needs 25.7, 25.11
- Build: `udl/plugins/runners/codex.py`, through the runner's `HostShell` like the others.
  - A turn is one process: `codex exec --json --model <model> --cd <worktree> --sandbox workspace-write --ask-for-approval never --skip-git-repo-check` with `-c` overrides that add udl's MCP server (`mcp_servers.udl.command` and `args`, the token in its `env`), and the prompt on stdin (`-`). Later turns: `codex exec resume <session id> --json …`.
  - Instructions: the fixed rules, the project's and the agent's are written to `.udl/instructions.md` in the worktree (git-ignored through `info/exclude`, `0600`) and given through the configuration key the installed Codex reads for extra instructions; if it has none, the first prompt starts with them. Codex also reads the repo's `AGENTS.md` itself.
  - Events: `thread.started` gives the session id; `item.*` events map to `TextDelta` (agent messages), `ToolStarted` / `ToolFinished` (commands, file changes, MCP calls); `turn.completed` gives `Usage` and `TurnEnded(done)`; `turn.failed` and `error` give `RunnerFailed`, or `RunnerLimited` with its reset time for a usage limit. Unknown lines are skipped.
  - Guards: `workspace-write` keeps writes in the worktree and the network off unless the project allows it (`network_access`); `git push` and `gh pr merge` are denied through Codex's command rules where the installed version has them; `health().guards` says which.
  - `health()`: `codex --version` and `codex login status`. **udl never opens Codex's credential file** (H-C14, O-C3).
  - 25.11's table gains a column: install / update with a fixed argv per platform (`npm install -g @openai/codex@<version>` or `brew upgrade codex`, chosen by `[runners.codex] install`); sign-in relayed through `codex login --device-auth`, or an API key given on stdin with `codex login --with-api-key` from udl's secret store; sign-out `codex logout`.
  - These flags were written from Codex's documentation without a local install: confirm each against `codex --help` and `codex exec --help` on the build machine, and record any change in the phase's decisions.
- Tests first: the runner contract over `tests/support/fake_codex.py`; `tests/unit/plugins/runners/test_codex.py`: the phase's second bullet, the instructions file, a usage limit.
- Done when: an opt-in smoke test has `codex` answer one prompt in a temp directory through the runner.

**35.3 Trying udl alone** · needs 35.1, 35.2
- Build: `docs/quickstart.md`: install on macOS or Linux (uv, Python 3.14); `udl serve`; udl as an MCP server for Claude Code, Claude Desktop and Codex (`codex mcp add udl -- udl mcp`), and for ChatGPT once a hub with a public URL exists (32.9); a project imported from a markdown plan (26.7) and a `codex` agent taking a task.
  - `udl doctor`: Python, uv, every plugin's `Requirement`s, the browser, each harness (installed, signed in), and what to install for this platform.
- Tests first: `tests/unit/test_doctor.py` against fake tools on `PATH`.
- Done when: the quickstart, followed on a clean macOS account, ends with a task at `ready`.

**35.4 Plugins written by agents** · needs 35.3
- Build: `udl plugins new <kind> <name> [--dir D]`: a local plugin from a template per kind (`integration`, `action`, `app`, `runner`, `site`, `downloader`, and `browser-app` from Phase 40), with its contract test already wired and failing (red first), and a checklist: docstrings written for agents, hints, `ui_field()`s, secrets only through `SecretRef`. `docs/plugins.md` gains "Writing a plugin with an AI agent": what to tell Claude or Codex, and how to run the contract suite.
- Tests first: `tests/unit/test_plugin_templates.py`: each template's test fails as generated and passes once the template's example body is filled in.
- Done when: an agent, given only the generated folder and the guide, makes a plugin that passes its contract suite.

**Phase 35 is done when:** on a Mac, following the quickstart from a clean user account, a Codex agent takes a fixture task to `ready`, and `udl plugins new integration demo` plus an agent gives a plugin that passes its contract suite.

---

## Phase 36: The data bank: datasets, docs, memories, search and sources

"udl is where a lot of AI can communicate on the same data." This phase gives that data its shapes: datasets anything can fill, a wiki, memories every agent reads, search over all of it, and conversations turned into plans.

### Tests first
- Search: the full-text contract returns the same hits, in the same order, in every database plugin; a workspace's search never returns another's records.
- Memory sync (hypothesis over sequences of edits on both sides): every interleaving merges or raises a question, and never loses an edit; `MEMORY.md` stays a correct index.
- Datasets: items validate against the dataset's schema when it has one; putting the same key twice updates the item, never duplicates it.
- Proposals: nothing proposed takes effect until a person with the permission accepts it, and each accepted record links back to its source's span.

### Tasks

**36.1 Datasets** · needs 31.2, 9.2
- Build: `udl/kb/datasets.py`.
  - `Dataset`: `name`, `title`, `description`, `schema: JsonObject | None` (JSON Schema 2020-12, checked with `jsonschema`), `key_field: str | None` (the item field that identifies an item, for upserts), `owner`, `visibility: workspace | project:<name>`, `retention_days`.
  - `DataItem`: `dataset`, `key`, `data: JsonObject`, `source` (the plugin, agent, device or user that put it), indexed by (`dataset`, `key`).
  - Operations: `data_list_datasets`, `data_get_dataset`, `data_set_dataset` (admin), `data_put` (one or many; upsert by key; idempotent), `data_query` (a 9.2 `Filter` over the items' data, `text`, order, page), `data_get`, `data_delete` (destructive). Filters run in Python over the dataset's records (fine to about 100,000 items a dataset; pushing them into each database is for later).
  - Events `data.item.added` and `data.item.changed`, so a subscription can react to a new row. `data_put` is an operation, so `operation` actions and AI tools can put data in.
- Dependency: `jsonschema`.
- Tests first: `tests/unit/kb/test_datasets.py`: the phase's third bullet; interface tests and `MATRIX` rows.
- Done when: an `operation` action puts an event's data into a dataset, and `data_query` finds it.

**36.2 Full-text search** · needs 36.1
- Build:
  - `DatabasePlugin.search(kind, text, *, where, limit) -> list[SearchHit]` (`id`, `score`, `snippet`), with a contract suite: SQLite FTS5 (an external-content table per searchable kind, kept in step on every write), PostgreSQL (a `tsvector('simple', …)` column and a GIN index), MongoDB (a text index), `memory` (casefolded tokens, BM25-like).
  - A kind is searchable by declaring `searchable: ClassVar[tuple[str, ...]]` (its text fields): docs, memories, tasks, features, notes, questions, data items, sources.
  - `kb_search` (`text`, `kinds`, `project`, `limit`): each kind's hits, merged by score normalised per kind, each with its `udl:` URL, so an agent can follow it.
- Tests first: `tests/contract/test_search_contract.py`: the phase's first bullet.
- Done when: `kb_search` gives the same answer on `sqlite`, `postgres`, `mongodb` and `memory`.

**36.3 Semantic search (optional)** · needs 36.2, 25.2
- Build: `AIProvider.embed(texts) -> list[list[float]]`, an optional capability (`openai-compatible` and `openrouter` call `/embeddings`; `ollama` does once 17.2 lands). `Embedding` records (`owner`, chunk index, model, `vector`, the text's hash); text cut into chunks of about 800 tokens at headings. `[kb] embeddings = {provider, model}` per workspace. `kb_search(mode = "hybrid")` merges the full-text hits with the nearest chunks by cosine (brute force within the workspace) by reciprocal-rank fusion. A changed record is embedded again, at most once a minute.
- Tests first: `tests/unit/kb/test_semantic.py` with `FakeAI` returning fixed vectors.
- Done when: a search for a word that isn't in a doc finds it by meaning, on the fake.

**36.4 Docs** · needs 36.2
- Build: `Doc` (`space` (a project or `wiki`), `path` (e.g. `guides/setup`), `title`, `body` (markdown), `tags`, `status: draft | published`, `updated_by`) and `DocRevision` (append-only: `doc`, `rev`, `body`, `author`, `message`). Operations: `kb_list_docs`, `kb_get_doc` (at a revision), `kb_set_doc` (with `base_rev`: a stale base is `Conflict`, with a three-way diff in the hint), `kb_move_doc`, `kb_delete_doc`, `kb_doc_history`, `kb_diff`. Links in a doc's text become `Link`s (26.4's scanner).
- Tests first: `tests/unit/kb/test_docs.py`; interface tests and `MATRIX` rows.
- Done when: two agents editing one doc from the same revision can't overwrite each other.

**36.5 Memories** · needs 36.2
- Build: `Memory` (`name`, a slug unique in its scope; `description`; `type: user | feedback | project | reference`; `body`; `scope: workspace | project:<p> | user:<h> | agent:<a>`; `pinned`, given to every agent in scope; `links`, the `[[name]]`s in the body) and `MemoryRevision` (append-only). Operations for agents and people: `kb_recall` (`query`, `scope`: the pinned memories in scope, then the best search hits), `kb_remember` (create or update by name), `kb_forget`. `orch_my_task` (27.3) adds the memories that apply (pinned in the project's, the agent's and its owner's scopes, and the five best hits for the task's title and spec), in a fenced block.
- Tests first: `tests/unit/kb/test_memories.py`.
- Done when: an agent's first `orch_my_task` carries its project's pinned memories.

**36.6 Two-way memory sync** · needs 36.5, 34.9, 27.5
- Build: `udl/kb/sync.py`, its core pure.
  - `MemorySource`: `name`; `format: claude-memory | agents-md | markdown-dir`; `location` (`device:<id>:<path>`, or `repo:<repo>:<path>`); `scope` (what the memories it brings in are scoped to); `direction: both | in | out` (default `both`, H-D7); `interval`.
  - Formats: **`claude-memory`**: a folder of `<name>.md` files with frontmatter (`name`, `description`, `metadata.type`) and a `MEMORY.md` index, one line a memory, rewritten by udl; **`agents-md`**: one file (`AGENTS.md`, `CLAUDE.md`) whose udl part sits between `<!-- udl:memories:start -->` and `<!-- udl:memories:end -->`, a `### <name>` heading a memory; nothing outside the markers is ever touched; **`markdown-dir`**: files with frontmatter and no index.
  - The merge, per memory, between the last synced version (`MemorySyncState`: its hash and body), the file and udl: unchanged on both sides, nothing; changed on one side, copied to the other; changed on both, a line-based three-way merge (diff3 on `difflib`), applied when clean; otherwise **a conflict**: neither side is overwritten, and a question (28.1) goes to the memory's owner with both versions (options: keep the file's, keep udl's, or write a merged one). A file deleted where udl's copy is unchanged deletes the memory; otherwise it is a conflict. A new memory on either side is made on the other.
  - Files on a device are read and written through the client (`node_memory_read` / `node_memory_write`, under the link's `paths`). Files in a repo change **only through a PR** that udl opens on a `udl-memories/<source>` branch (never pushing to a base branch, O-C8), and what merges into the base comes back on the GitHub watcher's `github.pr.merged`.
  - The first source is Claude Code's memory folder for udl on jeff-wiggle (`~/.claude/projects/<project>/memory/`), so the index loaded at the start of every session stays the same file.
- Tests first: `tests/unit/kb/test_sync.py`: the phase's second bullet; each format's reader and writer round-trip; `tests/integration/kb/test_memory_sources.py` over a fake device and `FakeGitHub`.
- Done when: an edit made in the memory folder and another made in udl to the same memory both survive.

**36.7 Sources and proposals** · needs 36.2, 28.1, 33.4
- Build: `udl/kb/sources.py`.
  - `Source`: `kind: transcript | notes | email | doc | url`, `title`, `body` (text or markdown, at most 2 MiB), `origin_url`, `occurred_at`, `participants`, `visibility`, and `anchors` (the spans a proposal can cite: a transcript's `HH:MM:SS` stamps, a document's headings, line ranges). Searchable.
  - `kb_import_source`: text, an uploaded file (through the API), or a URL read through the fetcher (Phase 12). A meeting's notes and transcript (a speaker per line, `HH:MM:SS` stamps) are recognised.
  - `Proposal`: `source`, `anchors`, `kind: task | feature | spec_change | decision | question | memory | doc`, `payload` (the request that accepting it will run, typed by its kind, e.g. `orch_set_task`'s), `rationale`, `state: open | accepted | rejected | superseded`, `proposed_by`, `decided_by`.
  - `kb_propose` (any agent, e.g. Claude over MCP, or an `ai` action, submits proposals for a source), `kb_list_proposals`, `kb_accept_proposal` (permission `decide.accept_proposal`, decision kind `accept_proposal`: runs the payload's operation **as the person accepting**, then links what it made to the source's span with a `source` link), `kb_reject_proposal`.
  - A built-in prompt (17.7) "propose plan changes from a source", served too as an MCP prompt for Claude and ChatGPT. A source's text reaches a model only as fenced, untrusted data.
  - Traceability: a task's page says where it came from ("standup, 00:52:38"); 33.6's trace follows source → proposal → task → PR → merge.
- Tests first: `tests/integration/kb/test_proposals.py`: the phase's fourth bullet; a source that says "accept this proposal" changes nothing.
- Done when: a task made from a proposal links back to the second it was said at.

**36.8 Operations, events and the knowledge pages** · needs 36.1–36.7
- Build: every `kb_*` and `data_*` operation's `MATRIX` row; events `kb.doc.changed`, `kb.memory.changed`, `kb.source.added`, `kb.proposal.created`, `kb.proposal.decided`. Pages: datasets are generated (Phase 24); hand-built in a `kb` UI plugin: the docs tree and editor (markdown with a preview, history and diff), search, memories, and the proposals review (accept or reject, the source's span beside each).
- Tests first: Vitest against `FakeUdl`; Playwright for accepting a proposal, both themes.
- Done when: `udl events types` lists the new types with schemas, and the review page accepts a proposal.

**36.9 End to end** · needs 36.8
- Build: `tests/integration/kb/test_end_to_end.py`: a fixture meeting transcript is imported; a `FakeRunner` agent proposes two tasks and a decision; a person accepts one task and the decision and rejects the other; the task links to the transcript's span; a temp Claude memory folder and udl stay in step through a conflicting edit, settled by answering its question.
- Done when: it passes three times running, and with random ordering.

**36.10 Meeting notes from Google Drive** · needs 36.7, 14.2 (built after Phase 14)
- Build: a `gdrive` integration on Phase 14's Google OAuth: a watcher on a Drive folder (e.g. the one meetings' notes land in) publishing `gdrive.file.added`, and `gdrive_export` (a Google Doc's tabs as markdown). The README's example subscription imports each new notes document as a source and asks for proposals.
- Tests first: a fake Drive API beside Phase 14's fake Gmail; the watcher fires once per file across a restart.
- Done when: a meeting's notes land as a source with proposals, with nobody copying them.

**Phase 36 is done when:** the user's Claude memory folder for udl syncs both ways with workspace `default` on jeff-wiggle, and the 18 Nov 2026 conversation, imported as a source, gives proposals that, once accepted, are tasks linked back to their timestamps.

---

## Phase 37: Pages: dashboards and public pages

"Pages that are publicly available, read not write, generated from the data we all have, that take ten minutes to spin up: almost like an AI's artifacts, but our data on our system."

### Tests first
- A public page reads only its views, cut to the fields they name; the visitor's request runs no operation.
- An HTML page's script can't reach the API, any other origin or the page around it: `fetch`, XHR, WebSockets, form posts, top-level navigation and messages to anything but its data channel all fail (Playwright), kept as regression tests.
- A Markdown page shows hostile markdown or HTML in its data as text.

### Tasks

**37.1 Views** · needs 31.2, 32.7
- Build: `View`: `name`, `title`, `operation` (read-only and not `sensitive`, checked when saved), `request` (JSON, which may use declared `params`), `fields: list[str]` (the projection; required for a view a public page uses), `max_age` (how long a result is cached), `owner`. For a private or workspace page, a view runs as the viewer; for a public page, the hub runs it as the page's publisher, keeps the result for `max_age`, and serves only the named fields (H-C13). Operations: `views_list`, `views_get`, `views_set`, `views_delete`, `views_run`.
- Tests first: `tests/unit/pages/test_views.py`: a view over a write operation or a `sensitive` one is refused; the projection.
- Done when: a view's result for a public page never holds a field it didn't name.

**37.2 Pages** · needs 37.1
- Build: `Page`: `slug` (unique in the workspace), `title`, `format: markdown | html`, `body`, `views`, `visibility: private | workspace | public`, `owner`, `theme: light | dark | auto`, `indexable: bool = False`; `PageRevision` (append-only). Operations: `pages_list`, `pages_get`, `pages_set` (agents may write pages), `pages_preview` (rendered for the caller, with live data), `pages_publish` (permission `pages.publish`, decision kind `publish`), `pages_unpublish`, `pages_delete`.
- Tests first: interface tests and `MATRIX` rows.
- Done when: an agent can draft a page over MCP but only a person with `pages.publish` makes it public.

**37.3 Markdown pages** · needs 37.2
- Build: `udl/pages/markdown.py`: rendered on the server (`markdown-it-py`, then `nh3` to sanitise) into static HTML with **no script**. Components as fenced blocks: `udl-table` (a view's rows), `udl-list`, `udl-stat` (a count, sum or latest value of a view's field), `udl-chart` (bar and line charts drawn as SVG on the server), `udl-tasks` (a task list with states and links).
- Dependencies: `markdown-it-py`, `nh3`.
- Tests first: `tests/unit/pages/test_markdown.py`: the phase's third bullet; each component.
- Done when: every component renders the same in both themes.

**37.4 HTML pages in a sandbox** · needs 37.2
- Build: an HTML page is shown in an `<iframe sandbox="allow-scripts">` (never `allow-same-origin`, `allow-forms`, `allow-popups` or `allow-top-navigation`), from `[hub] pages_origin` when set (a separate origin, e.g. `pages.<domain>`) or as `srcdoc` (an opaque origin) otherwise. Its CSP: `default-src 'none'; script-src 'unsafe-inline' <allow-listed CDN URLs, pinned>; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; frame-ancestors <the hub's origin>`. The host page posts `{type: "udl:data", views: {…}}`, and a shim injected first gives the page `window.udl.data` and `window.udl.onData()`.
- Tests first: `web/e2e/pages-sandbox.spec.ts`: the phase's second bullet.
- Done when: a page written to steal data and call the API does neither.

**37.5 Serving pages** · needs 37.3, 37.4
- Build: public pages at `/pages/<workspace>/<slug>` (no sign-in; `Cache-Control: public, max-age=60`; a rate limit per address; `X-Robots-Tag: noindex` unless `indexable`); private and workspace pages inside the UI. View results are cached for their `max_age` and refreshed in the background.
- Tests first: `tests/interface/web/test_pages.py`: visibility, the cache and the rate limit.
- Done when: a public page answers with no session, and a workspace page doesn't.

**37.6 Templates** · needs 37.5
- Build: `pages_from_template` (`template`, `project`, `visibility`): **Roadmap** (features by status and order, with progress), **Upcoming** (open and queued tasks with their assignees and needs), **Changelog** (merged tasks with their PRs, by week), **Wiki** (a docs space as a site with navigation), **Team** and **Models** (Phase 38's numbers).
- Tests first: each template against fixtures, both themes.
- Done when: a project's public Upcoming page is one command.

**37.7 Generated guides** · needs 37.6, 36.7
- Build: a `docs` task kind for agents (26.3): "write or refresh the guide for feature X from its spec, decisions and notes". The agent's result is a doc proposal (36.7) a person accepts; a schedule can ask for a refresh.
- Tests first: on fakes, a `docs` task ends in an open proposal, never in a published doc.
- Done when: the wiki template shows a guide an agent wrote and a person accepted.

**37.8 Export** · needs 37.5
- Build: `pages_export` (`slug`): a static folder (`index.html`, `data.json` with the views' results as of now, the page's assets) for hosting anywhere (GitHub Pages, Cloudflare Pages). `udl pages export <slug> <dir>`.
- Tests first: the exported folder renders the same page offline.
- Done when: an export opens from disk with no hub running.

**37.9 The pages editor** · needs 37.5
- Build: in the `kb` UI plugin: the pages list (with each one's visibility), an editor (markdown or HTML, a live preview in the sandbox, a view picker) and the publish dialog (with 33.4's signing where the workspace asks for it).
- Tests first: Vitest; Playwright: an agent makes a public roadmap over MCP, a person publishes it, and a browser with no session loads it.
- Done when: that Playwright test passes in both themes.

**Phase 37 is done when:** a public Upcoming page for the `udl` project is live on the hub's public URL and changes when a task merges.

---

## Phase 38: Analytics: tasks, agents, models and budgets

How long things take, where people and agents are stuck, and how each model does: what the user works out by hand today when comparing models.

### Tests first
- Each metric is a pure function over recorded records and events, with worked examples whose answers are known, and gives the same answer whatever order its input comes in.
- Budgets: a turn that would start over a budget doesn't start, and the event fires once.

### Tasks

**38.1 Task timelines** · needs Phase 33
- Build: `TaskTimeline` (per task, rebuilt on each `orch.task.changed` from its notes and audit entries): the time in each state and what it waited on: the agent working, CI, the review bot, a person's answer, a merge, blocked (and on whom).
- Tests first: `tests/unit/analytics/test_timeline.py`: worked examples, including a task moved between agents.
- Done when: a task's timeline adds up to its life, to the second.

**38.2 Metrics** · needs 38.1
- Build: `udl/analytics/`: `analytics_tasks` (cycle and lead times, time per state, waiting on people against waiting on agents, review rounds, nudges, reassignments), `analytics_agents` (per agent, runner and model: tasks merged, first-pass clean rate (the bot's first verification clean), rounds, nudges per task, turns, tokens, cost, cost per merged task, stalls), `analytics_stuck` (now: the longest-blocked tasks and whom they wait on, the oldest unanswered questions, PRs waiting to be merged, agents limited or offline). Windows and grouping by project, feature, agent, model and week. Permission `analytics.read`; members see their own agents'.
- Tests first: `tests/unit/analytics/test_metrics.py`: the phase's first bullet; interface tests and `MATRIX` rows.
- Done when: every number on the dashboards (38.5) comes from one of these operations.

**38.3 Comparing models** · needs 38.2
- Build: `analytics_models`: a table across (runner, model) for the same project and task kind, with how many tasks each number rests on, flagging fewer than five.
- Tests first: worked examples.
- Done when: the ERNIE agents' numbers can be compared with a `claude-code` agent's on the same project.

**38.4 Budgets** · needs 38.2, 27.7
- Build: `Budget` (`scope: agent | project | workspace`, `period: day | week | month`, `limit` in USD, `then: warn | pause`). The dispatcher adds up the period's cost from the sessions' usage (25.3's cost, the runners' `Usage`) before each turn; `orch.budget.warned` at 80 %, `orch.budget.exceeded` at 100 %; a paused agent shows the reason. Operations: `analytics_budgets`, `set_budget` (admin), `delete_budget` (admin).
- Tests first: `tests/integration/plugins/apps/orch/test_budgets.py` on a `FakeClock`: the phase's second bullet.
- Done when: an agent over its budget stops before its next turn.

**38.5 Dashboards** · needs 38.2, 37.6
- Build: the **Team** and **Models** templates (37.6) over these operations, and the numbers on the orch UI's Agents page (29.6).
- Tests first: Vitest and Playwright, both themes.
- Done when: the dashboard shows each agent's first-pass clean rate and cost per merged task.

**Phase 38 is done when:** on the live hub, the Models dashboard compares the agents that built Phase 38 by first-pass clean rate and cost per merged task.

---

## Phase 39: Running a hub for others

### Tasks

**39.1 Packaging** · needs Phase 34
- Build: a `Containerfile` (multi-stage: `uv sync --locked --no-dev` into `/app`; runtime `python:3.14-slim`; a non-root user; `udl serve --host 0.0.0.0`; config from a mounted file and `UDL_*`), `deploy/compose.yaml` (a hub and `postgres:18-alpine`, with volumes), and a Helm chart `deploy/helm/udl/` (Deployment, Service, optional Ingress, a PVC or a PostgreSQL URL from a Secret, the config as a ConfigMap, probes on `/healthz` and `/readyz`). CI builds the image on every PR and publishes it to GHCR on tags (this touches `.github/workflows/`: read in full by whoever merges it).
- Tests first: a CI job that starts the compose file and runs `udl doctor` and a hub smoke test against it; `helm lint` and `helm template` in CI.
- Done when: `docker compose up` gives a working hub, and so does the chart on a local cluster.

**39.2 Behind a proxy** · needs 39.1
- Build: `[hub] trusted_proxies` (CIDRs; `X-Forwarded-For` and `-Proto` are believed only from them); the Host check (which today accepts only localhost) accepts `public_url`'s host too, and still refuses others (DNS rebinding); `/healthz` and `/readyz`. Docs for Caddy, Cloudflare Tunnel and a Kubernetes ingress.
- Tests first: `tests/interface/test_proxy.py`: forwarded headers believed only from trusted addresses; an unknown Host refused.
- Done when: a hub behind Caddy gets passkeys, OAuth and links right.

**39.3 OIDC sign-in** · needs 32.4
- Build: `[[hub.oidc]]` (`name`, `issuer`, `client_id`, `client_secret` (a secret reference), `scopes`, `allowed_domains`, `auto_create`, `default_role`): discovery, the authorization code flow with PKCE, ID token checks (issuer, audience, expiry, nonce, signature from the JWKS, with `joserfc`). A provider identity is linked to an existing user only when that user, signed in, confirms it. GitHub (OAuth 2, not OIDC) is offered beside it, through its `user` and `user/emails` calls. The sign-in page lists the providers.
- Dependency: `joserfc`.
- Tests first: `tests/interface/test_oidc.py` against a fake OIDC provider (an ASGI app in `tests/support/`): every ID token check.
- Done when: signing in with a Google account works against the fake, and an opt-in smoke test does against Google.

**39.4 Quotas and limits** · needs 31.7
- Build: per workspace (defaults in `[hub]`, overrides by the hub's admin): records per kind, storage, API calls per principal per minute, runs at once, devices, agents running. Over one: `QuotaExceeded` (429 for a rate, 413 for a size), with the limit in the hint. A hub-admin page shows each workspace's use.
- Tests first: `tests/unit/hub/test_quotas.py`.
- Done when: one workspace can't slow another down past its limits.

**39.5 Backup and restore** · needs 31.7
- Build: `udl hub backup <dir>` (the hub database and every workspace's, each a consistent snapshot; the secrets are listed by reference, not copied) and `udl hub restore <dir>`. With 31.7's export and import, a workspace moves between a self-hosted hub and a shared one.
- Tests first: a backup restored into an empty hub gives the same records and a verifying audit chain.
- Done when: the hand check restores a backup of the live hub onto a scratch machine.

**39.6 Mail** · needs 32.1
- Build: `[hub.mail]` (SMTP: host, port, user, a password reference, `from`) for invites, password resets and sign-in alerts. Without it, an invite is a link the admin hands over, and a reset is an admin's job.
- Tests first: against `aiosmtpd` in the tests.
- Done when: an invite arrives by mail on the fake server.

**39.7 Security review of Phases 31–39** · needs 39.1–39.6
- Check, each with a test where one is missing: no workspace reaches another's records, search hits, pages, links or `udl:` URLs; tokens and sessions (scopes, expiry, revocation); the OAuth server (open redirects, code reuse, a PKCE downgrade, refresh reuse, the `resource` binding); device links (a replayed `Hello`, a grant that widens, a path that escapes the ceiling, a hub asking for what its link doesn't allow); the audit (redaction, the chain, signatures); pages (escaping the sandbox, view projection); memory sync (names that make paths); proposals (a source's text can't get anything accepted). Findings are fixed test-first and listed here.

**39.8 Docs** · needs 39.7
- Build: `docs/hub.md` (one machine, compose and Helm; workspaces; people; Claude and ChatGPT connectors), `docs/clients.md` (enrolling, links, grants and the ceiling), `docs/audit.md`, `docs/knowledge.md` (datasets, docs, memories, sources), `docs/pages.md`; the README.

**39.9 (Claude + user) Live check** · needs 39.8
1. A hub on the machine the user picks, behind TLS, with two workspaces.
2. The user's account and a teammate's; jeff-wiggle and thevault enrolled; the teammate's Mac (Codex) enrolled by the teammate.
3. Claude's and ChatGPT's connectors list a workspace's tasks.
4. An agent's task merged with a passkey-signed merge, traced by `udl audit trace`.
5. The public Upcoming page, and `udl audit verify` clean.

**Phase 39 is done when:** the live check passes.

---

## Phase 40: Browser-driven integrations (after Phases 13 and 16.2)

Apps with no API (or none a person can use) are read through udl's own browser (H-D8): the user's example was an agent pulling chats out of a messaging app into the data bank. This phase builds the kit and proves it on a fixture app; WhatsApp waits (see Later).

### Tests first
- The kit's contract, over a fixture app: it knows when it is signed in; a watcher sees each new item once, across reloads and restarts; a changed layout is reported once; anything that posts goes through an operation the policy allows.

### Tasks

**40.1 `BrowserApp`** · needs 13.8, 13.9, 16.2
- Build: `udl/integrations/browser_app.py`: an `Integration` whose accounts are `web` accounts (13.8, a browser profile each), with `origins`, `signed_in(page) -> bool`, `screens` (a `Screen` names a URL pattern and the value chains (16.2) that read it into a Pydantic model) and `steps` (13.9) for what it does in the page.
- Tests first: `tests/contract/test_browser_app_contract.py` over the fixture app's plugin (40.4).
- Done when: the contract's sign-in and reading cases pass.

**40.2 The live watcher** · needs 40.1
- Build: one long-lived tab per account in udl's browser; a `MutationObserver` bridge (an injected script reporting changes through `page.expose_binding`); the screen read again after changes settle (debounced), and compared with the cursor (item keys), so each new item is an event once; a crashed tab is reloaded with backoff; a layout that stops matching reports `udl.account.error` once (16.5's rule). Every input goes through `HumanInput` (13.4).
- Tests first: the phase's bullet, over the fixture app.
- Done when: an item added in the fixture app is one event, even across a udl restart.

**40.3 Acting through the page** · needs 40.1
- Build: operations that post (send, reply, react) are `open_world`, and destructive where they send; AI tools reach them only in `ask` mode unless the action is `auto` and the policy allows the operation (17.4).
- Tests first: an AI action in `ask` mode waits for approval before anything is sent.
- Done when: nothing leaves the browser without an allowed operation.

**40.4 The fixture app and an example plugin** · needs 40.2, 40.3
- Build: `tests/support/fakechat/`: a small chat web app (sign-in, conversations, messages arriving over a WebSocket), and the `fakechat` example plugin on the kit; `udl plugins new browser-app <name>` (35.4).
- Done when: `fakechat` messages become events that a subscription turns into dataset items (36.1), and a reply sent through the kit appears in the app.

**40.5 Docs** · needs 40.4
- Build: `docs/browser-apps.md`: writing one; using it only on your own accounts and data; many sites' terms forbid automation, and accounts can be banned for it.

**Phase 40 is done when:** 40.4's "done when" holds against the fixture app, from a plugin made with the template.

---

## Phase 41: Agent sandboxes

"Containerisation and more of the security features, eventually." Agents' processes are fenced in, so a confused or injected agent can't reach past its worktree.

### Tasks

**41.1 Harness sandboxes, on and checked** · needs 25.8, 35.2
- Build: the `claude-code` runner's settings file (25.8) turns on Claude Code's own sandbox (writes only in the worktree, network to an allow-list); `codex` already runs `workspace-write` (35.2); OpenCode has none, so it needs 41.2. `RunnerHealth.sandbox: native | container | none`; a project's `sandbox = required | preferred | off`; the dispatcher refuses to start an agent where `required` can't be met. Confirm Claude Code's sandbox settings against the installed version and record them in the phase's decisions.
- Tests first: the settings file's sandbox block; the dispatcher's refusal.
- Done when: `udl orch runners` shows each harness's sandbox on each host.

**41.2 `ContainerShell`** · needs 41.1, 25.6
- Build: a `HostShell` wrapper that runs a session's processes in a rootless Podman (or Docker) container: the worktree mounted read-write at the same path; the repo's caches (`uv`, `pnpm`) as named volumes; no home directory, no SSH agent; network through an allow-list proxy (package indexes, the model's provider; OpenCode's server on the host through a forwarded socket); CPU and memory from the device's capacity. Commits are signed through `udl node signer`, a socket that signs only git commit objects on the task's branch, each one audited.
- Tests first: the `HostShell` contract over `ContainerShell` (its `Requirement`: `podman` or `docker`); escape tests: writing outside the worktree, reading `~/.ssh`, reaching a host off the allow-list, all fail.
- Done when: the contract and the escape tests pass.

**41.3 Images** · needs 41.2
- Build: `Repo.sandbox` (`image`, or `containerfile`, a path in the repo), built and cached per version of that file; a default image with git, gh (with no token), uv, Node with pnpm, and the harness CLIs.
- Tests first: the image is rebuilt when its Containerfile changes and not otherwise.
- Done when: udl's own repo has a sandbox image whose `make check` passes inside it.

**Phase 41 is done when:** an OpenCode agent works a task in a container on thevault with `sandbox = required`, and the escape tests pass there.

---

## Later (not in these phases)

- **WhatsApp**, on Phase 40's kit (a linked device, read-only first), with its terms and the risk of a ban stated plainly.
- **Analytics over a product's own data:** dashboards on datasets beyond what views and pages give (a business's own analytics platform).
- **Hubs sharing with hubs:** a project in one hub's workspace shared with another hub.
- **Anchoring the audit chain outside the hub:** publishing checkpoints to a git repo or a transparency log.
- **Workspace data encrypted at rest** with keys per workspace.
- **SAML and SCIM** for companies' identity systems.
- Translations.

## Tests (every task)

- **Unit:** pure logic (permissions, the chain, redaction, the merge, metrics, placement, the protocol's models) with property tests where the card names one.
- **Contract:** link transports, search, `BrowserApp`, and the existing contracts over the new stores (`NamespacedStore`) and shells (`ClientShell`, `ContainerShell`); new record kinds join the record contract in every database plugin.
- **Integration:** an in-process hub with in-process devices over real localhost sockets, `FakeGitHub`, `FakeRunner`, `tests/support/webauthn.py`, a fake OIDC provider and a fake Drive, on a `FakeClock`.
- **Interface:** a test and a `MATRIX` row per operation; the parity test; workspace isolation; every credential kind over the API and `/mcp`; the CLI through `RemoteService`.
- **Web:** Vitest per component and store; Playwright per flow, both themes, with the virtual authenticator for passkeys.
- **Coverage:** the 90 % gates are a floor. Cover every refusal, every error mapping and every way a link, a sync or a chain can break.
