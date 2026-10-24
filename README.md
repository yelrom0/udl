# udl (Ultra Downloader)

One command for downloading from anywhere. Give udl a URL and it works out how to download it:
yt-dlp, gallery-dl, a plain HTTP download, the media in the page's HTML, or a real browser. It
remembers what worked for each site, so the next download from that site goes straight to it.

Everything udl does is available three ways, with the same options and results:

- the **command line** (`udl`)
- an **HTTP API** (`udl serve`)
- an **MCP server** (`udl mcp`), so AI agents can download things and teach udl new sites

```console
$ udl https://youtu.be/jNQXAC9IVRw
✓ https://www.youtube.com/watch?v=jNQXAC9IVRw → ~/Downloads/udl/youtube.com/Me at the zoo.webm (474.5 kB, yt-dlp)
```

## Contents

- [Install](#install)
- [Downloading](#downloading)
- [How udl picks a downloader](#how-udl-picks-a-downloader)
- [Sites and strategies](#sites-and-strategies)
- [Sites that need a login](#sites-that-need-a-login)
- [Tag chains: sites no downloader handles](#tag-chains-sites-no-downloader-handles)
- [Databases](#databases)
- [Automations](#automations)
- [Plugins](#plugins)
- [The HTTP API](#the-http-api)
- [The MCP server](#the-mcp-server)
- [Configuration](#configuration)
- [Security](#security)
- [Development](#development)

## Install

udl needs Python 3.14 and [uv](https://docs.astral.sh/uv/). Install it as a tool, with the
extras you want:

```sh
uv tool install --editable '.[browser]'    # from a checkout of this repository
uv run playwright install chromium         # (in the checkout) Chromium, for the browser downloader
```

| Extra | Adds |
|---|---|
| `browser` | the `browser` downloader (Playwright), and `udl browser login` |
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

## Databases

udl keeps sites, strategies and download history in one or more databases. With no
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

`db sync` copies sites, strategies and the downloads that have ended:

- **Ids:** records keep their ids (UUIDs), so a record is the same in every database.
- **Conflicts:** what the target lacks is added. What it has differently is replaced by the
  source's copy, so sync from the database that is right.
- **Nothing is deleted**, so syncing twice changes nothing the second time.
- **Read-only connections** can be synced from, but not into.

## Automations

udl turns what happens into **events** (a download ended, an action's run ended, an event of your
own), and runs **actions** for the **subscriptions** an event matches. An action is a named
thing to run, of a kind: `script` (a program from `[automations] scripts_dir`, never through a
shell, with the event as JSON on its stdin), `operation` (one of udl's operations), `webhook`
(an HTTP request), `emit` (an event of your own) or `notify` (a desktop notification). Its
options are templates, rendered with the event (`"{{ event.data.url }}"`).

```sh
udl actions set hello --kind script -O 'command=["hello.sh"]'      # ~/.config/udl/scripts/hello.sh
udl subs set greet --on custom.hello --action hello --where 'name == "x"'
udl events emit custom.hello --data '{"name": "x"}'
udl runs list                                   # the run, and what the script printed
udl events list                                 # the event, and the run's udl.action.succeeded
```

- **Filters** are conditions on the event's data: dotted paths compared with `==` `!=` `contains`
  `startswith` `endswith` `~` (a regex) `in` `<` `<=` `>` `>=` `exists`, joined by `and`, `or`,
  `not` and parentheses (`from.address endswith "@bank.com" and not sent_by_udl`).
  `udl events types` lists each event type's fields; a filter that names one its types lack is
  refused when it is saved. `udl subs test <name>` tries a subscription on the latest events.
- **The runtime** runs in `udl serve` (unless `[automations] enabled = false`): it dispatches the
  events (whichever udl published them) and runs their actions. One udl runs them per database,
  the one that holds its lease; `udl status` says which. A failure that trying again may help is
  tried again (an action's `retries`, with backoff); a subscription's throttle (`10 per 1h`) and
  `max_event_depth` (events caused by runs caused by events...) keep anything from running for
  ever.
- **Live:** `GET /events/stream` sends the events and the runs' changes as they happen.

## Plugins

udl has five kinds of plugin: databases, downloaders, site plugins, secret stores and kinds of
action. `udl plugins list` shows each one with its type, its source (`builtin`, `installed` or
`local`), and whether it can run.

- **Site plugins** are code for a particular site's behaviour. They can rewrite its URLs (for
  example short links to their usual form), choose its downloader, add the headers or cookies it
  needs, give it a folder, and work on the files after a download. The built-in `youtube` plugin
  is an example.
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

See **[docs/plugins.md](docs/plugins.md)** to write your own.

## The HTTP API

```sh
udl serve                       # http://127.0.0.1:8765, docs at /docs
udl serve --host 0.0.0.0 --port 8765
```

Every command has a route, named after its operation:

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
| list databases | `udl db list` | `GET /databases` | `list_databases` |
| sync databases | `udl db sync` | `POST /databases/sync` | `sync_databases` |
| list plugins | `udl plugins list` | `GET /plugins` | `list_plugins` |
| a plugin's settings | `udl plugins settings` | `GET /plugins/{name}/settings` | `get_plugin_settings` |
| change them | `udl plugins configure` | `PUT /plugins/{name}/settings` | `set_plugin_settings` |
| show the config | `udl config show` | `GET /config` | `get_config` |
| event types | `udl events types` | `GET /event-types` | `list_event_types` |
| events | `udl events list` / `show` | `GET /events`, `GET /events/{id}` | `list_events`, `get_event` |
| emit / replay an event | `udl events emit` / `replay` | `POST /events`, `POST /events/{id}/replay` | `emit_event`, `replay_event` |
| subscriptions | `udl subs list` / `show` / `set` / `rm` | `/subscriptions`, `/subscriptions/{name}` | `list_subscriptions`, `get_subscription`, `set_subscription`, `delete_subscription` |
| test a subscription | `udl subs test` | `POST /subscriptions/test` | `test_subscription` |
| actions | `udl actions list` / `show` / `set` / `rm` | `/actions`, `/actions/{name}` | `list_actions`, `get_action`, `set_action`, `delete_action` |
| run an action | `udl actions run` | `POST /actions/{name}/run` | `run_action` |
| scripts | `udl actions scripts` | `GET /actions/scripts` | `list_scripts` |
| runs | `udl runs list` / `show` / `cancel` | `GET /runs`, `GET` / `DELETE /runs/{id}` | `list_runs`, `get_run`, `cancel_run` |
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
agent can download things, look at what udl knows, and teach udl a site itself
(`suggest_chain`, `test_chain`, then `set_site_chain`).

```json
{"mcpServers": {"udl": {"command": "udl", "args": ["mcp"]}}}
```

`udl mcp` serves it over stdio. `udl serve` also serves it over streamable HTTP at `/mcp`,
behind the same API key. By default, `download` waits up to 120 s and reports progress as MCP
progress notifications.

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

# [db] and [[db.connections]]: see Databases. [ai] and [web] are for what planning.md's later
# phases build.
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

udl is built test first and fully typed. [planning.md](planning.md) is the current plan (accounts,
automations, web-page watching, local AI and a web UI), and [planning/](planning/) keeps the finished ones with the
decisions made along the way ([planning/01-core.md](planning/01-core.md): the downloader). The tests run against a local fixture site. The PostgreSQL and
MongoDB tests start throwaway Docker containers (`postgres:18-alpine`, `mongo:8`). Without
Docker they are skipped, unless `UDL_TEST_POSTGRES_URL` or `UDL_TEST_MONGODB_URL` names a server
to use.
