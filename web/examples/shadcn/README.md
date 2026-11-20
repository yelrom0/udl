# The shadcn/ui example plugin

18.10's proof: a udl UI plugin written with React and [shadcn/ui](https://ui.shadcn.com), built by
Vite with a **Tailwind 4 setup of its own** — it scans this folder's files and no others — and its
tokens mapped onto daisyUI's, so the shell's theme drives it.

It contributes a page (a card, with shadcn's buttons) and a dashboard widget.

## The two things it proves

**A plugin's own Tailwind.** `src/index.css` imports Tailwind with `source(none)` and then
`@source "./"`, so the classes come from `src/` alone: the shell's CSS is never a candidate, and
this plugin's utilities never reach the shell's page (the e2e test checks both directions).

**daisyUI's palette, inside a shadow root.** shadcn's components are styled by CSS variables
(`--background`, `--primary`, `--card`, …). Here they are aliases of daisyUI's:

```css
:host > * {
	--primary: var(--color-primary);
	--background: var(--color-base-100);
	…
}
```

`:host > *` is the wrapper the shell draws the plugin into, and the shell puts the theme's
`--color-*` on that wrapper — daisyUI registers them with `@property … inherits: false`, so they
would not reach a shadow root otherwise. The `dark:` variant is the shell's `data-theme` on the
wrapper, not the system's:

```css
@custom-variant dark (&:where([data-theme="dark"], [data-theme="dark"] *));
```

So switching the theme in the shell's navbar changes the plugin's colours, and nothing in the
plugin needs to know which theme is showing.

## Build it

```sh
pnpm build      # dist/shadcn/{manifest.json,index.js,index.css}
```

`dist/` is a folder of UI plugins, as `[web] ui_plugin_dirs` reads one; `make web` builds it, and
`make e2e` serves it to `web/e2e/shadcn.spec.ts` beside the hand-written bundles in
`tests/support/e2e_ui_plugins/`.

## The components

`src/components/ui/{button,card}.tsx` are shadcn's, generated with `pnpm dlx shadcn@latest add
button card` (`components.json` is this package's). The one change: their `cn` import is
`@/lib/utils` (this package's own), which the tsconfig's `paths` and Vite's `resolve.alias` both
point at.
