# udl (Ultra Data Layer)

**The shared data layer for people and AI agents.** udl downloads, reads, watches and automates data,
and keeps it where every person and every agent on a team can use it. It is extensible by plugins and AI
native, but never needs AI to work.

The idea in one line: **udl is the back end, and AI is the front end.** AI is good at turning data into
dashboards, code and answers, and it is getting good at working as several agents together. What it lacks
is one back end that many agents and many people share, and that both can read. udl is that back end.
Your AI (Claude, ChatGPT, Codex, a local model) reads and writes udl's data over MCP, while people use the
web UI or the command line.

Everything udl does is available three ways, with the same options and results:

- the **command line** (`udl`)
- an **HTTP API** (`udl serve`)
- an **MCP server** (`udl mcp`), so AI agents can use all of it: download things, read pages, teach udl
  new sites, set up automations

```console
$ udl https://youtu.be/jNQXAC9IVRw
✓ https://www.youtube.com/watch?v=jNQXAC9IVRw → ~/Downloads/udl/youtube.com/Me at the zoo.webm (474.5 kB, yt-dlp)
```

## Contents

- [What udl is becoming](#what-udl-is-becoming)
- [Where it stands](#where-it-stands)
- [Install](#install)
- [Downloading](#downloading)
- [How udl picks a downloader](#how-udl-picks-a-downloader)
- [Sites and strategies](#sites-and-strategies)
- [Sites that need a login](#sites-that-need-a-login)
- [Tag chains: sites no downloader handles](#tag-chains-sites-no-downloader-handles)
- [Reading pages a plain fetch can't](#reading-pages-a-plain-fetch-cant)
- [Databases](#databases)
- [Automations](#automations)
- [Schedules](#schedules)
- [Accounts](#accounts)
- [Hosts](#hosts)
- [Plugins](#plugins)
- [The HTTP API](#the-http-api)
- [The MCP server](#the-mcp-server)
- [Configuration](#configuration)
- [Security](#security)
- [Development](#development)

## What udl is becoming

udl started as a downloader that works out how to download any URL. It has grown into a local
automation hub (events, subscriptions, schedules, integrations, a page fetcher), and it is heading
towards a place where a team of people and AI agents works on the same data:

```
 People: web UI · CLI · their own AI (Claude, ChatGPT, Codex…) over MCP
                               |
 HUB    workspaces, each with its own projects, people, secrets and data:
        plans and tasks · docs and memories · datasets · events and automations · who did what, when
                               |
        links to the machines people work on (each link its own way: wss, SSH or a private network)
           ┌───────────────────┴───────────────────┐
 CLIENT (your workstation)                  CLIENT (a teammate's laptop)
   runs agents: Claude Code, OpenCode, Codex   linked to several hubs, as a different person on each
   downloads, fetches, scripts, on this machine  its own Gmail, Slack and browser apps push data up
   its owner decides what each hub may use     its secrets never leave it
```

- **One hub, many workspaces.** A hub can be one machine of yours or a shared server that many teams use.
  Each workspace has its own members, projects, secrets and data.
- **A client on every machine.** udl itself, linked to one or more hubs: the machine's safe way to and
  from each hub, and its manager. A device belongs to whoever installed it, and its owner decides what
  each hub and each person may use on it.
- **Work, for people and agents alike.** Projects, features and tasks, assigned by dragging them to a
  person or an agent. Agents can manage other agents. Questions, answers and ready-to-merge PRs land in one
  inbox. Agents run through their own harnesses (Claude Code, OpenCode, Codex) and finish their work on
  GitHub.
- **Who did what, when.** Every change is recorded, whether a person, an agent, a device or an automation
  made it, and on whose behalf, in a tamper-evident log. Decisions can be signed with a passkey or an SSH
  key.
- **A data bank.** Datasets that plugins, agents and people fill; docs; memories that every agent reads,
  synced both ways with the files agents already use (`AGENTS.md`, `CLAUDE.md`, Claude's memory folders);
  search over all of it; and conversations (a meeting's transcript, an email) turned into plan changes
  that a person accepts, each traceable to where it was said.
- **Pages.** Dashboards and public read-only pages made from udl's data, written by people or by AI.
- **Pub/sub across everything.** An email about a bug can become a task; a new row in a dataset can start
  an agent; a plugin's events can drive another plugin's actions. Plugins are optional: install only
  what you need.

udl is open source, and it stays self-hostable.

## Where it stands

| | What |
|---|---|
| **Works today** | Downloading anything (yt-dlp, gallery-dl, HTTP, the page's HTML, a browser), learning what works per site, and tag chains for sites nothing handles; several databases at once (SQLite, PostgreSQL, MongoDB) with sync; events, subscriptions and actions; schedules; the framework for integrations and accounts; a page fetcher that reads what a plain fetch can't; secret stores; hosts reached over SSH; CI with a review bot and a locked `main` |
| **Being built** | The web UI, with a generated page for every plugin; 1Password references, OpenRouter, GitHub; agent runners (Claude Code, OpenCode); the orchestrator: projects, features, tasks, agents and managers, the dispatcher, Q&A, ready to merge ([planning/02-agent-orchastrator.md](planning/02-agent-orchastrator.md)) |
| **Planned next** | Workspaces and hubs, people and sign-in, the audit trail, clients on every machine, macOS and a Codex runner, the data bank, pages, analytics, hosting a hub for others, browser-driven integrations, sandboxed agents ([planning/04-shared-data-layer.md](planning/04-shared-data-layer.md)) |
| **After that** | A browser that passes for a person's and solves anti-bot checks, Gmail, Slack, a web-page watcher, local AI, the rest of the web UI, Proxmox ([planning.md](planning.md)) |

## Install

udl needs Python 3.14 and [uv](https://docs.astral.sh/uv/). Install it as a tool, with the
extras you want:

```sh
uv tool install --editable '.[browser]'    # from a checkout of this repository
uv run playwright install chromium         # (in the checkout) Chromium, for the browser downloader
```

| Extra | Adds |
|---|---|
| `browser` | the `browser` downloader (Playwright), `udl browser login`, and the fetcher's browser |
| `postgres` | the `postgres` database plugin (psycopg) |
| `mongodb` | the `mongodb` database plugin (pymongo) |

Combine extras as `'.[browser,postgres]'`. After you change udl's dependencies or entry points,
reinstall with `uv tool install --editable '.[browser]' --reinstall`. `udl plugins list` shows
which plugins can run and what is missing for the rest. The http downloader can also use `curl`
or `wget` when they are installed.

## Downloading

```sh
udl <url>...                       # the same as `udl get <url>...`
udl get -o ~/Videos <url>          # into a folder
udl get -t '{site}/{title}.{ext}' <url>   # where each file goes in the folder
udl get -i urls.txt                # the URLs in a file, one a line (- for stdin)
udl get --tool yt-dlp <url>        # with this downloader only
udl get --reprobe <url>            # ignore what worked on the site before
udl get --no-record <url>          # remember nothing
udl get -j 8 <url>...              # at most 8 downloads at once
```

Several URLs download at once, with at most `max_concurrent` downloads in all and `max_per_site`
from any one site. Progress bars go to stderr and the results to stdout. `--json` prints the
results as JSON. Ctrl-C cancels the downloads. udl exits with 0 when every download worked,
1 when some failed, 2 for a usage or config error, and 130 when cancelled.

| Command | What it does |
|---|---|
| `udl probe <url> [--all] [--tool T]` | which downloaders can download a URL, and what media each sees, without downloading |
| `udl history [--site S] [--status S] [-n N]` | the downloads, newest first |
| `udl jobs show <id>` | one download: where it is, and what came of it |
| `udl jobs cancel <id>` | cancel a download, or a whole batch. A udl cancels only the downloads it runs: Ctrl-C cancels `udl get`'s, and the API's or MCP server's cancel cancels theirs |

**Where files go.** The folder is the first of these that is set: `-o/--output`, the site
plugin's folder, the site's `default_output_dir`, then the config's `download_dir` (by default
`~/Downloads/udl`). Inside it, `filename_template` (by default `{site}/{title}.{ext}`) names each
file. `{site}` is the site's key (for example `example.com`), and the downloader fills in the
other fields. A template must stay inside the folder, and a field's value can never add a
folder or leave one.

## How udl picks a downloader

For each URL, udl tries candidates in this order until a download actually succeeds, meaning
real, non-empty files in the output folder:

1. **The request's tool** (`--tool`): if given, the only candidate.
2. **A site plugin's strategy**, for example the `youtube` plugin's yt-dlp.
3. **Strategies udl has learned or been told** for the site, from every connected database.
4. **The probe chain**: first the downloaders that recognise the URL offline (a yt-dlp or
   gallery-dl extractor, a media file's extension), then the rest in `probe_order`: `http`,
   `yt-dlp`, `gallery-dl`, `scraper` and, slowest, `browser`.

What goes wrong decides what happens next:

| Error | What udl does |
|---|---|
| Not supported, or no media found | tries the next candidate |
| The site wants a login (401/403) | tries again with the site's cookies, then with the browser |
| Rate limited, or a network problem | waits, then tries the same tool again |
| The content is gone (404) | stops |

udl then remembers the downloader that worked as a strategy for the site, and counts each
strategy's successes and failures. After `heal_threshold` failures in a row (3 by default), a
learned strategy is disabled and udl probes again. It never disables a strategy you set by hand;
it only warns.

When nothing works, udl says why, and suggests teaching it the site with a
[tag chain](#tag-chains-sites-no-downloader-handles).

## Sites and strategies

```sh
udl sites list                       # the sites udl knows
udl sites show example.com           # a site's strategies, with their counts
udl sites set example.com --tool gallery-dl
udl sites set example.com --tool yt-dlp --pattern '^/shorts/' -O format=best
udl sites rm example.com             # forget a site and its strategies (its history stays)
```

A site is identified by its key, its registrable domain (`www.example.co.uk` belongs to
`example.co.uk`). Commands accept a key, a host, or any URL on the site. `--pattern` is a regex
matched against the URL's path, so one site can use different tools for different sections. `-O
KEY=VALUE` sets one of the downloader's options (JSON values are allowed).

## Sites that need a login

udl tries a download without cookies first. When the site asks for a login, it tries again with
the site's cookies, from two places:

- **udl's browser profile**: `udl browser login <url>` opens Chromium on udl's own profile. Sign in,
  then press Enter (or close the browser). udl's downloads from that site then send its cookies.
- **Your own browser's cookies**, if you set `cookies_from_browser = "firefox"` (or `chrome`,
  `brave` and so on). They are read with yt-dlp's cookie readers.

A strategy that needed a login is saved with `requires_auth`, so udl sends the cookies from the
first try next time.

## Tag chains: sites no downloader handles

A tag chain is a list of steps from a page to its media, saved for a site. The easiest way to
make one is the wizard: it suggests chains, you pick one, it tests it and saves it.

```sh
udl chain wizard https://example.com/watch/1
```

The steps one at a time:

```sh
udl chain suggest https://example.com/watch/1      # chains for the media on the page, best first
udl chain test https://example.com/watch/1 'iframe#player@src >> video source@src'
udl chain set example.com 'iframe#player@src >> video source@src' --pattern '^/watch/'
udl chain export example.com > example.yaml        # a chain file
udl chain import example.yaml                      # e.g. on another machine
```

In the one-line form, steps are separated by `>>`:

- **A CSS selector**, then `@attr` or `::text` to take from what it selects. A selector after a
  step that found URLs opens them first.
- **Other steps:** `regex:PATTERN`, `json:PATH` and `follow`. In the browser (`--browser`) you
  can also use `click:SELECTOR`, `wait_for:SELECTOR` and `network:PATTERN`.
- **How many to keep:** a step keeps its first find; `| all` or `| best` keeps all of them, or
  the best one.

A chain runs in the scraper (static HTML) or in the browser, and hands the media off to `http`
or `yt-dlp`. Chains set by hand are tried before anything udl has learned.

## Reading pages a plain fetch can't

`udl fetch` reads a page's content (as Markdown, text or HTML) when a plain request gets a 5xx, a 403,
an anti-bot check, a page built by JavaScript, or a login wall. It is also what an AI agent can call
over MCP (`fetch_pages`) when its own fetch tool is turned away.

```sh
udl fetch https://example.com/a https://example.org/b   # the same as `udl fetch get …`
udl fetch get -F text --select 'main article' <url>      # text, only what the selector matches
udl fetch get --via browser <url>                        # start at the browser
udl fetch list                                           # the fetches (batches), newest first
udl fetch show <id>                                      # a batch: each URL's status and content
udl fetch page <page-id> -F html                         # a page again, in another format
udl fetch challenges                                     # the sites waiting for you to pass a check
udl fetch solve                                          # pass them, a tab per site
```

- **The ladder:** each URL climbs only as far as it has to: a plain request, then with the site's
  cookies, then udl's browser, then **you**, at an anti-bot check udl can't pass. The rung that worked is
  remembered per site.
- **Batches and the queue:** the URLs of one request are a batch. Each site has its own lane, with limits
  and delays; a site's check holds its other URLs until it is passed, in whichever udl fetches them.
- **At a check:** `--person ask` (the default, `[fetch] person`) opens one window on udl's profile with a
  tab per waiting site; `wait` waits for someone to run `udl fetch solve`; `fail` gives up. A clearance
  you earn is kept.
- **Cookies, both ways:** `udl browser cookies import cookies.txt` (or a JSON export; or, with `--site`,
  a `Cookie:` header or `--from-browser firefox`) brings cookies into udl's profile, with the User-Agent
  they belong to; `udl browser cookies list` and `rm` show and delete them (never their values).

## Databases

udl keeps sites, strategies, download history and its other records in one or more databases. With no
configuration it uses a single SQLite file (`~/.local/share/udl/udl.db`). Each database is a
plugin:

| Plugin | URL | Install |
|---|---|---|
| `sqlite` | `sqlite:///~/.local/share/udl/udl.db` | built in |
| `postgres` | `postgresql://udl@nas/udl` | the `postgres` extra |
| `mongodb` | `mongodb://nas/udl` | the `mongodb` extra |
| `memory` | `memory://` (forgets everything on exit) | built in |

You can connect several at once, for example a local SQLite file plus a PostgreSQL server shared
by several machines:

```toml
[db]
read_policy = "first"        # first: the highest-priority connection that knows wins; merge: all of them
write_policy = "primary"     # primary | all | a list of connection names

[[db.connections]]
name = "local"
plugin = "sqlite"
url = "sqlite:///~/.local/share/udl/udl.db"
primary = true

[[db.connections]]
name = "shared"
plugin = "postgres"
url = "postgresql://udl:secret@nas/udl"
priority = 10                # lower is consulted first
read_only = false
```

- **When a database fails:** a connection that fails is skipped with a warning, unless it is the
  primary.
- **Timeouts:** PostgreSQL and MongoDB give up on an unreachable server after 10 s, unless the
  URL sets `connect_timeout` (PostgreSQL) or `serverSelectionTimeoutMS` (MongoDB).
- **Schema:** each database records its schema version. udl migrates older ones when it connects
  and refuses ones made by a newer udl.

```sh
udl db list                                   # the connections, and whether each answers
udl db sync --from local --to shared          # copy what one knows into others
udl db sync --from shared --to local --dry-run
```

`db sync` copies sites, strategies, the downloads that have ended and udl's other records:

- **Ids:** records keep their ids (UUIDs), so a record is the same in every database.
- **Conflicts:** what the target lacks is added. What it has differently is replaced by the
  source's copy, so sync from the database that is right.
- **Nothing is deleted**, so syncing twice changes nothing the second time.
- **Read-only connections** can be synced from, but not into.

## Automations

udl turns what happens into **events** (a download ended, an action's run ended, a schedule came due,
an account's watcher saw something, an event of your own), and runs **actions** for the
**subscriptions** an event matches. An action is a named thing to run, of a kind: `script` (a program
from `[automations] scripts_dir`, never through a shell, with the event as JSON on its stdin),
`operation` (one of udl's operations), `webhook` (an HTTP request), `emit` (an event of your own) or
`notify` (a desktop notification). Its options are templates, rendered with the event
(`"{{ event.data.url }}"`).

```sh
udl actions set hello --kind script -O 'command=["hello.sh"]'      # ~/.config/udl/scripts/hello.sh
udl subs set greet --on custom.hello --action hello --where 'name == "x"'
udl events emit custom.hello --data '{"name": "x"}'
udl runs list                                   # the run, and what the script printed
udl events list                                 # the event, and the run's udl.action.succeeded
```

A subscription on what the orchestrator does tells you when you are needed: a blocking question
an agent asks (`orch.question.asked`, with its `task`, `question` and `blocking`), a task that
moved, was blocked or merged (`orch.task.changed`, `orch.task.blocked`, `orch.task.merged`),
something only you can do (`orch.action.needed`) and an agent with nothing on its queue
(`orch.agent.idle`).

```sh
udl actions set tell-me --kind notify \
  -O 'title=udl: {{ event.data.project }} {{ event.data.task }}' \
  -O 'body={{ event.subject }}'
udl subs set a-blocking-question --on orch.question.asked \
  --where 'blocking == true' --action tell-me
```

- **Filters** are conditions on the event's data: dotted paths compared with `==` `!=` `contains`
  `startswith` `endswith` `~` (a regex) `in` `<` `<=` `>` `>=` `exists`, joined by `and`, `or`,
  `not` and parentheses (`from.address endswith "@bank.com" and not sent_by_udl`).
  `udl events types` lists each event type's fields; a filter that names one its types lack is
  refused when it is saved. `udl subs test <name>` tries a subscription on the latest events.
- **The runtime** runs in `udl serve` (unless `[automations] enabled = false`): it dispatches the
  events (whichever udl published them), runs their actions, fires the schedules and runs the accounts'
  watchers. One udl runs them per database, the one that holds its lease; `udl status` says which. A
  failure that trying again may help is tried again (an action's `retries`, with backoff); a
  subscription's throttle (`10 per 1h`) and `max_event_depth` (events caused by runs caused by
  events...) keep anything from running for ever.
- **Live:** `GET /events/stream` sends the events and the runs' changes as they happen.

## Schedules

A schedule fires `schedule.fired` at its times, like cron, and can run actions itself, so a cron job
is one command:

```sh
udl schedules set nightly --cron '0 3 * * *' --tz Australia/Melbourne --action backup
udl schedules set poll --every 15m --jitter 30s --action check-feed
udl schedules set once --at '2026-12-01T09:00' --action remind
udl schedules preview '0 9 * * MON-FRI'           # the next times it would fire (or 'every 15m')
udl schedules run nightly                         # fire it now, by hand
udl schedules list                                # and `show <name>`, `rm <name>`
```

A schedule's times are worked out in its time zone, across daylight-saving changes. `--misfire skip`
(the default) or `once` says what happens to the times missed while udl was stopped.

## Accounts

An **integration** is a plugin that connects udl to a service or a source; an **account** is one
connected login of it, named by you (`gmail:work`, `gmail:personal`), with any number per integration.
Its credentials live in the secret store. Its operations become commands, routes and MCP tools that take
an `account`, and its watcher turns what happens there into events.

```sh
udl accounts add <integration> <name>     # OAuth opens the browser; tokens are asked for, hidden
udl accounts list
udl accounts show gmail:work              # connected, needing a new sign-in, or failing
udl accounts check gmail:work             # ask the service whether it still works
udl accounts set gmail:work -O KEY=VALUE
udl accounts rm gmail:work                # give the credentials back and forget them
```

Read-only operations that list things can fan out over several accounts at once (`account: "*"`), with
each result tagged by its account. That gives an AI agent several inboxes, workspaces or sign-ins where
its own connector sees one. The first integrations (GitHub, Gmail, Slack, web-page watching) are being
built; see [Where it stands](#where-it-stands).

## Hosts

Hosts are the machines udl's agents run on: `local` (this one, always there), and others reached over
SSH, by a name in your `~/.ssh/config`.

```sh
udl hosts list
udl hosts set --host '{"name": "thevault", "transport": "ssh", "ssh": "thevault", "work_dir": "~/udl-wt"}'
udl hosts check thevault     # uname, and which of git, gh, claude and opencode it has
udl hosts rm thevault
```

Remote commands run in a login shell, with every argument quoted, and a secret is passed on the
command's stdin, never in its arguments.

### Memory, and what gets what

A runaway is stopped before it takes the machine with it, and what is stopped is the thing that ran
away: a gate is `udl-gate-<project>-<key>-<8 hex>`, a turn is `udl-session-<agent>-<8 hex>`, each
its own transient user scope under `systemd-run --user --scope`, with `MemoryMax` and no swap. The
cap is a setting: `[orch] gate_memory`, `session_memory` and `gates_per_host` (4 GiB, 20 GiB and 1
by default; `null` turns a cap off), and a repo says how to clean up after a gate that was stopped
(`Repo.gate_cleanup`: the containers, the temp files, whatever a suite leaves behind).

| What runs it | Its cap | Why |
| --- | --- | --- |
| A gate (a task's `make check`, in `push`) | `gate_memory`, one per host (`gates_per_host`) | It is the biggest thing udl runs: a suite of its own |
| A turn of `command`, `codex` or `claude-code` | `session_memory` | The harness is a process of its own, so a turn can be capped |
| A turn of `opencode` | Its service's own | The turn runs in the `opencode serve` process, so the cap is that unit's (`MemoryMax` in an ExecStart drop-in); `udl orch runners` reports it |
| udl itself (`udl serve`) | Nothing | It is the thing that must not be stopped: a runaway it started is in a scope of its own |

A gate or a turn stopped at its cap pushes nothing, and says so (`stopped: memory` on the gate's
result; `RunnerFailed` and `TurnEnded` with the reason on a turn). A gate stopped by its time is
stopped too (its unit is `systemctl --user stop`ped, so the suite's own children go with it), and
the repo's `gate_cleanup` runs after either — not after a gate that simply failed, where the agent
reads its failure in the worktree. Where the host has no user systemd, the cap cannot be enforced:
the gate runs as it did before, and its result says `limits: unenforced` rather than `enforced`.

### A host that is in trouble

The same machine that runs the gates also runs the sessions, the database and this checkout, so a
host that is out of room takes everything with it. On 23 Nov btrfs ran out of unallocated space —
`df` still said 116 GiB free, and every commit failed with "No space left on device" — and five
full suites at once took disk I/O pressure to 50%; both were caught by hand, every half hour.

`udl orch hosts health` reads each host (one `sh -c` script through its shell: memory, swap, I/O
pressure, every path udl uses there, and each harness service's `MemoryCurrent`/`MemoryMax`) and
says what the thresholds make of it. A measure a host cannot give — no PSI, not Linux, not btrfs —
is "not measured", never zero. The thresholds are `[apps.orch.health]`: `memory_min` (2 GiB),
`swap_max_ratio` (0.9), `disk_min` (10 GiB), `btrfs_unallocated_min` (1 GiB),
`btrfs_metadata_max_ratio` (0.9), `io_pressure_max` (40%) and `check_minutes` (10), and each issue
carries the fix it needs — for btrfs unallocated space, the balance a person runs.

What a breach does is `on_breach`, `act_and_tell` by default:

| `on_breach` | What happens |
| --- | --- |
| `act` | No new turn and no new gate starts on that host while an issue is open; what is running finishes, and a check that finds it healthy releases it |
| `tell` | One action in the inbox per open issue (host and kind), with its detail and hint; udl closes it with "cleared at …" when the issue clears |
| `act_and_tell` | Both |

Either way, `orch.host.unhealthy` and `orch.host.healthy` say what changed, and the inbox's host
strip says which hosts are held.

## Plugins

udl has six kinds of plugin: databases, downloaders, site plugins, secret stores, kinds of action, and
integrations. `udl plugins list` shows each one with its type, its source (`builtin`, `installed` or
`local`), and whether it can run.

- **Site plugins** are code for a particular site's behaviour. They can rewrite its URLs (for
  example short links to their usual form), choose its downloader, add the headers or cookies it
  needs, give it a folder, and work on the files after a download. The built-in `youtube` plugin
  is an example.
- **Integrations** connect accounts (by OAuth, tokens, or nothing for a source that needs no login),
  and bring operations, event types and a watcher. See [Accounts](#accounts).
- **Secret stores** keep udl's secrets (account tokens, secret settings), never in a database or
  the config: the system's keyring when it has one (`keyring`), else a file only you can read
  (`file`). `[secrets] store` picks one.
- **A plugin's own operations** are commands, routes and MCP tools too, made for it:
  `udl <plugin> <op>`, `POST /plugins/<plugin>/<op>`, and the `<plugin>_<op>` tool.
- **A plugin's settings** (a plugin with a settings model) are its defaults, then its
  `[integrations.<name>]` table in the config, then what `udl plugins configure <name> -O KEY=VALUE`
  set (`udl plugins settings <name>` shows them). Its secrets go to the secret store, and settings
  that widen what udl reaches are set in the config file only.
- **Local plugins:** any `.py` file in a plugin dir (`~/.config/udl/plugins` by default) is
  loaded, with no packaging needed. A local plugin with the same name as a built-in one replaces
  it.

Being built: app plugins (the orchestrator is one), agent runners, AI providers, and UI plugins that guide
the page the web UI generates for every plugin. See **[docs/plugins.md](docs/plugins.md)** to write your
own.

## The HTTP API

```sh
udl serve                       # http://127.0.0.1:8765, docs at /docs
udl serve --host 0.0.0.0 --port 8765
```

Every command has a route, named after its operation, and every operation is an MCP tool of the same
name:

| Operation | CLI | API | MCP tool |
|---|---|---|---|
| download | `udl get` | `POST /downloads` | `download` |
| job status | `udl jobs show` | `GET /downloads/{id}` | `get_download` |
| history | `udl history` | `GET /downloads` | `list_downloads` |
| cancel | `udl jobs cancel` | `DELETE /downloads/{id}` | `cancel_download` |
| probe | `udl probe` | `POST /probe` | `probe_url` |
| list sites | `udl sites list` | `GET /sites` | `list_sites` |
| show a site | `udl sites show` | `GET /sites/{key}` | `get_site` |
| set a strategy | `udl sites set` | `PUT /sites/{key}/strategies` | `set_site_strategy` |
| forget a site | `udl sites rm` | `DELETE /sites/{key}` | `delete_site` |
| suggest chains | `udl chain suggest` | `POST /chains/suggest` | `suggest_chain` |
| test a chain | `udl chain test` | `POST /chains/test` | `test_chain` |
| save a chain | `udl chain set` | `PUT /sites/{key}/chain` | `set_site_chain` |
| import chains | `udl chain import` | `POST /chains/import` | `import_chains` |
| export chains | `udl chain export` | `GET /sites/{key}/chain` | `export_chain` |
| read pages | `udl fetch get` | `POST /fetches` | `fetch_pages` |
| fetches | `udl fetch list` / `show` / `page` | `GET /fetches`, `GET /fetches/{id}`, `GET /fetches/pages/{id}` | `list_fetches`, `get_fetch`, `get_fetched_page` |
| cancel / retry a fetch | `udl fetch cancel` / `retry` | `DELETE /fetches/{id}`, `POST /fetches/{id}/retry` | `cancel_fetch`, `retry_fetch` |
| anti-bot checks | `udl fetch challenges` / `solve` | `GET /fetches/challenges`, `POST /fetches/challenges/solve` | `list_challenges`, `solve_challenges` |
| cookies | `udl browser cookies import` / `list` / `rm` | `POST /browser/cookies`, `GET /browser/cookies`, `DELETE /browser/cookies/{site}` | `import_cookies`, `list_cookies`, `delete_cookies` |
| list databases | `udl db list` | `GET /databases` | `list_databases` |
| sync databases | `udl db sync` | `POST /databases/sync` | `sync_databases` |
| list plugins | `udl plugins list` | `GET /plugins` | `list_plugins` |
| a plugin's settings | `udl plugins settings` | `GET /plugins/{name}/settings` | `get_plugin_settings` |
| change them | `udl plugins configure` | `PUT /plugins/{name}/settings` | `set_plugin_settings` |
| show the config | `udl config show` | `GET /config` | `get_config` |
| event types | `udl events types` | `GET /event-types` | `list_event_types` |
| events | `udl events list` / `show` | `GET /events`, `GET /events/{id}` | `list_events`, `get_event` |
| emit / replay an event | `udl events emit` / `replay` | `POST /events`, `POST /events/{id}/replay` | `emit_event`, `replay_event` |
| subscriptions | `udl subs list` / `show` / `set` / `rm` | `GET /subscriptions`, `GET` / `PUT` / `DELETE /subscriptions/{name}` | `list_subscriptions`, `get_subscription`, `set_subscription`, `delete_subscription` |
| test a subscription | `udl subs test` | `POST /subscriptions/test` | `test_subscription` |
| actions | `udl actions list` / `show` / `set` / `rm` | `GET /actions`, `GET` / `PUT` / `DELETE /actions/{name}` | `list_actions`, `get_action`, `set_action`, `delete_action` |
| run an action | `udl actions run` | `POST /actions/{name}/run` | `run_action` |
| scripts | `udl actions scripts` | `GET /actions/scripts` | `list_scripts` |
| runs | `udl runs list` / `show` / `cancel` | `GET /runs`, `GET` / `DELETE /runs/{id}` | `list_runs`, `get_run`, `cancel_run` |
| schedules | `udl schedules list` / `show` / `set` / `rm` | `GET /schedules`, `GET` / `PUT` / `DELETE /schedules/{name}` | `list_schedules`, `get_schedule`, `set_schedule`, `delete_schedule` |
| run / preview a schedule | `udl schedules run` / `preview` | `POST /schedules/{name}/run`, `POST /schedules/preview` | `run_schedule`, `preview_schedule` |
| accounts | `udl accounts list` / `show` / `add` / `set` / `rm` | `GET` / `POST /accounts`, `GET` / `PATCH` / `DELETE /accounts/{account}` | `list_accounts`, `get_account`, `connect_account`, `set_account`, `disconnect_account` |
| check an account | `udl accounts check` | `POST /accounts/{account}/check` | `check_account` |
| hosts | `udl hosts list` / `set` / `rm` / `check` | `GET` / `PUT /hosts`, `DELETE /hosts/{host}`, `POST /hosts/{host}/check` | `list_hosts`, `set_host`, `delete_host`, `check_host` |
| the automations' status | `udl status` | `GET /status` | `get_status` |
| a plugin's operation | `udl <plugin> <op>` | `POST /plugins/<plugin>/<op>` | `<plugin>_<op>` |

- **Starting downloads:** `POST /downloads` answers 202 with the job ids. Add `?wait=true` to get
  the results instead, once the downloads have ended.
- **Following a download:** `GET /downloads/{id}/events` streams its progress as server-sent
  events.
- **Errors** come back as `{"code", "message", "hint"}`, with a status that fits: 404 for
  something that does not exist, 400 for a tool or options that cannot work, 403 for something
  not allowed, 401 without the API key, 409 for a name that is taken (or an account that needs
  connecting again), and 503 for a database (or secret store) problem.

## The MCP server

The MCP server offers the same operations as tools, with descriptions written for agents. An
agent can download things, read pages its own fetch tool can't, look at what udl knows, set up
automations, and teach udl a site itself (`suggest_chain`, `test_chain`, then `set_site_chain`).

```json
{"mcpServers": {"udl": {"command": "udl", "args": ["mcp"]}}}
```

`udl mcp` serves it over stdio. `udl serve` also serves it over streamable HTTP at `/mcp`,
behind the same API key. By default, `download` waits up to 120 s and reports progress as MCP
progress notifications. Operations that change automations, accounts or settings are marked `admin`,
and no action ever calls one: an `operation` action that names one is refused.

## Configuration

udl reads `~/.config/udl/config.toml` (or the file named by `--config` or `$UDL_CONFIG`), then
`UDL_*` environment variables (for nested tables use `__`, for example `UDL_API__PORT=9000`).
Every setting is optional. `udl config show` prints the settings in use, with secrets hidden.

```toml
download_dir = "~/Downloads/udl"
filename_template = "{site}/{title}.{ext}"
max_concurrent = 4                  # downloads at once
max_per_site = 2                    # downloads at once from one site
probe_order = ["http", "yt-dlp", "gallery-dl", "scraper", "browser"]
heal_threshold = 3                  # failures in a row before a learned strategy is replaced
cookies_from_browser = "firefox"    # optional
plugin_dirs = ["~/.config/udl/plugins"]

[api]                               # udl serve, and the MCP server
host = "127.0.0.1"
port = 8765
api_key = ""                        # set it to require an X-API-Key header
allowed_output_roots = ["~/Downloads"]

[browser]
headless = true
profile_dir = "~/.local/share/udl/browser-profile"

[tools.yt-dlp]                      # each downloader's options
format = "bv*+ba/b"

[tools.http]
backend = "httpx"                   # httpx | curl | wget
user_agent = "Mozilla/5.0 ..."      # by default a browser's

[secrets]
store = "auto"                      # auto (the keyring, else a file) | keyring | file | memory

[automations]                       # the runtime in udl serve (see Automations)
enabled = true
max_concurrent_runs = 4
max_event_depth = 8                 # events caused by runs, caused by events...
event_retention_days = 30
scripts_dir = "~/.config/udl/scripts"   # script actions run programs from here only

[integrations.gmail]                # a plugin's settings (under what udl plugins configure sets)
poll_interval = "60s"               # durations: 30s, 5m, 24h, 1h30m

# [db] and [[db.connections]]: see Databases. [ai], [web], [apps.*] and [hub] are for what the
# plans' later phases build.
```

## Security

- **Who can reach the API.** `udl serve` listens on 127.0.0.1 and answers only requests addressed
  to localhost, which stops DNS-rebinding attacks. It accepts only JSON, which a web page cannot
  send to it without a preflight that udl does not grant. To serve other machines, set `api.host`
  **and** an `api.api_key`; without a key, udl warns.
- **What a client can make udl do.** Anyone who can use the API or the MCP server can make udl
  fetch any http(s) URL from its machine, including addresses on your local network. Treat
  access to it like access to a shell on that machine's network.
- **Where downloads can go.** The output folder a request names (`output_dir`) must be inside
  `api.allowed_output_roots`. Links and `..` are resolved before the check. A downloader's files
  must end up inside the output folder.
- **What udl keeps on disk.** The browser profile holds your logins. Chromium creates it so that
  only you can read it, and udl warns if other users can. A new SQLite database is readable only
  by you. A config file that holds `api_key`, or database passwords, should be too
  (`chmod 600`).
- **Cookies and headers are never put on a command line**, which other users can read. curl,
  wget and gallery-dl get them in files in a private temporary folder.
- **What an action can do.** A script action runs only programs in `scripts_dir` (links and
  `..` resolved), never through a shell, with an environment of udl's making (not udl's own,
  which may hold secrets); udl refuses to run the automations when `scripts_dir` is inside a
  folder it downloads to. An operation action never calls an admin operation (one that changes
  automations or settings), and paths it gives are held to their roots. Event data (an email,
  a message) was written by anyone: templates render it in a sandbox, and it reaches a script
  as data, never as code. An action's secrets (a webhook's secret headers) go to the secret
  store.
- **Plugins are code.** Install packages you trust, and keep the plugin dir writable only by you.

## Development

```sh
uv sync --all-extras            # the environment, with every extra
make check                      # uv lock --check, ruff, pyright (strict), pytest with coverage
make smoke                      # udl against real sites (needs the internet; never in check)
make fmt                        # format, and fix what ruff can
```

udl is built test first and fully typed (`pyright --strict`, Pydantic and SQLModel at every boundary,
90 % branch coverage as a floor). The tests run against a local fixture site and fake servers. The
PostgreSQL and MongoDB tests start throwaway Docker containers (`postgres:18-alpine`, `mongo:8`).
Without Docker they are skipped, unless `UDL_TEST_POSTGRES_URL` or `UDL_TEST_MONGODB_URL` names a
server to use.

**The plan is the spec.** [planning.md](planning.md) holds the progress tracker and the working
agreement; each phase's tasks have a card (what it needs, what to build, the tests to write first, when
it is done). The orchestrator's phases are in [planning/02-agent-orchastrator.md](planning/02-agent-orchastrator.md),
the shared data layer's in [planning/04-shared-data-layer.md](planning/04-shared-data-layer.md), and
finished plans, with the decisions made along the way, in [planning/](planning/)
([planning/01-core.md](planning/01-core.md): the downloader).

**Contributing.** `main` is locked: every change is a branch and a pull request naming its task, and it
merges once CI's `make check` and the Claude review bot both pass with nothing left open. Much of udl is
built by AI agents working through that same flow, which udl's orchestrator is being built to run.
[CLAUDE.md](CLAUDE.md) has the rules agents (and people) follow.
