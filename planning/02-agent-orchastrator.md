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
| **O-D4** | **udl's own repo: `main` only.** Feature branches PR straight into a locked `main`, with CI and the review bot. Only Claude or the user merges (the user's standing rule for `main`), so udl's project uses `merge = human`, with auto merge on and `claude` as its merger (O-D13, 28.7) | `develop → rc → main`; `develop → main` |
| **O-D5** | **1Password by reference:** anywhere udl takes a secret, `op://vault/item/field` may be given; udl reads it at use through `op` and never writes it to disk. Vaults are allow-listed. udl's own tokens stay in its secret store | 1Password as udl's secret store; both |
| **O-D6** | **1Password sign-in is a service account token,** limited to the allowed vaults, kept in udl's secret store | The desktop app's integration; either per machine |
| **O-D7** | **Many projects, each with several repos, from the start; agents run on this machine and on others over SSH** (thevault) | Local agents only; udl's repo only |
| **O-D8** | **Markdown is imported, then retired.** udl's database is the only tracker afterwards | Regenerated snapshots in git; two-way sync |
| **O-D9** | **The bot workspace mirrors thevault:** OpenCode + oc-guard, a udl `agent-push` profile, a queue file per agent. **Two DeepSeek V4.1 Flash 1017 agents** (`deepseek/deepseek-v4.1-flash-1017` on OpenRouter, paid, each key spend-capped) since 19 Nov 2026 AEDT (user); from 17 Nov until then, four free ERNIE-5.5-Flash-Preview agents. Claude supervises them and keeps human-comms.md | One agent; Claude Code only; four free ERNIE agents (17–18 Nov) |
| **O-D10** | **A manager model may assign and reorder tasks, answer its reports' questions, review before "ready", and create or split tasks. Each is a setting, allowed per manager** | A fixed set |
| **O-D11** | **Other machines are reached over SSH now. udl clients come later** (see [Later](#later-not-in-these-phases)): udl itself in client mode on a machine, a full node on which the hub can run any allowed operation. `HostShell` (25.6) is the seam a client transport plugs into, so nothing above it changes. (Clients are planned now: Phase 34, after Phase 30, per H-D1, H-D3 and H-D4 in [04-shared-data-layer.md](04-shared-data-layer.md).) | Clients in this plan with SSH only to bootstrap; both transports now |
| **O-D12** | **udl manages the `claude` CLI on every host, not only this one:** running and following sessions, status (version, signed in, plan, limit state), installing and updating it, and starting its sign-in from the hub for the user to finish. The same for OpenCode | Sessions only |

**The user's (21 Nov 2026, Q-8, from looking through 29.1's screens):**

| ID | Decision | Rejected |
|---|---|---|
| **O-D13** | **Auto merge:** a project can turn on auto merge, with a **merger**: a designated agent, most often the supervising model, that merges the project's ready PRs (after its own review). It is a switch on Ready to merge, with the merger beside it (28.7) | Only `merge = human` or the implementer itself (`merge = agent`) |
| **O-D14** | **Tasks have a size, in T-shirt sizes:** `S`, `M`, `L`, `XL`, worth 1, 3, 5 and 8 points. udl times how long each task really took and learns the hours a point takes per model, so progress is graphed over time and forecasts get better (26.8, 27.10, 29.11) | Fibonacci points; an estimate in hours |
| **O-D15** | **A chat sidebar on the right of the web UI** (and in a window of its own) **to talk to Claude, or ChatGPT, and get things done from the browser.** A chat is an ordinary Claude Code (or Codex) session: it can do whatever that harness can do in a terminal, practically anything, on the connected machines, and through udl's MCP everything the user can (27.11, 29.12) | A chat that asks before every change; a chat limited to a manager agent's powers |
| **O-D16** | **OpenAI models, wherever Claude is used today, the main and planning models included. Both ways:** a `codex` runner for OpenAI's Codex CLI signed in with the user's ChatGPT plan, the twin of `claude-code` (25.12), and an OpenAI API key, by `op://` reference, for OpenCode's `openai` provider and udl's own AI provider (25.13). Not drawn in the screens | The Codex CLI only; an API key only |

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
| **O-C7** | **Merging is never an MCP tool for an implementer.** `github_merge_pull` is `mcp=False`-style (see 25.5) unless the project's `merge = agent`, and then only for that agent's own ready PR. (A project's merger, under auto merge, is O-C13's.) | A prompt-injected agent must not be able to merge |
| **O-C8** | **`main`'s ruleset has no bypass, the admin included:** PR only, CI and the review job required, signed commits, linear history, no force-push or delete | "Branch locking" that the owner can skip isn't locking; a hotfix is still a PR |
| **O-C9** | Drag and drop uses `@dnd-kit` (keyboard-usable), and every drag has an operation and a CLI command behind it | The drag is a convenience over `orch_assign`, not a web-only feature |
| **O-C10** | The Stitch screens are **references**: each UI task builds its screen in React and daisyUI against the screenshot, and never pastes Stitch's HTML | The shell's stack, typing and tests are fixed (18.1) |
| **O-C13** (21 Nov) | **The merger is an agent with role `reviewer` or `manager`, never an implementer.** It merges a task it is the assignee of only when the project has a review bot, whose clean verdict on that head is then the review by someone else; without one, its own tasks wait for a person. Auto merge, and who the merger is, are set only by an owner, and only through `orch_set_auto_merge`. With auto merge on, the merger may call `orch_merge` on its project's `ready` tasks, and `orch_merge` re-checks readiness for the current head (28.3), whoever calls it. O-C7 stands for implementers | Auto merge is O-D13's. Who may hold it, and what it may not do, keeps a prompt-injected agent from merging, as O-C7 does. The bot's verdict is what lets Claude merge its own docs PRs today |
| **O-C14** (21 Nov) | **A task's size is set by whoever plans it** (the user, Claude, or a manager with `plan`), never by the agent doing it. The importer reads it from a card's header (`**27.3 Queues** · needs 27.1 · size L`). **Working time** is the time a task spent `in_progress`, less the time its agent waited on CI or the review bot or sat out a usage limit: what the agent spent, not what the calendar did | An agent sizing its own task would grade its own estimate |
| **O-C15** (21 Nov) | **A chat runs the user's own harness, as the user.** The `claude` CLI (or `codex`) runs on the chosen host with the user's own settings and permission rules: udl adds its MCP server and nothing else, so whatever the harness would ask in a terminal, it asks in the sidebar (Allow once, Allow for this chat, Deny), and udl never passes a flag that skips permissions. The session calls udl with a chat token of its own, which is the owner's powers minus making tokens and opening chats, and ends with the chat | O-D15 gives a chat the harness's powers; the harness's own permission prompts are then the guard the user already relies on in a terminal, and a token per chat can be revoked |
| **O-C16** (21 Nov) | **The chat sidebar is a new contribution point of the shell, `panels`** (a panel on the right of every page, with a button in the navbar), and `orch`'s UI plugin contributes the chat to it. It sits over the page, docks beside it on a window 1600 px or wider, and is a full-height sheet on a phone | The chat needs `orch`'s sessions and tokens, but belongs on every page, not only `orch`'s |

## Concepts

| Term | Meaning |
|---|---|
| **Project** | A body of work with one or more **repos** (GitHub slug, checkout path on a host, base branch, gate command) and its merge policy: `udl`, `liively` |
| **Feature** | The top tier: a goal with a spec (`F-12`, or an imported key like `22`). It links to each of its tasks |
| **Task** | The second tier: one piece of a feature that one agent (or one human) does, usually one PR (`22.4`). It always links back to its feature |
| **Link** | A typed pointer from a feature, task, question or action to something else: a PR, issue, commit, CI run, file, URL, Stitch screen, or another task. Looked up both ways |
| **Note** | An entry on a task or feature: what was built, a decision, a finding. Never edited, only added |
| **Agent** | A named AI worker: a runner, a model, a host, a role and, optionally, a manager (another agent). `deepseek-a`, `claude` |
| **Runner** | A plugin (`udl.runners`) that drives one harness: starts a session in a directory, sends prompts, streams what happens |
| **Host** | A machine agents run on: `local`, or an SSH target |
| **Queue** | An agent's assigned tasks, in order |
| **Session** | One conversation of an agent in its harness. It outlives turns and restarts |
| **Question** | Something an agent (or Claude) needs a human or a manager to answer, with its thread (`Q-7`) |
| **Human action** | Something only a person can do, with a result line |
| **Inbox** | Ready to merge + Q&A + To action: what human-comms.md is today |
| **Size** | How big a task is: `S`, `M`, `L` or `XL`, worth 1, 3, 5 or 8 points (O-D14) |
| **Merger** | The agent a project's auto merge lets merge its ready PRs (O-D13) |
| **Chat** | A conversation with Claude Code or Codex from the web UI's sidebar, on a host the user picks, as the user (O-D15) |

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
- Build: `planning/agent-queues/` with `_rules.md` (thevault's, with udl's flow: `[r]` at READY, never merge, `[!]` + the question when blocked, never wait, end the turn only when no `[ ]` line is left), `deepseek-a.md` and `deepseek-b.md`, a brief for each, and `register.sh` setting each OpenCode session's queue (`oc-guard-check --session … --set-queue …`). Both run `deepseek/deepseek-v4.1-flash-1017` through OpenRouter (user, 19 Nov 2026 AEDT; from 17 Nov until then, four agents `ernie-a` … `ernie-d` on `baidu/ernie-5.5-flash-preview:free`), each in its own worktrees. The queue files and `human-comms.md` are live files in the main checkout, kept out of git by `.git/info/exclude`, as `planning/` is on thevault: agents edit only their own queue file there, and tracker marks and card notes go in each task's PR. A watchdog re-prompts an idle agent whose queue still has an open line, as `nemotron-watchdog` does on thevault.
- Limits: a `429` is a limit, not a failure, and the watchdog backs off and retries. A paid key at its spend cap fails every request, so the watchdog leaves that agent for Claude. (The ERNIE agents' free tier allowed 20 requests a minute and 1,000 a day **per account**, shared by all four.)
- Queues (lanes chosen so agents rarely touch the same files). Each agent works its first lane, then its second:

  | Agent | Lanes | Tasks, in order |
  |---|---|---|
  | `deepseek-a` | the web shell: `web/`, `udl/interfaces/web`; then hosts and runners: `udl/plugins/runners`, their fakes | 18.1 → 18.2 → 18.3 → 18.4 → 18.5 → 18.6 → 18.9 → 18.10 → 24.6 → 24.8; then 25.7 → 25.10 → 25.8 → 25.9 → 25.11 |
  | `deepseek-b` | UI contracts and the generated UI: `udl/core/operations.py`, the `udl.ui` kind, `web/packages/ui-sdk`, `web/packages/shell/src/generated`; then connections: `udl/plugins/integrations/github`, `udl/plugins/secrets`, `udl/plugins/ai`, their fakes | 24.2 → 26.1 → 18.7 → 18.8 → 24.3 → 24.4 → 24.5 → 24.7 → 24.9; then 25.5 → 25.1 → 25.2 (17.1, 17.3) → 25.3 |
  | `ernie-c` (free tier, from 19 Nov 11:00) | Phase 26's records: `udl/plugins/apps/orch` | 26.2 → 26.3 → 26.4 → 26.5 (26.6 and 26.7 moved to `deepseek-a`, Q-10) |
  | `ernie-d` (free tier, from 19 Nov 11:00) | AI providers, then agent tokens, then the optional `codex` runner | 17.1 → 17.3 → 25.3 (finished by `deepseek-b`) → 27.2 → 25.12 |

  The DeepSeek rows are the lanes as planned on 19 Nov; since then Claude has moved lines between all four as lanes emptied, and the queues in `planning/agent-queues/` are the live list.

  (Until 19 Nov these were four lanes, one ERNIE agent each: `ernie-a` the web shell, `ernie-b` the UI contracts, `ernie-c` the connections from 25.4, and `ernie-d` hosts and runners from 25.6.)

  Phases 26 (from 26.2), 27 and 28 are handed out by Claude as lanes empty, in the order the cards' `needs` allow.
- Done when: each agent, started once, takes its first task and reaches a PR without being prompted again.

- **Partly done (Claude, 17 Nov 2026):** the user chose **four ERNIE-5.5-Flash-Preview agents** (`baidu/ernie-5.5-flash-preview:free`, OpenRouter) instead of two DeepSeek ones: `ernie-a`…`ernie-d`, each on its own spend-capped key, with queues, `_rules.md` and briefs in `planning/agent-queues/` (local, not in git). A session started through OpenCode's API **runs on the default model unless the model is named when it is created**, and its shell gets a bare environment, so the script that starts sessions sets both. A watchdog timer re-prompts an idle agent whose queue has open lines and waits for a rate limit's reset. **Free-model limits are per account:** the 1,000 requests a day lasted about 45 minutes with four agents (they reset at 00:00 UTC), so the user chose (Q-4) to keep four until each current task is a PR, then run two. **Left:** "each agent takes its first task and reaches a PR without being prompted again".
- **The user's other answers (17–19 Nov 2026 AEDT):** when the free tier's daily cap runs out, wait for its reset, since the work is free (Q-2). Sending udl's code to the free ERNIE endpoint, which keeps no zero-data-retention promise, is fine, because udl is open source (Q-3).
- **Partly done (Claude, 19 Nov 2026 AEDT):** the user switched to **two paid DeepSeek V4.1 Flash 1017 agents**, `deepseek-a` and `deepseek-b`, set up as on thevault: one provider, zero data retention, no data collection, and a spend cap on each key. They took over the ERNIE agents' open PRs (#8, #9) and lanes, as in the table above. The ERNIE agents were parked (two came back the same day: next). Claude asks the user before raising a cap. The "Done when" is met for both: `deepseek-a`, started once, finished #8's review fixes and then opened #12 (18.2) on its own; `deepseek-b` finished #9's and then opened #15 (25.5) on its own. **Left:** 23.5, which 23.6 needs.
- **ERNIE back (the user, 19 Nov 2026 ~09:30 AEDT):** "Make sure the Ernie agents are also put to use at 11am. The Deepseek agents can then take over the work that the Ernie agents don't complete." So `ernie-c` and `ernie-d` work again from 19 Nov 11:00, on the free tier's 1,000 requests a day per account (shared, which is why only two came back); `ernie-a` and `ernie-b` stay parked. **The handover rule:** a line an ERNIE agent has started but can't finish before the day's cap moves to the DeepSeek agent with the least open work (`[m]` in ERNIE's queue), and ERNIE keeps its unstarted lines for the next day. Q-10 (below) made one exception.
- **When the review bot is out of usage (the user, Q-6, 19 Nov 2026 AEDT):** its token's weekly limit ran out on 19 Nov at 03:11 AEDT, and it resets on 20 Nov at 20:00 AEDT. Until then the agents keep working, and each PR waits as `[!]` ("waiting for the review bot") in its agent's queue. When the bot works again, Claude re-runs the reviews, oldest first and stacked PRs bottom-up, and merges each one that comes back clear.
- **Memory (the user, Q-7, 20 Nov 2026 AEDT):** only Claude writes Claude's memory, in any regard, and the OpenCode agents keep their own, separate memory in Magic Context. So the `opencode-claude-memory` plugin is `readOnly: true` in `~/.config/opencode/opencode.json`: no `memory_save` or `memory_delete`, no extraction, no auto-dream, and nothing written under `~/.claude`. Magic Context's `memory.enabled` is on in `~/.config/cortexkit/magic-context.jsonc`, so an agent remembers with `ctx_memory`, and the historian captures memories as it compresses a session. It keys a project by its root commit, so all udl worktrees share one memory, kept in Magic Context's own store. Both switches are user-level, since neither plugin can scope by agent, so the user's own OpenCode sessions follow them too. The agents' `AGENTS.md` says so. Claude still moves out anything an OpenCode session writes under `~/.claude`.
- **Codex (the user, Q-9, 21 Nov 2026 AEDT):** 25.12 is optional, and the user isn't signing Codex in: that subscription isn't the user's, and the user uses the Claude Code plugin. Claude installed `codex` 0.171.0 on jeff-wiggle (`~/.local/bin`, not signed in) so 25.12 has a real CLI to read. A test's real turn runs on jeff-wiggle's llama.cpp router instead (`codex exec --profile local`, the router's key from `LLAMA_API_KEY`, never in a file). Claude recorded the first turn and a resume for the fixtures. 25.12 is `ernie-d`'s, on the free tier.
- **Phase 26's end (the user, Q-10, 21 Nov 2026 AEDT: "your recommendation"):** 26.6 and 26.7 moved from `ernie-c` to `deepseek-a`, since they had become the critical path (26.8, 27.10 and Phase 29 wait on them). `ernie-c` kept 26.5. This is an exception to the 19 Nov rule that ERNIE keeps its unstarted lines; the rule still holds otherwise.

**Phase 23 is done when:** a throwaway one-line PR opened by an implementation agent goes red on a failing test, green after the fix, gets the bot's review, and can only be merged through the PR.

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
- **Built (deepseek-b, 2026-11-19, PR #24):** `has_ui_plugin` is 18.7's `ui` (merged). `udl/interfaces/web/generated.py` (`GeneratedOperation`, `GeneratedForm`, `GeneratedAccounts`, `GeneratedPlugin`, `GeneratedUI`, and `generated(ops, plugins)`), served at `GET /ui/generated.json` with the API's auth (`authenticate`: the key, or a signed-in session's cookie, which is what the shell has), `no-cache` and a SHA-256 `ETag` (304 on revalidation). `udl/core/schemas.py`: `inlined()` (every `$ref` into `$defs` replaced by its definition; a self-referring one — a filter tree — keeps its `$defs`, the one case that cannot be inlined) and `model_schema()`, for 17.4 too. **Names:** `GeneratedForm.schema` is `json_schema` (a field cannot override pydantic's `BaseModel.schema()`, and it is what `PluginSettingsInfo.json_schema` is called); the resolved view is `GeneratedOperation.ui.view` (the card's `ui (resolved)`), not a field beside it; `method`/`path` are `None` for udl's own operations, whose routes are hand-written in `udl/interfaces/api/routes` (`route()` cannot say them) — the shell calls those by name, through the typed client. Settings forms come from `settings_model_of(cls)` and the pure `is_file_only`/`is_secret`, so `generated()` needs no service (and no database). **Parity:** nothing to add — `/ui/generated.json` is a Starlette `Route`, so it is not in the OpenAPI schema the parity test reads (`/ui/plugins.json` was exempt the same way). Tests: `tests/interface/web/test_generated.py` (23: udl's own under `udl`, a plugin's route/view/hints/schemas, self-contained schemas, the settings form with its secrets, an integration's accounts and event types, `has_ui_plugin`, the route's ETag/304, its key when there is one, a signed-in session without one, a stale cookie refused, the shell's files still without one, and the same JSON twice) and `tests/unit/core/test_schemas.py` (5: inlining, a field's own keywords beside its `$ref` surviving it, a nested model, a tree that keeps its `$defs`, `model_schema`). **Review round (deepseek-b, 19 Nov 2026, PR #24):** `inlined()` keeps a field's own keywords beside its `$ref` (pydantic writes its description, title and default there, not in an `allOf`) — the definition is the field's, its own keys win — and the stability test compares `model_dump_json(by_alias=True)`, the bytes the ETag hashes; Claude's blocking item: the route takes the API's `authenticate()` (the key, or a signed-in session's cookie, which is what the shell has), so a signed-in shell gets it, a stale cookie does not, and the key alone still does.

**24.4 `<SchemaForm>`** · needs 24.1 (18.5)
- Build: `web/packages/shell/src/generated/SchemaForm.tsx` and `schema.ts`.
  - `schema.ts`: `fieldsOf(schema) -> FieldSpec[]`, a pure function from a JSON Schema object to an ordered list of fields (type, widget, required, default, enum, help, nested fields, item schema), reading `x-udl-ui` and falling back by type and `format`. Unknown shapes become the `json` widget, never an error.
  - `<SchemaForm schema value onChange errors readOnly? secret? widgets?>`: a controlled daisyUI form. Advanced fields sit in a collapsed section. `errors` are udl's `ErrorInfo` field errors, shown at their fields.
  - Exported from `@udl/ui-sdk` as well, so UI plugins reuse it.
- Dependency: `fast-check` (dev).
- Tests first: `schema.test.ts` and `SchemaForm.test.tsx`: the phase's second bullet.
- Done when: every request schema in `openapi.json` renders without the `json` fallback being the whole form.
- **Built (deepseek-b, 2026-11-19, PR #26):** stacked on 18.5 (shell), 18.7 and 18.8 (both merged). `schema.ts` and `SchemaForm.tsx` are `@udl/ui-sdk`'s (`src/schema.ts`, `src/SchemaForm.tsx`), exported from its `react` entry and re-exported by the shell's `src/generated/SchemaForm.tsx` — the card's "exported from `@udl/ui-sdk` as well", so a UI plugin draws the same form udl's own pages do. `fieldsOf(schema)` → `FieldSpec[]` (widget, required, default, choices, nested `fields`, `x-udl-ui`'s label/help/placeholder/order/advanced/writeOnly, ordered); `valueAt`/`withValue`/`withoutValue`; `resolveRefs(schema, document)` for an OpenAPI document's `#/components/schemas/X` (a cycle, or a pointer it cannot follow, is a box of JSON); `isJsonObject`. **The two schema shapes that are easy to get wrong, and are covered:** pydantic's `T | None` is `anyOf: [T, {"type": "null"}]`, and reading it as a union makes *every optional field* a box of JSON — it is unwrapped, so a nullable string is a text box; and `$ref`s are followed, so a `ChainInput` field is its own fields. `<SchemaForm schema document? value onChange errors? readOnly? secret? widgets? disabled?>`: a controlled daisyUI form (drawn from `fieldsOf`), the advanced fields in a closed `<details>`, an object's own fields in a `<fieldset>`, a field's error as `role="alert"` at the field (keyed by its dotted path), the config file's fields disabled with "set in the config file", secrets as password boxes that never draw their value back, a plugin's own widget by the widget's name, a multiselect as checkboxes, and a box of JSON that keeps half-typed text and only changes the value once it parses. **Done when:** `src/generated/openapi.test.ts` walks every operation's `requestBody` in `packages/api/openapi.json` (24 of them) and asserts no request is a single box of JSON, except the two that are genuinely free-form (`post /fetches/challenges/solve`: a list of sites; `put /plugins/{name}/settings`: the settings' values) — that is the test that caught the `anyOf` gap. Also `fast-check` (the dev dependency the card names) for a property: `fieldsOf` never throws on any JSON. **The review's four findings, fixed:** a field's own `description`, `default`, `title` and `x-udl-ui` sit beside its `$ref` (pydantic writes them there, not in an `allOf`) and now win over the definition's — 124 in `openapi.json`, which the new test walks; `list[Literal[...]]` puts its `enum` under `items`, so such a field is a multiselect with its items' choices (`FieldSpec.items` is the item schema the card names, which was missing); a secret's box is never bound to the value — it holds what was typed in this visit, and a set secret says "(set: type to replace)" without its value in the DOM; and a field cleared is taken out of the value with `withoutValue` rather than sent as `null` (a non-nullable field with a default refused it). A secret erased back to empty is taken out too (`onClear`, not saved over the stored secret as `""`), and the JSON box emptied is taken out rather than sent as `null` — the same rule as a select or a number, and the form's multiselect fixture is now the `list[Literal[...]]` shape pydantic actually writes. Tests: `schema.test.ts` (29), `SchemaForm.test.tsx` (30), `openapi.test.ts` (4), and the shell's re-export test; `pnpm check` green (sdk 71, shell 97, api 21; coverage ≥ 90 % in every package).

**24.5 `<ResultView>`** · needs 24.4
- Build: `ResultView.tsx`: from a response schema and a value: a paged, sortable table for a list of objects (or an object whose only list is `items`, as `ItemsPage`); a description list for a flat object; `JsonTree` otherwise and for nested values. Columns are the scalar fields, `x-udl-ui.order` first; URLs become links, datetimes `TimeAgo`, booleans badges. Results tagged by account (11.4's fan-out) get an account column.
- Tests first: `ResultView.test.tsx`: each shape; untrusted strings are text, never HTML.
- Done when: `list_accounts`' and `list_events`' responses render as tables.
- **Built (deepseek-b, 2026-11-19, PR #28):** `web/packages/shell/src/generated/result.ts` decides what a response is (`shapeOf(schema, value, document?)`): a bare array of objects, an object whose *one* array of objects is its rows (`{accounts: [...]}`, `{events: [...]}`), or a fan-out's answer (11.4's `{items: [{account, item}], failures, next_pages}` — its items' fields flattened into the columns with the account they came from first) → a table (caption from the list's title); a flat object whose schema names its fields → a description list; everything else (a scalar, a nested value, two lists, a list with a non-object in it) → JSON. The table's columns are the item's scalar fields from 24.4's `fieldsOf`, so `x-udl-ui.order` applies, and a nested field is not a column. `ResultView.tsx` draws it: sortable by any column (a click toggles the way; numbers as numbers, booleans as booleans, `undefined` and `null` the same nothing, always last), paged 25 at a time with 18.5's `Paged` (a smaller answer than the page that was open shows the last page there is, never an empty one), a boolean as a badge, a `format: uri` as a link only when it is `http(s)` (any other scheme is text), a `format: date-time` as `TimeAgo`, nothing as "—", a value that is not what the schema said as `JsonTree`, and an empty list as "Nothing yet." — an empty list is still a table when its schema says its items are objects. Rows are keyed by their JSON and where they sit, so sorting moves them instead of remounting them and two identical rows are two rows. **Done when:** `src/generated/openapi.test.tsx` reads `list_accounts`' and `list_events`' response schemas from the API's committed `openapi.json` and renders them with the API package's own fixtures — the two real responses, not a schema written to fit the component. Untrusted strings are text: a test renders `<img src=x onerror=…>` as a row value and asserts no element is made of it, and `javascript:…` in a plain string field, or in a `format: uri` one, is not a link. **Review round (deepseek-b, 19 Nov 2026, PR #28):** the fan-out's account column (above), the duplicate row keys, the stale page and the nullish comparator, and the `http(s)`-only link, all with a test each. Round 2: a fan-out is only one when the schema says its `items` are `{account, item}` (so `{items: [], next_page}` stays a table of its own items, not one of a phantom "Account" column), its `failures` are said under the table (every account failed → "Nothing yet." and why), an item's own `account` does not double the column, a row's key is its place in the answer before sorting (so sorting moves rows and does not remount them, tested by the DOM node surviving a sort), and the duplicate-key test uses `toContainEqual` (the `not.toContain(expect…)` it had could never fail). `github_list_pulls`' real `FanOutResult[PullInfo]` schema from `openapi.json` is walked too. Tests: `result.test.ts` (19), `ResultView.test.tsx` (21), `openapi.test.tsx` (6); shell 143 passed, coverage ≥ 90 %.

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
- **Built (deepseek-a, 20 Nov, PR #53):** `web/packages/shell/src/generated/` — `spec.ts` (the list as the page needs it), `load.ts` + `stores/generated.ts` (read beside the plugins' list at startup), `nav.ts` (the drawer's entries), `PluginPage.tsx` (the tabs) with `Operations.tsx`, `Settings.tsx`, `Accounts.tsx`, `Events.tsx`, `run.ts` (a call and its answer) and `confirm.ts` (which operations ask first). `@udl/api` gained `callNamed`, which calls an operation by the name the spec gives it through the generated routes; an operation the API has no id for runs on the plugin's own route (`method` + `path`), which is how a third-party plugin's operations work (the API's `callNamed` refuses a name it does not know, so the route is used instead). Tests first, piece by piece: each file's `.test` was written against `FakeUdl` and run red before its module existed — `spec.test.ts` (the list read, an operation's group and order), `load.test.ts` (`/ui/generated.json` read, a failure said not thrown), `nav.test.ts` (which plugins get an entry), `run.test.ts` (a route's path parameters split out, a GET's fields as its query, a refusal's message and hint), `confirm.test.ts`, `Settings.test.tsx`, `Accounts.test.tsx`, `Events.test.tsx`, `PluginPage.test.tsx` and `app.test.tsx`'s routes (`nav.test.ts` came with the review, for the drawer's rule); `web/e2e/generated.spec.ts` runs against `tests/support/e2e_plugins/fake.py` (a fake integration the harness loads), both themes. Three bugs the page found, each with the test that failed first: `SchemaForm` gave two forms on one page the same field ids (now `useId`; `SchemaForm.test.tsx`'s two-form case), the settings tab drew an empty form instead of what is stored, and a secret's mask was sent back on save (which would have overwritten it). Claude's review then found three more, fixed the same way (test red, then the code): Save sent every layered value (a file-only field is refused, and an unchanged one shadows the config file — only what changed is sent now), a read's fields were sent as a JSON body (`fetch` refuses one on a GET; the client builds the query for GET/HEAD/DELETE), and `/p/<plugin>/` said "there is no plugin named X" while the generated list was still loading or had failed (a spinner, then the error). Plus two nits in the connect form (`access` reset to nothing; an omitted `authorization_url` read as OAuth) and "Sign in" on a `none` form. A second commit made two of `tests/unit/support/test_e2e.py`'s own tests independent of whether this checkout's web tree is built.

**24.7 UI plugins guide the generated UI** · needs 24.6
- Build:
  - `UIManifest.generated` (all optional): `nav: {title, icon} | false`, `tabs: list[Literal["operations","settings","accounts","events"]]` (which to keep, in order), `hide: list[str]` (operation names or globs), `groups: dict[str, list[str]]`, `order: list[str]`, `labels: dict[str, str]`.
  - Three new contribution points, added to the closed set and to the SDK's prop types: `operationViews` (operation name → a mount that replaces that operation's panel; props `{operation, schema, run}`), `resultViews` (operation name or glob → a mount for its result; props `{operation, value}`), `fieldWidgets` (a widget name → a mount; props `{field, value, onChange}`).
  - Precedence, stated once in `docs/ui-plugins.md`: a contributed `settings` or `accountSetup` replaces that generated tab; a contributed route at a path wins over the generated page at that path; everything not replaced stays generated.
  - Bump `SDK_VERSION`'s minor.
- Tests first: `tests/unit/plugins/test_ui.py` (a `hide` glob matching nothing and an `operationViews` key naming no operation are problems, not errors); Vitest for precedence.
- Done when: the template plugin (18.10) hides one operation and replaces one result view in six lines of manifest.
- **Built (deepseek-a, 2026-11-21, PR #55):** `UIManifest.generated` — `nav` (a title and icon of its own,
  or `false` for no drawer entry), `tabs`, `hide`, `groups`, `order`, `labels` — validated in
  `udl/plugins/ui/base.py` (`guidance_problems`: a `hide` glob matching nothing and an `operationViews`
  key naming no operation are **problems, not errors**, so a page still opens and says what its guidance
  names that the plugin has not), served in `/ui/plugins.json` as `generated`, and read by the shell's
  `generated/guidance.ts` (`guidanceOf`, merging every manifest in order — the last to say something wins
  — then `guided()`, `tabsOfGuided`, `navOfGuided`). `SDK_VERSION` is 1.1.0 (the minor bump, with the
  `@udl/ui-sdk` package and `manifest.schema.json`). The three points joined the closed set with the SDK's
  prop types: `operationViews` (`{operation, schema, run}`), `resultViews` (`{operation, value}`) and
  `fieldWidgets` (`{field, value, onChange}`); the generated page draws them through the same
  `<PluginSlot>`, and `PluginPage` now takes `manifests` and `registry` (the `/p/$plugin/$` route passes
  the plugins store, which it was not doing — the first e2e run is what found it). A field asks for a
  plugin's widget with `x-udl-ui.widget`, so `FieldSpec` gained `asked` (the name, when it is not one of
  udl's own) and `SchemaForm` prefers `widgets[asked]` over `widgets[field.widget]`. Precedence is stated
  once in `docs/ui-plugins.md` (new; 21.4 grows it): what a plugin contributes replaces that part of the
  generated page and everything else stays generated. Tests: `tests/unit/plugins/test_ui.py` (the
  guidance is read whole, its problems, the drawer's `false`), Vitest `guidance.test.ts` (the merge, the
  tabs, the labels, the groups, the order, the drawer), `PluginPage.test.tsx` (8 precedence tests: a
  hidden operation gone, a label and a group applied, the tabs it names, the problems said, a contributed
  operation panel / result view / settings panel / account setup drawn instead), `widgets.test.tsx`,
  `schema.test.ts` and `SchemaForm.test.tsx` for `asked`, and e2e `generated.spec.ts` — "another plugin's
  manifest guides the page (24.7)", both themes. **Done when, met:** `web/packages/template`'s manifest
  hides one operation and contributes one result view in six lines (`"generated": {"hide": ["fake_ping"]}`
  and `"resultViews": ["fake_count_things"]`), drawn by `src/resultView.tsx`. **Two things to know:** the
  template is a UI-only plugin, so its guidance names the e2e fake's operations — the fake gained
  `count_things` and `ping` for it (guidance names operations, and the page it guides is udl's); and
  24.8's `generated-guided.spec.ts` is still the browser proof the phase card describes, with the shadcn
  example's own `resultViews`. **Red first:** the Python guidance tests in `test_ui.py` and the Vitest
  cases in `guidance.test.ts` were written before `guidance_problems` and `guidanceOf` and failed on
  them; the template's `resultView` test came after the coverage gate named the file uncovered (it is
  the test that found the gap, not one written with the design).

- **Review round (deepseek-a, 2026-11-21, PR #55):** six items, all test-first. (1) A global
  manifest's guidance reaches every page, so it is now checked against every operation udl knows and its
  problems are served on the bundle: `UIPluginInfo.problems` (`list[str]`, from `guidance_problems`
  against `ops.names()` for a UI-only bundle and the plugin's own for the others), `ui_plugins()` takes
  the operations registry, and the Plugins page says each one under the bundle's "What it adds".
  (2) `docs/ui-plugins.md` states whose page guidance reaches, that the plugin's own applies after a
  UI-only one's, and that `sdk: ^1.1` is what a manifest needs for guidance. (3) A group two manifests
  name now keeps both sets of members (`guidanceOf` appends rather than replaces). (4) The template's
  `sdk` is `^1.1`. (5) A point that replaces one thing draws only the last contribution: `<PluginSlot
  replace>`, on `operationViews`, `resultViews`, `settings`, `accountSetup` and `fieldWidgets`. (6)
  `tabs: []` (what udl serves for a guidance that names none) means all of them, on both sides, and
  `SettingsProps.save`'s doc says the shell leaves out a file-only field and an unchanged value. Tests:
  `test_ui.py` (a UI-only bundle's guidance against another plugin's operation is no problem; a name
  matching nothing is a problem on the bundle), `guidance.test.ts` (two manifests' members of one group;
  an empty `tabs`), `slot.test.tsx` (the last of two contributions to a replacing point), `PluginList.test.tsx`
  (the bundle's problems said) and `Settings.test.tsx` (a contributed panel's save sends only what
  changed, never the file-only field or the secret's mask). **Second round** (three items from the
  follow-up review, test-first again): (7) guidance is scoped as `docs/ui-plugins.md` says —
  `guidanceOf(manifests, plugin)` keeps a bound manifest for its own plugin's page only and applies it
  after the UI-only ones (so its labels and hides win), and `generatedNav` asks per plugin; (8) `replace`
  with `match="prefix"` takes the exact key, then the longest wildcard, then the last registered
  (`oneOf`), so a UI-only `resultViews: ["*"]` cannot take a plugin's own view; (9) the Built note above
  says which tests ran red first. Tests: `guidance.test.ts` (a bound manifest's guidance reaches its
  page only; its labels beat a UI-only one's; the UI-only ones come first), `nav.test.ts` (another
  plugin's guidance leaves this one's entry) and `slot.test.tsx` (the exact key over `gmail_*` over `*`;
  the longest wildcard when no key is exact).

**24.8 Proof** · needs 24.7
- Build: `web/e2e/generated-guided.spec.ts`: the shadcn example (18.10) gains a `resultViews` contribution for one fake operation; the rest of that plugin's page stays generated. Both themes.
- Done when: both e2e specs of this phase pass under `make e2e`.
- **Built (deepseek-a, 2026-11-20, PR #57):** `web/examples/shadcn` gains `resultViews: ["fake_list_things"]`
  in its manifest (and `sdk: ^1.1`), drawn by `src/resultView.tsx` — shadcn's card with the things as a
  list, in place of udl's table. It reads a fan-out list's answer as udl shapes it
  (`{items: [{account, item: {id, text}}]}`), and draws nothing it cannot read. `web/e2e/generated-guided.spec.ts`
  connects an account of its own, runs the list, and asserts shadcn's card is there, udl's table is not,
  the destructive operation is still udl's own form with its confirm field, and the template plugin still
  draws the count on the same page — both themes. **Red first:** the spec was written before the example's
  contribution and failed on `[data-testid='shadcn-list']`; the first fix still failed, which is what
  found that a fan-out list's answer wraps each thing with its account. Tests: `generated-guided.spec.ts`
  (both themes), `src/index.test.tsx` (the manifest↔entry guard now covers `resultViews`, the card's
  drawing, a thing from each of two accounts that give the same ids — keyed by account and id, as the
  first review asked — and every unreadable answer, at 100 % coverage of the file).

**24.9 planning.md amended** · needs 24.8
- Build: requirement **5d** becomes "A plugin without a UI plugin gets a generated UI from its operations, settings and accounts"; the "Without a UI plugin" bullet under UI plugins is rewritten to match; 20.7–20.10 note that each UI plugin now only builds what the generated page can't (the mailbox, the watch wizard) and guides the rest.
- **Built (deepseek-b, 2026-11-18, PR #29):** docs only. Requirement **5d** now reads "A plugin without a UI plugin gets a generated UI from its operations, settings and accounts" (refs 24.3–24.6, 8.1–8.3, 18.7); the "Without a UI plugin" bullet under *UI plugins (18.7–18.10)* says what the generated page is — `/ui/p/<plugin>`, its Operations/Settings/Accounts/Events, a request as `<SchemaForm>` and a response as `<ResultView>`, built from the same operations as the plugin's CLI/API/MCP adapters — and a new bullet beside it says a UI plugin *guides* the generated page (the manifest's `generated`, `operationViews`, `resultViews`, `fieldWidgets`) instead of replacing it; Phases 20.7–20.10 gain a note (a continuation of 20.10's line, not a task) that each builds only what the generated page cannot and leaves the rest generated; Phase 20's done-when and 19.5 (tracker and card) say a plugin without a UI plugin gets `/ui/p/<plugin>` rather than "no web UI". **Rebased after 24.8 (#57)** and checked against what was built: 24.6–24.8 are on `main`, and the wording matches `web/packages/shell/src/generated/guidance.ts` and the manifest's `generated`, `operationViews`, `resultViews` and `fieldWidgets`. The lease-TTL flake fix this started with is now its own PR (#30). Gates: `make check` green.

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
- **Built (deepseek-b, 2026-11-19, PR #19):** all of the above, stacked on 25.5 (its PR, "Merge after #15"), because the token operation is `mcp=False` and `OperationHints.mcp` comes from 25.5. Started by ernie-b (the resolver kind, `ResolvingStore` and the config tables), finished here: `op` in `onepassword.py` (its token in the child's environment only, `[secrets.onepassword] timeout` bounding it, `op read --no-newline`, `check()` from `op whoami` and `op vault list`), `open_resolvers()`/`open_secret_store()`, the `SECRET_RESOLVERS` kind in `list_plugins` (it needs `op`), the two operations and their CLI (`udl secrets status`, `udl secrets onepassword login`), the renderer, and the models (`SecretsStatus`, `ResolverStatus`, `SetToken`, `GetSecretsStatusRequest`). `ResolvingStore` joins the store contract (a `resolving` kind); `tests/unit/plugins/secrets/test_onepassword.py` fakes `op` as a script on `PATH`. The Done-when cannot be met as written (no `udl.ai` kind until 25.2), so `tests/integration/test_secret_references.py` proves it on the path an AI provider's `api_key` will take: a webhook action's secret header set to `op://vault-a/webhook/token` is sent resolved while the store holds only the reference. `SecretsStatus.Resolver` was dropped (a pydantic field, not a class alias) in favour of `ResolverStatus`. **Review round (deepseek-b, 19 Nov 2026, PR #19):** only a resolver's scheme is a reference, so a value with a URL's scheme (`https://…`, a `postgresql://` DSN) is a value udl keeps and reads back, not a refusal (`test_a_url_is_a_value_not_a_reference`); the cache holds only what a resolver answered, so a token another process wrote is seen at once rather than after `cache_ttl` (`test_a_value_the_store_holds_is_read_again_each_time`); `op` gets `HOME` as well as `PATH` (its config is there), tested by the fake's environment log; `op vault list`'s entries are read with a typed `_label()` instead of a `cast()`, and a vault with no name is its id; a resolver that is off is not checked at all (`ResolverStatus.ok` is `bool | None`, None for one udl does not read through, so a read-only MCP call runs no `op`); `_read` and `_allowed` share `_refused()`, so the refusal is said once; the module header's bottom rule is as long as its title; and the test that asserted `SecretStoreError` is named for what it asserts. **Claude's review (deepseek-b, 19 Nov 2026, PR #19):** vaults are allow-listed, so an empty `[secrets.onepassword] vaults` no longer means any: `allows()` matches the list only, a reference to a vault that is not in it is refused when saved (`test_with_no_vault_configured_no_vault_may_be_read`, `test_only_the_vaults_the_settings_list_are_allowed`), and `check()` with none allowed says so without running `op` (`test_a_check_with_no_vault_allowed_says_so_and_runs_nothing`: `ok=False`, `vaults=[]`, "no vault is allowed: add one to [secrets.onepassword] vaults"), so `udl secrets status` says it too. O-D5's two layers (the allow-list and the token's scope, O-D6) hold again. Round 2 (deepseek-b, 19 Nov 2026, PR #19): the saved-reference refusal is tested through `ResolvingStore.set` with the real resolver and `vaults=[]`, hint included (`test_with_no_vault_allowed_a_reference_is_refused_when_it_is_saved`), and the hint names the resolver's config table rather than its scheme (`SecretResolver.settings_table`, `onepassword` for `op`), which is what `[secrets.op]` used to say; the integration helper's `vaults or [...]` is `is None` too, so `vaults=[]` means none there as well.

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
- **Built (deepseek-b, 2026-11-19, PR #36):** `src/udl/plugins/ai/openrouter.py` (`OpenRouterProvider(OpenAICompatibleProvider)`,
  `OpenRouterOptions` with `url` defaulting to `https://openrouter.ai/api/v1`, registered as `openrouter` in `pyproject.toml`),
  `FakeOpenRouter` in `tests/support/ai_servers.py` (`/api/v1/models` with per-token prices, `/api/v1/key` with label, usage
  and limit, the same chats priced, usage sent for `usage: {"include": true}`), its contract case in
  `tests/contract/test_ai_provider_contract.py`, `tests/unit/plugins/ai/test_openrouter.py` (prices per million, context and
  tools, cost of a chat and of a stream, the key's label/limit/credit, 402 as `NotAllowed` "out of credit", 429 as
  `RateLimited` with its Retry-After, the `HTTP-Referer`/`X-Title` headers) and `tests/smoke/test_openrouter.py` (network, skipped
  without `UDL_OPENROUTER_KEY`: the real models still carry prices, the key still says what it has, a chat still says what it
  cost). `ModelInfo.pricing` (`ModelPricing`, USD per million), `Usage.cost` and `ProviderHealth.credit_left` are new, all
  optional. **The Done-when's `udl ai models` is 17.9's CLI (and `list_ai_models` its operation), not built yet:** the prices are
  proven through `models()` against the fake here, and the smoke test proves them against the real API.
  **Review round (deepseek-b, 19 Nov 2026, PR #36):** what is left on a key that resets is OpenRouter's own
  `limit_remaining` (`limit - usage` only when it says none, since `usage` is all-time); a key with a limit and no
  usage says its limit rather than "used None"; a variable-priced router (`"-1"`) and a non-finite price
  (`"NaN"`, `"Infinity"`) are no price, so one odd model does not lose the list; `as_money` (in `openai.py`) is the
  one helper both providers parse a price or cost with; `FakeOpenRouter` prices a copy of the answer, never a test's
  own; and every request — `/key` and `/models` too, not only a chat — is tested for its `HTTP-Referer` and
  `X-Title`. The new tests came first, in their own commit (`8f2b87a`). Round 2: a resetting key's detail
  says each period plainly (`"used 40 USD; 4 of 10 USD left"`), since all-time usage and a per-period limit are not
  the same period.

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

- **Built (deepseek-b, 19 Nov 2026, PR #15):** `udl/plugins/integrations/github/plugin.py` (the integration, its operations, its watcher) and `events.py` (its six event types), with its entry point in `pyproject.toml`. Accounts are `ConnectMethod.TOKENS` only; `identify()` calls `/user` and each repository and refuses a token that cannot write pull requests to one (a repository outside `repos` is refused by every operation). `api_url` is `FileOnly`. The six operations are the card's: `list_pulls` (fan-out over accounts), `get_pull`, `open_pull` (updates the open pull for that head), `comment`, `rerun_checks`, and `merge_pull`, which checks `confirm == owner/repo#number` and is `OperationHints.mcp=False`. **`OperationHints.mcp` is added here** (22.1's item, which had not landed): `False` keeps an operation off the MCP server (`interfaces/mcp/server.py`) and wraps it so it refuses `Caller.MCP` and `Caller.AUTOMATION` — a prompt-injected agent cannot reach a merge through an action either. The watcher polls each account every 60 s (`[integrations.github] poll_every`), reads a repository again only when its ETag says it changed (a 304), and publishes each event once (`opened`, `updated` on a new head, `checks_finished` with the conclusion, `commented` with author/body/is_bot, `merged`, `closed`; each carries `repo`, `number`, `head_sha`, `html_url` and a dedupe key). Its state is the ETags and a cursor per pull (head sha, checks and comments digests, state, `checks_done`), so a restart publishes nothing twice; a pull whose checks have not started is asked about while it is open, and one whose checks are done is not asked again. Tests: `tests/unit/plugins/integrations/github/test_plugin.py` (24), the client's ETag read in `test_client.py`, the integration contract (`tests/contract/test_integration_contract.py`, with a `github_case`), `tests/integration/test_github.py` (6: `udl accounts add github main` with its token on stdin, `udl github list-pulls`, `get-pull`, `comment`, a merge whose `confirm` names another pull, and a repository the account does not name, all against `FakeGitHub`), and `tests/interface/test_mcp_hints.py` for the hint. The card names `tests/integration/plugins/integrations/test_github.py`; udl's integration tests are flat, so the file is `tests/integration/test_github.py`. `tests/integration/test_servers.py` now compares the served MCP server's tools with the service's operations (a plugin's are tools too). Coverage of the plugin: 99 %. `make check`: 4647 passed. **Follow-up (deepseek-b, 19 Nov 2026, PR #15):** the watcher now takes a baseline on its first look at a repository (what is already there is not news: an automation is not told of every pull there ever was), and after it asks about each open pull's checks, its conversation and the comments on its diff with their ETags — three 304s when nothing happened, which cost no rate limit — so a rerun's verdict and a review comment are published although the pull itself did not change. A pull whose head moved while its checks were being read is left to the repository's own look. `github_list_pulls` pages with GitHub's own `next_page` (a number the caller gives back; a 0-based slice index it cannot), `github_open_pull` also moves an open pull to a `base` and makes it a draft, and `github.pr.merged` carries `merged_by`. The card's `base`, `title`, `body` and `draft` are optional in `OpenPullRequest`: a pull already open for that head is only changed by what the request names, and the base defaults to the repository's default branch. A repository is named as the account names it (GitHub's names are not case-sensitive). `github_merge_pull`'s `mcp=False` now also refuses an automation at save time (`fix(actions)` above, 22.1's card). Tests: `test_plugin.py` (31: the baseline, each event, a rerun's verdict, an unchanged pull costing no rate limit, a head that moved, the state saved), `test_client.py`'s three conditional reads, `pulls_page`, `update_pull`'s base and draft and a merge's `merged_by`; the contract's `Case` gained `settle` for a watcher that starts with a baseline. `make check`: 4660 passed. **Follow-up 2 (deepseek-b, 19 Nov 2026, PR #15):** the second review's three items. A repository's baseline is its first ETag, not the pulls it had, so the first pull opened in a repository that was empty at its first look is published. An ETag is one page's own, so the cursor remembers when a list took more than a page (`checks_paged`, `comments_paged`, `review_paged`) and reads such a list whole — and a page that is exactly full counts as paged too, since one more item starts a second page and leaves the first as it was; the conditional request's answer is the walk's first page, so nothing is fetched twice. The cost is a full read each poll for a pull with more than a page of comments or check runs: correctness over rate limit, while every pull under a page keeps its three free 304s. `update_pull` no longer sends `draft` (REST has no such field: converting to a draft needs GraphQL), the fake no longer honours one, and asking for a draft on a pull that is already open is refused with `NotAllowed` and a hint.

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
- **Built (deepseek-a, 2026-11-18, PR #22):** the kind, its contract and the fake.
  - `udl/plugins/runners/base.py`: `AgentRunner[O]` with `name`, `options_model`, `requires`, the
    `options`/`shell`/`clock` it is given, `health()`/`models()`/`start()`/`send()`/`stop()`, and
    `install()`/`login()` whose defaults refuse (`PluginError`: "cannot be installed by udl", "cannot
    sign in by itself") so 25.11 can add them where a harness allows it.
  - The models: `RunnerOptions`, `McpEndpoint`, `SessionSpec`, `SessionRef`, `RunnerHealth`,
    `RunnerModel`, and `AgentEvent` with `TextDelta`, `ToolStarted`, `ToolFinished`, `Usage`,
    `TurnEnded`, and the two that say a turn could not go on, `RunnerLimited` and `RunnerFailed`.
  - `RUNNERS` is in `PLUGIN_KINDS` (`udl.runners`); a runner in a plugin dir is found and named
    (`tests/unit/plugins/test_registry.py`).
  - `tests/support/runners.py`: `FakeRunner`, scripted per prompt (`script={prompt: turn}`), with a
    `count_sites` turn that asks udl as an agent's tool call would (it takes an `AsksUDL`, the one
    operation it calls, so a test needs no database) and a `wait_for_stop` turn that waits until the
    session is stopped; its sessions live in a `Sessions` store, not in the runner, so a session
    outlives the runner object as a real harness's does (two runners given one store share it), and
    a spec whose environment says `FAKE_HARNESS_LIMITED=1` is out of quota with a reset time. Each
    session has its own stop gate (stopping one lets that one's waiting turn go, not another's).
    `RecordingShell` is a real local shell that records every argv and every secret environment
    (a secret reaches the harness through the last, and is in no argv).
    `tests/support/fake_harness.py` is a program the real runners' tests point their binary at
    (`--version`, `auth status`, a stream-json turn, `FAKE_HARNESS_FAILS`, `FAKE_HARNESS_LIMITED`
    with its `resets_at`, and `FAKE_HARNESS_SLOW` so a stop can end a turn).
  - `tests/contract/test_runner_contract.py` over `fake`: named, its options model, its requires;
    health, models, start (the spec's cwd, the shell's host); a prompt's events ending in exactly
    one `TurnEnded`; a session resuming by id after the runner object is thrown away; `stop()`
    ending a turn that is running (the spec asks the harness to be slow, `FAKE_HARNESS_SLOW`, so a
    real runner's turn is still running when the stop comes); a rate limit as a `RunnerLimited`
    with its `until`, followed by
    `TurnEnded(LIMITED)`; secrets given as `secret_env` reaching the harness and in no argv; a
    stopped session saying
    `STOPPED` rather than hanging (and stopping twice is fine); and the refusing `install`/`login`.
    22 tests (11 of them the contract's); `udl/plugins/runners` at 100 %.
  - Decisions:
    - **`AgentEvent` is a base class, not a `type` alias**: the contract (and 26.x's consumer) asks
      `isinstance(event, AgentEvent)`, which a union alias cannot answer. Each kind is a subclass
      with its own `kind: Literal[...]`, so a discriminated parse is still one `Field` away.
    - **A `RunnerLimited`/`RunnerFailed` is followed by the turn's `TurnEnded`**, which is always
      the last event: `base.py`'s docstring and the two classes say so, and the contract asserts it.
    - **`login()` is `async def`**, unlike the card's `def login(self) -> Interaction`, because
      `HostShell.interact()` returns `Awaitable[Interaction]` (25.6), which a synchronous signature
      could not implement.
    - **`RunnerLimited`/`RunnerFailed` are events in the stream, not exceptions**: a turn ends with
      one of them rather than raising into the reader, so an orch turn (26.x) reads a reason
      whatever happened. `TurnEnded.reason` keeps `done|stopped|error|limited` as the card has it.
    - **A runner's settings table (`[runners.<name>]`) is not wired yet**: `RunnerOptions` documents
      it, and 25.11 (which adds install/login and the harness management) puts it in the config
      beside `[apps.<name>]` and `[integrations.<name>]`.

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
- **Built (deepseek-a, 2026-11-18, PR #27):** the runner, and the smoke test run against the CLI here.
  - `udl/plugins/runners/claude_code.py`: `ClaudeCode` (name `claude-code`, `requires` `claude`),
    published under `udl.runners`. `ClaudeCodeOptions`: `cli` (the program and arguments before the
    turn's own, so a test points it at `fake_harness.py`), `models` (`opus`, `sonnet`, `haiku`),
    `permission_mode` (`acceptEdits`).
  - A turn is one process: `claude -p --output-format stream-json --verbose --model … --permission-mode
    … --session-id <uuid>` (first) or `--resume <uuid>` (later), `--settings .udl/settings.json`
    (`{"permissions": {"deny": [...]}}`), `--append-system-prompt-file .udl/system-prompt.md` when the
    spec has instructions, `--mcp-config .udl/mcp.json --strict-mcp-config` when it has an endpoint. The
    prompt goes on stdin; the spec's `env` and `secret_env` go to the process.
  - The files are written through the host's shell (`sh -c 'umask 077; mkdir -p …; cat > …'`, the
    content on stdin), so a session on thevault is written there, never from the hub's disk; the
    worktree's `info/exclude` lists `/.udl/`. The MCP token is in that file and nowhere else (not an
    argv, not a log).
  - Its stream's lines become events: an assistant message's text (`TextDelta`) and tool_use
    (`ToolStarted`, summarised by its command/path/query), a user message's tool_result
    (`ToolFinished`, ok unless `is_error`), the result (`Usage`, then `TurnEnded`: done, error, or
    limited). A line udl does not know — a `rate_limit_event`, something new — is skipped, never fatal;
    a line it cannot parse is logged at debug. A limit is a result whose subtype or text says so, and
    `RunnerLimited.until` is the time its text gives — the `…|<epoch second>` form the CLI prints, or
    an ISO time (None when it gives none or an unreadable one).
  - `ClaudeCode(..., sessions=...)` takes a `ClaudeSessions` (its specs and the ids that have had a
    turn): one a service holds means a session outlives the runner object, and a runner made later
    resumes it with `--resume` rather than starting it again. A bare mapping still works, but it
    shares the specs alone (each runner object keeps its own first-turn state). The id is marked
    once the process has started, so a turn whose process never started is started again, not
    resumed.
  - `health()`: `--version` and `auth status` (`loggedIn`, `email` shown and never logged,
    `subscriptionType`); udl never opens Claude Code's own credential files.
  - `stop()` cancels the turn's pull task, which ends the process (the shell closes it when its
    generator ends) and the turn ends as `TurnEnded(stopped)`.
  - Tests: the runner contract's `claude-code` case, its CLI pointed at `fake_harness.py`, so the
    whole of it runs (the resumed session, the mid-turn `stop()`, the `RunnerLimited` with its
    `until`, the secret in no argv); 38 unit tests
    (the argv of a first and a later turn, a bare spec, the two files and the exclude, the token,
    every stream shape, a real 2.1.306 result line with nested usage, limits with and without a
    readable reset time, health's answers, a stopped turn, a turn nobody reads to the end, a process
    that cannot start, a session resumed by a second runner); `udl/plugins/runners` at 100 %. `tests/smoke/test_claude_code.py` (opt-in,
    `-m network`): a real `claude` answers one prompt in a temp directory — run here, it passes.
  - Flags were confirmed against Claude Code 2.1.306 on this machine (`--session-id`, `--resume`,
    `--append-system-prompt-file`, `--mcp-config`, `--strict-mcp-config`, `--settings`,
    `--permission-mode`): no change from the card.
  - Decisions:
    - **The turn's output is pulled by a task of its own**, so `stop()` can cancel it: closing an
      async generator that is being read raises, and cancelling the pull runs the shell's `finally`,
      which terminates the process. A failure the pull raises (the process could not start) is
      carried to the reader and becomes `TurnEnded(error)`.
    - **The stream is read with `stream()` here** (unlike the `command` runner): the CLI's result line
      says how the turn ended, so the exit code is not needed.

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
  - **Providers.** OpenRouter's key reaches OpenCode through the service's own API, `POST /api/credential` (`{integrationID, value: {type: key, key}, activate: true}`), read from udl's secret store (so it may be an `op://` reference); the variable's name says the provider (`OPENROUTER_API_KEY` → `openrouter`), and model ids then start `openrouter/`.
  - `tests/support/runners.py`: `FakeOpenCode`, an ASGI app serving those calls with the same shapes (validated against the committed OpenAPI document), Basic auth, and a log that a test can cut and resume.
- Tests first: the contract over `FakeOpenCode`; `tests/unit/plugins/runners/test_opencode.py`: the ruleset built from a `deny` list; the log resumed from the stored sequence after a dropped connection, with no event lost or repeated; a `permission.asked` rejected; the password in no log line.
- Done when: an opt-in smoke test runs one prompt on an OpenRouter model (ERNIE or DeepSeek) through OpenRouter, here and on thevault.
  - **The real run, here (2026-11-20):** `openrouter-ds-a/deepseek-v4.1-flash-1017-instant`, one
    prompt — `TextDelta("Hello there, friend.")`, `Usage(input 2828, output 9, $0.0003305932)`,
    `TurnEnded(done)`. (The free ERNIE models were out of their daily quota: `provider.rate-limit`,
    which the runner reads as a `RunnerLimited`.) **The thevault run is Claude's, at review**
    (2026-11-20): `opencode` is installed there at `~/.local/bin/opencode` — non-interactive SSH
    just has no `~/.local/bin` on `PATH` — and the supervisor runs the smoke test himself rather
    than having an agent SSH there.
- **Built (deepseek-a, 2026-11-19/20, PR #40):** the `opencode` runner.
  - `udl/plugins/runners/opencode.py`: `OpenCode` (name `opencode`, published under `udl.runners`),
    `OpenCodeOptions` (`cli`, `models`, `agent`, `task`, `title`, `directory`) and `OpenCodeSessions`
    (a session's spec, kept outside the runner so a runner made later resumes by id).
  - **The service, as the card says:** `opencode service status` and `~/.config/opencode/service.json`
    read through the `HostShell` on every connect, Basic auth (a Bearer header is refused), the port
    through `shell.forward()`. Health is `GET /api/info`; a service that is stopped, a `service.json`
    that cannot be read and a missing program are each a `RunnerHealth` problem, not an exception.
  - **A session:** `POST /api/session` with the ruleset, the location, the model (V2's
    `{providerID, id, variant?}`), the agent and `metadata.udl`. udl's MCP server is added with the
    token as `{env:UDL_AGENT_TOKEN}`; the token, the spec's `env` and its `secret_env` go to
    `PUT /api/session/{id}/environment`, so none is in a file or an argv.
  - **A turn — a correction to the card's plan, from the real run.** The stream is subscribed
    (`GET /api/event`, filtered to the session) **before** `POST /api/session/{id}/prompt`
    (`delivery: queue`) is sent: the stream has no replay, so a prompt sent first loses a fast
    turn's own events. The card's durable log (`/api/experimental/session/{id}/log`) **replays
    nothing in 2.1.2** — a real turn read with `after=0`, `after=1`, `after=12` and no `after`
    answers only `log.synced`, while the same turn's events carry `durable.{aggregateID,seq}` on
    `/api/event`. That makes `/api/event` the source and the session's own `outcome`
    (`GET /api/session/{id}`) the backstop: a stream that drops is opened again, and one that
    brings nothing asks the session how the turn ended — after **every** read, and every
    `_ASK_EVERY` (5 s) of *other* sessions' events, because a busy host's stream never times out
    while this turn's own events are gone. The session's `outcome` is its **last** turn's, so it
    is taken only when the session's `time.idle` (snapshotted before the prompt) has moved on:
    `session.execution.started` is not used as a shortcut, since the service does not clear
    `outcome` as a new turn starts and the event itself is volatile — a test keeps the previous
    `succeeded` in place for a turn that says nothing, and the turn is not ended by it; another
    leaves `failed` behind for a turn that succeeded with its events off the stream, and that turn
    ends as done — only the session's own outcome says so. A
    turn that keeps sending events is never ended by silence, however long it runs (30 reads with
    nothing at all, an hour, is a turn given up on). A drop can lose text — the stream is
    volatile — but never the end of the turn. `session.text.delta` streams a `TextDelta`, and
    `session.text.ended` is not repeated when its deltas were read;
    `session.tool.*` are `ToolStarted`/`ToolFinished` (the tool's name is in the provider's own
    blob), `session.usage.recorded` / `.updated` are a `Usage`, and `session.execution.succeeded` /
    `.interrupted` / `.failed` end the turn (done / stopped / error). A failure that says the account is out of
    quota — `status: 429`, or a `retryAfterMs` — is a `RunnerLimited(until)` and then
    `TurnEnded(limited)`. A `PluginFailed` mid-turn (a stream udl cannot read) is `TurnEnded(error)`,
    never an exception out of `send`.
  - **Permissions — a correction to the card's plan.** The session's rules use V2's action names
    (`shell`, `edit`, `external_directory`, `question`); `tool.use` is a *policy's* action (25.11's
    hard stops), not a session rule's, so the ruleset is `*/* allow`, then a `shell` deny per
    `SessionSpec.deny`, then `external_directory * deny`, `edit <cwd>/* allow`, `edit * deny` and
    `question * deny` (last match wins). A permission is **not in the durable log** (the log has no
    `permission.*` events, checked against the committed OpenAPI document), so while a turn runs its
    pending requests are read (`GET /api/session/{id}/permission`) and each is answered `reject` —
    and once more when the turn ends, so a fast turn's request is not left hanging.
  - **The brief and the secrets.** `SessionSpec.instructions` is written to the host's own agent file
    (`~/.config/opencode/agents/udl-<sha256[0:12]>.md`, through the shell with the text on stdin, so
    it is in no argv) and the session runs as that agent — the card's "pass the brief as the agent's
    instructions", as OpenCode's own agent file. `secret_env` is sent at session start
    (`PUT /api/session/{id}/environment`) and **not** kept in the runner's store: it is in the
    service's memory, and a runner made later resumes by id without it.
  - **Fixtures and tests:** `tests/fixtures/opencode/openapi-2.1.2.json` (the document the service
    serves), `tests/fixtures/opencode/events-2.1.2.json` (one real turn's events, exact payloads), `tests/support/opencode.py` (`FakeOpenCode`, an ASGI app of those calls with Basic auth,
    a log a test can cut, pending permissions, and the contract's slow/limited turns through
    `FAKE_HARNESS_SLOW`/`FAKE_HARNESS_LIMITED`; `FakeShell`, its shell), the contract's fourth case,
    57 unit tests and `tests/smoke/test_opencode.py` (opt-in, `make smoke`). The smoke test runs one
    real turn in the directory the suite was started from (OpenCode's service is per directory) on
    the first OpenRouter model the host has, or `OPENCODE_SMOKE_MODEL`; a model whose free daily
    quota is spent is a skip that says so, and so is a host with no OpenRouter model at all.
    **It passes here** with
    `OPENCODE_SMOKE_MODEL=openrouter-ds-a/deepseek-v4.1-flash-1017-instant` (the free ERNIE models
    were out of quota). A test reads the real-turn fixture and asserts the `TextDelta` / `Usage` /
    `TurnEnded` it becomes. A test checks that every path the runner calls is a path in the
    committed document. `docs/plugins.md` gains the runner row in the contract-suite table.

**25.10 `command`** · needs 25.7
- Build: `udl/plugins/runners/command.py`: any CLI as a runner. Options: `argv` (a template with `{model}`, `{session}`, `{cwd}`; the prompt on stdin), `resume_argv`, `output: text | jsonl`. `argv[0]` must be listed in `[orch] allowed_commands` (file-only), as 17.3's provider commands are. Output lines are `TextDelta`s; exit 0 is `TurnEnded(done)`.
- Tests first: the contract over a tiny script.
- Done when: `echo`-like and failing commands both end a turn correctly.
- **Built (deepseek-a, 2026-11-18, PR #25):** the `command` runner.
  - `udl/plugins/runners/command.py`: `CommandRunner` (name `command`, published under `udl.runners`
    in pyproject, so `udl plugins list` shows it). `CommandRunnerOptions`: `argv` (the template),
    `resume_argv` (a later turn's, when it differs), `output: text | jsonl`, `models`.
  - `{model}`, `{session}` and `{cwd}` are filled in by plain replacement, not `str.format`: a
    program's own arguments often hold braces (a JSON config, a Python one-liner) and must be
    passed as they are.
  - The prompt goes on stdin, the spec's `env` and `secret_env` go to the program (a secret never in
    an argv), and the session's cwd is where it runs. Its output lines are `TextDelta`s; with
    `output = "jsonl"` a line that is a JSON object is read for its `text`, and one that is not is
    said as it is (never fatal), while a line that says the account is limited (`limited`, with
    `resets_at`) ends the turn as `TurnEnded(limited)` after the `RunnerLimited` that says when it
    lifts. Exit 0 ends the turn as `TurnEnded(done)`; anything else ends it as `TurnEnded(error)`
    with what the program said; `stop()` cancels the run, and the turn ends as `TurnEnded(stopped)`.
  - `CommandRunner(..., sessions=...)` takes the mapping it keeps its sessions in: one a service
    holds means a session outlives the runner object, and a runner made later resumes it by id (the
    contract's resume case, which every runner has to pass).
  - `[orch] allowed_commands` (`OrchSettings`, file-only, as `[ai] allowed_commands` is): a program
    not in it is refused by `start()` (`NotAllowed`) and `health()` says why, without running it.
    The runner takes the list when it is made (`CommandRunner(..., allowed=...)`), since 26.x builds
    runners from the settings.
  - Tests: the runner contract now runs over `command` as well as `fake` — all 11 of its cases,
    including the resumed session, the mid-turn `stop()`, the `RunnerLimited` with its `until` and
    the secret that reaches the program and is in no argv; 17 unit tests (stdin, the template,
    `resume_argv`, jsonl and unreadable lines, a limit line, a second runner resuming, a failing
    command, a stopped turn, the refusal, the health, models, an empty line); `[orch]
    allowed_commands` in the config's tests. `udl/plugins/runners` at 100 %.
  - Decision: **a turn is read with `run()`, not `stream()`** — the shell's `stream()` gives no exit
    code, and the card makes exit 0 the turn's outcome. So a command's turn arrives when it ends
    rather than line by line; live output needs a `stream()` that says how the program ended (a
    change to 25.6's interface, for 25.11 or a follow-up).

**25.11 Managing harnesses on every host** · needs 25.8, 25.9
- Build: `udl/plugins/runners/manage.py`, and each runner's `health()`, `install()` and `login()`.
  - `RunnerHealth`: `installed`, `version`, `latest: str | None`, `signed_in`, `account: str | None`, `plan: str | None`, `limited_until`, `guards: bool` (the deny policies of 25.9 are in place; always true for runners that enforce them per session), `problems: list[str]`.

    | | `claude-code` | `opencode` |
    |---|---|---|
    | status | `claude --version`, `claude auth status` (JSON), `claude doctor` for `problems` | `opencode --version`, `opencode service status`, `GET /api/info`, `opencode auth list --format json` |
    | install / update | `claude install [stable\|latest\|<version>]`; `claude update` | `opencode upgrade [<version>]`, then `opencode service restart` only while no session is running on that host |
    | sign in | `claude auth login`, relayed | a provider key: `POST /api/credential` from udl's secret store (never a command); an OAuth provider: `opencode auth login <provider>`, relayed |
    | sign out | `claude auth logout` | `opencode auth logout <provider> <account>` |

  - **Relayed sign-in** (O-C12): `login()` returns the shell's `Interaction`. `orch_runner_login` (`host`, `runner`, `provider?`) starts it and answers a `LoginAttempt` (`id`, `prompt`: the text and the URL the harness printed, `expects: none | code`); `orch_runner_login_reply` (`id`, `text`) writes a line to it; `orch_runner_login_status` says whether it finished. Attempts live in memory, end after 10 minutes, and one runs per (host, runner). Where a harness's login can't be driven without a terminal, the answer is the exact command to run in an SSH session on that host; say so in the phase's decisions.
  - `limited_until` is what the dispatcher last saw (27.7), per (host, runner): two agents on one machine's Claude login share its limit, and an agent on another machine doesn't.
  - Installing and updating run **only a fixed argv per runner** with a version matching `^[0-9A-Za-z][0-9A-Za-z.\-]*$` (an alphanumeric first character, so `-x` and `--help` are refused); there is no "run this command on a host" operation.
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
- **Built (deepseek-a, 20 Nov 2026, PR #42):** `udl/plugins/runners/manage.py` (`Harnesses`: the manager every harness operation goes through) and the seven operations on `orch`, `udl orch runners` in the CLI and `/plugins/orch/...` on the API.
  - **`RunnerHealth`** gained the card's fields: `account`, `plan`, `guards` and `problems` beside `installed`, `version`, `latest`, `signed_in` and `limited_until`. `claude-code` fills `plan` from `claude auth status`'s `subscriptionType` and `problems` from the `missing:` lines of `claude doctor`; `guards` is `True` (its deny list is a settings file per session, so there is nothing on the host to check). `opencode` fills `account` from `opencode auth list --format json` (a bare list or `{"items": [...]}` — 2.1.4 prints the former; **`signed_in` means it has an OpenCode credential there, not a provider account**: its service's password is not one), and `guards` by reading its `opencode.json` back through the shell and checking both deny resources are in its `policies`.
  - **The base contract** (`AgentRunner`) gained `setup(roles, env) -> list[str]` (the files it wrote) and `logout()`; `login(provider)` now takes the provider. All three refuse with `PluginError` unless a runner implements them, and the runner contract test now says a runner either manages itself or says it cannot (it never runs an install in the contract: `install()` is only checked to be its own iterator).
  - **`claude-code`**: `install([version])` → `claude install <version>` / `claude update` (its own output streamed back); `login(provider)` → `claude auth login` (no provider argument: it signs into the account); `logout()` → `claude auth logout`; `setup()` writes nothing and says so (guards are per session). `problems` comes from the lines of `claude doctor` that *name* one (so "No errors found" is not a problem), and the doctor gets 20 seconds: it is an interactive screen, and one that does not end is no problem rather than a hang.
  - **`opencode`**: `install([version])` → `opencode upgrade [<version>]`, then `opencode service restart` (the manager refuses while a session runs there); `login(provider)` → `opencode auth login <provider>`, refused with `NotAllowed` when no provider is named (without one the CLI opens a picker no relay can drive); `logout()` → `opencode auth logout <provider> <account>` for every sign-in `auth list` names (the card's argv: the account goes, never a guess at its provider); `setup(roles, env)` **reads `~/.config/opencode/opencode.json` first and merges** udl's two deny policies into it — every provider, model and MCP server the host already has survives, and a second setup changes nothing — then writes one `~/.config/opencode/agents/udl-<role>.md` per role (`mode: primary`, `steps: 200`, and a brief per role: an implementer is told it never merges, a reviewer is not) and stores each provider key as a service credential (`POST /api/credential`, never an argv).
  - **Sign-in (O-C12)**: `Harnesses.login` starts the runner's `Interaction`, answers the first thing it printed as the attempt's `prompt` (`expects: "code"` only when it asks for one: `paste the code`, `enter the code`, not the words "opencode" or "Claude Code"), and watches it in a task under the clock's ten-minute timeout; `reply` writes a line, `status` says how it is going, and a timed-out attempt is closed and reported `ok: False`. One attempt per (host, runner), and a finished attempt is dropped when the next one starts. A harness that cannot be driven without a terminal raises `LoginByHand` (its argv: `sign_in_command`), and only that is answered as a command — `done, ok: False`, `command` set to the exact command to run on the host; any other error is udl's own and goes to the caller.
  - **Install safety**: only a runner's own argv, a version matching `^[0-9A-Za-z][0-9A-Za-z.\-]*$` checked before anything runs, and no update while `Harnesses` knows a session is running there (`started`/`ended`, which 27.7's dispatcher calls). There is no "run this command on a host" operation.
  - **A runner's options** come from `plugin_settings` when it declares a settings model, else from its config-file table (`[integrations.<runner>]`), so `udl orch runners` works before any runner has one.
  - Tests: `tests/unit/plugins/runners/test_manage.py` (45: every status field, an install per runner, the output kept by characters, a bad version refused before anything runs, an update refused while a session runs, a setup's policies and agent files, a provider key kept out of every argv, a sign-in relayed end to end, one attempt per host and runner, a finished attempt forgotten, a timed-out attempt ended, a prompt that names the harness but asks for nothing, a sign-in answered with its exact command, a misconfigured or unreachable runner keeping its row with its problem in its health, a sign-out that names what it signs out, and the operations' names, CLI paths and hints), plus new health/install/setup/login/logout cases in `test_claude_code.py` and `test_opencode.py` (a clean doctor, a doctor that never ends, a config merged not replaced, a second setup, a role's own brief, a key that is not a variable name, a sign-in with no provider), and `tests/support/harnesses.py` (`ScriptedShell`, which answers by argv, keeps files written through it and hands back a `ScriptedInteraction`). The runner contract test's install/login case was rewritten (a runner either manages itself or says it cannot).
  - **Not run here:** the card's Done-when against `thevault` (a real `claude` on both machines) is Claude's check; this worktree's tests drive scripted harnesses.
  - **The group's own path runs its operation (Claude's Done-when, 20 Nov).** `orch_list_runners`' CLI path *is* the `orch runners` group, so `udl orch runners` printed the group's help and the table needed a subcommand that is not one. `cli_groups` and `add_commands` now give a group whose path is also an operation's own a callback (`_own_paths`, `cli_command(..., only_alone=True)`, `invoke_without_command`, `no_args_is_help=False`), so `udl orch runners --host local` is the listing while `udl orch runners install …` is still its subcommand and `--help` still lists them. Tested with the real CLI (`CliRunner` on udl's app, a scripted host) and with the gizmo plugin (`tests/interface/test_generated.py`: `udl gizmo things --count 3` runs `every_thing`, and `udl gizmo things find` prints only the find).
  - **A timeout is that row's problem, never the listing's (Claude's Done-when, 20 Nov).** One run crashed with an unhandled `TimeoutError` from a shell's `asyncio.wait_for` (`DEFAULT_TIMEOUT`, 30 s): a slow host or harness took the whole `udl orch runners` down. `_ASK_FAILED` now has `TimeoutError`, so each of the three calls (`runner()`, the health check, `models()`) turns it into its own row's `health.problems` — `_why()` names it as a timeout with the time it was given ("it did not answer in 30s"), since a timeout carries no message of its own — and every other row is still listed. `ScriptedShell.never(argv)` makes a command that raises it, and `test_a_harness_that_never_answers_keeps_its_row` checks the row and its neighbours.
  - **Every harness keeps its row (Claude's Done-when, 20 Nov).** A harness that could not be asked used to be dropped into `RunnerStatusList.problems` and vanish from the table (opencode on `local` in Claude's run). `Harnesses._checked` now returns a health for every host and runner: one udl cannot make (`ConfigError`: its settings do not fit it), one whose doctor raises, and one whose `models()` cannot be listed all keep the row, and `health.problems` says which call failed. `RunnerStatusList.problems` stays (empty) for the API's sake. Reproduced here with `cli = ["no-such-opencode"]` in `[integrations.opencode]`: the row now reads `installed: false`, `problems: ["its models: opencode's models cannot be listed: no-such-opencode is not installed"]`. **Why opencode was missing in Claude's run was not reproduced** (a real run here lists opencode 2.1.2, signed out, 35 models, `problems: []`, with the normal and with a minimal `PATH`); a transient `service status`/`/api/model` failure and a `cli` udl cannot find both took that path, and both now show in the row instead of dropping it.

**Decisions made while building 25.11:**

- **A sign-in udl cannot drive is answered with a command, never a failure.** `LoginByHand` (a
  `PluginError` carrying an argv) is the one case the relay answers as `done, ok: False` with
  `command`; every other error is the caller's. A CLI udl does not know (`command` runner) names
  its program, so the answer is `my-harness` — the exact command to run in a shell on that host.
- **A host's `opencode.json` is merged, never replaced.** Setup reads it, adds the two deny
  policies if they are missing and writes every other key back; a second setup changes nothing.
  `/etc/opencode/opencode.json` (which a project cannot widen) is *not* written: udl may not be
  root on that host, and the file is not there yet, so the user's global config is where the
  policies go until a host has a system one.
- **A setup replaces the credential it wrote.** Its id is `udl-<provider>`: a second setup's
  `POST` is a 409, so udl deletes that id and posts again — one credential per provider, the
  newest key, and no stale secret left behind. Its failure never repeats the answer's body (a
  service that echoes what it was sent would put the key in udl's log).
- **A provider key is a credential on the service, never a command.** `opencode service set env`
  takes the value in its argv, so it would show in the host's process list; `POST /api/credential`
  (OpenCode 2.1.2's own API, in the committed OpenAPI document) stores it instead, and `activate`
  makes it the one sessions use. The key is in no argv on either machine, and it is set at setup,
  not at every session. Which provider a variable is for comes from the service itself
  (`GET /api/integration`, each one's `EnvMethod.names`), never from its name: `TOGETHER_API_KEY`
  is `togetherai`; a variable the service does not list is refused, naming what it does, and one
  two providers take is refused too (storing it under whichever came last would be the wrong
  provider).
- **A runner's quota is the dispatcher's to record:** `Harnesses.limited(host, runner, until)` is
  what 27.7 calls; `RunnerHealth.limited_until` reads it back. Nothing polls a provider's quota.
- **`claude doctor` is an interactive screen:** it gets 20 seconds and no terminal, and one that
  does not end in time is no problem, so a listing never hangs on a host.

**25.12 `codex`** · needs 25.7, 25.11 · size L (O-D16) · optional (Q-9)
- Build: `udl/plugins/runners/codex.py`, the runner for OpenAI's Codex CLI signed in with the user's ChatGPT plan: the twin of `claude-code` (25.8). Everything goes through the agent's `HostShell`, so each host has its own install, its own sign-in and its own limits.
  - **Read the installed version first,** as 25.9 did for OpenCode: `codex --version`, `codex exec --help`, `codex exec resume --help`, `codex login --help`, and the `--json` output of one real turn. Commit them as `tests/fixtures/codex/<version>/` (the help texts and `events.jsonl`). The argv and the parser are written against them, and a test fails when a flag the runner passes is missing from the help text. The flags below are what to check for, not a promise.
  - **A turn is one process,** as in 25.8. The first turn: `codex exec --json --model <model> --cd <worktree> --sandbox workspace-write -c approval_policy="never" -c sandbox_workspace_write.network_access=true -`, with the prompt on stdin. A later turn: `codex exec resume <session id> --json -`. The session id is the one the first event (`thread.started`) gives, and it is the `SessionRef`. The instructions (the fixed rules, the project's and the agent's brief) open the first prompt, unless the installed version has a config key for extra instructions: then use it, and say which in the card.
  - **Events:** the JSON lines become `AgentEvent`s.
    - `thread.started` gives the session.
    - `item.started`/`item.completed` map by the item's type: `agent_message` is text; `command_execution`, `mcp_tool_call` and `file_change` are `ToolStarted`/`ToolFinished`; reasoning is skipped.
    - `turn.completed` with its `usage` (input, cached input, output) is `TurnEnded`.
    - `turn.failed` and `error` are `RunnerFailed`; a usage-limit error is `RunnerLimited`, with the reset time when it gives one.
    - An unknown type is skipped, never fatal.
    - The ChatGPT plan has no cost per turn (`cost = None`); a session on an API key (25.13) is priced from udl's model prices where it has them.
  - **udl's MCP server** is added per turn with `-c` keys: a streamable HTTP server at `<hub>/mcp` whose `X-API-Key` header comes from an environment variable (`env_http_headers`). The token is in the process's environment, through the shell, never in `argv` or a file. If the installed version can't take a header from the environment, use udl's stdio MCP server instead (`udl mcp`, with the token in its environment), and say so in the card.
  - **Hard stops.**
    - Codex's sandbox keeps writes inside the worktree.
    - `git push` and `gh pr merge` are refused by Codex's own command rules (`prefix_rule(pattern=[…], decision="forbidden")` in a rules file under `~/.codex/rules/`). 25.11's `setup()` writes them, and `health()` reports them as `guards`.
    - On a version without command rules, `guards` is false, and the dispatcher won't start an implementer on that host (25.9's rule). A reviewer, a manager or a chat (27.11) still may.
  - **Managing it:** a third column in 25.11's table.
    - **Status:** `codex --version` and `codex login status` (whether it is signed in, and with ChatGPT or an API key: the `plan`).
    - **Install and update:** the official npm package at a checked version (`npm install -g @openai/codex@<version>`), refused while a session runs on that host.
    - **Sign in:** `codex login --device-auth`, relayed (O-C12: the page shows the link and the code it prints). With an API key (25.13): `codex login --with-api-key`, the key on stdin from udl's secret store.
    - **Sign out:** `codex logout`.
    - **The auth file:** **udl never opens `~/.codex/auth.json`** (O-C3's rule, for OpenAI's plan too).
    - **Models:** the CLI lists none, so the runner's options name them (`models = […]`), and `orch_list_runners` shows those.
  - `FakeCodex` in `tests/support/runners.py`: a script that prints the recorded events for a prompt and resumes by id.
- Tests first: the runner contract over `FakeCodex`; `tests/unit/plugins/runners/test_codex.py`:
  - the argv of a first and a later turn, with no token and no key in either;
  - each recorded event type mapped, and an unknown one skipped;
  - a usage limit;
  - the guard check with and without the rules file;
  - `login status` read for both kinds of sign-in;
  - `auth.json` never opened (a test host that logs every read).
- Done when: `udl orch runners` shows `codex` on `local`, signed in with the ChatGPT plan, and an opt-in smoke test runs one real turn that calls `get_status` over udl's MCP.

**25.13 OpenAI by API key** · needs 25.2, 25.9, 25.11 · size M (O-D16)
- Build:
  - `udl/plugins/ai/openai.py`: `OpenAIProvider(OpenAICompatibleProvider)`, named `openai`.
    - `url = "https://api.openai.com/v1"`; the key comes from the secret store, so an `op://` reference works (O-D5).
    - `models()` comes from `GET /models`, and `health()` is the same call. OpenAI has no credit endpoint, so `credit_left` is `None`.
    - A 429 is `RateLimited`, with `Retry-After`. A 401 is `NotAllowed` ("OpenAI: the key was refused").
    - `FakeOpenAI` goes beside `FakeOpenRouter`.
  - OpenCode gets the key the way it gets OpenRouter's (25.9): `POST /api/credential` with `OPENAI_API_KEY` from the secret store, sent by `orch_runner_setup`. Its model ids then start `openai/`.
  - Codex on a host can be signed in with the key instead of the plan (25.12).
  - So an agent of any role, and a chat (27.11), can run an OpenAI model: through `opencode` with an `openai/…` model, or through `codex`.
- Tests first:
  - the AI provider contract over `FakeOpenAI`;
  - the key in no `argv` and no record;
  - `orch_runner_setup` sends both providers' keys when both are set.
- Done when:
  - `udl ai models --provider openai` lists models against the fake, and an opt-in smoke test does against the real one;
  - an `opencode` agent on an `openai/…` model passes the runner contract against `FakeOpenCode`.

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
- **Built (`ernie-c`, 19 Nov 2026, PR #33):** `udl/plugins/apps/orch/` (`plugin.py`, `models.py`, `records.py`, `settings.py`) and the `orch` app (`AppPlugin`) it is. `models.py` is what the operations will take and answer: `Project` (name, title, description, `merge: human | agent`, `promotion`, `review_bot`, `rules`) and `Repo` (project, name, github `owner/repo`, `account` as a `github:<name>` account, base branch, the `gate` argv, `checkouts` by host, `branch_pattern`), with `branch()` rendering a task's branch and `slugify()` its tail. A repo's branch pattern is checked where it is read: it may only name `{type}`, `{key}` and `{slug}`, each once, and the fixed text must read as one git ref (so `feat/{key}-{slug}/x` and `{type}/{name}-{slug}` are refused). The `gate` is an argv: an empty one, an empty argument or one with a space in it is refused, rather than run as a shell line. `records.py` holds the two records (`orch_project`, `orch_repo`, both `config`, both indexed as the card says; a repo is unique on `(project, name)` and looked up by `github` too), each with an `example()` the record contract round-trips, and `ProjectRecords`/`RepoRecords` over an app's `AppRecords`: saving a repo whose project is not there is `DoesNotExist`, and deleting a project deletes its repos. The records' fields are typed with the shapes' own aliases (`GitHubSlug`, `GitHubAccount`, `BranchName`, `HostName`, the checked `BranchPattern`, the `Gate` argv), so a record refuses exactly what a shape refuses — one rule, in one place. `plugin.py` is the app: named `orch`, titled, `settings_model` `OrchSettings`, `record_kinds()` the two, no operations (they are 26.6's) and a service that waits on `ctx.stop` until the dispatcher (27's) has work for it. Tests: `tests/unit/plugins/apps/orch/test_projects.py` (72: the shapes' fields and refusals, the branch pattern, the two records' examples and that shape and record agree field for field, and storing over an in-memory database — read back, duplicates refused, a repo without a project, two projects with a repo of the same name, deleting). `tests/contract/test_app_contract.py` gained orch as a second case, so the app contract runs over it too. Nothing existing changed behaviour. The narrow suites pass (`tests/unit/plugins/apps`, `tests/contract/test_app_contract.py`, `tests/unit/plugins/test_registry.py`, `tests/unit/db/test_records.py`, `tests/unit/test_package.py`: 322, with 98 % coverage of `models.py` and 97 % of `records.py`; `tests/contract/test_record_contract.py -k orch`: 8, the two examples through every database plugin), and pyright/ruff are clean. The two commits are the failing tests first, then the code that makes them pass.

**26.3 Features and tasks** · needs 26.2
- Build:
  - `Feature`: `project`, `key` (unique in the project; `F-<n>` from a per-project counter unless given), `title`, `spec` (markdown), `status: planned | active | done | parked`, `order: int`.
  - `Task`: `project`, `feature` (the feature's id; **required**), `key` (unique in the project; `<feature key>.<n>` unless given), `title`, `spec` (markdown), `kind: build | human | review`, `state` (the table under Concepts), `needs: list[str]` (task keys), `repo: str | None`, `assignee: str | None` (an agent's name, or `human:<name>`), `position: int | None` (its place in the assignee's queue), `hold: str | None` (a reason: built now, merged later), `blocked_on: str | None` (a question's key or free text), `branch`, `worktree`, `updated_by`.
  - `transitions.py`: `TRANSITIONS`, a table of `(from_state, to_state) → allowed roles`, and `move(task, to, by: Principal) -> Task`, raising `NotAllowed` naming the rule. A feature's `status` follows its tasks unless set by hand: `active` once one is past `open`, `done` when all are `done` or `cancelled`.
  - `Counter` record for keys (`F-`, `Q-`, `A-`), claimed through `claim_lease`-style compare-and-set so two callers never get the same number.
- Tests first: `tests/unit/plugins/apps/orch/test_transitions.py`: the phase's first bullet, generated from the table itself plus the refusals.
- Done when: no code path sets `Task.state` except `move()`.
- **Built (`ernie-c` and `deepseek-a`, 19 Nov 2026, PR #38):** `Feature` and `Task` in `orch/models.py` and as records (`orch_feature`, `orch_task`, unique on `(project, key)`), `FeatureRecords`/`TaskRecords` over an app's `AppRecords`, and the `Counter` their keys come from. A feature or task of a project that is not there is `DoesNotExist`, and so is a task whose feature is not (a task always links back to one); deleting a feature takes its tasks with it and deleting a project takes its repos, features, tasks and counters; a feature's tasks are read in key order, tasks by feature, state and assignee (state is filtered in udl, not a column: it is a Literal, and a database indexes plain types). **Keys:** `create_feature`/`create_task` claim the next `F-<n>` or `<feature key>.<n>` from the project's counter unless the caller gives one (an imported `22`, or `26.3`). **A feature's status follows its tasks** (`active` once one is past `open`, `done` when all are `done` or cancelled, `planned` while they are all open) and a `parked` feature is what a person set by hand, so it is left alone. **The counter is a claim a row, not a running total:** `(project, prefix, last)` is its unique key, so the database's uniqueness is the compare-and-set — two callers that read the same last number both try to store the next one and the one that loses is told (`DuplicateKey`) and reads again — and only the last claim is kept, so reading it is one row however long a project has been counting. `Counters.claim(project, prefix)` hands out 1, then 2; `latest()` is the last one handed out. `orch.transitions`: `TRANSITIONS`, the `(from, to) → the roles that may` table over the Concepts table, and `move(task, to, by=Principal, note=)`, the one code path that sets `Task.state` — `TaskRecords.move()` is its one store, and `save_task()` refuses a task whose state differs from the stored one (a new one is `open`), so the card's done-when holds. A person may move a task anywhere (the rules are for the agents), and **a person is a person by its `kind`, not its name**: an agent called `human:x` is held to the agents' rules. An agent moves only its own task; udl itself (the dispatcher assigning, the watcher merging) and the reviewer (who reviews others' work) may move any, still bound by the table; the `role` a caller says is what it is *doing*, never a permission, so naming `cancel` does not make another agent's task yours. Only a reviewer or a human reaches `done` (O-C7), checked before ownership so the refusal names the right rule; a move to the state a task is already in is refused, and one to a state there is not names the states there are; a caller that says no role is given the table's own first agent verb, so it is never offered a verb the table would refuse. A move sets `blocked_on` from its note (and clears it on the way out) and leaves `hold` alone: a hold is why a task is held (built now, merged later), and why a cancellation or a move happened is a note (26.5). `STATE` and `ROLE` are read from the `State` and `Role` literals (one list of each), and `STATE_MARK` is the mark each state imports from and prints as (`[ ]`, `[!]`, `[r]`, `[~]`, `[x]`, `[m]`, none for cancelled). Names: **a task's `kind` of work is `task_kind`**, in the shape and the record alike, because a record's `kind` is the name of its kind and a field may not shadow it. `Link` and `Note` are 26.4's and 26.5's, and are not here. Tests, written first: `tests/unit/plugins/apps/orch/test_transitions.py` (670: **every** `(state, state, role)` triple of the nine states and eight roles allowed or refused — not only the rows the table has — the refusals above, the spoofing of a person's name and of a manager's role, the marks, and the verb a caller's move implies) and `test_features.py` (48: the shapes' fields, the records read back, duplicates refused, a feature or task of a project that is not there, ordering and filtering, saving versus moving, a feature's status following its tasks and a parked one left alone, the keys claimed, and the counters' claims, their one kept row, and a caller that loses the race reading again). The record contract stores the new kinds' examples in every database plugin and the app contract runs over them; the shape-and-record agreement test covers Feature and Task. Nothing existing changed behaviour: a repo's `checkouts` still has no default (ernie-c's branch had added one; reverted, 26.2's card gives none).

**26.4 Links** · needs 26.3
- Build: `Link`: `owner_kind` (`feature`, `task`, `question`, `action`), `owner` (its id), `kind` (`pull`, `issue`, `commit`, `branch`, `ci_run`, `file`, `url`, `doc`, `stitch`, `task`, `feature`, `question`), `url` (the canonical form; for internal kinds a `udl:` URL such as `udl:task/udl/22.4`), `title`, `state: JsonObject` (for a pull: `state`, `head_sha`, `checks`, `review`; validated by `PullState`), `added_by`. Indexed by `owner` and by `url`.
  - `canonical(url)`: GitHub URLs normalised (`https://github.com/o/r/pull/12`, no trailing parts), so one PR is one URL.
  - A subscription-free hook in `orch`'s service: on `github.pr.*` events, every `pull` link with that URL has its `state` refreshed (through `github_get_pull`), and `orch.link.changed` is published.
  - Text fields (`spec`, notes, questions) are scanned on save: a GitHub URL or a task key in backticks becomes a link automatically.
- Tests first: `tests/unit/plugins/apps/orch/test_links.py`: the phase's second bullet; canonical forms (hypothesis: canonicalising twice changes nothing).
- Done when: `orch_find_links --url <a PR>` names the task it belongs to.

- **Built (`ernie-c`, 20 Nov 2026, PR #45):** `Link` as a shape (orch/models.py) and as a record (`orch_link`, orch/records.py, unique on `(project, owner_kind, owner, url)` and looked up by its URL and by its owner — so two projects that each have a task `1.1` each link the same PR as their own, and a feature and a task of one key never share one), `orch/links.py`, and orch's service (orch/plugin.py). **`canonical(url)`** checks the host first (only `github.com`, `www.` and `http` are the same site; `api.`, `gist.` and `evil.example` are not GitHub), then keeps owner/repo (lower-cased, as GitHub treats them) and what the URL points at — a pull's number, an issue's, a commit's sha, a branch's path — so a trailing slash, a query, a fragment, `/files` or a commit's `.patch` do not make another URL, and canonicalising twice changes nothing (hypothesis). **`links_in(text, project=)`** scans a text as it is saved — a GitHub URL (its trailing sentence punctuation left out) and a task key in backticks (`26.2`; `v1.2.3`, `python3.14` and a feature's `F-1` are not keys, and `3.14`, `0.5`, `10.2.3` and `20.04` are not: a plan's key has two or more digits before the dot and one part after it, with no leading zero) — and a save's `_scan()` stores them, drops the ones a spec no longer has, and never breaks the save (a text it cannot read is logged). **`LinkRecords`** (orch/records.py, one copy) is the both-ways store: `by_url`/`for_url` (what finds a task from its PR) and `for_owner`/`delete_for` (what hangs on a task, by project and owner kind), `add` (one URL, one link), `refresh` (through `github_get_pull`). A deleted task, feature or project deletes its links (and its notes) now, in its own project only. **`refresh_pull()`** reads a pull through udl's own `github_get_pull` (a `PullDetail`, validated), so a merged pull is `merged`, never `closed`, and keeps the card's four: `state`, `head_sha`, `checks` (pass/fail/pending/none) and `review` (`none` until O-C5) — as a `PullState`, whose three words are `Literal`s, so a word outside them is refused when the link is stored. **`review` is `none` while the github integration answers no reviews**: `github_get_pull` answers a pull and its comments, not its reviews (O-C5), so there is no review word to read and none is invented. **Orch's service** subscribes to `github.pr.*` from now on: for each event it refreshes every `pull` link whose canonical URL is the event's pull (`event_url()` reads udl's `PullEvent` and a webhook's payload), stores it, and publishes `orch.link.changed` (its full name — `link.changed` is not this app's) with a key that carries what changed, so a link that changed again is published again. It ends when the app is told to stop (the lease going), and when the subscription ends (the runtime closing it); a test drives the loop with a fake subscription and a fake service, and `AppEvents` is unchanged (the contract's stand-in bus is its own). **`orch.link.changed`** (`LINK_CHANGED`, a `LinkChanged`) is one of orch's `event_types()`. Names: the link's own kind of thing is **`link_kind`** (as a task's is `task_kind`). Tests, written first: `tests/unit/plugins/apps/orch/test_links.py` (65: the canonical forms of every GitHub shape and the hosts that are not GitHub, the hypothesis, a link read back and added twice, a task found from its PR's URL written another way, deleting a task's links and a link, the four owner kinds, two projects each linking one PR, a feature and a task of one key, the `udl:` URL, the refusals, the scan of a spec into links and its dropping one, a pull refreshed (a detail's checks and review, a model) and left alone when it is not a pull, the `github.pr.*` hook and its no-URL case, the service's own loop refreshing and publishing, and the `orch.link.changed` event type and its subject). **Deferred, and said so here:** `orch_find_links` is 26.6's operation (this task's Done-when is met by `LinkRecords.for_url`, which that operation will call); note text is scanned from 26.5 (a note is not a spec); and the dispatcher's subscription is 27's. Nothing existing changed behaviour: orch's other records are unchanged, and the record contract and the app contract run over the new kind.

**26.5 Notes** · needs 26.3
- Build: `Note`: `owner_kind`, `owner`, `kind: progress | decision | finding | review | system`, `text` (markdown), `author` (an agent, `human:<name>` or `udl`), `at`. Append-only: there is no update operation; a correction is another note. State changes write a `system` note ("ready → merged, by the GitHub watcher").
- Tests first: in `test_links.py`'s file for owners; ordering and authorship.
- Done when: a task's history reads top to bottom as its card's notes do in the markdown trackers today.

- **Built (`ernie-c`, 21 Nov 2026, PR #59):** `Note` (orch/records.py, `orch_note` — the card's fields, `owner_kind`/`owner`/`note_kind`/`text`/`author`/`at`, indexed by owner) and `NoteRecords` (orch/records.py): `add` (append-only — there is no update and no edit, so a correction is another note), `get`, `for_owner` (the record's notes, **oldest first**, then by when they were stored, so notes written in one tick keep their order and a task's history reads top to bottom as its card's notes do today — the Done-when), and `system()`, which writes what udl itself says as a `system` note by `udl`. **A state change writes one, and every path writes it:** `move_note()` (orch/records.py) builds the note — `<from> → <to>, by <who>`, where `who` is the caller's own name (never guessed from the state it moved to), so a merge udl made itself is `by udl` and the watcher's own merge is `by <the GitHub login that merged>` (or `by someone`, when the event does not say) — and `TaskRecords.save_moved()` — `move()`'s own write — stores the task and its note in one batch, so a state change never lands without its history entry. The paths that plan their own moves build the same note with `move_note()` and write it in their batch: `queue.assign` (two notes for a reassign — `queued → moved` then `moved → queued` — and none when it only re-places a task in its own queue), `queue.unassign`, `orch_ready` and `orch_promote`. A move the table refuses writes nothing, so there is no such thing as a move to the state a task is already in. `NoteRecords.for_owner` is the read the other operations use (`orch_my_task`'s history among them); the queue's own "must stop" note and readiness's progress note are unchanged, so a task that was moved and then made ready has both, in their order. Tests, written first: `tests/unit/plugins/apps/orch/test_notes.py` (12: read back, the history top to bottom, ordering by when a note was written, a correction as another note, notes kept apart by owner and by kind, the author whoever wrote it, the system note a move writes (by the watcher, by a person, by an agent), a move writing only its own note, and the kind's example round-tripping as a record). A move writing a note changed what six earlier tests saw (they counted the notes, or read the first or the last): they now pick out the note they mean by kind or by text, and the two that count a batch's writes count the note in it. `NoteRecords` is now the one read the plugin, the queue, readiness and actions use. Nothing else changed behaviour except the scan (26.4's review, carried here): `_scan()` takes a `ValueError` from a URL it cannot parse and says so in the log instead of letting it escape a save, and it no longer reads every link of a URL before adding one (the owner check does). Both are tested by `test_a_scan_never_breaks_the_save` and the two-project tests in 26.4's file.

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

- **Built (`deepseek-a`, 21 Nov 2026, PR #N):** the catalogue's eighteen operations on `orch`, each one of its methods (`models.py` holds their shapes, `plugin.py` the methods), so all three interfaces have them through udl's own adapters: `list_projects`, `get_project`, `set_project`, `delete_project`, `set_repo`, `delete_repo`, `list_features`, `get_feature`, `set_feature`, `list_tasks`, `get_task`, `set_task`, `move_task`, `split_task`, `add_link`, `remove_link`, `find_links`, `add_note`. **Every request is flat and by name, never by id** (the WIP's nested `{project: Project}` requests were replaced): a project, repo, feature or task is set by its `name`/`key`, and what is not given is kept — a new one needs its title (a repo its `github` and `account`, a task its `feature`), refused as `NotAllowed` naming what is missing. **`project` is optional while udl has one** (`_project`, as `account` is in 11.4) and `NotAllowed: Which project?` names them when it does not. Answers are built from the records by `as_shape` (`Projects`, `ProjectInfo` with its repos, `Features`, `FeatureInfo` with its tasks/links/notes, `Tasks`, `TaskInfo` with its feature/links/notes/questions, `Links`, `Deleted`, `NoteInfo`), so the API and MCP have schemas and the CLI a table. **A task's state is never set by a save** (it is not in `SetTaskRequest`): `move_task` is the one path, through `transitions.move` with the caller's principal (`_by`: udl itself when no interface set one — the CLI —, the agent itself, a person for the server's owner), so an agent is held to its own tasks and to the table, and the move writes the system note (`open → cancelled, by udl.`). A task set without a key takes its feature's next free one from the counter (past any keys a project was imported with), and `split_task` keeps the first piece's key — so its PR links and its notes stay with it — gives the rest the feature's next keys, and writes `Split into 26.1, 26.3 and 26.4.` on it. `add_link` canonicalises (26.4) and a pull that is not a GitHub URL is `ConfigError`; `remove_link` canonicalises too, so any form of the URL finds the stored one; **`find_links` is 26.4's Done-when, and `tests/integration/test_orch_cli.py` runs it through the real `udl` command** (`udl orch links find --url <a PR>`) over a sqlite config, naming the task the pull is on. `add_note` appends, and a note on a question or an action is `NotAllowed` until 28.1 stores them. The CLI groups are the card's: `udl orch projects [show|set|rm]`, `repos`, `features`, `tasks [show|set|move|split]`, `links [add|rm|find]`, and `udl orch note <key> <text>`. Tests, written first: `tests/unit/plugins/apps/orch/conftest.py` (a service with a project, its repo, a feature, two tasks and an agent) and five files — `test_projects_operations.py`, `test_features_operations.py`, `test_tasks_operations.py`, `test_links_operations.py`, `test_notes_operations.py` — for every operation's hints and models, what it answers, the refusals, the edits that keep what is not given, and the move/split/link behaviour; plus the integration CLI test. `tests/interface/test_parity.py`, `test_apps.py` and the rest of `tests/interface` pass unchanged (the parity test now finds all eighteen). **Deferred, and said so:** the `Link` shape has no `project` (so `find_links` names the owner's key and title, not its project: adding the field would change nine existing constructions in 26.4's tests), and `TaskInfo.questions` is empty until 28.1's record.

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

**26.8 Task sizes** · needs 26.3, 26.6, 26.7 · size M (O-D14, O-C14)
- Build:
  - **The size.** `Task.size: Literal["S", "M", "L", "XL"] | None` (`None`: not sized yet), and `POINTS = {"S": 1, "M": 3, "L": 5, "XL": 8}` in `orch/models.py`, the one place a size becomes points. `Task.points` is derived (`None` without a size). Records written before this read as unsized.
  - **Who sets it** (O-C14): `orch_set_task` and `orch_add_task` take `size`. An agent may set it only with the `plan` power, and never on a task it is the assignee of (`NotAllowed`).
  - **Totals:** a feature's and a project's, derived, in `orch_get_feature` and `orch_list_features`:
    - `points`;
    - `points_merged` (tasks `merged` or `done`);
    - `points_ready`;
    - `unsized`, a count, so a task without a size shows.
  - **Import:** the importer (26.7) reads `· size M` from a card's header line, in any order with `needs`. A size it can't read is reported as an unplaced line.
  - **CLI:** `udl orch tasks set 27.3 --size L`. `udl orch features show 24` prints each task's size, and the totals.
  - **The plan:** planning.md's Working agreement says a new card gives its size in its header, as Q-8's cards do.
- Tests first: `tests/unit/plugins/apps/orch/test_sizes.py`:
  - the points for each size;
  - a feature's totals, with an unsized task counted and a cancelled one left out;
  - an implementer refused, and a manager with `plan` allowed on its reports' tasks;
  - the importer on a card with and without `· size`.
- Done when: importing this file gives 26.8 the size in its own header.

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
- **Built (deepseek-b, 2026-11-19, PR #39):** `src/udl/plugins/apps/orch/agents.py` (the shapes: `Agent` with its
  `status()`, `AgentSession`, `AgentRole`, `AgentStatus`, `SessionState`, `ManagerPowers`, and `AnAgentEvent`, the
  runner's event union with its `kind` discriminator) and `orch/records.py` (`Agent` `orch_agent`, `AgentSession`
  `orch_agent_session` — runtime, never synced — and `SessionEvent` `orch_session_event` — runtime history, synced
  once it happened; `AgentRecords` with the forest's rules and `SessionRecords` with `current()` and
  `transcript()`). `OrchSettings.session_days = 30`; `Orch.record_kinds()` brings all eight (26.3's five and these three). Tests:
  `tests/unit/plugins/apps/orch/test_agents.py` (40: what an agent holds and refuses, every derived status
  including a limited session whose quota is back and a failed one waiting on a person, the manager that is not
  there / not a manager / whose chain reaches itself / itself, a manager's reports, the session's current one and
  its transcript in order, and the three kinds in the contract).
  - **Decisions:** `status()` is on the shape, not the record, and takes `offline` from 27.7's host check and
    `now` for the limited window — `offline` is never derived here, since a host check is not a session's state.
    `SessionRef` is 25.7's (`udl.plugins.runners.base`), reused rather than copied. A manager that is not a manager,
    and a chain that reaches itself, raise `ConfigError` (the data is wrong, not missing); a manager that is not
    there raises `DoesNotExist`, as a repo's missing project does. `AgentSession` is `SyncRule.NEVER` and
    `SessionEvent` `SyncRule.ENDED`, since a session belongs to the runtime that started it and only its transcript
    is history. `ManagerPowers` (27.8's) is defined here as the four bools, all off, because the `Agent` field
    needs the shape; 27.8 wires each power to its operations.
  - **Review round (deepseek-b, 19 Nov 2026, PR #39):** the forest is now checked from above too — a manager that has
    reports cannot be renamed or stop being a manager (`ConfigError`), and cannot be deleted (`InUse`, which is what
    27.9's `orch_delete_agent` reports), so no report is ever left pointing at nothing. `SessionRecords.prune(before)`
    (over a new `AppRecords.prune`) is the retention `session_days` names: 27.7's dispatcher calls it, and the
    docstrings say so instead of claiming it already happens. `an_event` (dead code) is gone, the cycle check runs
    before the manager lookup (so a chain that comes back says "reaches itself", not "is not a manager"), the second
    `# Local Imports` header is gone, the limited-session test's name matches what it tests, and one test pins the
    exact set of `Orch.record_kinds()`.

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
- **Built (ernie-d, 2026-11-19, PR #46):** `udl/interfaces/security.py` (`Principal`, `PrincipalKind`, `authenticate(settings, given, tokens)`, `AgentTokens`, `current_principal`), `udl/plugins/apps/orch/tokens.py` (`AgentToken` records, `OrchTokens`, `ROLE_OPERATIONS`, `allowed`/`refused`, `orch_issue_token`), the `AgentToken` record and `AgentTokenRecords` in `orch/records.py`, the role check in `UDLService.call` and on the service's own methods (the `@operation` decorator), the API's dependency and the /mcp wrapper setting `current_principal`, and `AppPlugin.operation` accepting `(self, request, *, service)`. Tests: `tests/unit/plugins/apps/orch/test_tokens.py` (44: the record, issuing/rotating, the book, and every role's table), `tests/interface/test_principals.py` (11, over the API and over /mcp) and `tests/unit/interfaces/test_security.py` (authenticate: the owner, an agent's token, one udl does not know). `make check` green.
- **Decisions:**
  - **The role check is on the operation, not only in `UDLService.call`:** the API's hand-written routes call `service.download(...)` directly, so a check only in `call` would miss them. The `@operation` decorator binds the check onto the service's method (and registers the bound method, so `operations()[name].method is UDLService.<name>` still holds); `Operation.of` is unchanged, so a plugin's operation is checked by `call` (its only caller).
  - **The book hashes, not security:** `AgentTokens.principal_for(token)` takes the token itself, so `udl.interfaces.security` never imports a plugin to hash it. Only `orch` knows the records.
  - **An agent's token is looked up by hash, so a leaked record is not a key:** the record keeps the SHA-256, and the token itself is written to the secret store at `apps/orch/agents/<name>/token` (where 27.7's dispatcher reads it to hand it to the runner).
  - **`orch_issue_token` is an app operation with `(self, request, *, service)`:** an app's method had no way to reach udl's records or its secret store, and 26.6's operations will need the same; `AppPlugin.operation` now passes the service to a method that asks for it. (Outside 27.2's own files, and additive: a method that does not ask is called as before.)
  - **A token only means something on a server that needs a key (Claude, 20 Nov 2026):** `orch_issue_token` refuses while `api.api_key` is unset (`NotAllowed`, naming it), because without one every request is the owner's — an agent could drop its token and do everything. **27.7's dispatcher must not start an agent session on a server without `api.api_key` either** (it is the other half: a runner that cannot be held to a role is not started).
  - **The role table's operations come later:** `orch_*` and `github_get_pull` are 26.6's and 25.5's, so the interface tests give the table one of udl's own operations (`get_status`) — what they test is the gate. The table itself is pinned, name by name, in the unit tests.
- **Review round (ernie-d, 20 Nov 2026, PR #46):** four items, all fixed. (1) **A call on someone else's session was that someone's call:** the MCP SDK runs a session's tools in the session's task, so `current_principal` set per HTTP request would have been the one that opened the session — `udl.interfaces.mcp.server._as_caller` now reads each message's own request (`ctx.request_context.request`) and authenticates it, and `test_a_call_on_someone_elses_session_is_its_own_callers` opens a session as the owner and calls a tool on it with an agent's token (refused as the agent's). A transport that names no request (stdio) leaves the caller that started the server. (2) **A deleted agent's token lived on:** `AgentRecords.delete_agent` now deletes its tokens (`delete_for`), so a new agent of the same name does not inherit the old one's key; the secret store's copy is left (it is not a credential by itself — nothing looks it up), which the dispatcher can clear when 27.7 lands. (3) **The duplicates:** `hash_token` and the `udl_agent_` prefix are each defined once — the hash in `orch.records` (beside the record that keeps it) and the prefix in `udl.interfaces.security`, which `orch.tokens` uses. (4) **The `Any`:** the `@operation` wrapper takes `**extra: object`, not `Any`, and passes it through untouched.

**27.3 Queues** · needs 27.1, 26.3
- Build: `queue.py`: `assign(task, agent, position=None)`, `reorder(agent, keys)`, `unassign(task)`.
  - Assigning an `open` task makes it `queued` at the end (or at `position`); assigning one that another agent holds makes it `moved` for that agent (a `system` note says who must stop) and `queued` for the new one, and the dispatcher stops the old session's turn.
  - A `human` task is assigned to `human:<name>` and appears under To action (28.2), never in an agent's queue.
  - Positions are dense integers per assignee, renumbered in one write.
  - Operations: `orch_assign` (`task`, `agent`, `position?`), `orch_reorder_queue`, `orch_unassign`, `orch_get_queue` (read-only); `orch_my_task` (read-only: the calling agent's current task, with the feature's spec, the task's spec, its links and notes, and the project's rules).
- Tests first: `tests/unit/plugins/apps/orch/test_queue.py`.
- Done when: every drag the board will offer (29.4) is one of these calls.
- **Built (`deepseek-b`, 20 Nov 2026, PR #41):** `orch.queue`: `assign(tasks, key, agent, project=, position=, by=)`, `reorder(tasks, project=, agent=, keys=, by=)`, `unassign(tasks, project=, key, by=)`, `queue_of()` and `next_task()`. **Positions are dense per assignee and renumbered in one pass** (`_place()` saves only the tasks whose position changed), so a queue is read from the numbers alone; `assign` at a position past the end is refused with the range, a reorder must name exactly the queue's keys (missing, extra and doubled are each named in the refusal), and `unassign` only takes a `queued` task out (it is `open` and nobody's again, and the gap closes). **A task another agent holds is moved through the table, not around it:** the old assignee's task goes to `moved` (a `system` note — `"Moved to <agent>'s queue: <old> must stop working on it."`, author `udl` — whose session 27.7 stops), then to `queued` for the new one; to make that possible the table gained **`(in_progress, moved)` and `(blocked, moved)`, both `move`/`human`**, and a task in `ready` (a PR waiting) is refused, as a table without those rows would be. A person's task (`task_kind` `human`) is refused to an agent and assigned to `human:<name>`, so it appears in that person's own list (28.2). Names: the card's `orch_assign (task, agent, position?)` is `orch_assign (key, agent, position?, project?)` — `key` is what every task operation names a task by (26.3's records), and `project` is optional while udl has one (as an account is, 11.4) and must be named with two. **`orch_my_task` answers what a session is given at its start:** the task (a `QueueEntry`: key, feature, title, state, position, assignee, repo), its feature's spec, its own spec, the project's rules and its notes (oldest first); the card's *links* wait for 26.4. `Note` (26.5) is built here, as the record and `NoteRecords` only (append, read by owner, oldest first, deleted with its task or feature) because the system note needs it; its operations are 26.5's. **`AppPlugin` now takes `records`:** the supervisor makes an app with `AppRecords(service.db)`, so an app's operations read and write its own records (`self.records`; an app made without them says so, rather than failing deeper). Tests, written first: `tests/unit/plugins/apps/orch/test_queue.py` (17: assigning at the end and at a position, a second agent taking a task (its queue closes up, the note is written), a task an agent is on, `ready` refused, a person's task, a refused position, reorder (exactly its keys, each once), unassign, and `next_task`), `test_queue_operations.py` (12: the five operations' names, hints and models, each run through `service.call()` as the CLI, API and MCP call it, the one-project rule, a task that is not there, and `my_task`'s feature spec, task spec, rules and notes), and `test_agents.py`'s kind set now has `Note`. `openapi.json` and the typed client regenerated; parity, the API and MCP get the five operations with no adapter code. **Review round 1 (fixed test-first):** a reassign is one move done twice — `→ moved` and `moved → queued` both with role `move` — so a caller with no principal (`by=None`, as the operations call it) and 27.8's managers are not refused after the first write; the moves are also planned on copies first, so a refusal cannot leave a task half-moved. `_by` takes a `Role` (no `# pyright: ignore`). `assign` refuses an agent that is not one (27.1's `AgentRecords`, with the known agents in the hint), a `human:` with no name, a task that is not a `human` one given to a person, and a task an agent is already on (its session would keep working what is no longer its own). **The gap that was here — a queue change was not one write — is closed by 27.3a** (`DatabasePlugin.upsert_records`, `DBManager.upsert_records`, `TaskRecords.save_all`, and a lock per (project, assignee) queue).

**27.3a A queue change is one write** · needs 27.3 (Claude, 20 Nov 2026: 27.3's "Known gap"; it must land before 27.7 or 29.4 write queues concurrently)
- Build: a batch write on the database kind, `DatabasePlugin.upsert_records(records: Sequence[Record]) -> list[Record]`, all or nothing.
  - SQL (`sqlalchemy.py`, and so `sqlite` and `postgres`): one transaction.
  - `memory`: validate every record first, then apply them.
  - `mongodb`: a session transaction (`with_transaction`) when the deployment supports one (a replica set or sharded). On a standalone server, the writes go in order: `connect()` logs one warning that a replica set is needed, and the plugin's `transactions: bool` property says which mode it got (`health()` only returns a `bool`). Say so in `docs/plugins.md`.
  - `DBManager.upsert_records` and `AppRecords.save_all()` pass it through.
  - `orch.queue`'s `_place`, `assign`, `reorder` and `unassign` write through `save_all()`, and each takes a per-(project, assignee) `asyncio.Lock`, so two calls in one process can't interleave their read and write. A reassign changes two queues, so it takes both assignees' locks, in sorted order, so two opposite reassigns (A→B and B→A) can't deadlock.
- Tests first:
  - The database contract: a batch with one invalid record writes none.
  - The queue: a failure on the second write leaves the queue as it was.
  - Two concurrent reorders (`asyncio.gather`) leave dense positions `0..n-1` with no doubles, and so does a reassign racing a reorder of the old assignee's queue. Two opposite reassigns at once both finish (under `asyncio.timeout`). A standalone MongoDB's warning is asserted with `caplog`, and `transactions` is false.
- Done when: every queue change is one `upsert_records` call, and 27.3's "Known gap" note is replaced by what was built.
- **Built (`deepseek-b`, 20 Nov 2026, PR #47):** `DatabasePlugin.upsert_records(records) -> list[Record]`, all or nothing: SQL (`sqlite`, `postgres`) writes the batch on one connection (one transaction, rolled back whole), `memory` validates every record against what is stored and against the ones before it in the batch before it applies any, and `mongodb` runs the batch in a session transaction when the deployment has one — a replica set or a sharded cluster. A standalone server has none: `connect()` asks the deployment once and logs one warning naming the replica set, and the new `DatabasePlugin.transactions` property says which mode it got (`health()` still returns a `bool`). `DBManager.upsert_records` stamps the batch from the clock and refuses a batch that mixes scopes (one write goes to one connection); `AppRecords.save_all()` passes it through. `orch.queue` now writes each change as one batch, under a lock per (project, assignee) queue (`_locks`/`_locked` in `queue.py`, one lock map per event loop, so a test's loop does not leave a lock a later one waits on): `_renumbered` saves only the tasks that are not stored as they are (`_place`, `assign` and `unassign` alike), in one write; `assign` plans its two moves through the table on copies, then writes the new queue, the old assignee's closed-up queue and the system note in the same batch, holding both queues' locks (taken in sorted key order, so two opposite reassigns cannot deadlock, and read again under them: a task that changed hands while the call waited starts it over, so no write touches an unheld queue); `reorder` reads and writes its queue under its lock, and `unassign` does the same as `assign` with one queue. `TaskRecords.save_all(tasks, also=())` checks what `save_task` checks of a new task (project and feature there), keeps each affected feature's status in step in the same write (`_follow_all`, the rule shared with `_follow` as `_status_of`), and takes the note as `also`. Its state guard is the one `save_task` has and a batch cannot: the states in a batch are the ones `orch.transitions.move()` planned. Tests, written first: the record contract (`test_record_contract.py`, 4 cases × 4 plugins: a batch stored and given back, an empty batch, one bad record writes none — or, on a standalone MongoDB, only the ones before it, which `transactions` says — and a stored record's `created_at` kept), `tests/unit/db/test_manager.py` (2: stamped and stored together, and never empty or mixed), `tests/unit/plugins/databases/test_mongodb.py` (a commit that fails is a `DatabaseError`, with pymongo's error as its cause), `tests/integration/test_mongodb.py` (a standalone server's warning once, with `caplog`, and `transactions` false; and a batch on a server whose `hello` failed falls back, once, to in-order writes), `tests/unit/plugins/apps/orch/test_queue.py` (8: a reorder is one `save_all` of the tasks that moved, a failing write leaves the queue as it was, two reorders at once leave `0..n-1`, a reassign racing a reorder of the old queue leaves both dense — the reorder is refused when it ran second, naming a task that had left — two opposite reassigns both finish under `asyncio.timeout(5)`, a reassign and an unassign write only the tasks whose place changed, and a store whose first read is stale starts the assign over — and the unassign too). `docs/plugins.md` says what a standalone MongoDB means for a batch.

**27.4 Suggested next tasks** · needs 27.3
- Build: `suggest.py`: `suggest(tasks, agents, *, agent=None, limit=10) -> list[Suggestion]`, a pure function. `Suggestion`: `task`, `agent` (who it suits best), `score`, `reasons: list[str]`, `stack_on: str | None`.
  - Candidates: `open` tasks of kind `build` in the agent's projects, whose `needs` are each `merged` or `done`, or `ready` (then `stack_on` names that task's PR and a reason says "stack on #N").
  - Score, highest first: the feature is `active` (+3); the agent's last task was in the same feature (+3) or the same repo (+1); it unblocks the most other tasks (+1 each, to +3); the feature's `order`, then the task's key, break ties.
  - With no `agent`, each candidate is paired with the idle agent it scores best for.
  - Operation: `orch_suggest` (read-only).
- Tests first: `tests/unit/plugins/apps/orch/test_suggest.py`: the phase's second bullet, and worked examples.
- Done when: suggestions over udl's imported plan put 22.1 before 22.2.
- **Built (`deepseek-b`, 20 Nov 2026, PR #43):** `suggest.py`: `suggest(tasks, features, agents, *, agent=None, limit=10) -> list[Suggestion]`, pure. Candidates are the `open` build tasks whose `needs` are each `merged`/`done`, or `ready` — then it may start on that task's branch, and `stack_on` names it (the card says "that task's PR"; the PR URL is 26.4's link, so until then the key names the task whose branch is the base). Scoring as the card says: +3 an `active` feature; +3 the agent's last task (the one it most recently worked, by `updated_at`) in the same feature, or +1 in the same repo; +1 per task it unblocks, to +3; ties to the feature's `order`, then the task's key read as numbers (`22.10` after `22.9`). With no agent, each candidate is paired with the idle agent (enabled, not paused, nothing in progress) it scores best for, a tie to the lower name, so the answer never depends on the order the agents were read in; an agent on a task of another project is not idle (the operation reads those across every project and passes them as `busy`); a named agent that is not one raises `DoesNotExist`. A candidate whose `needs` are two `ready` tasks is not one: it would have to stack on two branches at once, so it waits until one is merged. A `cancelled` dependent is not a task it unblocks. Its "last task" is one it started (a `queued` one says nothing about what follows). **The signature takes `features` as well as `tasks` and `agents`** — the card's two are not enough: a feature's `status` and `order` are what two of the scores and the tie-break read, and a task carries only its feature's key. Operation `orch_suggest` (`SuggestRequest`: `project?`, `agent?`, `limit` 10; answer `Suggestions`), read-only, so the CLI, the API and MCP get it generated. Tests, written first: `tests/unit/plugins/apps/orch/test_suggest.py` (23 functions, 24 cases: each rule of the scoring, the states a need may be in, the number-read keys, the pairing and its order, the limit) and `test_suggest_operations.py` (7: the operation's hints and models, the plan read best first, an unknown agent, an agent busy in another project, the limit); `test_queue_operations.py`'s operation lists now include `suggest`. `openapi.json` and the typed client regenerated.
- **Review round 2 (`deepseek-b`, 20 Nov, #43):** the bot's nine items, all fixed: a `cancelled` dependent is not one it unblocks; the pairing knows an agent busy in another project (`busy`, which the operation reads across every project); a task with two `ready` needs is not a candidate; "last task" is only a state the agent started; `_last` is read once per agent and `_score`/`_reasons` are one `_rank` returning both (the two nits); the card note's blank line and test count; the tie-break and `repo=None` tests.

**27.5 Worktrees, gates and `orch_push`** · needs 25.5, 25.6, 27.3
- Build: `workspace.py`.
  - `prepare(task, agent) -> Worktree`: on the agent's host, `git -C <checkout> fetch origin`, then `git worktree add <host.work_dir>/<project>/<key>-<repo> -b <branch> origin/<base>` (or the `stack_on` task's branch). The branch comes from the repo's `branch_pattern`; it and the path are saved on the task. An existing worktree for the task is reused.
  - `remove(task)`: after a merge, `git worktree remove` and delete the local branch.
  - `GateLane`: one gate run at a time **per host** (a `Lease` named `orch-gate:<host>`), so two agents' test runs never fight for the machine.
  - `orch_push` (`task`, `title?`, `body?`): refuses unless the caller is the task's assignee (or an owner). In the worktree: nothing uncommitted (else `NotAllowed` with `git status`'s summary); rebased on the base if it moved; the repo's `gate` argv in the lane, with a timeout (`[apps.orch] gate_timeout`, 60 min); on a pass, `git push --force-with-lease origin <branch>`, then `github_open_pull` (the body gets "Task: `<key>`" and, when stacked, "Merge after #N"), and a `pull` link on the task. It returns `PushResult`: the gate's exit code and the last 200 lines of its output, the PR's URL.
  - `orch_pr_status` (`task`, `wait: bool`, `timeout`): the readiness of 27.6; with `wait`, it returns when the head's checks and the bot's comment for that head are both in, or at the timeout.
- Tests first: `tests/integration/plugins/apps/orch/test_workspace.py` on a temp git repo with a local bare `origin` and `FakeGitHub`: a failing gate pushes nothing; a pass pushes and opens the PR once, and a second push updates it; the lane serialises two pushes.
- Done when: the agent's whole path to a PR is `orch_push`.
- **Built (deepseek-b, 20 Nov, PR #52):** `workspace.py` — `prepare()` (fetch, `worktree add -b
  <branch> origin/<base>`, or `origin/<the stacked task's branch>` — off origin, since the needed
  task's worktree is on another host; an existing worktree reused, and with `tasks=` the branch
  and path saved on the task), `remove()` (worktree and local branch, and nothing when they are
  already gone), `GateLane` (a `Lease` `orch-gate:<host>`: one gate at a time per host, its lease
  as long as the gate may run plus 10 minutes and renewed every third of that while it runs, and
  a holder unique per push — `assignee:key:8 hex` — so two pushes never take each other's lease
  for granted) and `push()`: uncommitted work refused with `git status`'s summary, and nothing on
  the branch that the worktree does not have refused (`git pull --rebase origin <branch>` in its
  hint) unless it is what the task itself pushed last — kept under `refs/udl/pushed/<branch>`,
  written after each push and taken away by `remove()`, since the tracking ref is shared and any
  fetch moves it — so someone else's commits are never overwritten while a rebase, amend or
  squash of its own still pushes; the lease pinned to origin's tip as just fetched, so the same
  holds for a push while the gate runs, and a push after the agent's own pull goes through; a
  rebase on what the branch is built on when that moved (the repo's base, or the needed task's branch while
  its pull is open; a conflict is `NotAllowed` with git's hint and nothing is pushed), the repo's
  `gate` run in the lane under `[apps.orch] gate_minutes` (60) — read, not raised on, so the
  agent gets its real exit code and the last 200 lines of its output (each stream's own end: the
  last 100 of stdout and of stderr, since a suite's summary is on one and a compiler's error on
  the other, and `HostShell.run(tail=True)` keeps stdout's end rather than its first 64 KiB) — and
  a gate that runs past its time or is not installed is `NotAllowed` — then `git push
  --force-with-lease=<branch>:<the sha we saw>`, `github_open_pull` (as udl, not as the agent: an
  implementer may not call it) with the body's "Task: `<key>`" and "Merge after #N" for each
  needed task that has a pull, and the `pull` link on the task. A second push sends the title and
  body only when it was given them, so what a person wrote in the description is not wiped
  (`OpenPullRequest.title` is now optional for that: an update leaves it, an open falls back to
  the head branch's name). `orch_push` refuses an agent that is not the task's assignee (an owner
  may push any), and `orch_pr_status` wraps 27.6's `readiness` with `wait`: `readiness` now also
  answers `pending`, the blockers that are only a matter of time (the checks running, the bot not
  done with this head, GitHub not knowing yet), and the wait holds while every blocker is one of
  them, polling `poll_seconds` (20) up to `wait_minutes` (60) or the request's `minutes`, and
  answers with `Readiness.waited` (seconds). The tests: 7 in `test_workspace.py`, 6 in
  `test_gate_lane.py` (two lanes on one host serialise, another host does not wait, an expired
  lease is taken, a gate longer than its lease keeps the lane, two holders of one name are two
  lanes, a failing gate still releases), 16 in `test_orch_push.py` (on a real temp repo with a
  bare `origin` and a fake `github`: a failing gate pushes nothing, a long one says its code and
  the end of its output, a gate that prints more than udl keeps still says its summary, a second
  push with no body leaves the description, a branch someone else moved is refused and their
  commit stays even when a fetch moved the tracking ref first, and the pull that follows lets the
  push through, a branch udl never pushed is not overwritten either, a task that rewrote its own
  pushed commits pushes again, a stacked task's body says what it merges after) and 6 in `test_pr_status.py` (on a `FakeClock`), plus
  `HostShell.run(check=False)` in `test_host_shell.py` (both shells give a failure back).
  Names, as the card's differ: the operation is `orch_pr_status`, the request field is `key` (not
  `task`), the setting is `gate_minutes` (not `gate_timeout`) and the wait's is `minutes` (not
  `timeout`); `PushResult.pull` is a URL string, and a pull is read through a `PullLike` protocol
  (`html_url`) rather than importing the github plugin's model. Differences from the card: a
  failing gate raises `NotAllowed` with the run in its hint rather than returning a `PushResult`
  (nothing is pushed, so there is no result), and the "lane serialises two pushes" test is on
  `GateLane` itself. Not built here: the dispatcher that calls `prepare()` before an agent works
  (27.7); a stacked task whose base was squash-merged rebases onto the repo's base branch, so the
  agent resolves the replay's conflicts (a `--onto` needs the pre-squash sha, which the
  dispatcher will have); and `oc.py`'s `agent-push`/`pr-status` as thin clients of these two
  operations (they live in `oc-guard`, outside this repo).

**27.6 Readiness** · needs 25.5, 26.4
- Build: `readiness.py`: `readiness(pull: PullDetail, *, bot: str | None, task, tasks) -> Readiness`, pure. `Readiness`: `ready: bool`, `blockers: list[str]`, `open_items: list[ReviewItem]`.
  - Ready needs all of: not a draft; `mergeable` and `mergeable_state` clean; every check run on the head completed with `success`, `neutral` or `skipped`; when `bot` is set, the bot's latest comment is **for this head** (written after the head was pushed: its earliest check's `started_at`, which `CheckInfo` now carries, or the commit's time when no check says) and its verdict is clean; no `needs` task still unmerged ("Merge after #N"); the task has no `hold`.
  - `parse_review(body) -> ReviewVerdict`: `items` (each with a status: `open`, `fixed`, `partially_fixed`, `not_fixed`, `new_issue`) and `assessment` (`approve`, `request_changes` or unknown). **Clean means: no item other than `fixed`, and the assessment isn't `request_changes`. An "approve" line never overrides an open item** (the 12 Nov bug in thevault's `pr-status`).
  - A task with `hold` set and everything else ready is reported as `held`, not ready.
  - `orch_ready` (`task`, `note`): refuses with the blockers unless ready; then moves the task to `ready`, writes the note as `progress`, publishes `orch.task.ready`. Where the project's `merge = agent` and the caller is the assignee, it merges instead (28.3's path) and the task goes to `merged`.
- Tests first: `tests/unit/plugins/apps/orch/test_readiness.py`: the phase's third bullet. Bot comments are real ones copied from udl's first PRs (23.2) into `tests/fixtures/reviews/`.
- Done when: a comment with one PARTIALLY FIXED row is not ready, by test.
- **Built (deepseek-b, 20 Nov, PR #50):** `readiness.py` — `readiness()` (pure) over a `Pull` that
  keeps only what a verdict needs, and `parse_review()` for the bot's status table and its
  numbered and bulleted lists; an approve line never overrides an open item, and a comment
  written before the head was pushed does not count — the push is the earliest check's
  `started_at` (`CheckInfo` carries it, so a comment written while `agent-push` runs its gates is
  not taken for one on the new head), and the commit's time only when no check says. `Readiness`
  adds `held`, and `open_items` are the bot's items that are not `fixed`. `orch_readiness` (read-only) and `orch_ready` (refuses
  with the blockers; then moves the task to `ready` with its `progress` note in the same
  `save_all` write, and publishes `orch.task.ready`). The GitHub integration grew
  `head_committed_at` on a pull (`get_commit`) and `started_at` on each check, which is what makes
  "for this head" checkable. udl moves a task to `ready` as the system, in the role `ready` (a
  person's principal may move a task anywhere, which would ready an `open` or `blocked` one and
  clear its reason).
  Tests: 42 in `test_readiness.py` (the fixture is 28.2's real review comment,
  `tests/fixtures/reviews/follow-up-all-clear.md`) and 14 in `test_readiness_operations.py`
  through the service with a fake `github` app, plus `test_plugin.py`'s two for the times the
  integration hands readiness. The card's "merge instead when `merge = agent`"
  is 28.3's, not built here. Names: the operations are `orch_readiness` and `orch_ready` (the
  card wrote only `orch_ready`); `orch_pr_status` (27.5) can wrap `readiness` later.

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
- **Built (`deepseek-b`, 21 Nov 2026, PR #58):** `orch/dispatcher.py`, run from `Orch.serve()` beside the links' watcher, plus `orch/rules.md` (the fixed rules every session is told) and `orch/sessions.py` (the transcript). One loop per enabled agent, woken by `orch.*`/`github.pr.*` events and a 30 s tick: it takes the agent's `in_progress` task (else its first `queued` one whose `needs` are done), `prepare()`s the worktree, starts or resumes the session, sends a turn and streams the runner's events into `SessionEvent`s and `orch.session.started`/`.event`/`.failed`/`.limited`. A task that moved on ends the session; one still going is told what changed — its pull's readiness blockers and the bot's open items, the bot's text fenced and labelled `review-bot` as 17.7 fences outside text — and the same news twice is a nudge; after `max_nudges` turns in a row that brought nothing new the task is `blocked` ("agent stalled"). A session that has failed `FAILURES` times moves its task to `blocked` too, and ends, so the agent's loop takes the next one. A pull only waiting on time (the checks running, the bot not done with the head) gives the session **no turn** (the pull's event wakes it). `RunnerLimited` sets `limited_until` and publishes `orch.agent.limited`, and the session is left alone until it is back; `RunnerFailed` retries three times on the clock and then the session is `failed`; sessions left `running` are resumed with "udl restarted; continue where you were"; an agent whose host fails `check_host` is `offline` at start and no session is started there; at most `max_agents_running` turns are in flight. **No session at all without `api.api_key`** (27.2's rule: its token would limit nothing). `AgentSession` gained `project` (its task's key is only unique within one), `turns`, `nudges`, `failures`, `limited_until`, `last_text` and `last_error`, and `Agent` gained `offline`. **Settings:** `hub_url`, `gate_minutes`, `max_agents_running`, `deny` (the harness deny list: `git push`, `gh pr merge`, `git clean`, …), `session_days`. Two names changed from the card: `allowed_commands` is `deny` (it is what a session must **not** run; `allowed_commands` means the opposite in `[ai]`), and `gate_timeout` is 27.5's `gate_minutes`. **Not done here, and why:** the question a stall or a failure raises is 28.1's (`orch_ask`, not in main yet), so the reason goes on the task's `blocked_on` and the event carries it; `bot_wait`'s "rerun the checks once, then ask" has no operation to call — `github_rerun_checks` reruns only *failed* checks and refuses a green pull — so a pull waiting on the bot is left waiting, and the session holds no turn meanwhile; nudging the agent a merge unblocks is 27.8's managers. **Tests, written first:** 35 in `tests/integration/plugins/apps/orch/test_dispatcher.py` — the card's done-when (two queued tasks to `ready` in order, nobody prompting), the worktree and the in-progress move before the turn, the instructions (rules + project rules + brief) and the first prompt, the transcript and the live events, what changed and then a nudge, a stall, a limit, a retry and a failure, a restart's resume, a paused agent, a task whose needs are not done, the fenced bot items, the MCP endpoint and the agent's token, no session without an API key, one turn at a time, no turn while waiting, and an unreachable host.
- **Review round 1 (`deepseek-b`, 21 Nov 2026, PR #58):** the bot's first round (items 1–14) and its follow-up. The session's `project` is what its task is looked up in (two projects may each have a `1.1`); the agent's MCP token is read from the secret store first and issued only when there is none (issuing rotates it, so a restart used to take the running session's away), and with no `hub_url` a session is given this machine's `/mcp` instead of no endpoint at all; a turn's end is one place (`_ended`) — `RunnerLimited` sets `limited_until` and publishes `orch.agent.limited`, a failure is retried with a backoff that holds no `max_agents_running` slot, and `failures` goes back to 0 after a turn that worked; `orch.session.ended` publishes what the session was, and the live events' dedupe key is a digest of what was published (a counter that started at 0 made each run's first events the last run's repeats). `hub_url` and `deny` are `FileOnly()`. The readiness error is fenced like the bot's text. `waited_since` and `rerun_for` are gone (nothing read them), and the transcripts are pruned on a `PRUNE_EVERY` (6 h) tick as well as at start-up. The tests moved to the path the card names. Regression tests for each fix: a loop that survives a non-UDL error while the others carry on, a nudge count that a turn with progress resets, the live events not waking the dispatcher, a bot's backticks staying inside their fence, two projects with one key, one token across sessions, the local `/mcp` URL, `failures` reset, a backoff that holds no turn's place, and pruning at start-up and on the tick.
- **Review round 2 (`deepseek-b`, 21 Nov 2026, PR #58):** a live event's dedupe key is something stored and unique — the `SessionEvent` record's own id for `orch.session.event`, and the session with its turn for `.started`/`.ended`/`.failed`/`.limited` — because a key made from what was said dropped the second `"."` of a turn, and a key that counted from 0 made each run's first events the last run's repeats. Three tests were added: a pull that cannot be read is fenced like the bot's text, two things said alike in one turn are two events, and a restart's first live event is not the last run's repeat. A fourth covers a token stored before the run (the session gets it, and nothing is issued).
- **Review round 3 (`deepseek-b`, 21 Nov 2026, PR #58):** `orch.agent.limited` is keyed by the session and how many times it has been limited (`AgentSession.limits`), not by its turn number: a limited turn is not a turn, so a key of the turn dropped every limit after the first. The test limits one session twice — a run, the quota back, then a resumed run — and both events are published.
- **Review round 4 (`deepseek-b`, 21 Nov 2026, PR #58):** the two helpers got their tests — `_repo_of` (the repo a task names, a missing one refused by name rather than falling back to the project's only one, and no name meaning the project's one repo, refused when it has none or several) and `_prepared` (a task that has a worktree is not prepared again, and one that has none is prepared on its agent's own host). `space.prepare` is what the tests swap, so the worktree stays a path and no git runs.

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

**27.10 Working time, learned hours and forecasts** · needs 26.8, 27.7 · size XL (O-D14, O-C14)
- Build:
  - **Recording time.** Two kinds of record:
    - `StateChange` (scope: history): `task`, `from`, `to`, `at`, `by`. It is written in the same write as every move (26.3's `move()`, and `save_all`), so a task's history is complete.
    - `Wait`: `task`, `kind: ci | bot | limit`, `start`, `end`. The dispatcher (27.7) writes it while the agent holds no turn because its PR's checks or the review bot are pending, and while the agent is `limited`.
  - **`timing.py`** (pure): `working_time(changes, waits) -> timedelta`.
    - It is the time spent `in_progress`, less the waits inside it; a wait that crosses a state change counts only for the part inside `in_progress`.
    - A task that moved between agents counts each agent's part for that agent's model.
  - **`estimates.py`** (pure):
    - `hours_per_point(samples) -> Rate` takes merged, sized tasks: the median of working hours ÷ points, per model (the model of the agent that worked the task), over its last 30.
    - A model needs at least 5 samples; below that the project's rate is used, then a default of 1 h a point. `Rate` says which it used (`model`, `project`, `default`).
    - `estimate(task, agent) -> timedelta` is the size × that rate.
    - The spread is the 25th and 75th percentile of actual ÷ estimate, over the same samples.
  - **`forecast(project, now) -> Forecast`** (pure):
    - It takes the open, sized work per agent in queue order: what is left of its current task's estimate, then each queued task's estimate.
    - Unassigned tasks are spread over the project's enabled implementers in suggestion order (27.4).
    - An agent's hours become days at its own working hours per day over the last 7 days. Below 1 h a day (a new or idle agent), the project's agents' average is used, then 8 h.
    - `likely` is when the last agent finishes; `early` and `late` come from the spread. The same is done per feature.
    - Unsized tasks are left out, and counted.
  - **`orch_progress`** (read-only; `project`, `since?`) → `Progress`:
    - `series`: for each day, the scope's points and the points merged. A day's scope counts the tasks that existed and weren't cancelled then, at their size now.
    - `pace`: points merged a day, over the last 7 days.
    - `forecast`, and `features` (key, title, points, merged, forecast).
    - `models`: model, runner, tasks, hours per point, and hours per size.
    - `recent`: the last 20 merged, sized tasks (key, title, size, estimate, actual), and `estimate_ratio`, the median of actual ÷ estimate.
  - **`orch_estimate`** (`task`): one task's size, estimate, and the time worked on it so far.
- Tests first: `tests/unit/plugins/apps/orch/test_timing.py` and `test_estimates.py`, on `FakeClock` histories:
  - waits taken off, including one across a state change, and a moved task split between two models;
  - a model with fewer than five samples falls back, and says so;
  - the forecast for two agents whose rates are known;
  - an agent with no working hours in the last 7 days, and a project with none at all, still forecast (no division by zero);
  - a property test: a forecast never ends before `now`, and `early ≤ likely ≤ late`.

  `test_progress_operations.py` goes through the service.
- Done when: `udl orch progress udl` prints the burn-up's numbers, the forecast and the hours per point, on a fixture history.

**27.11 Chat sessions** · needs 27.2, 27.9, 25.8, and 25.12 for its Codex half only (25.12 is optional, Q-9: the Claude Code half doesn't wait for it) · size XL (O-D15, O-C15)
- Build: `orch/chat.py`, the sessions behind the chat sidebar (29.12).
  - **`Chat`**: `id`, `owner` (`human:<name>`), `runner` (`claude-code` or `codex`), `model`, `host`, `directory`, `ref: SessionRef`, `state` (an `AgentSession`'s states), `title` (from the first message), `created_at`, `last_at`, `tokens_in`, `tokens_out`, `cost`.
    - `directory` is where the harness starts: any directory on that host the user picks, by default the host's udl checkout.
    - A chat's stream is `SessionEvent`s, as an agent session's is (27.1), so the live view and the transcript are shared code.
  - **As the user** (O-C15). A chat's turn runs the runner with the chat's own options, not an agent's:
    - none of `--settings` with a deny file, `--strict-mcp-config`, `--permission-mode` or a worktree: the user's own settings decide;
    - udl's MCP server is added beside the user's own;
    - never `--dangerously-skip-permissions`, `--permission-mode bypassPermissions` or Codex's bypass flag (asserted).
  - **Permission prompts reach the sidebar.**
    - **`claude-code`:** `--permission-prompt-tool mcp__udl__orch_chat_ask_permission`, an MCP-only tool (not on the CLI or the API). The harness calls it, with the tool and its input, whenever the user's settings say to ask.
    - udl then records a `PermissionRequest` (`chat`, `id`, `tool`, `input` kept as data, `state`) and publishes `orch.chat.permission`. It waits up to 10 min, then denies, for `orch_chat_permission` (`chat`, `request`, `decision: allow_once | allow_chat | deny`, `message?`).
    - `allow_chat` remembers that exact request (the tool and its exact input: the whole command, or the same file) for the rest of the chat, and answers it itself when it comes again. Nothing is matched by prefix. A shell command that chains or nests others (`&&`, `||`, `;`, `|`, `$(…)`, backticks), or runs a shell or `env`, is never remembered, so Allow for this chat on one is Allow once.
    - **`codex`:** drive `codex app-server`, the JSON-RPC protocol over stdio that OpenAI's IDE extension uses. Its approval requests become the same `PermissionRequest`s. Read its methods from the installed version (`codex app-server generate-json-schema --out <dir>`, committed as fixtures).
    - Without `app-server`, a Codex chat isn't started: `orch_chat_start` refuses it (`NotAllowed`: "this Codex can't relay its approvals; update it"), since running without approvals would break O-C15. A Codex agent (25.12) is another matter: it works in a worktree, under its command rules.
  - **The chat's token** (O-C15): a `ChatToken`.
    - It has the `udl_chat_` prefix. The record keeps its SHA-256; the token itself is in the secret store at `apps/orch/chats/<id>/token`, and reaches the harness in its environment.
    - 27.2's `authenticate` reads it as `Principal(kind="owner", name=<owner>, via=f"chat:{id}")`.
    - `UDLService.call` refuses it `orch_issue_token`, `orch_set_auto_merge` (28.7), the secret store's writes, and every `orch_chat_*` operation but one: `orch_chat_ask_permission`, the prompt tool its harness calls. For that one, the chat is the token's own (taken from its `via`, never from the request), and `orch_chat_permission` stays refused, so a chat can't answer its own prompts.
    - It is deleted when the chat is stopped or deleted, or has been idle for `[apps.orch] chat_idle` (default 24 h). Every call made with it is logged with its `via`.
  - **Operations,** owner only (an agent's token is refused, `NotAllowed`):
    - `orch_chat_start` (`runner`, `host`, `directory?`, `model?`, `text`);
    - `orch_chat_send` (`chat`, `text`: queued while a turn runs, or steered where the runner can);
    - `orch_chat_permission`;
    - `orch_chat_stop`, which ends the turn;
    - `orch_chat_list` (the user's chats, newest first) and `orch_chat_get` (the transcript, paged);
    - `orch_chat_delete`.

    `GET /orch/chats/{id}/stream` (an SSE of its events and permission requests) is interface-only, as 27.9's session stream is. The CLI has `udl orch chat`: an interactive loop over the same operations, with prompts answered in the terminal.
  - **Text from outside:** what a chat reads (PR comments, bot reviews, pages) is its harness's business, as it is in a terminal. udl's own messages to it never carry text from outside.
- Tests first: `tests/integration/plugins/apps/orch/test_chat.py`, with `FakeRunner` and a fake `claude` that asks for one permission:
  - the request reaches `orch.chat.permission`;
  - Allow once runs it, Deny stops it, Allow for this chat answers the same request by itself the second time but asks again for `git push` after `git status` and for `ls && rm x` after `ls`, and no answer in 10 min denies;
  - the argv never has a flag that skips permissions;
  - the chat's token is refused `orch_issue_token`, `orch_chat_start` and `orch_chat_permission`, can call `orch_chat_ask_permission` for its own chat only, works for `orch_assign`, and stops working after `orch_chat_stop`;
  - an agent's token can't start a chat.
- Done when: an opt-in smoke test on `local`, with the real `claude`, starts a chat that asks to run `ls`; it is allowed through `orch_chat_permission`, and its answer names a file.

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
- **Built (`deepseek-b`, 20 Nov 2026, PR #48):** `orch/actions.py` — `ActionRecords` (read by project, state, when, person and task; `save_action`, `action`, `actions`), `set_action`, `become_now`, `finish`, `drop`, `for_task` and `_action`. The record is `orch_action` (`HumanAction`, unique on `(project, key)`, its `A-` number from 26.3's `Counters`), and deleting a task, a feature or a project deletes its actions with it, in one place (`records._delete_actions`). The shape is `orch.models.Action`, with `ActionWhen` (`now | after | later`) and `ActionState` (`open | done | dropped`). The card's fields plus **`task`**: the task an action is about, so its result has somewhere to go and 26.4's links can hang on it. **`when` and `after` must agree** — an action `after` names a task and becomes `now` when that task merges (`become_now`, for 28.3 to call on a merge), and one that is `now` or `later` names none; an assignee that is not `human:<name>` is refused. **`finish`** writes the result as a `progress` note on the task it is about (author `human` — udl does not know which person, and the CLI's caller is the owner), and for a `human` task it moves the task to `merged` (a person may move a task anywhere; the table's rows are for agents), clears its assignee and closes up the person's queue behind it. **A person's task gets its action when it is assigned** (`plan_for_task` from `orch.queue.assign`, written in the queue's own batch: one write, under its locks — round 2 below), and assigning it to another person moves the action with it: a task has one action, not one per assignee. Operations: `orch_set_action`, `orch_list_actions` (read-only; `state` None means the open ones, the "To action" list), `orch_finish_action`, `orch_drop_action` (idempotent). Tests, written first: `tests/unit/plugins/apps/orch/test_actions.py` (17: the key and its next, the due order, `after` without a task and `after` on a `now` action refused, an agent as assignee refused, **`after` turning into `now`** and only the open ones, finishing (its result, a second finish refused), the note on the task, a person's task merging and leaving the list, dropping with its reason, an action that is not there, a person's task getting an action and keeping one when reassigned, an agent's task getting none, and the record in `Orch.record_kinds()`), `tests/unit/plugins/apps/orch/test_actions_operations.py` (7: each operation through `service.call()`, the narrowed listing, the requests and answers). `openapi.json` and the typed client regenerated.
- **Review round 1 (`deepseek-b`, 20 Nov 2026):** the bot's six, each with its test. (1) **Deleting takes the actions with it**: `records._delete_actions` is called from `delete_task`, `delete_feature` (its tasks') and `delete_project` (every action of the project, so its `A-` numbers start over rather than colliding on `A-1`). (2) `finish` on a person's task **makes what waited `after` it `now`** (`become_now`, since a person's task merges with no PR for 28.3 to see), and **refuses before writing anything** when that task is already `merged` — a second action about it would otherwise write its note and then fail the move. (3) `for_task` **reopens a dropped action** instead of handing it to the next person. (4) `drop` **answers an already-dropped action unchanged**, so the operation's `idempotent` hint is true. (5) `set_action` **refuses a `task` or `after` that is not a task of the project** (`DoesNotExist`), and `human:` with no name says so instead of calling it an agent's name. (6) `unassign` **takes the action off the person** (`assignee=None`, still open) so a task nobody holds is not on someone's To action.
- **Review round 2 (`deepseek-b`, 20 Nov 2026, #48):** the To action is **one write with the queue**, not two: `actions.plan_for_task` (and `plan_action` under it) answers the action with its `A-` number already claimed, and `queue._assign` / `queue._unassign` carry it in `save_all(..., also=[...])` inside the queue's own lock, so a store that cannot write the action writes neither (two tests over a store that refuses an action's write: `test_a_person_s_task_and_its_action_are_one_write`, `test_unassigning_a_person_s_task_writes_the_action_off_them_with_it`). The `A-` number's claim stays its own compare-and-set — a batch cannot take it — so a failed batch burns a number and writes no action. **The To-action section of `planning/human-comms.md` is 28.4's inbox**: what is built here is the record and its operations, not the section that shows it.

**28.3 Ready to merge** · needs 27.6, 25.5
- Build: `merge.py`.
  - `orch_merge_list` (read-only) → `MergeList`: `ready` (tasks in `ready`, grouped by repo, in merge order: a topological sort of "Merge after" within each stack, then by when they became ready), `coming` (tasks with an open PR that isn't ready, each with its blockers), `held` (with the reason), `done` (merged in the last 14 days, with what followed). Every row has the PR's URL, title, repo in full, head SHA, and the task and feature keys.
  - `orch_merge` (`task`, `confirm` = `owner/repo#number`; admin, owner-only unless the project's `merge = agent` and the caller is the assignee): re-checks readiness for the current head, then `github_merge_pull` with that head's SHA (so a PR that changed since isn't merged blind).
  - After a merge, however it happened (the watcher's `github.pr.merged`): the task moves to `merged` (and to `done` at once if a reviewer or the owner merged and the project has no promotion branches); its worktree is removed; each task stacked on it gets a `system` note and its agent a prompt to rebase onto the base and push again; actions waiting `after` it become `now`.
  - A PR closed without merging moves its task back to `in_progress` and tells its agent why (the closing comment, as data).
  - Promotion, for projects that have branches after the base: `orch_promotion` (`project`) lists what is `merged` and not `done` per repo; `orch_promote` (`tasks`, `note`; reviewer or owner) marks them `done` after the promotion PR merges. udl's own project has none.
- Tests first: `tests/integration/plugins/apps/orch/test_merge.py`: the phase's second bullet; `confirm` checked; a head that moved between listing and merging is refused.
- Done when: a three-PR stack merges in order on the fakes, each rebase prompted.
- **Built (`deepseek-b`, 20 Nov 2026, PR #56):** `orch/merge.py` (`after_merge`, `after_close`, `stacked`, `order`, `DAYS`) and `orch_merge_list`, `orch_merge`, `orch_promotion`, `orch_promote` in `plugin.py`. A merge is set off by one function, whether udl made it (`orch_merge`) or the watcher saw it (`github.pr.merged` → `pull_happened`, through `follow`'s new `happened` callback, so `follow` keeps its narrow `PullReader`): the task moves to `merged` — and to `done` at once when the merge was a review and nothing follows the base — the tasks stacked on it (`needs`) get a `system` note to rebase and push again (a note, not a prompt: nudging the agent's session is 27.7's), and actions waiting `after` it are `now` (28.2's `become_now`). `after_merge` is idempotent: a task already `merged`/`done` gets no second note, so udl's own merge and the watcher's event for it write once. A pull closed without merging moves its task back to `in_progress` with the pull's last comment as a `system` note. `orch_merge` (`key`, `confirm` = `owner/repo#number`, `sha` = the head the caller saw, which is required) re-assesses at that head and refuses a pull that moved since it was listed, rather than merging blind; `github_merge_pull` is called as udl (it is no agent's operation), and an agent may merge only where the project's `merge = agent` and the task is its own (`orch_merge` is in the implementer's role set for exactly that, and it stays an MCP tool so that path works there too; anyone else is refused by the operation). `merge_list` reads every pull at once and a pull udl cannot read is that row's blocker, not the list's failure; `done` is the last 14 days. **27.6's deferred branch:** where the project's `merge = agent` and the caller is the task's own assignee, `orch_ready` merges through `_merge_now` — the same call `orch_merge` makes — instead of moving the task to `ready`. **Promotion:** `orch_promotion` lists what is `merged` and not `done`; `orch_promote` (`tasks`, `note`) refuses a project with no `promotion` branches and is all-or-nothing (every key checked before one is written), then marks them `done` in one batch with a `progress` note each. **Tests, written first:** 31 in `tests/unit/plugins/apps/orch/test_merge_operations.py` (the hints and shapes; the list's four groups; a pull udl cannot read as its own blocker; the linked head; a stack of three in merge order; the 14 days; merging at its head; a moved head refused and the listed head merged; the wrong `confirm`; not-ready; a person's merge done at once; an agent refused, and allowed where the project says so; `ready`'s merge branch, its other side, and that it really merges; the rebase notes; the action becoming `now`; no pull; promote all-or-nothing and with no promotion branches; and the card's done-when — three pulls merged in order, each prompting the next), 10 in `test_merge_after.py` (the state a merge leaves, the note, who merged, the stack, `stacked`, the action, a merge seen twice writing once, a closed pull, one nobody started), 7 in `test_merge_watcher.py` (both event paths, the review bot's merge, a person's merge, an already-merged task, a pull no task has, and the loop's callback), and 7 in `test_merge.py` for `order()` alone. `openapi.json` and the typed client regenerated. **Not done here, and why:** the merged task's worktree is taken away by 27.5's `remove()` (`workspace.py`), which is READY in #52 but not in `origin/main` when this branched, so this PR leaves the task's `worktree` field as it is; wiring it is the two lines `_after_merge` used to hold, once #52 is in. The tests are unit-level rather than the integration file the card names: the fake `github` app inside a real `UDLService` covers the same path (the pull read, the merge, the notes) and the integration harness would repeat it.

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

**28.7 Auto merge** · needs 28.3, 27.8 · size L (O-D13, O-C13)
- Build:
  - **The setting.** `Project` gains `auto_merge: bool = False` and `merger: str | None` (an agent's name).
    - Saving checks that the merger is an agent with role `reviewer` or `manager`, whose `projects` include this one (or are all).
    - Turning auto merge on needs a merger.
    - `merge = human | agent` is unchanged: auto merge sits beside it, and wins while it is on.
  - **`orch_set_auto_merge`** (`project`, `on`, `merger?`), owner only. An agent's or a chat's token is refused, so no model can give itself the merge. It publishes `orch.project.changed`. CLI: `udl orch projects auto-merge udl --on --merger claude`.
  - **It is the only writer of `auto_merge` and `merger`.** `orch_set_project`, the importer and every other write keep both as stored: a change to either through them is refused (`NotAllowed`, naming `orch_set_auto_merge`). Otherwise a chat's owner-level token could turn auto merge on through `orch_set_project`.
  - **The merger's turn.**
    - The merger's standing session (27.8's; a reviewer gets one too) is prompted with each task of the project that becomes `ready`, in Ready to merge's order (28.3). The task comes as data: its spec, the PR's URL, the head SHA and the diff's stat.
    - It reviews the diff against the task's spec and the project's rules.
    - Then it calls `orch_merge` (`task`, `confirm`), or `orch_review_task` with `changes` and its reasons. That moves the task back to `in_progress`, and the reasons go to its agent as a note.
    - One PR at a time per project, and the queue always moves on:
      - after a merge, the next ready task is prompted;
      - after `changes`, the task has left `ready`, so the next ready task that isn't stacked on it is prompted at once (one stacked on it waits for it, as it would for a person);
      - the merger's own task (O-C13) is merged by it only when the project has a review bot. Without one, it is skipped: it stays under Ready now marked "waiting for a person: the merger's own task", and the next one not stacked on it is prompted.
    - A merger may call `orch_review_task` on any of its project's `ready` tasks, outside its own subtree too, so a `manager` merger isn't refused the review it needs.
  - **`orch_merge` for the merger** (it widens 28.3's rule). The merger may merge when:
    - it is the project's merger and auto merge is on;
    - the task is `ready`;
    - it is not the task's assignee, or the project has a review bot (O-C13).

    Readiness is re-checked for the current head, as it is for anyone. The merge's note and `orch.task.merged` carry `by` and `auto: true`.
  - **The lists:** `orch_merge_list` and `orch_inbox` carry `auto_merge` and `merger`, and each ready row carries what the merger is doing with it (`reviewing`, `next`, `changes asked`).
  - **udl's own project** (30.3) turns it on, with `claude` as its merger. That is the user's standing rule since 17 Nov 2026: Claude merges a PR once CI is green and the review bot has nothing open.
- Tests first: `tests/integration/plugins/apps/orch/test_auto_merge.py`:
  - with auto merge on, the merger merges two stacked PRs in order, with nobody else acting; with it off, nothing is merged;
  - the merger can't merge another project's task, or one whose head moved;
  - the merger's own task: merged by it with a review bot set; without one, skipped and marked, with the next one merged;
  - a `changes` verdict moves on to the next ready task that isn't stacked on it, and a `manager` merger can review a task outside its subtree;
  - `orch_set_project` can't change `auto_merge` or `merger`;
  - an implementer's token is refused `orch_merge` while auto merge is on;
  - `orch_set_auto_merge` is refused to an agent's token and to a chat's;
  - a merger that isn't a reviewer or a manager can't be saved.
- Done when: on the fakes, a reviewer merger takes a stack of three PRs from Ready to merge to merged by itself.

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
- **Built (Claude, 20 Nov 2026, PR #44):** the Stitch project [udl orchestrator](https://stitch.withgoogle.com/projects/15107434053989671605), 42 screens: the ten the card names and a Q&A thread on the phone (the desktop shows the list and a thread side by side), each at desktop and phone width, light and dark. Their PNGs, the design system ([`DESIGN.md`](../web/plugins/orch/design/DESIGN.md)) and an index with every screen's Stitch link are in [`web/plugins/orch/design/`](../web/plugins/orch/design/README.md); each UI task's card below links its own. Built before 24.1 closed: the screens need only 18.5's themes, which are merged.
  - **The design system** is daisyUI 5.8.1's stock `light` and `dark`, the two themes the shell turns on (`web/packages/shell/src/index.css`): their colours as hex, the radius (boxes and selectors 8 px, fields 4 px), Tailwind's type scale, the shell's frame, and udl's vocabulary (a badge per task state, a dot per agent status, a chip per link kind). In Stitch it is two design systems, "udl daisyUI light" (the project's) and "udl daisyUI dark". Inter stands in for the shell's system font.
  - **Decision: Stitch draws the layout, daisyUI colours it.** Stitch colours every screen from a Material palette it derives itself, whatever hex values the design system gives (its primary came out `#2a00b5`, not `#422AD5`; its error `#ba1a1a`, not `#FF627D`). So each screen's HTML was re-themed onto daisyUI's CSS variables (Stitch's colour roles, Tailwind's palettes and any hex, by role or by lightness) and rendered in Chromium (Playwright) under `data-theme="light"` and `"dark"`; those renders and that HTML replaced Stitch's own, so the project and the folder show the same pictures. A dark screen is its light screen's layout with daisyUI's dark tokens, nothing else; in Stitch it is a copy titled "… (Dark)". The rule, every mapping and the steps are written down in [`RETHEME.md`](../web/plugins/orch/design/RETHEME.md), so a screen can be redone on any machine; the scripts that apply it are one-off tooling, kept on jeff-wiggle in `~/Documents/code/udl-wt/stitch-tools/` rather than under the gate.
  - **Hit:** Stitch kept adding what udl's shell doesn't have (an avatar in the navbar, a version line under the menu, a back-arrow top bar on phones, a stray "Feature" nav item) and making up details (an `orch/dispatch.yaml`, a "production-vault", an encryption claim, OpenCode 0.18): five screens were edited in Stitch to take them out, and the avatar is dropped when re-theming. An edit in Stitch makes a new screen, so the superseded ones became the dark copies; the other dark copies, made by edits, left three unused auto-named design systems in the project, which the MCP can't delete. Some screens came back on daisyUI 4's CSS (or Tailwind 4's): those keep it and get daisyUI 5.8.1's colours through its variables, so their component shapes are daisyUI 4's; the folder's README says to build with daisyUI 5's own components.
- **The user's look (Q-8, 21 Nov 2026), and the changes (Claude, 21 Nov 2026):** six answers.
  - **The answers:** four are new work as well as screens, recorded as O-D13–O-D16 with Claude's O-C13–O-C16:
    - auto merge by a designated model (28.7);
    - T-shirt sizes and progress graphed over time (26.8, 27.10, 29.11);
    - a chat sidebar to talk to Claude or ChatGPT from the web UI (27.11, 29.12);
    - OpenAI models as well as Claude's (25.12, 25.13; not drawn).

    The other two are screen changes: the Feature screens' cards needed clearer edges, and an open PR needed a link to it.
  - **What changed on the screens:**
    - **Feature:** cards have a `base-content` 20 % border and a shadow in both themes. Tasks have a Size column. The badges are udl's vocabulary, which the phone's dark theme couldn't read before. A Progress card holds the feature's burn-up and its forecast.
    - **Board:** sizes on every card, points per feature and per agent queue, and open PRs as chips.
    - **Task:** the size, its estimate and the time worked so far.
    - **Agent:** sizes in the queue, and the PR chips.
    - **Inbox:** PR links, and auto merge's merger.
    - **Ready to merge:** the auto merge switch and its merger, a link on every PR's title, and the merger's state on each row.
    - **Every screen:** a Chat button in the navbar, and "Progress" in the desktop nav.
    - **New screens:** Progress, and the chat sidebar (over the Board on a desktop, a full-height sheet on a phone).

    An open PR's chip (`#41` with its checks dot and an icon that says it opens GitHub) is a real link everywhere.
  - **How:** these were edits to each screen's own HTML, not new Stitch generations, so nothing the user had already seen moved. The two new screens were drawn the same way, in the Feature and Board screens' shells. The re-theme and the renders are as before (RETHEME.md). Each screen keeps its Stitch id: the light and dark copies were updated in place, and the new ones are four more pairs. [`DESIGN.md`](../web/plugins/orch/design/DESIGN.md) gained the card edge, sizes, PR chips, auto merge and the chat.
  - **Left in Stitch:** the generations that made the new screens' slots, and an extra first try at one of them, sit in the project under their own titles, unused. The MCP can't delete screens.

**29.2 The UI plugin's skeleton** · needs 24.7, 28.4, 26.8
- Build: `web/plugins/orch/` (React, daisyUI, the SDK), built into `src/udl/plugins/apps/orch/ui/`.
  - Manifest: nav "Work" (Inbox, Board, Agents, Q&A, Merge, To action); routes `inbox`, `board`, `features/$key`, `tasks/$key`, `agents`, `agents/$name`, `questions`, `questions/$key`, `merge`, `actions`; a dashboard widget (the inbox's counts); `generated: { tabs: ["settings"], hide: ["orch_*"] }` so the generated page keeps only Settings.
  - Stores (vanilla Zustand): `inbox`, `board` (features, tasks, agents, suggestions), `sessions`; each refetches on the `orch.*` events it cares about through `ctx.events.subscribe`.
  - `<Markdown>`: `marked` + `DOMPurify`, links opening in a new tab with `rel="noopener"`.
  - `<LinkChip>`: one look per link kind; a PR chip shows its checks and review state from `Link.state`.
- Dependencies: `@dnd-kit/core`, `@dnd-kit/sortable`, `marked`, `dompurify`.
- Tests first: stores against `FakeUdl`; `<Markdown>`'s sanitising (the phase's third bullet).
- Done when: every route renders a heading inside the shell, isolated in its shadow root.
- **Q-8 (user, 21 Nov 2026):** the manifest's nav gets "Progress" after Board (route `progress`, 29.11). `<LinkChip>` for an open PR is a real link to it (`target="_blank"`, `rel="noopener"`): the checks dot, `#n`, and an icon that says it opens GitHub. A `<SizeChip>` shows `S`/`M`/`L`/`XL`, with the points in its tooltip. (The navbar's Chat button is 29.12's.)

**29.3 Inbox** · needs 29.2, 28.7
- Build: the home route: the three sections of `orch_inbox` as cards with counts, the agents strip (status dots, the task each is on), blocking questions pinned on top. Answering a question with `options` is one click; others open the thread.
- Done when: its e2e test answers a question from the inbox and sees it leave.
- **Screens (29.1, Google Stitch):** **Inbox**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=4fbab1751017480cacc6609079604f69) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=fddee541cf074971aec7566bd70cf58a), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=6c577ec04aa5438a903a91626e61388f) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=ebfcc3fa8f2d426a99640ee1e0a0c652); `web/plugins/orch/design/inbox-{light,dark}-{desktop,phone}.png`.
- **Q-8 (user, 21 Nov 2026):** the Ready to merge card says whether auto merge is on, and who the merger is (28.7). Every PR number on it is a link to the PR.

**29.4 Board** · needs 29.2, 26.8, 27.10
- Build: three panes: **features** (collapsible, each with its tasks as cards showing key, title, state and link chips; filters by project, state and text, kept in the URL), **agents** (a column per agent: its queue as a sortable list, its current task on top, its status), and **suggested** (from `orch_suggest`, each card showing its reasons and the agent it suits; "Queue" on a card accepts the suggestion).
  - Drags: a task or a suggestion onto an agent → `orch_assign` (at the drop position); within a queue → `orch_reorder_queue`; from a queue back to the features pane → `orch_unassign`. A drop the server refuses (a `human` task onto an agent, unmet `needs`) snaps back with the error's hint as a toast. Dropping a task whose `needs` are only `ready` asks "stack on #N?".
  - The keyboard path and the menu path ("Assign to…") call the same functions.
- Done when: its e2e test drags a suggested task to a fake agent and watches it go `in_progress` live.
- **Screens (29.1, Google Stitch):** **Board**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=a01ede4ba0104abf9ad5eb2765efda0d) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=7051af8570a747ab841bc745f8ec20c2), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=714267e7946f417488a3f789e8f42688) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=35c29045a2ba4d27844663b7aa1044bd); `web/plugins/orch/design/board-{light,dark}-{desktop,phone}.png`.
- **Q-8 (user, 21 Nov 2026):** each task card shows its size and, while its PR is open, the PR's chip (a link to it). Each feature's bar is its points merged out of its points. Each agent's column gives the points queued and their estimate in hours (27.10).

**29.5 Feature and task pages** · needs 29.2, 26.8, 27.10
- Build: **Feature:** spec (markdown, editable), its tasks in order with states, links, notes, "Add task", "Split". **Task:** breadcrumb back to its feature, spec, state with the moves this user may make, assignee, needs and what it unblocks (as links), every link by kind, the PR's readiness with its blockers, notes as a timeline with an "Add note" box, its questions, its session (a link to the agent's page at that session).
- Done when: every key and URL on both pages is a link, and a task's page reaches its feature and back.
- **Screens (29.1, Google Stitch):** **Feature**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=d2657d70cd2d41f29da24fb19918b6cf) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=4dc4588af1d843e8820efd62c771d5cc), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=1d4c2ed96c2a4aa189e87ef8718fccf9) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=51abd9cf242f4b79abe75aa577dddcd2); `web/plugins/orch/design/feature-{light,dark}-{desktop,phone}.png`; **Task**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=6423d930355047cb84df2a85eaaca868) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=bd90d38f4a3b449495a9f43bff0fa93b), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=22c404ee1d564a5a8e72da8d588f5cd4) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=b4dd135234bd406fab8a0321554992a3); `web/plugins/orch/design/task-{light,dark}-{desktop,phone}.png`.
- **Q-8 (user, 21 Nov 2026):** **Feature:** each card has a clear edge in both themes (DESIGN.md's card rule: a `base-content` 20 % border and a shadow), and tasks get a Size column. An open PR is a chip that links to it. A Progress card shows the points merged, the feature's burn-up, its forecast, and the rate behind it (27.10). **Task:** its size, its estimate and the time worked so far (`orch_estimate`), and its PR as a link.

**29.6 Agents** · needs 29.2, 26.8
- Build: **Agents:** the hierarchy as a tree (humans at the root); dragging an agent onto a manager sets `manager` ("assign models to models"), refused with a reason for a cycle or a non-manager; an editor for an agent (runner, model from `orch_list_runners`' list for its host, host, role, brief, and a manager's four powers as switches). **Agent:** status, queue (sortable), token rotate, pause / resume / stop, and the session: a live transcript from the SSE stream (text, tool calls collapsed to a line, usage and cost), a box to send it a message (owner only).
- Done when: its e2e test builds a manager with two reports by dragging, and watches a fake session stream.
- **Screens (29.1, Google Stitch):** **Agents**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=7791ff8dfd96441a8f407efbda0f23b5) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=d17fed4e665c45a880b2114968ccdcb3), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=ddefdd0cfbfb4718a0a6d7650b5754f1) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=0e287012078b43938fff6f0b4b492fa8); `web/plugins/orch/design/agents-{light,dark}-{desktop,phone}.png`; **Agent**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=da1624bace6c4b98a0d64c45d05165a0) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=fa261cca352745de8be29520980348ed), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=e4a4d6e0423e4ce989f0222376cac769) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=27ecbc8414ca40a091f73a3dac131dc2); `web/plugins/orch/design/agent-{light,dark}-{desktop,phone}.png`.
- **Q-8 (user, 21 Nov 2026):** on an agent's page, the queue shows each task's size, the current task's open PR as a chip, and the queue's points.

**29.7 Q&A** · needs 29.2
- Build: open questions (blocking first) and answered ones with their outcomes; a thread view with the question, what it is about (link chips), option buttons or a markdown answer box, follow-ups, "Close with outcome". New questions appear live, with a count in the nav.
- Done when: its e2e test sees a question arrive, answers it, and finds the decision on the task's page.
- **Screens (29.1, Google Stitch):** **Q&A**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=47ac5610e253455c9b7f4ad2b27f6828) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=bcb6abba194a4d818f16dbbf0488fcf2), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=efa3ed1403154b6faf6f655196af030b) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=cb6281e6a610495a94e0ac79aabf46cf); `web/plugins/orch/design/qa-{light,dark}-{desktop,phone}.png`; **Q&A thread**: phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=39735072e25c4bf08794d011655189f8) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=046e8294e25c40b9b1c3ff330ec361db); `web/plugins/orch/design/qa-thread-{light,dark}-phone.png`.

**29.8 Ready to merge, To action** · needs 29.2, 28.7
- Build: **Merge:** Ready now (per repo, numbered in merge order, each with its PR link, checks, the bot's verdict and a Merge button that asks for the `owner/repo#n` confirmation), Coming soon (with blockers), Held (with reasons), Done (with what followed). **To action:** Now / After / Later, each with a result box and Done.
- Done when: its e2e test merges a ready PR on `FakeGitHub` and sees the next in the stack flagged for rebase.
- **Screens (29.1, Google Stitch):** **Ready to merge**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=dad3482589b143abb7c0266c6562e6bc) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=2584378a5ef04df0a0e8f045046b3b6b), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=529dd7d5434a491c82404792620125bd) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=48bf3cac921645f28f8a45e23e97fc56); `web/plugins/orch/design/merge-{light,dark}-{desktop,phone}.png`; **To action**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=0c0b84dc96674e279f156dce50032e96) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=0fd28d661b034e41b6abd24c82305a09), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=f55c20038eff4331a1ea94f4b4254a54) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=d79a7ae6f71940d79d37fa5b8244a3d1); `web/plugins/orch/design/actions-{light,dark}-{desktop,phone}.png`.
- **Q-8 (user, 21 Nov 2026):** above Ready now, the project's auto merge switch, with its merger (owner only, 28.7). Each PR's title is a link to it, with an icon saying so. While auto merge is on, each ready row says what the merger is doing (`reviewing`, `next`), and Done says who merged it ("claude (auto merge)"). A person can still merge one by hand.

**29.9 Connections and import** · needs 24.7, 29.2
- Build: no hand-built pages: `generated` manifest entries and `ui_field()` hints so the generated pages for `github`, `openrouter`, hosts, secrets status, runners, projects and repos read well (groups, order, labels, the `account` and `markdown` widgets), and two contributions: a `resultViews` one that renders `orch_import_plan`'s report (what a dry run would create, the unplaced lines), and an `operationViews` one for `orch_list_runners`, the **Machines** view: a card per host with each harness's version, update available, signed in and plan, limit state and guards, with Install / Update, Set up and Sign in buttons. Sign in opens the relay (25.11): the harness's link as a button, a box for the code, and the result. This is Phase 24's point proved on real plugins: write down in the phase's decisions anything the generated UI couldn't do and how it was solved.
- Done when: a 1Password token, an OpenRouter key by `op://` reference, a GitHub account, a host and a project with a repo can all be set up from the web alone.
- **Screens (29.1, Google Stitch):** **Connections: a generated page**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=126785af99b04553983316310fb2fd68) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=b4193d88b702431088fb00a576accdd4), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=7272b669d4e243529dc6dd6406ee8579) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=80b6bb3f19c449829bd03c30c808b326); `web/plugins/orch/design/connections-{light,dark}-{desktop,phone}.png`.

**29.10 End to end** · needs 29.3–29.9, 29.11, 29.12
- Build: `web/e2e/orch.spec.ts`: 28.6's story through the web UI only, with `tests/support/e2e.py` extended to start `FakeGitHub`, a `FakeRunner` plugin and a temp git repo.
- Done when: `make e2e` passes in both themes.
- **Q-8 (user, 21 Nov 2026):** it covers 29.11 and 29.12 too: the Progress page for a fixture history, and a chat that allows a permission and assigns a task.

**29.11 Progress** · needs 29.2, 27.10 · size L (O-D14)
- Build: the route `progress`, with a "Progress" nav item after Board.
  - **Stats:** points merged of all the points, the forecast with its spread, the pace, and the estimate ratio.
  - **A burn-up:** the scope, the points merged, the forecast line to its likely date with the early-to-late range shaded, and today marked. It is an SVG component, `<BurnUp>`, with no chart library: it is two lines and a band.
  - **By feature:** a bar, the points and the forecast for each.
  - **Recently merged:** size, estimate, actual, and their ratio (amber over 1.25×, green under 0.9×).
  - **Hours per point by model,** and per size.
  - The project and "since" are kept in the URL. On a phone it is the same page, stacked.
- Tests first: `<BurnUp>` against fixed series (each point placed, the band between early and late, nothing drawn past the last day); the page's states against `FakeUdl`.
- Done when: its e2e test shows the burn-up for a fixture history, with the forecast date the API gives.
- **Screens (29.1, Google Stitch):** **Progress**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=b66963cc7a234f828911ccd061168ef7) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=c060647fdc644d2a9b79f9769af789f4), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=c1a79deffe0d4765913f560fbdc30867) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=1bfeab5df26840ee8d0ed3d7fb3e1401); `web/plugins/orch/design/progress-{light,dark}-{desktop,phone}.png`.

**29.12 The chat sidebar** · needs 29.2, 27.11 · size XL (O-D15, O-C16)
- Build:
  - **In the shell:** a contribution point, `panels` (`PanelProps`: `open`, `close()`, `popOut()`).
    - The navbar draws a button per panel; the chat's is its icon and "Chat", with Ctrl+J.
    - An open panel gets 400 px on the right: over the page, docked beside it on a window 1600 px or wider, and a full-height sheet on a phone.
    - A popped-out panel is the route `/ui/panel/<plugin>/<id>`, opened in its own window (`window.open`), drawing the same contribution at full size.
    - Whether it is open, and its width, are remembered per browser, in `localStorage` (wrapped).
  - **The chat** (`orch`'s contribution):
    - **A header:** the runner picker (Claude Code or Codex, the model, the host and the directory), and New chat, Pop out and Close.
    - **The context line:** model · host · directory · as whom.
    - **The transcript:** the user's messages on the right; the model's as sanitised markdown; each tool call as one collapsed line with its result, which opens.
    - **Permission cards:** the command or tool with its input in a block, where it would run, why it asked, and Allow once, Allow for this chat and Deny.
    - **A composer:** `@` completes a task, feature or question key, and `/` the harness's commands. Enter sends and Shift+Enter starts a new line; while a turn runs, it shows Stop.
    - Past chats are in a menu. It is live from the chat's SSE stream, and a reload resumes the chat.
- Tests first:
  - Vitest: the store (a permission card appears, and goes once answered; a reload resumes) and the sanitising of the model's markdown;
  - Playwright: open it with Ctrl+J, send, answer a permission prompt, pop out and keep the same chat, in both themes, at desktop and phone widths.
- Done when: its e2e test, on a `FakeRunner` chat, sends a message, allows a permission, and sees the result in the transcript and on the Board.
- **Screens (29.1, Google Stitch):** **The chat sidebar**: desktop [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=77e7e5da0d184db7a599dc178b3dbc67) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=4292c722ab044c828fa5bd11a2571099), phone [light](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=2d8de97f1b714b84be450766032c5d1e) / [dark](https://stitch.withgoogle.com/projects/15107434053989671605?node-id=858586079f6c4119b1421a44832db6b0); `web/plugins/orch/design/chat-{light,dark}-{desktop,phone}.png`.

**Phase 29 is done when:** with `udl serve` on real connections, the user assigns a task by dragging it, answers the agent's question, and merges its PR, all from `/ui/p/orch/`.

---

## Phase 30: Moving in, docs and the live check

**30.1 Security review** · needs Phase 29
- Check, each with a test where one is missing: agent tokens (27.2) can't reach admin operations or another agent's work; an agent can't merge except under `merge = agent` on its own ready PR; text from PRs, bots and CI reaches a model only as fenced data; `HostShell` never builds a command from unquoted input; secrets (the 1Password token, agent tokens, the OpenCode password, `op://` values) appear in no record, log, `argv`, event or transcript; the generated UI never echoes a stored secret; markdown is sanitised. Findings are fixed test-first and listed here.
- **Q-8 (user, 21 Nov 2026):** also check, each with a test: no model can turn auto merge on or name itself the merger, through `orch_set_auto_merge` or any other write, and a merger merges its own task only on a review bot's clean verdict, and never another project's (O-C13); a chat never gets a flag that skips permissions, its token can't make tokens, open chats or change auto merge, and the token is gone when the chat ends (O-C15); udl never opens `~/.codex/auth.json` (25.12).

**30.2 Docs** · needs 30.1
- Build: README (a "Work" section, the connections), `docs/orchestrator.md` (concepts, a walkthrough from import to merge, agent roles and powers, running agents on another host, the events), `docs/plugins.md` (writing an app plugin and a runner; what the generated UI reads and how to guide it), a config example for `[apps.orch]` and `[secrets.onepassword]`.

**30.3 (Claude) Moving udl's plans in** · needs 30.1
- Create project `udl` (`merge = human`, auto merge on with `claude` as its merger (28.7, the user's standing rule since 17 Nov 2026), no promotion branches, repo `yelrom0/udl`, gate `make check`). Import planning.md and 03-proxmox.md; check the report line by line; fix what the importer placed wrongly by hand, and the importer by test where it was the importer's fault. Then replace the trackers in the markdown with one line pointing at `udl orch` and move the files to `planning/archive/` (O-D8). `planning/agent-queues/` goes too: the agents' queues are in udl.
- Done when: `udl orch features` lists Phases 13–22 with their tasks, and nothing in `planning/` is a live tracker.

**30.4 (Claude + user) Live check** · needs 30.3
1. The user makes the 1Password service account (read access to one vault) and runs `udl secrets onepassword login`.
2. Claude connects OpenRouter (by reference), GitHub, and the hosts `local` and `thevault`; creates `deepseek-a` (`opencode`, local) and one `claude-code` agent as `reviewer`.
3. The user drags a small real udl task to `deepseek-a`. It must reach Ready to merge by itself; Claude reviews and merges it.
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
