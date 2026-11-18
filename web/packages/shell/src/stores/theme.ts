/**
 * The theme store (18.5's full one): `system`, `light` or `dark`, persisted. Until a choice is
 * made it follows the system; `applyTheme()` puts it on `<html>` as `data-theme` (and takes it
 * off for `system`, which daisyUI resolves with `@prefersdark`).
 */

import { useStore } from "zustand";
import { createStore } from "zustand/vanilla";

const MEDIA = "(prefers-color-scheme: dark)";

export const THEMES = ["system", "light", "dark"] as const;
export type Theme = (typeof THEMES)[number];

const KEY = "udl.theme";

/** The system's theme, as the browser reports it (daisyUI's dark is `prefers-color-scheme`). */
export function systemTheme(): "light" | "dark" {
	return window.matchMedia(MEDIA).matches ? "dark" : "light";
}

export const isTheme = (value: unknown): value is Theme =>
	typeof value === "string" && (THEMES as readonly string[]).includes(value);

function initial(): Theme {
	const saved: unknown = JSON.parse(localStorage.getItem(KEY) ?? "null");
	return isTheme(saved) ? saved : "system";
}

/** Put `chosen` on `<html>`, so daisyUI and the shell's own CSS follow it. */
export function applyTheme(chosen: Theme): void {
	const shown = chosen === "system" ? systemTheme() : chosen;
	document.documentElement.dataset.theme = shown;
}

export const theme = createStore<{ current: Theme }>(() => ({ current: initial() }));

/** Set the chosen theme (persisting it), or move back to following the system. */
export function setTheme(current: Theme): void {
	theme.setState({ current });
	try {
		localStorage.setItem(KEY, JSON.stringify(current));
	} catch {
		// (Private mode: the choice lasts this visit.)
	}
	applyTheme(current);
}

/** The chosen theme, as a hook. */
export const useTheme = () => useStore(theme, (state) => state.current);

/** Follow the system while that is what the user chose (called when the shell starts). */
export function followSystem(): () => void {
	const media = window.matchMedia(MEDIA);
	const changed = () => theme.getState().current === "system" && applyTheme("system");
	media.addEventListener("change", changed);
	return () => media.removeEventListener("change", changed);
}
