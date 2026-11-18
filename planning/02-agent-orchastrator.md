# UDL Phases 23–30: Agent orchestrator (`orch`), generated plugin UIs, CI and branch locking

> Part of udl's plan, and **the next work to build**. [planning.md](../planning.md) holds the [Working agreement](../planning.md#working-agreement-tdd--typing) (TDD, `pyright --strict`, `make check`), the Definition of Done and the task-card format; they all apply here. Source notes: [new-plugin.md](new-plugin.md). Written by Claude on 13 Nov 2026.

## Progress tracker

**The tracker for these phases is in [planning.md](../planning.md#next-to-build-phases-2330-the-agent-orchestrator-highest-priority)** (moved there on 17 Nov 2026 at the user's request, so there is one place to mark). The marks and who sets them are explained there. Agents write what they did, the PR and anything they hit under the task's card below, in the same PR as the work.

---

## What it is

udl gets an **orchestrator**: one place to plan work (features and their tasks), hand it to AIs, follow them, answer their questions and merge what they finish. It replaces what Liively runs today from markdown on thevault (`~/code/liively/planning/human-comms.md`, the `agent-queues/` files, the trackers in each planning file, oc-guard's `tracker-check`), with the same three interfaces as the rest of udl (CLI, HTTP API, MCP) plus web pages.

Alongside it come three things the notes ask for: a bot workspace on this machine to build it with, CI with the Claude review bot and a locked `main`, and a change to the web UI plan so **every plugin gets a generated UI from its operations**, which a UI plugin then guides rather than replaces.

**Depends on:** Phases 8–12 (done). It pulls forward Phase 18 (as 24.1) and tasks 17.1 and 17.3 (as 25.2). Phases 13–16, the rest of 17, and 19–22 are independent and come after.

## Decisions

**The user's (13 Nov 2026):**

| ID | Decision | Rejected |
|---|---|---|
| **O-D1** | **Phase 18 and 17.1/17.3 are pulled forward** and built first as part of this work | Backend first with the UI later; a small standalone UI |
| **O-D2** | **udl drives harnesses.** A runner plugin starts and prompts OpenCode sessions (OpenRouter models) and the machine's `claude` CLI (subscription models), in a worktree per task. Any other AI is another runner | Tracking only with oc-guard driving; udl's own coding agent loop |
| **O-D3** | **Who merges a ready PR is a project setting:** `human` (it appears on Ready to merge) or `agent` (the agent merges when green). Promotion branches, where a project has them, are always a reviewer's or a human's | Always a human; always the agent |
| **O-D4** | **udl's own repo: `main` only.** Feature branches PR straight into a locked `main`, with CI and the review bot. Only Claude or the user merges (the user's standing rule for `main`), so udl's project uses `merge = human` | `develop → rc → main`; `develop → main` |
| **O-D5** | **1Password by reference:** anywhere udl takes a secret, `op://vault/item/field` may be given; udl reads it at use through `op` and never writes it to disk. Vaults are allow-listed. udl's own tokens stay in its secret store | 1Password as udl's secret store; both |
| **O-D6** | **1Password sign-in is a service account token,** limited to the allowed vaults, kept in udl's secret store | The desktop app's integration; either per machine |
| **O-D7** | **Many projects, each with several repos, from the start; agents run on this machine and on others over SSH** (thevault) | Local agents only; udl's repo only |
| **O-D8** | **Markdown is imported, then retired.** udl's database is the only tracker afterwards | Regenerated snapshots in git; two-way sync |
| **O-D9** | **The bot workspace mirrors thevault:** OpenCode + oc-guard, a udl `agent-push` profile, a queue file per agent. **Two DeepSeek V4.1 Flash 1017 agents** (`deepseek/deepseek-v4.1-flash-1017` on OpenRouter, paid, each key spend-capped) since 19 Nov 2026 (user); from 17 Nov until then, four free ERNIE-5.5-Flash-Preview agents. Claude supervises them and keeps human-comms.md | One agent; Claude Code only; four free ERNIE agents (17–18 Nov) |
| **O-D10** | **A manager model may assign and reorder tasks, answer its reports' questions, review before "ready", and create or split tasks. Each is a setting, allowed per manager** | A fixed set |
| **O-D11** | **Other machines are reached over SSH now. udl clients come later** (see [Later](#later-not-in-these-phases)): udl itself in client mode on a machine, a full node on which the hub can run any allowed operation. `HostShell` (25.6) is the seam a client transport plugs into, so nothing above it changes. (Clients are planned now: Phase 34, after Phase 30, per H-D1, H-D3 and H-D4 in [04-shared-data-layer.md](04-shared-data-layer.md).) | Clients in this plan with SSH only to bootstrap; both transports now |
| **O-D12** | **udl manages the `claude` CLI on every host, not only this one:** running and following sessions, status (version, signed in, plan, limit state), installing and updating it, and starting its sign-in from the hub for the user to finish. The same for OpenCode | Sessions only |

**Claude's, made while planning (revisit in the phase's "Decisions made while building" if building finds better):**

| ID | Decision | Why |
|---|---|---|
| **O-C1** | The orchestrator is a plugin named **`orch`** of a new kind, **`udl.apps`**: a plugin that brings operations, record kinds, event types, a runtime service and a UI, without accounts | The notes call it a plugin; `Integration` is built around accounts and watchers per account, which it has none of |
| **O-C2** | Harness adapters are their own kind, **`udl.runners`** (`claude-code`, `opencode`, `command`, and `fake` in tests) | "Kept generic so any AI can be used": a new AI is one small plugin |
| **O-C3** | **Claude Code subscription: udl only ever runs the `claude` CLI** that is signed in on the host. It never reads, copies or sends the subscription's token, and never calls Anthropic's API with it | That is the use the subscription allows; anything else is a third-party client on a personal token |
| **O-C4** | **Remote agents run through `ssh <host> -- <argv>`** from the one `udl serve` (the hub), with worktrees on that host. Agents call back to the hub's `/mcp`. A harness's HTTP API on another host (OpenCode's) is reached through an SSH port forward, never by exposing it on the network | One database, one dispatcher, one lease (O-D11). Runners and `orch` only ever see a `HostShell`, so the later client transport is one new class |
| **O-C11** | **The `opencode` runner uses OpenCode 2's HTTP API** (`/api/session…`, read from the installed 2.1.2's own OpenAPI document and the V2 docs), not `opencode run` and not the V1 routes (`/session/:id/message`, `prompt_async`), which V2 no longer has | Sessions get per-session permissions, a durable log udl can resume from, steering, and cost; `run --format json` gives none of those reliably |
| **O-C12** | **Sign-in from the hub is a relay, not a login by udl:** udl starts the harness's own login on the host, shows the link or code it prints, and passes back what the user types. The token is written by the harness, on that host, and udl never sees it | Same reason as O-C3 |
| **O-C5** | **GitHub is an integration** (`github`), not part of `orch`. `orch` uses its operations and events | Its PR operations and events are useful to subscriptions and MCP clients on their own |
| **O-C6** | **An agent talks to udl over MCP with its own token.** Marking a task ready, blocking it, asking a question and pushing are udl operations. Agents get no `git push` and no `gh pr merge` in their harness | It replaces `tracker-check`, the queue files and `agent-push`, and udl knows who called |
| **O-C7** | **Merging is never an MCP tool for an implementer.** `github_merge_pull` is `mcp=False`-style (see 25.5) unless the project's `merge = agent`, and then only for that agent's own ready PR | A prompt-injected agent must not be able to merge |
| **O-C8** | **`main`'s ruleset has no bypass, the admin included:** PR only, CI and the review job required, signed commits, linear history, no force-push or delete | "Branch locking" that the owner can skip isn't locking; a hotfix is still a PR |
| **O-C9** | Drag and drop uses `@dnd-kit` (keyboard-usable), and every drag has an operation and a CLI command behind it | The drag is a convenience over `orch_assign`, not a web-only feature |
| **O-C10** | The Stitch screens are **references**: each UI task builds its screen in React and daisyUI against the screenshot, and never pastes Stitch's HTML | The shell's stack, typing and tests are fixed (18.1) |

## Concepts

| Term | Meaning |
|---|---|
| **Project** | A body of work with one or more **repos** (GitHub slug, checkout path on a host, base branch, gate command) and its merge policy: `udl`, `liively` |
| **Feature** | The top tier: a goal with a spec (`F-12`, or an imported key like `22`). It links to each of its tasks |
| **Task** | The second tier: one piece of a feature that one agent (or one human) does, usually one PR (`22.4`). It always links back to its feature |
| **Link** | A typed pointer from a feature, task, question or action to something else: a PR, issue, commit, CI run, file, URL, Stitch screen, or another task. Looked up both ways |
| **Note** | An entry on a task or feature: what was built, a decision, a finding. Never edited, only added |
| **Agent** | A named AI worker: a runner, a model, a host, a role and, optionally, a manager (another agent). `ernie-a`, `claude` |
| **Runner** | A plugin (`udl.runners`) that drives one harness: starts a session in a directory, sends prompts, streams what happens |
| **Host** | A machine agents run on: `local`, or an SSH target |
| **Queue** | An agent's assigned tasks, in order |
| **Session** | One conversation of an agent in its harness. It outlives turns and restarts |
| **Question** | Something an agent (or Claude) needs a human or a manager to answer, with its thread (`Q-7`) |
| **Human action** | Something only a person can do, with a result line |
| **Inbox** | Ready to merge + Q&A + To action: what human-comms.md is today |

Task states and the marks they import from and print as:

| State | Mark | Set by | Meaning |
|---|---|---|---|
| `open` | `[ ]` | — | Not assigned |
| `queued` | `[ ]` | assigning | In an agent's queue |
| `in_progress` | `[ ]` | the dispatcher | The agent is on it |
| `blocked` | `[!]` | the agent (`orch_block`), with the question | Only a human or manager can unblock it |
| `ready` | `[r]` | `orch_ready`, once readiness (27.6) holds | PR green, waiting to be merged |
| `merged` | `[~]` | the GitHub watcher | Merged into the repo's base branch |
| `done` | `[x]` | a `reviewer` agent or a human only | Reviewed against the spec (and promoted, where the project has promotion branches) |
| `moved` | `[m]` | reassigning | Taken from one agent for another; the first must not touch it |
| `cancelled` | — | a human or manager | Not going to be done |

Where a project's base branch is the last branch (udl: `main`), the reviewer's merge is the review, so `merged` and `done` are set together by Claude.

## Architecture

```
 Web UI: shell + generated plugin pages + the orch UI plugin        CLI: udl orch …      MCP: orch_* tools
                 \_______________________________ UDLService: operations registry ______________________/
                                                             |
              ┌───────────────────────┬──────────────────────┼─────────────────────┬──────────────────┐
        orch (udl.apps)          github (integration)   AI providers           Secrets            Hosts
   projects · features · tasks   PRs · checks · comments  openrouter · …    store + op:// resolver  local · ssh
   links · notes · agents        watcher → github.pr.*                                                 |
   questions · actions                  |                                                         HostShell
              |                         |                                                              |
        Dispatcher (runtime service, under the runtime's lease) ───► AgentRunner (udl.runners) ────────┘
        queue → worktree → prompt → events → nudge / next             claude-code · opencode · command
              ▲                                                              |
              └──────── the agent calls back: /mcp with its agent token ─────┘
                        orch_my_task · orch_note · orch_ask · orch_block · orch_push · orch_ready
```

A task's life: **assigned** (dragged to an agent, or picked from its suggestions) → the dispatcher makes a worktree and prompts the agent with the rules, the feature's spec and the task's → the agent works test-first, calls `orch_push` (gates, push, PR) → the GitHub watcher follows CI and the review bot → the agent fixes what they find → `orch_ready` succeeds once readiness holds → **Ready to merge** → merged (by a human, or by the agent where `merge = agent`) → the worktree goes, PRs stacked on it are sent to be rebased → a reviewer marks it done. A question at any point goes to the agent's manager, then to the humans' Q&A page, and its answer comes back into the session and into the task's notes.

---

## Phase 23: Repo workflow and the bot workspace on jeff-wiggle

### Tasks

**23.1 CI** · needs nothing
- Build: `.github/workflows/ci.yml`.
  - Triggers: `pull_request` to `main`; `push` to `main`. `concurrency` cancels a PR's older runs.
  - Job `check` on `ubuntu-latest`: `actions/checkout`, `astral-sh/setup-uv` (cache on, Python from `.python-version`), `uv sync --locked --all-extras`, `uv run playwright install --with-deps chromium`, `make check`.
  - `permissions: contents: read`. Never `pull_request_target`: the repo is public, and a fork's PR must not see secrets.
  - Once `web/` exists (24.1), `make check` already runs the web checks; add `pnpm/action-setup` and `actions/setup-node` (from `web/.node-version`) then, and a second job `e2e` running `make e2e`.
- Tests first: the workflow is its own test. Open the PR that adds it with one deliberately failing assertion in a scratch test, see `check` fail, remove it, see it pass. A test that fails in CI only because a tool is missing there must skip through its `Requirement`, as it does locally: fix the skip, never the workflow.
- Done when: a PR shows `check` green, and a red test turns it red.

- **Built (Claude, 17 Nov 2026, PR #1, merged as `06af10a`):** `ci.yml` as above. Two things the card didn't know: `astral-sh/setup-uv` publishes no floating major tag, so it is pinned to `v10.3.3`; and `uv` reads `.python-version` itself (setup-uv has no `python-version-file` input). The canary (a test failing only under `GITHUB_ACTIONS`) turned `check` red, as it should, and CI's first full run also found four tests that passed only on jeff-wiggle, fixed in the tests as the card says: the CLI harness read Typer's help through the forced terminal Typer turns on whenever `GITHUB_ACTIONS` is set (`tests/support/cli.py` now switches `rich_utils.FORCE_TERMINAL` off per call); `test_a_service_made_from_the_settings_alone_uses_udl_s_own_plugins` assumed `notify-send` (plugins that say what to install are now told apart from broken ones); and the browser downloader's "writes no files" contract counted fontconfig's cache, which Chromium writes into the isolated home when the machine's own cache is stale (`files_under` leaves out `.cache/fontconfig`). `make check` takes about 7 minutes on `ubuntu-latest`.

**23.2 The Claude review bot** · needs 23.1
- Build: `.github/workflows/claude-review.yml`, copied from Liively's (`liively.thevault.lan:code/liively/admin-2.0/.github/workflows/claude-review.yml`) with `branches: [main]` and `runs-on: ubuntu-latest`. Keep everything else: the model-variable check, the full review on `opened` / `reopened`, the verification review on `synchronize` (each earlier item stated as FIXED, PARTIALLY FIXED, NOT FIXED or NEW ISSUE), `use_sticky_comment: true`, the same `--allowedTools`, and `--model ${{ vars.CLAUDE_REVIEW_MODEL }}`. The job is named `review`.
- Add to the full-review prompt: "The spec for this work is in `planning/`: check the change against the task the PR names, and that its tests were written to fail first."
- Done when: a PR gets the bot's sticky comment, and a second push gets the verification table.

- **Built (Claude, 17 Nov 2026, PR #1):** `claude-review.yml` as the card says. claude-code-action **skips a review whose workflow file differs from the default branch's** (its own guard against a PR rewriting its reviewer), so the PR that adds or changes `claude-review.yml` is never reviewed by the bot and its `review` job passes empty; PR #1 was merged by the user on that basis. `CLAUDE_REVIEW_MODEL` is `claude-opus-5-5` (as Liively's); `CLAUDE_VERIFY_MODEL` is unset.

**23.3 (Claude + user) Secrets, variables and the lock** · needs 23.1, 23.2
- User: `claude setup-token`, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN -R yelrom0/udl` (typed in the user's own terminal, never in a transcript).
- Claude: `gh variable set CLAUDE_REVIEW_MODEL` (and `CLAUDE_VERIFY_MODEL` if a cheaper one is wanted) on the repo; they are repo variables here, since `yelrom0` is a user and not an organisation.
- Claude: a repository ruleset `main` (`gh api repos/yelrom0/udl/rulesets`), enforcement `active`, target `refs/heads/main`, **no bypass actors** (O-C8), with rules: `pull_request` (0 approvals needed, since a one-person repo can't approve its own PR; conversations resolved), `required_status_checks` (`check` and `review`, strict), `required_signatures`, `required_linear_history`, `non_fast_forward`, `deletion`. Repo settings: squash merge only, delete branches on merge.
- Done when: `git push origin main` is refused, and a PR can't merge while `check` is red.

- **Done (17 Nov 2026):** the user set `CLAUDE_CODE_OAUTH_TOKEN`; Claude set the variable, made the repo squash-only with branches deleted on merge, and created ruleset `main` (id 27418077): `deletion`, `non_fast_forward`, `required_linear_history`, `required_signatures`, `pull_request` (0 approvals, threads resolved, squash only), `required_status_checks` (`check`, `review`, strict), **no bypass actors**. `GET /repos/yelrom0/udl/rules/branches/main` lists all six. PR #1 itself went in as a merge commit, just before the lock. The user decided (Q-1, 17 Nov) that Claude merges PRs "as long as the Claude review bot is happy with them including no nits, CI and everything pass".

**23.4 The working agreement** · needs 23.3
- Build: in planning.md's "Working a task", replace "Branch `phase-N` … fast-forward `main` and push" with: a branch per task (`feat/<task>-<slug>` or `fix/…`) → PR into `main` naming its task → CI and the review bot green → the implementation agent marks `[~]` and writes its notes → Claude reviews against the plan, merges (squash) and marks `[x]`. Add a `CLAUDE.md` at the repo root with the same, the gate command (`make check`) and "never push to `main`", so the review bot and agents read it.
- Done when: planning.md, `CLAUDE.md` and the memory note agree.

- **Done (Claude, 17 Nov 2026, PR #2):** "Working a task" in planning.md now describes this flow, and a root `CLAUDE.md` gives the bot and agents the spec, the rules, the gate and "never push to `main`".

**23.5 (Claude) oc-guard here** · needs 23.3
- Build, on jeff-wiggle:
  - `git clone liively.thevault.lan:code/oc-guard ~/Documents/code/oc-guard`, `bun` installed, `bun install`, its own checks green, `./install.sh`.
  - In oc-guard, test-first: an `agent-push` profile for udl (gate `make check`, base `main`, worktrees under `~/Documents/code/udl-wt/`), and `pr-status` reading udl's review bot. Carry the fix for the bug human-comms.md records on 12 Nov: a verification comment with any PARTIALLY FIXED, NOT FIXED or NEW ISSUE row is **not** READY, whatever its closing line says.
  - `~/.config/opencode/AGENTS.md` and the policies as on thevault: push only with `agent-push`, check only with `pr-status --wait`, never merge, own worktree only, one subagent at a time, signed commits. OpenCode here is 2.1.2: its config uses V2's names (`permissions` as an ordered `{action, resource, effect}` list with `shell`, and `policies` with `tool.use` / `shell:git push *` for what must never be allowed). Check thevault's `opencode.json` is in the same form before copying it, and migrate it (`opencode.ai/docs/migrate-v1/`) if it isn't.
  - Commit signing for agents through the local 1Password SSH agent.
- Done when: the smoke test in oc-guard's README passes here: a write in the main checkout, `git push` and `gh pr merge` are all refused in an agent session.

- **Partly done (Claude, 17 Nov 2026):** oc-guard now knows two owners, LIIVE-LY's repos (base `develop`) and `yelrom0/udl` (base `main`), from the remote (also through an SSH host alias) or the checkout's place, and `agent-push` and `pr-status` take the owner and base from the repo. A `udl` profile gates with `make fmt` (the formatter) then `make check`, installing the venv with uv. The card's 12 Nov `pr-status` fix was already in oc-guard. An agent's `git push` and `gh pr merge` are refused, checked in a probe session. What is left, and the host's setup, are in Claude's local notes (not in git), since the repo is public.

**23.6 (Claude) Queues and agents** · needs 23.5
- Build: `planning/agent-queues/` with `_rules.md` (thevault's, with udl's flow: `[r]` at READY, never merge, `[!]` + the question when blocked, never wait, end the turn only when no `[ ]` line is left), `ernie-a.md` … `ernie-d.md`, a brief for each, and `register.sh` setting each OpenCode session's queue (`oc-guard-check --session … --set-queue …`). All four run `baidu/ernie-5.5-flash-preview:free` through OpenRouter (user, 17 Nov 2026), each in its own worktrees. The queue files and `human-comms.md` are live files in the main checkout, kept out of git by `.git/info/exclude`, as `planning/` is on thevault: agents edit only their own queue file there, and tracker marks and card notes go in each task's PR. A watchdog re-prompts an idle agent whose queue still has an open line, as `nemotron-watchdog` does on thevault.
- Free-model limits (OpenRouter, 17 Nov 2026): 20 requests a minute and 1,000 a day **per account**, shared by all four agents. A `429` is a limit, not a failure: the watchdog backs off and retries.
- First queues (lanes chosen so agents rarely touch the same files):

  | Agent | Lane | First tasks, in order |
  |---|---|---|
  | `ernie-a` | the web shell: `web/`, `udl/interfaces/web` | 18.1 → 18.2 → 18.3 → 18.4 → 18.5 → 18.6 → 18.9 → 18.10 → 24.6 → 24.8 |
  | `ernie-b` | UI contracts and the generated UI: `udl/core/operations.py`, the `udl.ui` kind, `web/packages/ui-sdk`, `web/packages/shell/src/generated` | 24.2 → 26.1 → 18.7 → 18.8 → 24.3 → 24.4 → 24.5 → 24.7 → 24.9 |
  | `ernie-c` | connections: `udl/plugins/integrations/github`, `udl/plugins/secrets`, `udl/plugins/ai`, their fakes | 25.4 → 25.5 → 25.1 → 25.2 (17.1, 17.3) → 25.3 |
  | `ernie-d` | hosts and runners: `udl/hosts.py`, `udl/plugins/runners`, their fakes | 25.6 → 25.7 → 25.10 → 25.8 → 25.9 → 25.11 |

  Phases 26 (from 26.2), 27 and 28 are handed out by Claude as lanes empty, in the order the cards' `needs` allow.
- Done when: each agent, started once, takes its first task and reaches a PR without being prompted again.

- **Partly done (Claude, 17 Nov 2026):** the user chose **four ERNIE-5.5-Flash-Preview agents** (`baidu/ernie-5.5-flash-preview:free`, OpenRouter) instead of two DeepSeek ones: `ernie-a`…`ernie-d`, each on its own spend-capped key, with queues, `_rules.md` and briefs in `planning/agent-queues/` (local, not in git). A session started through OpenCode's API **runs on the default model unless the model is named when it is created**, and its shell gets a bare environment, so the script that starts sessions sets both. A watchdog timer re-prompts an idle agent whose queue has open lines and waits for a rate limit's reset. **Free-model limits are per account:** the 1,000 requests a day lasted about 45 minutes with four agents (they reset at 00:00 UTC), so the user chose (Q-4) to keep four until each current task is a PR, then run two. **Left:** "each agent takes its first task and reaches a PR without being prompted again".
- **The user's other answers (17–19 Nov 2026):** when the free tier's daily cap runs out, wait for its reset, since the work is free (Q-2). Sending udl's code to the free ERNIE endpoint, which keeps no zero-data-retention promise, is fine, because udl is open source (Q-3).
- **Done (Claude, 19 Nov 2026):** the user switched to **two paid DeepSeek V4.1 Flash 1017 agents**, `deepseek-a` and `deepseek-b`, set up as on thevault: one provider, zero data retention, no data collection, and a spend cap on each key. `deepseek-a` took over `ernie-a`'s lane and `ernie-d`'s runners (25.7–25.11). `deepseek-b` took over `ernie-b`'s lane and `ernie-c`'s connections (25.1–25.5). The ERNIE agents are parked. The watchdog leaves an agent whose key is out of credit for Claude, who asks the user before raising a cap. The "Done when" is met: each agent, started once, took its first task to a PR without another prompt (`deepseek-a` took 18.1, #8, to READY and then opened #12 for 18.2; `deepseek-b` pushed 26.1's review fixes to #9).

**Phase 23 is done when:** a throwaway one-line PR opened by `ernie-a` goes red on a failing test, green after the fix, gets the bot's review, and can only be merged through the PR.

---

## Phase 24: Web UI foundations, and a generated UI for every plugin

Today's plan (18.9, "Without a UI plugin") gives a plugin without a UI plugin no pages at all. That changes: **every plugin gets pages generated from what it already declares** (its operations' request and response models, its settings model, its account options, its event types), and a UI plugin's job becomes guiding and extending that: hiding, ordering, grouping, swapping one panel for a hand-built one, adding routes.

### Tests first
- The generated description (pytest): every operation of a fully loaded registry appears under its plugin with schemas that validate as JSON Schema; a secret field is marked and never carries a value; file-only settings are marked read-only; the output is deterministic.
- `<SchemaForm>` (Vitest, and a property test with `fast-check`): for any schema udl's request models can produce, a filled form's value validates against the schema; required, defaults, enums, optional (`anyOf` with null), nested models, lists, `dict` fields as key/value rows, and `SecretStr` as a password box that never shows a stored value.
- The generated page (Playwright): with `FakeIntegration` and no UI plugin, connect an account, run a read-only operation and see its table, run a destructive one only after confirming, change a setting.

### Tasks

**24.1 Phase 18, pulled forward** · needs 23.1
- Build: 18.1–18.10 exactly as their cards in [planning.md](../planning.md#phase-18-web-ui-foundations--ui-plugins) say. Tick them there; this box follows them. Two amendments: 18.5's `p/$plugin/$.tsx` falls back to the generated page (24.6) when the plugin contributes no route for the path, and 18.7's `UIManifest` gains what 24.7 lists (add the fields in 24.7, not here).
- Done when: Phase 18's own "done when" holds.

**24.2 `UISpec` and `ui_field()`** · needs nothing
- Build: in `udl/core/operations.py`:
  - `UISpec` (frozen): `group: str | None`, `order: int = 0`, `view: Literal["auto", "list", "detail", "form", "action"] = "auto"`, `icon: str | None`, `hidden: bool = False`, `confirm_field: str | None` (a field that must equal a named thing before the button enables, as Proxmox's `confirm` in 22.5).
  - `Operation.ui: UISpec | None`, accepted by `Operation.of` and `@operation`, and by `IntegrationOperation`.
  - `ui_field(*, widget=None, label=None, help=None, placeholder=None, order=None, advanced=False) -> dict[str, JsonValue]`, used as `Field(json_schema_extra=ui_field(…))`; it writes one `x-udl-ui` key. Widgets are a closed `Literal`: `text`, `textarea`, `markdown`, `password`, `number`, `switch`, `select`, `multiselect`, `path`, `url`, `datetime`, `duration`, `json`, `account`, `code`.
  - `resolved_view(op)`: what `auto` means: `list` for a read-only operation whose request has no required fields; `detail` for a read-only one that has; `action` for one whose request has no fields; else `form`.
- Tests first: `tests/unit/core/test_operation_ui.py`: `resolved_view` for each case; an unknown widget is refused when the model is defined; `x-udl-ui` survives into `model_json_schema()`.
- Done when: no existing operation changes behaviour, and the parity test still passes.
- **Built (ernie-b, 17 Nov 2026, PR #5):** `UISpec` (frozen: `group`, `order`, `view`, `icon`, `hidden`, `confirm_field`) as `Operation.ui`, accepted by `Operation.of`, `@operation`, and by an integration's `@integration_operation` (it carries a `ui` on `IntegrationOperation`, and `udl.integrations.operations` passes it to `Operation.of`), kept by `in_namespace`; `ui_field()` writing one `x-udl-ui` key (an unknown widget is refused); `resolved_view(op)`; and `json_schema(model)`, with `UIModel`, `UISchemaGenerator` and `check_ui_hints()`, which draw a model's schema checking every model in it — nested ones too — and refuse a widget udl does not have and a `SecretStr` shown as anything but a password box. `Operation.of` also refuses a `confirm_field` the request does not have, or one on an operation that is not destructive. **Decision:** `resolved_view`'s rules apply in the card's order, so `list` comes first: a read-only operation with no required fields is a `list`, including one that takes no fields at all (24.6 runs a list on open, as it does `list_accounts`), and `action` is only for one that changes something and takes no fields. Tests: `tests/unit/core/test_operation_ui.py` (33), written first in their own commit. Nothing existing changed behaviour; the parity test passes.

**24.3 `GET /ui/generated.json`** · needs 24.1 (18.7), 24.2
- Build: `udl/interfaces/web/generated.py`.
  - `GeneratedUI`: `plugins: list[GeneratedPlugin]`. `GeneratedPlugin`: `name`, `kind`, `title`, `summary`, `has_ui_plugin`, `operations: list[GeneratedOperation]`, `settings: GeneratedForm | None`, `accounts: GeneratedAccounts | None` (integrations: `connect` methods, `access_levels`, the options schema), `event_types: list[str]`.
  - `GeneratedOperation`: `name`, `title`, `summary`, `description`, `hints`, `ui` (resolved), `method` and `path` (from `route(op)`), `request_schema`, `response_schema` (both with `$defs` inlined, by the helper 17.4 needs too: put it in `udl/core/schemas.py`).
  - `GeneratedForm`: `schema`, `read_only: list[str]` (file-only fields), `secret: list[str]`.
  - Served at `GET /ui/generated.json`, behind the same auth as the API, `no-cache` with an `ETag`. Interface-only: add it to the parity test's exemptions beside `/ui/plugins.json`.
  - udl's own operations (namespace `None`) are listed under a pseudo-plugin `udl`, so the shell can generate pages for anything Phases 19–20 haven't hand-built yet.
- Tests first: `tests/interface/web/test_generated.py`: the phase's first bullet.
- Done when: the JSON for the built-in plugins is stable across two runs.

**24.4 `<SchemaForm>`** · needs 24.1 (18.5)
- Build: `web/packages/shell/src/generated/SchemaForm.tsx` and `schema.ts`.
  - `schema.ts`: `fieldsOf(schema) -> FieldSpec[]`, a pure function from a JSON Schema object to an ordered list of fields (type, widget, required, default, enum, help, nested fields, item schema), reading `x-udl-ui` and falling back by type and `format`. Unknown shapes become the `json` widget, never an error.
  - `<SchemaForm schema value onChange errors readOnly? secret? widgets?>`: a controlled daisyUI form. Advanced fields sit in a collapsed section. `errors` are udl's `ErrorInfo` field errors, shown at their fields.
  - Exported from `@udl/ui-sdk` as well, so UI plugins reuse it.
- Dependency: `fast-check` (dev).
- Tests first: `schema.test.ts` and `SchemaForm.test.tsx`: the phase's second bullet.
- Done when: every request schema in `openapi.json` renders without the `json` fallback being the whole form.

**24.5 `<ResultView>`** · needs 24.4
- Build: `ResultView.tsx`: from a response schema and a value: a paged, sortable table for a list of objects (or an object whose only list is `items`, as `ItemsPage`); a description list for a flat object; `JsonTree` otherwise and for nested values. Columns are the scalar fields, `x-udl-ui.order` first; URLs become links, datetimes `TimeAgo`, booleans badges. Results tagged by account (11.4's fan-out) get an account column.
- Tests first: `ResultView.test.tsx`: each shape; untrusted strings are text, never HTML.
- Done when: `list_accounts`' and `list_events`' responses render as tables.

**24.6 The generated plugin page** · needs 24.3–24.5, 24.1 (18.9)
- Build: `web/packages/shell/src/generated/PluginPage.tsx`, the fallback of `/p/$plugin/$`.
  - Tabs, each shown only when it has something: **Operations**, **Settings**, **Accounts**, **Events**.
  - Operations: grouped by `ui.group`, ordered by `ui.order` then title. A `list` view runs on open and shows its table with a refresh button. `detail` and `form` views show a `<SchemaForm>` and a Run button, then the `<ResultView>`. A destructive operation asks through `ConfirmDialog`; with `confirm_field`, the button stays disabled until the field matches. An operation the API refuses (`NotAllowed`) shows the error's hint.
  - Settings: the settings form; read-only fields say "set in the config file"; Save calls `set_plugin_settings`.
  - Accounts: the plugin's accounts with status, Check, Disconnect; Connect opens a generated form for `tokens` and `none`, and a "Sign in" button for `oauth` (the server's callback, 11.3).
  - Events: the plugin's event types (schema and example) and its recent events from the `live` store.
  - The `plugins` store adds a nav item for every plugin that has operations, settings or accounts, unless its UI plugin contributes its own nav.
- Tests first: Vitest for the page against `FakeUdl`; `web/e2e/generated.spec.ts`: the phase's third bullet.
- Done when: the Plugins page links every plugin to a page that does something.

**24.7 UI plugins guide the generated UI** · needs 24.6
- Build:
  - `UIManifest.generated` (all optional): `nav: {title, icon} | false`, `tabs: list[Literal["operations","settings","accounts","events"]]` (which to keep, in order), `hide: list[str]` (operation names or globs), `groups: dict[str, list[str]]`, `order: list[str]`, `labels: dict[str, str]`.
  - Three new contribution points, added to the closed set and to the SDK's prop types: `operationViews` (operation name → a mount that replaces that operation's panel; props `{operation, schema, run}`), `resultViews` (operation name or glob → a mount for its result; props `{operation, value}`), `fieldWidgets` (a widget name → a mount; props `{field, value, onChange}`).
  - Precedence, stated once in `docs/ui-plugins.md`: a contributed `settings` or `accountSetup` replaces that generated tab; a contributed route at a path wins over the generated page at that path; everything not replaced stays generated.
  - Bump `SDK_VERSION`'s minor.
- Tests first: `tests/unit/plugins/test_ui.py` (a `hide` glob matching nothing and an `operationViews` key naming no operation are problems, not errors); Vitest for precedence.
- Done when: the template plugin (18.10) hides one operation and replaces one result view in six lines of manifest.

**24.8 Proof** · needs 24.7
- Build: `web/e2e/generated-guided.spec.ts`: the shadcn example (18.10) gains a `resultViews` contribution for one fake operation; the rest of that plugin's page stays generated. Both themes.
- Done when: both e2e specs of this phase pass under `make e2e`.

**24.9 planning.md amended** · needs 24.8
- Build: requirement **5d** becomes "A plugin without a UI plugin gets a generated UI from its operations, settings and accounts"; the "Without a UI plugin" bullet under UI plugins is rewritten to match; 20.7–20.10 note that each UI plugin now only builds what the generated page can't (the mailbox, the watch wizard) and guides the rest.

**Phase 24 is done when:** `make web && udl serve`, then `/ui/p/<any plugin>` lets a person do everything that plugin's CLI commands can.

---

## Phase 25: Connections: 1Password, OpenRouter, GitHub, hosts and runners

### Tests first
- Secret references: a value `op://allowed/item/field` is stored as a reference and resolved on `get`; a vault outside the allow-list is refused when it is saved, not when it is used; the resolved value is never in a record, a log, an error message or `get_config`; a failing `op` is `ToolUnavailable` with a hint; the cache expires on the `Clock`.
- Runner contract (`fake`, then each real runner against a fake harness binary): a session starts in the given directory; a prompt produces events ending in exactly one `TurnEnded`; a session resumes by id after the runner object is thrown away; `stop()` ends a running turn; a rate limit is a `RunnerLimited` carrying when it lifts; secrets given as environment never appear in `argv`.
- `HostShell`: the same contract for `local` and for `ssh` against a test SSH server (`asyncssh`, dev only) running on localhost.

### Tasks

**25.1 Secret references (`onepassword`)** · needs 8.6
- Build:
  - `udl/plugins/secrets/resolvers.py`: `SecretResolver` ABC (`scheme: ClassVar[str]`, `requires`, `async resolve(reference: str) -> SecretStr`, `async check() -> ResolverHealth`, `allows(reference) -> bool`), a `udl.secret_resolvers` plugin kind, and `ResolvingStore(SecretStore)`: it wraps the configured store; `set()` of a value matching `^<scheme>://` checks `allows()` and stores the reference text; `get()` of a stored reference resolves it through its resolver, with an in-memory cache (`cache_ttl`, default 5 min, on the `Clock`). `open_secret_store()` returns the wrapped store when any resolver is enabled.
  - `udl/plugins/secrets/onepassword.py`: `OnePasswordResolver`, scheme `op`. `resolve()` runs `op read --no-newline <reference>` through `udl.process` (argv, no shell) with `OP_SERVICE_ACCOUNT_TOKEN` in the child's environment only. The token is read from the base store at `SecretRef.of("secrets", "onepassword", "token")`. `requires = (Requirement.binary("op"),)`. `check()` runs `op whoami --format json` and `op vault list --format json`.
  - `[secrets.onepassword]`: `enabled = false`, `vaults: list[str] = []` (the allow-list; **file-only**, since it widens what udl reads), `cache_ttl = "5m"`, `timeout = "20s"`.
  - Operations: `get_secrets_status` (read-only: the store, each resolver's health and vault names; never a value) and `set_onepassword_token` (admin; the token as `SecretStr`; `mcp=False`-style: CLI prompts hidden, API takes it in the body, no MCP tool). CLI: `udl secrets status`, `udl secrets onepassword login`.
- Tests first: `tests/unit/plugins/secrets/test_resolvers.py` and `test_onepassword.py`, with a fake `op` script on `PATH` (as the downloader tests fake their tools); `ResolvingStore` over `memory` joins `tests/contract/test_secret_contract.py`.
- Done when: an AI provider saved with `api_key = "op://udl/openrouter/key"` works, and `secrets.json` / the keyring hold only that text.

**25.2 17.1 and 17.3, pulled forward** · needs 25.1
- Build: both cards as written in [planning.md](../planning.md#phase-17-local-ai). Tick them there.

**25.3 `openrouter`** · needs 25.2
- Build: `udl/plugins/ai/openrouter.py`: `OpenRouterProvider(OpenAICompatibleProvider)`, name `openrouter`.
  - Default `url = "https://openrouter.ai/api/v1"`; headers `HTTP-Referer` and `X-Title: udl`.
  - `models()` from `GET /models`: `ModelInfo` gains optional `pricing: ModelPricing | None` (`prompt`, `completion`, in USD per million tokens, converted from OpenRouter's per-token strings) and `context_length`; `tools` from `supported_parameters`.
  - `health()` from `GET /key`: the label, usage and limit, shown as `ProviderHealth.detail` (and `credit_left: Decimal | None`).
  - Requests send `usage: {"include": true}`; `ChatResponse.usage` gains optional `cost: Decimal | None`.
  - 402 → `NotAllowed` ("OpenRouter: out of credit"); 429 → `RateLimited` with `Retry-After`.
  - `FakeOpenRouter` in `tests/support/ai_servers.py`.
- Tests first: the AI provider contract over `FakeOpenRouter`; `tests/unit/plugins/ai/test_openrouter.py` for prices, credit and the two errors.
- Done when: `udl ai models --provider openrouter` lists models with prices against the fake, and an opt-in smoke test does against the real one.

**25.4 Fake GitHub, typed client** · needs nothing
- Build:
  - `tests/support/github.py`: `FakeGitHub`, an ASGI app with the REST calls below, state a test can change (push a commit, finish a check, post a comment as the bot, merge), `ETag` / 304 support and rate-limit headers.
  - `udl/plugins/integrations/github/client.py` and `models.py`, on `udl.http`, one Pydantic model per response:

    | Call | Model |
    |---|---|
    | `GET /user` | `GhUser {login, id}` |
    | `GET /repos/{o}/{r}` | `GhRepo {full_name, default_branch, private, permissions}` |
    | `GET /repos/{o}/{r}/pulls?state=&head=&base=` · `GET …/pulls/{n}` | `GhPull {number, title, state, draft, merged, mergeable, mergeable_state, head {ref, sha}, base {ref}, html_url, user, body, updated_at}` |
    | `POST …/pulls` · `PATCH …/pulls/{n}` | `GhPull` |
    | `PUT …/pulls/{n}/merge` (`merge_method`, `sha`) | `GhMerge {merged, sha, message}` |
    | `GET …/commits/{sha}/check-runs` | `list[GhCheckRun {name, status, conclusion, html_url, completed_at}]` |
    | `GET …/issues/{n}/comments` · `POST` | `list[GhComment {id, user, body, created_at, updated_at, html_url}]` |
    | `GET …/pulls/{n}/comments` | `list[GhReviewComment {…, path, line, commit_id, in_reply_to_id}]` |
    | `POST …/actions/runs/{id}/rerun-failed-jobs` | — |

  - Errors: 401 → `ReauthRequired`; 403 with a rate-limit header → `RateLimited`; other 403 and 404 → `NotAllowed` / `DoesNotExist`; 409 and 422 on merge → `NotAllowed` with GitHub's message; 5xx → retry policy, then `PluginError`. Pagination by `Link` headers.
- Tests first: `tests/unit/plugins/integrations/github/test_client.py`: each call's path, query, auth header, parsing and every error mapping.
- Done when: the client is `pyright --strict` clean with no `Any` in a signature.
- **Built (`ernie-c`, 17 Nov 2026, PR #4):** `tests/support/github.py` (`FakeGitHub`, an ASGI app whose state is the client's own models, with Link/ETag/rate-limit headers, a page cap, a 403/429 switch, a forced status per call and a `mangle` hook for a body GitHub does not send) and `src/udl/plugins/integrations/github/{client,models.py}`: `GitHubClient` on `udl.http`, one method per call the card's table lists, `GhUser`/`GhRepo`/`GhPull`/`GhCheckRun`/`GhComment`/`GhReviewComment`/`GhMerge`, and the error mapping (401 `ReauthRequired`; a 403/429 that is the rate limit `RateLimited` with its `Retry-After`; other 403/404 `NotAllowed`/`DoesNotExist`; 409/422 `NotAllowed` with GitHub's message; 5xx retried three times on the Clock, then `PluginError`). A connection failure is retried and then raised as `udl.http` raised it (`NetworkError`), not as `PluginError`, so a caller knows trying later may work. Pagination follows `Link: rel="next"` and refuses a next link off GitHub's host; a list stops after `GitHubClient.max_pages` (100) pages. Tests: 58 in `tests/unit/plugins/integrations/github/test_client.py`, 100 % branch coverage of both new modules, `pyright --strict` clean. `tests/unit/plugins/integrations/test_base.py` was touched (not my lane): its parametrization ids are the path below the integration's folder (github has two modules), and it now allows an integration to import its own modules.

**After the review on PR #4 (17 Nov):** a rate limit now says when the window opens (the `X-RateLimit-Reset` header, measured on the Clock), not a flat 60 s; the rate limit of an *error* answer is kept (a watcher reads `client.rate_limit` after a `RateLimited`); only a GET is ever retried — a POST, a PATCH or a PUT that fails is raised at once (never "not tried again" silently), and only an error `is_retryable()` says may be; check runs are read to their last page (they were cut off at GitHub's 30); every paged call asks for 100; `_retry_after` reads the Clock; `GhRepo.permissions` is optional (GitHub omits it without a token); `sha` is left out of a merge when there is none; the merge method and the pull state are `Literal`s; a repository that is not `owner/name` is refused before it reaches the path; and the adapter cache (and its `# pyright: ignore`) is gone — `model.model_validate` is enough.

**25.5 The `github` integration** · needs 25.4, 11.1–11.6
- Build: `udl/plugins/integrations/github/plugin.py`, `events.py`; its entry point.
  - `connect = (ConnectMethod.TOKENS,)`: a fine-grained token (may be an `op://` reference). Account options: `repos: list[str]` (the `owner/repo`s it may act on; operations refuse others), `api_url = "https://api.github.com"` (file-only override, for the fake). `identify()` calls `/user` and each repo, and refuses a token without pull-request write access to one.
  - Operations:

    | Operation | Request → response | Hints |
    |---|---|---|
    | `github_list_pulls` | `repo`, `state`, `head?` → `ItemsPage[PullInfo]` (fans out over accounts) | read-only, open-world |
    | `github_get_pull` | `repo`, `number` → `PullDetail` (the pull, its head's check runs, its issue comments and review comments) | read-only, open-world |
    | `github_open_pull` | `repo`, `head`, `base`, `title`, `body`, `draft` → `PullInfo` (updates the open PR for that head if there is one) | open-world, idempotent |
    | `github_comment` | `repo`, `number`, `body` → `CommentInfo` | open-world |
    | `github_rerun_checks` | `repo`, `number` → `Rerun` (failed jobs of the head's runs) | open-world |
    | `github_merge_pull` | `repo`, `number`, `sha`, `method = squash`, `confirm` (must equal `owner/repo#number`) → `MergeResult` | destructive, open-world, **admin**; no MCP tool (see below) |

  - `OperationHints.mcp: bool = True` is added here if Phase 22 hasn't added it yet (22.1's card is the spec: no MCP tool, refused for MCP and automation callers, parity test updated). `github_merge_pull` sets `mcp=False`. `orch` reaches merging through its own operation (28.3), which checks who is asking.
  - Watcher: every 60 s per account (ETags, so an unchanged repo costs no rate limit), cursor = each open PR's `(head sha, checks digest, comments digest, state)`. Events, each once: `github.pr.opened`, `github.pr.updated` (a new head), `github.pr.checks_finished` (`conclusion`), `github.pr.commented` (`author`, `body`, `is_bot`), `github.pr.merged`, `github.pr.closed`. Data carries `repo`, `number`, `head_sha`, `html_url`.
- Tests first: the integration contract; `tests/integration/plugins/integrations/test_github.py` against `FakeGitHub`: each event fires once across a restart; `confirm` is checked; a repo outside `repos` is refused.
- Done when: `udl accounts add github main` against the fake, then `udl github list-pulls`, work.

**25.6 Hosts and `HostShell`** · needs nothing
- Build: `udl/hosts.py`.
  - `Host(Record)`: `name` (unique; `local` always exists and can't be deleted), `transport: Literal["local", "ssh"]` (a closed set that `client` joins later, O-D11), `ssh: str | None` (a `~/.ssh/config` host name; never a user-typed command line), `work_dir: str` (where worktrees go on it), `env: dict[str, str]` (non-secret), `enabled`.
  - `HostShell` protocol: `async run(argv, *, cwd, env, secret_env, stdin, timeout) -> ProcessResult`, `stream(argv, …) -> AsyncIterator[bytes]` plus `terminate()`, `interact(argv, …) -> Interaction` (a process whose output is read and whose stdin is written while it runs: 25.11's sign-in), and `forward(port) -> AsyncContextManager[str]` (a base URL on this machine for a loopback port on the host: itself for `local`, `ssh -N -L 127.0.0.1:0:127.0.0.1:<port>` for `ssh`). **Nothing outside `udl/hosts.py` may know which transport a host uses:** a test greps for `ssh` in the runners and in `orch`. `LocalShell` wraps `udl.process`. `SshShell` runs `ssh -o BatchMode=yes -o ControlMaster=auto -o ControlPersist=60 <host> -- <shlex.join(argv)>`, changing directory and exporting `env` through a `sh -c` prefix built only with `shlex.quote`. **`secret_env` never goes in `argv`:** the remote command starts with a tiny reader (`IFS= read -r` per secret from stdin) before `exec`-ing the program.
  - Operations: `list_hosts`, `set_host` (admin), `delete_host` (admin), `check_host` (read-only, open-world: runs `uname` and reports the tools the runners need: `git`, `gh`, `claude`, `opencode`). CLI `udl hosts …`.
  - `Requirement.binary("ssh")` for `SshShell`.
- Dependency: `asyncssh` (dev group only, for the test server).
- Tests first: `tests/unit/test_hosts.py` (quoting: a property test that any `argv` and `cwd` round-trip unchanged through the remote `sh`); `tests/integration/test_host_shell.py`: the phase's third bullet.
- Done when: `udl hosts check thevault` lists the tools found there.
- **Built (ernie-d, 17–18 Nov 2026, PR #7):** `udl/hosts/` as a package (as the other features with models and a manager are): `shell.py` (the `Host` record, the `HostShell` protocol, `LocalShell`, `SshShell`, `shell_for`, `Interaction`), `models.py` (the operations' models) and `manager.py` (`Hosts`: listing, adding, changing, deleting and checking), re-exported from `udl/hosts/__init__.py`, and wired as `UDLService.hosts` with the four operations and the CLI's `udl hosts …` group. **Name change:** the shell's parameter bounding an awaited call is `within: timedelta | None` (the cards say `timeout`), which is udl's convention elsewhere (`FetchQueue.wait(batch_id, *, within=...)`); it names the same thing, and it means `udl/hosts/shell.py` needs no `noqa` for ASYNC109. The remote command is built by `remote_command()` with `shlex.quote` throughout, and a secret given as `secret_env` is read from the command's stdin (a line each) before `exec`, so it is in no argv. **The card's remote `sh -c` became `sh -lc`** (a login shell): sshd's own PATH lacks `~/.local/bin`, which only the user's login profile adds, so `udl hosts check` said `claude` and `opencode` were not installed on a host that has them — and the runners (25.8, 25.9) would have hit the same wall. A test writes a `.profile` putting a fake `claude` on PATH and finds it over SSH. The quoting property test runs the built line through a real `sh` with awkward argv and folders. Tests: `tests/unit/test_hosts.py` (35), `tests/unit/test_host_ops.py` (11), `tests/integration/test_host_shell.py` (11, against a localhost `asyncssh` server in `tests/support/ssh_server.py`). Notes: `forward()` for `ssh` returns the tunnel's URL and the command is tested (`ssh -N -L 127.0.0.1:<local>:127.0.0.1:<port> <host>`); a real tunnel's data path needs a remote sshd that proxies direct-tcpip channels, which asyncssh 2.24 does not, so it is exercised where it is used (25.9). `SshShell` declares `Requirement.binary("ssh")` (ssh on the machine udl runs on, not on the host). `forward()` for `ssh` waits for the tunnel to be ready and gives up with a `PluginFailed` when it is not. `Interaction.read()` gives everything printed since the last read, and its `within` bounds the whole call. A missing `cwd` is the same `PluginFailed` on both shells, and ssh's own exit codes are distinguished (255: the host was not reached; 127: the program is not there).

**25.7 The `udl.runners` kind** · needs 25.6
- Build: `udl/plugins/runners/base.py`.

  ```python
  class AgentRunner[O: RunnerOptions](ABC):
      name: ClassVar[str]                                  # "claude-code", "opencode", "command"
      options_model: ClassVar[type[RunnerOptions]]
      requires: ClassVar[tuple[Requirement, ...]] = ()
      def __init__(self, options: O, *, shell: HostShell, clock: Clock) -> None: ...
      async def health(self) -> RunnerHealth: ...          # installed? signed in? its version (25.11 fills it in)
      async def models(self) -> list[RunnerModel]: ...     # what it can run, on this host
      async def install(self, version: str | None) -> AsyncIterator[str]: ...   # install or update; its output (25.11)
      def login(self) -> Interaction: ...                  # the harness's own sign-in, relayed (25.11)
      async def start(self, spec: SessionSpec) -> SessionRef: ...
      def send(self, session: SessionRef, prompt: str) -> AsyncIterator[AgentEvent]: ...   # one turn
      async def stop(self, session: SessionRef) -> None: ...
  ```

  - `SessionSpec`: `cwd`, `model`, `instructions` (the system text), `mcp: McpEndpoint | None` (`url`, and the token as a secret), `deny: list[str]` (commands the harness must refuse: `git push`, `gh pr merge`, …), `env`, `secret_env`.
  - `SessionRef`: `runner`, `id` (the harness's session id), `cwd`, `host`.
  - `AgentEvent`, a discriminated union: `TextDelta`, `ToolStarted {name, summary}`, `ToolFinished {name, ok}`, `Usage {input_tokens, output_tokens, cost?}`, `TurnEnded {reason: done | stopped | error | limited, text}`, and `RunnerLimited {until}` / `RunnerFailed {message}` as errors.
  - `RUNNERS` in `PLUGIN_KINDS`; `tests/support/runners.py`: `FakeRunner`, scripted per prompt, able to call back into a `UDLService` as an agent would (so e2e tests need no model); and `fake_harness.py`, a small script the real runners' tests point their binary at.
- Tests first: `tests/contract/test_runner_contract.py` over `fake`: the phase's second bullet.
- Done when: `FakeRunner` passes the contract.

**25.8 `claude-code`** · needs 25.7
- Build: `udl/plugins/runners/claude_code.py`. Everything goes through the runner's `HostShell`, so a `claude` on thevault is driven exactly as the one here: each host has its own install, its own login and its own limits, and an agent's `host` picks which.
  - A turn is one process: `claude -p --output-format stream-json --verbose --model <model> --session-id <uuid>` (first turn) or `--resume <id>` (later ones), with `--append-system-prompt-file`, `--mcp-config <file>` + `--strict-mcp-config`, `--settings <file>` holding the `deny` permissions, and `--permission-mode` from the options (default `acceptEdits`). The prompt goes on stdin. The two files are written in the worktree's `.udl/` (git-ignored through `info/exclude`), `0600`.
  - It parses the stream's JSON lines into `AgentEvent`s; unknown line types are skipped, never fatal. A usage-limit result becomes `RunnerLimited` with the reset time when the CLI gives one.
  - `health()`: `claude --version` and `claude auth status` (JSON: `loggedIn`, `authMethod`, `subscriptionType`, and the account's email and organisation, which udl shows but never logs). **udl never opens Claude Code's credential files** (O-C3).
  - The session's files on a remote host are written there through the shell (`sh -c 'umask 077; cat > …'` with the content on stdin), never copied from the hub's disk.
  - `models()`: the options' `models` list (default: the aliases the CLI accepts, `opus`, `sonnet`, `haiku`), since the CLI has no listing call.
  - Flags were checked against Claude Code 2.1.306 here; confirm them against `claude --help` on the build machine and record any change in the phase's decisions.
- Tests first: the contract over `fake_harness.py` speaking stream-json; `tests/unit/plugins/runners/test_claude_code.py`: the argv for a first and a later turn, the deny list in the settings file, the token only in the MCP config file.
- Done when: an opt-in smoke test has `claude` answer one prompt in a temp directory through the runner.

**25.9 `opencode`** · needs 25.7
- Build: `udl/plugins/runners/opencode.py`.
  - It drives OpenCode 2's HTTP API (O-C11). What follows was read from the V2 docs (`opencode.ai/docs/api/`, `/permissions/`, `/policies/`, `/mcp-servers/`, `/agents/`, `/providers/`) and from the OpenAPI document OpenCode 2.1.2 serves here (`opencode api GET /openapi.json`). Commit that document as `tests/fixtures/opencode/openapi-2.1.2.json`; the typed client's models are written against it, and a test fails when a call the runner makes is missing from it.
  - **Finding the server.** One background service per user, on a loopback port that changes: `opencode service status` prints its URL (or `stopped`; then `opencode service start`). Auth is HTTP Basic, user `opencode`, password in `~/.config/opencode/service.json` (`password`); a Bearer header is refused. The runner reads the URL and the password through the `HostShell` on every connect, keeps the password in memory only, and reaches a remote host's port through `shell.forward(port)`. It never asks for `opencode serve --hostname 0.0.0.0`, and never uses `opencode pair --remote` (that exposes the server to the internet).
  - **Calls:**

    | Step | Call | Notes |
    |---|---|---|
    | health | `GET /api/info` | `version`, `pid`, `urls` |
    | models | `GET /api/model?location[directory]=<cwd>` | `Model.Info`: `id`, `providerID`, `modelID`, `name`, `capabilities`, `variants`. A model is named `provider/model#variant` in the CLI and `Model.Ref {providerID, id, variant?}` in the API; OpenRouter's are `openrouter/<vendor>/<model>` |
    | start | `POST /api/session` | `{title, model: Model.Ref, agent?, location: {directory: <worktree>}, permissions: Permission.Ruleset, metadata: {udl: {agent, task}}}` → `Session.Info` (`id`, `cost`, `tokens`, `outcome`) |
    | its MCP server | `PUT /api/experimental/mcp/udl?location[directory]=<worktree>` | `{config: {type: "remote", url: <hub>/mcp, headers: {"X-API-Key": "{env:UDL_AGENT_TOKEN}"}, oauth: false}}`: `oauth` must be `false` for a header key, since OAuth is on by default |
    | its secrets | `PUT /api/session/{id}/environment` | `{variables: {UDL_AGENT_TOKEN: …}}`, so the token is in no config file |
    | a turn | `POST /api/session/{id}/prompt` | `{text, delivery: "queue", metadata: {udl: true}}`. It returns as soon as the input is admitted; the turn is followed on the log |
    | a message into a running turn | the same, `delivery: "steer"` | `orch_send` (27.9), and a manager's or a human's answer arriving mid-turn |
    | following | `GET /api/experimental/session/{id}/log?after=<seq>&follow=true` (SSE) | **The durable log, resumed from the last sequence number udl stored.** `GET /api/event` is not used for this: the docs call it volatile (a slow reader is dropped, and events during a disconnect are lost) |
    | stop | `POST /api/session/{id}/interrupt` | → `{interrupted}` |
    | a permission asked | `POST /api/session/{id}/permission/{requestID}/reply` | `{decision: "reject", message}`: see below |
    | usage | `GET /api/session/{id}` | `cost` (USD) and `tokens` (`input`, `output`, `reasoning`, `cache.read`, `cache.write`) after each turn |
    | resume | `GET /api/session/{id}` | a session outlives the runner and the service; `SessionRef.id` is its id |

    Event names seen in the docs: `session.step.started`, `session.text.delta`, `session.tool.called`, `session.idle`, `session.status`, `permission.asked`, `permission.replied`. `session.idle` ends a turn (`TurnEnded`); map the rest onto `AgentEvent`s, and skip unknown ones. Record each event's exact payload from the log on the first real run and add it to the fixture.
  - **Permissions.** V2's are an ordered list of `{action, resource, effect}` (`allow`, `ask`, `deny`), last match wins, and an unmatched request **asks**. The session's `permissions` are therefore: `{"action": "*", "resource": "*", "effect": "allow"}` first, then a `deny` for each of `SessionSpec.deny` as `{"action": "shell", "resource": "git push *"}` (a pattern ending in ` *` also matches the bare command), then `deny` for `edit` and `external_directory` outside the worktree, and for `question` (an agent asks through `orch_ask`, never the harness's own question tool). A `permission.asked` that still arrives is answered `reject` with "Not allowed here: ask with orch_ask", so a session can never hang on a prompt nobody sees.
  - **Hard stops are policies, on the host.** A session's rules can be widened by a project's `opencode.json`; a `policies` entry can't be. 25.11's setup writes `/etc/opencode/opencode.json` (or, without root, the user's global config) with `{"action": "tool.use", "resource": "shell:git push *", "effect": "deny"}` and the same for `shell:gh pr merge *`. The runner's `health()` reports whether they are in place, and the dispatcher refuses to start an implementer on a host where they aren't.
  - **The agent.** One OpenCode agent file per role, written to the host's `~/.config/opencode/agents/udl-<role>.md` (frontmatter `mode: primary`, `steps`; the body is the system text). The session names it in `agent`. V1's `tools`, `prompt`, `maxSteps` and `permission` keys are not used.
  - **Providers.** OpenRouter's key reaches OpenCode on the host as `opencode service set env OPENROUTER_API_KEY <value>` (25.11), read from udl's secret store (so it may be an `op://` reference); model ids then start `openrouter/`.
  - `tests/support/runners.py`: `FakeOpenCode`, an ASGI app serving those calls with the same shapes (validated against the committed OpenAPI document), Basic auth, and a log that a test can cut and resume.
- Tests first: the contract over `FakeOpenCode`; `tests/unit/plugins/runners/test_opencode.py`: the ruleset built from a `deny` list; the log resumed from the stored sequence after a dropped connection, with no event lost or repeated; a `permission.asked` rejected; the password in no log line.
- Done when: an opt-in smoke test runs one prompt on an OpenRouter model (ERNIE or DeepSeek) through OpenRouter, here and on thevault.

**25.10 `command`** · needs 25.7
- Build: `udl/plugins/runners/command.py`: any CLI as a runner. Options: `argv` (a template with `{model}`, `{session}`, `{cwd}`; the prompt on stdin), `resume_argv`, `output: text | jsonl`. `argv[0]` must be listed in `[orch] allowed_commands` (file-only), as 17.3's provider commands are. Output lines are `TextDelta`s; exit 0 is `TurnEnded(done)`.
- Tests first: the contract over a tiny script.
- Done when: `echo`-like and failing commands both end a turn correctly.

**25.11 Managing harnesses on every host** · needs 25.8, 25.9
- Build: `udl/plugins/runners/manage.py`, and each runner's `health()`, `install()` and `login()`.
  - `RunnerHealth`: `installed`, `version`, `latest: str | None`, `signed_in`, `account: str | None`, `plan: str | None`, `limited_until`, `guards: bool` (the deny policies of 25.9 are in place; always true for runners that enforce them per session), `problems: list[str]`.

    | | `claude-code` | `opencode` |
    |---|---|---|
    | status | `claude --version`, `claude auth status` (JSON), `claude doctor` for `problems` | `opencode --version`, `opencode service status`, `GET /api/info`, `opencode auth list --format json` |
    | install / update | `claude install [stable\|latest\|<version>]`; `claude update` | `opencode upgrade [<version>]`, then `opencode service restart` only while no session is running on that host |
    | sign in | `claude auth login`, relayed | a provider key: `opencode service set env <NAME> <value>` from udl's secret store; an OAuth provider: `opencode auth login <provider>`, relayed |
    | sign out | `claude auth logout` | `opencode auth logout <provider> <account>` |

  - **Relayed sign-in** (O-C12): `login()` returns the shell's `Interaction`. `orch_runner_login` (`host`, `runner`, `provider?`) starts it and answers a `LoginAttempt` (`id`, `prompt`: the text and the URL the harness printed, `expects: none | code`); `orch_runner_login_reply` (`id`, `text`) writes a line to it; `orch_runner_login_status` says whether it finished. Attempts live in memory, end after 10 minutes, and one runs per (host, runner). Where a harness's login can't be driven without a terminal, the answer is the exact command to run in an SSH session on that host; say so in the phase's decisions.
  - `limited_until` is what the dispatcher last saw (27.7), per (host, runner): two agents on one machine's Claude login share its limit, and an agent on another machine doesn't.
  - Installing and updating run **only a fixed argv per runner** with a version matching `^[0-9A-Za-z.\-]+$`; there is no "run this command on a host" operation.
  - Operations:

    | Operation | CLI | Hints |
    |---|---|---|
    | `orch_list_runners` (`host?`) → every (host, runner) with its `RunnerHealth` and models | `udl orch runners [--host H]` | read-only, open-world |
    | `orch_runner_install` (`host`, `runner`, `version?`) → the output's tail and the new version | `udl orch runners install` | admin, open-world |
    | `orch_runner_setup` (`host`, `runner`): writes the deny policies and the `udl-<role>` agent files, sets provider keys | `udl orch runners setup` | admin, idempotent |
    | `orch_runner_login`, `orch_runner_login_reply`, `orch_runner_login_status` | `udl orch runners login` (interactive in the CLI) | admin, open-world; no MCP tools |
    | `orch_runner_logout` (`confirm` = `<host>/<runner>`) | `udl orch runners logout` | admin, destructive |

  - These are registered by `orch` (26.1) but built and tested here against `FakeApp`'s registry, so the phase doesn't wait for Phase 26.
- Tests first: `tests/unit/plugins/runners/test_manage.py` with fake `claude` and `opencode` scripts on a fake host: each status field parsed; an update refused while a session runs; a login relayed end to end (the link shown, a code written back, success seen); an attempt that times out is killed; a bad version string is refused before anything runs.
- Done when: `udl orch runners` shows, for `local` and `thevault`, each harness's version, whether it is signed in and on which plan.

**Phase 25 is done when:** with real connections, `udl secrets status` shows 1Password healthy, `udl ai models --provider openrouter` lists models, `udl github list-pulls` lists udl's, `udl hosts check thevault` passes, and `udl orch runners` reports the `claude` CLI on both machines.

---

## Phase 26: Work tracking: projects, features, tasks, links

### Tests first
- Transitions: every (state, operation, caller role) triple is either allowed or refused by one table-driven test; only a `reviewer` agent or a human reaches `done`; an implementer can't change a task that isn't its own.
- Links both ways: adding a PR link to a task makes the task findable from the PR's URL; deleting a task deletes its links; a feature lists its tasks and each task names its feature.
- Import: importing `planning/03-proxmox.md` gives one feature with twelve tasks, their marks as states, their `needs`, their cards as specs; importing it twice changes nothing.

### Tasks

**26.1 The `udl.apps` kind** · needs 9.9
- Build: `udl/plugins/apps/base.py`.
  - `AppPlugin[S: AppSettings]`: `name`, `title`, `summary`, `requires`, `settings_model`, `ui`; classmethods `operations() -> list[Operation]`, `event_types()`, `record_kinds() -> Sequence[type[Record]]`; `async serve(self, ctx: AppContext) -> None` (its runtime service; default: returns at once).
  - `AppContext`: `service` (to call operations), `records`, `events` (an `EventSink` and a way to wait for events by type glob), `secrets`, `clock`, `settings`, `stop: asyncio.Event`.
  - `APPS` in `PLUGIN_KINDS`; `[apps.<name>]` settings tables, layered like `[integrations.<name>]`.
  - `AutomationRuntime` starts each app's `serve()` under its lease, restarts one that raises with the watchers' backoff, and reports it in `RuntimeStatus.apps`.
  - Record kinds an app defines register as soon as its module is imported (as today); `record_kinds()` exists so the record contract suite can run over them.
  - `tests/support/apps.py`: `FakeApp` (one record kind, two operations, one event type, a service that ticks on the clock).
- Tests first: `tests/contract/test_app_contract.py`; `tests/unit/automations/test_runtime_apps.py`: started once per lease, restarted after a crash, stopped on shutdown.
- Done when: `FakeApp`'s operations appear on the CLI, API and MCP through the generated adapters, and its page appears in the web UI through Phase 24.
- **Built (ernie-b, 18 Nov 2026, PR #9):** `udl/plugins/apps/base.py` with `AppPlugin[S]`, `AppSettings`, `AppContext`, `AppRecords` (save/get/list/delete over udl's database), `AppEvents` (emit through the bus, `wait_for` a type glob) and `AppPlugin.operation()`, which builds an operation from one of the app's own methods and runs it on the app the service has. `APPS` joins `PLUGIN_KINDS`; `[apps.<name>]` is a settings table of its own, layered like `[integrations.<name>]` (an app's file layer is its own table, the same stored layer and file-only rules, and `get_config` shows it); `udl plugins list` shows an app and what it needs. `AppSupervisor` (udl/automations/apps.py) starts each app's `serve()` once per lease, restarts one that raises with the watchers' backoff (FIRST_BACKOFF/MAX_BACKOFF), stops them when the lease goes (they are told through `ctx.stop`, then cancelled after STOP_GRACE), and reports each in `RuntimeStatus.apps` (running, backoff, stopped, failed). Names: the app class is `AppPlugin` (the card's), and the status model is `AppStatus`. `AppPlugin` is generic in its settings (`AppPlugin[S: AppSettings]`) but is held as `AnApp` (`AppPlugin[Any]`) where apps of every kind meet, and its `ui` is the ClassVar the card asks for (a udl.ui plugin's manifest, 18.7, or None for the generated page; 24.7 reads it). **Deferred:** "its page appears in the web UI through Phase 24" is 24.3-24.7's, not 26.1's — 26.1 is done when the app's operations are on the three interfaces and its service runs, which is what its Done-when says beside that clause. Tests: `tests/contract/test_app_contract.py` (11), `tests/unit/automations/test_runtime_apps.py` (7), `tests/unit/plugins/apps/` (24), `tests/interface/test_apps.py` (6, the CLI command, the API route, the MCP tool and `plugins list`). Nothing existing changed behaviour; the parity test and the whole suite pass.

**26.2 Projects and repos** · needs 26.1
- Build: `udl/plugins/apps/orch/` (`plugin.py`, `models.py`, `records.py`), entry point `orch`.
  - `Project`: `name` (unique), `title`, `description`, `merge: Literal["human", "agent"] = "human"`, `promotion: list[str] = []` (branches after the base, in order, e.g. `["rc", "main"]`; empty for udl), `review_bot: str | None` (the bot's login, e.g. `claude[bot]`), `rules: str` (markdown given to every agent on this project: the ground rules).
  - `Repo`: `project`, `name`, `github: str` (`owner/repo`), `account: str` (a `github:<name>` account), `base_branch`, `gate: list[str]` (argv, e.g. `["make", "check"]`), `checkouts: dict[str, str]` (host name → the main checkout's path there), `branch_pattern = "{type}/{key}-{slug}"`. Unique on `(project, name)`.
- Tests first: the record contract over both kinds; `tests/unit/plugins/apps/orch/test_projects.py`.
- Done when: `Project.example()` and `Repo.example()` round-trip in every database plugin.

**26.3 Features and tasks** · needs 26.2
- Build:
  - `Feature`: `project`, `key` (unique in the project; `F-<n>` from a per-project counter unless given), `title`, `spec` (markdown), `status: planned | active | done | parked`, `order: int`.
  - `Task`: `project`, `feature` (the feature's id; **required**), `key` (unique in the project; `<feature key>.<n>` unless given), `title`, `spec` (markdown), `kind: build | human | review`, `state` (the table under Concepts), `needs: list[str]` (task keys), `repo: str | None`, `assignee: str | None` (an agent's name, or `human:<name>`), `position: int | None` (its place in the assignee's queue), `hold: str | None` (a reason: built now, merged later), `blocked_on: str | None` (a question's key or free text), `branch`, `worktree`, `updated_by`.
  - `transitions.py`: `TRANSITIONS`, a table of `(from_state, to_state) → allowed roles`, and `move(task, to, by: Principal) -> Task`, raising `NotAllowed` naming the rule. A feature's `status` follows its tasks unless set by hand: `active` once one is past `open`, `done` when all are `done` or `cancelled`.
  - `Counter` record for keys (`F-`, `Q-`, `A-`), claimed through `claim_lease`-style compare-and-set so two callers never get the same number.
- Tests first: `tests/unit/plugins/apps/orch/test_transitions.py`: the phase's first bullet, generated from the table itself plus the refusals.
- Done when: no code path sets `Task.state` except `move()`.

**26.4 Links** · needs 26.3
- Build: `Link`: `owner_kind` (`feature`, `task`, `question`, `action`), `owner` (its id), `kind` (`pull`, `issue`, `commit`, `branch`, `ci_run`, `file`, `url`, `doc`, `stitch`, `task`, `feature`, `question`), `url` (the canonical form; for internal kinds a `udl:` URL such as `udl:task/udl/22.4`), `title`, `state: JsonObject` (for a pull: `state`, `head_sha`, `checks`, `review`; validated by `PullState`), `added_by`. Indexed by `owner` and by `url`.
  - `canonical(url)`: GitHub URLs normalised (`https://github.com/o/r/pull/12`, no trailing parts), so one PR is one URL.
  - A subscription-free hook in `orch`'s service: on `github.pr.*` events, every `pull` link with that URL has its `state` refreshed (through `github_get_pull`), and `orch.link.changed` is published.
  - Text fields (`spec`, notes, questions) are scanned on save: a GitHub URL or a task key in backticks becomes a link automatically.
- Tests first: `tests/unit/plugins/apps/orch/test_links.py`: the phase's second bullet; canonical forms (hypothesis: canonicalising twice changes nothing).
- Done when: `orch_find_links --url <a PR>` names the task it belongs to.

**26.5 Notes** · needs 26.3
- Build: `Note`: `owner_kind`, `owner`, `kind: progress | decision | finding | review | system`, `text` (markdown), `author` (an agent, `human:<name>` or `udl`), `at`. Append-only: there is no update operation; a correction is another note. State changes write a `system` note ("ready → merged, by the GitHub watcher").
- Tests first: in `test_links.py`'s file for owners; ordering and authorship.
- Done when: a task's history reads top to bottom as its card's notes do in the markdown trackers today.

**26.6 Operations** · needs 26.2–26.5
- Build: in `orch`'s `operations()`, CLI under `udl orch`:

  | Operation | CLI | Hints |
  |---|---|---|
  | `orch_list_projects`, `orch_get_project` | `udl orch projects [show <name>]` | read-only |
  | `orch_set_project`, `orch_delete_project` | `udl orch projects set / rm` | admin |
  | `orch_set_repo`, `orch_delete_repo` | `udl orch repos set / rm` | admin |
  | `orch_list_features`, `orch_get_feature` (with its tasks, links, notes) | `udl orch features [show <key>]` | read-only |
  | `orch_set_feature` | `udl orch features set` | idempotent |
  | `orch_list_tasks` (`project`, `feature`, `state`, `assignee`, `text`) , `orch_get_task` (with its feature, links, notes, questions) | `udl orch tasks [show <key>]` | read-only |
  | `orch_set_task` (create or edit: title, spec, needs, repo, kind, hold) | `udl orch tasks set` | idempotent |
  | `orch_move_task` (`key`, `to`, `note`) | `udl orch tasks move` | — |
  | `orch_split_task` (`key`, `into: list[{title, spec}]`) | `udl orch tasks split` | — |
  | `orch_add_link`, `orch_remove_link`, `orch_find_links` | `udl orch links add / rm / find` | — / — / read-only |
  | `orch_add_note` | `udl orch note <key> <text>` | — |
  | `orch_import_plan` (26.7) | `udl orch import <file>… [--dry-run]` | admin |

  A `project` argument is optional while there is one project, as `account` is in 11.4. Every operation takes keys, never ids.
- Tests first: an interface test per operation and its `MATRIX` row; the parity test.
- Done when: the parity test lists them all.

**26.7 Markdown import** · needs 26.6
- Build: `udl/plugins/apps/orch/importer.py`: `parse_plan(text, *, source) -> ParsedPlan` (pure) and `orch_import_plan(project, files, dry_run)`.
  - A tracker is the list under a `## Progress tracker` heading. A top-level `- [m] **Phase N: Title**` line is a feature (key `N`); its `- [m] N.M text` children are tasks. In a single-phase file (03-proxmox.md), the `# … Phase N: Title` heading is the feature and the tracker's top-level lines are its tasks. Sub-bullets under a task line join its spec.
  - Marks map to states by the Concepts table (`[x]` → `done`, `[~]` → `merged`, `[r]` → `ready`, `[!]` → `blocked` with the text after `BLOCKED:`, `[m]` → `moved`).
  - A card (`**N.M Title** · needs A, B` and its bullets, up to the next card or heading) becomes the task's spec, and its `needs` the task's. `8.5` and ranges `17.5–17.7` are both read.
  - A phase's own sections (from `## Phase N` to the next `## Phase`) become the feature's spec. A file's remaining top-level sections (Decisions, Concepts, Spec…) go on the feature too in a single-phase file, or on a feature keyed `plan` otherwise.
  - Liively's queue files and human-comms.md are **not** parsed: their open items are entered by hand in 30.4 (they are short, and their formats are prose).
  - Re-importing matches by `(project, key)` and changes nothing that is unchanged; a task whose state moved on in udl keeps udl's state, and the report says so.
  - The result is an `ImportReport`: created, updated, unchanged, and every line it could not place, with its line number.
- Tests first: `tests/unit/plugins/apps/orch/test_importer.py`: the phase's third bullet, on copies of `planning.md` and `planning/03-proxmox.md` in `tests/fixtures/plans/`; nothing in a tracker is silently dropped (every `- [` line is in the report as placed or unplaced).
- Done when: a dry run over udl's two plan files reports no unplaced lines.

**Phase 26 is done when:** `udl orch import planning/03-proxmox.md`, then `udl orch features show 22` prints the feature, its tasks with their states, and each task's spec.

---

## Phase 27: Agents, assignment and the dispatcher

### Tests first
- Tokens: an agent's token reaches only the operations its role lists; with it, `orch_ready` on another agent's task is `NotAllowed`; a deleted agent's token stops working at once; the token is stored hashed and shown once.
- Suggestions (hypothesis): a suggested task is always `open`, never `human`, and every one of its `needs` is `merged`, `done` or `ready`; ranking is deterministic; each suggestion carries at least one reason.
- Readiness: a table of recorded bot comments (approve; "approved" with one PARTIALLY FIXED row; NOT FIXED; NEW ISSUE; a verification for an older head) against expected verdicts.
- The dispatcher (`FakeRunner`, `FakeClock`, `FakeGitHub`): an idle agent with a queued task gets a worktree and a prompt; a turn that ends without the task moving is nudged, then blocked after `max_nudges`; a `RunnerLimited` parks the agent until its time; killing the runtime mid-turn and starting it again resumes the same session; two tasks never run in one agent at once.

### Tasks

**27.1 Agents** · needs 26.1, 25.7
- Build: `Agent`: `name` (unique), `title`, `runner`, `runner_options: JsonObject`, `model`, `host` (default `local`), `role: implementer | manager | reviewer`, `manager: str | None` (another agent's name), `manages: ManagerPowers` (27.8), `projects: list[str]` (empty: all), `brief: str` (markdown, added to its instructions), `max_nudges = 3`, `enabled`, `paused`.
  - Derived, never stored: `status: idle | working | waiting | limited | offline | paused` from its session and queue.
  - The hierarchy is a forest: saving an agent whose `manager` chain reaches itself is refused; a `manager` must have role `manager`; an agent with no manager reports to the humans.
  - `AgentSession`: `agent`, `task`, `ref: SessionRef`, `state: starting | running | idle | limited | failed | ended`, `turns`, `nudges`, `limited_until`, `tokens_in`, `tokens_out`, `cost`, `last_text`. `SessionEvent` (scope: history; retention from `[apps.orch] session_days`): the stream, for the live view and the transcript.
- Tests first: `tests/unit/plugins/apps/orch/test_agents.py`: cycles, roles, derived status.
- Done when: the record contract covers `Agent`, `AgentSession` and `SessionEvent`.

**27.2 Agent tokens** · needs 27.1
- Build:
  - `udl/interfaces/security.py`: `check_api_key` becomes `authenticate(settings, given, tokens) -> Principal`. `Principal`: `kind: owner | agent`, `name`, `role`. The configured `api_key` (or localhost without one) is `owner`. A key starting `udl_agent_` is looked up by its SHA-256 in `AgentToken` records. `current_principal: ContextVar[Principal | None]` is set beside `current_caller` by the API and the HTTP MCP endpoint.
  - `AgentToken`: `agent`, `hash`, `created_at`, `last_used_at`. The token itself is in the secret store at `apps/orch/agents/<name>/token`, so the dispatcher can hand it to the runner.
  - `UDLService.call` refuses an `agent` principal any operation outside `ROLE_OPERATIONS[role]`, before validation, with `NotAllowed`:
    - `implementer`: `orch_my_task`, `orch_get_task`, `orch_get_feature`, `orch_list_tasks`, `orch_add_note`, `orch_add_link`, `orch_ask`, `orch_block`, `orch_push`, `orch_pr_status`, `orch_ready`, and the read-only `github_get_pull`.
    - `manager`: the above, plus what its `manages` powers switch on (27.8), each checked against its own subtree.
    - `reviewer`: every `orch_*` operation that isn't `admin`, `orch_move_task` to `done` included.
  - An agent principal never reaches a non-`orch`, non-listed operation, whatever its hints.
  - Operations: `orch_issue_token` (admin; rotates), no "show token".
- Tests first: `tests/interface/test_principals.py`: the phase's first bullet, over the API and over `/mcp`.
- Done when: the role table is the only place roles are compared.

**27.3 Queues** · needs 27.1, 26.3
- Build: `queue.py`: `assign(task, agent, position=None)`, `reorder(agent, keys)`, `unassign(task)`.
  - Assigning an `open` task makes it `queued` at the end (or at `position`); assigning one that another agent holds makes it `moved` for that agent (a `system` note says who must stop) and `queued` for the new one, and the dispatcher stops the old session's turn.
  - A `human` task is assigned to `human:<name>` and appears under To action (28.2), never in an agent's queue.
  - Positions are dense integers per assignee, renumbered in one write.
  - Operations: `orch_assign` (`task`, `agent`, `position?`), `orch_reorder_queue`, `orch_unassign`, `orch_get_queue` (read-only); `orch_my_task` (read-only: the calling agent's current task, with the feature's spec, the task's spec, its links and notes, and the project's rules).
- Tests first: `tests/unit/plugins/apps/orch/test_queue.py`.
- Done when: every drag the board will offer (29.4) is one of these calls.

**27.4 Suggested next tasks** · needs 27.3
- Build: `suggest.py`: `suggest(tasks, agents, *, agent=None, limit=10) -> list[Suggestion]`, a pure function. `Suggestion`: `task`, `agent` (who it suits best), `score`, `reasons: list[str]`, `stack_on: str | None`.
  - Candidates: `open` tasks of kind `build` in the agent's projects, whose `needs` are each `merged` or `done`, or `ready` (then `stack_on` names that task's PR and a reason says "stack on #N").
  - Score, highest first: the feature is `active` (+3); the agent's last task was in the same feature (+3) or the same repo (+1); it unblocks the most other tasks (+1 each, to +3); the feature's `order`, then the task's key, break ties.
  - With no `agent`, each candidate is paired with the idle agent it scores best for.
  - Operation: `orch_suggest` (read-only).
- Tests first: `tests/unit/plugins/apps/orch/test_suggest.py`: the phase's second bullet, and worked examples.
- Done when: suggestions over udl's imported plan put 22.1 before 22.2.

**27.5 Worktrees, gates and `orch_push`** · needs 25.5, 25.6, 27.3
- Build: `workspace.py`.
  - `prepare(task, agent) -> Worktree`: on the agent's host, `git -C <checkout> fetch origin`, then `git worktree add <host.work_dir>/<project>/<key>-<repo> -b <branch> origin/<base>` (or the `stack_on` task's branch). The branch comes from the repo's `branch_pattern`; it and the path are saved on the task. An existing worktree for the task is reused.
  - `remove(task)`: after a merge, `git worktree remove` and delete the local branch.
  - `GateLane`: one gate run at a time **per host** (a `Lease` named `orch-gate:<host>`), so two agents' test runs never fight for the machine.
  - `orch_push` (`task`, `title?`, `body?`): refuses unless the caller is the task's assignee (or an owner). In the worktree: nothing uncommitted (else `NotAllowed` with `git status`'s summary); rebased on the base if it moved; the repo's `gate` argv in the lane, with a timeout (`[apps.orch] gate_timeout`, 60 min); on a pass, `git push --force-with-lease origin <branch>`, then `github_open_pull` (the body gets "Task: `<key>`" and, when stacked, "Merge after #N"), and a `pull` link on the task. It returns `PushResult`: the gate's exit code and the last 200 lines of its output, the PR's URL.
  - `orch_pr_status` (`task`, `wait: bool`, `timeout`): the readiness of 27.6; with `wait`, it returns when the head's checks and the bot's comment for that head are both in, or at the timeout.
- Tests first: `tests/integration/plugins/apps/orch/test_workspace.py` on a temp git repo with a local bare `origin` and `FakeGitHub`: a failing gate pushes nothing; a pass pushes and opens the PR once, and a second push updates it; the lane serialises two pushes.
- Done when: the agent's whole path to a PR is `orch_push`.

**27.6 Readiness** · needs 25.5, 26.4
- Build: `readiness.py`: `readiness(pull: PullDetail, *, bot: str | None, task, tasks) -> Readiness`, pure. `Readiness`: `ready: bool`, `blockers: list[str]`, `open_items: list[ReviewItem]`.
  - Ready needs all of: not a draft; `mergeable` and `mergeable_state` clean; every check run on the head completed with `success`, `neutral` or `skipped`; when `bot` is set, the bot's latest comment is **for this head** (written after the head's commit) and its verdict is clean; no `needs` task still unmerged ("Merge after #N"); the task has no `hold`.
  - `parse_review(body) -> ReviewVerdict`: `items` (each with a status: `open`, `fixed`, `partially_fixed`, `not_fixed`, `new_issue`) and `assessment` (`approve`, `request_changes` or unknown). **Clean means: no item other than `fixed`, and the assessment isn't `request_changes`. An "approve" line never overrides an open item** (the 12 Nov bug in thevault's `pr-status`).
  - A task with `hold` set and everything else ready is reported as `held`, not ready.
  - `orch_ready` (`task`, `note`): refuses with the blockers unless ready; then moves the task to `ready`, writes the note as `progress`, publishes `orch.task.ready`. Where the project's `merge = agent` and the caller is the assignee, it merges instead (28.3's path) and the task goes to `merged`.
- Tests first: `tests/unit/plugins/apps/orch/test_readiness.py`: the phase's third bullet. Bot comments are real ones copied from udl's first PRs (23.2) into `tests/fixtures/reviews/`.
- Done when: a comment with one PARTIALLY FIXED row is not ready, by test.

**27.7 The dispatcher** · needs 27.2–27.6, 25.7
- Build: `dispatcher.py`, run from `OrchApp.serve()`. One loop per enabled agent, woken by `orch.*` and `github.pr.*` events and by a 30 s tick.
  1. **Pick:** if the agent has no `in_progress` task, take its first `queued` one whose `needs` allow it; `prepare()` the worktree; move it to `in_progress`.
  2. **Start or resume:** one `AgentSession` per (agent, task). Instructions = the fixed rules (`rules.md` in the package: work test-first, use only the `orch_*` tools to push, ask and finish, never wait for a person, your work ends at `orch_ready` or `orch_block`) + the project's `rules` + the agent's `brief`. The first prompt names the task and says to call `orch_my_task`.
  3. **Stream** the turn's events into `SessionEvent`s and `orch.session.*` live events.
  4. **After a turn,** look at the task: `ready`, `blocked`, `merged` or `moved` → the session goes idle and the loop picks again. Still `in_progress` → send what changed (a failed check with its name and URL; the bot's open items, quoted as untrusted data; "your PR is green: call `orch_ready`"), or, with nothing new, a nudge. After `max_nudges` turns without progress, the task is `blocked` ("agent stalled") and a question goes to its manager.
  5. **While waiting** on CI or the bot, the agent holds no turn. The `github.pr.*` event for its PR wakes it. A bot that hasn't commented 60 min after the head's checks finished gets `github_rerun_checks` once, and a question after another 60.
  6. **Limits and failures:** `RunnerLimited` sets `limited_until` and publishes `orch.agent.limited`; `RunnerFailed` retries with backoff three times, then the session is `failed` and a question is raised. An agent whose host fails `check_host` is `offline`.
  7. **Recovery:** on start, sessions marked `running` are resumed by their `SessionRef` with "udl restarted; continue".
  - Text from outside (bot comments, CI logs, answers typed by people other than the owner) is always passed inside a fenced, labelled block, as 17.7 does for events.
  - `[apps.orch]`: `hub_url` (what agents on other hosts use to reach `/mcp`; file-only), `gate_timeout`, `bot_wait = "60m"`, `session_days = 30`, `allowed_commands` (file-only), `max_agents_running`.
- Tests first: `tests/integration/plugins/apps/orch/test_dispatcher.py`: the phase's fourth bullet.
- Done when: a `FakeRunner` agent takes two queued tasks to `ready` in order with nobody prompting it.

**27.8 Managers** · needs 27.7
- Build: `ManagerPowers` on a manager agent, each `bool`, default `False` (O-D10): `assign` (queue suggested tasks to its reports and reorder their queues), `answer` (answer its reports' questions), `review` (its OK is needed before a report's task is `ready`), `plan` (create and split tasks within features its reports work on).
  - Each power switches on operations in its role list (27.2), always limited to its subtree: `assign` → `orch_assign`, `orch_reorder_queue`, `orch_suggest`; `answer` → `orch_answer`; `review` → `orch_review_task`; `plan` → `orch_set_task`, `orch_split_task`.
  - A manager's session has a standing task (kind `review`, never finished) and is prompted on events from its subtree, with the event as data: a report went idle with an empty queue (`assign`); a report asked a question (`answer`: answer it only where the spec or a recorded decision covers it, and say which; else `orch_escalate`, which sends it up to its own manager or to the humans); a report's PR is green (`review`: read the diff against the task's spec, then `orch_review_task` with `approve` or `changes` and the reasons; with `review` on, 27.6 adds "manager's OK for this head" to readiness).
  - Everything a manager does is a note on the task with the manager as author, so a human sees who decided what.
- Tests first: `tests/integration/plugins/apps/orch/test_managers.py` with two `FakeRunner`s: each power on and off; a manager can't touch an agent outside its subtree; an escalated question reaches the humans' list.
- Done when: with all four powers on, a manager keeps two reports busy across four tasks without a human, on fakes.

**27.9 Operations** · needs 27.1–27.8
- Build: besides those named above: `orch_list_agents`, `orch_get_agent` (with its queue, session and reports), `orch_set_agent` (admin), `orch_delete_agent` (admin; `InUse` while it has reports or an unfinished task), `orch_pause_agent` / `orch_resume_agent`, `orch_stop_session`, `orch_get_session` (the transcript, paged), `orch_send` (owner only: a message typed into an agent's session), and 25.11's runner operations joined to `orch`'s registry. CLI under `udl orch agents …`, `udl orch queue …`, `udl orch suggest`. `GET /orch/sessions/{id}/stream` (SSE of session events) is interface-only.
- Tests first: an interface test per operation and its `MATRIX` row.
- Done when: the parity test lists them all.

**Phase 27 is done when:** `udl orch agents set fake-a --runner fake …`, `udl orch assign 22.2 fake-a`, and the task reaches `ready` with its PR linked, on the fakes, with `udl serve` restarted once in the middle.

---

## Phase 28: Human comms: Q&A, Ready to merge, To action

This is human-comms.md as data. Its three sections become three lists with the same rules: every item says what it is about and links to it, an answer or result is acted on and then moved to Done with what was done about it.

### Tests first
- A question's answer is delivered into the asking agent's session, unblocks its task, and is written as a `decision` note on every task and feature the question links to.
- Ready to merge orders a stack so that a PR never comes before one it says "Merge after"; a merge moves the task, removes the worktree and sends each PR stacked on it back to its agent to rebase.
- The inbox is the same from the CLI, the API and MCP, by one snapshot test of its model.

### Tasks

**28.1 Questions** · needs 27.7
- Build: `Question`: `key` (`Q-<n>`), `project`, `asked_by`, `to` (an agent's name or `humans`), `title`, `text` (markdown), `options: list[str]`, `blocking: bool`, `state: open | answered | closed`, `answer`, `answered_by`, `outcome` (what was done about the answer). `Message` records hold the thread (follow-ups both ways). Links name the tasks and features it is about.
  - `orch_ask` (`task`, `title`, `text`, `options?`, `blocking`): from an agent. It goes to the agent's manager where that manager has `answer`, else to `humans`. Blocking moves the task to `blocked` and the agent on to its next queued task; non-blocking leaves the agent working.
  - `orch_block` (`task`, `reason`) is `orch_ask` with `blocking` set, for "only a person can do this".
  - `orch_answer` (`question`, `answer`): records it; writes a `decision` note ("Q-7, answered by …: …") on each linked task and feature; moves a blocked task back to `queued` at the front; and the dispatcher delivers the answer into the agent's session.
  - `orch_reply` (a follow-up from either side), `orch_escalate` (a manager passes it up), `orch_close_question` (`outcome`).
  - `orch_list_questions` (`state`, `to`, `task`), `orch_get_question`.
- Tests first: `tests/integration/plugins/apps/orch/test_questions.py`: the phase's first bullet; routing with and without a manager.
- Done when: no question can be answered without its answer landing on the tasks it names.

**28.2 Human actions** · needs 26.3
- Build: `HumanAction`: `key` (`A-<n>`), `project`, `title`, `text`, `when: now | after | later`, `after: str | None` (a task key: it becomes `now` when that task merges), `assignee` (`human:<name>`), `state: open | done | dropped`, `result`. A task of kind `human` creates one when it is assigned.
  - Operations: `orch_set_action`, `orch_list_actions`, `orch_finish_action` (`key`, `result`: writes the result as a note on the linked task and, for a `human` task, moves it to `merged`), `orch_drop_action`.
- Tests first: `tests/unit/plugins/apps/orch/test_actions.py`: `after` turning into `now`.
- Done when: human-comms.md's "To action" section can be entered item for item.

**28.3 Ready to merge** · needs 27.6, 25.5
- Build: `merge.py`.
  - `orch_merge_list` (read-only) → `MergeList`: `ready` (tasks in `ready`, grouped by repo, in merge order: a topological sort of "Merge after" within each stack, then by when they became ready), `coming` (tasks with an open PR that isn't ready, each with its blockers), `held` (with the reason), `done` (merged in the last 14 days, with what followed). Every row has the PR's URL, title, repo in full, head SHA, and the task and feature keys.
  - `orch_merge` (`task`, `confirm` = `owner/repo#number`; admin, owner-only unless the project's `merge = agent` and the caller is the assignee): re-checks readiness for the current head, then `github_merge_pull` with that head's SHA (so a PR that changed since isn't merged blind).
  - After a merge, however it happened (the watcher's `github.pr.merged`): the task moves to `merged` (and to `done` at once if a reviewer or the owner merged and the project has no promotion branches); its worktree is removed; each task stacked on it gets a `system` note and its agent a prompt to rebase onto the base and push again; actions waiting `after` it become `now`.
  - A PR closed without merging moves its task back to `in_progress` and tells its agent why (the closing comment, as data).
  - Promotion, for projects that have branches after the base: `orch_promotion` (`project`) lists what is `merged` and not `done` per repo; `orch_promote` (`tasks`, `note`; reviewer or owner) marks them `done` after the promotion PR merges. udl's own project has none.
- Tests first: `tests/integration/plugins/apps/orch/test_merge.py`: the phase's second bullet; `confirm` checked; a head that moved between listing and merging is refused.
- Done when: a three-PR stack merges in order on the fakes, each rebase prompted.

**28.4 The inbox** · needs 28.1–28.3
- Build: `orch_inbox` (read-only; `project?`, `for = humans`) → `Inbox`: `merge: MergeList`, `questions` (open, blocking first, newest first), `actions` (`now`, then `after`, then `later`), `agents` (each agent's status in a line), `updated_at`. `udl orch inbox` renders it as human-comms.md reads today: three headed sections, each item with its links. `--watch` redraws on `orch.*` events.
- Tests first: `tests/interface/test_orch_inbox.py`: the phase's third bullet.
- Done when: Claude, as an MCP client, gets from one call what it reads from human-comms.md today.

**28.5 Events** · needs 28.4
- Build: event types with schemas and examples: `orch.task.changed` (`from`, `to`), `orch.task.ready`, `orch.task.blocked`, `orch.task.merged`, `orch.question.asked`, `orch.question.answered`, `orch.action.needed`, `orch.agent.limited`, `orch.agent.idle` (an empty queue), `orch.session.started` / `ended`, `orch.link.changed`. A README example: a `notify` subscription on `orch.question.asked where blocking == true`.
- Tests first: each type is registered and each is published once at its moment (asserted in the integration tests above).
- Done when: `udl events types` lists them with schemas.

**28.6 End to end** · needs 28.5
- Build: `tests/integration/plugins/apps/orch/test_end_to_end.py`, through a real `udl serve` on `FakeRunner`, `FakeGitHub` and a temp git repo, driven only by CLI commands and the fake agent's MCP calls: import a plan → assign a task → the agent asks a blocking question → answered from the CLI → the agent pushes, the fake bot finds one item, the agent fixes it, the bot's verification is clean → Ready to merge lists it → merged → the task is `merged`, the worktree gone, the decision on the task's notes.
- Done when: it passes three times running with `pytest -p no:randomly` and with random ordering.

**Phase 28 is done when:** that test passes, and `udl orch inbox` during it shows each stage.

---

## Phase 29: Screens (Google Stitch) and the `orch` UI plugin

Pages that only show and edit records (connections, projects, repos, hosts, the import form) are **generated** (Phase 24) and guided by the manifest. The screens below are hand-built because they are about flow: dragging, live sessions, threads.

### Tests first
- Every screen has a Vitest test against `FakeUdl` for its states (empty, loading, error, full) and a Playwright test for its main flow, in both themes, at desktop and phone widths.
- Each drag on the board has a keyboard path (pick up, move, drop) and both paths end in the same operation call.
- Text from agents, PRs and questions renders as sanitised markdown: a `<script>` or an `onerror` in a question's text never runs (kept as a regression test).

### Tasks

**29.1 (Claude) The Stitch project** · needs 24.1
- Build: a new Google Stitch project, "udl orchestrator".
  - A design system made from the shell's daisyUI `light` and `dark` themes (colours, radius, type scale), so screens match what will be built.
  - One screen each, desktop and phone, light and dark: **Inbox**; **Board**; **Feature**; **Task**; **Agents** (the hierarchy); **Agent** (queue and live session); **Q&A** (list and a thread); **Ready to merge**; **To action**; and the generated **Connections** page as it will look (for reference only).
  - Each screen's Stitch URL and an exported PNG (`web/plugins/orch/design/<screen>-<theme>-<size>.png`) are recorded in the matching task's card below, and later as `stitch` links on the tasks in udl.
- Done when: the user has looked through the screens and said which to change, and those changes are made.

**29.2 The UI plugin's skeleton** · needs 24.7, 28.4
- Build: `web/plugins/orch/` (React, daisyUI, the SDK), built into `src/udl/plugins/apps/orch/ui/`.
  - Manifest: nav "Work" (Inbox, Board, Agents, Q&A, Merge, To action); routes `inbox`, `board`, `features/$key`, `tasks/$key`, `agents`, `agents/$name`, `questions`, `questions/$key`, `merge`, `actions`; a dashboard widget (the inbox's counts); `generated: { tabs: ["settings"], hide: ["orch_*"] }` so the generated page keeps only Settings.
  - Stores (vanilla Zustand): `inbox`, `board` (features, tasks, agents, suggestions), `sessions`; each refetches on the `orch.*` events it cares about through `ctx.events.subscribe`.
  - `<Markdown>`: `marked` + `DOMPurify`, links opening in a new tab with `rel="noopener"`.
  - `<LinkChip>`: one look per link kind; a PR chip shows its checks and review state from `Link.state`.
- Dependencies: `@dnd-kit/core`, `@dnd-kit/sortable`, `marked`, `dompurify`.
- Tests first: stores against `FakeUdl`; `<Markdown>`'s sanitising (the phase's third bullet).
- Done when: every route renders a heading inside the shell, isolated in its shadow root.

**29.3 Inbox** · needs 29.2
- Build: the home route: the three sections of `orch_inbox` as cards with counts, the agents strip (status dots, the task each is on), blocking questions pinned on top. Answering a question with `options` is one click; others open the thread.
- Done when: its e2e test answers a question from the inbox and sees it leave.

**29.4 Board** · needs 29.2
- Build: three panes: **features** (collapsible, each with its tasks as cards showing key, title, state and link chips; filters by project, state and text, kept in the URL), **agents** (a column per agent: its queue as a sortable list, its current task on top, its status), and **suggested** (from `orch_suggest`, each card showing its reasons and the agent it suits; "Queue" on a card accepts the suggestion).
  - Drags: a task or a suggestion onto an agent → `orch_assign` (at the drop position); within a queue → `orch_reorder_queue`; from a queue back to the features pane → `orch_unassign`. A drop the server refuses (a `human` task onto an agent, unmet `needs`) snaps back with the error's hint as a toast. Dropping a task whose `needs` are only `ready` asks "stack on #N?".
  - The keyboard path and the menu path ("Assign to…") call the same functions.
- Done when: its e2e test drags a suggested task to a fake agent and watches it go `in_progress` live.

**29.5 Feature and task pages** · needs 29.2
- Build: **Feature:** spec (markdown, editable), its tasks in order with states, links, notes, "Add task", "Split". **Task:** breadcrumb back to its feature, spec, state with the moves this user may make, assignee, needs and what it unblocks (as links), every link by kind, the PR's readiness with its blockers, notes as a timeline with an "Add note" box, its questions, its session (a link to the agent's page at that session).
- Done when: every key and URL on both pages is a link, and a task's page reaches its feature and back.

**29.6 Agents** · needs 29.2
- Build: **Agents:** the hierarchy as a tree (humans at the root); dragging an agent onto a manager sets `manager` ("assign models to models"), refused with a reason for a cycle or a non-manager; an editor for an agent (runner, model from `orch_list_runners`' list for its host, host, role, brief, and a manager's four powers as switches). **Agent:** status, queue (sortable), token rotate, pause / resume / stop, and the session: a live transcript from the SSE stream (text, tool calls collapsed to a line, usage and cost), a box to send it a message (owner only).
- Done when: its e2e test builds a manager with two reports by dragging, and watches a fake session stream.

**29.7 Q&A** · needs 29.2
- Build: open questions (blocking first) and answered ones with their outcomes; a thread view with the question, what it is about (link chips), option buttons or a markdown answer box, follow-ups, "Close with outcome". New questions appear live, with a count in the nav.
- Done when: its e2e test sees a question arrive, answers it, and finds the decision on the task's page.

**29.8 Ready to merge, To action** · needs 29.2
- Build: **Merge:** Ready now (per repo, numbered in merge order, each with its PR link, checks, the bot's verdict and a Merge button that asks for the `owner/repo#n` confirmation), Coming soon (with blockers), Held (with reasons), Done (with what followed). **To action:** Now / After / Later, each with a result box and Done.
- Done when: its e2e test merges a ready PR on `FakeGitHub` and sees the next in the stack flagged for rebase.

**29.9 Connections and import** · needs 24.7, 29.2
- Build: no hand-built pages: `generated` manifest entries and `ui_field()` hints so the generated pages for `github`, `openrouter`, hosts, secrets status, runners, projects and repos read well (groups, order, labels, the `account` and `markdown` widgets), and two contributions: a `resultViews` one that renders `orch_import_plan`'s report (what a dry run would create, the unplaced lines), and an `operationViews` one for `orch_list_runners`, the **Machines** view: a card per host with each harness's version, update available, signed in and plan, limit state and guards, with Install / Update, Set up and Sign in buttons. Sign in opens the relay (25.11): the harness's link as a button, a box for the code, and the result. This is Phase 24's point proved on real plugins: write down in the phase's decisions anything the generated UI couldn't do and how it was solved.
- Done when: a 1Password token, an OpenRouter key by `op://` reference, a GitHub account, a host and a project with a repo can all be set up from the web alone.

**29.10 End to end** · needs 29.3–29.9
- Build: `web/e2e/orch.spec.ts`: 28.6's story through the web UI only, with `tests/support/e2e.py` extended to start `FakeGitHub`, a `FakeRunner` plugin and a temp git repo.
- Done when: `make e2e` passes in both themes.

**Phase 29 is done when:** with `udl serve` on real connections, the user assigns a task by dragging it, answers the agent's question, and merges its PR, all from `/ui/p/orch/`.

---

## Phase 30: Moving in, docs and the live check

**30.1 Security review** · needs Phase 29
- Check, each with a test where one is missing: agent tokens (27.2) can't reach admin operations or another agent's work; an agent can't merge except under `merge = agent` on its own ready PR; text from PRs, bots and CI reaches a model only as fenced data; `HostShell` never builds a command from unquoted input; secrets (the 1Password token, agent tokens, the OpenCode password, `op://` values) appear in no record, log, `argv`, event or transcript; the generated UI never echoes a stored secret; markdown is sanitised. Findings are fixed test-first and listed here.

**30.2 Docs** · needs 30.1
- Build: README (a "Work" section, the connections), `docs/orchestrator.md` (concepts, a walkthrough from import to merge, agent roles and powers, running agents on another host, the events), `docs/plugins.md` (writing an app plugin and a runner; what the generated UI reads and how to guide it), a config example for `[apps.orch]` and `[secrets.onepassword]`.

**30.3 (Claude) Moving udl's plans in** · needs 30.1
- Create project `udl` (`merge = human`, no promotion branches, repo `yelrom0/udl`, gate `make check`). Import planning.md and 03-proxmox.md; check the report line by line; fix what the importer placed wrongly by hand, and the importer by test where it was the importer's fault. Then replace the trackers in the markdown with one line pointing at `udl orch` and move the files to `planning/archive/` (O-D8). `planning/agent-queues/` goes too: the agents' queues are in udl.
- Done when: `udl orch features` lists Phases 13–22 with their tasks, and nothing in `planning/` is a live tracker.

**30.4 (Claude + user) Live check** · needs 30.3
1. The user makes the 1Password service account (read access to one vault) and runs `udl secrets onepassword login`.
2. Claude connects OpenRouter (by reference), GitHub, and the hosts `local` and `thevault`; creates `ernie-a` (`opencode`, local) and one `claude-code` agent as `reviewer`.
3. The user drags a small real udl task to `ernie-a`. It must reach Ready to merge by itself; Claude reviews and merges it.
4. The same with an agent on thevault, on a small Liively task in a `liively` project entered by hand (its open human-comms.md items entered as questions and actions).

## Later (not in these phases)

The first four items below are planned now, as Phases 31–41 in [04-shared-data-layer.md](04-shared-data-layer.md) (user, 18 Nov 2026): clients as Phase 34, people and permissions as Phase 32 (with the audit in Phase 33), the wiki and guides in 36.4 and 37.6–37.7, budgets and cost reports in Phase 38. They are kept here as they were written.

- **udl clients** (O-D11): udl itself in client mode, installed on a machine and dialling out to the hub, so it works behind NAT and needs no SSH access. A client is a **full udl node**: the hub can run any operation "on" it (fetches, downloads, plugins, Proxmox playbooks, agent sessions), limited by an allow-list the client's own config file holds, and the client manages the machine it is on. It arrives as a third `Host.transport` (`client`) and a `ClientShell` behind `HostShell`, plus enrolment (SSH can install and enrol it), a hub ↔ client protocol, and updates. What this plan does so it fits: every remote action goes through `HostShell` (25.6), harness management is a fixed set of operations per runner rather than free commands (25.11), and callers are `Principal`s (27.2), which a client becomes one kind of.
- **Sign-in and permissions for several users,** human and AI: accounts, roles per project, who may answer, merge and assign. `Principal` (27.2) is where it attaches.
- **A generated wiki, guides and "upcoming features"** from features, specs, decisions and notes.
- Budgets and cost reports per agent, feature and project (the usage is already recorded on sessions).
- Parsing Liively's queue files and human-comms.md automatically; a full move of Liively's trackers.
- A runner on udl's own agent loop (17.5) for work that isn't code.

## Tests (every task)

- **Unit:** pure logic (transitions, suggestions, readiness, the importer, quoting, schema → fields) with property tests where the card names one.
- **Contract:** `udl.apps`, `udl.runners` and `udl.secret_resolvers` each get their suite first, with a fake; new record kinds join the record contract in every database plugin.
- **Integration:** real plugins against `FakeGitHub`, `FakeOpenRouter`, the fake harnesses, a temp git repo and a localhost SSH server, on a `FakeClock`.
- **Interface:** a test and a `MATRIX` row per operation; the parity test; agent principals over the API and `/mcp`.
- **Web:** Vitest per component and store; Playwright per flow, both themes.
- **Coverage:** the 90 % gates are a floor. Cover every refusal, every error mapping and every way a loop can stop.
