# Writing udl UI plugins

A UI plugin is a folder of bundles that the shell loads at startup: a `manifest.json` saying what it
adds, an ES module that exports the mounts, and its stylesheets. `web/packages/template` is the starting
point — copy it, change the manifest's name, and build with `pnpm build`; `[web] ui_plugin_dirs` in
`udl.toml` points at the folders to serve.

A plugin may **be** the UI of a udl plugin (`"plugin": "gmail"` in the list `/ui/plugins.json`
answers), or stand on its own (`"plugin": null`: a page, a widget, a theme of its own).

## The manifest

| Field | What it is |
|---|---|
| `name`, `version` | Its own name and version (the version is what the shell shows when it fails). |
| `sdk` | The `@udl/ui-sdk` range it was built for: `^MAJOR.MINOR`, which the shell's must satisfy. `^1.1` is the first with guidance (`generated`); a manifest that says `^1.0` gets no guidance. |
| `entry`, `styles` | Files of the bundle: its ES module, and the stylesheets adopted into its root. |
| `isolation` | `shadow` (its own root, the default) or `none` (the shell's DOM and CSS). |
| `contributes` | What it adds, by point — a closed set (`POINTS` in `src/udl/plugins/ui/base.py`). |
| `generated` | How it guides the page udl generates for its plugin (below). |

`contributes` points: `nav`, `routes`, `settings`, `accountSetup`, `eventViews`, `filterEditors`,
`actionEditors`, `widgets`, `themes`, `operationViews`, `resultViews`, `fieldWidgets`. Each mount the
manifest names must exist in the entry under the same key, and the shell says which one is missing
when it is not.

## The generated page, and who draws what

A udl plugin without a UI plugin of its own gets a page udl generates from its operations, settings and
accounts (`/ui/p/gmail`). A UI plugin can take over parts of it:

- `settings: ["gmail"]` — its own settings panel, given what udl answered and a `save` that sends it
  back.
- `accountSetup: ["gmail"]` — its own setup for the integration's accounts. udl still lists, checks
  and disconnects them.
- `operationViews: { gmail_send: … }` — its own panel in place of one operation's form and Run; props
  `{operation, schema, run}`.
- `resultViews: ["gmail_list_*"]` — its own drawing of a result, in place of udl's table or JSON; props
  `{operation, value}`.
- `fieldWidgets: { duration: … }` — a widget for a field that asks for it with
  `x-udl-ui.widget: "duration"`; props `{field, value, onChange}`.

### Precedence, stated once

**What a UI plugin contributes replaces that part of the generated page, and everything it does not
replace stays generated.** A contributed `settings` or `accountSetup` replaces that tab; a contributed
route at a path wins over the generated page at that path; a contributed `operationView` replaces that
operation's panel and its result. Nothing else changes: the operations it does not name keep udl's
form, its table, its confirm dialog and its errors.

## Guiding the page (`generated`)

All of it optional; what a plugin does not say stays as udl answered it.

```json
"generated": {
	"nav": { "title": "Mail", "icon": "mail" },
	"tabs": ["operations", "settings"],
	"hide": ["gmail_watch_*"],
	"groups": { "Sending": ["gmail_send", "gmail_reply"] },
	"order": ["gmail_inbox"],
	"labels": { "gmail_inbox": "Inbox" }
}
```

- `nav`: its drawer entry's title and icon, or `false` for no entry at all.
- `tabs`: which of `operations`, `settings`, `accounts`, `events` stay, in this order.
- `hide`, `groups`, `order`, `labels`: operation names, or a `*` prefix (`gmail_send_*`).

Guidance names operations by name; naming one the plugin does not have is not an error — the page says
so in a notice (`problems` in the shell's own words) and shows the rest. Several manifests may guide the
same plugin: the last one to say something wins, and a group two of them name keeps both sets of members.

**Whose page guidance reaches.** A manifest with `"plugin": "gmail"` guides gmail's page only. A UI-only
manifest (`"plugin": null`) guides **every** generated page: it is checked against every operation udl
knows, and the plugin's own guidance is applied after it, so the plugin's page is the UI-only one's
guidance with the plugin's on top. A name no operation matches is listed on the bundle in
`/ui/plugins.json` (`problems`), where the Plugins page says it: the bundle is still served.

## Building

`pnpm build` writes the bundle folder (`dist/<name>/`), and `pnpm test` type-checks and tests it. The
shell's own Vitest suite covers what a contribution is given and when it is drawn; `web/e2e` covers it
in a browser, in both themes.
