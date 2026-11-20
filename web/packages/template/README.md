# A udl UI plugin

A starting point: React 19, daisyUI and `@udl/ui-sdk`, built by Vite into the bundle udl serves at
`/ui/plugins/<name>/`. Copy this folder, change `manifest.json`'s `name`, and the page and the
widget in `src/`.

## What is in it

| File | What it is |
|---|---|
| `manifest.json` | What udl reads: the plugin's name and version, the `sdk` it needs, its entry and styles, and what it contributes (a nav item, a page, a dashboard widget). |
| `src/index.tsx` | The entry: `defineUIPlugin({ routes, widgets })`, each contribution a `Mount` (`react(Component)` from `@udl/ui-sdk/react`). |
| `src/page.tsx`, `src/widget.tsx` | The contributions themselves: ordinary React components. The shell gives a page its route (`path`, `params`, `search`) and a widget its `slot`, and both get the context as `ctx`. |
| `src/index.css` | Tailwind 4 and daisyUI, for this plugin's own bundle only (`source(none)`, then `@source`). |
| `vite.config.ts` | The bundle: one ES module named `index.js`, one stylesheet named `index.css`, and the manifest, in `dist/template/`. |
| `src/*.test.tsx` | The template's own guard: the manifest's contributions are the entry's, and both draw. |

## Build and load it

```sh
pnpm install
pnpm build                      # dist/template/{manifest.json,index.js,index.css}
```

Then point udl at the folder that holds the bundle — `dist/` is a folder of UI plugins, as
`[web] ui_plugin_dirs` reads one:

```toml
[web]
enabled = true
ui_plugin_dirs = ["/path/to/this/dist"]
```

`udl serve` lists it on its Plugins page (`/ui/plugins`) and in `/ui/plugins.json`, serves its files
at `/ui/plugins/template/`, and the shell draws its page and its widget in shadow roots of their
own.

## How it is drawn

- **Its own root.** `isolation: "shadow"` (the default) puts each contribution in a shadow root, so
  the plugin's CSS and the shell's never meet. `isolation: "none"` draws in the shell's DOM instead,
  sharing its styles.
- **The theme comes from the shell.** daisyUI's colours are registered with
  `@property ... inherits: false`, so they do not reach a shadow root on their own: the shell puts
  the theme's tokens on the wrapper it draws the plugin into, and sets `data-theme` there. daisyUI
  classes (`btn`, `card`, `bg-base-200`) therefore draw in the page's theme, and the theme switch
  changes them without a reload.
- **React is its own.** The bundle carries React (a page has no import map), so a plugin may use
  React 19 or nothing at all — `Mount` is a plain DOM contract.
- **`@property` rules are hoisted.** A shadow root cannot register a custom property, so the shell
  copies a plugin's `@property` rules to a document-level sheet.

## Reading udl

`ctx` is the shell's context: the typed `api` client (`@udl/api`, generated from udl's OpenAPI
schema), `call(name, request)` for an operation by name, `events.subscribe(filter)`, the theme and
its tokens, the stores read-only, `navigate`, `toast` and `confirm`.

See `web/examples/shadcn` for a plugin built with shadcn/ui and its own Tailwind setup, which maps
shadcn's tokens onto daisyUI's.
