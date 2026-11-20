# Re-theming a Stitch screen onto daisyUI

Stitch draws a screen's layout, but colours it from a palette of its own, whatever the design system
says. Every PNG in this folder was therefore re-themed onto daisyUI 5.8.1's `light` and `dark` (the
shell's themes, [DESIGN.md](DESIGN.md)) and rendered in Chromium. This is the whole rule, so a screen
changed in Stitch can be redone the same way on any machine.

## The steps

1. Download the screen's HTML from Stitch (the MCP's `get_screen`, then `download_file` on its
   `htmlCode`).
2. Put daisyUI 5.8.1's two theme blocks in a `<style>` at the end of `<head>` (from the `daisyui`
   package's `theme/light.css` and `theme/dark.css`), with `html { background: var(--color-base-100) }`,
   and Inter from Google Fonts.
3. Re-map every colour onto daisyUI's CSS variables (the tables below):
   - **Tailwind 3 (`cdn.tailwindcss.com`) with a `tailwind.config`:** each quoted hex in the config is
     replaced by its mapping. Then a script right after the config sets
     `tailwind.config.theme.extend.colors` to the role and palette maps below, each value wrapped as
     `color-mix(in oklab, <expression> calc(<alpha-value> * 100%), transparent)` so `bg-error/5`-style
     opacities still work. With no config of its own, the same script goes right after Tailwind's.
   - **Tailwind 4 (`@tailwindcss/browser`):** its palette is CSS variables, so the theme block also sets
     `--color-white`, `--color-black` and `--color-<palette>-<shade>` to the palette map.
   - **daisyUI 4 (`daisyui@4…/full.min.css`):** kept, because daisyUI 5's CSS is in cascade layers and
     loses to Tailwind 3's reset. The theme block feeds it daisyUI 5.8.1's colours through daisyUI 4's
     variables (`--color-base-100` → `--b1`, `--color-base-200` → `--b2`, `--color-base-300` → `--b3`, `--color-base-content` → `--bc`, `--color-primary` → `--p`, `--color-primary-content` → `--pc`, `--color-secondary` → `--s`, `--color-secondary-content` → `--sc`, `--color-accent` → `--a`, `--color-accent-content` → `--ac`, `--color-neutral` → `--n`, `--color-neutral-content` → `--nc`, `--color-info` → `--in`, `--color-info-content` → `--inc`, `--color-success` → `--su`, `--color-success-content` → `--suc`, `--color-warning` → `--wa`, `--color-warning-content` → `--wac`, `--color-error` → `--er`, `--color-error-content` → `--erc`, the same oklch values) and its radii (`--rounded-box: 0.5rem`,
     `--rounded-btn: 0.25rem`, `--rounded-badge: 0.5rem`).
   - **Hex anywhere else** (`<style>` blocks, `style=""`, arbitrary classes such as `bg-[#F8F8F8]`): by
     the exact table, else by the rule for greys and tints.
4. Drop the navbar avatar Stitch keeps adding (a `rounded-full` element holding the `person` icon).
5. Set `<html data-theme="light" class="light">`, render, then `data-theme="dark" class="dark"`, render.
   Desktop: a 1440 px wide viewport at 1×; phone: 390 px at 2×. Wait for the network to go idle and the
   fonts to load, then set the viewport to the page's full height (a fixed sidebar is only as tall as
   the viewport) and take the screenshot.
6. Push the HTML and PNG back onto the Stitch screen (`update_screen`), and the dark ones onto its
   "… (Dark)" copy, then copy the PNGs here as `<screen>-<theme>-<size>.png`.

## Stitch's colour roles

Stitch writes Material colour roles. `x at N %` is `color-mix(in oklab, x N%, transparent)`;
`x N % into y` is `color-mix(in oklab, x N%, y)`. Every name is a daisyUI colour variable
(`--color-<name>`), so each one is right in both themes.

| Stitch role | daisyUI |
|---|---|
| `background` | `base-100` |
| `surface` | `base-100` |
| `surface-bright` | `base-100` |
| `surface-container-lowest` | `base-100` |
| `surface-container-low` | `base-200` |
| `surface-container` | `base-200` |
| `surface-container-high` | `base-300` |
| `surface-container-highest` | `base-300` |
| `surface-variant` | `base-300` |
| `surface-dim` | `base-300` |
| `on-background` | `base-content` |
| `on-surface` | `base-content` |
| `on-surface-variant` | `base-content` at 70% |
| `outline` | `base-content` at 45% |
| `outline-variant` | `base-content` at 13% |
| `inverse-surface` | `neutral` |
| `inverse-on-surface` | `neutral-content` |
| `inverse-primary` | `primary` |
| `surface-tint` | `primary` |
| `white` | `base-100` |
| `black` | `base-content` |
| `primary` | `primary` |
| `primary-container` | `primary` |
| `primary-fixed` | `primary` 14% into `base-100` |
| `primary-fixed-dim` | `primary` 32% into `base-100` |
| `on-primary` | `primary-content` |
| `on-primary-container` | `primary-content` |
| `on-primary-fixed` | `primary` |
| `on-primary-fixed-variant` | `primary` |
| `secondary` | `secondary` |
| `secondary-container` | `secondary` |
| `secondary-fixed` | `secondary` 14% into `base-100` |
| `secondary-fixed-dim` | `secondary` 32% into `base-100` |
| `on-secondary` | `secondary-content` |
| `on-secondary-container` | `secondary-content` |
| `on-secondary-fixed` | `secondary` |
| `on-secondary-fixed-variant` | `secondary` |
| `tertiary` | `accent` |
| `tertiary-container` | `accent` |
| `tertiary-fixed` | `accent` 14% into `base-100` |
| `tertiary-fixed-dim` | `accent` 32% into `base-100` |
| `on-tertiary` | `accent-content` |
| `on-tertiary-container` | `accent-content` |
| `on-tertiary-fixed` | `accent` |
| `on-tertiary-fixed-variant` | `accent` |
| `error` | `error` |
| `error-container` | `error` 15% into `base-100` |
| `error-fixed` | `error` 14% into `base-100` |
| `error-fixed-dim` | `error` 32% into `base-100` |
| `on-error` | `error-content` |
| `on-error-container` | `error` |
| `on-error-fixed` | `error` |
| `on-error-fixed-variant` | `error` |
| `primary-content` | `primary-content` |
| `secondary-content` | `secondary-content` |
| `accent` | `accent` |
| `accent-content` | `accent-content` |
| `neutral` | `neutral` |
| `neutral-content` | `neutral-content` |
| `info` | `info` |
| `info-content` | `info-content` |
| `success` | `success` |
| `success-content` | `success-content` |
| `warning` | `warning` |
| `warning-content` | `warning-content` |
| `error-content` | `error-content` |
| `base-100` | `base-100` |
| `base-200` | `base-200` |
| `base-300` | `base-300` |
| `base-content` | `base-content` |

## Tailwind's palettes

- Hues: `red` → `error`, `rose` → `error`, `pink` → `secondary`, `orange` → `warning`, `amber` → `warning`, `yellow` → `warning`, `lime` → `success`, `green` → `success`, `emerald` → `success`, `teal` → `accent`, `cyan` → `info`, `sky` → `info`, `blue` → `info`, `indigo` → `primary`, `violet` → `primary`, `purple` → `primary`. Shades 50–400 are that colour into `base-100` (50: 8 %, 100: 15 %, 200: 28 %, 300: 45 %, 400: 75 %); 500 and 600 are the colour;
  700–950 are the colour into `base-content` (700: 78 %, 800: 64 %, 900: 52 %, 950: 40 %).
- Greys (`gray`, `slate`, `zinc`, `neutral`, `stone`): `base-content` into `base-100`, by shade: 50: 4 %, 100: 8 %, 200: 14 %, 300: 24 %, 400: 40 %, 500: 55 %, 600: 65 %, 700: 75 %, 800: 85 %, 900: 92 %, 950: 96 %.
- `white` → `base-100`; `black` → `base-content`.

## Hex values

- Exact: `#ffffff` → `base-100`, `#fff` → `base-100`, `#f8f8f8` → `base-200`, `#eeeeee` → `base-300`, `#18181b` → `base-content`, `#422ad5` → `primary`, `#00d390` → `success`, `#fcb700` → `warning`, `#ff627d` → `error`, `#00bafe` → `info`, `#00d3bb` → `accent`; and every hex Stitch's own config gives a role is mapped as that role.
- **A grey** (chroma under 0.15 when its lightness is under 0.5, else under 0.08): `base-content` into
  `base-100` by `(1 − lightness) / 0.9` (white is `base-100`).
- **A light tint** (lightness over 0.82): the daisyUI colour of the nearest hue (`error` 350°, `warning` 43°, `success` 160°, `accent` 174°, `info` 197°, `primary` 248°, `secondary` 328°), into
  `base-100` by `max(6 %, (1 − lightness) × 140 %)`.
- **A dark saturated colour** (lightness under 0.5: text, a border or a dot), by where it is:
  - text or a border on the page or on a tint: the nearest hue's daisyUI colour into `base-content`, by
    `30 % + 120 % × lightness` (40–90 %), as Tailwind's 700–950 shades are, so it reads in both themes;
  - text in a class list or `style` whose background is a solid status colour (`bg-[#00D390]`,
    `background-color: #00D390`…), and a dark background itself (a dot on a badge): that status
    colour's `-content` (`success-content`…), which is the same in both themes, as daisyUI's badges are;
  - a `hover:` or `focus:` colour: kept (no picture shows it).
- **Anything else is kept:** a saturated colour that isn't dark is a status colour, and daisyUI's status
  colours are the same in both themes.
- Every value put into an arbitrary class gets Tailwind's `color:` type hint (`text-[color:…]`), since
  Tailwind 3 can't tell that `color-mix(…)` is a colour.
