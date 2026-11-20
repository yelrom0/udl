# udl orchestrator: design system

The screens are references for pages built in React with **Tailwind CSS 4 and daisyUI 5.8.1**, inside
udl's web shell. Everything here is daisyUI's stock `light` and `dark` themes, unchanged, so a screen
can be rebuilt from daisyUI components and Tailwind utilities alone. Never invent colours, shadows,
gradients or fonts beyond these.

## Colours (daisyUI 5.8.1 `light`, the default)

| Token | Hex | Use |
|---|---|---|
| base-100 | #FFFFFF | page and card background |
| base-200 | #F8F8F8 | navbar, sidebar, inset panels, table header |
| base-300 | #EEEEEE | borders, dividers, empty slots |
| base-content | #18181B | text |
| primary | #422AD5 | the one main action per view, active nav item, links |
| primary-content | #E0E7FF | text on primary |
| secondary | #F43098 | rare: a second emphasis |
| secondary-content | #F9E4F0 | |
| accent | #00D3BB | rare: highlights |
| accent-content | #084D49 | |
| neutral | #09090B | neutral buttons and badges |
| neutral-content | #E4E4E7 | |
| info | #00BAFE | in progress, waiting |
| info-content | #042E49 | |
| success | #00D390 | green checks, ready, done, working |
| success-content | #004C39 | |
| warning | #FCB700 | held, limited, pending checks |
| warning-content | #793205 | |
| error | #FF627D | blocked, failed, destructive |
| error-content | #4D0218 | |

## Colours (daisyUI 5.8.1 `dark`)

| Token | Hex |
|---|---|
| base-100 | #1D232A |
| base-200 | #191E24 |
| base-300 | #15191E |
| base-content | #ECF9FF |
| primary | #605DFF |
| primary-content | #EDF1FE |
| secondary, accent, neutral, info, success, warning, error and their -content | as in `light` |

Dark is the same layout with only these tokens swapped: base surfaces get darker as the number rises
(base-100 is the lightest dark surface).

## Shape and depth

- Radius: boxes (cards, modals, alerts, tables' wrapper) 8px; fields (inputs, selects, buttons) 4px;
  selectors (badges, checkboxes, toggles, tabs) 8px. Badges are pills only when daisyUI makes them so.
- Borders 1px, in base-300 (or base-content at 10 % opacity).
- Depth 1: cards have a faint shadow (`shadow-sm`); nothing floats higher except modals, dropdowns and toasts.
- No noise, no gradients, no glass, no illustrations, no stock photos, no avatars with faces
  (agents and people are shown by initials in a small `avatar placeholder` circle).

## Type

Tailwind's default scale. The shell sets no font of its own (the system UI font); Inter stands in for it.
Keys, branch names, SHAs, commands and model ids are monospace (`font-mono`).

| Level | Size / line | Weight | Tailwind |
|---|---|---|---|
| page title | 24 / 32 | 700 | `text-2xl font-bold` |
| section title | 20 / 28 | 600 | `text-xl font-semibold` |
| card title | 18 / 28 | 600 | `card-title` |
| body | 14 / 20 | 400 | `text-sm` (most of the UI) |
| body large | 16 / 24 | 400 | `text-base` (markdown specs) |
| meta | 12 / 16 | 400 | `text-xs opacity-70` |

## Spacing

4px unit. Page padding 16px on a phone, 32px from `lg` (1024px) up. Cards: 24px inside
(`card-body`), 16px between cards. Dense lists use 8–12px rows.

## The shell (on every screen)

- **Desktop (lg and up):** a fixed left sidebar 224px wide in base-200: a daisyUI `menu` of the shell's
  pages (Dashboard, Downloads, Sites, Chains, Events, Subscriptions, Actions, Runs, Schedules, AI,
  Accounts, Plugins, Databases, Settings), then a **Work** group from the orch plugin: Inbox, Board,
  Agents, Q&A (with a count badge), Merge, To action. The active item is `menu-active` (primary).
- A top `navbar` in base-200: the word **udl** as a ghost button at the left; at the right a small
  `badge badge-success` reading "live", a theme `select` (System / Light / Dark) and a "Sign out" `btn btn-sm`.
- **Phone (under lg):** the sidebar is a drawer, hidden; the navbar gets a ☰ square ghost button on
  its left. Panes stack in one column; tables become stacked cards; nothing scrolls sideways except a
  board's columns, which scroll as a row of snap-to columns.
- The page's own content sits in `main`, under a page title (`text-2xl font-bold`) and an optional
  one-line description (`text-sm opacity-70`), with the page's actions at the title's right.

## Components (daisyUI names)

`card`, `card-body`, `card-title`, `badge` (`badge-sm`, `badge-outline`, `badge-soft`), `btn`
(`btn-primary` for the one main action, `btn-ghost`, `btn-outline`, `btn-sm`, `btn-xs`, `btn-error` for
destructive), `table` (`table-sm`, zebra off), `menu`, `tabs tabs-border`, `stats`/`stat`, `alert`
(`alert-soft`), `collapse collapse-arrow`, `join`, `dropdown`, `modal`, `toast`, `tooltip`, `kbd`,
`status` (the small coloured dot), `avatar avatar-placeholder`, `chat chat-start/chat-end` with
`chat-bubble` (question threads), `timeline timeline-vertical timeline-compact` (notes), `steps`,
`textarea`, `input`, `select`, `toggle`, `filter`, `breadcrumbs`, `divider`, `skeleton`, `loading`.
Icons: simple 16–20px outline icons (Heroicons outline style), never filled illustrations.

## udl's own vocabulary

**Task states** (badge, always with its word):

| State | Badge |
|---|---|
| open | `badge-ghost` |
| queued | `badge-neutral badge-outline` |
| in_progress | `badge-info` |
| blocked | `badge-error` |
| ready | `badge-success` |
| merged | `badge-primary` |
| done | `badge-success badge-outline` |
| moved | `badge-warning badge-outline` |
| cancelled | `badge-ghost` with the word struck through |

**Agent status** (a `status` dot before the name, and the word): idle (base-300 / grey), working
(success, pulsing), waiting (info), limited (warning, with "until 14:05"), offline (error), paused (neutral).

**Link chips** (`badge badge-outline badge-sm` with an icon, one look per kind): `pull` (PR icon,
`#41`, then a tiny checks dot: green / amber / red, and the bot's verdict: ✓ approve, ✎ changes),
`issue`, `commit` (7-char SHA in mono), `branch` (mono), `ci_run`, `file` (path in mono), `url`, `doc`,
`stitch`, `task` (key in mono, e.g. `29.4`), `feature` (e.g. `29`), `question` (e.g. `Q-8`).

**Keys** are always monospace and always links: task keys `24.6`, feature keys `24` or `F-3`, question
keys `Q-8`, action keys `A-3`, PRs `yelrom0/udl#41`.

**Text from agents, PRs and questions** is rendered markdown (headings, lists, `code`, fenced blocks),
in body-large, never raw HTML.

## Sample data (use it, so the screens read as udl's own work)

- Project `udl` (repo `yelrom0/udl`, base `main`, gate `make check`, merge = human, bot `claude[bot]`).
- Features: `24` Web UI foundations and a generated UI for every plugin (active); `27` Agents,
  assignment and the dispatcher (active); `28` Human comms: Q&A, Ready to merge, To action (planned);
  `29` Screens and the orch UI plugin (planned).
- Tasks: `24.6` The generated plugin page; `24.7` UI plugins guide the generated UI; `26.4` Links;
  `26.5` Notes; `27.2` Agent tokens; `27.3` Queues; `27.4` Suggested next tasks; `29.2` The UI plugin's
  skeleton; `29.4` Board.
- Agents: `deepseek-a` and `deepseek-b` (runner `opencode`, model `deepseek/deepseek-v4.1-flash-1017`,
  host `local`, role implementer, manager `lead`); `lead` (runner `claude-code`, model `claude-sonnet-5-5`,
  role manager); `claude` (runner `claude-code`, model `claude-opus-5-5`, role reviewer); `thevault-a`
  (runner `opencode`, host `thevault`, offline).
- People: `human:daniel` (the owner).
- PRs: `yelrom0/udl#41` (24.6, green, bot approved), `#42` (26.4, checks running), `#43` (27.2,
  "Merge after #42"), `#39` (merged).
- Question `Q-8` from `deepseek-b` on `27.3`: "Should a task moved between agents keep its worktree?"
  with options "Keep it" / "Make a new one", blocking.
- Costs in US dollars with cents (`$0.42`), tokens as `12.4k in · 3.1k out`, times as "4 min ago".
